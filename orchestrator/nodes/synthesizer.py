"""
orchestrator/nodes/synthesizer.py

Response Synthesizer — the mentor's voice layer.

Every agent output (plan, log, goal breakdown, etc.) passes through here before
reaching Nikhil. This node does one job: take structured agent output and rewrite
it in the mentor's natural voice using full Nikhil context.

This is what separates a task-dispatcher from an actual mentor.
The agents own the *logic*. This node owns the *delivery*.
"""

from __future__ import annotations

from typing import Any

from orchestrator.cognition.persona import build_persona_block, get_user_name
from orchestrator.llm import get_conversational_llm
from orchestrator.tracing import component_span, traced

# ---------------------------------------------------------------------------
# Mentor voice — imported from the single source of truth (cognition/persona)
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    return (
        f"You are {get_user_name()}'s personal AI mentor-companion.\n\n"
        + build_persona_block(include_examples=False)
        + """

TASK-SPECIFIC RULES (synthesis):
- The substance comes from the agent output you are given. Your job is the
  framing: one line of personal context around it, honest status, his name
  where it lands naturally.
- If the agent output is already well-formatted (e.g. a numbered list of
  tasks), keep the structure — don't throw away good formatting.
- When a plan is ready, don't just dump it — give him one line of context
  ("given you're job hunting, I front-loaded the DSA block").
- When something failed or is partial, be honest about it, not defensive.
- Keep your synthesis concise. The agent output is the substance. Your job
  is the framing."""
    )


@traced("synthesizer", "llm")
@component_span("synthesizer", tags=["component:synthesizer"])
def synthesize_response(
    agent_output: str,
    agent_name: str,
    task_type: str,
    user_input: str,
    profile_context: dict[str, Any],
    status: str,
) -> str:
    """
    Wrap agent output in the mentor's voice.

    Args:
        agent_output:    The raw text from the agent (plan, log confirmation, etc.)
        agent_name:      Which agent produced this (for context, not display)
        task_type:       The task that was run
        user_input:      What Nikhil originally asked
        profile_context: Compact profile dict for personalisation hints
        status:          "success" | "partial" | "failed"

    Returns:
        A natural, mentor-voiced response string.
    """
    # Build natural context hints from organic memories / profile data
    profile_lines = []
    if isinstance(profile_context, dict):
        for k, v in profile_context.items():
            if v and k not in ("private_memory",):
                profile_lines.append(f"  • {k}: {v}")

    profile_block = "\n".join(profile_lines) if profile_lines else "  (organic memory context active)"

    # Status context for the LLM
    status_note = {
        "success": "The task completed successfully.",
        "partial": "The task completed with minor adjustments.",
        "failed":  "The task hit an error. Acknowledge honestly and offer a simple next step.",
    }.get(status, "The task completed.")

    human_prompt = f"""Nikhil said: "{user_input}"

Task ran: {task_type} (via {agent_name})
Result status: {status_note}

Context & memory hints:
{profile_block}

Agent output to frame in your mentor voice:
---
{agent_output}
---

Now write your mentor response. Keep the substance clear and structured, but deliver it conversationally as Nikhil's mentor — warm, direct, empathetic, and human."""

    try:
        llm = get_conversational_llm(temperature=0.45)
        response = llm.invoke([
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user",   "content": human_prompt},
        ])
        return response.content.strip()
    except Exception as exc:
        # Never crash the pipeline on a synthesizer failure — fall back to raw output.
        print(f"[synthesizer] LLM call failed ({exc}) — returning raw agent output.")
        return agent_output
