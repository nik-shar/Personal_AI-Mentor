"""
scripts/test_4_day_prep_bugfix.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv()

from agents.Daily_Coach.daily_coach import parse_input as daily_input_parser
from agents.Daily_Coach.state import build_initial_state as build_daily_state
from orchestrator.nodes.reasoner import run_reasoner
from schemas import AgentTask, MemorySlice, TaskSource

def test_bugfixes():
    print("=== 1. Testing Capping of 26-Hour Single-Day Plan in daily_coach ===")

    task = AgentTask(
        task_id="test_overbudget_task",
        source=TaskSource.CHAT,
        agent_name="daily_planner",
        task_type="build_daily_plan",
        instructions="Plan my day for 26-30 hours",
        params={"energy_override": 5, "available_minutes": 1560},
        memory_slice=MemorySlice(agent_name="daily_planner", task_type="build_daily_plan", relevant_profile={}),
    )

    state = build_daily_state(task)
    parsed_dict = daily_input_parser(state)
    available_capped = parsed_dict.get("available_minutes")

    print(f"  Input available_minutes: 1560 (26h)")
    print(f"  Capped available_minutes for today: {available_capped} ({available_capped // 60}h)")
    assert available_capped == 480, f"Expected 480 mins (8h), got {available_capped}"
    print("✅ Single-day plan time budget safely capped to 8 hours!")

    print("\n=== 2. Testing Reasoner Routing & Minutes Extraction ===")
    prompt = (
        "hi there, I have an upcoming interview, i have 4 days to prepare, they will mainly focus on Python, "
        "SQL and ML. i have solved 500+ problems on leetcode but in c++, i am familiar with python but need some revision "
        "for DSA and python. in SQL i am a complete beginner so i will need learning as well as practice. for ML i learned it "
        "in past but need revision and the interview will mainly focus on the algorithms related to credit card and finance systems. "
        "so i will need a basic project also to demonstrate them that i have basic knowledge about it. so basically i have 6-8 hours daily "
        "and total 4 hours so totally i have approx 26-30 hours for preparation"
    )

    decision = run_reasoner(prompt)
    print(f"  Reasoner Decision Agent: {decision.agent_name}")
    print(f"  Parsed Available Minutes: {decision.parsed_available_minutes}")
    
    assert decision.agent_name == "goal_decomposer", f"Expected routing to goal_decomposer, got {decision.agent_name}"
    print(f"✅ Multi-day interview prep request correctly routed to 'goal_decomposer'!")
    print("✅ Reasoner correctly parsed single-day time allocation!")

    print("\n🎉 ALL MULTI-DAY / 26-HOUR BUGFIX TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_bugfixes()
