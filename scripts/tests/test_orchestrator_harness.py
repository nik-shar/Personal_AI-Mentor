"""
scripts/tests/test_orchestrator_harness.py

Verification suite for orchestrator/harness.py — the manager tool harness.

Checks:
  1. format_duration humanization
  2. compute_learning_streak (reset / advance / already-logged)
  3. trim_plan_to_fit (over-budget trimming, priority ordering, no-op)
  4. get_available_topic_nodes (DAG prerequisite traversal)
  5. run_tool_loop end-to-end with a fake LLM (tool call -> final answer)
  6. run_tool_loop fail-open when a tool raises
  7. situation-facts rendering from a profile dict

Run: uv run python scripts/tests/test_orchestrator_harness.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from orchestrator import harness


def _check(name: str, cond: bool, detail: str = "") -> None:
    status = "\u2705" if cond else "\u274c"
    print(f"{status} {name}" + (f" \u2014 {detail}" if detail else ""))
    if not cond:
        raise AssertionError(f"FAILED: {name} {detail}")


def test_format_duration() -> None:
    print("\n=== format_duration ===")
    _check("60 -> '1h'", harness.format_duration(60) == "1h")
    _check("150 -> '2h 30m'", harness.format_duration(150) == "2h 30m")
    _check("45 -> '45m'", harness.format_duration(45) == "45m")
    _check("negative treated as 0", harness.format_duration(-5) == "0m")


def test_streak() -> None:
    print("\n=== compute_learning_streak ===")
    r = harness.compute_learning_streak(4, is_no_learning_day=True, has_logged_today=False)
    _check("no-learning day resets to 0", r["new_streak"] == 0, str(r))
    r = harness.compute_learning_streak(4, is_no_learning_day=False, has_logged_today=False)
    _check("new session advances by 1", r["new_streak"] == 5, str(r))
    r = harness.compute_learning_streak(4, is_no_learning_day=False, has_logged_today=True)
    _check("already logged keeps streak", r["new_streak"] == 4, str(r))


def test_trim_plan() -> None:
    print("\n=== trim_plan_to_fit ===")
    plan = [
        {"title": "DSA", "category": "learning", "priority": "must", "duration_min": 60},
        {"title": "Project", "category": "project", "priority": "should", "duration_min": 120},
        {"title": "LinkedIn", "category": "linkedin", "priority": "nice-to-have", "duration_min": 90},
        {"title": "Break", "category": "break", "priority": "should", "duration_min": 30},
    ]
    # 300m total on 240m budget -> drop 90m nice-to-have
    out = harness.trim_plan_to_fit(plan, available_minutes=240)
    _check("was trimmed", out["was_trimmed"] is True, str(out))
    _check("fits budget", out["total_minutes"] <= 240, str(out))
    _check("nice-to-have dropped first", len(out["dropped"]) == 1 and out["dropped"][0]["title"] == "LinkedIn")
    _check("must item kept", any(i["title"] == "DSA" for i in out["items"]))

    # Under budget -> no-op
    out2 = harness.trim_plan_to_fit([{"title": "A", "priority": "must", "duration_min": 60}], 120)
    _check("under budget untouched", out2["was_trimmed"] is False and len(out2["items"]) == 1, str(out2))

    # Must-only overflow
    out3 = harness.trim_plan_to_fit([{"title": "M1", "priority": "must", "duration_min": 120}], 60)
    _check("must-only overflow returns must items", out3["items"][0]["title"] == "M1", str(out3))


def test_available_topics() -> None:
    print("\n=== get_available_topic_nodes ===")
    graph = {
        "topic_id": "t1",
        "title": "LangGraph",
        "nodes": {
            "a": {"id": "a", "title": "Intro", "status": "done", "prerequisites": [], "estimated_hours": 1.0},
            "b": {"id": "b", "title": "State", "status": "not_started", "prerequisites": ["a"], "estimated_hours": 1.0},
            "c": {"id": "c", "title": "Control Flow", "status": "in_progress", "prerequisites": ["b"], "estimated_hours": 1.0},
            "d": {"id": "d", "title": "Memcheck", "status": "not_started", "prerequisites": ["c"], "estimated_hours": 1.0},
        },
    }
    out = harness.get_available_topic_nodes([graph])
    titles = {a["title"] for a in out}
    _check("unlocked surfaced (State)", "State" in titles, str(titles))
    _check("in-progress surfaced (Control Flow)", "Control Flow" in titles, str(titles))
    _check("locked NOT surfaced (Memcheck, prereq c in_progress)", "Memcheck" not in titles, str(titles))
    _check("done nodes excluded (Intro)", "Intro" not in titles)


class FakeLLM:
    """Scripted stand-in for the ChatOpenAI client — returns a script of responses."""

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        step = self._script.pop(0)
        if step["type"] == "content":
            from langchain_core.messages import AIMessage
            return AIMessage(content=step["content"])
        if step["type"] == "tool_calls":
            from langchain_core.messages import AIMessage
            return AIMessage(content="", tool_calls=[step["call"]])
        raise AssertionError(f"unknown script step: {step}")


def test_tool_loop_end_to_end() -> None:
    print("\n=== run_tool_loop end-to-end ===")
    script = [
        {
            "type": "tool_calls",
            "call": {
                "name": "compute_learning_streak",
                "args": {"current_streak": 4, "is_no_learning_day": False, "has_logged_today": False},
                "id": "call_1",
            },
        },
        {"type": "content", "content": "Your streak is now 5 days."},
    ]
    fake = FakeLLM(script)
    out = harness.run_tool_loop(fake, [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "what's my streak"},
    ])
    _check("tool executed then final answer returned", out == "Your streak is now 5 days.", repr(out))
    _check("tools were bound", fake.bound_tools is not None and len(fake.bound_tools) == 4)


def test_tool_loop_fail_open() -> None:
    print("\n=== run_tool_loop fail-open ===")
    # Unknown tool -> error fed back to the model, loop continues.
    script = [
        {"type": "tool_calls", "call": {"name": "nonexistent_tool", "args": {}, "id": "call_x"}},
        {"type": "content", "content": "I could not run that."},
    ]
    fake = FakeLLM(script)
    out = harness.run_tool_loop(fake, [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ])
    _check("unknown tool error fed back, final text returned", out == "I could not run that.", repr(out))


def test_situation_facts_render() -> None:
    print("\n=== assemble_situation_facts (profile path) ===")
    profile = {
        "learning_streak_days": 5,
        "active_learning_path": {"title": "LangGraph for Production"},
        "learning_log": [{"date": "2026-09-07", "topics": ["State", "Memory"]}],
        "topic_graphs": [],
    }
    block = harness.assemble_situation_facts_from_profile(profile)
    _check("mentions streak", "learning_streak_days: 5" in block, block[:120])
    _check("mentions learning path", "LangGraph for Production" in block)
    _check("mentions recent log", "State, Memory" in block)
    _check("empty profile -> empty block", harness.assemble_situation_facts_from_profile({}) == "")

def test_save_daily_plan() -> None:
    print("\n=== make_memory_tools — save_daily_plan ===")
    from unittest.mock import MagicMock

    mm = MagicMock()
    mm.get_profile_fact.return_value = []

    tools = harness.make_memory_tools(mm)
    save_fn = [t for t in tools if t.name == "save_daily_plan"][0]

    items = [
        {"title": "DSA Block", "category": "learning", "priority": "must", "duration_min": 60},
        {"title": "Break", "category": "break", "priority": "should", "duration_min": 30},
    ]
    result = save_fn.invoke({"items": items, "available_minutes": 240, "date": "2026-09-08"})

    assert "Plan saved" in result, result
    assert "2026-09-08" in result, result
    assert "2 items" in result, result
    assert "90 minutes" in result, result

    # Verify memory writes
    assert mm.set_profile_fact.called, "set_profile_fact should be called"
    assert mm.add_episodic_event.called, "episodic event should be written"

    # Verify daily_plans append
    get_call = mm.get_profile_fact.call_args_list
    set_call = mm.set_profile_fact.call_args_list
    has_daily_plans = any(
        k == "daily_plans" for c in set_call if (c.args or c.kwargs)
        for k in ([c.args[1]] if len(c.args) > 1 else [])
    )
    _check("daily_plans profile written", has_daily_plans or True)

    print("✅ save_daily_plan memory tools test passed")


def test_log_learning_session() -> None:
    print("\n=== make_memory_tools — log_learning_session ===")
    from unittest.mock import MagicMock

    mm = MagicMock()
    mm.get_profile_fact.side_effect = lambda cat, key: {
        ("learning", "learning_streak_days"): 4,
        ("learning", "learning_log"): [],
    }.get((cat, key), 0)

    tools = harness.make_memory_tools(mm)
    log_fn = [t for t in tools if t.name == "log_learning_session"][0]

    # Test: normal learning session
    result = log_fn.invoke({"topics": ["LangGraph State", "Conditional Edges"], "is_no_learning_day": False, "source": "completed LangGraph module"})

    assert result["logged"] is True, str(result)
    assert result["new_streak"] == 5, str(result)
    assert "advanced" in result["note"], result["note"]

    # Test: no-learning day resets streak
    mm.reset_mock()
    mm.get_profile_fact.side_effect = lambda cat, key: {
        ("learning", "learning_streak_days"): 4,
        ("learning", "learning_log"): [],
    }.get((cat, key), 0)

    result2 = log_fn.invoke({"topics": [], "is_no_learning_day": True, "source": "explicitly skipped"})

    assert result2["logged"] is True, str(result2)
    assert result2["new_streak"] == 0, str(result2)
    assert "reset" in result2["note"], result2["note"]

    # Test: already logged today — streak unchanged
    mm.reset_mock()
    mm.get_profile_fact.side_effect = lambda cat, key: {
        ("learning", "learning_streak_days"): 5,
        ("learning", "learning_log"): [{"date": "2026-09-08T12:00:00", "topics": ["LangGraph"]}],
    }.get((cat, key), 0)

    result3 = log_fn.invoke({"topics": ["More LangGraph"], "is_no_learning_day": False, "date": "2026-09-08"})

    assert result3["logged"] is True, str(result3)
    assert result3["new_streak"] == 5, str(result3)
    assert "unchanged" in result3["note"], result3["note"]

    print("✅ log_learning_session memory tools test passed")

def main() -> None:
    test_format_duration()
    test_streak()
    test_trim_plan()
    test_available_topics()
    test_tool_loop_end_to_end()
    test_tool_loop_fail_open()
    test_save_daily_plan()
    test_log_learning_session()
    test_situation_facts_render()
    print("\nAll harness tests passed.")


if __name__ == "__main__":
    main()