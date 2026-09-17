/**
 * mentor/src/memory/store.ts
 *
 * The memory files — the ONLY place the mentor's memory is written.
 *
 * Design rules (from MEMORY.md):
 *   - Append-only. A correction supersedes; nothing is ever deleted or rewritten
 *     in place. This is what makes concurrent writers safe: appending a line is
 *     atomic, rewriting a file is not.
 *   - Provenance is mandatory. A memory without a `source` and a supporting
 *     `quote` is rejected — the curator decides what is worth keeping, never what
 *     is true, and the quote is what makes that checkable.
 *   - The confidence ceilings are CODE, not prompt. A model told "cap inferred
 *     memories at 0.60" will eventually write 0.9. Arithmetic belongs in code
 *     (GOAL.md §5.2), so it lives here and refuses the write.
 *   - One writer per file (MEMORY.md §4). `memories.jsonl` and `profile.yaml`
 *     are written by the memory agent; `daylog.jsonl` and `requests.jsonl` by the
 *     mentor. They never write each other's files.
 *
 * Pure enough to unit-test: every function takes the repo root, touches no
 * ambient state, and imports nothing from PI.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { parse as parseYaml, stringify as toYaml } from "yaml";

// ---------------------------------------------------------------------------
// The confidence lifecycle — code-owned
// ---------------------------------------------------------------------------

export type MemorySource = "user_stated" | "seeded" | "data_derived" | "mentor_inferred";

interface SourceRule {
  /** Where a new memory of this kind starts. */
  start: number;
  /** The highest confidence it may ever reach without him confirming it. */
  ceiling: number;
  /** Whether the source itself counts as his word. */
  userConfirmed: boolean;
}

export const SOURCE_RULES: Record<MemorySource, SourceRule> = {
  // He said it. Trusted.
  user_stated: { start: 0.95, ceiling: 1.0, userConfirmed: true },
  // He authored it in the guidelines / a seed file. Trusted.
  seeded: { start: 0.95, ceiling: 1.0, userConfirmed: true },
  // Computed from his actual behaviour (a pattern in the day log, the grid).
  data_derived: { start: 0.7, ceiling: 0.95, userConfirmed: false },
  // A guess. The mentor must ASK about these, never assert them.
  mentor_inferred: { start: 0.4, ceiling: 0.6, userConfirmed: false },
};

export function isMemorySource(value: unknown): value is MemorySource {
  return typeof value === "string" && value in SOURCE_RULES;
}

/**
 * Clamp a confidence to what its source is allowed to claim.
 *
 * Unknown sources are treated as the weakest (`mentor_inferred`) rather than
 * trusted — an unrecognised source is a bug, and the safe reading of a bug is
 * "we don't know this."
 */
export function applyCeiling(source: unknown, requested?: number): { confidence: number; clamped: boolean } {
  const rule = SOURCE_RULES[isMemorySource(source) ? source : "mentor_inferred"];
  const raw = typeof requested === "number" && Number.isFinite(requested) ? requested : rule.start;
  const confidence = Math.max(0, Math.min(raw, rule.ceiling));
  return { confidence, clamped: confidence !== raw };
}

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

export interface MemoryPaths {
  dir: string;
  profile: string;
  memories: string;
  daylog: string;
  requests: string;
  attention: string;
  cursor: string;
  scheduleDir: string;
}

/** Resolve every memory path from the repo root. One place, so they cannot drift. */
export function memoryPaths(repoRoot: string): MemoryPaths {
  const dir = join(repoRoot, "data");
  return {
    dir,
    profile: join(dir, "profile.yaml"),
    memories: join(dir, "memories.jsonl"),
    daylog: join(dir, "daylog.jsonl"),
    requests: join(dir, "requests.jsonl"),
    attention: join(dir, "attention.jsonl"),
    cursor: join(dir, ".curator_cursor.json"),
    scheduleDir: join(dir, "schedule"),
  };
}

function ensureDir(path: string): void {
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

/** Append one JSON object as a line. The only write primitive for jsonl. */
export function appendJsonl(path: string, record: unknown): void {
  ensureDir(path);
  appendFileSync(path, JSON.stringify(record) + "\n", "utf8");
}

/** Read a jsonl file tolerantly: a malformed line is skipped, not fatal. */
export function readJsonl(path: string): Record<string, unknown>[] {
  if (!existsSync(path)) return [];
  const out: Record<string, unknown>[] = [];
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        out.push(parsed as Record<string, unknown>);
      }
    } catch {
      // A corrupt line must not make the whole store unreadable.
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Memory — the durable facts about him
// ---------------------------------------------------------------------------

/** What a memory looks like once written. */
export interface MemoryRecord {
  id: string;
  content: string;
  memory_type: string;
  source: MemorySource;
  confidence: number;
  confidence_ceiling: number;
  user_confirmed: boolean;
  /** The sentence this came from. Makes a wrong memory traceable. */
  quote: string;
  /** Where the quote came from: a session file + timestamp, or "conversation". */
  origin: string;
  tags: string[];
  active: boolean;
  superseded_by: string | null;
  created_at: string;
}

export interface MemoryInput {
  content?: unknown;
  memory_type?: unknown;
  source?: unknown;
  confidence?: unknown;
  quote?: unknown;
  origin?: unknown;
  tags?: unknown;
}

export type WriteResult =
  | { ok: true; id: string; confidence: number; clampedFrom?: number }
  | { ok: false; error: string };

/** Short, human-readable id. Not a uuid — these are read by a person. */
function newId(prefix = "mem"): string {
  const stamp = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 6);
  return `${prefix}_${stamp}${rand}`;
}

/**
 * Append one memory, enforcing what the curator is not allowed to decide.
 *
 * Rejected outright: no content, an unknown source, or no quote. The quote rule
 * is the important one — a memory the curator cannot trace to a sentence is a
 * memory it invented, and those are worse than missing ones because they persist.
 */
export function appendMemory(repoRoot: string, input: MemoryInput): WriteResult {
  const content = typeof input.content === "string" ? input.content.trim() : "";
  if (!content) return { ok: false, error: "no content" };

  if (!isMemorySource(input.source)) {
    return {
      ok: false,
      error:
        `unknown or missing source '${String(input.source)}'. ` +
        `Must be one of: ${Object.keys(SOURCE_RULES).join(", ")}`,
    };
  }

  const quote = typeof input.quote === "string" ? input.quote.trim() : "";
  if (!quote) {
    return {
      ok: false,
      error:
        "no quote — a memory must cite the sentence it came from. " +
        "If you cannot quote it, it is an inference you cannot support; do not write it.",
    };
  }

  const { confidence, clamped } = applyCeiling(input.source, Number(input.confidence));
  const rule = SOURCE_RULES[input.source];
  const record: MemoryRecord = {
    id: newId(),
    content,
    memory_type: typeof input.memory_type === "string" && input.memory_type.trim()
      ? input.memory_type.trim()
      : "fact",
    source: input.source,
    confidence,
    confidence_ceiling: rule.ceiling,
    user_confirmed: rule.userConfirmed,
    quote: quote.slice(0, 500),
    origin: typeof input.origin === "string" ? input.origin : "conversation",
    tags: Array.isArray(input.tags) ? input.tags.filter((t) => typeof t === "string") : [],
    active: true,
    superseded_by: null,
    created_at: new Date().toISOString(),
  };

  appendJsonl(memoryPaths(repoRoot).memories, record);
  return clamped
    ? { ok: true, id: record.id, confidence, clampedFrom: Number(input.confidence) }
    : { ok: true, id: record.id, confidence };
}

/**
 * Active memories, newest last.
 *
 * Superseded ones are excluded by reading the *pointer* rows — the supersede
 * record carries `supersedes: <old id>`, so the old memory is filtered out
 * without ever being edited. That is the append-only rule working: the
 * correction is a new line, not a rewrite.
 */
export function readMemories(repoRoot: string, opts: { includeInactive?: boolean } = {}): MemoryRecord[] {
  const all = readJsonl(memoryPaths(repoRoot).memories);

  const pointerIds = new Set<string>();
  for (const row of all) {
    if (row.kind === "supersede" && typeof row.supersedes === "string") {
      pointerIds.add(row.supersedes);
    }
  }

  const memories = all.filter((row) => row.kind !== "supersede" && typeof row.id === "string");
  const live = memories.filter((row) => {
    // `includeInactive` means *everything*, including superseded rows — which is
    // what makes the store auditable. So the superseded check belongs inside the
    // live branch, not in front of it.
    if (opts.includeInactive) return true;
    if (pointerIds.has(row.id as string)) return false;
    return row.active !== false && !row.superseded_by;
  });
  return live as unknown as MemoryRecord[];
}

/**
 * Supersede a memory rather than editing it.
 *
 * Append-only means the old row is never touched: a new memory is written, and
 * then a pointer row records that the old one is replaced. Rewriting the file is
 * exactly the two-writer hazard the design exists to avoid.
 */
export function supersedeMemory(repoRoot: string, oldId: string, replacement: MemoryInput): WriteResult {
  const written = appendMemory(repoRoot, replacement);
  if (!written.ok) return written;
  appendJsonl(memoryPaths(repoRoot).memories, {
    id: newId("sup"),
    kind: "supersede",
    supersedes: oldId,
    superseded_by: written.id,
    superseded_at: new Date().toISOString(),
  });
  return written;
}

// ---------------------------------------------------------------------------
// Profile — the structured facts
// ---------------------------------------------------------------------------

export interface ProfileFile {
  facts: Record<string, Record<string, { value: unknown; source?: string; updated_at?: string }>>;
}

/** Read profile.yaml. A missing or corrupt file degrades to empty, never throws. */
export function readProfile(repoRoot: string): ProfileFile {
  const path = memoryPaths(repoRoot).profile;
  if (!existsSync(path)) return { facts: {} };
  try {
    const parsed = parseYaml(readFileSync(path, "utf8"));
    if (parsed && typeof parsed === "object" && parsed.facts && typeof parsed.facts === "object") {
      return { facts: parsed.facts as ProfileFile["facts"] };
    }
  } catch {
    // fall through to empty
  }
  return { facts: {} };
}

/** Flat view: `category.key` -> value. What a prompt actually wants. */
export function profileFactsFlat(repoRoot: string): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [category, keys] of Object.entries(readProfile(repoRoot).facts)) {
    for (const [key, entry] of Object.entries(keys ?? {})) {
      out[`${category}.${key}`] = entry?.value;
    }
  }
  return out;
}

/**
 * Write profile.yaml.
 *
 * This is the ONE file the store rewrites rather than appends to. It is safe
 * because it has exactly one writer (the memory agent) — the discipline holds
 * at the file level, not only the record level.
 */
export function writeProfileFact(
  repoRoot: string,
  category: string,
  key: string,
  value: unknown,
  source: MemorySource | "curator" = "curator",
): void {
  const profile = readProfile(repoRoot);
  profile.facts[category] = profile.facts[category] ?? {};
  profile.facts[category][key] = { value, source, updated_at: new Date().toISOString() };

  const path = memoryPaths(repoRoot).profile;
  ensureDir(path);
  writeFileSync(
    path,
    toYaml({
      _note: "Structured facts about him. Written by the memory agent; read by the mentor.",
      facts: profile.facts,
    }),
    "utf8",
  );
}

// ---------------------------------------------------------------------------
// Attention — signals queued for the mentor, NOT instructions (MEMORY.md §4)
// ---------------------------------------------------------------------------

export type AttentionKind = "unresolved_thread" | "contradiction" | "system_state";

/**
 * Queue a signal the mentor should consider.
 *
 * Deliberately NOT an instruction. The mentor reads these and decides whether to
 * act, and when — which is what keeps it the one directing the conversation.
 * A `suggestion` field is allowed; a `command` is not.
 */
export function appendAttention(
  repoRoot: string,
  item: { kind: AttentionKind; what: string; evidence?: string; suggestion?: string },
): void {
  appendJsonl(memoryPaths(repoRoot).attention, {
    id: newId("att"),
    kind: item.kind,
    what: item.what,
    evidence: item.evidence ?? "",
    suggestion: item.suggestion ?? "",
    raised_at: new Date().toISOString(),
    resolved_at: null,
  });
}

// ---------------------------------------------------------------------------
// Requests — the seam the MENTOR writes, and the curator drains
// ---------------------------------------------------------------------------

/**
 * Read what the mentor asked to be remembered.
 *
 * The mentor cannot write `memories.jsonl` (one writer per file), so an explicit
 * "remember this" becomes a request instead: the mentor appends here, the
 * curator drains it into real memory with proper provenance. Two writers, two
 * files, no shared mutable state.
 */
export function readRequests(repoRoot: string): Record<string, unknown>[] {
  return readJsonl(memoryPaths(repoRoot).requests);
}

/** The curator has drained the queue — empty it. */
export function clearRequests(repoRoot: string): void {
  const path = memoryPaths(repoRoot).requests;
  if (existsSync(path)) writeFileSync(path, "", "utf8");
}

/** Append a request (used by the mentor's `remember` tool). */
export function appendRequest(repoRoot: string, what: string, detail?: string): void {
  appendJsonl(memoryPaths(repoRoot).requests, {
    id: newId("req"),
    what,
    detail: detail ?? "",
    requested_at: new Date().toISOString(),
  });
}
