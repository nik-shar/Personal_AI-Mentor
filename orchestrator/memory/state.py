"""
orchestrator/memory/state.py

P2 — PRESENT STATE. "What is true right now?"

Purpose, not table
------------------
Identity (P1) is what does not change. This is the opposite: today's plan, the
blocks already committed on the grid, the windows still free, the streak — all of
it moves hour by hour, and all of it is needed *before* the mentor says anything
about the day. A mentor that offers a window it already booked has failed at the
one job this purpose exists for.

It also closes a documented gap
-------------------------------
`docs/Repo-to-Curriculum Blueprint.md` (G7) records: *"No time sense: no `now` /
elapsed / remaining in the grid → past windows are still offered."*
`build_day_grid` returns slots and free windows and no clock at all, so "how much
time do I have left?" was arithmetic the model approximated — the same class of
guess the code-owned slot math exists to prevent. `read_state()` supplies
`elapsed_minutes` and `remaining_minutes` as code.

What this is NOT
----------------
Not the curriculum: "what can I study right now" has its own reader
(`available_topic_nodes`) because the DAG frontier is a different question from
"what is on today's calendar", and answering both here would make this a god
read. Not momentum either — that is a derived pattern (P4), not present state.

Naming, for the reader's sake: this is `orchestrator.memory.state` — P2, the
present moment. It is **not** `orchestrator/state.py`, which holds
`OrchestratorState`, the LangGraph turn state of the Python pipeline. The two are
unrelated, and the qualifier in `orchestrator.memory.state` is what tells them
apart.

Fail-open, like the rest of the package: a broken store degrades to a partial
answer that names what failed, so the mentor can still say what it knows about
the day instead of refusing to speak.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.config import local_tz, now_local, today_local
from orchestrator.memory.grid import (
    ANCHOR_STATES,
    SLOT_MINUTES,
    end_clock,
    slot_clock,
    time_sense,
)

# Profile keys that are PRESENT STATE. They were previously in the sidecar's
# `DEFAULT_PROFILE_KEYS` alongside identity keys; splitting them is the reason
# this module exists (see identity.py's scope note).
STATE_PROFILE_KEYS: tuple[str, ...] = ("todays_plan", "learning_streak_days")


class CommittedBlock(BaseModel):
    """One uninterrupted run on the grid — an anchor, a task, or something else."""

    kind: str = Field(..., description="'anchor' | 'task' | 'other'")
    label: str
    start_clock: str
    end_clock: str
    minutes: int
    slots: list[int] = Field(default_factory=list)
    status: str | None = None
    event_id: str | None = None

    def describe(self) -> str:
        """`10:00-11:30 Deep work (task)` — the format the calendar renders."""
        return f"{self.start_clock}-{self.end_clock} {self.label} ({self.kind})"


class FreeWindow(BaseModel):
    """A contiguous run of free slots, computed by code."""

    start_clock: str
    end_clock: str
    minutes: int
    start_slot: int
    slots: list[int] = Field(default_factory=list)


class PresentState(BaseModel):
    """P2 — the answer to "what is true right now?".

    `elapsed_minutes` / `remaining_minutes` are the time sense the grid never had.
    `degraded` names any source that failed, so a partial answer is visibly
    partial rather than quietly thin.
    """

    date: str
    weekday: str
    now_clock: str
    timezone: str
    elapsed_minutes: int
    remaining_minutes: int
    committed: list[CommittedBlock] = Field(default_factory=list)
    free_windows: list[FreeWindow] = Field(default_factory=list)
    free_minutes: int = 0
    plan: dict[str, Any] | None = None
    streak_days: int | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.committed or self.free_windows or self.plan or self.streak_days)


# ---------------------------------------------------------------------------
# Grid interpretation — code owns what "free" and "committed" mean
# ---------------------------------------------------------------------------


def _is_free(state: Any) -> bool:
    """A slot nobody has claimed (mirrors harness's FREE_STATES)."""
    return (state or "").strip().lower() in ("", "free")


def _kind(state: Any) -> str:
    s = (state or "").strip().lower()
    if s in ANCHOR_STATES:
        return "anchor"
    if s == "task":
        return "task"
    return "other"


def _label(slot: dict[str, Any]) -> str:
    """Prefer the display label, then the event title, then the raw state."""
    for key in ("label", "event_title", "state"):
        value = slot.get(key)
        if value not in (None, ""):
            return str(value)
    return "busy"


def _blocks_from_slots(slots: list[dict[str, Any]]) -> list[CommittedBlock]:
    """Group contiguous occupied slots into committed blocks.

    A run breaks when the state OR the event changes: two adjacent tasks with
    different titles are two blocks, not one, because the mentor says them out
    loud differently.
    """
    ordered = sorted(slots, key=lambda s: int(s.get("slot_index", 0)))
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def _continues(previous: dict[str, Any], nxt: dict[str, Any]) -> bool:
        return (
            int(previous.get("slot_index", 0)) + 1 == int(nxt.get("slot_index", 0))
            and str(previous.get("state") or "") == str(nxt.get("state") or "")
            and str(previous.get("event_id") or "") == str(nxt.get("event_id") or "")
        )

    for slot in ordered:
        if _is_free(slot.get("state")):
            if current:
                runs.append(current)
                current = []
            continue
        if current and not _continues(current[-1], slot):
            runs.append(current)
            current = []
        current.append(slot)
    if current:
        runs.append(current)

    blocks: list[CommittedBlock] = []
    for run in runs:
        first, last = run[0], run[-1]
        start_idx = int(first.get("slot_index", 0))
        blocks.append(
            CommittedBlock(
                kind=_kind(first.get("state")),
                label=_label(first),
                start_clock=str(first.get("clock") or slot_clock(start_idx)),
                end_clock=end_clock(int(last.get("slot_index", 0))),
                minutes=len(run) * SLOT_MINUTES,
                slots=[int(s.get("slot_index", 0)) for s in run],
                status=first.get("status"),
                event_id=first.get("event_id"),
            )
        )
    return blocks


def _windows_from_grid(raw: list[dict[str, Any]]) -> list[FreeWindow]:
    """Coerce the harness's free windows; skip a malformed one loudly."""
    windows: list[FreeWindow] = []
    for entry in raw or []:
        try:
            windows.append(
                FreeWindow(
                    start_clock=str(entry.get("start_clock") or ""),
                    end_clock=str(entry.get("end_clock") or ""),
                    minutes=int(entry.get("duration_min") or 0),
                    start_slot=int(entry.get("start_slot") or 0),
                    slots=[int(i) for i in (entry.get("slots") or [])],
                )
            )
        except Exception as exc:
            print(f"[memory.state] skipping a malformed free window ({exc})")
    return windows


def _default_memory_manager() -> Any:
    from orchestrator.memory.store import MemoryManager

    return MemoryManager()


def _tz_name() -> str:
    tz = local_tz()
    return str(getattr(tz, "key", None) or tz)


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


def read_state(
    *,
    memory_manager: Any | None = None,
    day: Any | None = None,
    now: datetime | None = None,
) -> PresentState:
    """Read P2: the day as it stands, with code-computed time sense.

    `day` and `now` are injectable so the read can be pinned to a fixed moment in
    a test — a present-state read that cannot be told what "now" is would be
    untestable by construction.

    Stores are injectable for the same reason, and so a caller can pass a
    session-scoped instance.
    """
    moment = now or now_local()
    target = day or today_local()
    # One implementation of time sense, living in the module that owns slot math.
    # The grid tool and this read must never disagree about how much of the day
    # is gone — the whole point is that it is computed, not estimated twice.
    elapsed_minutes, remaining_minutes, _is_today = time_sense(moment, target)

    snapshot = PresentState(
        date=str(target),
        weekday=moment.strftime("%A"),
        now_clock=moment.strftime("%H:%M"),
        timezone=_tz_name(),
        elapsed_minutes=elapsed_minutes,
        remaining_minutes=remaining_minutes,
    )

    manager: Any | None = None
    try:
        manager = memory_manager if memory_manager is not None else _default_memory_manager()
    except Exception as exc:
        snapshot.degraded.append(f"memory_manager: {exc}")

    if manager is not None:
        # --- What is committed, and what is still free (the grid) ---------
        try:
            from orchestrator.harness import build_day_grid

            grid = build_day_grid(manager, target, now=moment)
            snapshot.committed = _blocks_from_slots(grid.get("slots") or [])
            snapshot.free_windows = _windows_from_grid(grid.get("free_windows") or [])
            snapshot.free_minutes = sum(window.minutes for window in snapshot.free_windows)
            # `build_day_grid` is fail-open by design, so an unreadable grid and
            # an empty day look identical in the payload. It reports which is
            # which; without this a store outage would be rendered as the false
            # claim "the day is not yet planned".
            for problem in grid.get("errors") or []:
                snapshot.degraded.append(f"day_grid: {problem}")
        except Exception as exc:
            snapshot.degraded.append(f"day_grid: {exc}")

        # --- What is stored: today's plan and the streak ------------------
        try:
            facts = manager.load_profile_facts(list(STATE_PROFILE_KEYS)) or {}
            plan = facts.get("todays_plan")
            snapshot.plan = plan if isinstance(plan, dict) else None
            streak = facts.get("learning_streak_days")
            snapshot.streak_days = int(streak) if isinstance(streak, (int, float)) else None
        except Exception as exc:
            snapshot.degraded.append(f"profile_facts: {exc}")

    snapshot.counts = {
        "committed_blocks": len(snapshot.committed),
        "anchors": sum(1 for block in snapshot.committed if block.kind == "anchor"),
        "tasks": sum(1 for block in snapshot.committed if block.kind == "task"),
        "free_windows": len(snapshot.free_windows),
        "free_minutes": snapshot.free_minutes,
        "plan_items": len((snapshot.plan or {}).get("items") or []),
    }
    return snapshot


# ---------------------------------------------------------------------------
# Rendering — the compact prompt block
# ---------------------------------------------------------------------------


def _duration(minutes: int) -> str:
    """`9h40m` / `45m` — the shape the calendar already speaks."""
    minutes = max(0, int(minutes))
    if minutes >= 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours}h{rest:02d}m" if rest else f"{hours}h"
    return f"{minutes}m"


def render_state(snapshot: PresentState) -> str:
    """Compact text for a prompt.

    Always renders something: the computed time sense is present state even on an
    empty day, and "nothing is planned yet" is an answer, not silence.
    """
    lines = [
        f"NOW — {snapshot.weekday} {snapshot.date}, {snapshot.now_clock} "
        f"({snapshot.timezone}): {_duration(snapshot.elapsed_minutes)} elapsed, "
        f"{_duration(snapshot.remaining_minutes)} left in the day."
    ]

    if snapshot.free_windows:
        shown = ", ".join(
            f"{w.start_clock}-{w.end_clock} ({_duration(w.minutes)})" for w in snapshot.free_windows[:6]
        )
        lines.append(f"Still free: {shown} — {_duration(snapshot.free_minutes)} in total.")
    else:
        lines.append(
            "Still free: nothing on the grid — the day is either fully committed or not yet planned."
        )

    if snapshot.committed:
        lines.append("Committed:")
        for block in snapshot.committed:
            lines.append(f"- {block.describe()}")

    items = (snapshot.plan or {}).get("items") or []
    if items:
        planned = sum(int(item.get("duration_min") or 0) for item in items)
        lines.append(f"Today's plan: {len(items)} item(s), {_duration(planned)} planned.")
    elif snapshot.plan:
        lines.append("Today's plan: saved, with no items on it.")
    else:
        lines.append("Today's plan: nothing saved for today yet.")

    if snapshot.streak_days is not None:
        lines.append(f"Learning streak: {snapshot.streak_days} day(s).")

    if snapshot.degraded:
        lines.append("Unreadable right now: " + "; ".join(snapshot.degraded))

    return "\n".join(lines)
