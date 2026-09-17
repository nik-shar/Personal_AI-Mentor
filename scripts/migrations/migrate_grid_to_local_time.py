"""
scripts/migrations/migrate_grid_to_local_time.py

One-shot corrective migration for the timezone-basis change (see
docs/PI-Mentor Boundary.md §13).

What went wrong
---------------
Write paths converted input to UTC (`.astimezone(timezone.utc)`) while read paths
printed the raw slot label. On a UTC+5:30 machine a time the user thought of as
"22:00" was stored against the 16:30 slot and read back as "16:30" — and the slot
depended on whether the model formatted `+00:00` or `+05:30`.

What this script can and cannot fix
-----------------------------------
`day_slots` is a derived cache (48 rows per date, mirrored from `schedule_events`),
so it can simply be rebuilt:  `--rebuild`.

`schedule_events` are the record of what happened and cannot be rebuilt. This
script REPORTS them, and `--shift-events` offers to re-interpret each stored UTC
clock reading as the wall clock the user meant. **That is a guess**: it is right
if the model passed `+00:00` and wrong if it passed `+05:30`, and nothing in the
row records which. Read the dry run before applying.

Anchors exist only in `day_slots` — nothing re-derives them — so a rebuild loses
them. They are listed here so they can be re-set by hand (there are only ever a
handful: sleep, meals, commute, gym).

Usage
-----
    uv run python scripts/migrations/migrate_grid_to_local_time.py                 # dry run
    uv run python scripts/migrations/migrate_grid_to_local_time.py --rebuild       # rebuild the grid
    uv run python scripts/migrations/migrate_grid_to_local_time.py --shift-events  # also re-time events
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.config import local_tz, now_local  # noqa: E402
from orchestrator.memory.grid import ANCHOR_STATES  # noqa: E402
from orchestrator.memory.store import MemoryManager  # noqa: E402


def _stored_utc_reading(value) -> str:
    """The clock reading as stored, i.e. the old (wrong on non-UTC machines) view."""
    from datetime import timezone

    if not isinstance(value, str):
        value = value.isoformat()
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%H:%M")
    except Exception:
        return "?"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true", help="delete and re-derive grid rows from events")
    parser.add_argument("--shift-events", action="store_true", help="re-time events (read the dry run first)")
    args = parser.parse_args()

    tz = local_tz()
    mm = MemoryManager()
    print(f"\n[grid migration] timezone is now {tz} (UTC{now_local().strftime('%z')})")

    # ---------------------------------------------------------------- anchors
    anchors: dict[str, list[tuple[int, str]]] = {}
    dates = sorted({row for row in mm.list_day_slot_dates(21)})
    for day in dates:
        for slot in mm.get_day_slots(day) or []:
            if slot.get("state") in ANCHOR_STATES:
                anchors.setdefault(str(day), []).append((int(slot["slot_index"]), slot["state"]))
    if anchors:
        print("\nAnchors currently on the grid (a rebuild DROPS these — re-set them after):")
        for day, entries in anchors.items():
            print(f"  {day}: " + ", ".join(f"slot {i} ({s})" for i, s in entries))
    else:
        print("\nNo anchors on the grid — nothing to re-set.")

    # ----------------------------------------------------------------- events
    events = mm.get_schedule_events() or []
    print(f"\n{len(events)} schedule event(s). The clock reading each one currently carries:")
    for ev in events:
        title = (ev.get("title") or "?")[:42]
        print(f"  - {title:42s} stored clock {_stored_utc_reading(ev.get('start_time'))}  {ev.get('start_time')}")

    if not (args.rebuild or args.shift_events):
        print("\nDry run only. Nothing written. Re-run with --rebuild (and --shift-events) once the above reads right.")
        return

    if args.shift_events:
        from datetime import datetime, timezone

        from orchestrator.memory.grid import wall_clock

        shifted = 0
        for ev in events:
            start = ev.get("start_time")
            if not start:
                continue
            try:
                old = datetime.fromisoformat(str(start)).astimezone(timezone.utc)
            except Exception:
                continue
            # Read the stored UTC clock reading as the wall clock the user meant.
            new_start = wall_clock(old)
            mm.update_schedule_event(ev["id"], {"start_time": new_start})
            shifted += 1
        print(f"\nRe-timed {shifted} event(s) to the local basis.")

    if args.rebuild:
        removed = 0
        for day in dates:
            removed += mm.clear_day_slots(day)
        print(f"Cleared {removed} grid row(s).")
        for day in dates:
            mm.reflect_schedule_on_day(day)
        print("Grid re-derived from events on the local basis.")

    print("\nDone. Re-set anchors if any were listed above.\n")


if __name__ == "__main__":
    main()