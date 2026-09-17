/**
 * Identity assembly — the mentor's persistent core.
 *
 * Gate: G4 (identity / prompt policy) → TypeScript.
 *
 * The mentor's identity is CONTENT, not code, so it lives in two
 * runtime-neutral markdown files that BOTH runtimes read fresh:
 *
 *   mentor_agent_guidelines.md   §1 anchor facts, §2 standing orders,
 *                                §3 router duties, §5 memory rules,
 *                                §6 guardrails, §7 success criteria
 *                                (Python reader: cognition/guidelines.py)
 *   mentor_persona.md            voice rules, hard rules, few-shot examples
 *                                (Python reader: cognition/persona.py)
 *
 * Nothing here is a copy. That is the whole point: before this module, the
 * voice was written twice — as f-strings in `cognition/persona.py` and again,
 * hand-condensed, inline in `extensions/mentor-identity.ts` — and the two
 * diverged, while the constitution never reached the PI core at all (PI loads
 * AGENTS.md / CLAUDE.md as *project* context, and neither exists here).
 * Loading the same two files on both sides makes drift structurally
 * impossible: the file is the single source of truth, and "loading fresh"
 * means editing it takes effect on the next turn with no restart and no
 * regenerated artifact to keep in sync.
 *
 * Fail-open, like every other mentor loader: an unreadable file degrades to an
 * empty block with a loud warning, never a crashed turn.
 */

import { readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// ---------------------------------------------------------------------------
// Paths — resolved from this module's own location, so cwd never matters
// ---------------------------------------------------------------------------

/** `mentor/src/identity.ts` → repo root. */
export const REPO_ROOT = resolve(dirname(modulePath()), "..", "..");

export const GUIDELINES_FILE = "mentor_agent_guidelines.md";
export const PERSONA_FILE = "mentor_persona.md";

/** Mirrors `orchestrator.config.USER_NAME` / `MENTOR_USER_NAME`. */
export const DEFAULT_USER_NAME = "Nik";

export function userName(): string {
  return process.env.MENTOR_USER_NAME?.trim() || DEFAULT_USER_NAME;
}

/** Mirrors `orchestrator.config.MENTOR_GUIDELINES_PATH`. */
export function guidelinesPath(): string {
  return process.env.MENTOR_GUIDELINES_PATH?.trim() || resolve(REPO_ROOT, GUIDELINES_FILE);
}

/** Mirrors `orchestrator.config.MENTOR_PERSONA_PATH`. */
export function personaPath(): string {
  return process.env.MENTOR_PERSONA_PATH?.trim() || resolve(REPO_ROOT, PERSONA_FILE);
}

/**
 * This module's own path on disk.
 *
 * `import.meta.url` is a URL, not a path — running `path.dirname` over it
 * silently produces `file:/home/...`-shaped garbage, so it must go through
 * `fileURLToPath` first.
 */
function modulePath(): string {
  try {
    return fileURLToPath(import.meta.url);
  } catch {
    // A CJS transform (or a bundler that drops import.meta) leaves no module
    // URL; fall back to cwd, which is the repo root under run_pi_mentor.sh.
    return resolve(process.cwd(), "mentor", "src", "identity.ts");
  }
}

// ---------------------------------------------------------------------------
// Fail-open, mtime-cached file reads
// ---------------------------------------------------------------------------

interface CacheEntry {
  mtimeMs: number;
  text: string;
}

const fileCache = new Map<string, CacheEntry>();
const warnedPaths = new Set<string>();

/** Read a content file, re-reading only when it changes on disk. "" if absent. */
export function readContentFile(path: string): string {
  let mtimeMs: number;
  try {
    mtimeMs = statSync(path).mtimeMs;
  } catch {
    if (!warnedPaths.has(path)) {
      warnedPaths.add(path);
      console.warn(`[mentor] ${path} not found — that part of the mentor identity is missing.`);
    }
    return "";
  }

  const cached = fileCache.get(path);
  if (cached && cached.mtimeMs === mtimeMs) {
    return cached.text;
  }
  try {
    const text = readFileSync(path, "utf8");
    fileCache.set(path, { mtimeMs, text });
    warnedPaths.delete(path);
    return text;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[mentor] failed to read ${path}: ${message}`);
    return cached?.text ?? "";
  }
}

/** Drop cached reads so the next access re-stats the files (used by tests). */
export function resetCaches(): void {
  fileCache.clear();
  warnedPaths.clear();
}

// ---------------------------------------------------------------------------
// Section parsing — the `## N. Title` convention shared by both content files
// ---------------------------------------------------------------------------

export interface Section {
  number: number;
  title: string;
  body: string;
}

const SECTION_HEADER = /^## (\d+)\. ([^\n]*)$/gm;

/**
 * Split a content document into its numbered sections.
 *
 * Bodies are trimmed exactly as the Python readers' `re.split(...).strip()`
 * does, so both runtimes inject byte-identical text.
 */
export function parseSections(text: string): Section[] {
  if (!text) return [];
  const headers = [...text.matchAll(SECTION_HEADER)];
  return headers.map((header, index) => {
    const start = (header.index ?? 0) + header[0].length;
    const end = index + 1 < headers.length ? (headers[index + 1].index ?? text.length) : text.length;
    return {
      number: Number(header[1]),
      title: header[2].trim(),
      body: text.slice(start, end).trim(),
    };
  });
}

/** Substitute the configured user name for the `{name}` placeholder. */
export function renderName(text: string, name: string = userName()): string {
  // split/join rather than replace() so a "$" in the name can never be read
  // as a replacement pattern.
  return text.split("{name}").join(name);
}

function sectionBody(path: string, number: number): string {
  return parseSections(readContentFile(path)).find((section) => section.number === number)?.body ?? "";
}

// ---------------------------------------------------------------------------
// Constitution readers (mirror orchestrator/cognition/guidelines.py)
// ---------------------------------------------------------------------------

/** §1 — Who This Agent Serves. The user-authored anchor facts. */
export function coreIdentity(): string {
  return sectionBody(guidelinesPath(), 1);
}

/** §2 — Core Principles (standing orders). */
export function corePrinciples(): string {
  return sectionBody(guidelinesPath(), 2);
}

/** §6 — Guardrails. */
export function guardrails(): string {
  return sectionBody(guidelinesPath(), 6);
}

/**
 * §4 subsection for one sub-agent — e.g. `agentGuidelines("job_hunter")`
 * returns the bullets under "### `job_hunter`". Empty string if absent.
 *
 * Used by the Phase-4 specialist tools; the overlay deliberately does not
 * inject §4, because naming specialists the core cannot yet call invites the
 * model to claim a delegation that does not exist.
 */
export function agentGuidelines(agentName: string): string {
  const section = sectionBody(guidelinesPath(), 4);
  if (!section || !agentName) return "";
  const subsections = section.split(/^### `([^`]+)`\s*$/m);
  for (let i = 1; i < subsections.length - 1; i += 2) {
    if (subsections[i].trim() === agentName) {
      return subsections[i + 1].trim();
    }
  }
  return "";
}

/**
 * Sections injected into the PI core's system prompt.
 *
 * §4 is excluded on purpose (see `agentGuidelines`). Everything else applies:
 * this core replaces the Python router, so §3's "assemble context before
 * deciding; prefer one cheap clarifying question over an expensive wrong
 * guess" is live policy here, not history.
 */
export const CONSTITUTION_SECTIONS: readonly number[] = [1, 2, 3, 5, 6, 7];

/** The standing-orders block. Empty string when the file is unavailable. */
export function constitutionBlock(numbers: readonly number[] = CONSTITUTION_SECTIONS): string {
  const sections = parseSections(readContentFile(guidelinesPath()));
  const wanted = numbers
    .map((number) => sections.find((section) => section.number === number))
    .filter((section): section is Section => Boolean(section?.body));

  if (wanted.length === 0) return "";

  const parts = ["### 📜 Operating Constitution (standing orders — never violate)"];
  for (const section of wanted) {
    parts.push(`\n**§${section.number} — ${section.title}**\n${section.body}`);
  }
  return parts.join("\n");
}

// ---------------------------------------------------------------------------
// Persona readers (mirror orchestrator/cognition/persona.py)
// ---------------------------------------------------------------------------

/** §1 — Voice rules. */
export function voiceRules(): string {
  return renderName(sectionBody(personaPath(), 1));
}

/** §2 — Hard rules. */
export function hardRules(): string {
  return renderName(sectionBody(personaPath(), 2));
}

/** §3 — Few-shot example exchanges (the voice, demonstrated). */
export function fewShotExamples(): string {
  return renderName(sectionBody(personaPath(), 3));
}

/** Voice + hard rules (+ examples). Mirrors `build_persona_block()`. */
export function personaBlock(includeExamples = true): string {
  const examples = includeExamples ? fewShotExamples() : "";
  const parts = [voiceRules(), "", hardRules()];
  // Only wrap when there is something to wrap — an unconditional wrapper emits
  // an empty `<examples></examples>` pair when the content file is missing.
  // Identical output when the file is present, which is the normal path.
  if (examples) {
    parts.push("", "<examples>", examples, "</examples>");
  }
  return parts.join("\n");
}

// ---------------------------------------------------------------------------
// Time grounding
// ---------------------------------------------------------------------------

/**
 * The grid's timezone — the single basis every clock in this system uses.
 *
 * Mirrors `orchestrator.config.local_tz()`: an explicit `MENTOR_TIMEZONE` wins,
 * otherwise the machine's own zone. The identity block must name the same zone
 * the sidecar stamps on every slot clock, or "18:30" here and "18:30" on a grid
 * read would mean different instants.
 */
export function mentorTimeZone(): string {
  return process.env.MENTOR_TIMEZONE?.trim() || Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/**
 * The current date AND clock, injected so the model guesses neither.
 *
 * The original defect was empirical: asked "am I free at 4pm today?", Qwen3-235B
 * called `get_day_grid` with `{"date":"2024-05-22"}` — a training-era date it
 * invented rather than omitting the optional argument — and the mentor reported
 * the wrong day. Then it turned out the block also carried no clock at all, and
 * a UTC date, so it could not answer "what time is it?" or "how much of today is
 * left?" without inventing them.
 *
 * `hourCycle: "h23"` matters: with `hour12: false` some ICU builds render
 * midnight as "24:00", which is a valid bedtime and a nonsense *current* time.
 */
export function currentTimeBlock(now: Date = new Date()): string {
  const timeZone = mentorTimeZone();
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "long",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const part = (type: string) => parts.find((entry) => entry.type === type)?.value ?? "";
  const isoDate = `${part("year")}-${part("month")}-${part("day")}`;
  const clock = `${part("hour")}:${part("minute")}`;

  return `CURRENT TIME (authoritative — never guess or invent either):
- Now: ${part("weekday")}, ${isoDate}, ${clock} (${timeZone}).
- Every clock time you state, and every one you pass to a tool, is in this
  timezone. Never convert to UTC first.
- When a tool takes an optional date and you mean today, OMIT the argument
  entirely. Never pass a remembered, assumed, or pattern-matched date.
- For "how much time is left today", call get_day_grid in this turn and read the
  windows it computes. Never estimate the remaining time in your head.`;
}

// ---------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------

export const IDENTITY_OPEN = "<mentor_identity>";
export const IDENTITY_CLOSE = "</mentor_identity>";

/**
 * The PI-specific framing clause.
 *
 * Deliberately NOT in either content file: PI ships as a coding agent, so this
 * says "you are not that" — a concern that only exists on this side of the
 * boundary. It restates no rule from the constitution or the persona.
 */
function framingBlock(name: string): string {
  return `You are ${name}'s personal mentor. PI ships as a coding agent; you are
not a coding assistant wearing a mentor costume — you ARE the mentor. Never
expose internal labels ("the agent returned", "the system generated", tool or
skill names).`;
}

/**
 * The full identity overlay, assembled from whatever is readable.
 *
 * Missing files shrink the block; they never fail it.
 */
export function buildIdentityBlock(now: Date = new Date()): string {
  const name = userName();
  const parts = [framingBlock(name)];

  const identity = coreIdentity();
  if (identity) {
    parts.push(`### 🧭 Core Identity (anchor facts — user-authored, always trust)\n${identity}`);
  }

  const constitution = constitutionBlock();
  if (constitution) {
    parts.push(constitution);
  }

  // Guard on the rules themselves: this skips the "Voice" heading entirely when
  // the content file is missing, rather than emitting a heading with nothing
  // under it.
  if (voiceRules() || hardRules()) {
    parts.push(`### 🗣️ Voice (how you speak — from mentor_persona.md)\n${personaBlock(true)}`);
  }

  parts.push(currentTimeBlock(now));

  return `${IDENTITY_OPEN}\n${parts.join("\n\n")}\n${IDENTITY_CLOSE}`;
}



