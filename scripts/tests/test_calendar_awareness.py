"""
scripts/tests/test_calendar_awareness.py

Verification suite for the mentor's AWARENESS of the calendar infrastructure
(the calendar-manager toolkit overlay + reasoner steering + fallback binding).

Covers:
  1. calendar-manager toolkit loads (manifest + 3 workflows)
  2. apply_toolkit_guardrail forces calendar-manager for scheduling signals
  3. ...availability and anchor signals
  4. ...without hijacking agent-routed, emotional, or already-toolkitted turns
  5. calendar-manager overlay injects into the direct-response system prompt
  6. reasoner prompt mentions calendar-manager + write_tutorial routing
  7. calendar tools are bound in the fallback agent lane (static, no LLM)
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.nodes.reasoner import ReasoningDecision, apply_toolkit_guardrail
from orchestrator.orchestrator import _build_direct_system_prompt
from orchestrator.toolkits import list_toolkits, load_toolkit

_passed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    flag = "✅" if ok else "❌"
    print(f"  {flag} {name}" + (f" — {detail}" if detail else ""))
    assert ok, name
    _passed += 1


def _decision(**over) -> ReasoningDecision:
    base = dict(
        action="direct_response",
        reasoning="test",
        agent_name=None,
        agent_pipeline=[],
        notes_for_agent=None,
        toolkit_mode=None,
        workflow=None,
        reasoning_depth="standard",
        disclosure_type=None,
    )
    base.update(over)
    return ReasoningDecision(**base)


def main() -> None:
    print("\n[1] calendar-manager toolkit loads")
    check("toolkit registered", "calendar-manager" in list_toolkits(), str(list_toolkits()))
    tk = load_toolkit("calendar-manager")
    check("manifest title", tk.title == "Calendar Manager")
    check("all 3 workflows", {"schedule", "availability", "anchors"}.issubset(tk.workflows), str(sorted(tk.workflows)))
    check("guardrails present", bool(tk.guardrails and "never_over_anchors" in tk.guardrails))

    print("\n[2] scheduling signals -> calendar-manager overlay (code backstop)")
    d = apply_toolkit_guardrail(
        _decision(),
        "I've got 3 hours today, can you place my revision hours on my calendar?",
    )
    check("forces direct_response", d.action == "direct_response", d.action)
    check("activates calendar-manager", d.toolkit_mode == "calendar-manager", d.toolkit_mode)
    check("defaults to schedule workflow", d.workflow == "schedule", d.workflow)
    check("no agent route", d.agent_name is None and d.agent_pipeline == [])

    d2 = apply_toolkit_guardrail(_decision(action="direct_response"), "plan my day today please")
    check("'plan my day' also activates calendar-manager", d2.toolkit_mode == "calendar-manager", d2.toolkit_mode)

    print("\n[3] availability + anchor signals")
    d3 = apply_toolkit_guardrail(_decision(), "when am I free tomorrow after 5?")
    check("availability question -> calendar-manager", d3.toolkit_mode == "calendar-manager", d3.toolkit_mode)
    d4 = apply_toolkit_guardrail(_decision(toolkit_mode="calendar-manager", workflow="availability"), "am i free at 4?")
    check("explicit workflow preserved", d4.workflow == "availability", d4.workflow)
    d5 = apply_toolkit_guardrail(_decision(), "I usually sleep midnight to 8 — set that as an anchor")
    check("anchor statement -> calendar-manager", d5.toolkit_mode == "calendar-manager", d5.toolkit_mode)

    print("\n[4] no hijacking")
    dj = apply_toolkit_guardrail(_decision(action="route", agent_name="job_hunter", reasoning="job app"), "did TestCorp update my application status?")
    check("agent-routed turn untouched", dj.action == "route" and dj.toolkit_mode is None, f"{dj.action}/{dj.toolkit_mode}")
    dc = apply_toolkit_guardrail(_decision(disclosure_type="venting"), "i am so burned out today")
    check("venting never gets calendar overlay", dc.toolkit_mode is None, dc.toolkit_mode)
    dt = apply_toolkit_guardrail(
        _decision(toolkit_mode="learning-companion", workflow="explain"),
        "teach me backprop, and maybe block some time later",
    )
    check("existing teaching toolkit wins over calendar", dt.toolkit_mode == "learning-companion", dt.toolkit_mode)

    print("\n[5] overlay injects into direct-response system prompt")
    prompt = _build_direct_system_prompt(toolkit_mode="calendar-manager", workflow="schedule")
    check("TOOLKIT OVERLAY present", "TOOLKIT OVERLAY" in prompt)
    check("role frame present", "ROLE FRAME" in prompt)
    check("schedule workflow body present", "Read the grid." in prompt)
    check("consent rule present", "Propose, don't write" in prompt or "consent" in prompt.lower())

    print("\n[6] reasoner prompt awareness")
    import orchestrator.nodes.reasoner as reasoner_mod
    src = reasoner_mod.__doc__ or ""
    sys_prompt_source = open(reasoner_mod.__file__, encoding="utf-8").read()
    check("reasoner skill-rules mention calendar-manager", "calendar-manager" in sys_prompt_source)
    check("VALID block includes write_tutorial", "write_tutorial" in sys_prompt_source)
    wf_desc = ReasoningDecision.model_fields["workflow"].description or ""
    check(
        "workflow field lists calendar workflows",
        "calendar-manager" in wf_desc and "schedule" in wf_desc and "availability" in wf_desc and "anchors" in wf_desc,
        wf_desc,
    )

    print("\n[7] fallback lane binds calendar tools (static check via harness factory)")
    from orchestrator.harness import make_calendar_tools
    names = [t.name for t in make_calendar_tools(None)]
    check("fallback-bound tool surface", {"get_day_grid", "find_available_slots", "place_time_block", "set_anchor"}.issubset(names), str(names))

    print(f"\n✅ test_calendar_awareness: {_passed} checks passed.")


if __name__ == "__main__":
    main()
