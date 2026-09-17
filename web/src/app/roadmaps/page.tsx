"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Route, Network, ListChecks, Clock, FolderTree } from "lucide-react";
import { Badge, Card, EmptyState, ErrorNotice, Spinner } from "@/components/ui";
import { MarkdownDrawer } from "@/components/Drawer";
import { getNodeDetail, getRoadmaps } from "@/lib/api";
import type { RoadmapSummary } from "@/lib/types";

export default function RoadmapsPage() {
  const [roadmaps, setRoadmaps] = useState<RoadmapSummary[]>([]);
  const [vaultPath, setVaultPath] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [drawer, setDrawer] = useState<{ open: boolean; title: string }>({
    open: false,
    title: "",
  });
  const [body, setBody] = useState("");
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerError, setDrawerError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getRoadmaps();
      setRoadmaps(res.roadmaps);
      setVaultPath(res.vault_path);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openNote = async (title: string) => {
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
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-xs text-[var(--color-fg-dim)]">
        <FolderTree size={14} />
        <span className="truncate">{vaultPath || "Obsidian vault"}</span>
      </div>

      {error ? <ErrorNotice message={error} onRetry={load} /> : null}

      {loading ? (
        <Spinner label="Reading roadmaps from the vault…" />
      ) : roadmaps.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Route size={30} />}
            title="No roadmaps in the vault yet"
            hint='Say "Create a 3-day roadmap for Rust" in chat and it will appear here.'
          />
        </Card>
      ) : (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {roadmaps.map((r) => {
            const pct = r.total_nodes
              ? Math.round((r.completed / r.total_nodes) * 100)
              : 0;
            return (
              <Card key={r.topic_id} className="flex flex-col">
                <div className="flex items-start gap-2 mb-3">
                  <Route size={18} className="text-[var(--color-accent)] mt-0.5" />
                  <h3
                    className="text-[0.95rem] font-semibold leading-snug"
                    style={{ fontFamily: "var(--font-display)" }}
                  >
                    {r.title}
                  </h3>
                </div>

                <div className="flex flex-wrap gap-2 mb-3">
                  <Badge>
                    <ListChecks size={12} /> {r.completed}/{r.total_nodes} topics
                  </Badge>
                  <Badge>
                    <Clock size={12} /> {r.total_hours}h
                  </Badge>
                  {r.in_progress > 0 ? (
                    <Badge color="var(--color-accent)">{r.in_progress} in progress</Badge>
                  ) : null}
                </div>

                <div className="h-2 rounded-full bg-[var(--color-surface-2)] overflow-hidden mb-4">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent)] to-[var(--color-accent-2)]"
                    style={{ width: `${pct}%` }}
                  />
                </div>

                <div className="mt-auto flex flex-wrap gap-2">
                  <Link
                    href={`/graph?id=${encodeURIComponent(r.topic_id)}`}
                    className="btn btn-secondary btn-sm"
                  >
                    <Network size={14} /> View graph
                  </Link>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => openNote(`${r.title} Roadmap`)}
                  >
                    Open index
                  </button>
                </div>

                {r.nodes.length > 0 ? (
                  <div className="mt-3 pt-3 border-t border-[var(--color-border-soft)] space-y-1">
                    {r.nodes.slice(0, 4).map((n) => (
                      <button
                        key={n.id}
                        onClick={() => openNote(n.title)}
                        className="w-full text-left text-xs text-[var(--color-fg-muted)] hover:text-[var(--color-accent)] truncate transition-colors"
                      >
                        • {n.title}
                        {n.status === "done" ? " ✓" : ""}
                      </button>
                    ))}
                    {r.nodes.length > 4 ? (
                      <div className="text-[0.65rem] text-[var(--color-fg-dim)] pl-2">
                        +{r.nodes.length - 4} more…
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </Card>
            );
          })}
        </div>
      )}

      <MarkdownDrawer
        open={drawer.open}
        onClose={() => setDrawer({ open: false, title: "" })}
        title={drawer.title}
        body={body}
        loading={drawerLoading}
        error={drawerError}
      />
    </div>
  );
}
