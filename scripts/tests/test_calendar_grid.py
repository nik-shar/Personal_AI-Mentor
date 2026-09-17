"""
scripts/tests/test_calendar_grid.py

Verification suite for the rolling 48-slot day grid calendar (Phase 1).

Covers:
  1. Slot math (clock labels, index mapping, 30-min alignment)
  2. Free-window computation on the pure layer
  3. DB round-trip: ensure schema, sleep/meal anchors via set_anchor
  4. find_available_slots candidates respect anchors
  5. place_time_block persists an event + mirrors grid slots
  6. Anchor guard: a task over a sleep slot is rejected
  7. Overlap guard: booked blocks cannot be overwritten
  8. Cross-midnight spans: an anchor that wraps writes BOTH dates

Side effects are scoped to a prefixed test title on future dates and cleaned
up afterwards — the state that matters (tables, columns) is left intact.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.harness import (
    align_to_slot,
    build_day_grid,
    compute_free_windows,
    find_available_slots,
    place_time_block,
    set_anchor,
    slot_clock,
    slot_index_for,
)
from orchestrator.memory.store import MemoryManager

_PREFIX = "calendartest_"
_passed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    flag = "✅" if ok else "❌"
    print(f"  {flag} {name}" + (f" — {detail}" if detail else ""))
    assert ok, name
    _passed += 1


def _cleanup(mm: MemoryManager, day) -> None:
    for ev in mm.get_schedule_events() or []:
        if ev.get("title", "").startswith(_PREFIX):
            mm.clear_event_slots(ev["id"])
            mm.delete_schedule_event(ev["id"])
    for idx in range(48):
        mm.set_day_slot_fields(day, idx, state="free", status="planned", event_id=None)


def main() -> None:
    print("\n[1] Slot math (pure)")
    check("slot_clock 0 -> 00:00", slot_clock(0) == "00:00")
    check("slot_clock 22 -> 11:00", slot_clock(22) == "11:00")
    check("slot_clock 47 -> 23:30", slot_clock(47) == "23:30")
    check(
        "slot_index_for 11:14 -> 22",
        slot_index_for(datetime(2026, 9, 10, 11, 14, tzinfo=UTC)) == 22,
    )
    check(
        "align_to_slot floors 14:47 -> 14:30",
        align_to_slot(datetime(2026, 9, 10, 14, 47, tzinfo=UTC)).minute == 30,
    )

    print("\n[2] Free-window computation (pure)")
    slots = [{"slot_index": i, "state": "free"} for i in range(48)]
    slots[8]["state"] = "meal"
    slots[10]["state"] = "task"
    slots[11]["state"] = "task"
    windows = compute_free_windows(slots)
    check(
        "contiguous free runs split correctly",
        [(w["start_clock"], w["end_clock"]) for w in windows]
        == [("00:00", "04:00"), ("04:30", "05:00"), ("06:00", "24:00")],
        str([(w["start_clock"], w["end_clock"]) for w in windows]),
    )

    print("\n[3] DB round-trip: schema + anchors")
    mm = MemoryManager()
    mm.ensure_schema()
    check("ensure_schema (incl. block_kind backfill)", True)

    day = (datetime.now(UTC) + timedelta(days=1)).date()
    mm.prune_old_day_slots(21)
    _cleanup(mm, day)
    mm.ensure_day_slots(day)

    r = set_anchor(mm, day, f"{day}T00:00:00+00:00", 480, state="sleep", label="sleep")
    check("sleep anchor 00:00-08:00 claims 16 slots", r["status"] == "ok" and len(r["slots"]) == 16, str(len(r["slots"])))
    r2 = set_anchor(mm, day, f"{day}T20:00:00+00:00", 30, state="meal", label="dinner")
    check("dinner anchor 20:00-20:30", r2["status"] == "ok", str(r2))

    print("\n[4] Availability respects anchors")
    grid = build_day_grid(mm, day)
    free = grid["free_windows"]
    check("sleep anchor excluded from free windows", free[0]["start_clock"] == "08:00", str(free[0]))
    check("meal anchor splits the afternoon window", any(w["start_clock"] == "20:30" for w in free))

    avail = find_available_slots(mm, day, 90)
    check("find_available_slots returns candidates", bool(avail.get("candidates")), str(avail)[:120])
    c = avail["candidates"][0]

    print("\n[5] Placement persists event + mirrors grid slots")
    p = place_time_block(mm, f"{_PREFIX}DSA deep work", f"{day}T{c['start_clock']}:00+00:00", 90)
    check("place_time_block ok", p["status"] == "ok", str(p)[:120])
    check("90m block = 3 slots", len(p.get("slots", [])) == 3, str(p.get("slots")))
    task_slots = [s for s in build_day_grid(mm, day)["slots"] if s["state"] == "task"]
    check("grid mirrors task slots with event title", bool(task_slots) and "DSA" in (task_slots[0].get("event_title") or ""), str(task_slots[:1]))

    print("\n[6] Anchor + alignment guards")
    bad = place_time_block(mm, f"{_PREFIX}fail", f"{day}T01:00:00+00:00", 60)
    check("task over sleep anchor rejected", bad["status"] == "error" and "anchor" in (bad.get("error") or ""), bad.get("error", "")[:80])
    bad2 = place_time_block(mm, f"{_PREFIX}fail2", f"{day}T10:17:00+00:00", 30)
    check("unaligned start auto-floors to 10:00", bad2["status"] == "ok" and bad2["start_clock"] == "10:00", str(bad2)[:120])

    print("\n[7] Overlap guard: booked blocks cannot be overwritten")
    # Regression: "task" slots used to be accepted as available, so a second
    # block could be placed on top of an existing one. The docstring promised
    # this check and the code did not implement it.
    already = place_time_block(mm, f"{_PREFIX}overlap", f"{day}T{c['start_clock']}:00+00:00", 90)
    check(
        "exact overlap rejected",
        already["status"] == "error" and "already holds a task block" in (already.get("error") or ""),
        already.get("error", "")[:90],
    )

    # One slot of overlap at the tail of the existing block must also be caught —
    # a partial overlap is the realistic failure ("move it 30 min later").
    tail_slot = slot_clock(slot_index_for(datetime.fromisoformat(f"{day}T{c['start_clock']}:00+00:00")) + 2)
    partial = place_time_block(mm, f"{_PREFIX}partial", f"{day}T{tail_slot}:00+00:00", 60)
    check(
        "partial overlap rejected",
        partial["status"] == "error" and "already holds a task block" in (partial.get("error") or ""),
        partial.get("error", "")[:90],
    )

    check(
        "rejection did not create an event",
        not any(
            str(ev.get("title", "")).startswith(f"{_PREFIX}overlap")
            or str(ev.get("title", "")).startswith(f"{_PREFIX}partial")
            for ev in mm.get_schedule_events() or []
        ),
    )

    # A non-overlapping block elsewhere on the same day must still be allowed —
    # the guard has to reject overlaps, not all placements.
    later = find_available_slots(mm, day, 60)
    free_after = [w for w in later.get("candidates", []) if w["start_clock"] >= "12:00"]
    if free_after:
        ok_next = place_time_block(mm, f"{_PREFIX}allowed", f"{day}T{free_after[0]['start_clock']}:00+00:00", 60)
        check("adjacent non-overlapping block still allowed", ok_next["status"] == "ok", str(ok_next)[:120])
    else:
        check("adjacent non-overlapping block still allowed", False, "no free candidate found")

    print("\n[8] Cross-midnight spans: an anchor that wraps writes BOTH dates")
    # The real case: "I sleep 10 PM to 9 AM". 22:00 + 660min = 22 slots.
    # Before this, set_anchor clamped at slot 47, claimed only the 4 slots before
    # midnight, and returned status "ok" — so the mentor told him his sleep was
    # protected while nine hours of it were not.
    night = day + timedelta(days=2)
    morning = night + timedelta(days=1)
    _cleanup(mm, night)
    _cleanup(mm, morning)

    sleep = set_anchor(mm, night, f"{night}T22:00:00+00:00", 660, state="sleep", label="sleep")
    check(
        "11h sleep anchor claims all 22 slots, not the 4 before midnight",
        sleep["status"] == "ok" and sleep["slot_count"] == 22,
        f"status={sleep.get('status')} slot_count={sleep.get('slot_count')}",
    )
    check("it reports that it spans midnight", sleep.get("spans_midnight") is True, str(sleep.get("spans_midnight")))
    check(
        "it names both dates it wrote",
        sleep.get("dates") == [night.isoformat(), morning.isoformat()],
        str(sleep.get("dates")),
    )
    check(
        "the summary states both clock windows",
        "22:00-24:00" in (sleep.get("summary") or "") and "00:00-09:00" in (sleep.get("summary") or ""),
        str(sleep.get("summary")),
    )

    # The tail must actually exist on the next date — the whole point.
    morning_slots = build_day_grid(mm, morning)["slots"]
    check(
        "next day 00:00-08:30 is sleep on the grid",
        all(morning_slots[i]["state"] == "sleep" for i in range(0, 18)),
        str({i: morning_slots[i]["state"] for i in range(0, 19)}),
    )
    check("next day 09:00 is still free", morning_slots[18]["state"] == "free", morning_slots[18]["state"])

    # And availability must respect the spill, not just the grid.
    morning_free = find_available_slots(mm, morning, 60)
    first = (morning_free.get("candidates") or [{}])[0]
    check(
        "availability on the next day starts after the anchored sleep",
        (first.get("start_clock") or "") >= "09:00",
        str(first),
    )

    # The deliberate asymmetry: anchors wrap, task placements do not.
    late = place_time_block(mm, f"{_PREFIX}late", f"{morning}T23:30:00+00:00", 60)
    check(
        "a task block still refuses to run past end of day",
        late["status"] == "error" and "past end of day" in (late.get("error") or ""),
        late.get("error", "")[:80],
    )

    # A span that fits in one day must not claim a second date.
    meal = set_anchor(mm, morning, f"{morning}T20:00:00+00:00", 30, state="meal", label="dinner")
    check(
        "a single-day anchor stays on one date",
        meal["status"] == "ok" and meal["slot_count"] == 1 and meal["spans_midnight"] is False,
        str(meal),
    )

    # `date` used to be a dead parameter the sidecar papered over: it passed a
    # value derived from start_time so the call site looked honest. It now rebases.
    rebased = set_anchor(mm, morning, f"{night}T06:00:00+00:00", 30, state="gym", label="gym")
    check("an explicit date rebases the clock onto it", rebased["status"] == "ok", str(rebased)[:100])
    after = build_day_grid(mm, morning)["slots"]
    check("rebased anchor landed on the requested date", after[12]["state"] == "gym", after[12]["state"])

    _cleanup(mm, night)
    _cleanup(mm, morning)
    _cleanup(mm, day)
    print(f"\n✅ test_calendar_grid: {_passed} checks passed.")


if __name__ == "__main__":
    main()
