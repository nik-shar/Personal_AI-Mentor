"""
scripts/test_procedural_memory.py

Tests Procedural Memory tier:
1. Adding a procedural rule and mindset calibration to MemoryManager.
2. Verifying context_builder pulls procedural memory into AgentTask instructions.
3. Verifying memory_merger persists procedural_rules delta into PostgreSQL.
"""

from dotenv import load_dotenv
load_dotenv()

from orchestrator.memory.store import get_memory_manager
from orchestrator.nodes.context_builder import build_task
from orchestrator.nodes.memory_merger import apply_memory_delta
from schemas import AgentResult, ResultStatus, ProceduralRule, MindsetCalibration

def test_procedural_memory():
    print("=== Testing Procedural Memory Tier ===")
    mm = get_memory_manager()
    mm.ensure_schema()

    # 1. Upsert a procedural rule & calibration
    rule = {
        "domain": "planning",
        "rule": "Keep morning study blocks under 45m when energy is below 3",
        "confidence": 0.95,
        "status": "active"
    }
    mm.upsert_procedural_rule(rule, source="test_script")
    
    cal = {
        "directness": "blunt",
        "nudge_frequency_cap": "max 1x/day",
        "framing_that_lands": ["bullet points", "concrete action items"],
        "framing_that_bounces": ["fluff", "generic praise"]
    }
    mm.set_mindset_calibration(cal, source="test_script")

    # 2. Check build_task formatting
    task = build_task(
        memory_manager=mm,
        session_id="test_proc_session",
        user_input="Plan my day today",
        agent_name="daily_planner",
        task_type="build_daily_plan"
    )

    print("\n[Generated Instructions Preview]")
    print(task.instructions)

    assert "[PROCEDURAL RULES & USER PREFERENCES]" in task.instructions, "Procedural header missing from instructions"
    assert "Keep morning study blocks under 45m" in task.instructions, "Procedural rule text missing from instructions"
    assert "Communication Directness: blunt" in task.instructions, "Calibration directness missing from instructions"
    assert "Effective Framing: bullet points, concrete action items" in task.instructions, "Effective framing missing"

    # 3. Test memory merger delta persistence
    delta_rule = ProceduralRule(
        domain="social",
        rule="Keep LinkedIn posts under 150 words",
        confidence=0.85,
        status="active"
    )
    result = AgentResult(
        task_id="test_task_id",
        agent_name="linkedin_writer",
        task_type="write_linkedin_post",
        status=ResultStatus.SUCCESS,
        output="Post drafted",
        memory_delta={
            "procedural_rules": [delta_rule.model_dump(mode="json")]
        }
    )
    apply_memory_delta(mm, result)

    saved_rules = mm.get_procedural_rules(domain="social")
    print(f"\n[Saved Procedural Rules count for domain='social']: {len(saved_rules)}")
    assert any("Keep LinkedIn posts under 150 words" in r.get("rule", "") for r in saved_rules), "Delta procedural rule was not merged"

    print("\n✅ Procedural Memory Tier verification PASSED successfully!")

if __name__ == "__main__":
    test_procedural_memory()
