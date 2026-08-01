"""
scripts/test_goal_decomposer_quality.py

Verification script for Goal Decomposer quality validation logic and parallel graph expansion.
"""

import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv()

from agents.Goal_Decomposer.goal_decomposer import validate_tutorial_quality, run_goal_decomposer
from schemas import AgentTask, MemorySlice, TaskSource
from orchestrator.config import OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER


def test_validator_logic():
    print("=== 1. Testing validate_tutorial_quality() Validator Rules ===")

    # Test case A: Valid, comprehensive tutorial markdown
    valid_md = """
# Python Asyncio Basics

> **Graph:** Python Async | **Est. Time:** 2.0h | **Day:** 1

## 1. Concept Overview
Asyncio is a library to write concurrent code using the async/await syntax. It is used as a foundation for multiple Python asynchronous frameworks that provide high-performance network and web-servers, database connection libraries, distributed task queues, etc.

Asyncio is often a perfect fit for IO-bound and high-level structured network code.

```python
import asyncio

async def main():
    print('Hello ...')
    await asyncio.sleep(1)
    print('... World!')

asyncio.run(main())
```

## 2. Trade-Offs & Comparisons
Asyncio vs Threading: Asyncio uses single-threaded cooperative multitasking whereas threading uses OS-level pre-emptive multitasking.

## 3. Common Misconceptions
Asyncio does not make single-threaded CPU-bound calculations faster.

## 4. Reflection Prompt
Explain when you would choose asyncio over multiprocessing.
"""
    is_valid, reason = validate_tutorial_quality(valid_md)
    print(f"  Valid Markdown Check: is_valid={is_valid}, reason='{reason}'")
    assert is_valid, f"Expected valid markdown to pass, but got: {reason}"

    # Test case B: Lazy stub with '...' placeholder
    lazy_md = """
# Python Asyncio Basics

## 1. Concept Overview
...

## 2. Trade-Offs & Comparisons
...
"""
    is_valid, reason = validate_tutorial_quality(lazy_md)
    print(f"  Lazy '...' Placeholder Check: is_valid={is_valid}, reason='{reason}'")
    assert not is_valid, "Expected lazy markdown with '...' to fail validation."
    assert "..." in reason or "brief" in reason

    # Test case C: Brief / truncated stub
    brief_md = "## Overview\nReview core concepts."
    is_valid, reason = validate_tutorial_quality(brief_md)
    print(f"  Brief Content Check: is_valid={is_valid}, reason='{reason}'")
    assert not is_valid, "Expected brief content under 800 chars to fail validation."

    print("✅ All programmatic validator rules passed successfully!\n")


def test_parallel_goal_decomposition():
    print("=== 2. Testing End-to-End Parallel Goal Decomposition ===")
    
    task = AgentTask(
        task_id="test_parallel_decomp_task",
        source=TaskSource.CHAT,
        agent_name="goal_decomposer",
        task_type="decompose_goal",
        instructions="Create a 2-day crash course on FastAPI and Pydantic v2",
        params={"days": 2, "hours_per_day": 3.0, "topic": "FastAPI and Pydantic v2"},
        memory_slice=MemorySlice(agent_name="goal_decomposer", task_type="decompose_goal", relevant_profile={}),
    )

    t0 = time.time()
    result = run_goal_decomposer(task)
    elapsed = time.time() - t0

    print(f"  Execution Time: {elapsed:.2f} seconds")
    print(f"  Result Status: {result.status}")

    assert result.status.value == "success", f"Expected SUCCESS, got {result.status}"
    
    # Check generated files in Obsidian folder for this test roadmap
    topic_dir = Path(OBSIDIAN_VAULT_PATH) / OBSIDIAN_TOPIC_FOLDER
    generated_files = [
        f for f in topic_dir.glob("*.md")
        if "FastAPI" in f.name or "Pydantic" in f.name
    ]
    print(f"  FastAPI/Pydantic Roadmap Topic Files Found: {len(generated_files)}")
    assert len(generated_files) > 0, "Expected generated topic files for FastAPI/Pydantic roadmap."

    # Verify that all files are non-empty and pass quality checks
    stub_count = 0
    for f in generated_files:
        content = f.read_text(encoding="utf-8")
        is_valid, reason = validate_tutorial_quality(content)
        if not is_valid:
            stub_count += 1
            print(f"  ⚠️ Quality Failure in {f.name} ({len(content)} bytes): {reason}")
        else:
            print(f"  ✅ {f.name} ({len(content)} bytes) — Passed quality validation!")

    print(f"  Stub files detected: {stub_count}/{len(generated_files)}")
    assert stub_count == 0, f"Found {stub_count} stub files in vault after validation fix!"

    print("🎉 PARALLEL GOAL DECOMPOSITION & QUALITY VALIDATION SUCCEEDED!")


if __name__ == "__main__":
    test_validator_logic()
    test_parallel_goal_decomposition()

