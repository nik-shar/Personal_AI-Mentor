/**
 * mentor/src/memory/transcript.ts
 *
 * Reading Tier 0 — the transcript — and remembering where the curator got to.
 *
 * PI already writes every session to `~/.pi/agent/sessions/<encoded-cwd>/*.jsonl`,
 * append-only, on disk. Nothing needs to *store* the record; it is already there.
 * What this module adds is the **path back**: a cursor, so the curator can read
 * forward from where it last stopped.
 *
 * Why the cursor matters more than it looks
 * ----------------------------------------
 * Without it, the design depends on a hook firing at exactly the right instant,
 * and a missed hook means lost memory. With it:
 *
 *   - nothing can be skipped — a missed run is caught by the next one
 *   - re-running is idempotent — curating the same range twice changes nothing
 *   - timing stops being critical — the curator may run late, twice, or after a
 *     crash, because the source of truth is a file, not an in-flight payload
 *
 * So the hooks in MEMORY.md §8 are conveniences, not requirements.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { memoryPaths } from "./store.ts";

// ---------------------------------------------------------------------------
// Where PI keeps sessions
// ---------------------------------------------------------------------------

/** Mirrors `getDefaultAgentDir()` in the PI harness. */
export function agentDir(): string {
  return process.env.PI_AGENT_DIR?.trim() || join(homedir(), ".pi", "agent");
}

/**
 * The session directory for a working directory.
 *
 * PI's encoding, mirrored exactly: strip the leading slash, then replace every
 * `/` or `:` with `-`, wrapped in `--`. For `/home/nik/Desktop/AI` that is
 * `--home-nik-Desktop-AI--`. Getting this wrong means silently reading zero
 * sessions, so it is asserted in the tests.
 */
export function sessionDirFor(cwd: string, agent: string = agentDir()): string {
  const resolved = resolve(cwd);
  const safe = `--${resolved.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`;
  return join(agent, "sessions", safe);
}

/** Session files, oldest first (PI names them with a leading ISO timestamp). */
export function listSessionFiles(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith(".jsonl"))
    .sort()
    .map((f) => join(dir, f));
}

// ---------------------------------------------------------------------------
// The cursor
// ---------------------------------------------------------------------------

export interface Cursor {
  /** Absolute path of the session file last read. */
  file: string;
  /** How many lines of it have been curated. */
  line: number;
  curated_at: string | null;
}

export const EMPTY_CURSOR: Cursor = { file: "", line: 0, curated_at: null };

export function readCursor(repoRoot: string): Cursor {
  const path = memoryPaths(repoRoot).cursor;
  if (!existsSync(path)) return { ...EMPTY_CURSOR };
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return {
      file: typeof parsed.file === "string" ? parsed.file : "",
      line: Number.isFinite(parsed.line) ? Number(parsed.line) : 0,
      curated_at: typeof parsed.curated_at === "string" ? parsed.curated_at : null,
    };
  } catch {
    return { ...EMPTY_CURSOR };
  }
}

export function writeCursor(repoRoot: string, cursor: Cursor): void {
  const path = memoryPaths(repoRoot).cursor;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(cursor, null, 2), "utf8");
}

// ---------------------------------------------------------------------------
// Reading forward
// ---------------------------------------------------------------------------

/** One parsed session line that carried a message. */
export interface TranscriptLine {
  index: number;
  timestamp: string;
  role: string;
  text: string;
}

/** Pull readable text out of an AgentMessage, tolerating both shapes. */
function messageText(message: unknown): { role: string; text: string } | null {
  if (!message || typeof message !== "object") return null;
  const m = message as { role?: unknown; content?: unknown };
  const role = typeof m.role === "string" ? m.role : "unknown";

  if (typeof m.content === "string") return { role, text: m.content };

  if (Array.isArray(m.content)) {
    const parts: string[] = [];
    for (const part of m.content) {
      if (typeof part === "string") {
        parts.push(part);
      } else if (part && typeof part === "object") {
        const text = (part as { text?: unknown }).text;
        if (typeof text === "string") parts.push(text);
      }
    }
    const joined = parts.join("\n").trim();
    return joined ? { role, text: joined } : null;
  }
  return null;
}

/**
 * How many lines of a file have actually been consumed.
 *
 * `split("\n")` on a file ending in a newline yields a trailing `""`. Counting
 * that phantom element as consumed is a silent data-loss bug: the next append
 * lands *at* the cursor index instead of after it, and its first message is
 * never read. (Caught by `mentor/test/memory.test.ts`.)
 */
function consumedLines(all: string[]): number {
  return all.length > 0 && all[all.length - 1] === "" ? all.length - 1 : all.length;
}

/**
 * Everything the curator has not seen yet.
 *
 * Walks from the cursor to the end of the newest session file, skipping entries
 * that carry no conversation (thinking-level changes, model switches, the session
 * header). Returns the lines *and* the cursor to store afterwards — so a caller
 * cannot advance the cursor without the content in hand.
 */
export function pendingTranscript(
  repoRoot: string,
  cwd: string = repoRoot,
): { lines: TranscriptLine[]; next: Cursor; filesRead: number } {
  const cursor = readCursor(repoRoot);
  const files = listSessionFiles(sessionDirFor(cwd));

  if (files.length === 0) {
    return { lines: [], next: { ...cursor, curated_at: new Date().toISOString() }, filesRead: 0 };
  }

  // Start where the cursor left off; if that file is gone (rotated, deleted),
  // start from the oldest file we have rather than silently skipping the gap.
  let startAt = cursor.file ? files.indexOf(cursor.file) : -1;
  if (startAt === -1) startAt = 0;

  const lines: TranscriptLine[] = [];
  let lastFile = cursor.file;
  let lastLine = cursor.line;
  let filesRead = 0;

  for (let f = startAt; f < files.length; f += 1) {
    const file = files[f];
    const from = file === cursor.file ? cursor.line : 0;

    let raw: string;
    try {
      raw = readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const all = raw.split("\n");

    for (let i = from; i < all.length; i += 1) {
      const trimmed = all[i].trim();
      if (!trimmed) continue;
      let entry: Record<string, unknown>;
      try {
        entry = JSON.parse(trimmed) as Record<string, unknown>;
      } catch {
        continue; // a half-written final line is expected, not an error
      }
      if (entry.type !== "message") continue;

      const extracted = messageText(entry.message);
      if (!extracted || !extracted.text.trim()) continue;
      lines.push({
        index: i,
        timestamp: typeof entry.timestamp === "string" ? entry.timestamp : "",
        role: extracted.role,
        text: extracted.text,
      });
    }

    lastFile = file;
    lastLine = consumedLines(all);
    filesRead += 1;
  }

  return {
    lines,
    next: { file: lastFile, line: lastLine, curated_at: new Date().toISOString() },
    filesRead,
  };
}

/** Render lines for a prompt, with a char budget so the curator is not flooded. */
export function renderTranscript(lines: TranscriptLine[], maxChars = 40000): string {
  const out: string[] = [];
  let used = 0;
  for (const line of lines) {
    const row = `[${line.timestamp}] ${line.role}: ${line.text}`;
    if (used + row.length > maxChars) {
      out.push(`… ${lines.length - out.length} further lines omitted (budget ${maxChars} chars)`);
      break;
    }
    used += row.length;
    out.push(row);
  }
  return out.join("\n");
}

/** True when a session file is still being written to (its mtime is fresh). */
export function isSessionActive(file: string, withinMs = 120_000): boolean {
  try {
    return Date.now() - statSync(file).mtimeMs < withinMs;
  } catch {
    return false;
  }
}