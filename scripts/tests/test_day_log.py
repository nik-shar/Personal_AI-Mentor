"""
scripts/tests/test_day_log.py

Verification suite for the day log — the *record* half of memory
(`api/tools.py::log_day_event`).

Covers:
  1. Manifest: log_day_event is declared as a write intent
  2. A first entry of the day has nothing to close
  3. A moment that starts something CLOSES the one before it — the duration rule
  4. `note` records an aside without closing anything
  5. `sleep` closes the open interval and leaves nothing open
  6. Idempotence: a re-delivered message is absorbed, not logged twice
  7. Fail-open: a bad `at`, or an empty `kind`, is a structured error
  8. A night that crosses midnight closes with the right duration
  9. His wording is stored verbatim (never normalised into a category)

Isolation: every entry is written at a far-future date and carries the
`daylogtest_` marker, so the suite can never see — or damage — a real day log.
Its own rows are deleted afterwards.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from api.tools import (
    DAY_LOG_EVENT_TYPE,
    LogDayEventRequest,
    build_manifest,
    log_day_event,
)
from orchestrator.memory.store import MemoryManager

_PREFIX = "daylogtest_"
_DAY_A = "2031-05-10"          # far future: no real row can fall in the look-back
_DAY_B = "2031-05-11"
_DAY_C = "2031-05-12"

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def _cleanup(mm: MemoryManager) -> int:
    """Delete ONLY this suite's rows, matched by its own marker."""
    with mm._session() as session:
        result = session.execute(
            text("DELETE FROM episodic_events WHERE event_type = :t AND content LIKE :p"),
            {"t": DAY_LOG_EVENT_TYPE, "p": f"%{_PREFIX}%"},
        )
        return int(result.rowcount or 0)


def _log(**kwargs) -> dict:
    return log_day_event(LogDayEventRequest(**kwargs))


def _rows(mm: MemoryManager) -> list[dict]:
    return mm.query_episodic(event_type=DAY_LOG_EVENT_TYPE, last_n=50)


def main() -> int:
    print("\n[1] Manifest declares the day log as a write intent")
    tools = {t.name: t for t in build_manifest().tools}
    check("log_day_event is in the manifest", "log_day_event" in tools)
    check(
        "declared as a write",
        bool(tools.get("log_day_event")) and tools["log_day_event"].kind == "write",
    )

    mm = MemoryManager()
    mm.ensure_schema()
    check("pre-existing test rows cleared", True, f"removed {_cleanup(mm)}")

    print("\n[2] A first entry of the day has nothing to close")
    r = _log(kind="wake", activity=f"{_PREFIX}waking up", at=f"{_DAY_A}T09:00:00")
    check("logged", r["logged"] is True, str(r.get("error") or ""))
    check("nothing was closed", r["closed"] is None)
    check("interval is open", r["open_now"] is True)
    check("clock is his wall clock", r["clock"] == "09:00", str(r["clock"]))

    print("\n[3] A moment that starts something closes the one before it")
    r = _log(kind="start", activity=f"{_PREFIX}learning", at=f"{_DAY_A}T10:00:00")
    check("logged", r["logged"] is True)
    check(
        "closed the waking interval",
        (r["closed"] or {}).get("activity") == f"{_PREFIX}waking up",
        str(r["closed"]),
    )
    check(
        "duration is code-computed (09:00 -> 10:00 = 60m)",
        (r["closed"] or {}).get("duration_min") == 60,
        str((r["closed"] or {}).get("duration_min")),
    )
    check("the message names the closed interval", "Closed" in (r["message"] or ""), r["message"])

    r = _log(kind="switch", activity=f"{_PREFIX}lunch", at=f"{_DAY_A}T12:30:00")
    check(
        "switch closes (150m) and reopens",
        (r["closed"] or {}).get("duration_min") == 150 and r["open_now"] is True,
        str(r["closed"]),
    )

    print("\n[4] `note` records an aside without closing anything")
    r = _log(
        kind="note",
        activity=f"{_PREFIX}learning",
        note=f"{_PREFIX}exhausted",
        at=f"{_DAY_A}T13:00:00",
    )
    check("note is logged", r["logged"] is True)
    check("note closes nothing", r["closed"] is None, str(r["closed"]))
    check("note keeps the session open (an aside is not a transition)", r["open_now"] is True)

    print("\n[5] `sleep` closes the session and opens the NIGHT")
    r = _log(kind="sleep", activity=f"{_PREFIX}going to sleep", at=f"{_DAY_A}T23:30:00")
    check(
        "closed the lunch interval (12:30 -> 23:30 = 660m)",
        (r["closed"] or {}).get("duration_min") == 660,
        str(r["closed"]),
    )
    check("sleep leaves the night open, so a later `wake` can close it", r["open_now"] is True)

    print("\n[6] Idempotence: a re-delivered message is absorbed, not logged twice")
    r = _log(kind="start", activity=f"{_PREFIX}learning", at=f"{_DAY_B}T10:00:00")
    check("first delivery logs", r["logged"] is True and r["duplicate"] is False)
    r2 = _log(kind="start", activity=f"{_PREFIX}learning", at=f"{_DAY_B}T10:00:20")
    check("re-delivery is reported as a duplicate", r2["duplicate"] is True, str(r2)[:120])
    check(
        "the duplicate says nothing new was recorded",
        "Already logged" in (r2["message"] or ""),
        r2["message"],
    )
    stamp = f"{_DAY_B}T10:00"
    dupes = [x for x in _rows(mm) if stamp in str((x.get("payload") or {}).get("at") or "")]
    check("exactly ONE row exists for that moment", len(dupes) == 1, f"found {len(dupes)}")

    print("\n[7] Fail-open on bad input")
    r = _log(kind="start", activity=f"{_PREFIX}x", at="not-a-date")
    check(
        "bad `at` -> structured error, no raise",
        r["logged"] is False and "invalid" in (r["error"] or ""),
        str(r.get("error"))[:90],
    )
    r = _log(kind="   ", activity=f"{_PREFIX}x")
    check(
        "empty kind -> structured error",
        r["logged"] is False and "kind" in (r["error"] or ""),
        str(r.get("error"))[:90],
    )

    print("\n[8] A night that crosses midnight closes with the right duration")
    r = _log(kind="sleep", activity=f"{_PREFIX}to bed", at=f"{_DAY_B}T23:00:00")
    check("late sleep logged", r["logged"] is True)
    r = _log(kind="wake", activity=f"{_PREFIX}up", at=f"{_DAY_C}T09:00:00")
    check(
        "23:00 -> 09:00 next day = 600 minutes",
        (r["closed"] or {}).get("duration_min") == 600,
        str(r["closed"]),
    )

    print("\n[9] His wording is stored verbatim")
    phrase = f"{_PREFIX}outreach to people"
    _log(kind="start", activity=phrase, at="2031-05-13T11:00:00")
    rows = [x for x in _rows(mm) if (x.get("payload") or {}).get("activity") == phrase]
    check("activity stored exactly as he said it", bool(rows), f"found {len(rows)}")
    check(
        "a tag is added for retrieval",
        bool(rows) and "daylogtest_outreach_to_people" in (rows[0].get("tags") or []),
        str(rows[0].get("tags")) if rows else "",
    )
    check(
        "the payload keeps the closed interval",
        bool(rows) and "closed" in (rows[0].get("payload") or {}),
    )

    print(f"\n  cleanup: removed {_cleanup(mm)} test row(s)")
    print("=" * 60)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 60)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
