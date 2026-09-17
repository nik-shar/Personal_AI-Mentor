"""
orchestrator/state.py

LangGraph State for the Orchestrator's mentor loop.

This state is the *top-level* graph state — it lives for one full turn of the
mentor conversation.  It is NOT the same as any sub-agent's internal state.

Turn lifecycle:
    user_input
        → intake_node          (write user_input into working_memory)
        → summarize_node       (build DNA context document)
        → reason_node          (LLM picks: route | direct_response | clarify | nudge)
          ├─ direct_response   → format_output_node → END  (Qwen3-235B w/ full context)
          ├─ clarify_node      → format_output_node → END
          ├─ nudge_node        → format_output_node → END
          └─ dispatch_node
               → context_builder_node   (build AgentTask from memory)
               → agent_executor_node    (run the sub-agent)
               → memory_merger_node     (persist memory_delta)
               → format_output_node     (write response_text)
                    → END

This state is working-memory only: it is NOT persisted across restarts unless
a node explicitly calls memory_manager methods.  Everything resets on each
new `run_orchestrator_turn()` call except what lives in the DB (MemoryManager).
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Optional, TypedDict

from schemas import AgentResult, AgentTask, DraftSuggestion

# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------

class ConversationTurn(TypedDict):
    """One entry in the live session transcript (msgpack-safe plain types)."""

    role: str        # "user" | "mentor"
    content: str
    timestamp: str   # ISO 8601 local time (with UTC offset)


class WorkingMemory(TypedDict):
    """Short-lived in-turn conversation context."""

    session_id: str
    turn_count: int
    user_input: Optional[str]
    last_assistant_message: Optional[str]
    pending_draft_suggestions: list[DraftSuggestion]
    # Live session transcript — appended on every turn (intake adds the user
    # turn, format_output adds the mentor turn), persisted to the
    # conversation_sessions table when the session closes.
    conversation_history: list[ConversationTurn]
    session_started_at: Optional[str]   # ISO timestamp of this session's first turn
    last_turn_at: Optional[str]         # ISO timestamp of the most recent turn
    extra: dict[str, Any]  # escape hatch for nodes to pass arbitrary state


# ---------------------------------------------------------------------------
# Top-level graph state
# ---------------------------------------------------------------------------

class OrchestratorState(TypedDict):
    """Full turn-level state passed through every orchestrator node."""

    # Working memory — updated each turn.
    working_memory: WorkingMemory

    # Optional reference to MemoryManager (kept None in state dict for msgpack checkpointing)
    memory_manager: Optional[Any]

    # Set by summarize_node and reason_node.
    summary_text: Optional[str]
    reasoning_decision: Optional[dict[str, Any]]

    # Set by dispatch_node → context_builder_node → agent_executor_node.
    current_task: Optional[AgentTask]
    results: Annotated[list[AgentResult], add]
    # Pipeline execution tracking for multi-agent chaining (e.g. goal_decomposer -> job_hunter)
    agent_pipeline: list[str]
    pipeline_step: int

    # Final outputs written by format_output_node.
    response_text: Optional[str]
    status: str  # "idle" | "done" | "needs_clarification" | "failed"

    # Per-turn execution trace for the architecture visualizer: an ordered list
    # of {"kind": "node"|"tool", ...} events appended by the node wrapper in
    # orchestrator.py (plain dicts → msgpack/JSON safe). Reset each turn.
    execution_trace: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Factory helpers (mirrors build_initial_state pattern in sub-agents)
# ---------------------------------------------------------------------------

def _build_working_memory(session_id: str) -> WorkingMemory:
    return WorkingMemory(
        session_id=session_id,
        turn_count=0,
        user_input=None,
        last_assistant_message=None,
        pending_draft_suggestions=[],
        conversation_history=[],
        session_started_at=None,
        last_turn_at=None,
        extra={},
    )


def build_initial_state(
    memory_manager: Optional[Any] = None,
    session_id: str = "default_session",
) -> OrchestratorState:
    """
    Build the starting OrchestratorState for a new session.
    """
    return OrchestratorState(
        memory_manager=None,
        working_memory=_build_working_memory(session_id),
        summary_text=None,
        reasoning_decision=None,
        current_task=None,
        results=[],
        agent_pipeline=[],
        pipeline_step=0,
        response_text=None,
        status="idle",
        execution_trace=[],
    )


# Backward-compat alias — runner.py and __main__.py use this name.
create_initial_state = build_initial_state
create_working_memory = _build_working_memory
