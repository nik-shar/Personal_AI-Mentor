"""
orchestrator/nodes/context_builder.py

Given a chosen agent + task_type, read that agent's context schema and build a
MemorySlice by pulling exactly the right chunks from each memory tier.

Design principle (simplified v2):
  The reasoner LLM has already processed the full DNA context + profile + user
  message. Its notes_for_agent is the PRIMARY rich-context channel for the
  sub-agent. This node only:
    1. Loads typed keys that code paths actually need (target_roles, energy_level, etc.)
    2. Wraps the reasoner's synthesized guidance into the AgentTask.instructions
    3. Does NOT load DNA memories, procedural rules, mindset calibration, or
       extra_instructions — those all go through the reasoner's notes_for_agent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from orchestrator.config import AGENT_CONTEXT_SCHEMAS, DEFAULT_TASK_TYPES
from orchestrator.memory.store import MemoryManager
from orchestrator.nodes.reasoner import ReasoningDecision
from orchestrator.tracing import traced
from schemas import ActivityLogEntry, AgentTask, MemorySlice, TaskSource


@traced("context_builder")
def build_task(
    memory_manager: MemoryManager,
    session_id: str,
    user_input: str,
    agent_name: str,
    task_type: str,
    decision: ReasoningDecision | None = None,
) -> AgentTask:
    """Construct the full AgentTask for the selected agent."""
    schema = AGENT_CONTEXT_SCHEMAS.get(agent_name, {})

    relevant_profile = _build_relevant_profile(memory_manager, schema)
    recent_activity = _build_recent_activity(memory_manager, schema)
    private_memory = _build_private_memory(memory_manager, schema)
    constraints = _build_constraints(memory_manager)

    # Synthesize a clean, conflict-free instruction for the specialist sub-agent.
    # Only two blocks: what the user said + what the reasoner synthesized.
    rich_instructions = [
        "[USER REQUEST]",
        f"The user requested: \"{user_input}\"",
        ""
    ]

    if decision and decision.notes_for_agent:
        rich_instructions.extend([
            "[MENTOR GUIDANCE / STRATEGY]",
            decision.notes_for_agent,
            ""
        ])

    # Per-agent operating guidelines from mentor_agent_guidelines.md §4 —
    # user-authored standing orders for this specialist (fail-open).
    try:
        from orchestrator.cognition.guidelines import get_agent_guidelines
        agent_guidelines = get_agent_guidelines(agent_name)
    except Exception as exc:
        print(f"[context_builder] guidelines load failed: {exc}")
        agent_guidelines = ""
    if agent_guidelines:
        rich_instructions.extend([
            "[AGENT OPERATING GUIDELINES — standing orders, never violate]",
            agent_guidelines,
            ""
        ])

    instructions = "\n".join(rich_instructions).strip()

    memory_slice = MemorySlice(
        agent_name=agent_name,
        task_type=task_type or DEFAULT_TASK_TYPES.get(agent_name, "direct_response"),
        relevant_profile=relevant_profile,
        recent_activity=recent_activity,
        private_memory=private_memory,
        constraints=constraints,
    )

    params: dict[str, Any] = {}

    return AgentTask(
        task_id=str(uuid4()),
        agent_name=agent_name,
        task_type=task_type or memory_slice.task_type,
        instructions=instructions,
        params=params,
        source=TaskSource.CHAT,
        memory_slice=memory_slice,
    )


def _build_relevant_profile(
    memory_manager: MemoryManager,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Pull requested profile keys and episodic aliases into the profile dict."""
    profile_keys = schema.get("profile_keys", [])

    virtual_keys = schema.get("virtual_keys", [])
    flat_keys = [k for k in profile_keys if k not in virtual_keys]
    profile = memory_manager.load_profile_facts(flat_keys)

    # --- Virtual key: curriculum topic graphs ---
    # Loaded from the roadmap manifests in the curriculum root (inside the repo),
    # not by scanning note frontmatter: the manifest is the authoritative
    # structure, and it is one file per roadmap instead of an rglob of every note.
    if "obsidian_topic_graphs" in virtual_keys or "topic_graphs" in profile_keys:
        curriculum_graphs = []
        try:
            from orchestrator.config import MENTOR_CURRICULUM_PATH
            from orchestrator.memory.roadmap import list_roadmaps

            curriculum_graphs = [
                g.model_dump(mode="json") for g in list_roadmaps(MENTOR_CURRICULUM_PATH)
            ]
        except Exception as exc:
            print(f"[context_builder] Failed to load curriculum topic_graphs: {exc}")

        db_graphs = profile.get("topic_graphs") or []
        if not isinstance(db_graphs, list):
            db_graphs = []

        merged_map = {}
        for g in db_graphs:
            if isinstance(g, dict) and g.get("topic_id"):
                merged_map[g["topic_id"]] = g
            elif hasattr(g, "topic_id"):
                merged_map[g.topic_id] = g.model_dump(mode="json")
        for g in curriculum_graphs:
            if isinstance(g, dict) and g.get("topic_id"):
                merged_map[g["topic_id"]] = g

        profile["topic_graphs"] = list(merged_map.values())

    # Episodic queries are exposed to the agent under a descriptive alias.
    for episodic_query in schema.get("episodic", []):
        alias = episodic_query.get("alias")
        if not alias:
            continue

        filters = {
            "event_type": episodic_query.get("event_type"),
            "source_agent": episodic_query.get("source_agent"),
            "tags": episodic_query.get("tags"),
            "importance__gte": episodic_query.get("importance__gte"),
            "last_n": episodic_query.get("last_n"),
        }
        # Remove None filters so query_episodic treats them as absent.
        filters = {k: v for k, v in filters.items() if v is not None}
        events = memory_manager.query_episodic(**filters)
        profile[alias] = events

    return profile


def _build_recent_activity(
    memory_manager: MemoryManager,
    schema: dict[str, Any],
) -> list[ActivityLogEntry]:
    """Convert episodic events into ActivityLogEntry models for the slice."""
    # Use a small, agent-agnostic recent window unless the schema already pulled
    # a dedicated alias. This keeps the MemorySlice contract intact.
    events = memory_manager.query_episodic(
        since=datetime.now(timezone.utc) - timedelta(days=3),
        last_n=10,
    )
    entries = []
    for evt in events:
        try:
            occurred = evt.get("occurred_at") or datetime.now(timezone.utc).isoformat()
            if isinstance(occurred, str):
                occurred = datetime.fromisoformat(occurred)
            entries.append(
                ActivityLogEntry(
                    timestamp=occurred,
                    source=evt.get("source_agent", "unknown"),
                    summary=f"[{evt.get('event_type', 'unknown')}] {evt.get('content', '')}"[:500],
                    task_type=evt.get("event_type"),
                    linked_memory_delta=evt.get("payload") or {},
                )
            )
        except Exception:
            # If an event cannot be coerced, skip it to avoid crashing the slice.
            continue
    return entries


def _build_private_memory(
    memory_manager: MemoryManager,
    schema: dict[str, Any],
) -> dict[str, Any] | None:
    """Fetch the agent's private scratch state, if requested."""
    agent_name = schema.get("private_memory")
    if not agent_name:
        return None
    return memory_manager.get_private_memory(agent_name)


def _build_constraints(memory_manager: MemoryManager) -> dict[str, Any]:
    """Build a small global constraints bundle from preferences."""
    prefs = memory_manager.load_profile_facts({"preferences", "linkedin_posting_frequency"})
    constraints: dict[str, Any] = {}
    if isinstance(prefs.get("preferences"), dict):
        for k, v in prefs["preferences"].items():
            if k in ("max_length", "quiet_hours"):
                constraints[k] = v
    if prefs.get("linkedin_posting_frequency"):
        constraints["linkedin_posting_frequency"] = prefs["linkedin_posting_frequency"]
    return constraints
