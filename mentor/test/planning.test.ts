/**
 * Parity tests for the pure planning math.
 *
 * Every expectation here is derived from the Python source
 * (`orchestrator/harness.py`), because the whole point of porting these to
 * TypeScript is that behaviour does not change across the boundary. If a test
 * ever fails, the port drifted — fix the port, not the test.
 *
 * Run (Node 24 strips TypeScript natively, no test framework needed):
 *   node --test mentor/test/planning.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  computeLearningStreak,
  formatDuration,
  trimPlanToFit,
  type PlanItem,
} from "../src/pure/planning.ts";

test("formatDuration matches the Python f-string behaviour", () => {
  assert.equal(formatDuration(150), "2h 30m");
  assert.equal(formatDuration(120), "2h");
  assert.equal(formatDuration(60), "1h");
  assert.equal(formatDuration(59), "59m");
  assert.equal(formatDuration(45), "45m");
  assert.equal(formatDuration(0), "0m");
  // Python clamps negatives to 0 and truncates like int().
  assert.equal(formatDuration(-5), "0m");
  assert.equal(formatDuration(90.7), "1h 30m");
});

test("computeLearningStreak advances, holds, and resets exactly as Python does", () => {
  const advanced = computeLearningStreak(3, false, false);
  assert.equal(advanced.current_streak, 3);
  assert.equal(advanced.new_streak, 4);
  assert.match(advanced.note, /advanced/);

  const alreadyLogged = computeLearningStreak(3, false, true);
  assert.equal(alreadyLogged.new_streak, 3);
  assert.match(alreadyLogged.note, /unchanged/);

  const reset = computeLearningStreak(3, true, false);
  assert.equal(reset.new_streak, 0);
  assert.match(reset.note, /reset/);

  // Negative streaks clamp to zero before arithmetic.
  assert.equal(computeLearningStreak(-2, false, false).new_streak, 1);
});

test("trimPlanToFit leaves a plan that already fits untouched", () => {
  const items: PlanItem[] = [
    { title: "DSA", priority: "must", duration_min: 60 },
    { title: "Reading", priority: "should", duration_min: 30 },
  ];
  const result = trimPlanToFit(items, 90);

  assert.equal(result.was_trimmed, false);
  assert.equal(result.total_minutes, 90);
  assert.deepEqual(result.dropped, []);
  assert.equal(result.items.length, 2);
});

test("trimPlanToFit drops nice-to-have before should, and never drops must", () => {
  const items: PlanItem[] = [
    { title: "DSA", priority: "must", duration_min: 60 },
    { title: "Side project", priority: "nice-to-have", duration_min: 30 },
    { title: "Reading", priority: "should", duration_min: 30 },
  ];
  const result = trimPlanToFit(items, 60);

  assert.equal(result.was_trimmed, true);
  assert.equal(result.total_minutes, 60);
  assert.deepEqual(
    result.items.map((item) => item.title),
    ["DSA"],
  );
  assert.deepEqual(
    result.dropped.map((item) => item.title),
    ["Side project", "Reading"],
  );
});

test("trimPlanToFit puts breaks last within the same priority", () => {
  const items: PlanItem[] = [
    { title: "Must", priority: "must", duration_min: 30 },
    { title: "Focus", priority: "should", duration_min: 30 },
    { title: "Break", priority: "should", duration_min: 30, category: "break" },
  ];
  const result = trimPlanToFit(items, 30);

  // Both are 'should', so the non-break is sacrificed first.
  assert.deepEqual(
    result.dropped.map((item) => item.title),
    ["Focus", "Break"],
  );
  assert.deepEqual(
    result.items.map((item) => item.title),
    ["Must"],
  );
});

test("trimPlanToFit reports was_trimmed even when nothing could be dropped", () => {
  // Locks a Python quirk: `must` items are undroppable, so an over-budget plan
  // made only of `must` items stays over budget but is still marked trimmed.
  const result = trimPlanToFit([{ title: "Must", priority: "must", duration_min: 60 }], 0);

  assert.equal(result.was_trimmed, true);
  assert.equal(result.total_minutes, 60);
  assert.deepEqual(result.dropped, []);
});

test("trimPlanToFit does not mutate the caller's array", () => {
  // Python copies with `[dict(i) for i in items]`.
  const items: PlanItem[] = [
    { title: "A", priority: "nice-to-have", duration_min: 60 },
    { title: "B", priority: "must", duration_min: 60 },
  ];
  const before = items.length;

  trimPlanToFit(items, 60);

  assert.equal(items.length, before);
});