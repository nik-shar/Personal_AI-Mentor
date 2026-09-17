import type { ArchitectureDescriptor } from "./types";

/** Visual state of a node in the architecture diagram. */
export type NodeState = "idle" | "visited" | "active" | "error";

export interface FlowNodeData extends Record<string, unknown> {
  label: string;
  kind: string;
  desc?: string;
  state: NodeState;
}

export interface FlowEdgeData extends Record<string, unknown> {
  traversed: boolean;
}

/**
 * Hand-tuned layout for the orchestrator pipeline. Coordinates are chosen to
 * read left→right like the real turn flow, with the three branches (direct /
 * clarify / nudge / recall) stacked and the agent cluster off to the right.
 */
const POSITIONS: Record<string, { x: number; y: number }> = {
  START: { x: 20, y: 300 },
  intake_node: { x: 200, y: 300 },
  summarize_node: { x: 400, y: 300 },
  reason_node: { x: 600, y: 300 },
  direct_response_node: { x: 820, y: 96 },
  clarify_node: { x: 820, y: 232 },
  nudge_node: { x: 820, y: 368 },
  long_term_recall_node: { x: 820, y: 520 },
  dispatch_node: { x: 1040, y: 520 },
  context_builder_node: { x: 1240, y: 520 },
  agent_executor_node: { x: 1440, y: 520 },
  linkedin_writer: { x: 1660, y: 120 },
  goal_decomposer: { x: 1660, y: 260 },
  job_hunter: { x: 1660, y: 400 },
  fallback: { x: 1660, y: 540 },
  memory_merger_node: { x: 1880, y: 520 },
  next_pipeline_agent_node: { x: 1880, y: 700 },
  format_output_node: { x: 2100, y: 520 },
  END: { x: 2300, y: 520 },
};

export const NODE_W = 172;
export const NODE_H = 60;

export interface BuiltGraph {
  nodes: {
    id: string;
    type: string;
    position: { x: number; y: number };
    data: FlowNodeData;
    draggable: boolean;
  }[];
  edges: {
    id: string;
    source: string;
    target: string;
    animated: boolean;
    style: React.CSSProperties;
    type: string;
    data: FlowEdgeData;
  }[];
}

export function buildGraph(descriptor: ArchitectureDescriptor): BuiltGraph {
  const nodes: BuiltGraph["nodes"] = [];
  const nodeIds = new Set<string>();

  const push = (id: string, label: string, kind: string, desc?: string) => {
    nodeIds.add(id);
    nodes.push({
      id,
      type: "mentor",
      position: POSITIONS[id] ?? { x: 40, y: 40 },
      draggable: true,
      data: { label, kind, desc, state: "idle" },
    });
  };

  push("START", "START", "terminal", "Turn begins");
  for (const n of descriptor.orchestrator.nodes) {
    push(n.id, n.label, n.kind, n.desc);
  }
  push("END", "END", "terminal", "Turn complete");

  // Agent cluster (invoked by agent_executor_node).
  for (const [key, agent] of Object.entries(descriptor.agents)) {
    push(key, agent.label, "agent", `${agent.nodes.length} internal nodes`);
  }

  const edges: BuiltGraph["edges"] = [];
  const addEdge = (from: string, to: string, dashed = false) => {
    if (!nodeIds.has(from) || !nodeIds.has(to)) return;
    edges.push({
      id: `${from}->${to}`,
      source: from,
      target: to,
      animated: false,
      type: "smoothstep",
      data: { traversed: false },
      style: {
        stroke: "#2c3a4f",
        strokeWidth: 1.5,
        strokeDasharray: dashed ? "5 4" : undefined,
      },
    });
  };

  for (const e of descriptor.orchestrator.edges) addEdge(e.from, e.to);
  for (const key of Object.keys(descriptor.agents)) {
    addEdge("agent_executor_node", key, true);
  }

  return { nodes, edges };
}

export interface ReplayState {
  visited: Set<string>;
  tools: Set<string>;
  traversed: Set<string>;
  active: string | null;
}

/**
 * Fold a trace prefix into highlight state: which nodes/edges/tools are active
 * after `step` events have played. Called repeatedly during the replay.
 */
export function stateAtStep(
  trace: { kind: string; node?: string; tool?: string }[],
  step: number,
): ReplayState {
  const visited = new Set<string>();
  const tools = new Set<string>();
  const traversed = new Set<string>();
  let active: string | null = null;
  let prev: string | null = null;

  for (let i = 0; i < step && i < trace.length; i++) {
    const ev = trace[i];
    if (ev.kind === "node" && ev.node) {
      if (prev && prev !== ev.node) traversed.add(`${prev}->${ev.node}`);
      visited.add(ev.node);
      active = ev.node;
      prev = ev.node;
    } else if (ev.kind === "tool" && ev.tool) {
      tools.add(ev.tool);
    }
  }
  return { visited, tools, traversed, active };
}
