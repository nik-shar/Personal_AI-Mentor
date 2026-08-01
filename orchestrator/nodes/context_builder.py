"""
orchestrator/nodes/context_builder.py

Given a chosen agent + task_type, read that agent's context schema and build a
MemorySlice by pulling exactly the right chunks from each memory tier.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from schemas import ActivityLogEntry, AgentTask, MemorySlice, TaskSource

from orchestrator.config import AGENT_CONTEXT_SCHEMAS, DEFAULT_TASK_TYPES
from orchestrator.memory.store import MemoryManager
from orchestrator.nodes.reasoner import ReasoningDecision


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

    # Synthesize a context-rich prompt for the specialist sub-agent.
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

    # Extra instructions from Reasoner or Standing Profile Preferences
    extra_inst = []
    if decision and getattr(decision, "extra_instructions", None):
        extra_inst.append(decision.extra_instructions)
    if relevant_profile.get("extra_instructions"):
        profile_extra = relevant_profile["extra_instructions"]
        if isinstance(profile_extra, list):
            extra_inst.extend(profile_extra)
        elif isinstance(profile_extra, str):
            extra_inst.append(profile_extra)

    if extra_inst:
        rich_instructions.extend([
            "[EXTRA INSTRUCTIONS FOR HELPING AGENT]",
            *([f"- {i}" for i in extra_inst]),
            ""
        ])

    # Extract relevant details from the loaded profile slice to guide the agent.
    context_lines = []
    if relevant_profile.get("bio_summary"):
        context_lines.append(f"- Bio Summary: {relevant_profile['bio_summary']}")
    if relevant_profile.get("long_term_goal"):
        context_lines.append(f"- Long-term Goal: {relevant_profile['long_term_goal']}")
    if relevant_profile.get("working_habits"):
        habits = relevant_profile["working_habits"]
        habits_str = "; ".join(habits) if isinstance(habits, list) else str(habits)
        context_lines.append(f"- Working Habits & Focus Patterns: {habits_str}")
    if relevant_profile.get("mindset_notes"):
        mindset = relevant_profile["mindset_notes"]
        mindset_str = "; ".join(mindset) if isinstance(mindset, list) else str(mindset)
        context_lines.append(f"- Mindset & Watchouts: {mindset_str}")
    if "energy_level" in relevant_profile:
        context_lines.append(f"- Energy Level: {relevant_profile['energy_level']}/5")
    if "employment_status" in relevant_profile:
        context_lines.append(f"- Employment Status: {relevant_profile['employment_status']}")
    if "target_roles" in relevant_profile:
        roles = relevant_profile["target_roles"]
        roles_str = ", ".join(roles) if isinstance(roles, list) else str(roles)
        context_lines.append(f"- Target Roles: {roles_str}")
    if "active_learning_path" in relevant_profile and relevant_profile["active_learning_path"]:
        path = relevant_profile["active_learning_path"]
        if isinstance(path, dict):
            context_lines.append(f"- Active Learning Path: {path.get('title')} (Current Week: {path.get('current_week')})")
        elif hasattr(path, "title"):
            context_lines.append(f"- Active Learning Path: {path.title} (Current Week: {path.current_week})")

    if context_lines:
        rich_instructions.extend([
            "[USER PROFILE & MINDSET CONTEXT]",
            *context_lines,
            ""
        ])

    # Procedural Memory & Behavioral Rules (§3.4, §5.4.3)
    proc_rules = relevant_profile.get("procedural_rules") or []
    calibration = relevant_profile.get("mindset_calibration") or {}

    proc_lines = []
    if isinstance(proc_rules, list):
        for r in proc_rules:
            if isinstance(r, dict) and r.get("rule"):
                proc_lines.append(f"- Rule ({r.get('domain', 'general')}): {r['rule']}")
            elif isinstance(r, str):
                proc_lines.append(f"- Rule: {r}")
    if isinstance(calibration, dict):
        if calibration.get("directness"):
            proc_lines.append(f"- Communication Directness: {calibration['directness']}")
        if calibration.get("framing_that_lands"):
            lands = ", ".join(calibration["framing_that_lands"]) if isinstance(calibration["framing_that_lands"], list) else str(calibration["framing_that_lands"])
            proc_lines.append(f"- Effective Framing: {lands}")
        if calibration.get("framing_that_bounces"):
            bounces = ", ".join(calibration["framing_that_bounces"]) if isinstance(calibration["framing_that_bounces"], list) else str(calibration["framing_that_bounces"])
            proc_lines.append(f"- Ineffective Framing (Avoid): {bounces}")

    if proc_lines:
        rich_instructions.extend([
            "[PROCEDURAL RULES & USER PREFERENCES]",
            *proc_lines,
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
    if decision:
        if getattr(decision, "parsed_energy_level", None) is not None:
            params["energy_override"] = decision.parsed_energy_level
        if getattr(decision, "parsed_available_minutes", None) is not None:
            params["available_minutes"] = decision.parsed_available_minutes

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

    # --- Virtual key: obsidian_topic_graphs ---
    # Loaded from Obsidian markdown files in the user's vault folder + profile_facts.
    if "obsidian_topic_graphs" in virtual_keys or "topic_graphs" in profile_keys:
        obsidian_graphs = []
        try:
            from orchestrator.config import OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER
            from orchestrator.memory.obsidian_graph import load_topic_graphs
            graphs = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
            obsidian_graphs = [g.model_dump(mode="json") for g in graphs]
        except Exception as exc:
            print(f"[context_builder] Failed to load obsidian topic_graphs: {exc}")

        db_graphs = profile.get("topic_graphs") or []
        if not isinstance(db_graphs, list):
            db_graphs = []

        merged_map = {}
        for g in db_graphs:
            if isinstance(g, dict) and g.get("topic_id"):
                merged_map[g["topic_id"]] = g
            elif hasattr(g, "topic_id"):
                merged_map[g.topic_id] = g.model_dump(mode="json")
        for g in obsidian_graphs:
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
