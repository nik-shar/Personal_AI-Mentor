"""
scripts/test_adaptive_tutorial.py
"""

import sys
from pathlib import Path
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv()

from agents.Goal_Decomposer.goal_decomposer import app as goal_decomposer_app, build_initial_state
from orchestrator.config import OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER
from orchestrator.memory.obsidian_graph import delete_topic_graph
from schemas import AgentTask, MemorySlice, TaskSource

def test_adaptive_generation():
    print("=== Testing Adaptive Tutorial Note Generation ===")

    task = AgentTask(
        task_id="test_adaptive_task",
        source=TaskSource.CHAT,
        agent_name="goal_decomposer",
        task_type="decompose_goal",
        instructions=(
            "hi there, I have an upcoming interview, i have 4 days to prepare, they will mainly focus on Python, SQL and ML. "
            "i have solved 500+ problems on leetcode but in c++, i am familiar with python but need some revision for DSA and python. "
            "in SQL i am a complete beginner so i will need learning as well as practice. for ML i learned it in past but need revision "
            "and the interview will mainly focus on the algorithms related to credit card and finance systems. so i will need a basic project "
            "also to demonstrate them that i have basic knowledge about it."
        ),
        memory_slice=MemorySlice(
            agent_name="goal_decomposer",
            task_type="decompose_goal",
            relevant_profile={
                "bio_summary": "Nikhil Sharma is a 23-year-old civil engineering graduate with 500+ LeetCode problems solved in C++.",
                "target_roles": ["AI Engineer", "ML Engineer"],
                "working_habits": ["Hands-on builder", "Structured daily plans"],
            },
        ),
    )

    state = build_initial_state(task)
    final_state = goal_decomposer_app.invoke(state)

    written_files = final_state.get("written_files") or []
    print(f"✅ Generated {len(written_files)} files:")
    for f in written_files:
        print(f"  - {f}")

    # Read Python DSA note file and print inspect content
    dsa_files = [f for f in written_files if "python" in f.lower() or "dsa" in f.lower() or "data_structures" in f.lower()]
    if dsa_files:
        target_file = Path(dsa_files[0])
        if target_file.exists():
            content = target_file.read_text(encoding="utf-8")
            print("\n============================================================")
            print(f"📄 GENERATED ADAPTIVE TUTORIAL NOTE ({target_file.name}):")
            print("============================================================")
            print(content[:1500])
            print("\n============================================================\n")

            # Quality assertions
            content_lower = content.lower()
            assert "train" not in content_lower, "Generic train analogy should not be present!"
            assert "dynamicarray(len=" not in content_lower, "Dummy DynamicArray wrapper should not be present!"
            print("🎉 ADAPTIVE QUALITY VERIFICATION PASSED: Boilerplate train analogy & dummy counters eliminated!")

    # Clean up test files
    delete_topic_graph(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, final_state.get("outline").graph_title if final_state.get("outline") else "4-Day Interview Preparation Roadmap")

if __name__ == "__main__":
    test_adaptive_generation()
