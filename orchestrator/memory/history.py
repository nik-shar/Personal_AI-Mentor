"""
orchestrator/memory/history.py

P3 — HISTORY. "What happened?"

Purpose, not table
------------------
Identity (P1) is what does not change. Present state (P2) is what is true this
minute. History is the third thing a mentor needs and neither of the others
carries: **the narrative**. What was said last week, what he actually did with
his day, what the last few days amounted to. Without it every session starts
from zero, which is the difference between a mentor and a very good one-shot
assistant.

Four sources, one story, in the order it happened
-------------------------------------------------
    rolling summary    the long arc — the continuous thread, folded forward
    daily recaps       one narrative per day, written by the evening job
    past sessions      when we talked, for how long, and what it was about
    day log            what he actually did, in his own words, from his phone

That last one is why this read matters: the day log is the only record of his
real day as it happened. `log_day_event` has been writing it since it shipped and
nothing on the default engine could read it back.

Deliberately NOT here
---------------------
Not semantic recall — that is `recall_memories`, which asks "what relates to this
query?". This asks "what happened", and answers chronologically from
deterministic sources rather than by similarity, so the answer does not change
depending on how the question was phrased.

Fail-open, like the rest of the package: a broken source degrades to a partial
story and names itself, because a mentor should still be able to say what it
remembers of the week rather than going silent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.config import now_local

# The event type `log_day_event` writes. Kept as a literal rather than imported
# from `api/` — the memory layer must not depend on the sidecar.
DAY_LOG_EVENT_TYPE = "day_log"

# How far back each source reaches by default.
DEFAULT_SESSIONS = 4
DEFAULT_RECAPS = 3
DEFAULT_DAY_LOG = 25


class PastSession(BaseModel):
    """One finished conversation."""

    session_id: str
    started_at: str | None = None
    ended_at: str | None = None
    turn_count: int = 0
    summary: str | None = None
    ago: str = Field(default="", description="Human '2h ago' / '3 day(s) ago'.")
    excerpt: str = Field(default="", description="The opening line of the transcript.")


class DailyRecap(BaseModel):
    """One day's narrative."""

    date: str | None = None
    content: str


class DayLogEntry(BaseModel):
    """One thing he said he was doing, when he said it."""

    occurred_at: str | None = None
    content: str
    kind: str | None = None
    tags: list[str] = Field(default_factory=list)


class HistoryView(BaseModel):
    """P3 — the answer to "what happened?".

    `degraded` names any source that failed, so a partial story is visibly
    partial instead of reading as an empty past.
    """

    rolling_summary: str | None = None
    summary_since: str | None = None
    sessions: list[PastSession] = Field(default_factory=list)
    recaps: list[DailyRecap] = Field(default_factory=list)
    day_log: list[DayLogEntry] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.rolling_summary or self.sessions or self.recaps or self.day_log)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_memory_manager() -> Any:
    from orchestrator.memory.store import MemoryManager

    return MemoryManager()


def _ago(stamp: str | None, moment: datetime) -> str:
    """'25m ago' / '6h ago' / '3 day(s) ago' from an ISO timestamp.

    Written here rather than imported from `dna_context` — that module is Gen-2
    and slated for retirement, and a purpose module must not depend on it. This
    is three lines of arithmetic, not shared logic.
    """
    if not stamp:
        return ""
    try:
        then = datetime.fromisoformat(str(stamp))
    except ValueError:
        return ""
    # Tolerate one side being naive: Postgres rows and the local clock should both
    # be aware, but a mixed pair must not raise inside a read.
    if then.tzinfo is None and moment.tzinfo is not None:
        then = then.replace(tzinfo=moment.tzinfo)
    elif then.tzinfo is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=then.tzinfo)

    seconds = max(0, int((moment - then).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400} day(s) ago"


def _clock_of(stamp: str | None) -> str:
    """'Sep 17 09:30' for a day-log line."""
    if not stamp:
        return "--:--"
    try:
        return datetime.fromisoformat(str(stamp)).strftime("%b %d %H:%M")
    except ValueError:
        return str(stamp)[:16]


def _date_of(row: dict[str, Any]) -> str | None:
    stamp = row.get("occurred_at")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp)).date().isoformat()
    except ValueError:
        return str(stamp)[:10]


def _excerpt(transcript: Any) -> str:
    """The first thing he said in a session, clipped — a clue when there is no summary."""
    for turn in transcript or []:
        if isinstance(turn, dict) and turn.get("role") == "user":
            text = (turn.get("content") or "").replace("\n", " ").strip()
            if text:
                return text[:160]
    return ""


def _log_kind(row: dict[str, Any]) -> str | None:
    """The activity label a day-log entry carries, from its payload or tags."""
    payload = row.get("payload")
    if isinstance(payload, dict) and payload.get("kind"):
        return str(payload["kind"])
    for tag in row.get("tags") or []:
        if tag != DAY_LOG_EVENT_TYPE:
            return str(tag)
    return None


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


def read_history(
    *,
    memory_manager: Any | None = None,
    sessions: int = DEFAULT_SESSIONS,
    recaps: int = DEFAULT_RECAPS,
    day_log: int = DEFAULT_DAY_LOG,
    now: datetime | None = None,
) -> HistoryView:
    """Read P3: the narrative, newest-first within each source.

    Each source is read independently and reported independently, so a missing
    day log cannot hide the rolling summary — the failure mode of one try/except
    wrapped around all four.
    """
    moment = now or now_local()
    view = HistoryView()

    manager: Any | None = None
    try:
        manager = memory_manager if memory_manager is not None else _default_memory_manager()
    except Exception as exc:
        view.degraded.append(f"memory_manager: {exc}")
        return view

    # --- The continuous thread: the long arc ------------------------------
    try:
        thread = manager.load_conversation_thread() or {}
        view.rolling_summary = (thread.get("summary") or "").strip() or None
        view.summary_since = thread.get("since")
    except Exception as exc:
        view.degraded.append(f"rolling_summary: {exc}")

    # --- Past conversations ------------------------------------------------
    try:
        for row in manager.get_recent_conversation_sessions(limit=max(1, sessions)) or []:
            view.sessions.append(
                PastSession(
                    session_id=str(row.get("session_id") or ""),
                    started_at=row.get("started_at"),
                    ended_at=row.get("ended_at"),
                    turn_count=int(row.get("turn_count") or 0),
                    summary=(row.get("summary") or None),
                    ago=_ago(row.get("ended_at"), moment),
                    excerpt=_excerpt(row.get("transcript")),
                )
            )
    except Exception as exc:
        view.degraded.append(f"conversation_sessions: {exc}")

    # --- Daily recaps ------------------------------------------------------
    try:
        from orchestrator.memory.daily_summary import load_recent_daily_summaries

        # The loader is fail-open, so it reports a read failure through `errors`
        # rather than by raising. Without this, an unreadable store would render
        # as the false claim "no recaps yet".
        problems: list[str] = []
        rows = load_recent_daily_summaries(manager, n=max(1, recaps), errors=problems) or []
        for row in rows:
            view.recaps.append(
                DailyRecap(date=_date_of(row), content=str(row.get("content") or "").strip())
            )
        for problem in problems:
            view.degraded.append(f"daily_recaps: {problem}")
    except Exception as exc:
        view.degraded.append(f"daily_recaps: {exc}")

    # --- The day log: what he actually did, in his own words --------------
    try:
        rows = manager.query_episodic(event_type=DAY_LOG_EVENT_TYPE, last_n=max(1, day_log)) or []
        for row in rows:
            view.day_log.append(
                DayLogEntry(
                    occurred_at=row.get("occurred_at"),
                    content=str(row.get("content") or "").strip(),
                    kind=_log_kind(row),
                    tags=list(row.get("tags") or []),
                )
            )
        view.day_log.reverse()  # a day reads forwards, not backwards
    except Exception as exc:
        view.degraded.append(f"day_log: {exc}")

    view.counts = {
        "sessions": len(view.sessions),
        "recaps": len(view.recaps),
        "day_log": len(view.day_log),
        "has_rolling_summary": int(bool(view.rolling_summary)),
    }
    return view


# ---------------------------------------------------------------------------
# Rendering — the compact prompt block
# ---------------------------------------------------------------------------


def render_history(view: HistoryView, *, day_log_lines: int = 12) -> str:
    """Compact text for a prompt. `""` when there is no past to report."""
    if view.is_empty() and not view.degraded:
        return ""

    lines: list[str] = ["WHAT HAPPENED (from memory)"]

    if view.rolling_summary:
        since = f" since {str(view.summary_since)[:10]}" if view.summary_since else ""
        lines.append(f"- The thread so far{since}: {view.rolling_summary}")

    if view.recaps:
        lines.append("Recent days:")
        for recap in view.recaps:
            label = f" — {recap.date}" if recap.date else ""
            lines.append(f"- {recap.content}{label}")

    if view.sessions:
        lines.append("Recent conversations:")
        for session in view.sessions:
            when = session.ago or session.ended_at or "at an unknown time"
            about = session.summary or session.excerpt or "(nothing recorded about it)"
            lines.append(f"- {when}, {session.turn_count} turn(s): {about}")

    if view.day_log:
        lines.append("What he said he was doing (oldest first):")
        for entry in view.day_log[-max(1, day_log_lines):]:
            kind = f" [{entry.kind}]" if entry.kind else ""
            lines.append(f"- {_clock_of(entry.occurred_at)}{kind} {entry.content}")

    if view.degraded:
        lines.append("Unreadable right now: " + "; ".join(view.degraded))

    return "\n".join(lines)
