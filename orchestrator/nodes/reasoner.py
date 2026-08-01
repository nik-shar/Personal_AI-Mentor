"""
orchestrator/nodes/reasoner.py

The mentor's reasoning core. Reads a compact state summary and decides
whether to route to a sub-agent, ask a clarifying question, or proactively
nudge Nikhil before any routing happens.
"""

from __future__ import annotations
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from orchestrator.llm import get_reasoning_llm


class ReasoningDecision(BaseModel):
    action: Literal["route", "ask_clarifying_question", "proactive_nudge"] = Field(
        ...,
        description="The high-level strategy for this turn.",
    )
    reasoning: str = Field(
        ...,
        description="Short explanation of why this action was chosen.",
    )
    context_sufficiency_score: int = Field(
        default=3,
        ge=1,
        le=3,
        description="1 = Insufficient/Ambiguous context (must ask 1-2 questions now). 2 = Marginally sufficient. 3 = Sufficient (proceed to dispatch/synthesize).",
    )
    sufficiency_reasoning: Optional[str] = Field(
        default=None,
        description="Why the context was scored 1, 2, or 3.",
    )
    agent_name: Optional[str] = Field(
        default=None,
        description="Target agent if action is 'route'.",
    )
    agent_pipeline: list[str] = Field(
        default_factory=list,
        description="Ordered list of agent names to execute sequentially in a multi-agent pipeline (e.g. ['goal_decomposer', 'daily_planner']). If non-empty, agent_name should match the first item in the list.",
    )
    task_type: Optional[str] = Field(
        default=None,
        description="Specific task type to send to the agent if action is 'route'.",
    )
    notes_for_agent: Optional[str] = Field(
        default=None,
        description="High-level mentor strategy/guidance (passed as part of task instructions).",
    )
    extra_instructions: Optional[str] = Field(
        default=None,
        description="Specific tactical instructions for the helping agent (e.g., 'Break down topics into tutorial-style explanations', 'Keep initial block to 45m hands-on building to avoid over-engineering').",
    )
    parsed_energy_level: Optional[int] = Field(
        default=None,
        description="Intelligent 1-5 energy assessment inferred from the user's message (e.g. 'fully energetic' -> 5, 'feeling great' -> 4, 'okay' -> 3, 'tired' -> 2, 'exhausted' -> 1). Set null if energy is not mentioned or implied.",
    )
    parsed_available_minutes: Optional[int] = Field(
        default=None,
        description="Intelligent time calculation in minutes inferred from user's message (e.g. '3 hours' -> 180, 'half an hour' -> 30, '90 mins' -> 90). Set null if available time is not mentioned.",
    )
    target_questions_now: list[str] = Field(
        default_factory=list,
        description="Max 1-2 highest-value questions to ask the user immediately if score == 1 (e.g. gut-check on active blocker). Hard capped at 2 questions.",
    )
    queued_questions: list[str] = Field(
        default_factory=list,
        description="Non-urgent questions noticed during reasoning to save in open_questions memory tier for opportunistic later use.",
    )
    clarifying_question: Optional[str] = Field(
        default=None,
        description="Concise 1-2 question message to present if action is 'ask_clarifying_question'.",
    )
    proactive_message: Optional[str] = Field(
        default=None,
        description="Mentor message to share if action is 'proactive_nudge'.",
    )
    needs_long_term_context: bool = Field(
        default=False,
        description=(
            "Set to True ONLY when the user's message requires historical context "
            "beyond the last 7 days — e.g. 'how have I been doing overall?', "
            "'am I making progress?', 'remind me what I learned last month', "
            "'I feel stuck'. Set False for routine tasks like planning today, "
            "logging learning, or writing a LinkedIn post."
        ),
    )


_REASONING_SYSTEM_PROMPT = """You are the reasoning core of Nikhil's personal AI mentor-companion.

Your job is to evaluate Nikhil's message against his profile context using a strict 1-2-3 Context Sufficiency Rubric before deciding whether to act or ask.

=============================================================================
CONTEXT SUFFICIENCY RUBRIC (1 - 3)
=============================================================================

Score 1 (INSUFFICIENT CONTEXT — MUST ASK NOW):
- Trigger: The user makes an open-ended, discovery, or direction-setting request (e.g., "Help me figure out what I should focus on next — learning, job search, whatever", "I'm stuck", "Where should I start?") AND profile context does NOT contain active goals or learning paths.
- Why: A static profile bio tells you who Nikhil is generally, but NOT what is happening right now when no active goals exist.
- Decision: Action = "ask_clarifying_question", context_sufficiency_score = 1.
- Rule for Questions: Ask AT MOST 1-2 highest-value gut-check questions in `target_questions_now` / `clarifying_question`. Never ask a long form.
- Question Queueing: Any non-urgent questions MUST be put into `queued_questions` for memory storage, NOT asked now.

Score 3 (SUFFICIENT CONTEXT — ROUTE & ACT):
- Trigger 1: Routine Planning & Scheduling ("plan my day", "what's my plan", "schedule today", "what should I do today", "I have N hours").
  CRITICAL RULE: If the profile summary contains active goals, target roles, or an active learning path/topics, DO NOT ask blank-slate clarifying questions like "What are your top priorities?" or "How much time do you have?".
  Decision: Action = "route", agent_name = "daily_planner", task_type = "build_daily_plan", context_sufficiency_score = 3.
  notes_for_agent: "Build a concrete candidate daily plan using profile goals and energy defaults. Invite user to adjust time or energy."

- Trigger 2: Concrete User Signals (e.g., "I applied to 20 roles, only 2 interviews", "Log my study session on LangGraph").
  Decision: Action = "route", agent_name = target_agent, context_sufficiency_score = 3.

- Trigger 3: Multi-Day Learning Goal & Interview Preparation + Schedule Request (e.g. "I have 4 days to prepare for an interview...", "Create a roadmap for X and schedule today").
  CRITICAL RULE: Set agent_pipeline to ["goal_decomposer", "daily_planner"] so goal_decomposer builds the 4-day Obsidian Vault roadmap first, and daily_planner immediately uses Day 1 to build today's 6-8h schedule.
  Decision: Action = "route", agent_name = "goal_decomposer", agent_pipeline = ["goal_decomposer", "daily_planner"], context_sufficiency_score = 3.
  notes_for_agent: "First create the 4-day Obsidian Vault roadmap & tutorial nodes for interview prep, then pass to daily_planner to schedule Day 1 for today."

FEW-SHOT EXAMPLE (Score 3 - Planning Query):
User: "Plan my day"
Reasoning: "User requested daily plan. Profile contains active goals and learning path topics. Context is sufficient for candidate plan."
context_sufficiency_score: 3
action: "route"
agent_name: "daily_planner"
task_type: "build_daily_plan"
notes_for_agent: "Build a focused daily plan prioritizing active learning path topics, job search, and project work."

FEW-SHOT EXAMPLE (Score 1 - Open-ended Strategy):
User: "Help me figure out what I should focus on next — learning, job search, whatever."
Reasoning: "Open-ended strategy request. Profile doesn't reveal current active friction."
context_sufficiency_score: 1
action: "ask_clarifying_question"
clarifying_question: "Quick gut-check before I suggest anything — is this more about the job search feeling stuck, or getting sharper at something specific?"
target_questions_now: ["Is this more about job search feeling stuck or getting sharper at something specific?"]

=============================================================================
PARAMETER EXTRACTION & ROUTING
=============================================================================

- `parsed_available_minutes`: Infer time in minutes for TODAY's single daily plan only (e.g. "3 hours" -> 180, "6-8 hours daily" -> 480). Null if unmentioned.
  * IMPORTANT: Never pass a multi-day total budget (e.g. "26 hours over 4 days") into parsed_available_minutes for today's daily plan. Extract today's daily allocation (max 480-720 mins).

When routing to an agent (Score 3):
- `agent_name`: MUST be one of the following valid agent names ONLY:
  - "daily_planner" (for scheduling today's single-day time/plan)
  - "learning_monitor" (for logging completed study/learning sessions)
  - "linkedin_writer" (for drafting technical LinkedIn posts)
  - "goal_decomposer" (for multi-day learning goals, multi-day interview preparation roadmaps, creating/editing/deleting/listing Obsidian topic graphs or vault roadmaps)
  - "fallback" (for general advice, profile queries, or unrouted chat)
- `notes_for_agent`: High-level mentor strategy.
- `extra_instructions`: Detailed tactical instructions for the sub-agent.

Be concise, calibrated, and mentor-grade."""


def run_reasoner(summary_text: str) -> ReasoningDecision:
    llm = get_reasoning_llm(temperature=0.2)
    structured_llm = llm.with_structured_output(ReasoningDecision)
    raw = structured_llm.invoke(
        [
            {"role": "system", "content": _REASONING_SYSTEM_PROMPT},
            {"role": "user", "content": summary_text},
        ]
    )
    if isinstance(raw, ReasoningDecision):
        return raw
    if isinstance(raw, dict):
        return ReasoningDecision(**raw)
    raise TypeError(f"Unexpected reasoner output type: {type(raw)}")


def decision_to_dict(decision: ReasoningDecision) -> dict[str, Any]:
    return decision.model_dump()
