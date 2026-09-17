"""
scripts/tests/test_memory_state.py

Verification suite for P2 — PRESENT STATE (`memory/state.py`).

Covers:
 1. The facade exposes the purpose, and `state` is the module (not shadowed)
 2. Time sense: `elapsed` + `remaining` are CODE, computed from an injected
    `now`, and always add up to the length of the day
 3. Committed blocks: contiguous occupied slots group into runs; anchors and
    tasks are classified; a run breaks when the event changes
 4. Free windows are taken from the code-computed grid, with the totals summed
 5. The stored half: today's plan and the streak come from profile facts
 6. Deterministic at a fixed moment — two calls agree
 7. Fail-open: a broken store degrades and NAMES the failure, never raises
 8. Rendering always says something, including on an empty day
 9. The purpose split holds — P2 and P1 do not share a profile key
10. Read-only: the module opens no write path

Read-only against live memory. The behavioural checks run against fakes so they
do not depend on what happens to be scheduled today.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator.memory as memory_facade  # noqa: E402
from orchestrator.memory.identity import IDENTITY_PROFILE_KEYS  # noqa: E402
from orchestrator.memory.state import (  # noqa: E402
    STATE_PROFILE_KEYS,
    PresentState,
    read_state,
    render_state,
)

_passed = 0
_failed = 0
_failed_names: list[str] = []

DAY = "2026-09-18"
# 14:20 on 2026-09-18 (a Friday): 860 minutes elapsed, 580 left.
MOMENT = datetime(2026, 9, 18, 14, 20)


def check(ok: bool, name: str, detail: str = "") -> None:
    """`check(condition, label)` — the shape the other memory suites use."""
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1
        _failed_names.append(name)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _clock(index: int) -> str:
    return f"{index // 2:02d}:{'30' if index % 2 else '00'}"


def _slots(overrides: dict[int, dict] | None = None) -> list[dict]:
    """A full 48-slot day, free by default, with `overrides` applied per index."""
    overrides = overrides or {}
    out: list[dict] = []
    for index in range(48):
        slot = {
            "slot_index": index,
            "state": "free",
            "clock": _clock(index),
            "status": "planned",
            "event_id": None,
            "date": DAY,
        }
        slot.update(overrides.get(index, {}))
        out.append(slot)
    return out


class FakeManager:
    """Just enough of MemoryManager for build_day_grid + the profile read."""

    def __init__(self, slots: list[dict] | None = None, facts: dict | None = None) -> None:
        self._slots = slots if slots is not None else _slots()
        self._facts = facts or {}

    def get_day_slots(self, _day) -> list[dict]:
        return list(self._slots)

    def get_schedule_events(self, start_date=None, end_date=None) -> list[dict]:
        return []

    def load_profile_facts(self, keys=None) -> dict:
        if keys is None:
            return dict(self._facts)
        return {k: v for k, v in self._facts.items() if k in set(keys)}


class BrokenManager:
    def get_day_slots(self, _day):
        raise RuntimeError("grid store down")

    def get_schedule_events(self, **_kw):
        raise RuntimeError("grid store down")

    def load_profile_facts(self, keys=None):
        raise RuntimeError("profile store down")


def _day_with_commitments() -> list[dict]:
    """Sleep 00:00-09:00, a task 18:00-19:30, sleep 22:00-24:00."""
    overrides: dict[int, dict] = {}
    for index in range(0, 18):
        overrides[index] = {"state": "sleep", "label": "sleep"}
    for index in (36, 37, 38):
        overrides[index] = {"state": "task", "event_id": "ev-1", "event_title": "DSA deep work"}
    for index in (44, 45, 46, 47):
        overrides[index] = {"state": "sleep", "label": "sleep"}
    return _slots(overrides)


# ---------------------------------------------------------------------------
# 1. The facade
# ---------------------------------------------------------------------------


def test_facade_surface() -> None:
    print("\n=== 1. The facade exposes the purpose ===")
    check(callable(read_state), "orchestrator.memory.read_state is callable")
    check("read_state" in memory_facade.__all__, "it is advertised in __all__")
    check(
        not callable(memory_facade.state),
        "orchestrator.memory.state is the MODULE, not a callable",
        type(memory_facade.state).__name__,
    )
    check(
        isinstance(STATE_PROFILE_KEYS, tuple) and bool(STATE_PROFILE_KEYS),
        "state profile keys are declared",
        str(STATE_PROFILE_KEYS),
    )


# ---------------------------------------------------------------------------
# 2 + 6. Time sense — the gap this purpose closes
# ---------------------------------------------------------------------------


def test_time_sense() -> None:
    print("\n=== 2. Time sense is code, not estimation ===")
    snap = read_state(memory_manager=FakeManager(), day=DAY, now=MOMENT)
    check(snap.elapsed_minutes == 860, "elapsed minutes are computed", f"{snap.elapsed_minutes}")
    check(snap.remaining_minutes == 580, "remaining minutes are computed", f"{snap.remaining_minutes}")
    check(
        snap.elapsed_minutes + snap.remaining_minutes == 24 * 60,
        "elapsed + remaining always span the whole day",
    )
    check(snap.now_clock == "14:20", "the clock reading is recorded", snap.now_clock)
    check(snap.weekday == "Friday", "the weekday is derived from the moment", snap.weekday)
    check(bool(snap.timezone), "the timezone basis is named", snap.timezone)

    print("\n=== 6. Deterministic at a fixed moment ===")
    again = read_state(memory_manager=FakeManager(), day=DAY, now=MOMENT)
    check(
        (again.elapsed_minutes, again.remaining_minutes, again.now_clock)
        == (snap.elapsed_minutes, snap.remaining_minutes, snap.now_clock),
        "two calls at the same moment agree exactly",
    )


# ---------------------------------------------------------------------------
# 3-5. The day
# ---------------------------------------------------------------------------


def test_committed_and_free() -> None:
    print("\n=== 3. Committed blocks ===")
    snap = read_state(memory_manager=FakeManager(_day_with_commitments()), day=DAY, now=MOMENT)
    kinds = [(b.kind, b.start_clock, b.end_clock) for b in snap.committed]
    check(len(snap.committed) == 3, "three runs: sleep, task, sleep", str(kinds))
    check(
        ("anchor", "00:00", "09:00") in kinds,
        "the morning sleep anchor is one block spanning 00:00-09:00",
    )
    check(("task", "18:00", "19:30") in kinds, "the task is its own block")
    check(
        ("anchor", "22:00", "24:00") in kinds,
        "the night anchor ends at 24:00 (the last slot's end, not 23:30)",
    )
    check(
        all(b.minutes == len(b.slots) * 30 for b in snap.committed),
        "each block's minutes equal its slot count",
    )
    task = next(b for b in snap.committed if b.kind == "task")
    check(task.label == "DSA deep work", "the event title wins as the label", task.label)
    check(task.event_id == "ev-1", "the event id is carried through")

    print("\n=== 4. Free windows ===")
    free = [(w.start_clock, w.end_clock, w.minutes) for w in snap.free_windows]
    check(len(snap.free_windows) == 2, "two free windows remain", str(free))
    check(("09:00", "18:00", 540) in free, "09:00-18:00 = 9h free")
    check(("19:30", "22:00", 150) in free, "19:30-22:00 = 2h30m free")
    check(
        snap.free_minutes == sum(w.minutes for w in snap.free_windows) == 690,
        "free minutes are summed from the windows",
        str(snap.free_minutes),
    )

    print("\n--- a run breaks when the event changes ---")
    adjacent = _slots({
        10: {"state": "task", "event_id": "a", "event_title": "first"},
        11: {"state": "task", "event_id": "b", "event_title": "second"},
    })
    snap2 = read_state(memory_manager=FakeManager(adjacent), day=DAY, now=MOMENT)
    labels = [b.label for b in snap2.committed]
    check(
        len(snap2.committed) == 2,
        "two adjacent tasks with different events are two blocks",
        str(labels),
    )
    check(labels == ["first", "second"], "in time order", str(labels))


def test_stored_half() -> None:
    print("\n=== 5. The stored half: plan and streak ===")
    plan = {"items": [{"title": "DSA", "duration_min": 90}, {"title": "Read", "duration_min": 60}]}
    managed = FakeManager(_slots(), {"todays_plan": plan, "learning_streak_days": 4})
    snap = read_state(memory_manager=managed, day=DAY, now=MOMENT)
    check(snap.plan is not None and len(snap.plan["items"]) == 2, "today's plan is read")
    check(snap.streak_days == 4, "the streak is read", str(snap.streak_days))
    check(snap.counts["plan_items"] == 2, "plan items are counted", str(snap.counts["plan_items"]))
    check(snap.is_empty() is False, "a day with a plan is not empty")
    check(
        set(snap.counts) == {
            "committed_blocks", "anchors", "tasks", "free_windows", "free_minutes", "plan_items",
        },
        "counts cover every section",
        str(sorted(snap.counts)),
    )


# ---------------------------------------------------------------------------
# 7-8. Fail-open and rendering
# ---------------------------------------------------------------------------


def test_fail_open() -> None:
    print("\n=== 7. Fail-open, naming what failed ===")
    broken = read_state(memory_manager=BrokenManager(), day=DAY, now=MOMENT)
    check(
        any(d.startswith("day_grid:") for d in broken.degraded),
        "a broken grid store is named",
        str(broken.degraded),
    )
    check(
        any(d.startswith("profile_facts:") for d in broken.degraded),
        "a broken profile store is named",
        str(broken.degraded),
    )
    check(broken.committed == [] and broken.plan is None, "and both halves are simply empty")
    check(
        broken.remaining_minutes == 580,
        "the clock still works — time sense survives a store outage",
        str(broken.remaining_minutes),
    )


def test_rendering() -> None:
    print("\n=== 8. Rendering ===")
    rich = read_state(memory_manager=FakeManager(_day_with_commitments()), day=DAY, now=MOMENT)
    text = render_state(rich)
    check("NOW —" in text, "the block leads with the moment")
    check("9h40m left" in text, "the computed time left is rendered")
    check("Still free:" in text, "free time is rendered")
    check("Committed:" in text, "committed blocks are rendered")
    check("00:00-09:00 sleep (anchor)" in text, "anchors render with their kind")
    check("18:00-19:30 DSA deep work (task)" in text, "tasks render with their title")
    check(len(text.splitlines()) <= 12, "the block stays prompt-sized", f"{len(text.splitlines())} lines")
    print("\n" + text)

    # A genuinely empty grid (no rows at all) — not an all-free day, which is a
    # different and very common state.
    empty = render_state(read_state(memory_manager=FakeManager(slots=[]), day=DAY, now=MOMENT))
    check(bool(empty.strip()), "an empty day still renders — silence is not an answer")
    check("not yet planned" in empty, "and says the day is unplanned")

    degraded_text = render_state(read_state(memory_manager=BrokenManager(), day=DAY, now=MOMENT))
    check("Unreadable right now" in degraded_text, "a degraded read says so in the block")


# ---------------------------------------------------------------------------
# 9-10. Invariants
# ---------------------------------------------------------------------------


def test_invariants() -> None:
    print("\n=== 9. The purpose split holds ===")
    overlap = set(STATE_PROFILE_KEYS) & set(IDENTITY_PROFILE_KEYS)
    check(not overlap, "no profile key is claimed by both P1 and P2", str(overlap))
    check(
        "todays_plan" in STATE_PROFILE_KEYS and "learning_streak_days" in STATE_PROFILE_KEYS,
        "the present-state keys live here",
    )
    check("todays_plan" not in IDENTITY_PROFILE_KEYS, "and are absent from identity")

    print("\n=== 10. Read-only ===")
    source = (ROOT / "orchestrator/memory/state.py").read_text(encoding="utf-8")
    mutators = [
        name for name in (
            "set_profile_fact", "create_memory", "confirm_memory", "revise_memory",
            "deactivate", "add_episodic_event", "place_time_block", "set_anchor",
            "create_schedule_event", "set_day_slot_fields",
        )
        if name in source
    ]
    check(not mutators, f"the module calls no mutator ({mutators})")
    check(
        isinstance(PresentState(date="x", weekday="y", now_clock="z", timezone="t",
                                elapsed_minutes=0, remaining_minutes=0), PresentState),
        "PresentState is constructible from code (not only from the reader)",
    )


def main() -> int:
    print("=" * 78)
    print("MEMORY P2 — PRESENT STATE (what is true right now)")
    print("=" * 78)
    test_facade_surface()
    test_time_sense()
    test_committed_and_free()
    test_stored_half()
    test_fail_open()
    test_rendering()
    test_invariants()

    print("\n" + "=" * 78)
    if _failed:
        print(f"RESULT: {_passed} passed, {_failed} failed")
        for name in _failed_names:
            print(f"  ❌ {name}")
        return 1
    print(f"RESULT: {_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())