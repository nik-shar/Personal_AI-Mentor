/**
 * Phase 1 read intents — the mentor bridge tools.
 *
 * Every tool is named after a mentor *intent* ("what do I know about him?"),
 * never a table, so its implementation can move from the Python sidecar to
 * TypeScript later without changing the tool contract. The seam is the tool
 * name + schema, not the transport.
 *
 * Gate decisions (see docs/PI-Mentor Boundary.md):
 *   get_profile           → G1  reads Postgres, owned by Python
 *   get_identity          → G1  composes profile_facts + dna_memory (both Python-owned),
 *                               and the rendering stays Python so the text cannot drift
 *   get_history           → G1  composes conversation_sessions + episodic + profile_facts;
 *                               same rule — one rendering, in Python
 *   recall_memories       → G2  Postgres + the 384-dim sentence-transformer
 *   get_day_grid          → G5  48-slot aggregate over `day_slots`
 *   available_topic_nodes → G1  DAG traversal + progress writes
 *   get_momentum          → G5  aggregate over `schedule_events`
 *
 * All five are read-only. Write intents arrive in Phase 3 and stay Python-side:
 * TypeScript never writes a store.
 */

import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { MENTOR_TOOLS, MENTOR_TOOLS_VERSION } from "../src/generated/mentor-tools.ts";
import { callTool } from "../src/sidecar.ts";

// ---------------------------------------------------------------------------
// Payload shapes (mirrors of the generated pydantic contracts)
// ---------------------------------------------------------------------------

interface ProfilePayload {
  facts: { key: string; value: unknown; updated_at?: string | null }[];
  count: number;
  error?: string | null;
}

interface IdentityPayload {
  block: string | null;
  found: boolean;
  counts: Record<string, number>;
  degraded: string[];
  error?: string | null;
}

interface HistoryPayload {
  block: string | null;
  found: boolean;
  counts: Record<string, number>;
  degraded: string[];
  error?: string | null;
}

interface RecallPayload {
  block: string | null;
  found: boolean;
  error?: string | null;
}

interface DaySlot {
  slot_index: number;
  state?: string;
  status?: string;
  label?: string;
  event_title?: string;
  /** Already behind the clock (computed in Python). */
  elapsed?: boolean;
  /** The slot the current moment falls in. */
  current?: boolean;
}

interface FreeWindow {
  start_clock: string;
  end_clock: string;
  duration_min: number;
}

interface DayGridPayload {
  date: string;
  grid: { slots?: DaySlot[]; free_windows?: FreeWindow[] };
  is_today?: boolean;
  now_clock?: string | null;
  elapsed_minutes?: number | null;
  remaining_minutes?: number | null;
  errors?: string[];
  error?: string | null;
}

interface TopicNodesPayload {
  nodes: { graph_title: string; node_id: string; title: string; status: string }[];
  count: number;
  error?: string | null;
}

interface MomentumPayload {
  streak_days: number;
  completion_rate_today: number;
  completion_rate_7d: number;
  momentum_trend: string;
  blocks_today: number;
  error?: string | null;
}

// ---------------------------------------------------------------------------
// Formatting — keep tool output compact; never dump raw rows into the prompt
// ---------------------------------------------------------------------------

const FREE_STATES = new Set(["", "free"]);

/** 'HH:MM' clock for a 30-minute slot index (0..47). */
function slotClock(slotIndex: number): string {
  const minutes = slotIndex * 30;
  if (minutes >= 1440) return "24:00";
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function renderValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function formatFacts(payload: ProfilePayload): string {
  if (payload.error) return `Profile unavailable: ${payload.error}`;
  if (payload.facts.length === 0) {
    return "Nothing is stored about him yet. You do not know who he is — ask, don't guess.";
  }
  const lines = ["What you know about Nik (from memory, never from assumption):"];
  for (const fact of payload.facts) {
    lines.push(`- ${fact.key}: ${renderValue(fact.value)}`);
  }
  return lines.join("\n");
}

/**
 * The identity block is rendered in Python (`memory/identity.py`), deliberately:
 * the provenance format, the section order and the "do not state as fact" wording
 * are one implementation, not two. This side only decides what to say when there
 * is nothing to show, so a broken or empty read can never be mistaken for an
 * answer.
 */
function formatIdentity(payload: IdentityPayload): string {
  if (payload.error) return `Identity unavailable: ${payload.error}`;
  if (!payload.found || !payload.block) {
    return "You know nothing about him yet. You do not know who he is — ask, don't guess.";
  }
  return payload.block;
}

/**
 * Also rendered in Python (`memory/history.py`), for the same reason as the
 * identity block: there is one rendering of the past, not two. This side only
 * decides what to say when there is nothing to show — and, importantly, when a
 * source was unreadable rather than empty.
 */
function formatHistory(payload: HistoryPayload): string {
  if (payload.error) return `History unavailable: ${payload.error}`;
  if (!payload.found || !payload.block) {
    return "Nothing has happened yet that you can recall — this is the beginning of the record.";
  }
  return payload.block;
}

/** Group contiguous slots into labeled runs so the grid stays readable. */
function groupSlotRuns(slots: DaySlot[]): string[] {
  const ordered = [...slots].sort((a, b) => a.slot_index - b.slot_index);
  if (ordered.length === 0) return [];

  const runs: string[] = [];
  let from = ordered[0];
  let to = ordered[0];

  const flush = (): void => {
    const label = to.label ?? from.label ?? to.event_title ?? from.event_title ?? to.state ?? "busy";
    runs.push(`  - ${slotClock(from.slot_index)}-${slotClock(to.slot_index + 1)} ${label}`);
  };

  for (const slot of ordered.slice(1)) {
    if (slot.slot_index === to.slot_index + 1) {
      to = slot;
      continue;
    }
    flush();
    from = slot;
    to = slot;
  }
  flush();
  return runs;
}

/** '9h40m' / '45m' — the shape the calendar already speaks. */
function humanDuration(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  if (total < 60) return `${total}m`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${hours}h${String(rest).padStart(2, "0")}m` : `${hours}h`;
}

function formatDayGrid(payload: DayGridPayload): string {
  if (payload.error) return `Calendar unavailable: ${payload.error}`;

  const slots = payload.grid?.slots ?? [];
  const windows = payload.grid?.free_windows ?? [];
  const problems = payload.errors ?? [];

  // An empty grid WITH read errors is unreadable, not unplanned. Saying "nothing
  // is blocked" there would be a confident false claim about his day — the exact
  // failure the errors channel exists to prevent.
  if (slots.length === 0 && problems.length > 0) {
    return (
      `The calendar could not be read (${problems.join("; ")}). ` +
      `Do not tell him the day is free — you do not know what is on it.`
    );
  }
  if (slots.length === 0) return `No calendar grid exists for ${payload.date}.`;

  const lines: string[] = [];
  if (payload.is_today && payload.now_clock) {
    lines.push(
      `Now ${payload.now_clock} — ${humanDuration(payload.elapsed_minutes ?? 0)} elapsed, ` +
        `${humanDuration(payload.remaining_minutes ?? 0)} left in the day (computed by code).`,
    );
  }
  lines.push(`Calendar for ${payload.date} (computed by code — never guess availability):`);

  const blocked = slots.filter((slot) => !FREE_STATES.has(slot.state ?? ""));
  if (blocked.length > 0) {
    lines.push("Blocked or anchored:");
    lines.push(...groupSlotRuns(blocked));
  } else {
    lines.push("Nothing is blocked or anchored.");
  }

  // A window is in the past once its LAST slot has elapsed.
  const elapsedSlots = new Set(slots.filter((slot) => slot.elapsed).map((slot) => slot.slot_index));
  const windowIsPast = (window: FreeWindow): boolean => {
    const span = Math.max(1, Math.round(window.duration_min / 30));
    return elapsedSlots.has(window.start_slot + span - 1);
  };

  lines.push("Free windows:");
  if (windows.length === 0) {
    lines.push("  - none");
  } else {
    for (const window of windows) {
      const past = windowIsPast(window);
      lines.push(
        `  - ${window.start_clock}-${window.end_clock} (${window.duration_min} min)` +
          (past ? "  ← ALREADY ELAPSED: do not offer it" : ""),
      );
    }
  }
  return lines.join("\n");
}

function formatTopicNodes(payload: TopicNodesPayload): string {
  if (payload.error) return `Topic graphs unavailable: ${payload.error}`;
  if (payload.nodes.length === 0) {
    return "No unlocked topic nodes right now (nothing in progress and no prerequisites satisfied).";
  }
  const lines = ["Study-ready topic nodes (prerequisites checked in code):"];
  for (const node of payload.nodes) {
    lines.push(`- ${node.graph_title} :: ${node.title} (${node.status})`);
  }
  return lines.join("\n");
}

function formatMomentum(payload: MomentumPayload): string {
  if (payload.error) return `Momentum unavailable: ${payload.error}`;
  return [
    "Momentum (computed fresh from schedule events, never cached):",
    `- streak_days: ${payload.streak_days}`,
    `- completion_rate_today: ${payload.completion_rate_today}`,
    `- completion_rate_7d: ${payload.completion_rate_7d}`,
    `- momentum_trend: ${payload.momentum_trend}`,
    `- blocks_today: ${payload.blocks_today}`,
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

export default function mentorReadTools(pi: ExtensionAPI) {
  pi.registerTool({
    name: "get_profile",
    label: "Get profile",
    description:
      "Read what the mentor actually knows about Nik: identity, career targets, goals, and learning state.",
    promptSnippet: "get_profile: what the mentor knows about Nik (identity, targets, goals, learning state)",
    promptGuidelines: [
      "Call get_profile before making any claim about Nik's goals, role, targets, or learning state.",
    ],
    parameters: Type.Object({
      keys: Type.Optional(
        Type.Array(Type.String(), {
          description: "Explicit profile keys to read; omit for the default mentor set",
        }),
      ),
      max_facts: Type.Optional(Type.Number({ description: "Maximum facts to return (default 40)" })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<ProfilePayload>("get_profile", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatFacts(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "get_identity",
    label: "Get identity",
    description:
      "The composed picture of who Nik is — the structured profile PLUS what you have learned about him through conversation (facts, goals, preferences), each with its provenance and confidence, plus anything time-sensitive and anything still awaiting his confirmation. Richer than get_profile.",
    promptSnippet:
      "get_identity: who he is — profile + what you learned about him, with provenance and time-sensitive items",
    promptGuidelines: [
      "Call get_identity when you need to ground a personal claim, recall who he is, or check what you are unsure about.",
      "Prefer get_identity over get_profile: it also carries what you learned in conversation, what is time-sensitive, and what is still unconfirmed.",
      "Items marked unconfirmed are your inferences, not his words — never state them as though he told you.",
    ],
    parameters: Type.Object({
      limit: Type.Optional(
        Type.Number({ description: "Maximum learned memories to include (default 40)" }),
      ),
      include_profile: Type.Optional(
        Type.Boolean({ description: "Include the structured profile half (default true)" }),
      ),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<IdentityPayload>("get_identity", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatIdentity(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "get_history",
    label: "Get history",
    description:
      "The narrative of the past: the rolling summary of the thread so far, recent daily recaps, past conversations (when, how long, what about), and what he said he was doing in his own words. Read chronologically, not by similarity — so it does not change with how you ask.",
    promptSnippet:
      "get_history: what happened — the thread so far, recent days, past conversations, and his own day log",
    promptGuidelines: [
      "Call get_history when a question depends on the past: what we discussed, what he actually did, how the last few days went.",
      "Use get_history for 'what happened'; use recall_memories when you need what RELATES to a specific topic or phrase.",
      "The day log is his own account of his real day — quote it rather than tidying it into something you would have said.",
      "If a source comes back unreadable, say that it was unreadable. Do not report it as 'nothing happened'.",
    ],
    parameters: Type.Object({
      sessions: Type.Optional(Type.Number({ description: "How many past conversations (default 4)" })),
      recaps: Type.Optional(Type.Number({ description: "How many daily recaps (default 3)" })),
      day_log: Type.Optional(Type.Number({ description: "How many day-log entries (default 25)" })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<HistoryPayload>("get_history", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatHistory(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "recall_memories",
    label: "Recall memories",
    description:
      "Semantic recall over older episodic memories (warm/cold tiers) — what he said or did around a topic.",
    promptSnippet: "recall_memories: semantic search over older memories for a topic or phrase",
    promptGuidelines: [
      "Use recall_memories when a question depends on older context (weeks or months back) that is not in the recent transcript.",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "Natural-language query to search memory with" }),
      limit: Type.Optional(Type.Number({ description: "How many memories to surface (default 5)" })),
      hot_threshold_days: Type.Optional(
        Type.Number({ description: "Only search memories older than this many days (default 14)" }),
      ),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<RecallPayload>("recall_memories", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      const text = result.data.block ?? "No relevant older memories found for that query.";
      return { content: [{ type: "text", text }], details: result.data };
    },
  });

  pi.registerTool({
    name: "get_day_grid",
    label: "Get day grid",
    description:
      "The 48-slot day grid for a date: blocked/anchored windows, code-computed free windows, and the clock — which slots have already elapsed, and exactly how much of the day is left. Answers 'am I free at 4?' and 'how much time do I have?'. Omit `date` for today — never pass a guessed date.",
    promptSnippet:
      "get_day_grid: blocked/anchored slots, free windows, and how much of the day has elapsed / is left",
    promptGuidelines: [
      "Never answer scheduling or availability questions from memory — always call get_day_grid.",
      "Omit the date argument when the user means today. Never pass an assumed or remembered date.",
      "Use the elapsed/remaining figures the grid returns; do not compute or estimate time left yourself.",
      "Never offer a window the grid marks ALREADY ELAPSED — it is in the past.",
    ],
    parameters: Type.Object({
      date: Type.Optional(Type.String({ description: "ISO date YYYY-MM-DD; defaults to today in his timezone" })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<DayGridPayload>("get_day_grid", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatDayGrid(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "available_topic_nodes",
    label: "Available topic nodes",
    description:
      "Study-ready topic nodes across every roadmap: in-progress first, then unlocked (all prerequisites done) not-started nodes.",
    promptSnippet: "available_topic_nodes: what he can study right now, with prerequisites checked in code",
    promptGuidelines: [
      "Never guess which topics are unlocked — call available_topic_nodes before suggesting what to study.",
    ],
    parameters: Type.Object({
      limit: Type.Optional(Type.Number({ description: "Maximum nodes to return (default 15)" })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<TopicNodesPayload>("available_topic_nodes", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatTopicNodes(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "get_momentum",
    label: "Get momentum",
    description:
      "Streak, completion rates, and momentum trend computed fresh from schedule events. Answers 'is he actually consistent?'.",
    promptSnippet: "get_momentum: streak, completion rates, and momentum trend computed from real schedule data",
    promptGuidelines: [
      "Never state a streak or consistency number from memory — call get_momentum.",
    ],
    parameters: Type.Object({}),
    async execute(_toolCallId, params) {
      const result = await callTool<MomentumPayload>("get_momentum", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatMomentum(result.data) }], details: result.data };
    },
  });

  // Contract drift check: the generated descriptor module is produced from the
  // Python pydantic models. If a declared tool is not registered here (or vice
  // versa), the two runtimes have silently diverged — surface it loudly.
  pi.on("session_start", (_event, ctx) => {
    const registered = new Set(pi.getAllTools().map((tool) => tool.name));
    const notRegistered = MENTOR_TOOLS.filter((tool) => !registered.has(tool.name)).map((tool) => tool.name);
    if (notRegistered.length === 0) return;

    const message =
      `mentor tool drift (sidecar contract v${MENTOR_TOOLS_VERSION}): declared but not registered → ` +
      `${notRegistered.join(", ")}. Regenerate with scripts/export_mentor_tools_schema.py`;
    console.warn(`[mentor] ${message}`);
    if (ctx.hasUI) ctx.ui.notify(message, "warning");
  });
}