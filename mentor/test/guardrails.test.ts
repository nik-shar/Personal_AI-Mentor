/**
 * Wiring tests for the crisis path in `guardrails.ts`.
 *
 * The classifier's own contract is covered in `disclosure.test.ts`. This file pins
 * the *guarantee* the safety design exists for: whatever raises it — keywords or the
 * LLM verdict — a crisis turn ends with a support pointer in the outgoing message,
 * even when the model omits one.
 *
 * PI is faked at its API boundary (`pi.on` + `ctx.modelRegistry`), so this runs with
 * no provider, no network and no session.
 *
 * Run (Node 24 strips TypeScript natively, no test framework needed):
 *   node --test mentor/test/guardrails.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import guardrails from "../extensions/guardrails.ts";

type Handler = (event: any, ctx: any) => Promise<any>;

/** Capture the handlers the extension registers, exactly as PI would. */
function mountExtension(verdict: string | null, options: { failCall?: boolean } = {}) {
  const handlers = new Map<string, Handler>();
  const fakePi = {
    on: (name: string, handler: Handler) => handlers.set(name, handler),
  };
  guardrails(fakePi as never);

  const model = { id: "fake-classifier" };
  const streamed: Array<Record<string, unknown>> = [];
  const ctx = {
    model,
    modelRegistry: {
      find: () => model,
      streamSimple: (_model: unknown, context: unknown, opts: unknown) => {
        streamed.push({ context, opts });
        return {
          result: async () => {
            if (options.failCall) {
              throw new Error("provider down");
            }
            return { stopReason: "stop", content: [{ type: "text", text: verdict ?? "" }] };
          },
        };
      },
    },
  };

  return { handlers, ctx, streamed };
}

function assistantMessage(text: string) {
  return { role: "assistant", content: [{ type: "text", text }] };
}

const SUPPORT_MARKER = "mental-health professional";

test("explicit crisis language raises the crisis directive without any model call", async () => {
  const { handlers, ctx, streamed } = mountExtension("none");
  const result = await handlers.get("before_agent_start")!(
    { prompt: "honestly I want to end my life", systemPrompt: "BASE" },
    ctx,
  );

  assert.match(result.systemPrompt, /CRISIS SIGNAL DETECTED/);
  assert.match(result.systemPrompt, /No skill applies on this turn/);
  assert.equal(streamed.length, 0, "keywords must not pay for a classifier call");
});

test("a classifier verdict of crisis raises the very same directive", async () => {
  const { handlers, ctx } = mountExtension("crisis");
  const result = await handlers.get("before_agent_start")!(
    { prompt: "I'm just so tired of all of this", systemPrompt: "BASE" },
    ctx,
  );

  assert.match(result.systemPrompt, /CRISIS SIGNAL DETECTED/);
  assert.match(result.systemPrompt, /Raised by the disclosure classifier/);
  assert.match(result.systemPrompt, /label: crisis/);
});

test("the verdict reaches the model call through PI's registry, not a side channel", async () => {
  const { handlers, ctx, streamed } = mountExtension("none");
  await handlers.get("before_agent_start")!({ prompt: "plan my day", systemPrompt: "BASE" }, ctx);

  assert.equal(streamed.length, 1);
  const call = streamed[0] as { context: { messages: Array<{ content: string }> } };
  assert.match(call.context.messages[0].content, /^<message>\nplan my day\n<\/message>$/);
});

test("venting / burnout suppresses teaching without claiming a crisis", async () => {
  const { handlers, ctx } = mountExtension("burnout");
  const result = await handlers.get("before_agent_start")!(
    { prompt: "I'm exhausted and nothing is working", systemPrompt: "BASE" },
    ctx,
  );

  assert.match(result.systemPrompt, /DISTRESS DETECTED/);
  assert.match(result.systemPrompt, /Do not teach, quiz, or hand him a plan/);
  assert.doesNotMatch(result.systemPrompt, /CRISIS SIGNAL DETECTED/);
});

test("a benign verdict leaves the turn alone", async () => {
  const { handlers, ctx } = mountExtension("seeking_guidance");
  const result = await handlers.get("before_agent_start")!(
    { prompt: "am I free at 4pm?", systemPrompt: "BASE" },
    ctx,
  );

  assert.doesNotMatch(result.systemPrompt, /CRISIS SIGNAL DETECTED/);
  assert.doesNotMatch(result.systemPrompt, /DISTRESS DETECTED/);
  assert.match(result.systemPrompt, /DISTRESS HANDLING \(always applies/);
});

/* -------------------------------------------------------------------------- */
/* The guarantee: a crisis turn always carries a support pointer               */
/* -------------------------------------------------------------------------- */

test("the pointer is appended by code when the model omits it (keyword crisis)", async () => {
  const { handlers, ctx } = mountExtension("none");
  await handlers.get("before_agent_start")!(
    { prompt: "I want to end my life", systemPrompt: "BASE" },
    ctx,
  );

  const outcome = await handlers.get("message_end")!(
    { message: assistantMessage("I'm here. Tell me more.") },
    ctx,
  );

  assert.ok(outcome?.message, "the footer must mutate the outgoing message");
  const text = outcome.message.content.map((part: { text: string }) => part.text).join("\n");
  assert.match(text, /I'm here\. Tell me more\./);
  assert.match(text, new RegExp(SUPPORT_MARKER));
});

test("the pointer is appended when only the classifier raised the crisis", async () => {
  const { handlers, ctx } = mountExtension("crisis");
  await handlers.get("before_agent_start")!(
    { prompt: "I'm just so tired of all of this", systemPrompt: "BASE" },
    ctx,
  );

  const outcome = await handlers.get("message_end")!(
    { message: assistantMessage("That sounds heavy.") },
    ctx,
  );

  assert.ok(outcome?.message);
  const text = outcome.message.content.map((part: { text: string }) => part.text).join("\n");
  assert.match(text, new RegExp(SUPPORT_MARKER));
});

test("a model-supplied pointer is left exactly as written", async () => {
  const { handlers, ctx } = mountExtension("none");
  await handlers.get("before_agent_start")!(
    { prompt: "I want to end my life", systemPrompt: "BASE" },
    ctx,
  );

  const outcome = await handlers.get("message_end")!(
    { message: assistantMessage("Please reach out to a mental-health professional today.") },
    ctx,
  );

  assert.equal(outcome, undefined, "no double-footer when the model already complied");
});

test("a benign turn is never mutated", async () => {
  const { handlers, ctx } = mountExtension("none");
  await handlers.get("before_agent_start")!({ prompt: "plan my day", systemPrompt: "BASE" }, ctx);

  const outcome = await handlers.get("message_end")!(
    { message: assistantMessage("Here's your plan.") },
    ctx,
  );

  assert.equal(outcome, undefined);
});

test("the footer is not re-applied to later turns", async () => {
  const { handlers, ctx } = mountExtension("none");
  await handlers.get("before_agent_start")!(
    { prompt: "I want to end my life", systemPrompt: "BASE" },
    ctx,
  );
  await handlers.get("message_end")!({ message: assistantMessage("I'm here.") }, ctx);

  // Next turn, benign: the crisis must not leak into it.
  await handlers.get("before_agent_start")!({ prompt: "what's on today?", systemPrompt: "BASE" }, ctx);
  const outcome = await handlers.get("message_end")!(
    { message: assistantMessage("Just your study block.") },
    ctx,
  );

  assert.equal(outcome, undefined, "crisis state must not persist across turns");
});

/* -------------------------------------------------------------------------- */
/* Fail-open: a broken classifier must not break or weaken the turn           */
/* -------------------------------------------------------------------------- */

test("a failing classifier falls back to keywords + baseline, never to no guardrails", async () => {
  const { handlers, ctx } = mountExtension(null, { failCall: true });
  const result = await handlers.get("before_agent_start")!(
    { prompt: "I'm just so tired of all of this", systemPrompt: "BASE" },
    ctx,
  );

  assert.ok(result?.systemPrompt, "the turn must still get its directives");
  assert.match(result.systemPrompt, /DISTRESS HANDLING \(always applies/);
  assert.doesNotMatch(result.systemPrompt, /CRISIS SIGNAL DETECTED/);
});

test("keywords still win when the classifier is unavailable", async () => {
  const { handlers, ctx } = mountExtension(null, { failCall: true });
  const result = await handlers.get("before_agent_start")!(
    { prompt: "I want to end my life", systemPrompt: "BASE" },
    ctx,
  );

  assert.match(result.systemPrompt, /CRISIS SIGNAL DETECTED/);
});

test("no model available is logged and the turn proceeds", async () => {
  const handlers = new Map<string, Handler>();
  guardrails({ on: (name: string, handler: Handler) => handlers.set(name, handler) } as never);
  const ctxWithoutModels = { model: undefined, modelRegistry: { find: () => undefined } };

  const result = await handlers.get("before_agent_start")!(
    { prompt: "plan my day", systemPrompt: "BASE" },
    ctxWithoutModels,
  );

  assert.match(result.systemPrompt, /DISTRESS HANDLING \(always applies/);
});