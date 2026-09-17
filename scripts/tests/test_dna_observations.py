"""
scripts/tests/test_dna_observations.py

Verification suite for the weekly data-derived observation job
(dna_memory_redesign_v2.md §13.5, §15, §4.1/§4.2, §6):

  1. Cold start: < MIN_EVENTS events in the window → zero candidates, zero writes
  2. Detectors fire on a synthetic fortnight (time-of-day contrast, category
     avoidance + strength) and stay silent on thin signals (block length,
     postponement, trend all below threshold in the baseline)
  3. derive creates data_derived memories with the §4.1 lifecycle defaults
  4. Stable week → SAME refresh: confirm bumps confidence +0.05, no duplicates
  5. Shifted numbers → REFINES: old row superseded with the new figures
     (confidence inherited, §4.4); a pattern that stops firing is simply
     not refreshed — fading is owned by the weekly decay pass
  6. §15 guard: no candidate is a point-in-time metric ("today" never appears)
  7. Write-path audit (§11.2) covers the job's creates
  8. Real-query path: events=None reads schedule_events (seeded rows counted)

compare_fn / merge_fn are injected — no LLM calls, fully deterministic.
Wipes only dna_memory + dna_memory audit events + testobs_* schedule events.

Run from project root:
    uv run python scripts/tests/test_dna_observations.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.cognition.observations import (
    derive_weekly_observations,
    gather_pattern_candidates,
)
from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager

PASSED = 0
FAILED = 0

# Pinned to midday so the week-split boundary never lands on an event hour.
NOW = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def clean_slate(store: DNAMemoryStore, mm: MemoryManager) -> None:
    with store.SessionLocal() as session:
        session.execute(text("DELETE FROM dna_memory"))
        session.execute(text("DELETE FROM episodic_events WHERE source_agent = 'dna_memory'"))
        session.execute(text("DELETE FROM schedule_events WHERE category LIKE 'testobs_%'"))
        session.commit()


def _ev(day_offset: int, hour: int, category: str, status: str, duration: int = 45) -> dict:
    start = (NOW + timedelta(days=day_offset)).replace(hour=hour, minute=0)
    return {
        "title": f"{category} block",
        "category": category,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=duration)).isoformat(),
        "duration_min": duration,
        "status": status,
    }


def baseline_events() -> list[dict]:
    """
    Synthetic fortnight with known ground truth:
      morning   5/5 completed (100%)  — testobs_leetcode, 45m
      evening   1/5 completed (20%)   — testobs_reading, 45m
      afternoon 2/4 completed (50%)   — testobs_project, 90m
      overall   8/14 (57%) · prev week 4/7, curr week 4/7 (no trend)
    """
    events = []
    for d in (-13, -11, -9, -6, -4):
        events.append(_ev(d, 8, "testobs_leetcode", "completed", 45))
    for d in (-12, -10, -7, -5, -3):
        events.append(_ev(d, 19, "testobs_reading", "completed" if d == -5 else "scheduled", 45))
    for d in (-11, -8, -4, -2):
        events.append(_ev(d, 14, "testobs_project", "completed" if d in (-11, -4) else "scheduled", 90))
    return events


BASELINE_KEYS = {
    "time_of_day:morning-strong",
    "category_strength:testobs_leetcode",
    "category_avoidance:testobs_reading",
}


def main() -> int:
    store = DNAMemoryStore()
    store.ensure_schema()
    mm = MemoryManager()
    clean_slate(store, mm)

    def same(new: str, existing: str) -> str:
        return "SAME"

    def same_or_refines(new: str, existing: str) -> str:
        return "SAME" if new == existing else "REFINES"

    def take_new(new: str, existing: str) -> str:
        return new

    def active_memories():
        return store.list_memories(active_only=True, limit=100)

    def active_count():
        with store.SessionLocal() as session:
            return session.execute(
                text("SELECT COUNT(*) FROM dna_memory WHERE active")
            ).scalar()

    # ------------------------------------------------------------------
    print("\n[1] Cold start — thin window derives nothing")
    report = derive_weekly_observations(mm, store, now=NOW, events=[])
    check("no candidates below MIN_EVENTS", report["candidates"] == [])
    check("cold-start note explains", "cold start" in report.get("note", ""))
    check("zero writes", active_count() == 0)

    # ------------------------------------------------------------------
    print("\n[2] Detectors on the synthetic fortnight")
    candidates = gather_pattern_candidates(baseline_events(), NOW)
    keys = {c.key for c in candidates}
    check("exactly the three expected patterns", keys == BASELINE_KEYS, f"got {sorted(keys)}")
    tod = next(c for c in candidates if c.key.startswith("time_of_day"))
    check("time-of-day contrast rendered",
          "100% of morning" in tod.content and "20% of evening" in tod.content)
    avoid = next(c for c in candidates if "avoidance" in c.key)
    check("avoidance carries evidence counts",
          "1 of 5" in avoid.content and "possible avoidance" in avoid.content)
    check("thin signals stay silent (no trend/block-length/postponement)",
          not any(k.startswith(("weekly_trend", "block_length", "postponement")) for k in keys))

    # ------------------------------------------------------------------
    print("\n[3] derive creates data_derived memories (§4.1 defaults)")
    report = derive_weekly_observations(
        mm, store, now=NOW, events=baseline_events(),
        compare_fn=lambda new, existing: "DISTINCT")
    check("all three created", set(report["created"]) == BASELINE_KEYS,
          f"created={report['created']} confirmed={report['confirmed']} errors={report['errors']}")
    mems = active_memories()
    check("3 active memories", len(mems) == 3)
    check("data_derived lifecycle defaults (0.70 conf, 0.95 ceiling, unconfirmed)",
          all(m.source == "data_derived" and abs(m.confidence - 0.70) < 1e-6
              and m.confidence_ceiling == 0.95 and not m.user_confirmed
              and m.memory_type == "observation" for m in mems))
    check("tagged with data_derived + pattern key",
          all("data_derived" in m.tags and any(t in BASELINE_KEYS for t in m.tags) for m in mems))

    # ------------------------------------------------------------------
    print("\n[4] Stable week → SAME refresh confirms, never duplicates (§4.2)")
    report = derive_weekly_observations(
        mm, store, now=NOW, events=baseline_events(), compare_fn=same)
    check("all three confirmed, none created/revised",
          set(report["confirmed"]) == BASELINE_KEYS
          and report["created"] == [] and report["revised"] == [])
    check("still exactly 3 active memories", active_count() == 3)
    mem = next(m for m in active_memories() if "avoidance" in ",".join(m.tags))
    check("confidence +0.05 toward the 0.95 ceiling", abs(mem.confidence - 0.75) < 1e-6,
          f"conf={mem.confidence}")
    check("confirmation_count bumped", mem.confirmation_count == 2)

    # ------------------------------------------------------------------
    print("\n[5] Shifted numbers → REFINES supersedes with fresh figures")
    shifted = baseline_events()
    shifted.append(_ev(-2, 19, "testobs_reading", "scheduled", 45))  # reading now 1/6
    report = derive_weekly_observations(
        mm, store, now=NOW, events=shifted,
        compare_fn=same_or_refines, merge_fn=take_new)
    check("unchanged strength confirmed",
          report["confirmed"] == ["category_strength:testobs_leetcode"], f"{report['confirmed']}")
    check("shifted patterns revised",
          set(report["revised"]) == {"time_of_day:morning-strong",
                                     "category_avoidance:testobs_reading"},
          f"revised={report['revised']} created={report['created']}")
    check("still exactly 3 active memories (2 superseded + 2 fresh + 1 confirmed)",
          active_count() == 3)
    with store.SessionLocal() as session:
        archived = session.execute(
            text("SELECT COUNT(*) FROM dna_memory WHERE NOT active AND superseded_by IS NOT NULL")
        ).scalar()
    check("2 old rows archived with superseded_by", archived == 2, f"archived={archived}")
    new_avoid = next(m for m in active_memories()
                     if any("category_avoidance" in t for t in m.tags))
    check("revision carries the new figures", "17%" in new_avoid.content and "1 of 6" in new_avoid.content,
          new_avoid.content)
    check("revision inherits confidence (§4.4)", abs(new_avoid.confidence - 0.75) < 1e-6,
          f"conf={new_avoid.confidence}")

    # ------------------------------------------------------------------
    print("\n[6] §15 guard — patterns only, never point-in-time metrics")
    contents = [c.content for c in gather_pattern_candidates(baseline_events(), NOW)]
    check("every pattern is window-framed", all("week" in c for c in contents))
    check("no point-in-time 'today' metric", all("today" not in c.lower() for c in contents))

    # ------------------------------------------------------------------
    print("\n[7] Write-path audit (§11.2)")
    with store.SessionLocal() as session:
        ops = session.execute(
            text("SELECT COUNT(*) FROM episodic_events "
                 "WHERE source_agent = 'dna_memory' AND event_type = 'memory_op'")
        ).scalar()
    check("memory_op audit rows written for the job", ops >= 3, f"ops={ops}")

    # ------------------------------------------------------------------
    print("\n[8] Real-query path (events=None reads schedule_events)")
    seeded_ids = []
    for i in range(3):
        created = mm.create_schedule_event({
            "title": "testobs_seed block",
            "category": "testobs_seed",
            "start_time": (NOW - timedelta(days=1 + i)).replace(hour=9, minute=0),
            "duration_min": 45,
            "status": "completed",
        })
        seeded_ids.append(created["id"])
    expected = len(mm.get_schedule_events(
        start_date=NOW - timedelta(days=14), end_date=NOW))
    report = derive_weekly_observations(
        mm, store, now=NOW, compare_fn=same_or_refines, merge_fn=take_new)
    check("job queried the real schedule_events table",
          report["events"] == expected, f"{report['events']} != {expected}")
    check("no errors on the real-query path", report["errors"] == [], f"{report['errors']}")
    for event_id in seeded_ids:
        mm.delete_schedule_event(event_id)

    # ------------------------------------------------------------------
    clean_slate(store, mm)
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed\n{'=' * 60}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())


