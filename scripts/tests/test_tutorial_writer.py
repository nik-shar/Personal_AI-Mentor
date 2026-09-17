"""
scripts/tests/test_tutorial_writer.py

Verification suite for the externally-written tutorial-writer skill + the
goal_decomposer's single-topic "tutorial" mode (Phase 4).

No real LLM calls — the architect's LLM path is patched to raise so the
fail-open fan-out is exercised deterministically.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.Goal_Decomposer.goal_decomposer import (
    input_parser,
    pack_result,
    route_by_action,
    tutorial_architect,
    validate_tutorial_quality,
)
from agents.Goal_Decomposer.state import build_initial_state
from orchestrator.config import ROUTING_HINTS, VALID_TASK_TYPES
from orchestrator.toolkits import (
    load_guardrail_rules,
    load_toolkit,
    render_toolkit_block,
)
from schemas import AgentTask, MemorySlice, ResultStatus, TaskSource

_passed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    flag = "✅" if ok else "❌"
    print(f"  {flag} {name}" + (f" — {detail}" if detail else ""))
    assert ok, name
    _passed += 1


def _task(task_type: str, instructions: str, **params) -> AgentTask:
    return AgentTask(
        task_id="t1",
        agent_name="goal_decomposer",
        task_type=task_type,
        instructions=instructions,
        source=TaskSource.CHAT,
        memory_slice=MemorySlice(agent_name="goal_decomposer", task_type=task_type),
        params=params,
    )


def main() -> None:
    print("\n[1] tutorial-writer toolkit loads")
    tk = load_toolkit("tutorial-writer")
    check("toolkit found", tk is not None)
    check("manifest title", tk.title == "Tutorial Writer")
    check("philosophy/behaviors/guardrails present", bool(tk.philosophy and tk.behaviors and tk.guardrails))
    expected_workflows = {"decompose", "research", "write-tutorial", "review"}
    check("all 4 workflows present", expected_workflows.issubset(tk.workflows), str(sorted(tk.workflows)))

    print("\n[2] guardrail rules parsed")
    rules = load_guardrail_rules("tutorial-writer")
    check("rules block parsed", rules.get("min_length") == 800 and rules.get("min_headings") == 2, str(rules))
    check("banned placeholders include .../TODO/TBD/lorem", {"...", "TODO", "TBD", "lorem"}.issubset(rules.get("banned_placeholders", [])))

    print("\n[3] instruction overlay renders")
    block = render_toolkit_block("tutorial-writer", workflow="write-tutorial")
    check("role frame + guardrails present", "ROLE FRAME" in block and "TOOLKIT GUARDRAILS" in block)
    check("selected workflow body included", "Write Tutorial" in block)

    print("\n[4] input_parser routes to tutorial mode")
    st = build_initial_state(_task("write_tutorial", "The user requested: \"write a tutorial on DPO derivation\"\n\n[MENTOR GUIDANCE / STRATEGY]\nI struggled at the heaviside-step step."))
    parsed = input_parser(st)
    check("task_type=write_tutorial -> action tutorial", parsed["action_type"] == "tutorial", str(parsed["action_type"]))
    check("topic extracted & stripped", "DPO derivation" in parsed["goal_topic"], parsed["goal_topic"])
    check("target_days defaults to 1 for single-topic", parsed["target_days"] == 1, str(parsed["target_days"]))

    st2 = build_initial_state(_task("decompose_goal", "write a tutorial on LangGraph checkpointing — focus on PostgresSaver"))
    parsed2 = input_parser(st2)
    check("keyword from decompose_goal also routes tutorial", parsed2["action_type"] == "tutorial", str(parsed2["action_type"]))
    check("mentor emphasis survives into goal_topic scope", "PostgresSaver" in parsed2["goal_topic"], parsed2["goal_topic"])

    print("\n[5] routing")
    check("tutorial -> tutorial_architect", route_by_action({"action_type": "tutorial"}) == "tutorial_architect")
    check("normal create path untouched (no days -> clarify)", route_by_action({"action_type": "create", "target_days": None}) == "clarify_timeframe")

    print("\n[6] tutorial_architect fail-open fallback (LLM patched to raise)")
    import orchestrator.llm as orc_llm
    orig = orc_llm.get_reasoning_llm
    orc_llm.get_reasoning_llm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no LLM in tests"))
    try:
        base = build_initial_state(_task(
            "write_tutorial",
            "[USER REQUEST]\nThe user requested: \"write a tutorial on SBERT fine-tuning\"\n\n[MENTOR GUIDANCE / STRATEGY]\nWeakness: pooling strategies and score ranges.",
        ))
        spec_state = dict(base)
        spec_state.update(input_parser(base))
        spec_state.update({
            "web_research_summary": "",
            "outline": None, "decomposition": None,
            "written_files": [], "index_file_path": "",
            "feedback_message": "", "memory_delta": {}, "result": None,
        })
        out = tutorial_architect(spec_state)
    finally:
        orc_llm.get_reasoning_llm = orig
    outline = out["outline"]
    check("fallback outline produced", outline is not None)
    check("exactly one subtopic", len(outline.subtopics) == 1, str(len(outline.subtopics)))
    check("single topic uses requested title", "SBERT" in outline.subtopics[0].title, outline.subtopics[0].title)
    check("emphasis guidance carried into what_to_cover", "pooling" in outline.subtopics[0].what_to_cover.lower(), outline.subtopics[0].what_to_cover[:80])
    check("graph title marks deep tutorial", "Deep Tutorial" in outline.graph_title, outline.graph_title)

    print("\n[7] pack_result success path")
    res = pack_result({
        "action_type": "tutorial", "target_days": 1,
        "task": _task("write_tutorial", "x"),
        "written_files": ["Learning/Topics/tutorial.md"], "feedback_message": "Saved 1 note.",
        "memory_delta": {},
    })
    check("tutorial with written note -> SUCCESS", res["result"].status == ResultStatus.SUCCESS, res["result"].status)

    print("\n[8] validator honors toolkit rules")
    valid = "# Intro\n\nConcept body.\n\n## Deep Dive\n\n" + "x" * 1000
    ok1, r1 = validate_tutorial_quality(valid)
    check("valid note passes", ok1, r1)
    ok2, r2 = validate_tutorial_quality("# Intro\n\n## B\n\n" + "z" * 100)
    check("too-short rejected with floor from rules", (not ok2) and "minimum 800" in r2, r2)
    ok3, r3 = validate_tutorial_quality("# A\n\nbody\n\n...\n\n## B\n" + "w" * 900)
    check("standalone '...' rejected", (not ok3) and "placeholder" in r3, r3)
    ok4, r4 = validate_tutorial_quality("# A\n\nInline like f(...).\n\n## B\n" + "k" * 900)
    check("inline ellipsis allowed", ok4, r4)

    print("\n[9] config wiring")
    check("write_tutorial in VALID_TASK_TYPES", "write_tutorial" in VALID_TASK_TYPES["goal_decomposer"])
    check("routing hints include tutorial triggers", "write a tutorial" in ROUTING_HINTS["goal_decomposer"] and "deep dive on" in ROUTING_HINTS["goal_decomposer"])

    print(f"\n✅ test_tutorial_writer: {_passed} checks passed.")


if __name__ == "__main__":
    main()
