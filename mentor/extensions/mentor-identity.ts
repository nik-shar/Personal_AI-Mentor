/**
 * Mentor identity — the persistent core the mentor never drops.
 *
 * Gate: G4 (identity / prompt policy) → TypeScript.
 *
 * PI already loads AGENTS.md / CLAUDE.md as *project* context (neither exists in
 * this repo). This block is the mentor's voice plus its constitution, injected as
 * a system-prompt overlay on every agent start. Skill overlays layer on top of
 * this; nothing layers underneath it.
 *
 * The text is NOT here. `src/identity.ts` reads it fresh from the same two
 * runtime-neutral files the Python build reads:
 *
 *   mentor_agent_guidelines.md   the constitution (§1–§7)
 *   mentor_persona.md            voice rules, hard rules, few-shot examples
 *
 * Before this, the voice was hand-duplicated here and drifted from
 * `cognition/persona.py`, and the constitution never reached the PI core at all.
 * Loading the same files on both sides makes drift structurally impossible. This
 * file is wiring only — edit the content files, never this one.
 *
 * Ordering: PI chains `before_agent_start` handlers, each receiving the previous
 * result's `systemPrompt` (core/extensions/runner.ts), so this overlay and
 * `guardrails.ts` compose cumulatively instead of overwriting each other. The
 * guardrails load last and therefore keep the final word on safety.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import {
  IDENTITY_OPEN,
  REPO_ROOT,
  buildIdentityBlock,
  coreIdentity,
  guidelinesPath,
  personaPath,
  voiceRules,
} from "../src/identity.ts";

export default function (pi: ExtensionAPI) {
  // Fail loud at load, not silently at turn time. The block still assembles from
  // whatever is readable (fail-open), but a missing file means the mentor is
  // running without its constitution or its voice — that should be visible the
  // moment PI starts, not something to infer from a reply. These reads also warm
  // the module cache for the first turn.
  if (!coreIdentity()) {
    console.warn(
      `[mentor] constitution missing at ${guidelinesPath()} — the mentor runs WITHOUT §1–§7. ` +
        `Repo root resolved to ${REPO_ROOT}.`,
    );
  }
  if (!voiceRules()) {
    console.warn(
      `[mentor] voice layer missing at ${personaPath()} — the mentor runs WITHOUT its persona. ` +
        `Repo root resolved to ${REPO_ROOT}.`,
    );
  }

  pi.on("before_agent_start", async (event) => {
    // Idempotent: never double-inject if the overlay is already present.
    if (event.systemPrompt.includes(IDENTITY_OPEN)) {
      return;
    }
    return { systemPrompt: `${event.systemPrompt}\n\n${buildIdentityBlock()}` };
  });
}
