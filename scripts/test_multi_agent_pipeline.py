"""
scripts/test_multi_agent_pipeline.py
"""

import sys
from pathlib import Path
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv()

from orchestrator.orchestrator import run_orchestrator_turn, build_initial_state
from orchestrator.nodes.reasoner import run_reasoner
from orchestrator.memory.store import MemoryManager
from orchestrator.memory.obsidian_graph import delete_topic_graph
from orchestrator.config import OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER

def test_multi_agent_pipeline():
    print("=== 1. Testing Reasoner Multi-Agent Pipeline Decision ===")

    prompt = (
        "hi there, I have an upcoming interview, i have 4 days to prepare, they will mainly focus on Python, "
        "SQL and ML. i have solved 500+ problems on leetcode but in c++, i am familiar with python but need some revision "
        "for DSA and python. in SQL i am a complete beginner so i will need learning as well as practice. for ML i learned it "
        "in past but need revision and the interview will mainly focus on the algorithms related to credit card and finance systems. "
        "so i will need a basic project also to demonstrate them that i have basic knowledge about it. so basically i have 6-8 hours daily "
        "and total 4 hours so totally i have approx 26-30 hours for preparation"
    )

    decision = run_reasoner(prompt)
    print(f"  Decision Action: {decision.action}")
    print(f"  Primary Agent Name: {decision.agent_name}")
    print(f"  Agent Pipeline Chain: {decision.agent_pipeline}")

    assert decision.action == "route", f"Expected action 'route', got {decision.action}"
    assert "goal_decomposer" in decision.agent_pipeline, "Expected goal_decomposer in agent_pipeline"
    assert "daily_planner" in decision.agent_pipeline, "Expected daily_planner in agent_pipeline"
    print("✅ Reasoner cleanly decided on Multi-Agent Pipeline ['goal_decomposer', 'daily_planner']!")

    print("\n=== 2. Testing End-to-End Orchestrator Turn (Goal Decomposer -> Daily Planner) ===")

    session_id = f"test_pipeline_{uuid4().hex[:6]}"
    state = build_initial_state(session_id=session_id)

    # Run the orchestrator turn
    final_state = run_orchestrator_turn(state, prompt, thread_id=session_id)

    results = final_state.get("results") or []
    print(f"  Number of sub-agents executed in turn: {len(results)}")
    for idx, res in enumerate(results, 1):
        print(f"    Step {idx}: {res.agent_name} (Status: {res.status.value})")

    assert len(results) >= 2, f"Expected at least 2 sub-agents executed in pipeline, got {len(results)}"
    executed_agents = [r.agent_name for r in results]
    assert "goal_decomposer" in executed_agents, "goal_decomposer was not executed in pipeline"
    assert "daily_planner" in executed_agents, "daily_planner was not executed in pipeline"

    response_text = final_state.get("response_text") or ""
    print(f"\n  Final Response Preview:\n{response_text[:350]}...\n")

    assert "Helping Agent: goal_decomposer" in response_text or "Roadmap" in response_text
    assert "Helping Agent: daily_planner" in response_text or "📅" in response_text
    print("✅ Multi-Agent Pipeline executed seamlessly in a single turn!")

    # Cleanup test files from vault if created
    try:
        delete_topic_graph(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, "Python SQL ML Interview Prep")
    except Exception:
        pass

    print("\n🎉 MULTI-AGENT PIPELINE TEST PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_multi_agent_pipeline()
