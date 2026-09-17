/**
 * mentor/src/disclosure.ts
 *
 * The disclosure classifier — the safety gate the Python build had and the PI
 * build was missing (`orchestrator/nodes/reasoner.py` STEP 0 returned
 * `disclosure_type` from the *reasoning* call, so classification cost nothing
 * extra there; here it is its own small call).
 *
 * Why it exists: the keyword backstop in `guardrails.ts` only catches explicit
 * language ("kill myself"). Indirect distress — "I'm just so tired of all of
 * this", "I don't see the point anymore" — sails past it, and that is exactly the
 * sentence a mentor must not answer with a study plan.
 *
 * Shape of this module: pure logic plus an **injected** model call
 * (`ModelCall`). That seam is deliberate — the taxonomy, verdict parsing, budget
 * and fail-open behaviour are all unit-testable with a fake caller, and no test
 * ever needs a live provider (`mentor/test/disclosure.test.ts`).
 *
 * Fail-open is the contract: every failure path returns `label: null` with a
 * reason. A classifier that cannot answer must never block a turn; the always-on
 * distress baseline in `guardrails.ts` is the floor beneath this.
 */

/** The taxonomy, ported from the Python reasoner's `disclosure_type` field. */
export const DISCLOSURE_LABELS = [
  "venting",
  "seeking_guidance",
  "burnout",
  "context_sharing",
  "crisis",
  "none",
] as const;

export type DisclosureLabel = (typeof DISCLOSURE_LABELS)[number];

/** Labels that suppress teaching/planning behaviour for the turn. */
export const DISTRESS_LABELS: readonly DisclosureLabel[] = ["venting", "burnout", "crisis"];

/**
 * The classification prompt.
 *
 * Wording is ported from the reasoner's STEP 0 block so the taxonomy does not
 * drift between the two runtimes. Deliberately strict about the output format
 * (one word) because a verbose answer is a parse failure waiting to happen.
 */
export const CLASSIFIER_SYSTEM_PROMPT = [
  "You are a safety classifier for a personal mentor's chat.",
  "Read the user's message and reply with EXACTLY ONE of these words, nothing else:",
  "",
  "venting           — emotional release, frustration, complaining; no request for help",
  "seeking_guidance  — a normal question or a request for advice about work, study or plans",
  "burnout           — exhaustion, being overwhelmed, running on empty, wanting to quit it all",
  "context_sharing   — a neutral update or fact, no emotional weight",
  "crisis            — hopelessness, 'no point to this', self-harm signals, not wanting to be here",
  "none              — anything else, including greetings and small talk",
  "",
  "Judge the emotional content, not the topic. A message about giving up on a career IS",
  "burnout even if no distress word appears; a message about a hard deadline with a plan",
  "to meet it is NOT. Reply with the single word only.",
].join("\n");

/** Cap the classified text: the signal is at the start, and this bounds cost. */
export const MAX_CLASSIFIER_INPUT_CHARS = 2000;

/** Wrap the message so its own text cannot be read as classifier instructions. */
export function buildClassifierInput(text: string): string {
  const trimmed = (text ?? "").trim().slice(0, MAX_CLASSIFIER_INPUT_CHARS);
  return `<message>\n${trimmed}\n</message>`;
}

/**
 * Parse the model's reply into a label.
 *
 * Tolerant on purpose: a small model may answer "crisis." or "Crisis — hopelessness".
 * Scans for the first label word that appears as a whole word, `crisis` first so
 * that a rambling answer mentioning several labels fails safe rather than
 * settling on "none".
 */
export function parseVerdict(raw: string): DisclosureLabel | null {
  if (!raw) {
    return null;
  }
  const lowered = raw.toLowerCase();
  const ordered: readonly DisclosureLabel[] = [
    "crisis",
    "burnout",
    "venting",
    "seeking_guidance",
    "context_sharing",
    "none",
  ];
  for (const label of ordered) {
    if (new RegExp(`\\b${label}\\b`).test(lowered)) {
      return label;
    }
  }
  return null;
}

export interface ClassifierOptions {
  /** Ceiling on how long a turn waits for a verdict before generation starts. */
  budgetMs?: number;
  /** Outer abort signal (the agent's own), forwarded to the model call. */
  signal?: AbortSignal;
}

export interface ClassifyResult {
  /** null when no verdict was obtained (fail-open — see `error`). */
  label: DisclosureLabel | null;
  crisis: boolean;
  error: string | null;
  ms: number;
  timedOut: boolean;
}

/**
 * The injected model call: given a system prompt and user text, return the raw
 * reply. `guardrails.ts` supplies PI's real provider call; tests supply a fake.
 */
export type ModelCall = (system: string, user: string, options: { signal: AbortSignal }) => Promise<string>;

/**
 * Default wait before generation proceeds without a verdict.
 *
 * Measured against the live Nebius endpoint (Qwen3-30B classifier tier): ~0.9s
 * for a benign turn, up to ~2.8s under load. The budget caps the *wait*, never
 * the call — a verdict that lands late is still read at `message_end`, so this
 * number trades directive timing against turn latency, not safety.
 */
export const DEFAULT_BUDGET_MS = 1500;

/**
 * A running classification. Two ways to read it, and both matter:
 *
 *   withinBudget() — "I want to decide before generating the answer." Resolves at
 *                    the budget, or earlier if the verdict arrives. Expiry does
 *                    **not** cancel the call: the answer is still coming.
 *   final()        — "I want the truth before the reply leaves." Resolves when the
 *                    verdict truly arrives, and never rejects.
 *
 * That split is the safety design, and it is measured rather than assumed: on the
 * live Nebius endpoint a 1200ms budget expired *before* the verdict arrived. If
 * expiry cancelled the call, the late verdict — the only thing that can enforce a
 * support pointer on a message no keyword reaches — would be thrown away.
 */
export interface ClassifierHandle {
  withinBudget(): Promise<ClassifyResult>;
  final(): Promise<ClassifyResult>;
}

export function startClassification(
  text: string,
  callModel: ModelCall,
  options: ClassifierOptions = {},
): ClassifierHandle {
  const started = Date.now();
  const budgetMs = options.budgetMs ?? DEFAULT_BUDGET_MS;
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();

  // Only an outer abort (the agent cancelling the turn) stops the call. The
  // budget deliberately does not — see the note above.
  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort();
    } else {
      options.signal.addEventListener("abort", forwardAbort, { once: true });
    }
  }

  // Two-argument `then` on purpose: `final` must never reject, so a failure can
  // never become an unhandled rejection or a thrown error mid-turn.
  const settled: Promise<ClassifyResult> = callModel(
    CLASSIFIER_SYSTEM_PROMPT,
    buildClassifierInput(text),
    { signal: controller.signal },
  ).then(
    (raw) => {
      const label = parseVerdict(String(raw ?? ""));
      return {
        label,
        crisis: label === "crisis",
        error: label === null ? `unparseable verdict: ${String(raw ?? "").slice(0, 60)}` : null,
        ms: Date.now() - started,
        timedOut: false,
      };
    },
    (error: unknown) => ({
      label: null,
      crisis: false,
      error: error instanceof Error ? error.message : String(error),
      ms: Date.now() - started,
      timedOut: false,
    }),
  );

  const final = settled.finally(() => {
    options.signal?.removeEventListener("abort", forwardAbort);
  });

  async function withinBudget(): Promise<ClassifyResult> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<ClassifyResult>((resolve) => {
      timer = setTimeout(
        () =>
          resolve({
            label: null,
            crisis: false,
            error: `no verdict within ${budgetMs}ms (classification still running)`,
            ms: Date.now() - started,
            timedOut: true,
          }),
        budgetMs,
      );
    });
    try {
      return await Promise.race([final, expired]);
    } finally {
      if (timer) {
        clearTimeout(timer);
      }
    }
  }

  return { withinBudget, final: () => final };
}

/**
 * Convenience wrapper: classify, waiting at most `budgetMs`.
 *
 * Use this when a late verdict is of no use to you. Use `startClassification`
 * when you can still act on it later — which is exactly what the crisis footer
 * does at `message_end`.
 */
export async function classifyDisclosure(
  text: string,
  callModel: ModelCall,
  options: ClassifierOptions = {},
): Promise<ClassifyResult> {
  return startClassification(text, callModel, options).withinBudget();
}