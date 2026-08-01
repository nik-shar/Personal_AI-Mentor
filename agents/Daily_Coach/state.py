"""
agents/Daily_Coach/state.py

Shared LangGraph state for the Daily Coach agent.

Two task types, one graph:
  "build_daily_plan"   → plan_builder branch
  "log_learning_*"     → learning_logger branch
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

from schemas import AgentResult, AgentTask, ResultStatus
from schemas.memory import (
    ActiveLearningPath,
    DailyPlan,
    LearningLogEntry,
    LearningPathEntry,
    Project,
    TopicGraph,
    TopicNode,
)


# ---------------------------------------------------------------------------
# LLM output schemas — strict Pydantic so structured output can't go wrong
# ---------------------------------------------------------------------------

class PlanItemDraft(BaseModel):
    """What the LLM returns for each plan item. Converted to PlanItem after validation."""
    title: str
    category: Literal["learning", "project", "job_search", "linkedin", "health", "admin", "break"]
    priority: Literal["must", "should", "nice-to-have"] = "should"
    duration_min: int = Field(ge=15, description="Must be a multiple of 15.")
    linked_goal: Optional[str] = None   # TopicNode.id
    notes: Optional[str] = None


class PlanResponse(BaseModel):
    """Structured output schema for the planning LLM call."""
    items: list[PlanItemDraft]
    coach_note: Optional[str] = None    # e.g. "Kept light given energy 2/5"


class TopicMatch(BaseModel):
    """One topic extracted from the user's learning report."""
    topic: str = Field(description="Exact string from the candidate topics list.")
    status: Literal["completed", "in_progress", "skipped"]


class LogResponse(BaseModel):
    """Structured output schema for the learning-log LLM call."""
    is_no_learning_day: bool = Field(
        default=False,
        description="True if the user explicitly said they did no learning today.",
    )
    matched: list[TopicMatch] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

class MatchedProgress(TypedDict):
    topic: str
    status: str
    evidence: str


class DailyCoachState(TypedDict):
    # Orchestrator input
    task: AgentTask

    # Parsed inputs
    task_type: str
    date: datetime
    raw_instructions: str
    report_text: str
    available_minutes: int
    energy_level: int

    # Memory — shared by both branches
    active_learning_path: Optional[ActiveLearningPath]
    topic_graphs: list[TopicGraph]
    learning_log: list[LearningLogEntry]
    learning_streak_days: int
    todays_plan: Optional[DailyPlan]

    # Plan-builder extras
    employment_status: Optional[str]
    target_roles: list[str]
    projects: list[Project]
    preferences: dict[str, Any]
    daily_plans: list[DailyPlan]
    extra_tasks: list[dict[str, Any]]
    dropped_tasks: bool
    plan: Optional[DailyPlan]

    # Learning-logger extras
    matched_entries: list[MatchedProgress]
    new_streak_days: int

    # Output
    feedback_message: str
    memory_delta: dict[str, Any]
    result: Optional[AgentResult]


# ---------------------------------------------------------------------------
# Default available-time per energy level (used when user doesn't specify)
# ---------------------------------------------------------------------------

ENERGY_DEFAULT_MINUTES: dict[int, int] = {
    5: 360,
    4: 300,
    3: 240,
    2: 150,
    1: 60,
}


# ---------------------------------------------------------------------------
# Coerce helpers — one source of truth
# ---------------------------------------------------------------------------

def _resolve_date(task: AgentTask) -> datetime:
    raw = task.params.get("date")
    if isinstance(raw, datetime):
        return raw.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw, fmt).replace(hour=0, minute=0, second=0, microsecond=0)
            except ValueError:
                continue
    return datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)


def _coerce_active_learning_path(value) -> Optional[ActiveLearningPath]:
    if value is None:
        return None
    if isinstance(value, ActiveLearningPath):
        return value
    if isinstance(value, dict):
        return ActiveLearningPath(**value)
    return None


def _coerce_daily_plan(value) -> Optional[DailyPlan]:
    if value is None:
        return None
    if isinstance(value, DailyPlan):
        return value
    if isinstance(value, dict):
        return DailyPlan(**value)
    return None


def _coerce_daily_plans(values) -> list[DailyPlan]:
    if not values:
        return []
    out = []
    for v in values:
        if isinstance(v, DailyPlan):
            out.append(v)
        elif isinstance(v, dict):
            try:
                out.append(DailyPlan(**v))
            except Exception:
                pass
    return out


def _coerce_learning_log(entries) -> list[LearningLogEntry]:
    if not entries:
        return []
    out = []
    for e in entries:
        if isinstance(e, LearningLogEntry):
            out.append(e)
        elif isinstance(e, dict):
            try:
                out.append(LearningLogEntry(**e))
            except Exception as exc:
                try:
                    # Attempt coercing with fallback date if missing
                    e_copy = dict(e)
                    if "date" not in e_copy or e_copy["date"] is None:
                        from datetime import datetime, timezone
                        e_copy["date"] = datetime.now(timezone.utc)
                    out.append(LearningLogEntry(**e_copy))
                except Exception as inner_exc:
                    print(f"[Daily_Coach] Warning: Could not coerce learning log dict {e}: {inner_exc}")
    return out


def _coerce_projects(values) -> list[Project]:
    if not values:
        return []
    out = []
    for v in values:
        if isinstance(v, Project):
            out.append(v)
        elif isinstance(v, dict):
            try:
                out.append(Project(**v))
            except Exception as exc:
                print(f"[Daily_Coach] Warning: Could not coerce project dict {v}: {exc}")
    return out


def _coerce_topic_graphs(values) -> list[TopicGraph]:
    if not values:
        return []
    out = []
    for v in values:
        if isinstance(v, TopicGraph):
            out.append(v)
        elif isinstance(v, dict):
            try:
                out.append(TopicGraph.model_validate(v))
            except Exception:
                pass
    return out


def _coerce_preferences(value) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


# ---------------------------------------------------------------------------
# Initial state builder
# ---------------------------------------------------------------------------

def build_initial_state(task: AgentTask) -> DailyCoachState:
    profile = task.memory_slice.relevant_profile or {}
    constraints = task.memory_slice.constraints or {}

    energy_level = task.params.get("energy_override") or profile.get("energy_level") or 3
    if not isinstance(energy_level, int) or not (1 <= energy_level <= 5):
        energy_level = 3

    available_minutes = (
        task.params.get("available_minutes")
        or constraints.get("available_minutes")
        or ENERGY_DEFAULT_MINUTES[energy_level]
    )
    try:
        available_minutes = int(available_minutes)
    except (TypeError, ValueError):
        available_minutes = ENERGY_DEFAULT_MINUTES[energy_level]

    report_text = (task.instructions or "").strip() or (task.params.get("report_text") or "").strip()

    return DailyCoachState(
        task=task,
        task_type=task.task_type or "build_daily_plan",
        date=_resolve_date(task),
        raw_instructions=(task.instructions or "").strip(),
        report_text=report_text,
        available_minutes=available_minutes,
        energy_level=energy_level,

        active_learning_path=_coerce_active_learning_path(profile.get("active_learning_path")),
        topic_graphs=_coerce_topic_graphs(profile.get("topic_graphs") or []),
        learning_log=_coerce_learning_log(profile.get("learning_log", [])),
        learning_streak_days=profile.get("learning_streak_days", 0),
        todays_plan=_coerce_daily_plan(profile.get("todays_plan")),

        employment_status=profile.get("employment_status"),
        target_roles=profile.get("target_roles") or [],
        projects=_coerce_projects(profile.get("projects")),
        preferences=_coerce_preferences(profile.get("preferences")),
        daily_plans=_coerce_daily_plans(profile.get("daily_plans")),
        extra_tasks=task.params.get("extra_tasks") or constraints.get("extra_tasks") or [],
        dropped_tasks=False,
        plan=None,

        matched_entries=[],
        new_streak_days=0,

        feedback_message="",
        memory_delta={},
        result=None,
    )
