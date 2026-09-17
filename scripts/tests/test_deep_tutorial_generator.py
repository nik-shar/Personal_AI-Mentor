"""
scripts/tests/test_deep_tutorial_generator.py

Automated test suite for:
1. get_deep_reasoning_llm() factory check
2. 2-Phase Deep Tutorial Generation (LLM Architect + Deep Node Expander Loop)
3. Curriculum note quality verification

Drives the REAL decomposer, so it sandboxes the curriculum root before importing
config — the agent writes through the roadmap store now, and without this the
suite would create a throwaway roadmap in the live curriculum.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure root workspace directory is in python path
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
from orchestrator.llm import get_deep_reasoning_llm
from schemas import AgentTask, MemorySlice, ResultStatus, TaskSource


def test_deep_generator():
    print("=== 1. Testing get_deep_reasoning_llm() Client Setup ===")
    try:
        llm = get_deep_reasoning_llm(temperature=0.2)
        print(f"✅ LLM Client initialized: {type(llm).__name__}")
    except Exception as exc:
        print(f"❌ Failed to initialize deep reasoning LLM: {exc}")
        raise

    print("\n=== 2. Running 2-Phase Goal Decomposer for 'DSPy Prompt Optimizers' ===")
    task = AgentTask(
        task_id="test_deep_decomp_task",
        source=TaskSource.CHAT,
        agent_name="goal_decomposer",
        task_type="decompose_goal",
        instructions="Create a 2-day roadmap for DSPy Prompt Optimizers",
        params={"topic": "DSPy Prompt Optimizers", "days": 2, "hours_per_day": 2.0},
        memory_slice=MemorySlice(agent_name="goal_decomposer", task_type="decompose_goal", relevant_profile={}),
    )

    initial_state = build_initial_state(task)
    final_state = goal_decomposer_app.invoke(initial_state)

    res = final_state["result"]
    assert res.status == ResultStatus.SUCCESS, f"Expected SUCCESS, got {res.status}. Output: {res.output}"
    print("✅ 2-Phase Agent Execution Completed Successfully!")

    written_files = final_state.get("written_files") or []
    assert len(written_files) > 0, "Agent must write markdown tutorial files to vault"
    print(f"✅ Generated {len(written_files)} markdown tutorial file(s) in Obsidian vault:")

    for file_path_str in written_files:
        p = Path(file_path_str)
        assert p.exists(), f"File {p} must exist on disk"
        content = p.read_text(encoding="utf-8")
        print(f"  - 📄 {p.name} ({len(content)} bytes)")

        # Verify key tutorial sections
        assert len(content) > 500, f"File {p.name} is too short ({len(content)} bytes)"
        assert "python" in content.lower() or "code" in content.lower(), f"Missing code snippet in {p.name}"

    print("✅ All generated markdown files verified for rich tutorial sections & code snippets!")

    # No cleanup needed: the curriculum was sandboxed to a temp dir that is
    # removed at process exit, so nothing here can touch a live vault.
    print("✅ Sandbox removed on exit (no live vault touched).")

    print("\n🎉 ALL 2-PHASE DEEP TUTORIAL GENERATOR TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_deep_generator()
