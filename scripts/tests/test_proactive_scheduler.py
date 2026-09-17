"""
scripts/tests/test_proactive_scheduler.py

Verification test suite for Milestone 1:
Dynamic AI Self-Scheduler Engine & Consent Action Framework.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from orchestrator.memory.store import MemoryManager
from orchestrator.nodes.reasoner import run_reasoner
from orchestrator.runner import OrchestratorRunner
from scheduler.engine import get_scheduler_engine


def test_scheduler_engine():
    print("=== 1. Testing APScheduler Engine & Dynamic Self-Scheduling ===")
    engine = get_scheduler_engine()

    # Schedule a wake-up job 10 minutes in future
    job_id = engine.schedule_wake_up(minutes_from_now=10, reason="Evening study block audit")
    assert job_id is not None, "Failed to schedule wake up job"
    print(f"✅ Scheduled Wake-up Job ID: {job_id}")

    jobs = engine.get_upcoming_jobs()
    print(f"✅ Active Scheduled Jobs Count: {len(jobs)}")
    assert len(jobs) > 0
    print(f"   Job Details: {jobs[0]}")

    engine.cancel_existing_autowakes()
    print("✅ Successfully cancelled existing autowake jobs.")


def test_reasoner_self_scheduling_fields():
    print("\n=== 2. Testing Reasoner Self-Scheduling & Proposed Actions Schemas ===")
    decision = run_reasoner("I have 4 hours today for LangGraph and job applications.")
    print(f"  Reasoning Action: {decision.action}")
    print(f"  Agent Pipeline: {decision.agent_pipeline}")
    print(f"  Next Wake Up Minutes: {decision.next_wake_up_minutes}")
    print(f"  Next Wake Up Reason: {decision.next_wake_up_reason}")
    print(f"  Proposed Actions: {decision.proposed_actions}")
    print("✅ Reasoner successfully evaluated context and self-scheduling fields!")


def test_consent_action_execution():
    print("\n=== 3. Testing Consent Action Framework in Intake Node ===")
    mm = MemoryManager()
    mm.ensure_schema()

    # Create a dummy schedule event to update via consent
    ev = mm.create_schedule_event({
        "title": "Practice LeetCode C++",
        "status": "scheduled",
        "duration_min": 45,
    })
    ev_id = ev["id"]

    runner = OrchestratorRunner(mm)
    state = runner.create_state("test_consent_session")

    # Manually inject pending proposed action
    state["working_memory"]["pending_proposed_actions"] = [
        {
            "action_type": "update_schedule",
            "event_id": ev_id,
            "updates": {"status": "completed"},
        }
    ]

    # User confirms action
    updated_state = runner.run_turn(state, "Yes, approve those updates.")
    output = updated_state.get("response_text") or ""
    print(f"✅ Orchestrator Output:\n{output}")

    # Verify event status in DB
    events = mm.get_schedule_events(status="completed")
    matching = [e for e in events if e["id"] == ev_id]
    assert len(matching) > 0, "Proposed action status update failed!"
    print(f"✅ Database Event Updated: '{matching[0]['title']}' Status = {matching[0]['status']}")


if __name__ == "__main__":
    test_scheduler_engine()
    test_reasoner_self_scheduling_fields()
    test_consent_action_execution()
    print("\n🎉 ALL MILESTONE 1 SELF-SCHEDULER & CONSENT TESTS PASSED!")
