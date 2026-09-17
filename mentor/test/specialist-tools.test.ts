/**
 * Tests for the Phase-4 specialist bridge.
 *
 * This file deliberately does NOT import `extensions/specialist-tools.ts`: the
 * extensions import `typebox`, which is a peerDependency provided by PI's own
 * loader and does not resolve under plain `node --test`. So the logic worth
 * testing lives in `src/specialist.ts` (status contract + formatting) and
 * `src/sidecar.ts` (the fail-open transport) — the same split `planning.ts` and
 * `disclosure.ts` use.
 *
 * Three things are pinned:
 *
 * 1. A sidecar response becomes text the model can act on, including `partial`,
 *    which must be relayed rather than smoothed over.
 * 2. Every transport failure (HTTP error, unreachable, real timeout) returns a
 *    readable error instead of throwing into the agent loop.
 * 3. The generated tool contract has not drifted from what the extensions
 *    register — checked against source, so adding a tool in `api/tools.py`
 *    without wiring it fails here instead of silently in a live session.
 *
 * Run (Node 24 strips TypeScript natively, no test framework needed):
 *   node --test mentor/test/specialist-tools.test.ts
 */

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import { MENTOR_TOOLS } from "../src/generated/mentor-tools.ts";
import { callTool } from "../src/sidecar.ts";
import {
  DEFAULT_SPECIALIST_TIMEOUT_MS,
  formatSpecialist,
  isSuccess,
  specialistTimeoutMs,
} from "../src/specialist.ts";

/* -------------------------------------------------------------------------- */
/* The status contract                                                        */
/* -------------------------------------------------------------------------- */

test("success is taken from the sidecar, never inferred from a non-empty reply", () => {
  assert.equal(isSuccess({ status: "success" }), true);
  assert.equal(isSuccess({ status: "partial" }), true, "partial did the work");
  assert.equal(isSuccess({ status: "failed" }), false);

  // The trap: output with no status, and output alongside an error.
  assert.equal(isSuccess({ output: "looks fine" }), false);
  assert.equal(isSuccess({ status: "success", error: "merge failed" }), false);
});

test("a partial result tells the model to relay the flag", () => {
  const text = formatSpecialist({
    status: "partial",
    agent: "linkedin_writer",
    task_type: "write_linkedin_post",
    output: "Draft attached, but the critic hit its revision cap.",
  });

  assert.match(text, /linkedin_writer \[write_linkedin_post\]/);
  assert.match(text, /status: partial/);
  assert.match(text, /PARTIAL/);
  assert.match(text, /critic hit its revision cap/);
});

test("a success result reports the status without inventing a caveat", () => {
  const text = formatSpecialist({
    status: "success",
    agent: "goal_decomposer",
    task_type: "decompose_goal",
    output: "Roadmap created with 12 nodes.",
  });

  assert.match(text, /status: success/);
  assert.doesNotMatch(text, /PARTIAL/);
});

test("an error payload is reported as a failure, not a result", () => {
  const text = formatSpecialist({ agent: "job_hunter", error: "unknown task_type 'find_jobs'" });

  assert.match(text, /job_hunter could not finish/);
  assert.match(text, /find_jobs/);
  assert.equal(isSuccess({ agent: "job_hunter", error: "boom" }), false);
});

test("a missing agent name still produces readable text", () => {
  assert.match(formatSpecialist({ status: "failed" }), /^Specialist/);
  assert.match(formatSpecialist({ status: "unknown" }), /status: unknown/);
});

/* -------------------------------------------------------------------------- */
/* Timeout policy                                                             */
/* -------------------------------------------------------------------------- */

test("the specialist timeout is minutes, not the 15s read default", () => {
  assert.equal(specialistTimeoutMs({}), DEFAULT_SPECIALIST_TIMEOUT_MS);
  assert.ok(
    DEFAULT_SPECIALIST_TIMEOUT_MS > 15_000,
    "goal decomposition legitimately runs for minutes",
  );
  // The env override wins, and junk falls back rather than removing the bound.
  assert.equal(specialistTimeoutMs({ MENTOR_SPECIALIST_TIMEOUT_MS: "120000" }), 120_000);
  assert.equal(
    specialistTimeoutMs({ MENTOR_SPECIALIST_TIMEOUT_MS: "not-a-number" }),
    DEFAULT_SPECIALIST_TIMEOUT_MS,
  );
  assert.equal(specialistTimeoutMs({ MENTOR_SPECIALIST_TIMEOUT_MS: "0" }), DEFAULT_SPECIALIST_TIMEOUT_MS);
  assert.equal(specialistTimeoutMs({ MENTOR_SPECIALIST_TIMEOUT_MS: "-5" }), DEFAULT_SPECIALIST_TIMEOUT_MS);
});

/* -------------------------------------------------------------------------- */
/* Transport: every failure is readable text, never a thrown exception        */
/* -------------------------------------------------------------------------- */

/** Run `fn` with a stubbed global fetch, restoring it afterwards. */
async function withFetch<T>(impl: typeof fetch, fn: () => Promise<T>): Promise<T> {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  try {
    return await fn();
  } finally {
    globalThis.fetch = original;
  }
}

test("an HTTP rejection never reaches the agent loop as an exception", async () => {
  const result = await withFetch(
    (async () => ({ ok: false, status: 500, json: async () => ({}) })) as unknown as typeof fetch,
    () => callTool("run_specialist", { agent: "job_hunter" }),
  );

  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /HTTP 500/);
});

test("an unreachable sidecar explains itself", async () => {
  const result = await withFetch(
    (async () => {
      throw new Error("connect ECONNREFUSED");
    }) as unknown as typeof fetch,
    () => callTool("run_specialist", {}),
  );

  assert.equal(result.ok, false);
  const message = result.ok === false ? result.error : "";
  assert.match(message, /unreachable/);
  assert.match(message, /FastAPI/, "points at the service that must be running");
});

test("the timeout override is honoured, and a timeout refuses to imply an outcome", async () => {
  // Never resolves on its own — only the AbortController can settle it.
  const hangingFetch = ((_url: unknown, init: { signal?: AbortSignal }) =>
    new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    })) as unknown as typeof fetch;

  const started = Date.now();
  const result = await withFetch(hangingFetch, () =>
    callTool("run_specialist", { agent: "goal_decomposer" }, { timeoutMs: 50 }),
  );
  const elapsed = Date.now() - started;

  // If the override were ignored this would have waited the 15s read default.
  assert.ok(elapsed < 5_000, `expected the 50ms override to abort quickly, took ${elapsed}ms`);

  assert.equal(result.ok, false);
  const message = result.ok === false ? result.error : "";
  assert.match(message, /did not finish/);
  // A cut-off run may still have written the vault — the model must not conclude.
  assert.match(message, /do not assume it failed or succeeded/);
});

/* -------------------------------------------------------------------------- */
/* Contract drift — declared but not registered                               */
/* -------------------------------------------------------------------------- */

test("every tool the sidecar declares is registered by an extension", () => {
  // Source-level on purpose: importing the extensions would need `typebox`, and
  // the registration name is the thing that actually breaks.
  const dir = new URL("../extensions/", import.meta.url);
  const sources = readdirSync(dir)
    .filter((name) => name.endsWith(".ts"))
    .map((name) => readFileSync(new URL(name, dir), "utf8"))
    .join("\n");

  const missing = MENTOR_TOOLS.map((tool) => tool.name).filter(
    (name) => !sources.includes(`name: "${name}"`),
  );

  assert.deepEqual(
    missing,
    [],
    `declared by the sidecar but not registered: ${missing.join(", ")}. ` +
      "Wire them in mentor/extensions/ or remove them from api/tools.py.",
  );
});
