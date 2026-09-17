/**
 * Tests for the memory layer: the store's guarantees, and the cursor.
 *
 * These are not smoke tests. Each one pins a claim the design makes in
 * `MEMORY.md`, and most exist because the naive implementation would pass
 * everything else and still corrupt memory:
 *
 *   - the confidence ceilings are CODE, not prompt (a model told "cap inferred
 *     memories at 0.60" will eventually write 0.9)
 *   - a memory without a quote is rejected (an unquotable memory is invented)
 *   - append-only really is append-only (superseding never rewrites a row)
 *   - the cursor never re-reads, and never skips
 *   - the session-dir encoding matches the PI harness exactly (getting it wrong
 *     reads zero sessions, silently)
 *
 * Run:
 *   node --test mentor/test/memory.test.ts
 */

import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  applyCeiling,
  appendAttention,
  appendMemory,
  appendRequest,
  clearRequests,
  memoryPaths,
  profileFactsFlat,
  readJsonl,
  readMemories,
  readRequests,
  supersedeMemory,
  writeProfileFact,
} from "../src/memory/store.ts";
import { pendingTranscript, readCursor, sessionDirFor, writeCursor } from "../src/memory/transcript.ts";

/** A throwaway repo root. Nothing here touches the real data/ directory. */
function tempRoot(): string {
  return mkdtempSync(join(tmpdir(), "mentor-memory-"));
}

// ---------------------------------------------------------------------------
// The confidence lifecycle — code-owned arithmetic
// ---------------------------------------------------------------------------

test("an inference cannot claim more than its ceiling, whatever it asks for", () => {
  // The whole point: enforced here, so a confident model cannot talk past it.
  const high = applyCeiling("mentor_inferred", 0.99);
  assert.equal(high.confidence, 0.6);
  assert.equal(high.clamped, true);

  assert.equal(applyCeiling("user_stated", 1.0).confidence, 1.0);
  assert.equal(applyCeiling("user_stated", 0.95).clamped, false);
});

test("an unknown source is treated as the weakest, not the strongest", () => {
  // An unrecognised source is a bug, and the safe reading of a bug is
  // "we do not know this" — so it must never inherit a trusted ceiling.
  const result = applyCeiling("totally_made_up", 0.99);
  assert.equal(result.confidence, 0.6);
  assert.equal(result.clamped, true);
});

// ---------------------------------------------------------------------------
// The write rules
// ---------------------------------------------------------------------------

test("a memory with no quote is rejected — an unquotable memory is invented", () => {
  const root = tempRoot();
  const result = appendMemory(root, {
    content: "He seems to avoid system design.",
    source: "mentor_inferred",
  });
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /quote/i);
  assert.equal(readMemories(root).length, 0);
});

test("a memory with an unknown source is rejected", () => {
  const root = tempRoot();
  const result = appendMemory(root, { content: "x", source: "i_think_so", quote: "x" });
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.error : "", /source/i);
});

test("a valid memory is written with its provenance intact", () => {
  const root = tempRoot();
  const result = appendMemory(root, {
    content: "He did DSA in C++ but his Python is rusty.",
    source: "user_stated",
    quote: "I did DSA in C++ but my Python is rusty",
    memory_type: "fact",
    tags: ["background"],
  });
  assert.equal(result.ok, true);

  const [memory] = readMemories(root);
  assert.equal(memory.source, "user_stated");
  assert.equal(memory.confidence, 0.95);
  assert.equal(memory.user_confirmed, true);
  assert.equal(memory.quote, "I did DSA in C++ but my Python is rusty");
  assert.deepEqual(memory.tags, ["background"]);
});

test("an over-confident confidence is clamped, and the clamp is reported", () => {
  const root = tempRoot();
  const result = appendMemory(root, {
    content: "He probably prefers mornings.",
    source: "mentor_inferred",
    quote: "I tend to work in the morning I guess",
    confidence: 0.95,
  });
  assert.equal(result.ok, true);
  assert.equal(result.ok === true ? result.confidence : 0, 0.6);
  assert.equal(result.ok === true ? result.clampedFrom : undefined, 0.95);
});

// ---------------------------------------------------------------------------
// Append-only
// ---------------------------------------------------------------------------

test("superseding never rewrites the old row — the file only grows", () => {
  const root = tempRoot();
  const first = appendMemory(root, {
    content: "His Python is rusty.",
    source: "mentor_inferred",
    quote: "python's a bit shaky for me",
  });
  assert.equal(first.ok, true);

  const before = readJsonl(memoryPaths(root).memories).length;
  supersedeMemory(root, first.ok ? first.id : "", {
    content: "His Python is fine; he is rusty on idioms.",
    source: "user_stated",
    quote: "I know Python, it's the idioms I forget",
  });
  const after = readJsonl(memoryPaths(root).memories);

  assert.equal(after.length, before + 2, "one new memory plus one pointer row");

  // The original row is still there, with its original confidence. A fresh
  // inference starts at the source's FLOOR (0.40), not its ceiling — it has to
  // earn its way up, and only confirmation can take it to 0.60.
  const original = after.find((r) => r.id === (first.ok ? first.id : ""));
  assert.equal(original?.content, "His Python is rusty.");
  assert.equal(original?.confidence, 0.4);
});

test("a superseded memory stops being returned, but stays on disk", () => {
  const root = tempRoot();
  const first = appendMemory(root, { content: "a", source: "seeded", quote: "a" });
  supersedeMemory(root, first.ok ? first.id : "", { content: "b", source: "seeded", quote: "b" });

  const live = readMemories(root);
  assert.equal(live.length, 1);
  assert.equal(live[0].content, "b");

  assert.equal(readMemories(root, { includeInactive: true }).length, 2);
});

test("a corrupted line does not make the store unreadable", () => {
  const root = tempRoot();
  appendMemory(root, { content: "real", source: "seeded", quote: "real" });
  // Simulate a crash mid-append: a half-written final line.
  const path = memoryPaths(root).memories;
  writeFileSync(path, `${readFileSync(path, "utf8")}{"id": "trunc`, "utf8");

  const memories = readMemories(root);
  assert.equal(memories.length, 1, "the good line survives the bad one");
});

// ---------------------------------------------------------------------------
// Profile, attention, requests
// ---------------------------------------------------------------------------

test("profile facts round-trip and read back flat", () => {
  const root = tempRoot();
  writeProfileFact(root, "identity", "full_name", "Nikhil");
  writeProfileFact(root, "career", "target_roles", ["AI Engineer", "ML Engineer"]);

  const flat = profileFactsFlat(root);
  assert.equal(flat["identity.full_name"], "Nikhil");
  assert.deepEqual(flat["career.target_roles"], ["AI Engineer", "ML Engineer"]);
});

test("attention items carry evidence, and a suggestion is not a command", () => {
  const root = tempRoot();
  appendAttention(root, {
    kind: "unresolved_thread",
    what: "He has raised the argmax step three times.",
    evidence: "three separate turns",
    suggestion: "Worth asking whether it is parked.",
  });

  const [item] = readJsonl(memoryPaths(root).attention);
  assert.equal(item.kind, "unresolved_thread");
  assert.equal(item.suggestion, "Worth asking whether it is parked.");
  assert.equal(item.resolved_at, null);
});

test("requests are a queue: written by the mentor, drained by the curator", () => {
  const root = tempRoot();
  appendRequest(root, "I don't want study blocks after 9pm", "affects every plan");
  appendRequest(root, "Interview on the 22nd", "time-sensitive");

  assert.equal(readRequests(root).length, 2);
  clearRequests(root);
  assert.equal(readRequests(root).length, 0);
});

// ---------------------------------------------------------------------------
// The cursor — the piece that makes a missed run safe
// ---------------------------------------------------------------------------

/** Write a fake PI session file, in the real entry shape. */
function fakeSession(root: string, name: string, messages: [string, string][]): string {
  const dir = sessionDirFor(root);
  mkdirSync(dir, { recursive: true });
  const file = join(dir, `${name}.jsonl`);
  const lines = [
    JSON.stringify({ type: "session", version: 3, id: name, timestamp: "t", cwd: root }),
    JSON.stringify({
      type: "thinking_level_change",
      id: "t1",
      parentId: null,
      timestamp: "t",
      thinkingLevel: "off",
    }),
    ...messages.map(([role, content], i) =>
      JSON.stringify({
        type: "message",
        id: `m${i}`,
        parentId: null,
        timestamp: `2026-09-17T10:0${i}:00Z`,
        message: { role, content },
      }),
    ),
  ];
  writeFileSync(file, lines.join("\n") + "\n", "utf8");
  return file;
}

test("the session-dir encoding matches the PI harness", () => {
  // If this drifts, the curator reads zero sessions and nothing is ever
  // remembered — silently, with no error anywhere.
  assert.equal(sessionDirFor("/home/nik/Desktop/AI", "/agent"), "/agent/sessions/--home-nik-Desktop-AI--");
});

test("the cursor reads forward, and never re-reads a curated range", () => {
  const root = tempRoot();
  process.env.PI_AGENT_DIR = join(root, "agent");
  fakeSession(root, "2026-09-17T10-00-00Z_a", [
    ["user", "hey"],
    ["assistant", "hello"],
  ]);

  const first = pendingTranscript(root);
  assert.equal(first.lines.length, 2, "both messages are seen the first time");

  writeCursor(root, first.next); // this is curator_advance

  assert.equal(pendingTranscript(root).lines.length, 0, "nothing is re-read after advancing");
});

test("a late run skips nothing, and a repeated run is harmless", () => {
  const root = tempRoot();
  process.env.PI_AGENT_DIR = join(root, "agent");
  fakeSession(root, "2026-09-17T10-00-00Z_a", [["user", "first"]]);

  // Never curated — then a later run still picks everything up.
  const late = pendingTranscript(root);
  assert.equal(late.lines.length, 1);
  assert.equal(late.lines[0].text, "first");

  // Nothing committed, so a second run sees it again. Idempotent by design:
  // only curator_advance moves the marker.
  assert.equal(readCursor(root).file, "");
  assert.equal(pendingTranscript(root).lines.length, 1);
});

test("messages added after a commit are picked up without re-reading the old ones", () => {
  const root = tempRoot();
  process.env.PI_AGENT_DIR = join(root, "agent");
  const file = fakeSession(root, "2026-09-17T10-00-00Z_a", [["user", "first"]]);

  writeCursor(root, pendingTranscript(root).next);

  // The session grows, as it does during a live conversation.
  const added = JSON.stringify({
    type: "message",
    id: "m9",
    parentId: null,
    timestamp: "2026-09-17T10:10:00Z",
    message: { role: "user", content: "second" },
  });
  writeFileSync(file, `${readFileSync(file, "utf8")}${added}\n`, "utf8");

  const next = pendingTranscript(root);
  assert.equal(next.lines.length, 1, "only the new message");
  assert.equal(next.lines[0].text, "second");
});

test("entries that carry no conversation are skipped", () => {
  const root = tempRoot();
  process.env.PI_AGENT_DIR = join(root, "agent");
  fakeSession(root, "2026-09-17T10-00-00Z_a", [["user", "only real message"]]);

  const { lines } = pendingTranscript(root);
  // The session header and the thinking-level change must not appear.
  assert.equal(lines.length, 1);
  assert.equal(lines[0].role, "user");
});