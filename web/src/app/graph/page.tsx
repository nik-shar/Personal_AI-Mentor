"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Network as NetworkIcon, RefreshCw } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorNotice, Spinner } from "@/components/ui";
import { MarkdownDrawer } from "@/components/Drawer";
import { getGraph, getNodeDetail, getRoadmaps } from "@/lib/api";
import type { GraphResponse, RoadmapSummary } from "@/lib/types";
import { statusColor } from "@/lib/utils";

export default function GraphPage() {
  const [roadmaps, setRoadmaps] = useState<RoadmapSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<{ destroy: () => void } | null>(null);

  const [drawer, setDrawer] = useState({ open: false, title: "" });
  const [body, setBody] = useState("");
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerError, setDrawerError] = useState<string | null>(null);

  const openNote = useCallback(async (title: string) => {
    setDrawer({ open: true, title });
    setBody("");
    setDrawerError(null);
    setDrawerLoading(true);
    try {
      const res = await getNodeDetail(title);
      setBody(res.body);
    } catch (err) {
      setDrawerError((err as Error).message);
    } finally {
      setDrawerLoading(false);
    }
  }, []);

  // Load the roadmap list once + honor an ?id= deep link.
  useEffect(() => {
    (async () => {
      try {
        const res = await getRoadmaps();
        setRoadmaps(res.roadmaps);
        const params = new URLSearchParams(window.location.search);
        const wanted = params.get("id") || res.roadmaps[0]?.topic_id || "";
        setSelected(wanted);
      } catch (err) {
        setError((err as Error).message);
      }
    })();
  }, []);

  // Fetch graph data whenever the selection changes.
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setLoading(true);
    setError(null);
    getGraph(selected)
      .then((g) => alive && setGraph(g))
      .catch((err) => alive && setError((err as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [selected]);

  // Render the vis-network graph whenever the data changes.
  useEffect(() => {
    if (!graph || !containerRef.current) return;
    let disposed = false;

    (async () => {
      const { Network } = await import("vis-network");
      if (disposed || !containerRef.current) return;

      networkRef.current?.destroy();

      const nodes = graph.nodes.map((n) => ({
        id: n.id,
        label: n.title,
        title: `${n.title} · ${n.status} · ${n.estimated_hours}h`,
        shape: "box",
        color: {
          background: "#141b27",
          border: statusColor(n.status),
          highlight: { background: "#1a2231", border: "#6ea8fe" },
        },
        font: { color: "#e6edf6", face: "Inter", size: 13 },
        borderWidth: n.status === "in_progress" ? 2.5 : 1.5,
      }));

      const edges = graph.edges.map((e) => ({
        from: e.from,
        to: e.to,
        arrows: "to",
        color: { color: "#3a4a63", highlight: "#6ea8fe" },
        smooth: { enabled: true, type: "cubicBezier", roundness: 0.4 },
      }));

      const network = new Network(
        containerRef.current,
        { nodes, edges },
        {
          autoResize: true,
          physics: {
            stabilization: { iterations: 120 },
            barnesHut: { gravitationalConstant: -6000, springLength: 160 },
          },
          interaction: { hover: true, tooltipDelay: 120 },
          layout: { improvedLayout: true },
        },
      );
      networkRef.current = network;
      network.on("click", (params: { nodes: string[] }) => {
        if (params.nodes.length > 0) {
          const node = graph.nodes.find((n) => n.id === params.nodes[0]);
          if (node) openNote(node.title);
        }
      });
    })();

    return () => {
      disposed = true;
      networkRef.current?.destroy();
      networkRef.current = null;
    };
  }, [graph, openNote]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select
          className="input !w-auto min-w-[220px]"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          {roadmaps.length === 0 ? <option value="">No roadmaps</option> : null}
          {roadmaps.map((r) => (
            <option key={r.topic_id} value={r.topic_id}>
              {r.title}
            </option>
          ))}
        </select>
        <Badge color="var(--color-success)">done</Badge>
        <Badge color="var(--color-accent)">in progress</Badge>
        <Badge color="var(--color-fg-dim)">not started</Badge>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setSelected((s) => s)}>
          <RefreshCw size={14} /> Refresh
        </Button>
      </div>

      {error ? <ErrorNotice message={error} /> : null}

      <Card className="!p-0 overflow-hidden">
        {loading ? (
          <Spinner label="Loading graph topology…" />
        ) : !graph ? (
          <EmptyState
            icon={<NetworkIcon size={30} />}
            title="No graph selected"
            hint="Create a roadmap in chat, then pick it here."
          />
        ) : (
          <>
            <div className="px-4 py-3 border-b border-[var(--color-border-soft)] flex items-center justify-between">
              <span className="text-sm font-medium">{graph.title}</span>
              <span className="text-xs text-[var(--color-fg-dim)]">
                {graph.total_nodes} nodes · {graph.edges.length} dependencies
              </span>
            </div>
            <div ref={containerRef} className="w-full h-[560px] bg-[var(--color-bg)]" />
          </>
        )}
      </Card>

      <MarkdownDrawer
        open={drawer.open}
        onClose={() => setDrawer({ open: false, title: "" })}
        title={drawer.title}
        tag="Topic note"
        body={body}
        loading={drawerLoading}
        error={drawerError}
      />
    </div>
  );
}
