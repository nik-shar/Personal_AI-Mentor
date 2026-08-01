"""
orchestrator/nodes/dispatcher.py

Converts the reasoner's decision (and, as a fallback, keyword matches on the
raw user input) into the concrete (agent_name, task_type) that the context
builder needs.
"""

from __future__ import annotations

from orchestrator.config import DEFAULT_TASK_TYPES, ROUTING_HINTS
from orchestrator.nodes.reasoner import ReasoningDecision


def dispatch(
    decision: ReasoningDecision,
    user_input: str,
) -> tuple[str, str]:
    """Return (agent_name, task_type) for the current turn."""
    if decision.action == "route" and decision.agent_name:
        agent_name = decision.agent_name
        task_type = decision.task_type or DEFAULT_TASK_TYPES.get(
            agent_name, "direct_response"
        )
        return agent_name, task_type

    # Fallback routing when the reasoner did not specify, or for direct use.
    lowered = user_input.lower()
    for agent_name, hints in ROUTING_HINTS.items():
        if any(hint in lowered for hint in hints):
            return agent_name, DEFAULT_TASK_TYPES[agent_name]

    return "fallback", DEFAULT_TASK_TYPES["fallback"]
