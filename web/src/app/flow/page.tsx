"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Play, RotateCcw, Send, Wrench, Activity } from "lucide-react";
import { Button } from "@/components/ui";
import { MentorNode } from "@/components/flow/MentorNode";
import { getArchitecture, sendChat } from "@/lib/api";
import type { ArchitectureDescriptor, TraceEvent } from "@/lib/types";
import { buildGraph, stateAtStep } from "@/lib/flow";

const nodeTypes = { mentor: MentorNode };
const STEP_MS = 320;

export default function FlowPage() {
  const [descriptor, setDescriptor] = useState<ArchitectureDescriptor | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const sessionRef = useRef<string | null>(null);

  // Load the static architecture descriptor once.
  useEffect(() => {
    getArchitecture()
      .then(setDescriptor)
      .catch((err) => setError((err as Error).message));
  }, []);

  // Replay driver: advance one trace event at a time (recursive timeout keeps
  // each step a clean 320ms apart and stops at the end).
  useEffect(() => {
    if (!playing) return;
    if (step >= trace.length) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setStep((s) => s + 1), STEP_MS);
    return () => clearTimeout(t);
  }, [playing, step, trace.length]);

  const graph = useMemo(
    () => (descriptor ? buildGraph(descriptor) : null),
    [descriptor],
  );

  const replay = useMemo(() => stateAtStep(trace, step), [trace, step]);

  const nodes: Node[] = useMemo(() => {
    if (!graph) return [];
    return graph.nodes.map((n) => {
      const event = trace.find((e) => e.kind === "node" && e.node === n.id);
      const isActive = replay.active === n.id && step < trace.length;
      const isVisited = replay.visited.has(n.id);
      const failed =
        event?.status === "error" ||
        (event?.detail &&
          typeof event.detail.status === "string" &&
          event.detail.status === "failed");
      return {
        ...n,
        data: {
          ...n.data,
          state: failed ? "error" : isActive ? "active" : isVisited ? "visited" : "idle",
        },
      } as Node;
    });
  }, [graph, replay, trace, step]);

  const edges: Edge[] = useMemo(() => {
    if (!graph) return [];
    return graph.edges.map((e) => {
      const traversed = replay.traversed.has(`${e.source}->${e.target}`);
      return {
        ...e,
        animated: traversed,
        style: {
          ...e.style,
          stroke: traversed ? "var(--color-accent)" : "#2c3a4f",
          strokeWidth: traversed ? 2.2 : 1.5,
        },
      } as Edge;
    });
  }, [graph, replay]);

  const submit = useCallback(async () => {
    const text = message.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    setMessage("");
    try {
      const res = await sendChat(text, sessionRef.current);
      sessionRef.current = res.session_id;
      setTrace(res.trace || []);
      setSelectedIdx(null);
      setStep(0);
      setPlaying(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  }, [message, sending]);

  const reset = () => {
    setPlaying(false);
    setStep(0);
    setTrace([]);
  };

  const decision = useMemo(() => {
    const reason = trace.find((e) => e.kind === "node" && e.node === "reason_node");
    return reason?.detail as Record<string, unknown> | undefined;
  }, [trace]);

  const usedTools = replay.tools;

  // Which trace event is being inspected: an explicit click, else the step the
  // replay is currently on.
  const effectiveIdx = selectedIdx ?? (step > 0 ? step - 1 : -1);
  const inspected = effectiveIdx >= 0 ? trace[effectiveIdx] : undefined;

  const selectNode = useCallback(
    (id: string) => {
      let idx = -1;
      trace.forEach((e, i) => {
        if (e.kind === "node" && e.node === id) idx = i;
      });
      if (idx >= 0) setSelectedIdx(idx);
    },
    [trace],
  );

  return (
    <div className="space-y-4">
      {/* Composer */}
      <div className="glass rounded-[var(--radius-xl)] p-4">
        <div className="flex items-center gap-2 mb-2 text-xs text-[var(--color-fg-dim)]">
          <Activity size={14} /> Send a message to watch the pipeline light up
        </div>
        <div className="flex gap-2">
          <input
            className="input"
            placeholder="e.g. plan my day · create a 3-day Rust roadmap · find me AI jobs"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <Button variant="primary" loading={sending} onClick={submit}>
            <Send size={15} /> Run
          </Button>
          <Button variant="ghost" onClick={reset} disabled={!trace.length}>
            <RotateCcw size={15} />
          </Button>
        </div>
        {decision ? (
          <div className="flex flex-wrap items-center gap-2 mt-3 text-xs">
            <span className="text-[var(--color-fg-dim)]">Decision:</span>
            {Object.entries(decision).map(([k, v]) => (
              <span key={k} className="badge">
                {k}: {Array.isArray(v) ? v.join(" → ") : String(v)}
              </span>
            ))}
          </div>
        ) : null}
        {error ? <p className="text-xs text-[var(--color-danger)] mt-2">{error}</p> : null}
      </div>

      <div className="grid lg:grid-cols-[1fr_300px] gap-4">
        <div className="space-y-4 min-w-0">
          <div className="card !p-0 overflow-hidden h-[500px] relative">
            {!graph ? (
              <div className="grid place-items-center h-full text-sm text-[var(--color-fg-muted)]">
                Loading architecture…
              </div>
            ) : (
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                fitView
                minZoom={0.2}
                maxZoom={1.6}
                proOptions={{ hideAttribution: true }}
                nodesConnectable={false}
                onNodeClick={(_, n) => selectNode(n.id)}
              >
                <Background color="#1c2634" gap={22} />
                <Controls showInteractive={false} />
                <MiniMap
                  pannable
                  zoomable
                  style={{ background: "#0c1119" }}
                  maskColor="rgba(10,14,22,0.7)"
                />
              </ReactFlow>
            )}
          </div>

          <FlowInspector event={inspected} index={effectiveIdx} />
        </div>

        <div className="space-y-4">
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Wrench size={16} className="text-[var(--color-info)]" />
              <h3 className="text-sm font-semibold">Tools</h3>
            </div>
            <ToolRail descriptor={descriptor} used={usedTools} />
          </div>

          <div className="card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Play size={15} className="text-[var(--color-accent)]" />
              <h3 className="text-sm font-semibold">Trace</h3>
              <span className="badge ml-auto">
                {Math.min(step, trace.length)}/{trace.length}
              </span>
            </div>
            <TraceList
              trace={trace}
              step={step}
              selectedIdx={effectiveIdx}
              onSelect={setSelectedIdx}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function ToolRail({
  descriptor,
  used,
}: {
  descriptor: ArchitectureDescriptor | null;
  used: Set<string>;
}) {
  if (!descriptor) return <p className="text-xs text-[var(--color-fg-dim)]">Loading…</p>;
  return (
    <div className="space-y-3 max-h-[300px] overflow-y-auto pr-1">
      {descriptor.tool_groups.map((group) => (
        <div key={group.name}>
          <div className="text-[0.68rem] uppercase tracking-wide text-[var(--color-fg-dim)] mb-1.5">
            {group.name}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {group.tools.map((t) => {
              const isUsed = used.has(t.name);
              return (
                <span
                  key={t.name}
                  title={t.desc}
                  className="badge transition-colors"
                  style={
                    isUsed
                      ? {
                          color: "var(--color-info)",
                          borderColor: "var(--color-info)",
                          background: "rgba(56,189,248,0.12)",
                        }
                      : undefined
                  }
                >
                  {isUsed ? "● " : ""}
                  {t.name}
                </span>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function FlowInspector({ event, index }: { event?: TraceEvent; index: number }) {
  if (!event) {
    return (
      <div className="card p-4">
        <h3 className="text-sm font-semibold mb-2">Message flow</h3>
        <p className="text-xs text-[var(--color-fg-dim)]">
          Send a message, then click any node or trace row to inspect the exact
          input and output payloads that flowed through it.
        </p>
      </div>
    );
  }

  const title = event.kind === "tool" ? `tool · ${event.tool}` : event.node;
  const input = event.kind === "tool" ? event.args : event.io?.in;
  const output =
    event.kind === "tool"
      ? event.error
        ? { error: event.error }
        : { result: event.result }
      : event.io?.out;

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-3">
        <h3 className="text-sm font-semibold">Message flow</h3>
        <span className="badge">{title}</span>
        {event.ms != null ? <span className="badge">{event.ms}ms</span> : null}
        <span className="text-[0.62rem] text-[var(--color-fg-dim)] ml-auto">
          step {index + 1}
        </span>
      </div>
      <div className="grid md:grid-cols-2 gap-3">
        <FlowBlock label="INPUT" data={input} />
        <FlowBlock label="OUTPUT" data={output} accent />
      </div>
    </div>
  );
}

function FlowBlock({
  label,
  data,
  accent,
}: {
  label: string;
  data?: Record<string, unknown>;
  accent?: boolean;
}) {
  const hasData = data && Object.keys(data).length > 0;
  return (
    <div>
      <div
        className="text-[0.62rem] uppercase tracking-wide mb-1.5"
        style={{ color: accent ? "var(--color-accent)" : "var(--color-fg-dim)" }}
      >
        {label}
      </div>
      <div className="rounded-[var(--radius)] border border-[var(--color-border-soft)] bg-[var(--color-bg-elev)] p-2.5 max-h-[190px] overflow-auto">
        {!hasData ? (
          <span className="text-[0.7rem] text-[var(--color-fg-dim)]">— none —</span>
        ) : (
          <dl className="space-y-1.5">
            {Object.entries(data!).map(([k, v]) => (
              <div key={k} className="text-[0.72rem] leading-snug">
                <dt className="text-[var(--color-fg-dim)] font-mono">{k}</dt>
                <dd className="text-[var(--color-fg-muted)] break-words whitespace-pre-wrap">
                  {v === null || v === undefined || v === ""
                    ? "—"
                    : typeof v === "object"
                      ? JSON.stringify(v)
                      : String(v)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </div>
  );
}

function TraceList({
  trace,
  step,
  selectedIdx,
  onSelect,
}: {
  trace: TraceEvent[];
  step: number;
  selectedIdx: number;
  onSelect: (i: number) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [step]);

  if (!trace.length) {
    return (
      <p className="text-xs text-[var(--color-fg-dim)]">
        No turn yet — send a message to record its execution.
      </p>
    );
  }

  return (
    <div ref={listRef} className="space-y-1.5 max-h-[300px] overflow-y-auto pr-1">
      {trace.map((e, i) => {
        const played = i < step;
        const label = e.kind === "tool" ? e.tool : e.node;
        const detailParts: string[] = [];
        if (e.kind === "node" && e.ms != null) detailParts.push(`${e.ms}ms`);
        if (e.kind === "tool") detailParts.push(e.ok ? "ok" : `error`);
        return (
          <button
            key={`${label}-${i}`}
            onClick={() => onSelect(i)}
            className="w-full flex items-center gap-2 text-xs transition-all text-left rounded px-1.5 py-1 hover:bg-[var(--color-surface-2)]"
            style={{
              opacity: played ? 1 : 0.35,
              background:
                i === selectedIdx ? "var(--color-surface-2)" : undefined,
              boxShadow:
                i === selectedIdx ? "inset 2px 0 0 var(--color-accent)" : undefined,
            }}
          >
            <span
              className="h-1.5 w-1.5 rounded-full shrink-0"
              style={{
                background:
                  e.kind === "tool"
                    ? e.ok === false
                      ? "var(--color-danger)"
                      : "var(--color-info)"
                    : "var(--color-accent)",
              }}
            />
            <span className="truncate" style={{ color: "var(--color-fg-muted)" }}>
              {e.kind === "tool" ? "▸ " : ""}
              {label}
            </span>
            <span className="ml-auto text-[0.62rem] text-[var(--color-fg-dim)] shrink-0">
              {detailParts.join(" · ")}
            </span>
          </button>
        );
      })}
    </div>
  );
}
