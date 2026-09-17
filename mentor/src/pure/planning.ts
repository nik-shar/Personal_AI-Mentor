/**
 * Pure planning math — the G3 side of the boundary.
 *
 * These are ported 1:1 from `orchestrator/harness.py` (the module docstring
 * there calls them "the source of truth"). They take arguments and return
 * values, touch no database, and hold no state, so they belong in TypeScript:
 * no HTTP hop, no process to run, testable with `node --test`.
 *
 * Names and result keys stay snake_case to match the Python contract exactly —
 * the tool contract must not change when an implementation moves sides
 * (docs/PI-Mentor Boundary.md, boundary rule 2).
 *
 * Parity note: every one of these has a mirror expectation in
 * `mentor/test/planning.test.ts`, derived from the Python behaviour.
 */

/** A plan item as the mentor LLM produces it. */
export interface PlanItem {
  title?: string;
  category?: string;
  priority?: string;
  duration_min?: number;
  linked_goal?: string | null;
  notes?: string | null;
  scheduled_time?: string | null;
}

export interface TrimPlanResult {
  items: PlanItem[];
  total_minutes: number;
  was_trimmed: boolean;
  dropped: PlanItem[];
}

export interface StreakResult {
  current_streak: number;
  new_streak: number;
  note: string;
}

/** Python `int()` truncates toward zero; JS needs Math.trunc for parity. */
function toInt(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
}

/** Format a minute count as a compact human string, e.g. 150 -> '2h 30m'. */
export function formatDuration(minutes: number): string {
  const safe = Math.max(0, toInt(minutes));
  if (safe >= 60) {
    const hours = Math.trunc(safe / 60);
    const rest = safe % 60;
    return rest ? `${hours}h ${rest}m` : `${hours}h`;
  }
  return `${safe}m`;
}

/**
 * Deterministic learning-streak arithmetic.
 *
 * Days with no learning reset the streak only when the user explicitly says so;
 * a day already logged leaves it unchanged.
 */
export function computeLearningStreak(
  currentStreak: number,
  isNoLearningDay: boolean,
  hasLoggedToday: boolean,
): StreakResult {
  const current = Math.max(0, toInt(currentStreak));

  if (isNoLearningDay) {
    return {
      current_streak: current,
      new_streak: 0,
      note: "User reported no learning today — streak reset to 0.",
    };
  }
  if (hasLoggedToday) {
    return {
      current_streak: current,
      new_streak: current,
      note: "Today was already logged — streak unchanged.",
    };
  }
  return {
    current_streak: current,
    new_streak: current + 1,
    note: "New learning logged — streak advanced by one.",
  };
}

/**
 * Drop the lowest-priority items until total duration fits the budget.
 *
 * `must` items are never dropped. Otherwise `nice-to-have` goes before
 * `should`, and breaks go last within the same priority. Ported exactly from
 * `trim_plan_to_fit` so the budget cap behaves identically on both sides.
 */
export function trimPlanToFit(items: PlanItem[], availableMinutes: number): TrimPlanResult {
  const available = Math.max(0, toInt(availableMinutes));
  const kept: PlanItem[] = items.map((item) => ({ ...item }));
  let total = kept.reduce((sum, item) => sum + toInt(item.duration_min, 0), 0);

  if (total <= available) {
    return { items: kept, total_minutes: total, was_trimmed: false, dropped: [] };
  }

  const priorityOrder: Record<string, number> = { must: 0, should: 1, "nice-to-have": 2 };
  const priorityOf = (item: PlanItem): number =>
    priorityOrder[item.priority ?? "should"] ?? priorityOrder.should;

  const droppable = kept
    .filter((item) => (item.priority ?? "should") !== "must")
    .sort((left, right) => {
      // Lower priority number sorts later: -2 (nice-to-have) before -1 (should).
      const byPriority = -priorityOf(left) - -priorityOf(right);
      if (byPriority !== 0) return byPriority;
      // Breaks go last within the same priority.
      return (left.category === "break" ? 1 : 0) - (right.category === "break" ? 1 : 0);
    });

  const dropped: PlanItem[] = [];
  for (const item of droppable) {
    if (total <= available) break;
    const index = kept.indexOf(item);
    if (index === -1) continue;
    kept.splice(index, 1);
    dropped.push(item);
    total -= toInt(item.duration_min, 0);
  }

  return { items: kept, total_minutes: total, was_trimmed: true, dropped };
}