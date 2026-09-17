"""
scripts/tests/test_mentor_cognition.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.memory.store import get_memory_manager
from orchestrator.orchestrator import intake_node, reason_node, summarize_node
from orchestrator.state import build_initial_state


def test_cognition():
    print("Ensuring schema...")
    mm = get_memory_manager()
    mm.ensure_schema()
    
    state = build_initial_state()
    state["working_memory"]["user_input"] = "I'm feeling a bit burned out lately, maybe we should ease up. Also what's my plan today?"
    state["memory_manager"] = mm
    
    print("Running intake_node...")
    state = intake_node(state)
    
    print("Running summarize_node...")
    state.update(summarize_node(state))
    print("--- DNA CONTEXT (Rendered) ---")
    print(state["summary_text"])
    
    print("--- REASONING NODE ---")
    res = reason_node(state)
    decision = res["reasoning_decision"]
    print("Action:", decision["action"])
    print("Coaching Mode:", decision.get("coaching_mode"))
    print("Reasoning:", decision["reasoning"])
    print("Agent Name:", decision.get("agent_name"))
    
    print("\nSUCCESS!")

if __name__ == "__main__":
    test_cognition()
