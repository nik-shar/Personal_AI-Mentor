"""
agents/Daily_Coach/daily_coach.py

LLM-driven agent that handles daily planning and learning progress logging.

Graph:
    START → parse_input → memory_reader → [branch on task_type]
        → plan_builder    → pack_result → END
        → learning_logger → pack_result → END

Design split:
    Code owns:  facts, constraints, prerequisite traversal, streak math, write-backs.
    LLM owns:   judgment — what to prioritize, how long to spend, what the report means.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from schemas import AgentResult, AgentTask, ResultStatus
from schemas.memory import DailyPlan, LearningLogEntry, PlanItem
from agents.Daily_Coach.state import (
    ENERGY_DEFAULT_MINUTES,
    DailyCoachState,
    LogResponse,
    MatchedProgress,
    PlanItemDraft,
    PlanResponse,
    TopicMatch,
    _coerce_active_learning_path,
    build_initial_state,
)

load_dotenv()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_minutes(minutes: int) -> str:
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{minutes}m"


def _same_day(a: datetime, b: datetime) -> bool:
    return (a.year, a.month, a.day) == (b.year, b.month, b.day)


def _path_entries(path) -> list:
    if path is None:
        return []
    if hasattr(path, "entries"):
        return path.entries or []
    if isinstance(path, dict):
        return path.get("entries") or []
    return []


def _get_available_nodes(state: DailyCoachState) -> list:
    """
    Prerequisite traversal — must stay in code (networkx DAG logic).
    Returns in-progress nodes first, then unstarted available nodes.
    """
    from orchestrator.memory.topic_graph import get_available_nodes, get_in_progress_nodes

    in_progress, available = [], []
    for graph in state["topic_graphs"]:
        in_progress.extend(get_in_progress_nodes(graph))
        available.extend(get_available_nodes(graph))

    return in_progress + [n for n in available if n not in in_progress]


def _trim_to_fit(items: list[PlanItem], available: int) -> tuple[list[PlanItem], bool]:
    """
    Safety net: if LLM returned items that exceed available time, drop the
    lowest-priority ones until they fit. Returns (trimmed_items, was_trimmed).
    """
    total = sum(i.duration_min for i in items)
    if total <= available:
        return items, False

    priority_order = {"must": 0, "should": 1, "nice-to-have": 2}
    # Sort so lowest priority (nice-to-have) drops first, breaks before learning
    droppable = sorted(
        [i for i in items if i.priority != "must"],
        key=lambda i: (-priority_order.get(i.priority, 1), i.category == "break"),
    )
    for item in droppable:
        if total <= available:
            break
        items.remove(item)
        total -= item.duration_min

    return items, True


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def parse_input(state: DailyCoachState) -> dict:
    """Normalise inputs. Use LLM-extracted energy and time parameters from the Orchestrator."""
    raw = (state["task"].instructions or "").strip()
    task_params = state["task"].params or {}

    # Extract user's plain text for report text
    user_text = raw
    if "[USER REQUEST]" in raw:
        m_req = re.search(r'The user requested:\s*"?(.+?)"?\s*(?:\[|$)', raw, re.DOTALL)
        if m_req:
            user_text = m_req.group(1).strip()

    user_lower = user_text.lower()
    profile = state["task"].memory_slice.relevant_profile or {}
    todays_plan = profile.get("todays_plan")

    energy = state["energy_level"]
    available = state["available_minutes"]

    # 1. Primary: LLM Reasoner extracted energy_override
    if task_params.get("energy_override") is not None:
        try:
            val = int(task_params["energy_override"])
            if 1 <= val <= 5:
                energy = val
        except (ValueError, TypeError):
            pass

    # 2. Primary: LLM Reasoner extracted available_minutes
    explicit_time = False
    if task_params.get("available_minutes") is not None:
        try:
            val = int(task_params["available_minutes"])
            if val > 0:
                # Single daily plan cannot exceed 14 hours (840 mins).
                # If a multi-day total (e.g. 26-30 hours over 4 days) was extracted, cap today's plan budget to 8h (480 mins).
                if val > 840:
                    val = 480  # Default to 8 hours max for today's single plan
                available = val
                explicit_time = True
        except (ValueError, TypeError):
            pass

    # If no explicit time was provided in this turn, check if a plan for today exists
    if not explicit_time:
        if todays_plan and isinstance(todays_plan, dict) and todays_plan.get("total_available_minutes"):
            available = int(todays_plan["total_available_minutes"])
        elif todays_plan and hasattr(todays_plan, "total_available_minutes") and todays_plan.total_available_minutes:
            available = int(todays_plan.total_available_minutes)
        elif task_params.get("energy_override") is not None:
            available = ENERGY_DEFAULT_MINUTES.get(energy, 240)

    # 3. Fallback: lightweight regex backup if LLM did not set overrides
    if not explicit_time and task_params.get("available_minutes") is None:
        m_time = re.search(r"(\d+(?:\.\d+)?)\s*hours?", user_lower)
        if m_time:
            available = int(float(m_time.group(1)) * 60)
        else:
            m_time = re.search(r"(\d+)\s*mins?", user_lower)
            if m_time:
                available = int(m_time.group(1))

    return {
        "raw_instructions": raw,
        "report_text": user_text or (state["task"].params.get("report_text") or ""),
        "available_minutes": available,
        "energy_level": energy,
        "task_type": state["task"].task_type or "build_daily_plan",
    }


def memory_reader(state: DailyCoachState) -> dict:
    """Unpack MemorySlice into flat state fields used by both branches."""
    profile = state["task"].memory_slice.relevant_profile or {}
    constraints = state["task"].memory_slice.constraints or {}

    from agents.Daily_Coach.state import (
        _coerce_daily_plan,
        _coerce_daily_plans,
        _coerce_learning_log,
        _coerce_preferences,
        _coerce_projects,
        _coerce_topic_graphs,
    )

    prefs = _coerce_preferences(profile.get("preferences"))
    # linkedin_posting_frequency sometimes lives at top level of profile
    if "linkedin_posting_frequency" not in prefs and "linkedin_posting_frequency" in profile:
        prefs["linkedin_posting_frequency"] = profile["linkedin_posting_frequency"]

    return {
        "active_learning_path": _coerce_active_learning_path(profile.get("active_learning_path")),
        "topic_graphs": _coerce_topic_graphs(profile.get("topic_graphs") or []),
        "learning_log": _coerce_learning_log(profile.get("learning_log", [])),
        "learning_streak_days": profile.get("learning_streak_days", 0),
        "todays_plan": _coerce_daily_plan(profile.get("todays_plan")),
        "employment_status": profile.get("employment_status"),
        "target_roles": profile.get("target_roles") or [],
        "projects": _coerce_projects(profile.get("projects")),
        "preferences": prefs,
        "daily_plans": _coerce_daily_plans(profile.get("daily_plans")),
        "extra_tasks": state.get("extra_tasks") or constraints.get("extra_tasks") or [],
        "bio_summary": profile.get("bio_summary"),
        "working_habits": profile.get("working_habits") or [],
        "mindset_notes": profile.get("mindset_notes") or [],
        "long_term_goal": profile.get("long_term_goal"),
        "extra_instructions": profile.get("extra_instructions") or [],
    }


# ---------------------------------------------------------------------------
# Branch A — Plan Builder
# ---------------------------------------------------------------------------

def _build_plan_prompt(state: DailyCoachState, topic_nodes: list) -> str:
    """Build the planning prompt from structured context."""
    energy = state["energy_level"]
    available = state["available_minutes"]
    date_str = state["date"].strftime("%A, %d %b %Y")

    lines = [
        f"Date: {date_str}",
        f"Energy level: {energy}/5  |  Available time: {_format_minutes(available)}",
        "",
    ]

    # What the user actually said (includes any corrections like "I have 8 hours" or "energy 5")
    raw = state.get("raw_instructions", "").strip()
    user_request = raw
    if "[USER REQUEST]" in raw:
        import re as _re
        m = _re.search(r'The user requested:\s*"?(.+?)"?\s*(?:\[|$)', raw, _re.DOTALL)
        user_request = m.group(1).strip() if m else raw
    if user_request:
        lines += [f"User said: \"{user_request}\"", ""]

    # Mentor guidance forwarded from the reasoner (notes_for_agent)
    if "[MENTOR GUIDANCE" in raw:
        import re as _re
        m = _re.search(r'\[MENTOR GUIDANCE[^\]]*\]\s*(.+?)\s*(?:\[|$)', raw, _re.DOTALL)
        if m:
            lines += [f"Mentor Guidance: {m.group(1).strip()}", ""]

    # Extra instructions for helping agent
    if "[EXTRA INSTRUCTIONS FOR HELPING AGENT]" in raw:
        import re as _re
        m = _re.search(r'\[EXTRA INSTRUCTIONS FOR HELPING AGENT\]\s*(.+?)\s*(?:\[|$)', raw, _re.DOTALL)
        if m:
            lines += [f"Tactical Instructions: {m.group(1).strip()}", ""]

    # Working Habits & Focus Patterns
    habits = state.get("working_habits") or []
    if habits:
        habits_str = "; ".join(habits) if isinstance(habits, list) else str(habits)
        lines.append(f"User Working Habits & Focus Patterns: {habits_str}")

    # Mindset & Watchouts
    mindset = state.get("mindset_notes") or []
    if mindset:
        mindset_str = "; ".join(mindset) if isinstance(mindset, list) else str(mindset)
        lines.append(f"User Mindset & Watchouts: {mindset_str}")

    if habits or mindset:
        lines.append("")

    # Learning topics (pre-filtered by prerequisite logic)
    if topic_nodes:
        lines.append("Learning topics ready to study (YOU MUST USE THESE EXACT TITLES AND IDs FOR LEARNING ITEMS):")
        for node in topic_nodes[:5]:  # cap at 5 to keep prompt tight
            status_tag = f"[{node.status}] " if node.status and node.status != "not_started" else ""
            lines.append(f"  - EXACT TITLE: '{node.title}' | ID: '{node.id}' | STATUS: {status_tag} (est. {node.estimated_hours}h)")
    else:
        path = state.get("active_learning_path")
        entries = _path_entries(path)
        todo = [e for e in entries if getattr(e, "status", "not_started") in ("not_started", "in_progress")]
        if todo:
            lines.append("Learning topics from active path:")
            for e in todo[:5]:
                lines.append(f"  - {e.topic}")
        else:
            lines.append("Learning topics: none available (all prerequisites locked or no path set)")

    lines.append("")

    # Projects
    active_projects = [p for p in state["projects"] if getattr(p, "status", None) in ("active", "ACTIVE")]
    if active_projects:
        lines.append("Active projects: " + ", ".join(p.name for p in active_projects[:3]))

    # Job search
    if state["employment_status"] == "unemployed_job_searching":
        targets = ", ".join(state["target_roles"]) if state["target_roles"] else "relevant roles"
        lines.append(f"Job search: actively searching — targeting {targets}")

    # LinkedIn
    linkedin_freq = state["preferences"].get("linkedin_posting_frequency")
    if linkedin_freq:
        lines.append(f"LinkedIn: posting frequency is {linkedin_freq}")

    # Extra/admin tasks — must be included
    if state["extra_tasks"]:
        lines.append("Must-include tasks today:")
        for t in state["extra_tasks"]:
            dur = t.get("duration_minutes", 30)
            lines.append(f"  - {t.get('description', 'Task')} ({dur} min)")

    # Recent learning activity — lets LLM avoid re-scheduling things just done
    recent_log = state.get("learning_log") or []
    if recent_log:
        recent = recent_log[-3:]  # last 3 entries
        lines.append("Recently studied (don't re-schedule these unless explicitly asked):")
        for entry in recent:
            topics = entry.topics if hasattr(entry, "topics") else entry.get("topics", [])
            date = entry.date if hasattr(entry, "date") else entry.get("date", "")
            if isinstance(date, datetime):
                date = date.strftime("%d %b")
            lines.append(f"  - {date}: {', '.join(topics)}")
        lines.append("")

    lines += [
        "",
        "Instructions:",
        "- Build a focused, realistic daily plan that fits within the available time.",
        "- All duration_min values must be multiples of 15.",
        f"- Total of all duration_min values must not exceed {available}.",
        "- Prioritise in-progress learning > job search (if applicable) > project work > new learning > LinkedIn > breaks.",
        "- Include at least one break if energy >= 2.",
        "- If energy is 1, plan mostly rest with at most one light review item.",
        "- CRITICAL RULE FOR LEARNING ITEMS: For any item in category 'learning', set linked_goal to the node's exact ID (from the list above) AND title to the node's exact TITLE. Do NOT invent new topic titles.",
        "- Be specific in project/task titles.",
        "- If the user's message contains corrections (e.g. different time or energy), honour them exactly.",
    ]

    return "\n".join(lines)


def plan_builder(state: DailyCoachState) -> dict:
    """
    LLM builds the plan. Python validates the time constraint, reconciles learning items with Topic Graph node IDs,
    and converts PlanItemDraft → PlanItem (which auto-generates stable IDs).
    """
    from orchestrator.llm import get_reasoning_llm

    topic_nodes = _get_available_nodes(state)
    prompt = _build_plan_prompt(state, topic_nodes)

    llm = get_reasoning_llm(temperature=0.3).with_structured_output(PlanResponse)
    try:
        response: PlanResponse = llm.invoke([
            {
                "role": "system",
                "content": (
                    "You are a personal productivity coach. "
                    "Build a structured daily plan from the context provided. "
                    "Output strictly valid JSON matching the schema."
                ),
            },
            {"role": "user", "content": prompt},
        ])
    except Exception as exc:
        # If the LLM call fails entirely, return a minimal safe plan
        print(f"[daily_coach] plan LLM call failed: {exc}")
        response = PlanResponse(
            items=[PlanItemDraft(title="Rest — plan generation failed, try again", category="break", priority="must", duration_min=state["available_minutes"])],
            coach_note=f"LLM error: {exc}",
        )

    # Convert drafts → PlanItems (gets auto-generated IDs)
    items = [PlanItem(**draft.model_dump()) for draft in response.items]

    # Reconcile learning items strictly against Topic Graph node IDs & titles
    if topic_nodes:
        node_id_map = {n.id: n for n in topic_nodes}
        node_title_map = {n.title.lower().strip(): n for n in topic_nodes}

        for item in items:
            if item.category == "learning":
                matched_node = None
                if item.linked_goal and item.linked_goal in node_id_map:
                    matched_node = node_id_map[item.linked_goal]
                elif item.title.lower().strip() in node_title_map:
                    matched_node = node_title_map[item.title.lower().strip()]
                else:
                    t_clean = item.title.lower().strip()
                    for n in topic_nodes:
                        n_clean = n.title.lower().strip()
                        if t_clean in n_clean or n_clean in t_clean:
                            matched_node = n
                            break

                if matched_node:
                    item.linked_goal = matched_node.id
                    item.title = matched_node.title
                else:
                    top_node = topic_nodes[0]
                    item.linked_goal = top_node.id
                    item.title = top_node.title

    # Safety net: trim if LLM overran the time budget
    items, was_trimmed = _trim_to_fit(items, state["available_minutes"])

    plan = DailyPlan(
        date=state["date"],
        generated_by="daily_coach",
        energy_level_assumed=state["energy_level"],
        total_available_minutes=state["available_minutes"],
        items=items,
        notes=response.coach_note,
    )

    # Format for the user
    EMOJI = {"learning": "📚", "linkedin": "💼", "job_search": "🔍", "project": "🛠️", "health": "🏃", "admin": "📝", "break": "☕"}
    PRIORITY_FLAG = {"must": "[MUST] ", "should": "", "nice-to-have": "[optional] "}

    out = [
        f"📅 {state['date'].strftime('%A, %d %b')}  |  Energy {state['energy_level']}/5  |  {_format_minutes(state['available_minutes'])} available",
        "",
    ]
    for i, item in enumerate(items, 1):
        emoji = EMOJI.get(item.category, "•")
        flag = PRIORITY_FLAG.get(item.priority, "")
        link = " 🔗" if item.linked_goal else ""
        out.append(f"{i}. {emoji} {flag}{item.title}{link}  ({item.duration_min}m)")

    if plan.notes:
        out += ["", f"💬 {plan.notes}"]
    if was_trimmed:
        out += ["", "⚠️  Some lower-priority items were trimmed to fit your available time."]
    out += ["", 'Say "add task X", "swap learning and project", or "I only have 2 hours" to adjust.']
    if any(i.linked_goal for i in items):
        out.append("(🔗 = linked to your Topic Graph — marking done updates your learning progress)")

    # Persist energy_level if the user overrode it this turn so the
    # orchestrator remembers it next time instead of loading the stale DB value.
    memory_delta: dict = {"daily_plans": [plan.model_dump()]}
    profile_energy = state["task"].memory_slice.relevant_profile.get("energy_level")
    if state["energy_level"] != profile_energy:
        memory_delta["energy_level"] = state["energy_level"]

    return {
        "plan": plan,
        "dropped_tasks": was_trimmed,
        "feedback_message": "\n".join(out),
        "memory_delta": memory_delta,
    }


# ---------------------------------------------------------------------------
# Branch B — Learning Logger
# ---------------------------------------------------------------------------

def _collect_candidate_topics(state: DailyCoachState) -> list[str]:
    """Gather all known topic titles the LLM can match against."""
    seen: set[str] = set()
    candidates: list[str] = []

    def _add(title: str) -> None:
        if title and title not in seen:
            seen.add(title)
            candidates.append(title)

    for graph in state.get("topic_graphs", []):
        nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", {})
        for node in nodes.values():
            _add(node.title if hasattr(node, "title") else node.get("title", ""))

    plan = state.get("todays_plan")
    if plan:
        for item in (plan.items if hasattr(plan, "items") else []):
            cat = item.category if hasattr(item, "category") else item.get("category", "")
            if cat == "learning":
                _add(item.title if hasattr(item, "title") else item.get("title", ""))

    for entry in _path_entries(state.get("active_learning_path")):
        _add(entry.topic if hasattr(entry, "topic") else entry.get("topic", ""))

    return candidates


def _build_log_prompt(report: str, candidates: list[str]) -> str:
    topics_str = "\n".join(f"  - {t}" for t in candidates) if candidates else "  (no topics in system yet)"
    return (
        f"The user sent this learning report:\n\"{report}\"\n\n"
        f"Known topics:\n{topics_str}\n\n"
        "Your job:\n"
        "1. If the user clearly said they did no learning today, set is_no_learning_day=true.\n"
        "2. Otherwise, match what they studied to the known topics list above.\n"
        "   - topic must be the exact string from the list.\n"
        "   - status: completed / in_progress / skipped.\n"
        "   - It's fine to return an empty matched list if nothing fits.\n"
        "3. Do not invent topics not in the list."
    )


def _apply_progress_to_path(entries: list, matched: list[MatchedProgress], today: datetime) -> Optional[list]:
    """Advance learning-path entry statuses. Returns None if nothing changed."""
    matched_map = {m["topic"].lower(): m["status"] for m in matched}
    changed = False
    new_entries = []

    for entry in entries:
        key = entry.topic.lower() if hasattr(entry, "topic") else entry.get("topic", "").lower()
        if key in matched_map:
            new_status = matched_map[key]
            if new_status != (entry.status if hasattr(entry, "status") else entry.get("status")):
                changed = True
                new_entry = entry.model_copy(deep=True)
                new_entry.status = new_status
                if new_status == "completed" and new_entry.completed_date is None:
                    new_entry.completed_date = today
                new_entries.append(new_entry)
                continue
        new_entries.append(entry)

    return new_entries if changed else None


def _apply_progress_to_obsidian(task: AgentTask, matched: list[MatchedProgress]) -> None:
    """Write updated node statuses back into Obsidian markdown frontmatter."""
    from orchestrator.config import OBSIDIAN_TOPIC_FOLDER, OBSIDIAN_VAULT_PATH
    from orchestrator.memory.obsidian_graph import load_topic_graphs, update_node_status

    graphs = (task.memory_slice.relevant_profile or {}).get("topic_graphs") or []
    if not graphs:
        # Load topic graphs directly from Obsidian vault on demand
        graphs = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)

    if not graphs:
        return

    for m in matched:
        title_lower = m["topic"].strip().lower()
        target = "done" if m["status"] in ("completed", "done") else m["status"]
        for graph in graphs:
            nodes = graph.nodes if hasattr(graph, "nodes") else (graph.get("nodes", {}) if isinstance(graph, dict) else {})
            for node_id, node in nodes.items():
                node_title = (node.title if hasattr(node, "title") else node.get("title", "")).strip().lower()
                if node_title == title_lower or title_lower in node_title or node_title in title_lower:
                    update_node_status(
                        vault_path=OBSIDIAN_VAULT_PATH,
                        folder=OBSIDIAN_TOPIC_FOLDER,
                        node_id=str(node_id),
                        new_status=target,
                    )


def learning_logger(state: DailyCoachState) -> dict:
    """
    LLM extracts what the user studied from their free-text report.
    Python handles streak arithmetic and all write-backs.
    """
    from orchestrator.llm import get_reasoning_llm

    report = state["report_text"].strip()
    today = state["date"]
    current_streak = state["learning_streak_days"]
    learning_log = state["learning_log"]
    path = state["active_learning_path"]

    candidates = _collect_candidate_topics(state)
    already_today = any(_same_day(e.date, today) for e in learning_log)

    # LLM call — structured output, returns is_no_learning_day + matched topics
    llm = get_reasoning_llm(temperature=0.1).with_structured_output(LogResponse)
    try:
        response: LogResponse = llm.invoke([
            {
                "role": "system",
                "content": (
                    "You are a learning-log assistant. "
                    "Extract learning progress from the user's report. Output valid JSON only."
                ),
            },
            {"role": "user", "content": _build_log_prompt(report, candidates)},
        ])
    except Exception as exc:
        print(f"[daily_coach] log LLM call failed: {exc}")
        response = LogResponse(is_no_learning_day=False, matched=[])

    # Normalise matches into internal format
    matched: list[MatchedProgress] = [
        {"topic": m.topic, "status": m.status, "evidence": report}
        for m in response.matched
    ]

    # --- Streak + memory_delta logic (pure Python — deterministic) ---
    if response.is_no_learning_day:
        new_streak = 0
        memory_delta: dict = {"learning_streak_days": 0}
        feedback = "No learning logged today. That's fine — rest is part of the process. 🔄 Streak reset to 0."

    elif matched and not already_today:
        new_streak = current_streak + 1
        new_entry = LearningLogEntry(
            date=today,
            topics=[m["topic"] for m in matched],
            source="self_report",
            confirmed_by_user=True,
        )
        memory_delta = {
            "learning_log": [new_entry.model_dump()],
            "learning_streak_days": new_streak,
        }
        # Update learning path entry statuses
        if path:
            updated = _apply_progress_to_path(_path_entries(path), matched, today)
            if updated is not None:
                memory_delta["active_learning_path"] = {
                    **path.model_dump(),
                    "entries": [e.model_dump() for e in updated],
                }
        # Write back to Obsidian
        _apply_progress_to_obsidian(state["task"], matched)

        topics_str = ", ".join(m["topic"] for m in matched)
        statuses = {m["status"] for m in matched}
        note = " (in progress)" if statuses == {"in_progress"} else (" (skipped)" if statuses == {"skipped"} else "")
        feedback = f"✅ Logged{note}: {topics_str}.\n🔥 Streak: {new_streak} day{'s' if new_streak != 1 else ''}."

    elif matched and already_today:
        new_streak = current_streak
        memory_delta = {"learning_streak_days": new_streak}
        feedback = f"Already logged learning today — streak stays at {new_streak}. 👍"

    else:
        new_streak = current_streak
        memory_delta = {"learning_streak_days": new_streak}
        feedback = (
            f"Couldn't match your report to any known topic. "
            f"Streak preserved at {new_streak}. "
            "Try rephrasing to match a topic name, e.g. 'finished LangGraph conditional edges'."
        )

    return {
        "matched_entries": matched,
        "new_streak_days": new_streak,
        "memory_delta": memory_delta,
        "feedback_message": feedback,
    }


# ---------------------------------------------------------------------------
# Shared result packer
# ---------------------------------------------------------------------------

def pack_result(state: DailyCoachState) -> dict:
    task = state["task"]
    is_plan = state["task_type"] == "build_daily_plan"

    if is_plan:
        ok = state.get("plan") is not None and not state.get("dropped_tasks")
    else:
        ok = bool(state.get("matched_entries")) or state.get("new_streak_days", -1) == 0

    return {
        "result": AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            task_type=task.task_type,
            status=ResultStatus.SUCCESS if ok else ResultStatus.PARTIAL,
            output=state.get("feedback_message", ""),
            memory_delta=state.get("memory_delta", {}),
        )
    }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _route(state: DailyCoachState) -> str:
    return "learning_logger" if state["task_type"] in (
        "log_learning_session", "learning_report", "log_learning"
    ) else "plan_builder"


_builder = StateGraph(DailyCoachState)
_builder.add_node("parse_input", parse_input)
_builder.add_node("memory_reader", memory_reader)
_builder.add_node("plan_builder", plan_builder)
_builder.add_node("learning_logger", learning_logger)
_builder.add_node("pack_result", pack_result)

_builder.add_edge(START, "parse_input")
_builder.add_edge("parse_input", "memory_reader")
_builder.add_conditional_edges("memory_reader", _route, {
    "plan_builder": "plan_builder",
    "learning_logger": "learning_logger",
})
_builder.add_edge("plan_builder", "pack_result")
_builder.add_edge("learning_logger", "pack_result")
_builder.add_edge("pack_result", END)

app = _builder.compile()


# ---------------------------------------------------------------------------
# Public entry points — same signatures as old agents, registry unchanged
# ---------------------------------------------------------------------------

def run_daily_planner(task: AgentTask) -> AgentResult:
    """Run the daily planning branch."""
    task = task.model_copy(update={"task_type": "build_daily_plan"})
    return app.invoke(build_initial_state(task))["result"]


def run_learning_monitor(task: AgentTask) -> AgentResult:
    """Run the learning-log branch."""
    task = task.model_copy(update={"task_type": "log_learning_session"})
    return app.invoke(build_initial_state(task))["result"]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from schemas import MemorySlice, TaskSource

    print("=== build_daily_plan ===")
    plan_task = AgentTask(
        task_id="dc-plan-1",
        agent_name="daily_planner",
        task_type="build_daily_plan",
        instructions="I have 4 hours today.",
        source=TaskSource.CHAT,
        memory_slice=MemorySlice(
            agent_name="daily_planner",
            task_type="build_daily_plan",
            relevant_profile={
                "energy_level": 4,
                "employment_status": "unemployed_job_searching",
                "target_roles": ["AI Engineer", "ML Engineer"],
                "active_learning_path": {
                    "title": "LangGraph for Production",
                    "current_week": 1,
                    "entries": [
                        {"topic": "LangGraph: conditional edges", "week": 1, "order": 1, "status": "in_progress"},
                        {"topic": "Chroma vector store basics", "week": 1, "order": 2, "status": "not_started"},
                    ],
                },
            },
        ),
    )
    print(run_daily_planner(plan_task).output)

    print("\n=== log_learning_session ===")
    log_task = AgentTask(
        task_id="dc-log-1",
        agent_name="learning_monitor",
        task_type="log_learning_session",
        instructions="Today I finished the LangGraph conditional edges section.",
        source=TaskSource.CHAT,
        memory_slice=MemorySlice(
            agent_name="learning_monitor",
            task_type="log_learning_session",
            relevant_profile={
                "active_learning_path": {
                    "title": "LangGraph for Production",
                    "current_week": 1,
                    "entries": [
                        {"topic": "LangGraph: conditional edges", "week": 1, "order": 1, "status": "in_progress"},
                    ],
                },
                "learning_log": [],
                "learning_streak_days": 3,
            },
        ),
    )
    result = run_learning_monitor(log_task)
    print(result.output)
    print(f"memory_delta keys: {list(result.memory_delta.keys())}")
