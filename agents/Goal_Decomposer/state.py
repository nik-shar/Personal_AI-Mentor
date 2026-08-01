"""
agents/Goal_Decomposer/state.py

LangGraph State for the Goal Decomposer specialist agent.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict
from pydantic import BaseModel, Field

from schemas import AgentResult, AgentTask, ResultStatus


# ---------------------------------------------------------------------------
# Structured Output Pydantic Schemas for LLM
# ---------------------------------------------------------------------------

class SubTopicOutline(BaseModel):
    title: str = Field(description="Clear, concise title of the subtopic")
    estimated_hours: float = Field(description="Estimated study hours for this subtopic (e.g. 1.0, 1.5, 2.0)")
    day: int = Field(description="Target day number (1, 2, 3, etc.) when this subtopic should be studied")
    prerequisite_titles: list[str] = Field(
        default_factory=list,
        description="Exact titles of other subtopics in this list that MUST be completed before studying this subtopic"
    )
    what_to_cover: str = Field(description="Short summary of key concepts to cover in this subtopic")
    content_type: Literal["conceptual", "algorithmic", "hands_on_code", "reference"] = Field(
        default="hands_on_code",
        description="Content classification for Phase 2 tutorial generation: 'conceptual', 'algorithmic', 'hands_on_code', or 'reference'"
    )


class GoalDecompositionOutline(BaseModel):
    graph_title: str = Field(description="Overall title of the learning roadmap (e.g. 'LangGraph Mastery in 5 Days')")
    total_days: int = Field(description="Total number of days planned")
    subtopics: list[SubTopicOutline] = Field(description="Sequential/branching breakdown of all subtopics across the days")
    summary_notes: str = Field(description="High-level strategy notes or overview of this roadmap")


class ConceptualContent(BaseModel):
    concept_overview: str = Field(description="Intuitive real-world analogy followed by a precise formal technical definition.")
    tradeoffs_and_comparisons: str = Field(description="Comparison against alternative approaches or related concepts.")
    common_misconceptions: str = Field(description="2-3 common misconceptions or mistakes learners make about this concept.")
    reflection_prompt: str = Field(description="Specific question or scenario to reason through to check internalization.")
    suggested_resources: list[str] = Field(default_factory=list, description="Documentation links, search terms, or articles.")


class AlgorithmicContent(BaseModel):
    concept_overview: str = Field(description="Intuitive problem framing followed by a formal technical definition.")
    approach_breakdown: str = Field(description="Walkthrough from brute-force to optimal approach explaining the key insight.")
    pseudocode_or_code: str = Field(description="Clear pseudocode or idiomatic Python code for the algorithm.")
    complexity_analysis: str = Field(description="Time and space complexity for each approach with one-line justifications.")
    common_pitfalls: str = Field(description="2-3 specific implementation mistakes (off-by-one, wrong invariant, TLE traps).")
    practice_problem: str = Field(description="One concrete practice problem with difficulty level that exercises this topic.")
    suggested_resources: list[str] = Field(default_factory=list, description="Documentation links, search terms, or articles.")


class HandsOnCodeContent(BaseModel):
    concept_overview: str = Field(description="Intuitive real-world analogy followed by a precise technical definition.")
    code_example: str = Field(description="Complete, production-ready, runnable code snippet with type hints and error handling.")
    common_pitfalls: str = Field(description="2-3 specific production watchouts or developer errors.")
    hands_on_challenge: str = Field(description="Specific practice task (20-40 mins) with concrete input/output requirements.")
    suggested_resources: list[str] = Field(default_factory=list, description="Documentation links, search terms, or articles.")


class ReferenceContent(BaseModel):
    concept_overview: str = Field(description="One or two sentences on what this is and when you'd reach for it.")
    comparison_table: str = Field(description="Markdown table comparing relevant options/variants, or state if single usage.")
    canonical_usage: str = Field(description="Standard, minimal correct code or syntax snippet.")
    common_pitfalls: str = Field(description="1-2 things people commonly get wrong when looking this up.")
    suggested_resources: list[str] = Field(default_factory=list, description="Documentation links, search terms, or articles.")


class SubTopicSpec(BaseModel):
    title: str = Field(description="Clear, concise title of the subtopic")
    estimated_hours: float = Field(description="Estimated study hours for this subtopic")
    day: int = Field(description="Target day number")
    prerequisite_titles: list[str] = Field(default_factory=list)
    what_to_cover: str = Field(description="Short summary of key concepts")
    content_type: str = Field(default="hands_on_code", description="Classification bucket")
    content_details: dict[str, Any] = Field(default_factory=dict, description="Dumped dict of structured pedagogical details")
    suggested_resources: list[str] = Field(default_factory=list)


class GoalDecompositionSpec(BaseModel):
    graph_title: str = Field(description="Overall title of the learning roadmap (e.g. 'LangGraph Mastery in 5 Days')")
    total_days: int = Field(description="Total number of days planned")
    subtopics: list[SubTopicSpec] = Field(description="Sequential/branching breakdown of all subtopics across the days")
    summary_notes: str = Field(description="High-level strategy notes or overview of this roadmap")


# ---------------------------------------------------------------------------
# Agent Subgraph State
# ---------------------------------------------------------------------------

class GoalDecomposerState(TypedDict):
    task: AgentTask

    raw_instructions: str
    action_type: Literal["create", "edit", "delete", "list"]
    target_graph_id: Optional[str]
    target_node_id: Optional[str]
    edit_payload: Optional[dict[str, Any]]

    goal_topic: str
    target_days: Optional[int]
    target_hours_per_day: float

    web_research_summary: str

    preferences: dict[str, Any]
    existing_graphs: list[dict[str, Any]]

    outline: Optional[GoalDecompositionOutline]
    decomposition: Optional[GoalDecompositionSpec]
    written_files: list[str]
    index_file_path: str

    feedback_message: str
    memory_delta: dict[str, Any]
    result: Optional[AgentResult]


def build_initial_state(task: AgentTask) -> GoalDecomposerState:
    """Unpack incoming AgentTask into GoalDecomposerState."""
    profile = task.memory_slice.relevant_profile or {}

    return GoalDecomposerState(
        task=task,
        raw_instructions=(task.instructions or "").strip(),
        action_type="create",
        target_graph_id=None,
        target_node_id=None,
        edit_payload=None,
        goal_topic="",
        target_days=None,
        target_hours_per_day=3.0,
        web_research_summary="",
        preferences=profile.get("preferences") or {},
        existing_graphs=profile.get("topic_graphs") or [],
        outline=None,
        decomposition=None,
        written_files=[],
        index_file_path="",
        feedback_message="",
        memory_delta={},
        result=None,
    )


def build_result(state: GoalDecomposerState) -> AgentResult:
    """Package final GoalDecomposerState into AgentResult."""
    task = state["task"]
    success = bool(state.get("written_files"))

    return AgentResult(
        task_id=task.task_id,
        agent_name=task.agent_name,
        task_type=task.task_type,
        status=ResultStatus.SUCCESS if success else ResultStatus.FAILED,
        output=state.get("feedback_message", ""),
        memory_delta=state.get("memory_delta", {}),
    )
