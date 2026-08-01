"""
orchestrator/nodes/agent_executor.py

Runs one agent from the registry. In v1 this is a single agent per turn;
the function signature already accepts a batch-style list so fan-out is a
small refactor later.
"""

from __future__ import annotations
from typing import Any
from uuid import uuid4

from schemas import AgentResult, AgentTask, ResultStatus

from orchestrator.registry import get_agent_spec


def execute_task(task: AgentTask) -> AgentResult:
    """Run a single task through its registered agent."""
    try:
        spec = get_agent_spec(task.agent_name)
        return spec.run(task)
    except Exception as exc:
        return _failed_result(task, exc)


def _failed_result(task: AgentTask, exc: Exception) -> AgentResult:
    return AgentResult(
        task_id=task.task_id or str(uuid4()),
        agent_name=task.agent_name,
        task_type=task.task_type,
        status=ResultStatus.FAILED,
        output=f"{task.agent_name} failed: {exc}",
        error_message=str(exc),
    )
