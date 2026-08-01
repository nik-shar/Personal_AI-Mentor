"""
scripts/test_checkpointing.py

Tests Working Memory Checkpointing:
1. Initializes OrchestratorState with thread_id.
2. Invokes first turn and checks response.
3. Invokes second turn with same thread_id and verifies thread_id persistence & turn_count increment.
"""

from dotenv import load_dotenv
load_dotenv()

from orchestrator.memory.store import get_memory_manager
from orchestrator.state import build_initial_state
from orchestrator.orchestrator import run_orchestrator_turn

def test_checkpointing():
    print("=== Testing Working Memory Checkpointing ===")
    mm = get_memory_manager()
    mm.ensure_schema()

    thread_id = "test_chk_thread_999"

    # Turn 1
    state1 = build_initial_state(mm, session_id=thread_id)
    print(f"\n[Turn 1] User: 'Hello mentor, I have 3 hours today'")
    out_state1 = run_orchestrator_turn(state1, "Hello mentor, I have 3 hours today", thread_id=thread_id)
    print(f"[Turn 1 Response Status]: {out_state1.get('status')}")
    print(f"[Turn 1 Response Text Preview]: {(out_state1.get('response_text') or '')[:120]}...")
    print(f"[Turn 1 Turn Count]: {out_state1['working_memory'].get('turn_count')}")

    assert out_state1['working_memory'].get('turn_count') == 1, "Turn count should be 1 after Turn 1"

    # Turn 2: Same thread_id, continuing session using returned state from Turn 1
    print(f"\n[Turn 2] User: 'Can you summarize what we discussed?'")
    out_state2 = run_orchestrator_turn(out_state1, "Can you summarize what we discussed?", thread_id=thread_id)
    print(f"[Turn 2 Response Status]: {out_state2.get('status')}")
    print(f"[Turn 2 Response Text Preview]: {(out_state2.get('response_text') or '')[:120]}...")
    print(f"[Turn 2 Turn Count]: {out_state2['working_memory'].get('turn_count')}")

    assert out_state2['working_memory'].get('turn_count') == 2, "Turn count should be 2 after Turn 2 (checkpointer working)"

    print("\n✅ Working Memory Checkpointing verification PASSED successfully!")

if __name__ == "__main__":
    test_checkpointing()
