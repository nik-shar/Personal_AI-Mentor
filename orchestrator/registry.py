"""
orchestrator/registry.py

The single extension seam: register a new agent here and add its
context schema to config.AGENT_CONTEXT_SCHEMAS.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agents.Goal_Decomposer.goal_decomposer import run_goal_decomposer
from agents.Job_Hunter import run_job_hunter
from agents.Linkedin_writer import run_linkedin_writer
from schemas import AgentResult, AgentTask


@dataclass(frozen=True)
class AgentSpec:
    """Everything the orchestrator knows about a stateless agent."""

    name: str
    run: Callable[[AgentTask], AgentResult]
    allowed_task_types: list[str]


REGISTRY: dict[str, AgentSpec] = {
    "linkedin_writer": AgentSpec(
        name="linkedin_writer",
        run=run_linkedin_writer,
        allowed_task_types=["write_linkedin_post"],
    ),
    "goal_decomposer": AgentSpec(
        name="goal_decomposer",
        run=run_goal_decomposer,
        allowed_task_types=["decompose_goal"],
    ),
    "job_hunter": AgentSpec(
        name="job_hunter",
        run=run_job_hunter,
        allowed_task_types=[
            "tailor_resume",
            "log_application",
            "update_application_status",
            "job_search_review",
            "assess_fit",
            "search_jobs",
        ],
    ),
    "fallback": AgentSpec(
        name="fallback",
        run=lambda task: _fallback_agent(task),
        allowed_task_types=["direct_response"],
    ),
}



def _fallback_agent(task: AgentTask) -> AgentResult:
    """
    General-chat catch-all agent powered by the reasoning LLM.
    Supports web search for external queries and opportunistic question surfacing.
    """
    from integrations.search import perform_web_search
    from orchestrator.llm import get_conversational_llm
    from schemas import AgentResult, ResultStatus

    profile = task.memory_slice.relevant_profile or {}

    # Extract the raw user message from the instructions (strip the wrapper text).
    raw_instructions = task.instructions or ""
    user_message = raw_instructions
    if "The user requested:" in raw_instructions:
        parts = raw_instructions.split("The user requested:", 1)
        user_message = parts[-1].strip().strip('"').strip("'").strip()

    # Determine if web search is helpful for external technical / market queries
    lowered = user_message.lower()
    search_keywords = ["latest", "news", "trend", "framework", "release", "market", "jobs", "what is", "how to", "who is", "japan", "mcp", "vllm"]
    should_search = any(kw in lowered for kw in search_keywords) and len(user_message.split()) > 2

    web_search_block = ""
    if should_search:
        try:
            results = perform_web_search(user_message, max_results=3)
            if results and "yielded no results" not in results:
                web_search_block = f"\n\nLive Web Search Findings:\n{results}"
        except Exception as exc:
            print(f"[_fallback_agent] search error: {exc}")

    # Build a compact profile block for the system prompt.
    profile_lines: list[str] = []
    for key, label in [
        ("full_name",          "Name"),
        ("current_role",       "Current role"),
        ("employment_status",  "Employment status"),
        ("target_roles",       "Target roles"),
        ("target_locations",   "Target locations"),
        ("long_term_goal",     "Long-term goal"),
        ("short_term_goal",    "Short-term goal"),
        ("skills",             "Skills"),
        ("learning_streak_days", "Learning streak"),
        ("energy_level",       "Energy level"),
    ]:
        val = profile.get(key)
        if val is not None and val != "" and val != []:
            profile_lines.append(f"  - {label}: {val}")

    alp = profile.get("active_learning_path")
    if isinstance(alp, dict) and alp.get("title"):
        profile_lines.append(f"  - Active learning path: {alp['title']}")

    projects = profile.get("projects") or []
    if projects:
        proj_names = ", ".join(
            (p.get("name") if isinstance(p, dict) else getattr(p, "name", str(p)))
            for p in projects[:3]
        )
        profile_lines.append(f"  - Projects: {proj_names}")

    profile_block = (
        "\n".join(profile_lines)
        if profile_lines
        else "  (No profile data yet — onboarding may not have run.)"
    )

    # Check for open queued questions in memory
    open_questions = profile.get("open_questions") or []
    question_hint = ""
    if open_questions and isinstance(open_questions, list):
        question_hint = f"\n\nQueued question you may optionally ask at the end if relevant: '{open_questions[0]}'"

    system_prompt = (
        "You are Nikhil's personal AI mentor-companion. "
        "You are warm, direct, and focused on helping him grow as an AI engineer.\n\n"
        "CRITICAL BEHAVIORAL RULES:\n"
        "- MATCH YOUR RESPONSE TO THE TYPE OF MESSAGE:\n"
        "  • Greeting ('hi', 'hey', 'hello', 'hi there') → Greet back warmly. "
        "1-2 sentences max. Do NOT give advice, plans, or numbered lists.\n"
        "  • Emotional sharing → Acknowledge first, then ask what he needs.\n"
        "  • Specific question → Answer using his profile context below.\n"
        "  • Open-ended request → Give focused, personalized guidance.\n"
        "- NEVER dump unsolicited career advice, generic tips, or numbered action "
        "plans unless Nikhil explicitly asks for them.\n"
        "- Use his ACTUAL profile data below — never give generic advice like "
        "'master Python and TensorFlow' when you know his specific skills.\n\n"
        "Here is what you know about Nikhil:\n"
        f"{profile_block}"
        f"{web_search_block}"
        f"{question_hint}\n\n"
        "Respond naturally, strategically, and concisely to his message. "
        "If live web search findings are provided, use them to give accurate, up-to-date answers. "
        "Never make up unverified facts."
    )

    # Manager harness: give the fallback the same deterministic computation
    # tools (streak math, plan budgeting, topic availability) plus the
    # calendar-grid and memory tools so general chat can answer scheduling /
    # arithmetic / persistence questions without hallucinating numbers.
    situation_facts = ""
    all_tools = []
    try:
        from orchestrator.harness import (
            TOOLS,
            assemble_situation_facts,
            make_calendar_tools,
            make_memory_tools,
            run_tool_loop,
        )
        from orchestrator.memory.store import get_memory_manager

        mm = get_memory_manager()
        situation_facts = assemble_situation_facts(mm)
        all_tools = TOOLS + list(make_memory_tools(mm)) + list(make_calendar_tools(mm))
    except Exception as exc:
        print(f"[_fallback_agent] harness unavailable: {exc}")

    tool_hint = (
        "\n\nCOMPUTATION TOOLS (available via function-calling): "
        "compute_learning_streak, trim_plan_to_fit, get_available_topic_nodes, format_duration. "
        "CALENDAR TOOLS: get_day_grid, find_available_slots, place_time_block, set_anchor. "
        "MEMORY TOOLS: save_daily_plan, log_learning_session. "
        "Use them for exact arithmetic, graph, calendar, or persistence facts; "
        "not for ordinary conversation."
    )
    user_content = f"{user_message}\n\n{situation_facts}{tool_hint}".rstrip()

    try:
        llm = get_conversational_llm(temperature=0.4)
        output = run_tool_loop(llm, [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ], tools=all_tools or TOOLS)
        output = output.strip()
    except Exception as exc:
        output = (
            f"I'm having trouble connecting to my language model right now ({exc}). "
            "Please check your API keys and try again."
        )

    return AgentResult(
        task_id=task.task_id,
        agent_name=task.agent_name,
        task_type=task.task_type,
        status=ResultStatus.SUCCESS,
        output=output,
        memory_delta={},
    )


def get_agent_spec(name: str) -> AgentSpec:
    if name not in REGISTRY:
        raise KeyError(f"Unknown agent: {name}")
    return REGISTRY[name]
