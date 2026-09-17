/**
 * Nebius provider registration.
 *
 * The mentor's Python stack already talks to Nebius over an OpenAI-compatible
 * endpoint (3-tier LLM strategy: router / converser / writer). Registering the
 * same endpoints here keeps model identity identical across both runtimes —
 * no proxy layer, no second place where a model name can drift.
 *
 * Four tiers are registered: `classifier` (the disclosure classifier's fast
 * one-word call), `nebius` (converser — the mentor's voice), `nebius-writer`
 * (deep generation), and the classifier id is exported so the guardrails
 * extension resolves the same model instead of naming its own.
 *
 * Gate: G4 (provider registration is a policy hook) → TypeScript.
 *
 * Auth: `$NEBIUS_API_KEY` is interpolated from the environment at request time.
 * `scripts/run_pi_mentor.sh` loads it from `.env`, so pi never needs a copy of
 * the secret in settings.json.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Tier 2 — "converser": the mentor's everyday voice.
const CONVERSER_BASE_URL = "https://api.tokenfactory.nebius.com/v1/";
const CONVERSER_MODEL_ID = "Qwen/Qwen3-235B-A22B-Instruct-2507";

// Tier 3 — "writer": reserved for deep generation (tutorials, drafts, resumes).
const WRITER_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/";
const WRITER_MODEL_ID = "MiniMaxAI/MiniMax-M3";

// Tier 0 — "classifier": the disclosure classifier's one-word safety call
// (`guardrails.ts`). Same model family as the converser so prompt behaviour stays
// consistent, but a small MoE — measured ~1.1s on the 235B converser versus a few
// hundred ms here, and this call sits on the turn's critical path.
//
// Exported so the classifier extension resolves this model through the same
// registry rather than carrying its own copy of the id (the drift this file's
// header warns about).
export const CLASSIFIER_PROVIDER_ID = "nebius-classifier";
export const CLASSIFIER_MODEL_ID = "Qwen/Qwen3-30B-A3B-Instruct-2507";

export default function (pi: ExtensionAPI) {
  pi.registerProvider("nebius", {
    name: "Nebius",
    baseUrl: CONVERSER_BASE_URL,
    apiKey: "$NEBIUS_API_KEY",
    api: "openai-completions",
    models: [
      {
        id: CONVERSER_MODEL_ID,
        name: "Qwen3 235B (Nebius converser)",
        reasoning: false,
        input: ["text"],
        // Cost is intentionally 0: pi only uses it for the usage display, and
        // fabricating per-token pricing would be worse than showing nothing.
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 262144,
        maxTokens: 8192,
      },
    ],
  });

  pi.registerProvider("nebius-writer", {
    name: "Nebius (writer)",
    baseUrl: WRITER_BASE_URL,
    apiKey: "$NEBIUS_API_KEY",
    api: "openai-completions",
    models: [
      {
        id: WRITER_MODEL_ID,
        name: "MiniMax-M3 (Nebius writer)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        // TODO: confirm the real limits from Nebius docs before relying on this
        // tier for long generations. Conservative values avoid context overflow.
        contextWindow: 131072,
        maxTokens: 8192,
      },
    ],
  });

  pi.registerProvider(CLASSIFIER_PROVIDER_ID, {
    name: "Nebius (classifier)",
    baseUrl: CONVERSER_BASE_URL,
    apiKey: "$NEBIUS_API_KEY",
    api: "openai-completions",
    models: [
      {
        id: CLASSIFIER_MODEL_ID,
        name: "Qwen3 30B A3B (Nebius classifier)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        // One word in, one word out — a small window is honest, not limiting.
        contextWindow: 32768,
        maxTokens: 2048,
      },
    ],
  });
}
