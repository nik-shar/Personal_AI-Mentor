/**
 * TypeScript mirrors of the FastAPI response contracts (api/main.py).
 * Kept intentionally loose where the backend returns free-form dicts
 * (profile facts, schedule payloads) so we never lie about the shape.
 */

export interface HealthResponse {
  status: string;
  obsidian_vault: string;
  active_sessions: number;
}

export interface AgentResultDTO {
  task_id?: string;
  agent_name?: string;
  task_type?: string;
  status?: string;
  output?: string;
  error_message?: string | null;
  clarification_needed?: string | null;
  draft_suggestions?: DraftSuggestionDTO[];
  [key: string]: unknown;
}

export interface DraftSuggestionDTO {
  kind: string;
  content: string;
  suggested_destination?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ChatResponse {
  session_id: string;
  response_text: string;
  agent_pipeline: string[];
  pipeline_step: number;
  results: AgentResultDTO[];
  trace: TraceEvent[];
}

/** One event in the per-turn execution trace (drives the /flow visualizer). */
export interface TraceEvent {
  kind: "node" | "tool";
  node?: string;
  tool?: string;
  ms?: number;
  status?: string;
  ok?: boolean;
  error?: string | null;
  args?: Record<string, unknown>;
  result?: string | null;
  detail?: Record<string, unknown>;
  /** Data payloads flowing in/out of the node (no prompt templates). */
  io?: { in?: Record<string, unknown>; out?: Record<string, unknown> };
}

export interface ArchitectureNode {
  id: string;
  label: string;
  kind: "node" | "llm" | "tool";
  desc: string;
}

export interface ArchitectureEdge {
  from: string;
  to: string;
}

export interface ArchitectureDescriptor {
  orchestrator: {
    nodes: ArchitectureNode[];
    edges: ArchitectureEdge[];
  };
  agents: Record<string, { label: string; nodes: string[] }>;
  tool_groups: { name: string; tools: { name: string; desc: string }[] }[];
}

export interface RoadmapNode {
  id: string;
  title: string;
  status: string;
  estimated_hours: number;
  resources?: string[];
  notes?: string;
}

export interface RoadmapSummary {
  topic_id: string;
  title: string;
  total_nodes: number;
  completed: number;
  in_progress: number;
  not_started: number;
  total_hours: number;
  nodes: { id: string; title: string; status: string; estimated_hours: number }[];
}

export interface RoadmapsResponse {
  roadmaps: RoadmapSummary[];
  vault_path: string;
}

export interface GraphResponse {
  graph_id: string;
  title: string;
  total_nodes: number;
  nodes: RoadmapNode[];
  edges: { from: string; to: string }[];
}

export interface NodeDetail {
  filename: string;
  path: string;
  body: string;
}

export interface ProfileResponse {
  profile: Record<string, unknown>;
}

export interface ProfileFactRow {
  category: string;
  key: string;
  value: unknown;
  source?: string | null;
  updated_at?: string | null;
}

export interface ProfileFactsResponse {
  facts: ProfileFactRow[];
}

export interface DNAMemory {
  id: string;
  content: string;
  memory_type: string;
  confidence: number;
  confidence_ceiling: number;
  source: string;
  user_confirmed: boolean;
  tags: string[];
  active: boolean;
  due_at?: string | null;
  created_at?: string | null;
  last_confirmed?: string | null;
  superseded_by?: string | null;
  confirmation_count: number;
  distance?: number | null;
  composite_score?: number | null;
}

export interface MemoriesResponse {
  count: number;
  memories: DNAMemory[];
}

export interface ScheduleEvent {
  id: string;
  title: string;
  category?: string | null;
  start_time?: string | null;
  duration_min?: number;
  status?: string;
  priority?: string;
  linked_goal?: string | null;
  block_kind?: string;
  notes?: string | null;
}

export interface ScheduleResponse {
  count: number;
  events: ScheduleEvent[];
}

export interface DaySlot {
  slot: number;
  start: string;
  state: string;
  event_id?: string | null;
  title?: string | null;
  category?: string | null;
  kind?: string | null;
}

export interface AvailabilityResponse {
  date: string;
  slots: DaySlot[];
  free_windows: { start: string; end: string; duration_min: number }[];
  free_minutes: number;
}

export interface Application {
  company: string;
  role_title?: string;
  stage: string;
  applied_date?: string | null;
  last_updated?: string | null;
  job_url?: string | null;
  location?: string | null;
  referral_contact?: string | null;
  notes?: string | null;
  days_since_update?: number | null;
  is_stale?: boolean;
  is_terminal?: boolean;
  has_jd?: boolean;
  has_tailored_resume?: boolean;
  fit_score?: number | null;
  source?: string | null;
  [key: string]: unknown;
}

export interface PipelineStats {
  total: number;
  active: number;
  closed: number;
  stale: number;
  response_rate: number;
  by_stage: Record<string, number>;
}

export interface ApplicationsResponse {
  count: number;
  stats: PipelineStats;
  applications: Application[];
}

export interface ApplicationHistoryResponse {
  count: number;
  events: {
    id: string;
    occurred_at: string;
    event_type: string;
    content: string;
    tags?: string[];
  }[];
}
