"""
orchestrator/memory/daily_summary.py

End-of-Day narrative memory (the "day summary" design):

Each evening a job (or an explicit call) reads the day's episodic events +
profile facts and asks the conversational LLM to write a compact narrative
summary — what was learned, completed, skipped, streak status, energy, mood.
The narrative is written as a `daily_summary` episodic event (append-only,
RAG-embeddable), and the last 2-3 summaries are injected into the DNA context
document so the reasoner carries day-over-day continuity.

Design:
  - Code owns the facts: which events to read, what date, idempotency.
  - LLM owns the narrative: a natural, specific, honest 4-6 sentence recap.
  - Fail-open: if the LLM call fails, write a deterministic structured recap
    so the facts are never lost even when the voice is.
  - Idempotent: one `daily_summary` event per day by default (force=True to redo).

Consumption:
  - `render_daily_summaries(events)` → injected into dna_context.py
  - `load_recent_daily_summaries(memory_manager, n)` → helper for callers
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from orchestrator.config import local_tz, now_local
from orchestrator.tracing import component_span

# ---------------------------------------------------------------------------
# Event-type selection for "what happened this day"
# ---------------------------------------------------------------------------

# Event types that carry meaningful day-content for the narrative. agent_run
# meta-events are noise and are excluded.
_DAY_EVENT_TYPES = (
    "learning_session",
    "daily_plan",
    "job_application",
    "linkedin_draft",
    "mood_note",
    "weekly_summary",
)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _day_window(target_date: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) for the target date — wall-clock day boundaries."""
    # The user's day, not UTC's: on a UTC+5:30 machine the two disagree for the
    # first 5.5 hours of every day, which is exactly when a nightly summary runs.
    start = datetime.combine(target_date.date(), time.min, tzinfo=local_tz())
    end = datetime.combine(target_date.date(), time.max, tzinfo=local_tz())
    return start, end


def load_day_events(
    memory_manager: Any,
    target_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch the day's episodic events (newest first) across meaningful types."""
    target_date = target_date or now_local()
    start, end = _day_window(target_date)
    events: list[dict[str, Any]] = []
    for event_type in _DAY_EVENT_TYPES:
        try:
            batch = memory_manager.query_episodic(
                event_type=event_type,
                since=start,
                until=end,
                last_n=25,
            )
            events.extend(batch or [])
        except Exception as exc:
            print(f"[daily_summary] query_episodic({event_type}) failed: {exc}")
    # Sort oldest-first for a chronological narrative.
    events.sort(key=lambda e: e.get("occurred_at") or "")
    return events


def load_day_facts(memory_manager: Any, today: datetime) -> dict[str, Any]:
    """Load the day's profile facts that matter for the narrative."""
    facts: dict[str, Any] = {}
    try:
        facts = memory_manager.load_profile_facts(
            [
                "learning_streak_days",
                "learning_log",
                "todays_plan",
                "active_learning_path",
                "energy_level",
            ]
        )
    except Exception as exc:
        print(f"[daily_summary] profile facts read failed: {exc}")
    # Keep the day's date in the payload for idempotency + provenance.
    facts["_date"] = today.strftime("%Y-%m-%d")
    return facts


def existing_daily_summary(
    memory_manager: Any,
    target_date: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the existing daily_summary event for the date, if any."""
    target_date = target_date or now_local()
    start, end = _day_window(target_date)
    try:
        events = memory_manager.query_episodic(
            event_type="daily_summary",
            since=start,
            until=end,
            last_n=5,
        )
    except Exception as exc:
        print(f"[daily_summary] existing lookup failed: {exc}")
        return None
    for evt in events or []:
        payload = evt.get("payload") or {}
        if payload.get("date") == target_date.strftime("%Y-%m-%d"):
            return evt
def load_recent_daily_summaries(
    memory_manager: Any,
    n: int = 3,
    before_date: datetime | None = None,
    errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch the last `n` daily_summary events, oldest→newest, optionally
    strictly before a date (e.g. before today, so today's in-progress day
    doesn't look like yesterday).

    `errors` is an optional out-parameter. A read failure is appended to it, not
    merely printed, because **emptiness and unreadability are different answers**:
    a caller that reports "no recaps yet" must be able to tell which one it saw.
    Non-breaking — omit it and the previous fail-open behaviour is unchanged.
    """
    try:
        if before_date is not None:
            _, end = _day_window(before_date)
            events = memory_manager.query_episodic(
                event_type="daily_summary",
                until=end,
                last_n=n * 3,
            )
        else:
            events = memory_manager.query_episodic(
                event_type="daily_summary",
                last_n=n * 3,
            )
    except Exception as exc:
        message = f"recent summaries read failed: {exc}"
        print(f"[daily_summary] {message}")
        if errors is not None:
            errors.append(message)
        return []

    events = events or []
    # Include only summaries whose payload date is strictly before the target day.
    if before_date is not None:
        key = before_date.strftime("%Y-%m-%d")
        events = [e for e in events if (e.get("payload") or {}).get("date") < key]

    events = sorted(events, key=lambda e: (e.get("payload") or {}).get("date") or "")
    return events[-n:]


def render_daily_summaries(events: list[dict[str, Any]]) -> str:
    """
    Render recent daily_summary events into compact context-document lines.
    Returns an empty string when there's nothing to show.
    """
    if not events:
        return ""
    lines = ["### 📖 Recent Days (narrative summaries)"]
    for evt in events:
        payload = evt.get("payload") or {}
        day = payload.get("date") or ""
        day_label = f"{day}: " if day else ""
        content = (evt.get("content") or "").strip()
        if not content:
            continue
        # Compact: content plus streak marker if present in payload.
        streak = payload.get("streak_days")
        streak_txt = f" (streak {streak}d)" if streak is not None else ""
        lines.append(f"- {day_label}{content}{streak_txt}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------

def _format_events_block(events: list[dict[str, Any]]) -> str:
    """Render the day's events as compact, chronological lines."""
    if not events:
        return "(no logged activity events this day)"
    lines = []
    for evt in events:
        ts = evt.get("occurred_at") or ""
        try:
            t_label = datetime.fromisoformat(str(ts)).strftime("%H:%M")
        except ValueError:
            t_label = ""
        etype = evt.get("event_type", "event")
        content = (evt.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"- [{t_label} · {etype}] {content[:240]}")
    return "\n".join(lines) if lines else "(no logged activity events this day)"


def _format_learning_block(facts: dict[str, Any]) -> str:
    """Summarize streak + today's plan + active path for the prompt."""
    parts = []
    streak = facts.get("learning_streak_days")
    if streak is not None:
        parts.append(f"learning_streak_days: {streak}")

    alp = facts.get("active_learning_path")
    if isinstance(alp, dict) and alp.get("title"):
        parts.append(f"active_learning_path: {alp['title']}")

    plan = facts.get("todays_plan")
    if isinstance(plan, dict) and plan.get("items"):
        item_txt = "; ".join(
            f"{i.get('category', 'task')} {i.get('title', '')} ({i.get('duration_min', 0)}m)"
            for i in plan["items"]
        )
        parts.append(f"todays_plan items: {item_txt}")

    log = facts.get("learning_log") or []
    day = facts.get("_date", "")
    day_entries = [e for e in log if isinstance(e, dict) and str(e.get("date", "")).startswith(day)]
    if day_entries:
        topics = set()
        for e in day_entries:
            for t in e.get("topics") or []:
                topics.add(t)
        if topics:
            parts.append(f"today's logged topics: {', '.join(sorted(topics))}")

    return "\n".join(parts) if parts else "(no learning facts captured today)"


def _format_calendar_block(mm: Any, today: datetime) -> str:
    """
    Compact day-grid facts for the narrative: placed blocks, completions,
    anchors, free windows. Fail-open to an empty string.
    """
    try:
        from orchestrator.harness import ANCHOR_STATES, build_day_grid
        grid = build_day_grid(mm, today)
    except Exception as exc:
        print(f"[daily_summary] calendar block failed: {exc}")
        return ""
    lines: list[str] = []
    tasks: list[str] = []
    completed = 0
    anchors: set[str] = set()
    for slot in grid.get("slots") or []:
        state = slot.get("state")
        if state == "task":
            tasks.append(slot.get("event_title") or "task")
            if slot.get("status") == "completed":
                completed += 1
        elif slot in ANCHOR_STATES:
            anchors.add(f"{slot.get('clock')} {slot.get('state')}")
    if tasks:
        lines.append("calendar tasks today: " + ", ".join(dict.fromkeys(tasks)))
    if completed:
        lines.append(f"calendar completions: {completed}")
    if anchors:
        lines.append("anchors: " + " · ".join(sorted(anchors)))
    free = grid.get("free_windows") or []
    if free:
        lines.append(
            "free windows: "
            + ", ".join(f"{w['start_clock']}–{w['end_clock']}" for w in free[:4])
        )
    return "\n".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Prompt + deterministic fallback (LLM owns narrative, code owns facts)
# ---------------------------------------------------------------------------

_DAILY_SUMMARY_PROMPT = """You are the memory writer for {name}'s AI mentor. Your job is to
write the end-of-day narrative summary that the mentor will remember later.

You are given today's logged activity events and current learning facts. Write a
compact, specific, honest summary of 4-6 sentences covering:
1. What was learned / worked on (be specific — topic names, project names).
2. Anything completed, skipped, or postponed.
3. Learning streak status and energy level if present.
4. Anything notable emotionally or in the job search.

Tone: factual and warm, like a trusted colleague's end-of-day note. Do NOT invent
facts. If nothing happened today, say so plainly. Keep it 4-6 sentences max.

=== TODAY'S ACTIVITY EVENTS ===
{events}

=== LEARNING FACTS ===
{facts}

=== END-OF-DAY SUMMARY ==="""
def _deterministic_fallback(events: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    """If the LLM fails, write a factual recap from the raw records."""
    parts = []
    for evt in (events or [])[:8]:
        content = (evt.get("content") or "").strip()
        if content:
            parts.append(content[:200])
    streak = facts.get("learning_streak_days")
    if streak is not None:
        parts.append(f"Learning streak: {streak} day(s).")
    day_entries = [
        e for e in (facts.get("learning_log") or [])
        if isinstance(e, dict) and str(e.get("date", "")).startswith(facts.get("_date", ""))
    ]
    if day_entries:
        topics = set()
        for e in day_entries:
            for t in e.get("topics") or []:
                topics.add(t)
        if topics:
            parts.append(f"Logged topics: {', '.join(sorted(topics))}.")
    return " | ".join(parts) if parts else "No meaningful activity recorded today."

@component_span("daily_summary", tags=["component:daily_summary"])
def build_daily_summary(
    memory_manager: Any,
    target_date: datetime | None = None,
    force: bool = False,
    llm: Any | None = None,
) -> dict[str, Any]:
    """
    Generate and persist a daily_summary event for the target date (UTC).

    Idempotent: refuses to write twice for the same date unless force=True.
    LLM-fail-open: deterministic recap is written if the LLM is unavailable.

    Returns a report dict: {status, summary, date, event_id}.
    """
    from orchestrator.llm import get_conversational_llm

    target_date = target_date or now_local()
    day_key = target_date.strftime("%Y-%m-%d")

    if not force:
        existing = existing_daily_summary(memory_manager, target_date)
        if existing:
            return {
                "status": "skipped",
                "summary": existing.get("content", ""),
                "date": day_key,
                "event_id": existing.get("id"),
                "reason": "daily_summary already exists for this date",
            }

    events = load_day_events(memory_manager, target_date)
    facts = load_day_facts(memory_manager, target_date)
    calendar_block = _format_calendar_block(memory_manager, target_date)
    events_block = _format_events_block(events)
    facts_block = _format_learning_block(facts)
    if calendar_block:
        facts_block = f"{facts_block}\n\n=== CALENDAR (from the 30-min day grid) ===\n{calendar_block}" if facts_block else (
            f"=== CALENDAR (from the 30-min day grid) ===\n{calendar_block}"
        )

    summary = ""
    llm_ok = False
    try:
        if llm is None:
            llm = get_conversational_llm(temperature=0.4)
        prompt = _DAILY_SUMMARY_PROMPT.format(
            name="Nik",
            events=events_block,
            facts=facts_block,
        )
        response = llm.invoke([("human", prompt)])
        summary = (response.content or "").strip()
        llm_ok = bool(summary)
    except Exception as exc:
        print(f"[daily_summary] LLM narrative failed ({exc}) — using deterministic recap.")

    if not llm_ok:
        summary = _deterministic_fallback(events, facts)

    # Streak marker for the context renderer.
    streak = facts.get("learning_streak_days")

    now_utc = datetime.now(timezone.utc)
    event_id = memory_manager.add_episodic_event(
        source_agent="daily_summary",
        event_type="daily_summary",
        content=summary,
        payload={
            "date": day_key,
            "streak_days": streak,
            "source_event_count": len(events),
            "llm_generated": llm_ok,
        },
        tags=["daily_summary", day_key],
        importance=4,
        occurred_at=now_utc,
    )
    # Embed for semantic retrieval (RAG) like weekly/monthly summaries.
    try:
        memory_manager.embed_and_store(event_id, summary)
    except Exception as exc:
        print(f"[daily_summary] embedding failed: {exc}")

    return {
        "status": "created" if llm_ok else "created_fallback",
        "summary": summary,
        "date": day_key,
        "event_id": event_id,
        "source_events": len(events),
    }