"""
orchestrator/cognition/metrics.py

On-demand momentum metrics (dna_memory_redesign_v2.md §12 Phase 4).

What survives the old `mentor_user_state` table is the *math*, not the
23-column pre-computed snapshot: completion rates, streaks, and drift are
computed in code, on demand, straight from `schedule_events` — always fresh,
never stale, nothing to persist.

Pure functions only. No LLM calls, no writes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from orchestrator.config import now_local


def compute_completion_rate(events: list[dict[str, Any]]) -> float:
    """Fraction of schedule events marked completed."""
    if not events:
        return 0.0
    completed = sum(1 for e in events if e.get("status") == "completed")
    return completed / len(events)


def classify_trend(current: float, previous: float) -> str:
    """Momentum trend from this week vs last week completion rates."""
    if current > previous + 0.1:
        return "rising"
    if current < previous - 0.1:
        return "declining"
    return "stable"


def compute_streak(mm, max_days: int = 365) -> int:
    """
    Consecutive days (ending today) with ≥60% block completion.
    Days with no scheduled events are skipped, not counted as breaks.
    """
    now = now_local()
    streak = 0
    for i in range(max_days):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)
        events = mm.get_schedule_events(start_date=day_start, end_date=day_end)
        if not events:
            continue
        if compute_completion_rate(events) >= 0.6:
            streak += 1
        else:
            break
    return streak


def compute_momentum(mm) -> dict[str, Any]:
    """
    The compact momentum bundle for the reasoner's context document:
    streak + completion rates + trend. Computed fresh on every call.
    """
    now = now_local()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)

    events_today = mm.get_schedule_events(start_date=today_start, end_date=now)
    events_7d = mm.get_schedule_events(start_date=seven_days_ago, end_date=now)
    events_prev_7d = mm.get_schedule_events(start_date=fourteen_days_ago, end_date=seven_days_ago)

    rate_today = compute_completion_rate(events_today)
    rate_7d = compute_completion_rate(events_7d)
    rate_prev = compute_completion_rate(events_prev_7d)

    return {
        "streak_days": compute_streak(mm),
        "completion_rate_today": round(rate_today, 3),
        "completion_rate_7d": round(rate_7d, 3),
        "momentum_trend": classify_trend(rate_7d, rate_prev),
        "blocks_today": len(events_today),
    }


def compute_drift(
    goal_priorities: dict[str, float],
    events_7d: list[dict[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    """
    Drift between intended time allocation (goal_priorities: goal → fraction)
    and actual completed-block minutes over the last 7 days.

    Returns (drift_score 0..1, details list). On-demand only — nothing stored.
    """
    total_minutes = sum(
        e.get("duration_min", 30) for e in events_7d if e.get("status") == "completed"
    )
    if total_minutes == 0 or not goal_priorities:
        return 0.0, []

    actual_distribution: dict[str, float] = {}
    for e in events_7d:
        if e.get("status") == "completed":
            goal = e.get("linked_goal") or e.get("category", "other")
            actual_distribution[goal] = actual_distribution.get(goal, 0) + e.get("duration_min", 30)

    for k in actual_distribution:
        actual_distribution[k] /= total_minutes

    drift = 0.0
    details = []
    for goal, expected_pct in goal_priorities.items():
        actual_pct = actual_distribution.get(goal, 0.0)
        diff = abs(expected_pct - actual_pct)
        drift += diff
        if diff > 0.15:
            details.append({
                "goal": goal,
                "expected_pct": expected_pct,
                "actual_pct": round(actual_pct, 3),
                "message": (
                    f"'{goal}' expected {int(expected_pct * 100)}% of time "
                    f"but got {int(actual_pct * 100)}%."
                ),
            })

    return min(drift / 2.0, 1.0), details
