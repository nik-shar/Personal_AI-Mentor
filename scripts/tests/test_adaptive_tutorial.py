"""
scripts/tests/test_adaptive_tutorial.py

Drives the REAL decomposer, so it sandboxes the curriculum root first: the agent
writes through the roadmap store now, and without this it would create a
throwaway roadmap in the live curriculum. The sandbox is removed at exit, so no
explicit cleanup is needed.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_TMP_CURRICULUM = tempfile.mkdtemp(prefix="mentor_curriculum_test_")
os.environ["MENTOR_CURRICULUM_PATH"] = _TMP_CURRICULUM
atexit.register(lambda: shutil.rmtree(_TMP_CURRICULUM, ignore_errors=True))

from dotenv import load_dotenv

load_dotenv()

from agents.Goal_Decomposer.goal_decomposer import app as goal_decomposer_app
from agents.Goal_Decomposer.goal_decomposer import build_initial_state
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

    # No cleanup needed: the curriculum was sandboxed to a temp dir that is
    # removed at process exit, so nothing here can touch a real vault.
    print("✅ Sandbox removed on exit (no live vault touched).")

if __name__ == "__main__":
    test_adaptive_generation()
