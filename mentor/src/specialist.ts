/**
 * Specialist routing — the pure half.
 *
 * The gate split matters here: deciding *when* a specialist is needed is policy
 * (G4 → TypeScript), while running one is G1/G2 — goal_decomposer writes the
 * Obsidian vault, job_hunter needs tectonic/pypdf, linkedin_writer reads the
 * Chroma voice store — so the work stays Python behind the sidecar. This file is
 * the pure part of that policy: how a sidecar response becomes text the model can
 * act on, and how long a run is allowed to take.
 *
 * Why it is not in the extension: `extensions/*.ts` import `typebox`, which only
 * resolves inside PI's loader (it is a peerDependency provided by the host). Logic
 * living there cannot be exercised by `node --test` at all — the same reason
 * `planning.ts` and `disclosure.ts` exist. Nothing here throws.
 */

/** What `POST /tools/run_specialist` returns (mirrors `RunSpecialistResponse`). */
export interface SpecialistPayload {
  status?: string;
  agent?: string;
  task_type?: string;
  output?: string;
  error?: string | null;
}

/**
 * Specialist runs are the one legitimately slow tool: goal_decomposer generates
 * complete tutorial notes in parallel. The sidecar's 15s default exists so a slow
 * *read* degrades to a readable error instead of stalling a turn — applying it
 * here would cut off exactly the turns that matter most. Kept under
 * `PI_TURN_TIMEOUT_SECONDS` (420) so the bridge, not this client, owns the outer
 * bound.
 */
export const DEFAULT_SPECIALIST_TIMEOUT_MS = 300_000;

/** Resolve the run timeout, honouring the env override. Never returns <= 0. */
export function specialistTimeoutMs(env: Record<string, string | undefined>): number {
  const raw = Number(env.MENTOR_SPECIALIST_TIMEOUT_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_SPECIALIST_TIMEOUT_MS;
}

/**
 * Success means the sidecar said so — never inferred from a non-empty reply.
 * `partial` counts: the agent did the work but flagged something, and the model is
 * told to relay the flag rather than smooth it over.
 *
 * Note there is deliberately no client-side agent whitelist: the sidecar owns that
 * list and returns an actionable error naming the valid values, so a second copy
 * here could only drift from it.
 */
export function isSuccess(payload: SpecialistPayload): boolean {
  return !payload.error && (payload.status === "success" || payload.status === "partial");
}

/** Turn a sidecar response into text the model should act on. */
export function formatSpecialist(payload: SpecialistPayload): string {
  if (payload.error) {
    return `The ${payload.agent ?? "specialist"} could not finish: ${payload.error}`;
  }
  const task = payload.task_type ? ` [${payload.task_type}]` : "";
  const status = payload.status ?? "unknown";
  const body = (payload.output ?? "").trim();
  const caution =
    status === "partial"
      ? "\n\n(It reported PARTIAL — relay what it flagged rather than presenting this as complete.)"
      : "";
  return `${payload.agent ?? "Specialist"}${task} — status: ${status}.\n\n${body}${caution}`.trim();
}
