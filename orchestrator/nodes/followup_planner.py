"""
orchestrator/nodes/followup_planner.py

Placeholder for multi-agent chaining. In v1 the orchestrator stops after
one agent per turn; this stub returns no follow-up so the design seam is
already in place.
"""

from __future__ import annotations
from typing import Optional

from schemas import AgentTask


def plan_followup(current_task: AgentTask, current_result) -> Optional[AgentTask]:
    """No follow-up planned in v1."""
    return None
