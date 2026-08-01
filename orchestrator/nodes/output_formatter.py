"""
orchestrator/nodes/output_formatter.py

Turns an AgentResult into a user-facing response, preserving any draft
suggestions for later confirmation.
"""

from __future__ import annotations

from schemas import AgentResult, ResultStatus


def format_output(result: AgentResult) -> tuple[str, str]:
    """Return (response_text, status) for the user."""
    if result.status == ResultStatus.FAILED:
        return (
            f"Something went wrong with {result.agent_name}: "
            f"{result.error_message or 'unknown error'}. "
            "Want to try again with more details?",
            "failed",
        )

    if result.status == ResultStatus.NEEDS_CLARIFICATION:
        return (
            result.clarification_needed
            or "I need a bit more context to help. What should I know?",
            "needs_clarification",
        )

    output = result.output or f"{result.agent_name} completed."

    if result.draft_suggestions:
        suggestions_text = "\n\n".join(
            f"--- Draft suggestion ({s.kind}) ---\n{s.content}"
            for s in result.draft_suggestions
        )
        output = f"{output}\n\n{suggestions_text}\n\n[ drafts are not actioned automatically ]"

    return output, "done"
