"""
orchestrator/nodes/intake.py

Captures the user message into working memory at the start of a turn.
"""

from orchestrator.state import OrchestratorState


def intake(state: OrchestratorState, user_input: str) -> OrchestratorState:
    state["working_memory"]["user_input"] = user_input
    state["working_memory"]["turn_count"] += 1
    state["status"] = "idle"
    return state
