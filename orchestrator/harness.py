"""
orchestrator/harness.py

The manager harness — deterministic computation tools for the orchestrator LLM.

Design principle (from the "manager, not a pipeline" discussion):
    The orchestrator is an intelligent manager. It decides WHAT to do and
    speaks with the user. But exact arithmetic and graph facts (streaks, time
    budgets, prerequisite-unlocked topics) are CODE — deterministic and
    testable — exposed to the LLM as tools it can call via function-calling.

This module provides:
  1. Plain deterministic functions (no langchain dependency) — the source of truth.
  2. LangChain tool wrappers (`TOOLS`) that let the conversational LLM
     (Qwen3-235B via Nebius) invoke them by name.
  3. `run_tool_loop` — a tiny fail-open ReAct loop: LLM proposes tool calls →
     we execute them → results feed back → LLM writes the final answer.
  4. `assemble_situation_facts` — pulls today's schedule, streak, recent
     learning log, and unlocked topic nodes into a compact structured block
     the manager can act on without a sub-agent.

The loop is fail-open by design: if the model doesn't emit tool calls, we
return its plain text; if a tool or the tool-bound call errors, we fall back
to a plain LLM call. Nothing here can crash a turn.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from orchestrator.config import local_tz, now_local
from orchestrator.memory.grid import (
    ANCHOR_STATES,
    SLOT_MINUTES,
    SLOTS_PER_DAY,
    align_to_slot,
    describe_span,
    end_clock,
    grid_date_of,
    group_span_by_date,
    parse_wall_clock,
    round_duration,
    slot_clock,
    slot_index_for,
    slot_span,
    time_sense,
)
from orchestrator.tracing import component_span

# ---------------------------------------------------------------------------
# 1. Plain deterministic functions (pure, unit-testable)
# ---------------------------------------------------------------------------


def format_duration(minutes: int) -> str:
    """Format a minute count as a compact human string, e.g. 150 → '2h 30m'."""
    minutes = max(0, int(minutes))
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{minutes}m"


def compute_learning_streak(
    current_streak: int,
    is_no_learning_day: bool,
    has_logged_today: bool,
) -> dict[str, Any]:
    """
    Deterministic learning-streak arithmetic (the absorbed planner logic).

    Args:
        current_streak:   streak before this day (from memory).
        is_no_learning_day: True when the user explicitly said they studied nothing.
        has_logged_today: True when today's session was already logged.

    Returns:
        {current_streak, new_streak, note}
    """
    current = max(0, int(current_streak))
    if is_no_learning_day:
        new_streak = 0
        note = "User reported no learning today — streak reset to 0."
    elif has_logged_today:
        new_streak = current
        note = "Today was already logged — streak unchanged."
    else:
        new_streak = current + 1
        note = "New learning logged — streak advanced by one."
    return {
        "current_streak": current,
        "new_streak": new_streak,
        "note": note,
    }
def trim_plan_to_fit(items: list[dict], available_minutes: int) -> dict[str, Any]:
    """
    Drop the lowest-priority items until total duration fits the budget.

    Priority ordering: 'must' items are
    never dropped; then 'should' before 'nice-to-have'; breaks go last.

    Returns:
        {items, total_minutes, was_trimmed, dropped}
    """
    available = max(0, int(available_minutes))
    items = [dict(i) for i in items]
    total = sum(int(i.get("duration_min", 0)) for i in items)

    if total <= available:
        return {"items": items, "total_minutes": total, "was_trimmed": False, "dropped": []}

    priority_order = {"must": 0, "should": 1, "nice-to-have": 2}
    droppable = sorted(
        [i for i in items if i.get("priority", "should") != "must"],
        key=lambda i: (-priority_order.get(i.get("priority", "should"), 1), i.get("category") == "break"),
    )
    dropped: list[dict] = []
    for item in droppable:
        if total <= available:
            break
        items.remove(item)
        dropped.append(item)
        total -= int(item.get("duration_min", 0))

    return {
        "items": items,
        "total_minutes": total,
        "was_trimmed": True,
        "dropped": dropped,
    }


def get_available_topic_nodes(topic_graphs: list[dict]) -> list[dict]:
    """
    Return the study-ready frontier: in-progress nodes first, then unlocked
    (all prerequisites done) not-started nodes, across every topic graph.

    DAG traversal stays in code — the LLM should never guess prerequisites.
    """
    from orchestrator.memory.topic_graph import get_available_nodes, get_in_progress_nodes
    from schemas.memory import TopicGraph

    out: list[dict] = []
    for g in topic_graphs or []:
        try:
            graph = TopicGraph.model_validate(g)
        except Exception as exc:
            print(f"[harness] topic graph coercion failed: {exc}")
            continue
        for node in get_in_progress_nodes(graph):
            out.append({
                "graph_title": graph.title,
                "node_id": node.id,
                "title": node.title,
                "status": node.status or "not_started",
            })
        for node in get_available_nodes(graph):
            out.append({
                "graph_title": graph.title,
                "node_id": node.id,
                "title": node.title,
                "status": node.status or "not_started",
            })
    return out


# ---------------------------------------------------------------------------
# Calendar grid — the manager's calendar surface (code owns the slot math)
# ---------------------------------------------------------------------------

# The slot primitives AND the anchor vocabulary live in `memory/grid.py`
# (imported above and re-exported here, because callers and tests — and
# memory/daily_summary.py + memory/dna_context.py — import them from this module).


def _is_free(state: str | None) -> bool:
    return state in (None, "", "free")


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def build_day_grid(memory_manager: Any, date: Any, now: Any = None) -> dict[str, Any]:
    """
    The 48-slot grid for a date: slot states + event titles + free windows,
    **and the clock**.

    Free windows are contiguous runs of `free` slots ≥ 30 minutes — computed
    here in code so the manager never has to guess availability.

    Time sense (docs/PI-Mentor Boundary.md §13, the P4 slice): the grid used to
    carry no notion of "now", so "how much time is left?" was arithmetic the model
    approximated and windows that had already passed were still offered as
    available. Each slot is now marked `elapsed` / `current`, and the response
    carries `now_clock` / `elapsed_minutes` / `remaining_minutes`. For any date
    other than today, `is_today` is False and nothing is marked elapsed — asking
    about tomorrow must not report hours already gone.

    Fail-open, but **not silently**. Read failures are collected into `errors`
    so a caller that needs certainty can tell an empty day apart from an
    unreadable one. A present-state read must never report "nothing is planned"
    when the grid simply could not be read — that is a false claim about the
    world, not a degraded answer. The `slots` / `free_windows` shape is
    unchanged, so callers that only want the grid are unaffected.
    """
    slots: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        slots = memory_manager.get_day_slots(date)
    except Exception as exc:
        errors.append(f"get_day_slots: {exc}")
        print(f"[harness] get_day_slots failed: {exc}")

    titles: dict[str, str] = {}
    if slots:
        try:
            day = slots[0].get("date")
            from datetime import datetime as _dt
            start = _dt.fromisoformat(day).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
            for ev in memory_manager.get_schedule_events(start_date=start, end_date=end) or []:
                titles[str(ev.get("id"))] = ev.get("title") or "Task"
        except Exception as exc:
            errors.append(f"event_titles: {exc}")
            print(f"[harness] event titles failed: {exc}")

    # The clock. Marked per slot so the model can see which windows are already
    # behind it, rather than being told a past window is free.
    moment = now or now_local()
    elapsed_minutes, remaining_minutes, is_today = time_sense(moment, date)
    current_slot = slot_index_for(moment) if is_today else SLOTS_PER_DAY

    enriched: list[dict[str, Any]] = []
    for slot in slots:
        s = dict(slot)
        if s.get("event_id") and titles.get(str(s["event_id"])):
            s["event_title"] = titles[str(s["event_id"])]
        index = int(s.get("slot_index", 0))
        s["elapsed"] = bool(is_today and index < current_slot)
        s["current"] = bool(is_today and index == current_slot)
        enriched.append(s)

    return {
        "date": _iso_date(date),
        "slots": enriched,
        "free_windows": compute_free_windows(enriched),
        "errors": errors,
        "is_today": is_today,
        "now_clock": moment.strftime("%H:%M"),
        "current_slot": current_slot if is_today else None,
        "elapsed_minutes": elapsed_minutes,
        "remaining_minutes": remaining_minutes,
    }


def compute_free_windows(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contiguous free runs of ≥30 minutes from a 48-slot list."""
    windows: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []
    for slot in slots or []:
        idx = int(slot.get("slot_index", 0))
        if _is_free(slot.get("state")):
            run.append({"slot_index": idx, "clock": slot_clock(idx)})
        else:
            if run:
                windows.append(_free_window_from_run(run))
                run = []
    if run:
        windows.append(_free_window_from_run(run))
    return windows


def _free_window_from_run(run: list[dict[str, Any]]) -> dict[str, Any]:
    first = run[0]["slot_index"]
    last = run[-1]["slot_index"]
    return {
        "start_clock": slot_clock(first),
        "end_clock": end_clock(last),
        "start_slot": first,
        "end_slot": last,
        "slots": [r["slot_index"] for r in run],
        "duration_min": len(run) * SLOT_MINUTES,
    }


def find_available_slots(
    memory_manager: Any,
    date: Any,
    duration_min: int,
    energy_level: int | None = None,
) -> dict[str, Any]:
    """
    Candidate placement windows for a task on a date.

    Availability is code-computed from the day grid; the mentor only chooses
    among the returned candidates (never guesses free time).
    """
    try:
        grid = build_day_grid(memory_manager, date)
    except Exception as exc:
        return {"error": f"grid unavailable: {exc}"}

    need = max(1, round(max(1, int(duration_min)) / SLOT_MINUTES))
    candidates: list[dict[str, Any]] = []
    for window in grid.get("free_windows", []):
        run = window["slots"]
        if len(run) < need:
            continue
        for i in range(len(run) - need + 1):
            start_idx = run[i]
            end_idx = run[i + need - 1]
            candidates.append({
                "start_clock": slot_clock(start_idx),
                "end_clock": end_clock(end_idx),
                "start_slot": start_idx,
                "duration_min": need * SLOT_MINUTES,
            })

    # Stable sort morning-to-late; a high energy_level nudges earlier windows
    # (peak-focus hours) to the front.
    if energy_level is not None and int(energy_level) >= 4:
        candidates.sort(key=lambda c: (c["start_slot"] >= 18, c["start_slot"]))
    else:
        candidates.sort(key=lambda c: c["start_slot"])

    return {
        "date": _iso_date(date),
        "duration_min": need * SLOT_MINUTES,
        "slots_needed": need,
        "candidates": candidates[:8],
    }


def place_time_block(
    memory_manager: Any,
    title: str,
    start_time: Any,
    duration_min: int,
    category: str = "learning",
    priority: str = "should",
    block_kind: str = "task",
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Validate and persist one booked block into the calendar (event + grid).

    Code-owned checks (never left to the LLM):
      - 30-minute alignment and duration rounding
      - overlap with existing task blocks
      - anchor guard: a task may never be placed over a sleep/meal/commute anchor
      - slot range sanity

    On failure the error string is fed back to the LLM as a tool observation
    so it can retry with another window.
    """
    try:
        start_dt = align_to_slot(parse_wall_clock(start_time))
    except Exception as exc:
        return {"status": "error", "error": f"invalid start_time '{start_time}': {exc}"}

    duration = round_duration(duration_min)
    start_slot = slot_index_for(start_dt)
    need = duration // SLOT_MINUTES
    span = list(range(start_slot, min(SLOTS_PER_DAY, start_slot + need)))
    if len(span) < need:
        # Deliberate asymmetry with set_anchor: anchors are *windows* that
        # legitimately wrap (sleep 22:00 -> 09:00), so they spill into the next
        # date. A task *placement* at 23:45 for an hour is a request to put work
        # where the user has no day left, so it is refused rather than silently
        # extended into tomorrow. This is also a pinned invariant
        # (scripts/tests/test_calendar_grid.py section [8]).
        return {"status": "error", "error": f"block runs past end of day (slot {start_slot}, need {need})"}

    slots: list[dict[str, Any]] = []
    try:
        slots = memory_manager.get_day_slots(start_dt.date())
    except Exception as exc:
        return {"status": "error", "error": f"grid unavailable: {exc}"}

    by_idx = {int(s["slot_index"]): s for s in slots}
    for idx in span:
        slot = by_idx.get(idx, {})
        state = slot.get("state")
        if state in ANCHOR_STATES:
            detail = f" ({slot.get('label')})" if slot.get("label") else ""
            return {
                "status": "error",
                "error": (
                    f"anchor conflict: slot {slot_clock(idx)} is '{state}'{detail} — "
                    "never place a task over a life anchor."
                ),
            }
        # Overlap guard (documented in this function's docstring, previously not
        # implemented): a booked block leaves its slots in state "task" (see
        # MemoryManager.reflect_schedule_on_day), and "task" used to be accepted
        # here as available — so a second block could be placed on top of a first.
        # Occupied slots are now rejected with an actionable message, matching
        # toolkits/calendar-manager/guardrails.md ("never silently overwrite an
        # existing task block; propose a move instead").
        if state == "task":
            occupant = slot.get("event_id")
            detail = f" (event {str(occupant)[:8]})" if occupant else ""
            return {
                "status": "error",
                "error": (
                    f"slot {slot_clock(idx)} already holds a task block{detail} — "
                    "never overwrite an existing block; propose a move instead."
                ),
            }
        if state not in (None, "", "free"):
            return {"status": "error", "error": f"slot {slot_clock(idx)} is '{state}' — not available"}

    try:
        event = memory_manager.create_schedule_event({
            "title": str(title),
            "category": category or "learning",
            "start_time": start_dt,
            "duration_min": duration,
            "priority": priority or "should",
            "block_kind": block_kind,
            "status": "scheduled",
            "notes": notes,
        })
    except Exception as exc:
        return {"status": "error", "error": f"event create failed: {exc}"}

    return {
        "status": "ok",
        "event": event,
        "slots": [slot_clock(i) for i in span],
        "start_clock": slot_clock(start_slot),
        "end_clock": end_clock(start_slot + need - 1),
    }


def set_anchor(
    memory_manager: Any,
    date: Any,
    start_time: Any,
    duration_min: int,
    state: str = "meal",
    label: str | None = None,
) -> dict[str, Any]:
    """
    Reserve contiguous slots as a life anchor (sleep/meal/commute/gym).

    Anchors are the mentor's hard availability constraints — no task is ever
    placed on top of them (see place_time_block's anchor guard).

    **A span may cross midnight, and now it does.** Sleep 22:00 -> 09:00 is four
    slots today plus eighteen tomorrow. The previous implementation clamped with
    `min(SLOTS_PER_DAY, start + need)` and returned `status: "ok"`, so an
    eleven-hour anchor quietly claimed two hours and dropped nine — the mentor
    then told the user their sleep was protected when it was not. `slot_span()`
    produces the full mapping, and every slot in it is written.

    `date` is honoured when supplied: the clock time is rebased onto it, so
    `set_anchor(mm, "2026-09-16", "2026-09-15T22:00", ...)` is unambiguous. It
    used to be a dead parameter that the sidecar compensated for.
    """
    state = (state or "meal").strip().lower()
    if state not in ANCHOR_STATES:
        return {"status": "error", "error": f"anchor state must be one of {ANCHOR_STATES}, got '{state}'"}

    try:
        start_dt = align_to_slot(parse_wall_clock(start_time))
    except Exception as exc:
        return {"status": "error", "error": f"invalid start_time '{start_time}': {exc}"}

    # Rebase onto the caller's date when it names a different day. Without this
    # the parameter was ignored, and the sidecar passed a derived value to look
    # honest.
    if date is not None:
        try:
            wanted = grid_date_of(date)
            if wanted is not None and wanted != start_dt.date():
                start_dt = start_dt.replace(year=wanted.year, month=wanted.month, day=wanted.day)
        except Exception as exc:
            return {"status": "error", "error": f"invalid date '{date}': {exc}"}

    span = slot_span(start_dt, duration_min)
    if not span:
        return {"status": "error", "error": "anchor duration rounds to zero slots"}

    by_date = group_span_by_date(span)
    try:
        for day, indices in by_date.items():
            for idx in indices:
                memory_manager.set_day_slot_fields(
                    day, idx, state=state, label=label or state,
                    planned_by="user", event_id=None,
                )
    except Exception as exc:
        return {"status": "error", "error": f"anchor write failed after {sum(len(v) for v in by_date.values())} slots: {exc}"}

    return {
        "status": "ok",
        "slots": [slot_clock(idx) for _, idx in span],
        "slot_count": len(span),
        "dates": [day.isoformat() for day in by_date],
        "spans_midnight": len(by_date) > 1,
        "summary": f"{state} anchored {describe_span(span)}",
        "anchor": state,
        "label": label,
    }


def _grid_date_of(value: Any) -> Any:
    """Deprecated: use `orchestrator.memory.grid.grid_date_of`."""
    return grid_date_of(value)


# ---------------------------------------------------------------------------
# 2. LangChain tool wrappers (what the LLM sees)
# ---------------------------------------------------------------------------

_TOOL_FUNCS: dict[str, Any] = {
    "format_duration": format_duration,
    "compute_learning_streak": compute_learning_streak,
    "trim_plan_to_fit": trim_plan_to_fit,
    "get_available_topic_nodes": get_available_topic_nodes,
}

TOOLS = [tool(fn) for fn in _TOOL_FUNCS.values()]
TOOL_BY_NAME = dict(_TOOL_FUNCS)


# ---------------------------------------------------------------------------
# Tool-call capture (drives the architecture visualizer's live trace).
#
# A per-turn, thread-local recorder: the orchestrator starts a capture before a
# turn, every executed tool appends a compact event, and the node wrapper drains
# them into the turn's `execution_trace`. Fail-open — if no capture is active
# this is a no-op, so tools used outside a turn are unaffected.
# ---------------------------------------------------------------------------

import threading as _threading

_TOOL_CAPTURE = _threading.local()


def start_tool_capture() -> None:
    """Begin a fresh tool-call capture for the current turn (this thread)."""
    _TOOL_CAPTURE.calls = []


def drain_tool_calls() -> list[dict[str, Any]]:
    """Return + clear the tool calls recorded so far this turn."""
    calls = list(getattr(_TOOL_CAPTURE, "calls", []) or [])
    _TOOL_CAPTURE.calls = []
    return calls


def _preview_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Compact, display-safe preview of tool arguments (no huge blobs)."""
    out: dict[str, Any] = {}
    for k, v in (args or {}).items():
        s = v if isinstance(v, (int, float, bool)) or v is None else str(v)
        if isinstance(s, str) and len(s) > 80:
            s = s[:77] + "…"
        out[str(k)] = s
    return out


def _preview_result(result: Any, n: int = 300) -> str | None:
    """Compact preview of a tool's return value for the flow view."""
    if result is None:
        return None
    try:
        s = result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception:
        s = repr(result)
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _record_tool_call(
    name: str,
    args: dict[str, Any] | None,
    ok: bool,
    error: str | None = None,
    result: Any = None,
) -> None:
    """Append one tool event to the active capture (no-op when inactive)."""
    calls = getattr(_TOOL_CAPTURE, "calls", None)
    if calls is None:
        return
    calls.append({
        "tool": name,
        "ok": bool(ok),
        "error": error,
        "args": _preview_args(args),
        "result": None if error else _preview_result(result),
    })


def _execute_tool(name: str, args: dict, tool_map: dict[str, Any] | None = None) -> str:
    """
    Run one tool by name and return a JSON string (errors are encoded, not raised).

    `tool_map` resolves langchain @tool instances (memory / calendar / code
    tools) that aren't in the static TOOL_BY_NAME registry. Every invocation is
    recorded for the architecture trace.
    """
    fn = TOOL_BY_NAME.get(name)
    if fn is None and tool_map:
        fn = tool_map.get(name)
    if fn is None:
        _record_tool_call(name, args, ok=False, error="unknown tool")
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        invoke = getattr(fn, "invoke", None)
        result = invoke(args or {}) if callable(invoke) else fn(**(args or {}))
        _record_tool_call(name, args, ok=True, result=result)
        return json.dumps(result, default=str)
    except Exception as exc:
        _record_tool_call(name, args, ok=False, error=str(exc))
        return json.dumps({"error": f"tool {name} failed: {exc}"})

# ---------------------------------------------------------------------------
# 2b. Code-explorer tool suite (Shape-1 code grounding)
# ---------------------------------------------------------------------------

# Commands the mentor may RUN (allowlisted). No arbitrary shell — tests, lint,
# and build only. Anything with shell metacharacters is refused outright.
_ALLOWED_COMMANDS = (
    "uv run pytest",
    "pytest",
    "uv run python -m pytest",
    "python -m pytest",
    "uv run ruff",
    "ruff check",
    "uv run ruff check",
    "uv run python -m compileall",
    "uv run py_compile",
    "uv run python -m py_compile",
)

_FORBIDDEN_META = (";", "&&", "||", "|", ">", "<", "$(", "`")

# Overridable sandbox roots (tests); default comes from config.WORKSPACE_ROOTS.
_WS_ROOTS: tuple[str, ...] = ()


def _set_ws_roots(roots: list[str]) -> None:
    """Override the workspace sandbox (used by tests)."""
    global _WS_ROOTS
    _WS_ROOTS = tuple(roots)


def _get_ws_roots() -> list[str]:
    if _WS_ROOTS:
        return list(_WS_ROOTS)
    try:
        from orchestrator.config import WORKSPACE_ROOTS
        return list(WORKSPACE_ROOTS)
    except Exception:
        return [str(Path.cwd())]


def _resolve_ws_path(path: str | None = None) -> Path:
    """
    Resolve a user-supplied path against the workspace sandbox.

    Relative paths resolve against the FIRST workspace root; absolute paths
    must be inside some workspace root. Returns an absolute Path guaranteed
    to be inside the sandbox. Raises RuntimeError (fail-open → JSON error).
    """
    raw = (path or "").strip()
    roots = _get_ws_roots()
    if not roots:
        raise RuntimeError("no workspace roots configured")
    base = Path(roots[0]).expanduser().resolve()
    cand = (Path(raw).expanduser().resolve() if raw and Path(raw).is_absolute()
            else (base / raw if raw else base)).resolve()
    for root in roots:
        root_p = Path(root).expanduser().resolve()
        if cand == root_p or root_p in cand.parents:
            return cand
    raise RuntimeError(f"path outside workspace sandbox: {raw}")


# Skips these dirs everywhere (recursion noise / vendor / secrets).
_SKIPPED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".obsidian",
                 ".ruff_cache", ".pytest_cache", ".pgdata", ".zed"}


def read_file(path: str, start: int | None = None, end: int | None = None) -> dict:
    """Read a text file inside the workspace, optionally line-bounded (1-based)."""
    p = _resolve_ws_path(path)
    if p.is_dir():
        return {"error": f"{p} is a directory — use list_directory"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"error": f"read failed: {exc}"}
    lines = text.splitlines()
    if start is None and end is None:
        s, e = 1, len(lines)
    else:
        s = max(1, int(start or 1))
        e = int(end) if end else min(s + 119, len(lines))
    return {
        "path": str(p),
        "total_lines": len(lines),
        "start_line": s,
        "end_line": e,
        "truncated": s > 1 or e < len(lines),
        "content": "\n".join(lines[s - 1 : e]) or "(file appears to be empty on this range)",
    }


def grep_search(pattern: str, path: str | None = None, max_results: int = 40) -> dict:
    """Regex search inside the workspace (single files or a directory walk)."""
    import re as _re

    target = _resolve_ws_path(path or ".")
    try:
        rx = _re.compile(pattern)
    except Exception as exc:
        return {"error": f"bad regex: {exc}"}
    hits: list[dict] = []
    files = [target] if target.is_file() else sorted(
        p for p in target.rglob("*") if p.is_file() and not any(
            part in _SKIPPED_DIRS for part in p.relative_to(target).parts
        )
    )
    for f in files:
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append({"path": str(f), "line": i, "text": line[:200]})
                    if len(hits) >= int(max_results):
                        return {"pattern": pattern, "count": len(hits), "truncated": True, "hits": hits}
        except Exception:
            continue
    return {"pattern": pattern, "count": len(hits), "truncated": False, "hits": hits}


def list_directory(path: str | None = None, max_entries: int = 60) -> dict:
    """List one directory level inside the workspace."""
    p = _resolve_ws_path(path or ".")
    if p.is_file() or not p.exists():
        return {"error": f"{p} is not a directory"}
    entries: list[dict] = []
    try:
        children = sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
    except Exception as exc:
        return {"error": f"list failed: {exc}"}
    for c in children:
        if c.is_dir() and c.name in _SKIPPED_DIRS:
            continue
        entries.append({
            "name": c.name,
            "is_dir": c.is_dir(),
            "size": c.stat().st_size if c.is_file() else None,
        })
        if len(entries) >= int(max_entries):
            return {"path": str(p), "entries": entries, "truncated": True}
    return {"path": str(p), "entries": entries, "truncated": False}


def git_status(path: str | None = None) -> dict:
    """Short git status (porcelain) for the workspace dir (or a subdir)."""
    p = _resolve_ws_path(path or ".")
    out = _run_git(["status", "--short"], p)
    return {"status": out.splitlines() if out else []}


def git_log(path: str | None = None, n: int = 10) -> dict:
    """Recent commit history for the workspace repo."""
    p = _resolve_ws_path(path or ".")
    out = _run_git(["log", f"-{max(1, min(int(n), 25))}", "--pretty=format:%h %ad %s", "--date=short"], p)
    return {"log": out.splitlines() if out else []}


def git_diff(path: str | None = None, max_lines: int = 120) -> dict:
    """Unstaged diff for the workspace; returns a bounded number of lines."""
    p = _resolve_ws_path(path or ".")
    out = _run_git(["diff", "--stat"], p)
    body = _run_git(["diff"], p)
    lines = body.splitlines()[: int(max_lines)]
    return {"stat": out.splitlines() or [], "diff": lines, "truncated": len(body.splitlines()) > int(max_lines)}


def _run_git(args: list[str], cwd: Path) -> str:
    import subprocess

    cp = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=20,
    )
    if cp.returncode != 0:
        return cp.stderr.strip() or "(git failed)"
    return cp.stdout.strip()


# Allowlisted runnable project commands — tests/lint/build ONLY, never a shell.
_ALLOWED_COMMANDS = (
    "uv run pytest",
    "pytest",
    "uv run python -m pytest",
    "python -m pytest",
    "uv run ruff",
    "ruff check",
    "uv run ruff check",
    "uv run python -m py_compile",
    "uv run python -m compileall",
)


def run_command(command: str) -> dict:
    """
    Run an allowlisted project command inside the workspace. Strict safety:
    only the commands in _ALLOWED_COMMANDS are permitted; anything with shell
    metacharacters (; | && > < ` $()) is refused outright.
    """
    cmd = (command or "").strip()
    if not cmd or any(m in cmd for m in (";", "|", "&&", "||", ">", "<", "`", "$(")):
        return {"error": "blocked: shell metacharacters are not allowed"}
    allowed = next((a for a in _ALLOWED_COMMANDS if cmd.startswith(a)), None)
    if not allowed:
        return {"error": "blocked: command not allowlisted — tests/lint/build only"}
    import subprocess

    p = _resolve_ws_path(".")
    cp = subprocess.run(
        cmd.split(), cwd=str(p), capture_output=True, text=True, timeout=180,
    )
    return {
        "command": cmd,
        "returncode": cp.returncode,
        "stdout": (cp.stdout or "")[-4000:],
        "stderr": (cp.stderr or "")[-1500:],
    }


def propose_edit(path: str, search_text: str, replace_text: str, reason: str = "") -> dict:
    """
    The mentor-writes lane (Shape-1): propose a code change WITHOUT applying it.
    Returns a diff and the edited content for the user to review and approve.
    V1 NEVER writes to disk — edits are proposals only (draft-and-confirm).
    """
    p = _resolve_ws_path(path)
    if p.is_file() is False:
        return {"error": f"{p} is not a file"}
    try:
        current = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"error": f"read failed: {exc}"}
    if search_text not in current:
        return {"error": "search_text not found in file — precise match required"}
    new_content = current.replace(search_text, replace_text, 1)
    import difflib

    diff = "\n".join(difflib.unified_diff(
        current.splitlines(), new_content.splitlines(),
        fromfile=str(p), tofile=str(p), lineterm="",
    ))
    return {
        "proposal": {
            "path": str(p),
            "reason": reason,
            "search_text": search_text[:200],
            "replace_text": replace_text[:500],
            "diff": diff[:4000] or "(replacements only — no line diff)",
            "status": "pending_approval",
            "note": "This is a PROPOSAL. Nothing has been written. Show this diff to the user and let THEM apply it.",
        }
    }


# Full code-explorer surface. NOTE: no write-to-disk tool exists in v1 — the
# mentor reads, reasons, proposes diffs; the human owns the pen.
CODE_TOOL_FUNCS: dict[str, Any] = {
    "read_file": read_file,
    "grep_search": grep_search,
    "list_directory": list_directory,
    "git_status": git_status,
    "git_log": git_log,
    "git_diff": git_diff,
    "run_command": run_command,
    "propose_edit": propose_edit,
}
CODE_TOOLS = [tool(fn) for fn in CODE_TOOL_FUNCS.values()]
TOOL_BY_NAME.update(CODE_TOOL_FUNCS)


# ---------------------------------------------------------------------------
# 3. Fail-open tool-calling loop
# ---------------------------------------------------------------------------

def _content_of(response: Any) -> str:
    """Extract plain text from an AIMessage, tolerating list/multimodal content."""
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict) and c.get("text"):
                parts.append(str(c["text"]))
        return "".join(parts)
    return str(content)


@component_span("tool_loop", tags=["component:tool_loop"])
def run_tool_loop(llm: Any, messages: list, tools: list | None = None, max_rounds: int = 3) -> str:
    """
    Run a minimal tool-calling loop against the LLM.

    Flow:
        LLM call → if the model emits tool_calls, execute them, append the
        results as ToolMessages, and call again. Stop on a text-only answer or
        after max_rounds.

    Fail-open behavior:
        - model emits no tool_calls → plain text returned immediately.
        - tool-bound invoke raises → retry with a plain (unbound) call.
        - a tool itself raises → the error string is fed back to the model.
    """
    tools = tools if tools is not None else TOOLS
    tool_map = {getattr(t, "name", ""): t for t in tools}
    llm_with_tools = llm.bind_tools(tools)
    current = list(messages)

    for _ in range(max_rounds):
        try:
            response = llm_with_tools.invoke(current)
        except Exception as exc:
            print(f"[harness] tool-bound call failed ({exc}) — falling back to plain call.")
            try:
                response = llm.invoke(current)
            except Exception as exc2:
                raise RuntimeError(f"LLM call failed with and without tools: {exc2}") from exc
            return _content_of(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return _content_of(response)

        current = current + [response]
        for tc in tool_calls:
            name = str(tc.get("name", ""))
            args = tc.get("args") or {}
            tool_call_id = tc.get("id") or ""
            current.append(
                ToolMessage(
                    content=_execute_tool(name, args, tool_map),
                    tool_call_id=tool_call_id,
                    name=name,
                )
            )

    # Max rounds reached without a final text — return the last assistant text.
    for m in reversed(current):
        if getattr(m, "type", "") == "ai" or (isinstance(m, dict) and m.get("role") == "assistant"):
            return _content_of(m)
    return "I hit my computation limit — please ask again."

# ---------------------------------------------------------------------------
# 4. Situation facts assembler (structured context for the manager)
# ---------------------------------------------------------------------------

def _render_situation_facts(profile: dict[str, Any], today_events: list | None = None) -> str:
    """Render a compact structured facts block from a profile dict."""
    facts: list[str] = []

    streak = profile.get("learning_streak_days")
    if streak not in (None, "", 0):
        facts.append(f"learning_streak_days: {streak}")

    alp = profile.get("active_learning_path")
    if isinstance(alp, dict) and alp.get("title"):
        facts.append(f"active_learning_path: {alp['title']}")

    log = profile.get("learning_log") or []
    if log:
        lines = []
        for entry in list(log)[-7:]:
            if isinstance(entry, dict):
                d = entry.get("date") or entry.get("date_str") or "?"
                topics = entry.get("topics") or entry.get("topic") or entry.get("summary") or ""
                if isinstance(topics, list):
                    topics = ", ".join(str(t) for t in topics)
                lines.append(f"- {d}: {topics}")
            else:
                lines.append(f"- {entry}")
        if lines:
            facts.append("recent_learning_log (last 7):\n" + "\n".join(lines))

    graphs = profile.get("topic_graphs") or []
    if graphs:
        try:
            avail = get_available_topic_nodes(graphs)
            if avail:
                node_lines = [
                    f"- {a['graph_title']} :: {a['title']} ({a['status']})"
                    for a in avail[:15]
                ]
                facts.append("available_topic_nodes (study-ready, computed by code):\n" + "\n".join(node_lines))
        except Exception as exc:
            facts.append(f"(available topics unavailable: {exc})")

    if today_events:
        ev_lines = []
        for ev in today_events:
            if isinstance(ev, dict):
                title = ev.get("title") or ev.get("summary") or ""
                start = ev.get("start_time") or ev.get("occurred_at") or ""
                ev_lines.append(f"- {start}: {title}")
        if ev_lines:
            facts.append("today_schedule:\n" + "\n".join(ev_lines))

    if not facts:
        return ""
    return "=== SITUATION FACTS (structured, computed by code) ===\n" + "\n".join(facts)


def assemble_situation_facts(memory_manager: Any) -> str:
    """Load profile + today's schedule from the memory manager and render facts."""
    try:
        profile = memory_manager.load_profile_facts(
            [
                "learning_streak_days",
                "learning_log",
                "todays_plan",
                "active_learning_path",
            ]
        )
    except Exception as exc:
        print(f"[harness] profile facts read failed: {exc}")
        profile = {}

    # Topic graphs come from the curriculum manifests, NOT from profile_facts.
    # The DB key `topic_graphs` was never populated, so this block used to report
    # "no available topics" while the curriculum held five unlocked nodes.
    try:
        from orchestrator.config import MENTOR_CURRICULUM_PATH
        from orchestrator.memory.roadmap import list_roadmaps

        profile["topic_graphs"] = [
            g.model_dump(mode="json") for g in list_roadmaps(MENTOR_CURRICULUM_PATH)
        ]
    except Exception as exc:
        print(f"[harness] curriculum read failed: {exc}")
        profile.setdefault("topic_graphs", [])

    today_events: list | None = None
    try:
        now = now_local()
        start = datetime.combine(now.date(), time.min, tzinfo=local_tz())
        end = datetime.combine(now.date(), time.max, tzinfo=local_tz())
        today_events = memory_manager.get_schedule_events(start_date=start, end_date=end)
    except Exception as exc:
        print(f"[harness] schedule read failed: {exc}")

    base = _render_situation_facts(profile, today_events)

    # Day-over-day narrative continuity: last 3 daily summaries.
    try:
        from orchestrator.memory.daily_summary import (
            load_recent_daily_summaries,
            render_daily_summaries,
        )
        summaries_block = render_daily_summaries(
            load_recent_daily_summaries(memory_manager, n=3, before_date=now_local())
        )
    except Exception as exc:
        print(f"[harness] daily summaries read failed: {exc}")
        summaries_block = ""

    if summaries_block and base:
        return f"{base}\n\n{summaries_block}"
    return base or summaries_block


def assemble_situation_facts_from_profile(profile: dict[str, Any]) -> str:
    """Render situation facts from an already-loaded profile dict (e.g. a MemorySlice)."""
    return _render_situation_facts(profile or {})
# ---------------------------------------------------------------------------
# 5. Memory-write tool factory (side-effect executors)
# ---------------------------------------------------------------------------


def make_memory_tools(mm: Any) -> list:
    """
    Create harness tools that write to the memory manager.

    These tools are NOT pure — they persist the LLM's classification decisions
    to PostgreSQL. They are bound to the current `mm` instance and injected
    alongside the pure computation tools during the tool loop.

    Returns:
        A list of LangChain `tool`-decorated functions.
    """
    from datetime import datetime

    from schemas.memory import DailyPlan, LearningLogEntry, PlanItem

    @tool
    def save_daily_plan(
        items: list[dict],
        available_minutes: int,
        date: str | None = None,
    ) -> str:
        """
        Persist a daily plan to memory (profile_facts + episodic event).

        Args:
            items: List of plan items, each with title, category, priority, duration_min.
            available_minutes: Total time budget for the day.
            date: ISO date string for the plan (defaults to today in the user's timezone).

        Returns:
            A confirmation string summarizing what was saved.
        """
        now = now_local()
        plan_date = now
        if date:
            try:
                plan_date = datetime.fromisoformat(date)
            except Exception:
                plan_date = now

        plan_items = []
        for i in items:
            plan_items.append(PlanItem(
                title=i.get("title", "Untitled"),
                category=i.get("category", "learning"),
                priority=i.get("priority", "should"),
                duration_min=int(i.get("duration_min", 30)),
                linked_goal=i.get("linked_goal"),
                notes=i.get("notes"),
                scheduled_time=i.get("scheduled_time"),
            ))

        plan = DailyPlan(
            date=plan_date,
            generated_by="orchestrator",
            items=plan_items,
            total_available_minutes=available_minutes,
        )
        plan_data = plan.model_dump(mode="json")

        # Persist
        try:
            mm.set_profile_fact("learning", "todays_plan", plan_data)
        except Exception as exc:
            return f"warning: failed to save plan: {exc}"

        try:
            existing = mm.get_profile_fact("learning", "daily_plans") or []
            if not isinstance(existing, list):
                existing = []
            existing.append(plan_data)
            mm.set_profile_fact("learning", "daily_plans", existing)
        except Exception:
            pass
# Write episodic event for plan creation
        try:
            task_summary = "; ".join(
                f"{i.get('category')} ({i.get('duration_min')}m)"
                for i in items
            )
            mm.add_episodic_event(
                source_agent="orchestrator",
                event_type="daily_plan",
                content=f"Daily plan for {plan_date.strftime('%Y-%m-%d')}: {task_summary}",
                payload={"date": plan_date.isoformat(), "tasks": items},
                tags=["daily_plan", "orchestrator"],
                importance=3,
                occurred_at=now,
            )
        except Exception as exc:
            print(f"[harness] episodic plan write failed: {exc}")

        total = sum(int(i.get("duration_min", 0)) for i in items)

        # Calendar mirror (gap fix): plan items carrying an explicit
        # scheduled_time are persisted as real schedule_events + grid slots so
        # the plan is visible in the mentor's own "Today's Schedule" AND in the
        # user's calendar — not just in the todays_plan profile fact.
        placed = 0
        for item in items:
            st = item.get("scheduled_time")
            if not st:
                continue
            try:
                mm.create_schedule_event({
                    "title": item.get("title", "Plan item"),
                    "category": item.get("category", "learning"),
                    "start_time": st,
                    "duration_min": int(item.get("duration_min", 30)),
                    "priority": item.get("priority", "should"),
                    "block_kind": "task",
                    "status": "scheduled",
                    "linked_goal": item.get("linked_goal"),
                    "notes": item.get("notes"),
                })
                placed += 1
            except Exception as exc:
                print(f"[harness] plan→calendar mirror failed: {exc}")

        mirror_note = f" · {placed} block(s) mirrored to the calendar grid" if placed else ""
        return f"Plan saved for {plan_date.strftime('%Y-%m-%d')}: {len(items)} items, {total} minutes total.{mirror_note}"

    @tool
    def log_learning_session(
        topics: list[str],
        is_no_learning_day: bool = False,
        source: str | None = None,
        date: str | None = None,
    ) -> dict:
        """
        Log a learning session and compute the updated streak.

        Args:
            topics: List of topic names studied.
            is_no_learning_day: True if user explicitly said they did nothing.
            source: Optional evidence/context.
            date: ISO date string (defaults to today UTC).

        Returns:
            {'logged': bool, 'new_streak': int, 'note': str}
        """
        now = now_local()
        log_date = now
        if date:
            try:
                log_date = datetime.fromisoformat(date)
            except Exception:
                log_date = now
        today_key = log_date.strftime("%Y-%m-%d")

        # Read current streak
        current_streak = 0
        try:
            current_streak = int(mm.get_profile_fact("learning", "learning_streak_days") or 0)
        except Exception:
            pass

        # Check if today was already logged
        has_logged_today = False
        try:
            existing_log = mm.get_profile_fact("learning", "learning_log") or []
            if not isinstance(existing_log, list):
                existing_log = []
            has_logged_today = any(
                isinstance(e, dict) and str(e.get("date", "")).startswith(today_key)
                for e in existing_log
            )
        except Exception:
            existing_log = []

        # Streak math (code, not LLM)
        if is_no_learning_day:
            new_streak = 0
        elif has_logged_today:
            new_streak = current_streak
        else:
            new_streak = current_streak + 1

        # Build and persist entry
        entry = LearningLogEntry(
            date=log_date,
            topics=topics if not is_no_learning_day else ["(skipped)"],
            source=source or "self_report",
        ).model_dump(mode="json")

        try:
            if not has_logged_today:
                existing_log.append(entry)
                mm.set_profile_fact("learning", "learning_log", existing_log)
            mm.set_profile_fact("learning", "learning_streak_days", new_streak)
        except Exception as exc:
            return {"logged": False, "new_streak": 0, "note": f"persistence failed: {exc}"}

        # Episodic event
        try:
            topics_str = ", ".join(topics) if topics else "(none)"
            mm.add_episodic_event(
                source_agent="orchestrator",
                event_type="learning_session",
                content=f"Learning session on {today_key}: {topics_str}",
                payload={"date": today_key, "topics": topics, "source": source,
                         "is_no_learning_day": is_no_learning_day},
                tags=["learning_session", "orchestrator"],
                importance=3,
                occurred_at=now,
            )
        except Exception:
            pass

        note = (
            "Streak reset to 0 (no learning today)."
            if is_no_learning_day
            else f"Streak advanced to {new_streak}."
            if new_streak > current_streak
            else f"Streak unchanged at {current_streak} (already logged today)."
        )
        return {"logged": True, "new_streak": new_streak, "note": note}

    return [save_daily_plan, log_learning_session]


def make_calendar_tools(mm: Any) -> list:
    """
    Create harness tools bound to the memory manager for the calendar grid.

    Derived from the deterministic functions in this module; the LLM sees the
    tools, code owns the slot math (alignment, overlap, anchor guard).
    """
    from datetime import datetime as _dt

    @tool
    def get_day_grid(date: str = "") -> str:
        """Return the full 48-slot day grid (states, labels, event titles, free windows) for date (ISO 'YYYY-MM-DD' or full ISO datetime; defaults to today in the user's timezone)."""
        d = now_local()
        if date:
            try:
                d = _dt.fromisoformat(str(date))
            except ValueError as exc:
                return json.dumps({"error": f"bad date '{date}': {exc}"})
        return json.dumps(build_day_grid(mm, d), default=str)

    @tool
    def find_available_slots(date: str, duration_min: int, energy_level: int | None = None) -> str:
        """Return candidate free windows on a date (ISO) for a task of duration_min — the mentor picks only from these candidates."""
        return json.dumps(find_available_slots(mm, date, int(duration_min), energy_level), default=str)

    @tool
    def place_time_block(
        title: str,
        start_time: str,
        duration_min: int,
        category: str = "learning",
        priority: str = "should",
        notes: str | None = None,
    ) -> str:
        """Book a focused block on a date's calendar (30-min aligned). Validates overlap and never places tasks over sleep/meal/commute anchors. Persisted as a schedule event + grid slots."""
        return json.dumps(
            place_time_block(mm, title, start_time, int(duration_min), category, priority, "task", notes),
            default=str,
        )

    @tool
    def set_anchor(
        date: str,
        start_time: str,
        duration_min: int,
        state: str = "meal",
        label: str | None = None,
    ) -> str:
        """Reserve recurring life anchor slots (sleep|meal|commute|gym) on a date. Anchors are hard availability constraints — no task is ever placed on them."""
        return json.dumps(set_anchor(mm, date, start_time, int(duration_min), state, label), default=str)

    return [get_day_grid, find_available_slots, place_time_block, set_anchor]
