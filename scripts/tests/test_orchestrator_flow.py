"""
scripts/tests/test_orchestrator_flow.py

Test script to run the orchestrator through a series of inputs and verify routing,
execution, memory merging, and output formatting.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

# Ensure the project root is on sys.path.
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.memory.store import MemoryManager
from orchestrator.runner import OrchestratorRunner


def test_turn(runner, state, user_input: str):
    print("\n" + "="*80)
    print(f"USER INPUT: '{user_input}'")
    print("="*80)
    
    state = runner.run_turn(state, user_input)
    
    print(f"\nDECISION ACTION: {state.get('reasoning_decision', {}).get('action')}")
    print(f"DECISION REASONING: {state.get('reasoning_decision', {}).get('reasoning')}")
    print(f"DECISION AGENT: {state.get('reasoning_decision', {}).get('agent_name')}")
    print(f"DISPATCHED TASK: {state.get('current_task').task_type if state.get('current_task') else None}")
    print(f"DISPATCHED AGENT: {state.get('current_task').agent_name if state.get('current_task') else None}")
    
    print("\nMENTOR RESPONSE:")
    print(state.get("response_text"))
    print("\nSTATUS:", state.get("status"))
    return state

def main() -> None:
    print("Connecting to database...")
    memory_manager = MemoryManager()
    memory_manager.ensure_schema()
    
    runner = OrchestratorRunner(memory_manager)
    session_id = str(uuid4())
    state = runner.create_state(session_id)
    
    print(f"Started session: {session_id}")
    
    # Test 1: Route to LinkedIn Writer
    state = test_turn(runner, state, "write a linkedin post on topic vectorless RAG. I want to show recruiters I can build production AI.")
    
    # Test 2: Route to Learning Monitor
    # Note: Before learning monitor can map it, we might need an active learning path or plan.
    state = test_turn(runner, state, "Today I completed the LangGraph: conditional edges section and did a React deep-dive.")

    # Test 3: Route to Daily Planner
    state = test_turn(runner, state, "Plan my day. I have about 4 hours and good energy.")

if __name__ == "__main__":
    main()
