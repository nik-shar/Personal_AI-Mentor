"""
orchestrator/nodes/memory_merger.py

Takes an AgentResult.memory_delta and writes each piece into the right
tier of the Postgres-backed memory store.
"""

from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel

from schemas import AgentResult

from orchestrator.config import EPISODIC_DELTA_KEYS, PROFILE_KEY_MAP
from orchestrator.memory.store import MemoryManager


def _json_safe(value: Any) -> Any:
    """Recursively make a value JSON/B serializable."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def _summarize_event(event_type: str, payload: dict[str, Any]) -> str:
    """Build a concise human-readable content string from a structured payload."""
    if event_type == "daily_plan":
        date = payload.get("date", "")
        notes = payload.get("notes", "")
        tasks = payload.get("tasks", [])
        task_summary = "; ".join(
            f"{t.get('category')} ({t.get('duration_minutes')}m)"
            for t in tasks
            if isinstance(t, dict)
        )
        parts = [f"Daily plan for {date}"]
        if task_summary:
            parts.extend(["tasks:", task_summary])
        if notes:
            parts.append(f"notes: {notes}")
        return " | ".join(parts)

    if event_type == "learning_session":
        topics = payload.get("topics", [])
        source = payload.get("source") or payload.get("evidence", "")
        date = payload.get("date", "")
        content = f"Learning session on {date}"
        if topics:
            content += f": {', '.join(str(t) for t in topics)}"
        if source:
            content += f" (source/evidence: {source})"
        return content

    if event_type == "job_application":
        company = payload.get("company", "")
        role = payload.get("role", "")
        stage = payload.get("stage", "")
        return f"Job application: {role} @ {company} ({stage})".strip()

    if event_type == "linkedin_draft":
        first_sentence = str(payload.get("content", "")).split(".")[0]
        return f"LinkedIn draft: {first_sentence}".strip()

    if event_type == "mood_note":
        energy = payload.get("energy")
        note = payload.get("note", "")
        return f"Mood/energy note: {note} (energy={energy})".strip()

    # Generic fallback: named fields, then full dict.
    for field in ("topic", "title", "summary", "what_happened", "content", "post"):
        if payload.get(field):
            return str(payload[field])

    return str(payload)


def apply_memory_delta(
    memory_manager: MemoryManager,
    result: AgentResult,
) -> None:
    """Persist all changes proposed by an AgentResult."""
    if not result.memory_delta:
        result.memory_delta = {}

    source = result.agent_name

    # 1. Profile facts (slow-changing)
    profile_updates: dict[str, Any] = {}
    for key, value in result.memory_delta.items():
        if key in PROFILE_KEY_MAP:
            profile_updates[key] = value

    if profile_updates:
        memory_manager.merge_profile_dict(profile_updates, source=source)

    # 2. Episodic events (append-only)
    for key, value in result.memory_delta.items():
        if key in EPISODIC_DELTA_KEYS:
            event_type = EPISODIC_DELTA_KEYS[key]
            _write_episodic_values(memory_manager, source, event_type, value)

    # 3. Per-agent private scratch memory
    private_memory = result.memory_delta.get("agent_private_memory")
    if isinstance(private_memory, dict):
        for agent_name, data in private_memory.items():
            if isinstance(data, dict):
                memory_manager.set_private_memory(agent_name, data)

    # 4. Always append a meta event recording that this agent ran.
    memory_manager.add_episodic_event(
        source_agent="orchestrator",
        event_type="agent_run",
        content=f"{result.agent_name} ran task {result.task_type} with status {result.status.value}.",
        payload=_json_safe({
            "agent_name": result.agent_name,
            "task_type": result.task_type,
            "task_id": result.task_id,
            "status": result.status.value,
            "output_preview": (result.output or "")[:500],
        }),
        tags=["agent_run", result.agent_name, result.status.value],
        importance=3,
        occurred_at=result.completed_at or datetime.now(timezone.utc),
    )


def _write_episodic_values(
    memory_manager: MemoryManager,
    source_agent: str,
    event_type: str,
    values: Any,
) -> None:
    """Convert a memory_delta value (list or dict) into episodic event rows."""
    if values is None:
        return

    items = values if isinstance(values, list) else [values]

    for item in items:
        occurred_at = datetime.now(timezone.utc)
        payload = {}
        tags = [event_type]
        content = ""
        importance = 3

        if isinstance(item, dict):
            payload = _json_safe(dict(item))
            content = _summarize_event(event_type, payload)

            if payload.get("energy") is not None:
                tags.append(f"energy:{payload['energy']}")
            if payload.get("duration_minutes"):
                content += f" ({payload['duration_minutes']} min)"
        else:
            content = str(item)

        if not content.strip():
            content = f"{event_type} logged by {source_agent}"

        memory_manager.add_episodic_event(
            source_agent=source_agent,
            event_type=event_type,
            content=content,
            payload=payload,
            tags=tags,
            importance=importance,
            occurred_at=occurred_at,
        )
