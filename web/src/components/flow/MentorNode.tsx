"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";
import type { FlowNodeData } from "@/lib/flow";

const KIND_COLOR: Record<string, string> = {
  terminal: "#5d6a7e",
  node: "#6ea8fe",
  llm: "#8b5cf6",
  tool: "#38bdf8",
  agent: "#34d399",
};

const KIND_LABEL: Record<string, string> = {
  terminal: "terminal",
  node: "node",
  llm: "LLM",
  tool: "tool",
  agent: "agent",
};

export function MentorNode({ data }: NodeProps<Node<FlowNodeData>>) {
  const color = KIND_COLOR[data.kind] ?? "#6ea8fe";
  const { state } = data;

  return (
    <div
      className={cn(
        "rounded-[var(--radius)] border px-3 py-2 w-[172px] transition-all duration-300",
        state === "active" && "shadow-[0_0_0_3px_rgba(110,168,254,0.35)]",
      )}
      style={{
        background: state === "active" ? "var(--color-surface-2)" : "var(--color-surface)",
        borderColor:
          state === "error"
            ? "var(--color-danger)"
            : state === "idle"
              ? "var(--color-border-soft)"
              : color,
        opacity: state === "idle" ? 0.62 : 1,
        boxShadow:
          state === "visited" && data.kind === "agent"
            ? `0 0 12px ${color}55`
            : undefined,
      }}
      title={data.desc}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="flex items-center justify-between gap-2">
        <span className="text-[0.78rem] font-medium leading-tight truncate">
          {data.label}
        </span>
        <span
          className="text-[0.58rem] uppercase tracking-wide shrink-0"
          style={{ color }}
        >
          {KIND_LABEL[data.kind] ?? data.kind}
        </span>
      </div>
      {state === "active" ? (
        <span
          className="mt-1 block h-0.5 rounded-full animate-pulse"
          style={{ background: color }}
        />
      ) : null}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}
