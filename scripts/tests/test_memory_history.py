"""
scripts/tests/test_memory_history.py

Verification suite for P3 — HISTORY (`memory/history.py`).

Covers:
 1. The facade exposes the purpose, and `history` is the module (not shadowed)
 2. A real read of live memory: the rolling thread, past sessions, daily recaps,
    and the day log
 3. Session shape: how long ago, how many turns, and something to identify it by
 4. The day log reads forwards (oldest first) — a day should not be told backwards
 5. `_ago` renders human distances, and survives a naive/aware mismatch
 6. Each source fails INDEPENDENTLY: one broken source still leaves the others
 7. A completely dead manager degrades instead of raising
 8. Rendering: header, sections, the unreadable line, and `""` when there is
    genuinely no past
 9. Read-only: the module opens no write path

Read-only against live memory; the behavioural checks run against fakes.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator.memory as memory_facade  # noqa: E402
import orchestrator.memory.history as history_mod  # noqa: E402
from orchestrator.memory.history import (  # noqa: E402
    DAY_LOG_EVENT_TYPE,
    HistoryView,
    _ago,
    read_history,
    render_history,
)

_passed = 0
_failed = 0
_failed_names: list[str] = []

NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)


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


def _session(session_id: str, hours_ago: int, turns: int, said: str = "hey") -> dict:
    ended = NOW - timedelta(hours=hours_ago)
    return {
        "id": f"row-{session_id}",
        "session_id": session_id,
        "started_at": (ended - timedelta(minutes=20)).isoformat(),
        "ended_at": ended.isoformat(),
        "turn_count": turns,
        "summary": None,
        "transcript": [
            {"role": "user", "content": said, "timestamp": ended.isoformat()},
            {"role": "mentor", "content": "hi", "timestamp": ended.isoformat()},
        ],
    }


def _event(event_type: str, content: str, hours_ago: int, tags=None, payload=None) -> dict:
    return {
        "id": f"ev-{content[:8]}",
        "occurred_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "event_type": event_type,
        "source_agent": event_type,
        "tags": tags or [event_type],
        "content": content,
        "payload": payload,
        "importance": 3,
    }


class FakeManager:
    """Just enough of MemoryManager for the four history sources."""

    def __init__(
        self,
        *,
        thread: dict | None = None,
        sessions: list[dict] | None = None,
        day_log: list[dict] | None = None,
        recaps: list[dict] | None = None,
        explode: tuple[str, ...] = (),
    ) -> None:
        self.thread = thread if thread is not None else {"summary": "", "since": None}
        self._sessions = sessions or []
        self._day_log = day_log or []
        self._recaps = recaps or []
        self.explode = set(explode)

    def load_conversation_thread(self) -> dict:
        if "thread" in self.explode:
            raise RuntimeError("thread store down")
        return self.thread

    def get_recent_conversation_sessions(self, limit: int = 3) -> list[dict]:
        if "sessions" in self.explode:
            raise RuntimeError("session store down")
        return self._sessions[:limit]

    def query_episodic(self, event_type=None, last_n=None, **_kw) -> list[dict]:
        if event_type == DAY_LOG_EVENT_TYPE:
            if "day_log" in self.explode:
                raise RuntimeError("episodic store down")
            return self._day_log[: last_n or 20]
        if event_type == "daily_summary":
            if "recaps" in self.explode:
                raise RuntimeError("recaps store down")
            return self._recaps[: last_n or 20]
        return []


class DeadManager:
    def __getattr__(self, _name):
        raise RuntimeError("memory is down")


def _populated() -> FakeManager:
    return FakeManager(
        thread={"summary": "Working through DSA revision.", "since": "2026-08-30T00:00:00+00:00"},
        sessions=[_session("s-1", 3, 4, "how do I do this"), _session("s-2", 30, 2)],
        day_log=[
            _event(DAY_LOG_EVENT_TYPE, "waking up", 8, tags=["day_log", "wake"]),
            _event(DAY_LOG_EVENT_TYPE, "start studying", 6, tags=["day_log", "start"]),
        ],
        recaps=[_event("daily_summary", "A solid day of DSA.", 30)],
    )


# ---------------------------------------------------------------------------
# 1-4. The facade and a real read
# ---------------------------------------------------------------------------


def test_facade_and_live() -> None:
    print("\n=== 1. The facade exposes the purpose ===")
    check(callable(read_history), "orchestrator.memory.read_history is callable")
    check("read_history" in memory_facade.__all__, "it is advertised in __all__")
    check(
        not callable(memory_facade.history),
        "orchestrator.memory.history is the MODULE, not a callable",
        type(memory_facade.history).__name__,
    )

    print("\n=== 2. A real read of live memory ===")
    view = read_history()
    check(isinstance(view, HistoryView), "read_history() returns a HistoryView")
    check(view.degraded == [], "nothing degraded on a healthy read", str(view.degraded))
    check(
        set(view.counts) == {"sessions", "recaps", "day_log", "has_rolling_summary"},
        "counts cover every source",
        str(view.counts),
    )
    print(
        f"      live: sessions={view.counts['sessions']} recaps={view.counts['recaps']} "
        f"day_log={view.counts['day_log']} rolling={view.counts['has_rolling_summary']}"
    )
    check(
        bool(view.sessions or view.recaps or view.day_log or view.rolling_summary),
        "at least one source of the past is populated",
    )

    print("\n=== 3. Session shape ===")
    if view.sessions:
        session = view.sessions[0]
        check(bool(session.session_id), "a session carries its id", session.session_id)
        check(session.turn_count >= 0, "and its turn count", str(session.turn_count))
        check(
            bool(session.ago or session.ended_at),
            "and a way to place it in time",
            session.ago or str(session.ended_at),
        )
        check(
            bool(session.summary or session.excerpt or not session.turn_count),
            "and something to identify it by",
            (session.summary or session.excerpt or "")[:60],
        )
    else:
        check(False, "live memory has at least one past session to inspect")

    print("\n=== 4. The day log reads forwards ===")
    stamps = [entry.occurred_at for entry in view.day_log if entry.occurred_at]
    check(stamps == sorted(stamps), "day-log entries are oldest-first", f"{len(stamps)} entries")


# ---------------------------------------------------------------------------
# 5. _ago
# ---------------------------------------------------------------------------


def test_ago() -> None:
    print("\n=== 5. _ago renders human distances ===")
    check(_ago(None, NOW) == "", "no timestamp renders as empty")
    check(_ago("not-a-date", NOW) == "", "an unparseable timestamp renders as empty")

    base = NOW.isoformat()
    check(_ago(base, NOW) == "1m ago", "just now is '1m ago'", _ago(base, NOW))
    check(
        _ago((NOW - timedelta(minutes=42)).isoformat(), NOW) == "42m ago",
        "minutes",
        _ago((NOW - timedelta(minutes=42)).isoformat(), NOW),
    )
    check(
        _ago((NOW - timedelta(hours=6)).isoformat(), NOW) == "6h ago",
        "hours",
        _ago((NOW - timedelta(hours=6)).isoformat(), NOW),
    )
    check(
        _ago((NOW - timedelta(days=3)).isoformat(), NOW) == "3 day(s) ago",
        "days",
        _ago((NOW - timedelta(days=3)).isoformat(), NOW),
    )
    check(
        _ago((NOW - timedelta(hours=40)).isoformat(), NOW) == "40h ago",
        "under 48h stays in hours",
    )

    # A naive stamp against an aware clock must not raise inside a read.
    naive = (NOW.replace(tzinfo=None) - timedelta(hours=2)).isoformat()
    check(_ago(naive, NOW) == "2h ago", "a naive/aware mix is tolerated", _ago(naive, NOW))


# ---------------------------------------------------------------------------
# 6-7. Source independence and fail-open
# ---------------------------------------------------------------------------


def test_source_independence() -> None:
    print("\n=== 6. Each source fails independently ===")
    for broken, expected_missing in (
        ("thread", "rolling_summary"),
        ("sessions", "conversation_sessions"),
        ("day_log", "day_log"),
        ("recaps", "daily_recaps"),
    ):
        view = read_history(memory_manager=_populated(), now=NOW)
        check(not view.degraded, f"baseline is healthy before breaking {broken}")

        view = read_history(
            memory_manager=FakeManager(**_kwargs_for(broken)), now=NOW
        )
        check(
            any(d.startswith(f"{expected_missing}:") for d in view.degraded),
            f"breaking {broken} names `{expected_missing}`",
            str(view.degraded),
        )
        surviving = sum(
            1 for present in (
                bool(view.rolling_summary), bool(view.sessions), bool(view.recaps), bool(view.day_log)
            ) if present
        )
        check(
            surviving >= 1,
            f"and the other sources still answer ({surviving} of 4 remain)",
        )

    print("\n=== 7. A dead manager degrades instead of raising ===")
    dead = read_history(memory_manager=DeadManager(), now=NOW)
    check(dead.is_empty(), "a manager that raises on every call yields an empty view")
    check(
        len(dead.degraded) == 4,
        "and each of the four sources names itself",
        str(dead.degraded),
    )

    # The other failure shape: the manager cannot even be constructed. That is
    # the one case where the manager itself is the reported failure.
    original = history_mod._default_memory_manager
    try:
        def _no_db():
            raise RuntimeError("no database")

        history_mod._default_memory_manager = _no_db
        unwired = read_history(now=NOW)
        check(
            any(d.startswith("memory_manager:") for d in unwired.degraded),
            "a manager that cannot be built names itself",
            str(unwired.degraded),
        )
    finally:
        history_mod._default_memory_manager = original


def _kwargs_for(broken: str) -> dict:
    """A populated fake with exactly one source broken."""
    base = {
        "thread": {"summary": "Working through DSA revision.", "since": "2026-08-30"},
        "sessions": [_session("s-1", 3, 4)],
        "day_log": [_event(DAY_LOG_EVENT_TYPE, "start studying", 6, tags=["day_log", "start"])],
        "recaps": [_event("daily_summary", "A solid day of DSA.", 30)],
        "explode": (broken,),
    }
    return base


# ---------------------------------------------------------------------------
# 8-9. Rendering and invariants
# ---------------------------------------------------------------------------


def test_rendering() -> None:
    print("\n=== 8. Rendering ===")
    check(
        render_history(HistoryView()) == "",
        "an empty past renders to '' — nothing invented",
    )

    text = render_history(read_history(memory_manager=_populated(), now=NOW))
    check("WHAT HAPPENED" in text, "the block has a header")
    check("The thread so far" in text, "the rolling summary renders")
    check("Working through DSA revision." in text, "with its content")
    check("Recent days:" in text, "daily recaps render")
    check("Recent conversations:" in text, "past sessions render")
    check("3h ago" in text, "with a human distance", "3h ago")
    check(
        "how do I do this" in text,
        "and an excerpt when the session has no summary",
    )
    check("What he said he was doing" in text, "the day log renders")
    check("Sep 17" in text, "day-log lines carry a date and clock")
    check(len(text.splitlines()) <= 20, "the block stays prompt-sized", f"{len(text.splitlines())} lines")
    print("\n" + text)

    broken = render_history(read_history(memory_manager=FakeManager(**_kwargs_for("day_log")), now=NOW))
    check("Unreadable right now" in broken, "a degraded read says so in the block")

    print("\n--- the day log is capped on render ---")
    many = FakeManager(
        day_log=[_event(DAY_LOG_EVENT_TYPE, f"thing {i}", i, tags=["day_log"]) for i in range(30)]
    )
    capped = render_history(read_history(memory_manager=many, now=NOW), day_log_lines=5)
    tail = capped.split("What he said he was doing")[-1].splitlines()
    entries = [line for line in tail if line.strip().startswith("- ")]
    check(len(entries) <= 5, "the day-log render honours its cap", f"{len(entries)} of 30")
    check("thing 0" in capped, "and keeps the most recent entries")


def test_read_only() -> None:
    print("\n=== 9. Read-only ===")
    source = (ROOT / "orchestrator/memory/history.py").read_text(encoding="utf-8")
    mutators = [
        name for name in (
            "set_profile_fact", "create_memory", "confirm_memory", "revise_memory",
            "deactivate", "add_episodic_event", "place_time_block", "set_anchor",
            "create_schedule_event", "set_day_slot_fields", "save_conversation_session",
            "save_conversation_thread", "log_conversation_turn",
        )
        if name in source
    ]
    check(not mutators, f"the module calls no mutator ({mutators})")
    check(
        "DAY_LOG_EVENT_TYPE = \"day_log\"" in source,
        "the day-log event type is a literal, not an import from api/",
    )
    check(
        "from api" not in source,
        "and the memory layer does not reach into the sidecar",
    )


def main() -> int:
    print("=" * 78)
    print("MEMORY P3 — HISTORY (what happened)")
    print("=" * 78)
    test_facade_and_live()
    test_ago()
    test_source_independence()
    test_rendering()
    test_read_only()

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