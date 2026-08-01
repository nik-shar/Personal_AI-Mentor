"""
orchestrator/runner.py

Thin compatibility wrapper around the LangGraph-native orchestrator.

All graph logic now lives in orchestrator/orchestrator.py.
This module keeps the OrchestratorRunner class so any existing callers
(tests, __main__.py, scheduler) don't need to change.
"""

from __future__ import annotations

from orchestrator.memory.store import MemoryManager
from orchestrator.orchestrator import run_orchestrator_turn
from orchestrator.state import OrchestratorState, build_initial_state


class OrchestratorRunner:
    """Thin wrapper around run_orchestrator_turn() for backward compatibility."""

    def __init__(self, memory_manager: MemoryManager | None = None) -> None:
        self.memory_manager = memory_manager or MemoryManager()

    def create_state(self, session_id: str) -> OrchestratorState:
        """Create a fresh OrchestratorState for a new session."""
        return build_initial_state(self.memory_manager, session_id)

    def run_turn(self, state: OrchestratorState, user_input: str) -> OrchestratorState:
        """Run one full turn of the mentor loop and return the updated state."""
        return run_orchestrator_turn(state, user_input)
