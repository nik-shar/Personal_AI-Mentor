"""
orchestrator/memory/grid.py

The calendar grid's pure primitives — slot math and the timezone basis.

Why this module exists
----------------------
The 48-slot grid is per-date (`day_slots.date` + `slot_index`). Two things about
a real calendar are not:

1. **A span can wrap past midnight.** Sleep 22:00 → 09:00 is four slots today and
   eighteen tomorrow. Every writer used to clamp with
   `min(SLOTS_PER_DAY, start + need)` and then report success, so an 11-hour
   sleep anchor claimed 4 slots, silently dropped 18 (nine hours), and returned
   `status: "ok"`. `slot_span()` returns the complete `(date, slot)` mapping, so
   a caller either writes all of it or refuses — never "ok" with a remainder
   nobody was told about. This is the choke point boundary finding 12 asked for:
   the guard lives where the mapping happens, not at each caller.

2. **A slot label is a wall-clock reading, not UTC.** Write paths converted input
   to UTC (`astimezone(timezone.utc)`) while read paths printed the raw index
   label, so on a UTC+5:30 machine `"22:00"` could round-trip as `"16:30"` — and
   the same request produced two different schedules depending on whether the
   model formatted `+00:00` or `+05:30`. `wall_clock()` makes the rule total:
   **the clock fields ARE the reading**, interpreted in `config.local_tz()`. One
   timezone, one meaning. (Rejected alternative: convert aware inputs to local.
   It is the purist reading of an instant, but it makes the slot depend on the
   offset the model happened to write — the exact ambiguity being removed — and
   it silently re-times every existing test fixture. See boundary doc §13.)

Nothing here touches the database, an LLM, or a store.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from orchestrator.config import local_tz

SLOT_MINUTES = 30
SLOTS_PER_DAY = 48
MINUTES_PER_DAY = 24 * 60

# The calendar-manager's anchor vocabulary. No task may ever be placed on one of
# these slots (see harness.place_time_block's anchor guard).
ANCHOR_STATES = ("sleep", "meal", "commute", "gym")


# ---------------------------------------------------------------------------
# The timezone basis
# ---------------------------------------------------------------------------

def wall_clock(dt: datetime) -> datetime:
    """
    Read a timestamp as a wall-clock time in the grid's timezone.

    The clock fields are authoritative: `22:00+00:00` and `22:00+05:30` both mean
    "22:00 on the user's day". A naive value is simply the wall clock — and naive
    was the dangerous case, because it was pushed through `.astimezone(utc)`,
    which Python reads as *local* time, shifting the slot by the machine's offset.
    """
    return dt.replace(tzinfo=local_tz(), fold=0)


def parse_wall_clock(value: Any) -> datetime:
    """Parse an ISO-8601 string (or datetime) into a wall-clock datetime."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return wall_clock(dt)


def grid_date_of(value: Any) -> Any:
    """
    Best-effort date from a date / datetime / ISO string (None if unparseable).

    Shared by the harness and the store so a caller's `date` argument means the
    same thing on both sides. `MemoryManager._grid_date` delegates here.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Slot math
# ---------------------------------------------------------------------------

def slot_clock(slot_index: int) -> str:
    """'HH:MM' start of the 30-min slot 0..47 (00:00..23:30)."""
    i = max(0, min(int(slot_index), SLOTS_PER_DAY - 1))
    return f"{i * 30 // 60:02d}:{i * 30 % 60:02d}"


def round_duration(duration_min: Any) -> int:
    """Round a requested duration to a whole slot, minimum one slot."""
    return max(SLOT_MINUTES, round(max(1, int(duration_min)) / SLOT_MINUTES) * SLOT_MINUTES)


def slot_index_for(dt: datetime) -> int:
    """Slot index (0..47) containing a given wall-clock datetime."""
    d = dt.replace(second=0, microsecond=0)
    return (d.hour * 60 + d.minute) // SLOT_MINUTES


def time_sense(moment: datetime, day: Any) -> tuple[int, int, bool]:
    """
    (elapsed_minutes, remaining_minutes, is_today) for `day` at `moment`.

    The grid had no clock at all, so "how much time is left?" was arithmetic the
    model approximated, and windows that had already passed were still offered as
    available (docs/Repo-to-Curriculum Blueprint.md, G7). Both are code now.

    `is_today` is what keeps it honest: for any other date elapsed is 0, because
    none of that day has passed yet. Asking about tomorrow must not return
    "14h20m elapsed".
    """
    target = grid_date_of(day)
    is_today = target == moment.date()
    elapsed = (moment.hour * 60 + moment.minute) if is_today else 0
    return elapsed, max(0, MINUTES_PER_DAY - elapsed), is_today


def align_to_slot(dt: datetime) -> datetime:
    """Floor a wall-clock datetime to the 30-min slot boundary."""
    d = dt.replace(second=0, microsecond=0)
    if d.minute % SLOT_MINUTES:
        d = d.replace(minute=d.minute - (d.minute % SLOT_MINUTES))
    return d


def slot_window_for(day: Any, slot_index: int) -> tuple[datetime, datetime]:
    """(start, end) wall-clock datetimes for one 30-min slot on the given date."""
    d = day.date() if isinstance(day, datetime) else day
    start = datetime.combine(d, time.min, tzinfo=local_tz()) + timedelta(
        minutes=int(slot_index) * SLOT_MINUTES
    )
    return start, start + timedelta(minutes=SLOT_MINUTES)

def slot_span(start_dt: datetime, duration_min: Any) -> list[tuple[date, int]]:
    """
    Map a (start, duration) onto grid slots, wrapping across midnight.

    Walks slot by slot from the aligned start, so the overflow that used to be
    discarded becomes tomorrow's slots. The caller gets the whole truth:

        slot_span(datetime(2026, 9, 15, 22, 0), 660)
        -> [(date(2026, 9, 15), 44), ... (date(2026, 9, 15), 47),
            (date(2026, 9, 16), 0), ... (date(2026, 9, 16), 17)]

    Deliberately tolerant of spans longer than a day: asking for one is a caller
    bug, but silently truncating at 48 slots is the exact failure this function
    exists to stop, so it returns what was asked for and lets the caller decide.
    """
    cursor = align_to_slot(start_dt)
    span: list[tuple[date, int]] = []
    for _ in range(round_duration(duration_min) // SLOT_MINUTES):
        span.append((cursor.date(), slot_index_for(cursor)))
        cursor += timedelta(minutes=SLOT_MINUTES)
    return span


def group_span_by_date(span: list[tuple[date, int]]) -> dict[date, list[int]]:
    """Group a span into {date: [slot_index, ...]} so a caller can write per date."""
    grouped: dict[date, list[int]] = {}
    for day, idx in span:
        grouped.setdefault(day, []).append(idx)
    return grouped


def describe_span(span: list[tuple[date, int]]) -> str:
    """
    A human sentence for a span, e.g.
    '2026-09-15 22:00-24:00 and 2026-09-16 00:00-09:00'.

    Built here rather than in the prompt layer so the mentor is told exactly
    which slots were written — including the ones that cross midnight, which the
    old code never mentioned because it never wrote them.
    """
    if not span:
        return "no slots"
    parts: list[str] = []
    for day, indices in group_span_by_date(span).items():
        run_start = previous = indices[0]
        for idx in indices[1:]:
            if idx == previous + 1:
                previous = idx
                continue
            parts.append(f"{day.isoformat()} {slot_clock(run_start)}-{end_clock(previous)}")
            run_start = previous = idx
        parts.append(f"{day.isoformat()} {slot_clock(run_start)}-{end_clock(previous)}")
    return " and ".join(parts)


def end_clock(last_index: int) -> str:
    """'HH:MM' just past a slot — '24:00' when the slot is the day's last."""
    return "24:00" if last_index + 1 >= SLOTS_PER_DAY else slot_clock(last_index + 1)