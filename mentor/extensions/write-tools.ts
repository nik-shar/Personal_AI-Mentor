/**
 * Phase 3 write intents + the native pure tools.
 *
 * This file is where the boundary is most visible, and deliberately so:
 *
 *   SIDECAR (G1 — Python owns the store):
 *     save_daily_plan, find_available_slots, place_time_block, set_anchor,
 *     log_learning_session
 *
 *   NATIVE (G3 — pure function of its arguments):
 *     trim_plan_to_fit, compute_learning_streak
 *
 * The two native tools import from `../src/pure/planning.ts` and never touch
 * the network. They are byte-for-byte ports of `orchestrator/harness.py`,
 * locked by `mentor/test/planning.test.ts`. If one of them ever needs the
 * database, it stops being a pure function and moves behind the sidecar — the
 * tool name and schema stay identical either way (boundary rule 2).
 *
 * Account of a real hazard: `compute_learning_streak` here is *advice* (the
 * model reasoning about the arithmetic), while `log_learning_session` on the
 * sidecar is *truth* (the streak actually persisted). They are the same rule,
 * so if they ever disagree the port has drifted — which is why the pure one is
 * tested against the Python behaviour.
 */

import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { computeLearningStreak, formatDuration, trimPlanToFit } from "../src/pure/planning.ts";
import { callTool } from "../src/sidecar.ts";

// ---------------------------------------------------------------------------
// Sidecar payload shapes
// ---------------------------------------------------------------------------

interface SavePlanPayload {
  saved: boolean;
  message: string;
  total_minutes?: number | null;
  error?: string | null;
}

interface SlotsPayload {
  date: string;
  duration_min: number;
  candidates: { start_clock: string; end_clock: string; start_slot: number; duration_min: number }[];
  error?: string | null;
}

interface PlaceBlockPayload {
  status: string;
  event?: { id?: string; title?: string } | null;
  slots: string[];
  start_clock?: string | null;
  end_clock?: string | null;
  error?: string | null;
}

interface AnchorPayload {
  status: string;
  slots: string[];
  slot_count?: number | null;
  dates?: string[];
  spans_midnight?: boolean | null;
  summary?: string | null;
  anchor?: string | null;
  label?: string | null;
  error?: string | null;
}

interface LogLearningPayload {
  logged: boolean;
  new_streak: number;
  note: string;
  error?: string | null;
}

interface MarkTopicPayload {
  updated: boolean;
  roadmap?: string;
  roadmap_title?: string;
  node?: string;
  node_title?: string;
  status?: string;
  unlocked?: string[];
  remaining?: number;
  note_appended?: boolean;
  error?: string | null;
}

function formatMarkTopic(payload: MarkTopicPayload): string {
  if (payload.error) return `Not updated: ${payload.error}`;
  const lines = [
    `Marked "${payload.node_title}" as ${payload.status} in ${payload.roadmap_title}.`,
  ];
  if (payload.unlocked && payload.unlocked.length > 0) {
    lines.push(
      `That unlocked: ${payload.unlocked.map((t) => `"${t}"`).join(", ")}.`,
    );
  } else if (payload.status === "done") {
    lines.push("Nothing new unlocked yet — the next topics still have unfinished prerequisites.");
  }
  lines.push(`${payload.remaining ?? 0} topic(s) left in that roadmap.`);
  if (payload.note_appended) {
    lines.push("Your note was appended to the topic's session log.");
  }
  return lines.join("\n");
}

interface LearnedConcept {
  concept: string;
  difficulty?: number | null;
  estimated_hours?: number;
  day?: number | null;
  anchors?: string[];
  prerequisites?: string[];
  what_to_cover?: string | null;
}

interface LearnRepoPayload {
  ok: boolean;
  error?: string | null;
  repo?: string;
  commit?: string | null;
  files_scanned?: number;
  summary?: string;
  roadmap?: string;
  title?: string;
  concept_count?: number;
  concepts?: LearnedConcept[];
  anchor_coverage?: string | null;
  dropped_anchors?: string[];
  dropped_concepts?: string[];
  broken_cycles?: string[];
  ground_truth_found?: number;
  ground_truth_total?: number;
  total_hours?: number;
  persisted?: boolean;
  roadmap_path?: string | null;
}

function formatLearnRepo(payload: LearnRepoPayload): string {
  if (payload.error) return `I couldn't build a curriculum from that repo: ${payload.error}`;
  const where = payload.commit ? `${payload.repo} @ ${payload.commit}` : `${payload.repo}`;
  const lines = [
    `${payload.title} — ${payload.concept_count} concepts from ${where}.`,
  ];
  if (payload.summary) lines.push(payload.summary);
  lines.push("");
  (payload.concepts ?? []).forEach((c, i) => {
    const anchors = (c.anchors ?? []).map((a) => `\`${a}\``).join(", ") || "no verified anchors";
    const after = (c.prerequisites ?? []).length
      ? ` — after: ${(c.prerequisites ?? []).join(", ")}`
      : "";
    lines.push(`${i + 1}. ${c.concept} (${c.difficulty ?? "?"}/5, ~${c.estimated_hours ?? "?"}h)${after}`);
    lines.push(`     in: ${anchors}`);
  });
  if (payload.anchor_coverage) lines.push(`\nCitations: ${payload.anchor_coverage}.`);
  if (payload.dropped_anchors?.length) {
    lines.push(
      `Citations I could not verify and removed (tell him — do not hide these): ${payload.dropped_anchors.join("; ")}`,
    );
  }
  if (payload.dropped_concepts?.length) {
    lines.push(`Over the node budget, so not included: ${payload.dropped_concepts.join(", ")}`);
  }
  if (payload.broken_cycles?.length) {
    lines.push(`Prerequisite cycles corrected: ${payload.broken_cycles.join("; ")}`);
  }
  lines.push(
    payload.persisted
      ? `Saved as a roadmap${payload.roadmap_path ? ` in ${payload.roadmap_path}` : ""} — it is ready to study.`
      : "Not saved. Ask him whether to keep it as a roadmap he can work through.",
  );
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function formatSavePlan(payload: SavePlanPayload): string {
  if (payload.error) return `Plan not saved: ${payload.error}`;
  const total = payload.total_minutes != null ? ` (${formatDuration(payload.total_minutes)} total)` : "";
  return `Plan saved${total}. ${payload.message}`;
}

function formatSlots(payload: SlotsPayload): string {
  if (payload.error) return `Slot search unavailable: ${payload.error}`;
  if (payload.candidates.length === 0) {
    return `No window on ${payload.date} fits ${payload.duration_min} min without breaking an anchor. Say so plainly rather than proposing a bad fit.`;
  }
  const lines = [
    `Candidate windows on ${payload.date} for ${payload.duration_min} min (computed by code):`,
  ];
  payload.candidates.forEach((candidate, index) => {
    lines.push(`${index === 0 ? "*" : " "} ${candidate.start_clock}-${candidate.end_clock}`);
  });
  lines.push("Propose the first one (marked *), and note the best alternative in one line.");
  return lines.join("\n");
}

function formatPlaceBlock(payload: PlaceBlockPayload): string {
  if (payload.status !== "ok") {
    return `Could not place that block: ${payload.error ?? "unknown error"}. Reroute rather than retrying the same slot.`;
  }
  return `Booked "${payload.event?.title ?? "block"}" ${payload.start_clock}-${payload.end_clock} (slots: ${
    payload.slots.join(", ") || "none"
  }). It is genuinely on the calendar now — you may say so.`;
}

function formatAnchor(payload: AnchorPayload): string {
  if (payload.status !== "ok") {
    return `Could not set that anchor: ${payload.error ?? "unknown error"}`;
  }
  const name = payload.anchor ?? "anchor";
  const label = payload.label ? ` (${payload.label})` : "";
  const count = payload.slot_count ?? payload.slots.length;
  const covered = payload.summary ?? `slots ${payload.slots.join(", ") || "none"}`;
  // A span that wraps is the normal case for sleep, and the honest report has to
  // name both windows — the mentor must not say "you're protected tonight" when
  // half the window is tomorrow morning.
  const midnight = payload.spans_midnight
    ? " It crosses midnight, so it covers part of two days — name both windows, not just tonight's."
    : "";
  return `Anchor "${name}"${label} set: ${covered} (${count} slots, ${formatDuration(count * 30)}).${midnight} No task can be placed over it.`;
}

function formatLearning(payload: LogLearningPayload): string {
  if (payload.error) return `Session not logged: ${payload.error}`;
  return `Session logged. Streak is now ${payload.new_streak} day(s). ${payload.note}`;
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

export default function mentorWriteTools(pi: ExtensionAPI) {
  // --- NATIVE (G3): pure math, no sidecar hop -------------------------------

  pi.registerTool({
    name: "trim_plan_to_fit",
    label: "Trim plan to fit",
    description:
      "Drop the lowest-priority items until a plan fits the time budget. Must-do items are never dropped; nice-to-have goes before should; breaks go last.",
    promptSnippet: "trim_plan_to_fit: make a plan fit a time budget by dropping the least important items",
    promptGuidelines: [
      "Call trim_plan_to_fit before proposing any plan that might exceed the time he said he has.",
    ],
    parameters: Type.Object({
      items: Type.Array(
        Type.Object({
          title: Type.Optional(Type.String()),
          category: Type.Optional(Type.String()),
          priority: Type.Optional(Type.String({ description: "must | should | nice-to-have" })),
          duration_min: Type.Optional(Type.Number()),
        }),
      ),
      available_minutes: Type.Number({ description: "Total minutes actually available" }),
    }),
    async execute(_toolCallId, params) {
      const result = trimPlanToFit(params.items, params.available_minutes);
      const kept = result.items.map((item) => `- ${item.title ?? "Untitled"} (${item.duration_min ?? 0}m)`);
      const dropped = result.dropped.map(
        (item) => `- ${item.title ?? "Untitled"} (${item.duration_min ?? 0}m)`,
      );
      const lines = [
        `Plan fits ${formatDuration(params.available_minutes)} as ${formatDuration(result.total_minutes)}.`,
        "Kept:",
        ...(kept.length ? kept : ["- (nothing)"]),
      ];
      if (dropped.length) {
        lines.push("Dropped:", ...dropped, "Name the tradeoff out loud — never drop silently.");
      }
      return { content: [{ type: "text", text: lines.join("\n") }], details: result };
    },
  });

  pi.registerTool({
    name: "compute_learning_streak",
    label: "Compute learning streak",
    description:
      "The streak arithmetic: advance by one for a newly logged day, hold if today is already logged, reset to zero when he explicitly says he learned nothing.",
    promptSnippet: "compute_learning_streak: exact streak arithmetic before quoting a streak number",
    promptGuidelines: [
      "Use compute_learning_streak for the arithmetic, then log_learning_session to make it real.",
    ],
    parameters: Type.Object({
      current_streak: Type.Number({ description: "Streak before today" }),
      is_no_learning_day: Type.Boolean({ description: "He explicitly said he learned nothing" }),
      has_logged_today: Type.Boolean({ description: "Today is already in the learning log" }),
    }),
    async execute(_toolCallId, params) {
      const result = computeLearningStreak(
        params.current_streak,
        params.is_no_learning_day,
        params.has_logged_today,
      );
      return {
        content: [
          {
            type: "text",
            text: `new_streak: ${result.new_streak} (was ${result.current_streak}). ${result.note}`,
          },
        ],
        details: result,
      };
    },
  });

  // --- SIDECAR (G1): anything that writes a store ---------------------------

  pi.registerTool({
    name: "log_learning_session",
    label: "Log learning session",
    description:
      "Record what he studied today and persist the updated streak. This is the write — the streak you quote afterwards comes from here.",
    promptSnippet: "log_learning_session: persist a learning session and the new streak",
    promptGuidelines: [
      "Only log a session he actually reported. Never log on his behalf to make a streak look better.",
    ],
    parameters: Type.Object({
      topics: Type.Array(Type.String(), { description: "Topics studied" }),
      is_no_learning_day: Type.Optional(
        Type.Boolean({ description: "True when he explicitly studied nothing" }),
      ),
      source: Type.Optional(Type.String({ description: "Optional evidence or context" })),
      date: Type.Optional(Type.String({ description: "ISO date; omit for today" })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<LogLearningPayload>("log_learning_session", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatLearning(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "save_daily_plan",
    label: "Save daily plan",
    description:
      "Persist today's plan to memory so tomorrow can see it. Call after the plan is agreed and trimmed.",
    promptSnippet: "save_daily_plan: persist today's agreed plan to memory",
    promptGuidelines: ["Never claim a plan is saved unless save_daily_plan returned saved: true."],
    parameters: Type.Object({
      items: Type.Array(
        Type.Object({
          title: Type.String(),
          category: Type.Optional(Type.String()),
          priority: Type.Optional(Type.String({ description: "must | should | nice-to-have" })),
          duration_min: Type.Optional(Type.Number()),
          linked_goal: Type.Optional(Type.String()),
          notes: Type.Optional(Type.String()),
          scheduled_time: Type.Optional(Type.String()),
        }),
      ),
      available_minutes: Type.Number({ description: "Total minutes the plan was built against" }),
      date: Type.Optional(Type.String({ description: "ISO date; omit for today" })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<SavePlanPayload>("save_daily_plan", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatSavePlan(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "find_available_slots",
    label: "Find available slots",
    description:
      "Candidate windows that actually fit a duration without breaking an anchor. Availability is computed from the grid in code — never guessed.",
    promptSnippet: "find_available_slots: code-computed candidate windows of a given length",
    promptGuidelines: [
      "Use find_available_slots instead of eyeballing get_day_grid when a block needs to be placed.",
    ],
    parameters: Type.Object({
      duration_min: Type.Number({ description: "How long the block needs to be, in minutes" }),
      date: Type.Optional(Type.String({ description: "ISO date; omit for today" })),
      energy_level: Type.Optional(
        Type.Number({ description: "1-5; a high value nudges earlier windows to the front" }),
      ),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<SlotsPayload>("find_available_slots", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatSlots(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "place_time_block",
    label: "Place time block",
    description:
      "Validate and book one block on the calendar. Rejects non-aligned times, overlaps, and anything that would sit on a life anchor. Ask before calling.",
    promptSnippet: "place_time_block: book a validated block on the calendar (needs his consent first)",
    promptGuidelines: [
      "Propose the block and get his agreement before calling place_time_block — it writes to his real day.",
      "Only say a block is booked when place_time_block returned status: ok in this turn.",
    ],
    parameters: Type.Object({
      title: Type.String({ description: "What the block is for" }),
      start_time: Type.String({
        description:
          "Wall-clock start in his timezone, e.g. '2026-09-15T09:00:00'. 09:00 means 09:00 for him.",
      }),
      duration_min: Type.Number({ description: "Length in minutes (rounded up to 30)" }),
      category: Type.Optional(Type.String()),
      priority: Type.Optional(Type.String({ description: "must | should | nice-to-have" })),
      block_kind: Type.Optional(Type.String()),
      notes: Type.Optional(Type.String()),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<PlaceBlockPayload>("place_time_block", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatPlaceBlock(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "set_anchor",
    label: "Set anchor",
    description:
      "Reserve sleep, meals, commute, or gym as a life anchor that task placement can never overwrite. Ask first — he owns his anchors.",
    promptSnippet: "set_anchor: reserve sleep/meal/commute/gym slots as protected anchors",
    promptGuidelines: [
      "Never set or move an anchor without asking. Anchors are his, not yours to optimise.",
      "A bedtime is usually a spanning anchor: 'I sleep 10pm to 9am' is one call — start 22:00, duration 660 — not two.",
      "When the result says it crosses midnight, name both windows (tonight AND tomorrow morning). Never report only the part before midnight.",
    ],
    parameters: Type.Object({
      start_time: Type.String({
        description:
          "Wall-clock start in his timezone, e.g. '2026-09-15T22:00:00'. 22:00 means 22:00 for him — the clock reading is what counts, not the offset.",
      }),
      duration_min: Type.Number({
        description:
          "Length in minutes (rounded to 30). May span midnight: sleep 22:00 -> 09:00 is 660.",
      }),
      state: Type.String({ description: "sleep | meal | commute | gym" }),
      label: Type.Optional(Type.String({ description: "Human label, e.g. 'dinner'" })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<AnchorPayload>("set_anchor", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return { content: [{ type: "text", text: formatAnchor(result.data) }], details: result.data };
    },
  });

  pi.registerTool({
    name: "mark_topic_done",
    label: "Mark a topic done",
    description:
      "Mark a curriculum topic done, started, or skipped — and get back which downstream topics it just unlocked. The prerequisite graph is computed in code, so the next step is never guessed. Use it when he says he finished or started something.",
    promptSnippet:
      "mark_topic_done: record that he finished/started a topic and see what it unlocked",
    promptGuidelines: [
      "Call mark_topic_done when he reports progress on a curriculum topic — 'finished X', 'done with Y', 'skipped Z'. That report is the trigger; never mark anything done on his behalf speculatively.",
      "Pass his exact topic wording in `node`. Matching is exact: if it does not resolve, ask him which one he means rather than trying a near-match.",
      "Pass `roadmap` when the same topic title exists in more than one roadmap.",
      "The `unlocked` list in the result is code-computed. Use it — do not work out what became available yourself.",
      "Never claim a topic is done unless mark_topic_done returned updated: true in this turn.",
      "Optionally pass `note` with what he actually did or found, in his own words; it is appended to that topic's session log rather than stored as an interpretation.",
    ],
    parameters: Type.Object({
      node: Type.String({
        description: "The topic's exact title or node id, as it appears in the curriculum.",
      }),
      roadmap: Type.Optional(
        Type.String({ description: "Which roadmap (id or exact title); needed only to disambiguate." }),
      ),
      status: Type.Optional(
        Type.String({ description: "'done' | 'in_progress' | 'skipped' | 'not_started' (default 'done')" }),
      ),
      note: Type.Optional(
        Type.String({ description: "What he actually did, in his words (optional)." }),
      ),
      session_id: Type.Optional(Type.String({ description: "Session id, for tracing." })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<MarkTopicPayload>("mark_topic_done", params);
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return {
        content: [{ type: "text", text: formatMarkTopic(result.data) }],
        details: { ...result.data, ok: Boolean(result.data?.updated) },
      };
    },
  });

  // A repo analysis is a real model pass over a codebase, so it gets minutes
  // rather than the 15s read default (same reasoning as run_specialist).
  const repoTimeoutMs = (() => {
    const raw = Number(process.env.MENTOR_REPO_ANALYSIS_TIMEOUT_MS);
    return Number.isFinite(raw) && raw > 0 ? raw : 240_000;
  })();

  pi.registerTool({
    name: "learn_repo",
    label: "Learn a repository",
    description:
      "Derive a prerequisite curriculum from a real codebase: which concepts the code embodies, exactly where each one lives (verified against the filesystem), and how hard it is. Use it when he asks to understand, learn, or get up to speed on a repository.",
    promptSnippet:
      "learn_repo: turn a repository into a validated concept curriculum (verified file/symbol citations)",
    promptGuidelines: [
      "Call learn_repo when the goal is to UNDERSTAND a codebase he has pointed you at — not for a single question about a file, which you can answer by reading it yourself.",
      "Pass the repo path in `repo_path`. Never invent or guess a path; if you do not know it, ask.",
      "Ask which stopping rule he wants when it is unclear: `comprehension` (understand it, the default) or `authorship` (be able to rebuild it). They produce very different curricula.",
      "Run it with persist false first for anything exploratory, read the inventory back, and let him react. Only persist when he wants to study it — it writes a real roadmap.",
      "Relay the corrections honestly: if `dropped_anchors` names citations that did not resolve, say so rather than presenting the inventory as fully verified. Same for concepts cut by the node budget.",
      "Never say a curriculum or roadmap exists unless learn_repo returned ok: true in this turn.",
    ],
    parameters: Type.Object({
      repo_path: Type.String({ description: "Path to the repository (must be inside the workspace sandbox)." }),
      title: Type.Optional(Type.String({ description: "Optional curriculum title; derived from the repo otherwise." })),
      stopping_rule: Type.Optional(
        Type.String({ description: "'comprehension' (default, to read the repo) or 'authorship' (to rebuild it)" }),
      ),
      max_nodes: Type.Optional(
        Type.Number({ description: "Hard cap on concepts, 2-30 (default 10). The stopping rule's teeth." }),
      ),
      persist: Type.Optional(
        Type.Boolean({ description: "true = also save it as a roadmap he can study and mark topics done against." }),
      ),
      target_days: Type.Optional(Type.Number({ description: "Optional: spread it over N days." })),
      hours_per_day: Type.Optional(Type.Number({ description: "Optional: study hours per day." })),
      session_id: Type.Optional(Type.String({ description: "Session id, for tracing." })),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<LearnRepoPayload>("learn_repo", params, { timeoutMs: repoTimeoutMs });
      if (!result.ok) {
        return { content: [{ type: "text", text: result.error }], details: { ok: false, error: result.error } };
      }
      return {
        content: [{ type: "text", text: formatLearnRepo(result.data) }],
        details: { ...result.data, ok: Boolean(result.data?.ok) },
      };
    },
  });
}