/**
 * Identity tests — what pins the mentor's voice and constitution to the PI core.
 *
 * The defect this file exists for: `mentor_agent_guidelines.md` (§1–§7) never
 * reached the PI core at all, and the voice was hand-duplicated inside
 * `extensions/mentor-identity.ts`, where it drifted from
 * `orchestrator/cognition/persona.py`.
 *
 * So these tests pin three things: the constitution is actually present in the
 * injected block, the voice is read from the content files rather than copied
 * into the extension, and a missing file degrades the block instead of crashing
 * the turn (the fail-open contract every mentor loader shares).
 *
 * PI is faked at its API boundary (`pi.on`), so this runs with no provider, no
 * network and no session.
 *
 * Run (Node 24 strips TypeScript natively, no test framework needed):
 *   node --test mentor/test/identity.test.ts
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import identityExtension from "../extensions/mentor-identity.ts";
import {
  CONSTITUTION_SECTIONS,
  IDENTITY_OPEN,
  agentGuidelines,
  buildIdentityBlock,
  coreIdentity,
  currentTimeBlock,
  guidelinesPath,
  parseSections,
  personaBlock,
  personaPath,
  readContentFile,
  resetCaches,
  userName,
  voiceRules,
} from "../src/identity.ts";

type Handler = (event: any, ctx: any) => Promise<any>;

/** Capture the handlers the extension registers, exactly as PI would. */
function mountExtension() {
  const handlers = new Map<string, Handler>();
  identityExtension({ on: (name: string, handler: Handler) => handlers.set(name, handler) } as never);
  return handlers;
}

/** Run `fn` with env overrides applied, then restore env and drop cached reads. */
function withEnv(vars: Record<string, string>, fn: () => void): void {
  const saved = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(vars)) {
    saved.set(key, process.env[key]);
    process.env[key] = value;
  }
  try {
    fn();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    resetCaches();
  }
}

/* -------------------------------------------------------------------------- */
/* The defect: the constitution has to reach the core                          */
/* -------------------------------------------------------------------------- */

test("every injected constitution section is present in the overlay", () => {
  const block = buildIdentityBlock();

  for (const number of CONSTITUTION_SECTIONS) {
    assert.ok(block.includes(`**§${number} — `), `§${number} missing from the identity overlay`);
  }
  // §1's anchor facts are the whole reason the constitution is injected.
  assert.match(block, /IIT Roorkee/);
});

test("§4 stays out of the overlay but remains extractable per agent", () => {
  const block = buildIdentityBlock();

  // Naming specialists the core cannot yet call would invite the model to claim
  // a delegation that does not exist — so §4 is deliberately not injected.
  assert.ok(!block.includes("### `job_hunter`"), "§4 leaked into the overlay");
  assert.ok(agentGuidelines("job_hunter"));
  assert.ok(agentGuidelines("goal_decomposer"));
  assert.equal(agentGuidelines("no_such_agent"), "");
});

test("the constitution reader parses all seven sections", () => {
  const sections = parseSections(readContentFile(guidelinesPath()));
  assert.deepEqual(
    sections.map((section) => section.number),
    [1, 2, 3, 4, 5, 6, 7],
  );
  assert.ok(coreIdentity());
});

/* -------------------------------------------------------------------------- */
/* No duplication: the voice is read from the file, never copied              */
/* -------------------------------------------------------------------------- */

test("the extension holds no copy of the voice text", () => {
  const source = readFileSync(new URL("../extensions/mentor-identity.ts", import.meta.url), "utf8");

  // The drift this migration removed: these strings used to live in the
  // extension and diverged from cognition/persona.py.
  assert.ok(!source.includes("YOUR VOICE:"), "voice rules were copied back into the extension");
  assert.ok(!source.includes("HARD RULES:"), "hard rules were copied back into the extension");
});

test("the persona reader agrees with the content file it reads", () => {
  const sections = parseSections(readContentFile(personaPath()));
  assert.deepEqual(
    sections.map((section) => section.number),
    [1, 2, 3],
  );

  // Byte-parity with what the Python reader (cognition/persona.py) renders from
  // the same file, `{name}` substituted: same trim, same substitution, same join.
  const voice = sections.find((section) => section.number === 1)!.body.split("{name}").join(userName());
  assert.equal(voiceRules(), voice);
  assert.equal(
    personaBlock(true),
    [voice, "", sections[1].body.split("{name}").join(userName()), "", "<examples>",
      sections[2].body.split("{name}").join(userName()), "</examples>"].join("\n"),
  );
});

test("the {name} placeholder is substituted from the same env var as Python", () => {
  withEnv({ MENTOR_USER_NAME: "Ada" }, () => {
    assert.match(voiceRules(), /first person to Ada:/);
    assert.doesNotMatch(voiceRules(), /\{name\}/);
    assert.ok(!buildIdentityBlock().includes("{name}"));
  });
});

/* -------------------------------------------------------------------------- */
/* Grounding                                                                  */
/* -------------------------------------------------------------------------- */

test("the time block names the date, the clock, and the timezone basis", () => {
  // Pinned to a fixed zone so the expectation is deterministic on any machine —
  // the point is that the block reports the USER's clock, not UTC's. At
  // 2026-09-15T23:30Z, Asia/Kolkata is already the 16th, 05:00.
  withEnv({ MENTOR_TIMEZONE: "Asia/Kolkata" }, () => {
    const block = currentTimeBlock(new Date("2026-09-15T23:30:00Z"));

    assert.match(block, /Wednesday, 2026-09-16, 05:00 \(Asia\/Kolkata\)/);
    assert.doesNotMatch(block, /2026-09-15/, "it must not leak the UTC date");
    assert.match(block, /Never convert to UTC first/);
  });
});

test("midnight renders as 00:00, never 24:00", () => {
  withEnv({ MENTOR_TIMEZONE: "Asia/Kolkata" }, () => {
    // 18:30Z is exactly midnight in IST. `hour12: false` renders this "24:00" in
    // some ICU builds — a valid bedtime and a nonsense current time.
    assert.match(currentTimeBlock(new Date("2026-09-15T18:30:00Z")), /00:00 \(Asia\/Kolkata\)/);
  });
});

test("a remaining-time question is routed to the grid, not to arithmetic", () => {
  const block = buildIdentityBlock();

  assert.match(block, /CURRENT TIME/);
  assert.match(block, /how much time is left today/i);
  assert.match(block, /get_day_grid/);
});

/* -------------------------------------------------------------------------- */
/* Wiring: append, never clobber, never double-inject                          */
/* -------------------------------------------------------------------------- */

test("the overlay is appended to the assembled prompt, not substituted for it", async () => {
  const handlers = mountExtension();
  const result = await handlers.get("before_agent_start")!(
    { prompt: "hey", systemPrompt: "BASE PROMPT" },
    {},
  );

  // guardrails.ts and the base prompt must survive: PI chains handlers, so
  // dropping the incoming prompt here would silently delete the safety layer.
  assert.match(result.systemPrompt, /^BASE PROMPT/);
  assert.ok(result.systemPrompt.includes(IDENTITY_OPEN));
  assert.ok(result.systemPrompt.includes("Operating Constitution"));
});

test("injection is idempotent", async () => {
  const handlers = mountExtension();
  const result = await handlers.get("before_agent_start")!(
    { prompt: "hey", systemPrompt: `BASE\n\n${IDENTITY_OPEN}\nalready here` },
    {},
  );

  assert.equal(result, undefined, "the overlay must not be injected twice");
});

/* -------------------------------------------------------------------------- */
/* Fail-open: a missing file shrinks the block, it never breaks the turn       */
/* -------------------------------------------------------------------------- */

test("a missing constitution and persona degrade to a partial block", async () => {
  withEnv(
    {
      MENTOR_GUIDELINES_PATH: "/nonexistent/mentor_agent_guidelines.md",
      MENTOR_PERSONA_PATH: "/nonexistent/mentor_persona.md",
    },
    () => {
      const block = buildIdentityBlock();

      assert.ok(block.startsWith(IDENTITY_OPEN), "the block must still be well formed");
      assert.ok(block.includes("you ARE the mentor"), "the PI framing must survive");
      assert.ok(block.includes("CURRENT TIME"), "time grounding must survive");
      assert.ok(!block.includes("Operating Constitution"));
      // The empty-wrapper bug: personaBlock(true) is non-empty even when its
      // source is missing, so assembly must not dress a broken read up as real.
      assert.ok(!block.includes("<examples>"), "an empty examples wrapper was injected");
    },
  );
});

test("the extension still registers and answers with both files missing", async () => {
  process.env.MENTOR_GUIDELINES_PATH = "/nonexistent/mentor_agent_guidelines.md";
  try {
    const handlers = mountExtension();
    const result = await handlers.get("before_agent_start")!(
      { prompt: "hey", systemPrompt: "BASE" },
      {},
    );
    assert.match(result.systemPrompt, /^BASE/);
    assert.ok(result.systemPrompt.includes("you ARE the mentor"));
  } finally {
    delete process.env.MENTOR_GUIDELINES_PATH;
    resetCaches();
  }
});