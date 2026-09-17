/**
 * Mentor guardrails — the code-enforced consequences ported from the Python
 * reasoner (`orchestrator/nodes/reasoner.py:apply_toolkit_guardrail`) and the
 * crisis boundary (`orchestrator/orchestrator.py`).
 *
 * Why this exists: in the Python build the reasoning LLM *proposed* a toolkit
 * and code *overrode* it. In PI, skills are chosen by the model via progressive
 * disclosure — so the safety-critical outcomes have to be re-expressed as
 * code-enforced directives and, where it matters, as message mutation.
 *
 * What is enforced here:
 *   1. Distress baseline — always present. PI has no reasoning-schema field
 *      like `disclosure_type`, so the model is the primary detector; this
 *      instructs it how to behave BEFORE it decides.
 *   2. Crisis backstop — explicit crisis language suppresses every skill and
 *      guarantees the support pointer (appended by code if the model omits it,
 *      exactly as `direct_response_node` did).
 *   3. Ship mode — "just give me the code" clears the teaching overlay (the
 *      teacher steps aside). Ported verbatim from TOOLKIT_SHIP_SIGNALS.
 *   4. Teacher backstop — "teach me X" engages the learning overlay.
 *   5. Code grounding — turns about Nik's real code must read before reasoning.
 *   6. Calendar grounding — scheduling turns must read the grid, never guess.
 *
 * Explicitly NOT ported yet: the "toolkits never ride on agent routing" rule.
 * There is no agent routing in PI until the specialist subagents land (Phase 4);
 * the rule comes back with them.
 *
 * Crisis detection is now two tiers, and this file owns the decision so there is
 * exactly one place to audit:
 *   Tier 1 — the keyword backstop below. Instant, code-only, explicit language.
 *   Tier 2 — the disclosure classifier (`mentor/src/disclosure.ts`), ported from
 *            the Python reasoner's STEP 0 `disclosure_type` field. It catches
 *            indirect distress that no keyword list reaches ("I'm just so tired
 *            of all of this"), is budgeted so a slow model cannot delay a turn,
 *            and fails open — the always-on baseline above is the floor beneath
 *            both tiers.
 * Either tier can raise the crisis path; neither can lower it.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { Context } from "@earendil-works/pi-ai";

import {
  DEFAULT_BUDGET_MS,
  startClassification,
  type ClassifierHandle,
  type DisclosureLabel,
  type ModelCall,
} from "../src/disclosure.ts";
import { CLASSIFIER_MODEL_ID, CLASSIFIER_PROVIDER_ID } from "./provider-nebius.ts";

// ---------------------------------------------------------------------------
// Ported signal tables (orchestrator/nodes/reasoner.py)
// ---------------------------------------------------------------------------

/** TOOLKIT_SHIP_SIGNALS — a direct demand for the answer ends teaching. */
const SHIP_SIGNALS: readonly string[] = [
  "ship this",
  "ship it",
  "just give me the code",
  "give me the answer",
  "show me the solution",
  "show me the code",
  "just tell me",
  "do it for me",
  "implement it",
  "implement it for me",
  "stop teaching",
  "stop quizzing",
  "no more hints",
  "give me the code",
  "just do it",
  "direct answer",
  "enough with the questions",
  "stop asking questions",
];

/** TOOLKIT_TEACHER_SWITCH_SIGNALS — an explicit request to be taught. */
const TEACHER_SIGNALS: readonly string[] = [
  "act as my teacher",
  "be my teacher",
  "switch to teacher",
  "teacher mode",
  "tutor me",
  "teach me",
  "tutorial mode",
  "help me learn",
  "learning mode",
  "teach me like",
  "explain it like",
  "guide me through",
];

/** TOOLKIT_CODE_SWITCH_SIGNALS — turns about Nik's ACTUAL code. */
const CODE_SIGNALS: readonly string[] = [
  "debug this",
  "debug my",
  "debugging my",
  "look at my code",
  "look at this file",
  "look at this code",
  "understand this code",
  "why is my code",
  "why does my code",
  "read this file",
  "read my code",
  "error in my",
  "traceback",
  "trace this",
  "stepping through",
  "review my code",
  "review this code",
  "explain this code",
  "in my repo",
  "in my project",
  "my codebase",
  "this function",
  "this class",
  "this module",
  "build this feature",
  "implement this feature",
  "my code",
  "this bug",
  "this error",
  "that error",
  "the traceback",
  "failing test",
  "test fails",
  "test failing",
  "it's broken",
  "it is broken",
];

/** TOOLKIT_CALENDAR_SWITCH_SIGNALS — scheduling / availability / anchors. */
const CALENDAR_SIGNALS: readonly string[] = [
  "my calendar",
  "the calendar",
  "on my calendar",
  "calendar",
  "schedule today",
  "schedule my",
  "to schedule",
  "want to schedule",
  "schedule it",
  "reschedule",
  "scheduled",
  "book a block",
  "book a slot",
  "place a block",
  "block my time",
  "time block",
  "time-block",
  "blocking",
  "protected time",
  "find a slot",
  "find slots",
  "find time for",
  "fit in",
  "available time",
  "when am i free",
  "when am i available",
  "when are you free",
  "free window",
  "free time",
  "i have free time",
];

/** Conservative crisis backstop — explicit language only (see limitation above). */
const CRISIS_SIGNALS: readonly string[] = [
  "kill myself",
  "killing myself",
  "suicide",
  "suicidal",
  "end my life",
  "ending my life",
  "want to die",
  "wanna die",
  "self harm",
  "self-harm",
  "hurt myself",
  "no reason to live",
  "better off dead",
  "can't go on",
  "cant go on",
];

/** Support pointers that satisfy the crisis boundary (same list as the Python build). */
const SUPPORT_POINTERS: readonly string[] = [
  "professional",
  "therapist",
  "counselor",
  "counsellor",
  "helpline",
];

const GATE_OPEN = "<mentor_guardrails>";
const GATE_CLOSE = "</mentor_guardrails>";

/**
 * Always-on distress handling. Ported from `orchestrator/orchestrator.py`
 * CRISIS_RESPONSE_GUIDANCE, but unconditional because PI has no disclosure
 * classifier to gate it on — the model must recognise distress itself.
 */
const DISTRESS_BASELINE = `DISTRESS HANDLING (always applies, above every skill):
If Nik discloses genuine distress, hopelessness, burnout, or crisis — respond as a caring
human, not a coach. Acknowledge what he said seriously and warmly, in his own terms.
No plans, no schedules, no productivity advice, no diagnosis, no acting as a therapist.
Gently point him toward someone he trusts or a mental-health professional, and remind him
you are here whenever he wants to talk. Warmth outranks every other instruction here.`;

/** Ported verbatim from CRISIS_RESPONSE_GUIDANCE. */
const CRISIS_GUIDANCE = `CRISIS SIGNAL DETECTED (code-enforced for this turn):
Respond as a caring human, not a coach: acknowledge what he said seriously and warmly, in
his own terms. Do NOT offer plans, schedules, or productivity advice. Do NOT diagnose or act
as a therapist. Gently encourage him to reach out to someone he trusts or a mental-health
professional, and remind him you are here whenever he wants to talk.`;

/** Ported verbatim from CRISIS_SUPPORT_LINE — appended by code when omitted. */
const CRISIS_SUPPORT_LINE =
  "One thing from me, mentor to friend: if what you're carrying ever feels heavier than a " +
  "bad week, please reach out to someone you trust or a mental-health professional. You " +
  "don't have to carry it alone — and I'm here whenever you want to talk.";

function matches(text: string, signals: readonly string[]): boolean {
  return signals.some((signal) => text.includes(signal));
}

interface DirectiveBlock {
  block: string;
  crisis: boolean;
}

/** How the crisis/distress decision was reached — kept so the directive is auditable. */
interface DirectiveDecision {
  crisis: boolean;
  distress: boolean;
  source: "keywords" | "classifier" | "none";
  label?: DisclosureLabel | null;
}

/**
 * Build the per-turn guardrail directives.
 *
 * Precedence is ported from `apply_toolkit_guardrail`: crisis outranks
 * everything, ship mode outranks teaching, and grounding backstops only add
 * requirements (never suppress the user's explicit intent).
 */
function buildDirectiveBlock(prompt: string, decision?: DirectiveDecision): DirectiveBlock {
  const lowered = (prompt || "").toLowerCase();
  const keywordCrisis = matches(lowered, CRISIS_SIGNALS);
  // Either tier can raise the crisis path; neither can lower it. So the decision
  // passed in may only *add* crisis to what the keywords already found.
  const crisis = keywordCrisis || (decision?.crisis ?? false);
  const distress = decision?.distress ?? keywordCrisis;
  const lines: string[] = [GATE_OPEN, DISTRESS_BASELINE];

  if (crisis) {
    lines.push(
      "",
      CRISIS_GUIDANCE,
      "",
      "- No skill applies on this turn. Do not teach, quiz, plan, schedule, or optimise.",
      "- Do not open workflow files. Presence and honesty only.",
      "- The support pointer is appended by code if you omit it, so focus on being human.",
    );
    if (!keywordCrisis && decision?.source === "classifier") {
      lines.push(
        `- Raised by the disclosure classifier (label: ${decision.label ?? "unknown"}), not by keywords.`,
      );
    }
  } else if (distress) {
    // Ported from the reasoner's "toolkits never ride on venting/burnout/crisis"
    // rule: this is not a crisis, but it is not a teaching turn either.
    lines.push(
      "",
      "DISTRESS DETECTED (code-enforced, not a crisis): he is venting or running on empty.",
      "- Do not teach, quiz, or hand him a plan right now.",
      "- Acknowledge the feeling first, in his own terms, and ask before offering anything.",
      "- A plan is welcome only if he asks for one on this turn.",
    );
  } else {
    const ship = matches(lowered, SHIP_SIGNALS);
    const teacher = matches(lowered, TEACHER_SIGNALS);
    const code = matches(lowered, CODE_SIGNALS);
    const calendar = matches(lowered, CALENDAR_SIGNALS);

    if (ship) {
      lines.push(
        "",
        "SHIP MODE (code-enforced): Nik asked for the answer directly.",
        "- The teaching overlay steps aside for this turn: give him the real thing, cleanly.",
        "- Do not quiz, do not hint-ladder, do not withhold. A top-tier teacher knows when the",
        "  lesson is over. This overrides any teaching skill that is loaded.",
      );
    } else if (teacher) {
      lines.push(
        "",
        "TEACHER REQUEST (code-enforced): Nik explicitly asked to be taught.",
        "- Load the `learning-companion` skill and pick its matching workflow before replying.",
        "- Do not silently drop the teaching intent, and do not answer it outright.",
      );
    }

    if (code) {
      lines.push(
        "",
        "CODE GROUNDING (code-enforced): this turn is about Nik's actual code.",
        "- Read the real files before reasoning about them. Use read/grep/find/ls/bash.",
        "- Never assert anything about code you have not looked at. If you could not read it,",
        "  say so plainly.",
      );
    }

    if (calendar) {
      lines.push(
        "",
        "CALENDAR GROUNDING (code-enforced): this turn touches scheduling or availability.",
        "- His calendar is the mentor's 48-slot day grid — not Google Calendar or any external service.",
        "- You CAN read it (`get_day_grid`, `find_available_slots`) and you CAN write to it",
        "  (`place_time_block`, `set_anchor`). If he asks whether you have access, the answer is yes:",
        "  you can read his grid and book or move blocks, and he confirms before writes happen.",
        "- Always read the grid and answer from what it returns. Never guess free time.",
        "- Propose a block and get his agreement before writing — a booking changes his real day.",
      );
    }
  }

  lines.push(GATE_CLOSE);
  return { block: lines.join("\n"), crisis };
}

// ---------------------------------------------------------------------------
// Tier 2 — the disclosure classifier, wired to PI's provider registry
// ---------------------------------------------------------------------------

/** Kill switch: `PI_CRISIS_CLASSIFIER=0` runs Tier 1 (keywords) + the baseline only. */
function classifierEnabled(): boolean {
  return (process.env.PI_CRISIS_CLASSIFIER ?? "1") !== "0";
}

/** `PI_CRISIS_DEBUG=1` also logs benign verdicts and their latency. */
function classifierDebug(): boolean {
  return process.env.PI_CRISIS_DEBUG === "1";
}

/** How long a turn waits for a verdict before generation starts. */
function classifierBudgetMs(): number {
  const raw = Number(process.env.PI_CRISIS_BUDGET_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_BUDGET_MS;
}

/**
 * Which model classifies.
 *
 * Three steps, in order, so the check never silently stops running:
 *   1. `PI_CRISIS_MODEL="provider/modelId"` — an explicit override.
 *   2. The fast tier registered by `provider-nebius.ts` (`nebius-classifier`) —
 *      a ~30B MoE answering one word, measured well under the budget while the
 *      235B converser straddled it.
 *   3. The session model — guaranteed authenticated, so a missing fast tier
 *      degrades latency rather than safety.
 */
function classifierModel(ctx: ExtensionContext) {
  const override = process.env.PI_CRISIS_MODEL;
  if (override) {
    const slash = override.indexOf("/");
    const provider = slash > 0 ? override.slice(0, slash) : override;
    const modelId = slash > 0 ? override.slice(slash + 1) : "";
    const found = modelId ? ctx.modelRegistry.find(provider, modelId) : undefined;
    if (found) {
      return found;
    }
    console.warn(`[mentor] PI_CRISIS_MODEL=${override} is not a known model; falling back`);
  }

  const fast = ctx.modelRegistry.find(CLASSIFIER_PROVIDER_ID, CLASSIFIER_MODEL_ID);
  if (fast) {
    return fast;
  }

  console.warn("[mentor] fast classifier tier unavailable; using the session model");
  return ctx.model;
}

/**
 * A `ModelCall` bound to PI's registry, or null when no model is available.
 *
 * Goes through `ctx.modelRegistry.streamSimple` (not a hand-rolled fetch) so it
 * resolves the same auth as the turn itself — including the provider registered by
 * `provider-nebius.ts`, which a raw client would not see.
 */
function modelCallFor(ctx: ExtensionContext): ModelCall | null {
  const model = classifierModel(ctx);
  if (!model) {
    return null;
  }
  return async (system, user, options) => {
    const context: Context = {
      systemPrompt: system,
      messages: [{ role: "user", content: user, timestamp: Date.now() }],
    };
    const stream = ctx.modelRegistry.streamSimple(model, context, {
      maxTokens: 8, // one word out; the budget is enforced by the caller
      temperature: 0,
      signal: options.signal,
    });
    const result = await stream.result();
    if (result.stopReason === "error") {
      throw new Error(result.errorMessage ?? "classifier model call failed");
    }
    const blocks = (result.content ?? []) as Array<{ type?: string; text?: string }>;
    return blocks
      .filter((block) => block?.type === "text")
      .map((block) => block.text ?? "")
      .join(" ");
  };
}

export default function (pi: ExtensionAPI) {
  // Set by `before_agent_start`, consumed by `message_end`. Module scope is the
  // right place: an extension instance lives for the session, and the crisis
  // decision belongs to the turn currently being generated.
  let crisisTurn = false;
  // The Tier-2 verdict, kept rather than dropped: it is awaited briefly before
  // generation and consulted again at `message_end`, so a verdict that arrives
  // late still guarantees the support pointer.
  let pendingVerdict: ClassifierHandle | null = null;

  pi.on("before_agent_start", async (event, ctx) => {
    const prompt = event.prompt ?? "";
    const keywordCrisis = matches((prompt || "").toLowerCase(), CRISIS_SIGNALS);

    let decision: DirectiveDecision = keywordCrisis
      ? { crisis: true, distress: true, source: "keywords" }
      : { crisis: false, distress: false, source: "none" };

    if (!keywordCrisis && classifierEnabled()) {
      const callModel = modelCallFor(ctx);
      if (callModel) {
        pendingVerdict = startClassification(prompt, callModel, {
          budgetMs: classifierBudgetMs(),
          signal: ctx.signal,
        });
        const verdict = await pendingVerdict.withinBudget();
        if (verdict.crisis) {
          decision = { crisis: true, distress: true, source: "classifier", label: verdict.label };
          console.warn(`[mentor] disclosure classifier raised crisis (${verdict.ms}ms)`);
        } else if (verdict.label === "venting" || verdict.label === "burnout") {
          decision = { crisis: false, distress: true, source: "classifier", label: verdict.label };
          console.warn(`[mentor] disclosure classifier: ${verdict.label} (${verdict.ms}ms)`);
        } else if (verdict.error) {
          // Visible, never silent: keywords + the always-on baseline still apply.
          console.warn(`[mentor] disclosure classifier unavailable: ${verdict.error}`);
        } else if (classifierDebug()) {
          console.warn(`[mentor] disclosure verdict: ${verdict.label ?? "unparseable"} in ${verdict.ms}ms`);
        }
      } else {
        console.warn("[mentor] no model available for the disclosure classifier");
      }
    }

    crisisTurn = decision.crisis;
    const { block } = buildDirectiveBlock(prompt, decision);

    // Idempotent: another extension (or a chained handler) may have added it.
    if (event.systemPrompt.includes(GATE_OPEN)) {
      return;
    }
    return { systemPrompt: `${event.systemPrompt}\n\n${block}` };
  });

  // Code-enforced crisis footer, ported from direct_response_node: the support
  // pointer must be present in every crisis response, even when the model omits
  // it. This is the part that must never depend on LLM judgment.
  pi.on("message_end", async (event) => {
    // A Tier-2 verdict that landed after generation still counts. In the common
    // case it resolved before the answer was written (so this await is already
    // settled and costs nothing); in the worst case it is the only thing standing
    // between indirect distress and a response with no support pointer.
    if (pendingVerdict) {
      const verdict = await pendingVerdict.final();
      pendingVerdict = null;
      if (verdict?.crisis) {
        console.warn("[mentor] disclosure classifier raised crisis late — enforcing the pointer");
        crisisTurn = true;
      }
    }

    if (!crisisTurn) {
      return;
    }
    const message = event.message as {
      role?: string;
      content?: Array<{ type?: string; text?: string }>;
    };

    if (message?.role !== "assistant" || !Array.isArray(message.content)) {
      return;
    }

    const text = message.content
      .filter((part) => part?.type === "text")
      .map((part) => part.text ?? "")
      .join("\n")
      .toLowerCase();

    crisisTurn = false;

    if (SUPPORT_POINTERS.some((pointer) => text.includes(pointer))) {
      return;
    }

    console.warn("[mentor] crisis response omitted a support pointer — appending by code");
    return {
      message: {
        ...(event.message as object),
        content: [
          ...message.content,
          { type: "text", text: CRISIS_SUPPORT_LINE },
        ],
      },
    } as never;
  });
}