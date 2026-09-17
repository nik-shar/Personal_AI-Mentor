"""
scripts/tests/test_guidelines.py

Verification suite for the mentor_agent_guidelines.md integration:

  1. Loader parses all 7 numbered sections from the constitution file
  2. §1 identity anchor + §2/§6 constitution blocks build correctly
  3. §4 per-agent guidelines extract for every registered specialist
  4. build_dna_context injects identity + constitution as deterministic sections
  5. context_builder appends [AGENT OPERATING GUIDELINES] to sub-agent tasks
  6. Fail-open: a missing guidelines file degrades to empty blocks, no crash

Run from project root:
    uv run python scripts/tests/test_guidelines.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.cognition import guidelines

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def main() -> None:
    print("\n[1] Loader parses the constitution file")
    guidelines.reset_cache()
    sections = guidelines._sections()
    check("all 7 sections parsed", sorted(sections.keys()) == ["1", "2", "3", "4", "5", "6", "7"],
          f"got {sorted(sections.keys())}")

    # ------------------------------------------------------------------
    print("\n[2] Identity anchor + constitution blocks")
    identity = guidelines.get_core_identity()
    check("§1 identity non-empty", len(identity) > 100)
    check("§1 contains anchor fact (Turing)", "Turing" in identity)
    check("§1 contains long-horizon goal (robotics MS)",
          "robotics" in identity.lower() and "MS" in identity)
    constitution = guidelines.build_constitution_block()
    check("constitution block has standing-orders header",
          "Operating Constitution" in constitution)
    check("constitution includes §2 principles", "fabricate" in constitution)
    check("constitution includes §6 guardrails", "Guardrails" in constitution)
    identity_block = guidelines.build_identity_block()
    check("identity block has anchor header", "Core Identity" in identity_block)

    # ------------------------------------------------------------------
    print("\n[3] §4 per-agent guidelines extraction")
    for agent, keyword in [
        ("goal_decomposer", "timeframe"),
        ("linkedin_writer", "tone"),
        ("fallback", "clarifying"),
        ("job_hunter", "resume"),
    ]:
        body = guidelines.get_agent_guidelines(agent)
        check(f"{agent}: guidelines found with expected content",
              len(body) > 50 and keyword in body.lower(),
              f"len={len(body)}")
    check("unknown agent → empty string (fail-open)",
          guidelines.get_agent_guidelines("nonexistent_agent") == "")

    # ------------------------------------------------------------------
    print("\n[4] build_dna_context injects constitution + identity deterministically")
    from orchestrator.memory.dna_context import build_dna_context
    from orchestrator.memory.dna_store import get_dna_store
    from orchestrator.memory.store import MemoryManager

    doc = build_dna_context(MemoryManager(), get_dna_store(), "hey, how are you?")
    check("context doc contains 🧭 Core Identity section",
          "🧭 Core Identity" in doc and "Turing" in doc)
    check("context doc contains 📜 Operating Constitution section",
          "📜 Operating Constitution" in doc)
    check("identity appears before memories (deterministic priority)",
          doc.find("🧭 Core Identity") < doc.find("### 🧬 Who Nik Is"),
          f"identity@{doc.find('🧭 Core Identity')} who@{doc.find('### 🧬 Who Nik Is')}")

    # ------------------------------------------------------------------
    print("\n[5] context_builder appends agent operating guidelines to tasks")
    from orchestrator.nodes.context_builder import build_task

    task = build_task(
        MemoryManager(), "test-session", "plan my day",
        "goal_decomposer", "decompose_goal",
    )
    check("task instructions contain [AGENT OPERATING GUIDELINES]",
          "[AGENT OPERATING GUIDELINES" in task.instructions)
    check("task guidelines are the goal_decomposer ones",
          "timeframe" in task.instructions)
    fb_task = build_task(
        MemoryManager(), "test-session", "whatever",
        "fallback", "direct_response",
    )
    check("fallback task carries its own guidelines",
          "clarifying question" in fb_task.instructions)

    # ------------------------------------------------------------------
    print("\n[6] Fail-open when the file is missing")
    guidelines.reset_cache()
    guidelines._CACHE.update({"path": "/nonexistent.md", "mtime": -1, "text": ""})
    original = guidelines.MENTOR_GUIDELINES_PATH
    guidelines.MENTOR_GUIDELINES_PATH = "/nonexistent.md"
    try:
        check("missing file → empty identity", guidelines.get_core_identity() == "")
        check("missing file → empty constitution", guidelines.build_constitution_block() == "")
        check("missing file → empty agent guidelines",
              guidelines.get_agent_guidelines("job_hunter") == "")
    finally:
        guidelines.MENTOR_GUIDELINES_PATH = original
        guidelines.reset_cache()

    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
