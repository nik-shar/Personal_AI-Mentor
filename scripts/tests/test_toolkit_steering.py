"""
scripts/tests/test_toolkit_steering.py

Verification suite for the instruction-set toolkit layer (orchestrator/toolkits.py):

  1. Loader — list_toolkits / load_toolkit parse manifest, philosophy, behaviors,
     guardrails, and all workflow frontmatter blocks from toolkits/<name>/
  2. Renderer — render_toolkit_block() with a selected workflow vs the workflow
     index; fail-open on unknown toolkits
  3. get_workflow — exact block lookup, '' when absent
  4. apply_toolkit_guardrail — ship override, teacher backstop, route clearing,
     crisis/emotional clearing, normal-talk untouched
  5. Reasoner schema — ReasoningDecision accepts toolkit_mode + workflow
  6. Prompt injection — _build_direct_system_prompt() layers the toolkit
     overlay only when toolkit_mode is active
  7. Retrieval material — assemble_retrieval_material() builds a real-history
     block from a (mock) memory manager + DNA store, fail-open when empty

Run from project root:
    uv run python scripts/tests/test_toolkit_steering.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from orchestrator import toolkits
from orchestrator.nodes.reasoner import (
    ReasoningDecision,
    apply_toolkit_guardrail,
)

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


def test_loader() -> None:
    print("\n=== [1] Toolkit Loader ===")
    names = toolkits.list_toolkits()
    check("learning-companion is discoverable", "learning-companion" in names, f"got {names}")

    tk = toolkits.load_toolkit("learning-companion")
    check("toolkit loads", tk is not None)
    if tk is None:
        return

    check("manifest name", tk.name == "learning-companion", tk.name)
    check("manifest title", tk.title == "Learning Companion", tk.title)
    check("manifest role present", len(tk.role) > 40, f"len={len(tk.role)}")
    check("manifest description present", len(tk.description) > 20)

    check("philosophy non-empty", len(tk.philosophy) > 100)
    check("behaviors non-empty", len(tk.behaviors) > 100)
    check("guardrails non-empty", len(tk.guardrails) > 50)

    expected_workflows = {
        "api", "arch", "autopsy", "debug", "explain", "explore",
        "hint", "learn", "read", "retrieve", "review", "test",
    }
    have_workflows = set(tk.workflows.keys())
    missing = expected_workflows - have_workflows
    check("all 12 workflows present", not missing, f"missing {missing}")
    check("12 workflow descriptions", len(tk.workflow_descriptions) == 12,
          f"{len(tk.workflow_descriptions)}")
    check("workflow bodies are substantive",
          all(len(body) > 50 for body in tk.workflows.values()))


def test_renderer() -> None:
    print("\n=== [2] Toolkit Renderer ===")

    block = toolkits.render_toolkit_block("learning-companion", workflow="hint")
    check("block has marker", "[TOOLKIT ACTIVE" in block)
    check("block has role frame", "ROLE FRAME" in block)
    check("block has philosophy", "Teaching Philosophy" in block)
    check("block has behaviors", "Examination" in block)
    check("block has selected workflow", "WORKFLOW — hint" in block)
    check("block has guardrails", "TOOLKIT GUARDRAILS" in block)

    idx = toolkits.render_toolkit_block("learning-companion")
    check("index block lists workflows", "AVAILABLE WORKFLOWS" in idx)
    check("index block includes a workflow name", "- debug:" in idx or "- hint:" in idx)

    check("unknown toolkit → ''", toolkits.render_toolkit_block("nope") == "")
    check("missing workflow falls to index",
          "AVAILABLE WORKFLOWS" in toolkits.render_toolkit_block("learning-companion", workflow="nope"))


def test_get_workflow() -> None:
    print("\n=== [3] get_workflow ===")
    body = toolkits.get_workflow("learning-companion", "hint")
    check("hint body returned", "Hint Ladder" in body and len(body) > 50)
    check("retrieve body returned", len(toolkits.get_workflow("learning-companion", "retrieve")) > 50)
    check("unknown workflow → ''", toolkits.get_workflow("learning-companion", "nope") == "")
    check("unknown toolkit → ''", toolkits.get_workflow("nope", "hint") == "")


def test_guardrail() -> None:
    print("\n=== [4] apply_toolkit_guardrail ===")

    # Ship-mode override
    d = ReasoningDecision(action="direct_response", toolkit_mode="learning-companion", workflow="hint", reasoning="t")
    out = apply_toolkit_guardrail(d, "just ship this, i need the code")
    check("ship this → toolkit cleared", out.toolkit_mode is None and out.workflow is None)

    d = ReasoningDecision(action="direct_response", toolkit_mode="learning-companion", workflow="explain", reasoning="t")
    out = apply_toolkit_guardrail(d, "stop teaching, just tell me the answer")
    check("stop teaching → toolkit cleared", out.toolkit_mode is None)

    # Teacher backstop
    d2 = ReasoningDecision(action="direct_response", reasoning="t")
    out2 = apply_toolkit_guardrail(d2, "teach me how binary search works")
    check("teach me → learning-companion forced", out2.toolkit_mode == "learning-companion")
    check("teach me → direct_response forced", out2.action == "direct_response")
    check("teach me → default workflow", out2.workflow == "learn", f"workflow={out2.workflow}")

    d2b = ReasoningDecision(action="direct_response", reasoning="t")
    out2b = apply_toolkit_guardrail(d2b, "act as my teacher for system design")
    check("act as my teacher → learning-companion", out2b.toolkit_mode == "learning-companion")

    # Backstop never hijacks agent routing
    d2c = ReasoningDecision(action="route", agent_name="job_hunter", task_type="update_application_status", reasoning="t", agent_pipeline=["job_hunter"])
    out2c = apply_toolkit_guardrail(d2c, "teach me, then update my application")
    check("agent route outranks teacher backstop", out2c.action == "route" and out2c.toolkit_mode is None)

    # Route clears toolkit
    d3 = ReasoningDecision(action="route", agent_name="job_hunter", toolkit_mode="learning-companion", workflow="hint", reasoning="t", agent_pipeline=["job_hunter"])
    out3 = apply_toolkit_guardrail(d3, "tailor my resume")
    check("route clears toolkit", out3.toolkit_mode is None and out3.workflow is None)

    # Crisis clears toolkit
    d4 = ReasoningDecision(action="direct_response", disclosure_type="crisis", toolkit_mode="learning-companion", reasoning="t")
    out4 = apply_toolkit_guardrail(d4, "nothing matters anymore")
    check("crisis clears toolkit", out4.toolkit_mode is None)

    # Emotional turns clear toolkit
    d4b = ReasoningDecision(action="direct_response", disclosure_type="venting", toolkit_mode="learning-companion", reasoning="t")
    out4b = apply_toolkit_guardrail(d4b, "today was garbage")
    check("venting clears toolkit", out4b.toolkit_mode is None)

    # Normal talk untouched
    d5 = ReasoningDecision(action="direct_response", reasoning="t")
    out5 = apply_toolkit_guardrail(d5, "hey whatsup")
    check("normal talk untouched", out5.toolkit_mode is None and out5.workflow is None)


def test_reasoner_schema() -> None:
    print("\n=== [5] ReasoningDecision schema ===")
    d = ReasoningDecision(action="direct_response", toolkit_mode="learning-companion", workflow="retrieve", reasoning="t")
    dumped = d.model_dump()
    check("toolkit_mode in serialized decision", dumped.get("toolkit_mode") == "learning-companion")
    check("workflow in serialized decision", dumped.get("workflow") == "retrieve")

    d2 = ReasoningDecision(action="direct_response", reasoning="t")
    check("defaults are None", d2.toolkit_mode is None and d2.workflow is None)


def test_prompt_injection() -> None:
    print("\n=== [6] _build_direct_system_prompt toolkit overlay ===")
    from orchestrator.orchestrator import _build_direct_system_prompt

    base = _build_direct_system_prompt()
    check("no toolkit → no overlay marker", "TOOLKIT OVERLAY" not in base)

    with_tk = _build_direct_system_prompt(toolkit_mode="learning-companion", workflow="hint")
    check("toolkit active → overlay marker", "TOOLKIT OVERLAY" in with_tk)
    check("overlay carries role frame", "ROLE FRAME" in with_tk)
    check("overlay carries selected workflow", "WORKFLOW — hint" in with_tk)
    check("base persona still present", "personal AI mentor-companion" in with_tk)

    bad = _build_direct_system_prompt(toolkit_mode="does-not-exist")
    check("unknown toolkit → overlay skipped, no crash", "TOOLKIT OVERLAY" not in bad)


class _FakeMemoryManager:
    """Minimal stand-in: load_profile_facts returns a learning_log; the
    daily-summary loader path queries episodic (returns nothing here)."""

    def load_profile_facts(self, keys=None):
        return {
            "learning_log": [
                {"date": "2026-09-05", "topics": ["LangGraph", "pgvector"]},
                {"date": "2026-09-07", "topics": ["System Design — rate limiters"]},
            ]
        }

    def query_episodic(self, **kwargs):
        return []


def test_retrieval_material() -> None:
    print("\n=== [7] assemble_retrieval_material ===")
    from orchestrator.toolkits import assemble_retrieval_material

    fake_dna = SimpleNamespace(
        list_memories=lambda **kw: [
            SimpleNamespace(
                content="Nik learns algorithmic concepts faster by writing visual code",
                tags=["concept", "learning"],
                confidence=0.9,
            ),
            SimpleNamespace(
                content="Recurring off-by-one bug in DSA practice",
                tags=["mistake"],
                confidence=0.7,
            ),
        ]
    )

    material = assemble_retrieval_material(_FakeMemoryManager(), dna_store=fake_dna)
    check("learning log surfaced", "RECENT LEARNING LOG" in material and "LangGraph" in material)
    check("tagged DNA memories surfaced",
          "DNA MEMORIES WORTH REVIEWING" in material and "visual code" in material)
    check("header present", "RETRIEVAL PRACTICE MATERIAL" in material)

    # Fail-open on an empty store
    empty = assemble_retrieval_material(_FakeMemoryManager(), dna_store=SimpleNamespace(list_memories=lambda **kw: []))
    check("no memories → still returns learning-log block", "RECENT LEARNING LOG" in empty)

    # Empty everything → ''
    broken_mm = SimpleNamespace(
        load_profile_facts=lambda keys: {},
        query_episodic=lambda **kw: [],
    )
    empty2 = assemble_retrieval_material(broken_mm, dna_store=SimpleNamespace(list_memories=lambda **kw: []))
    check("empty everything → ''", empty2 == "", repr(empty2)[:80])


def main() -> None:
    toolkits.reset_cache()
    test_loader()
    test_renderer()
    test_get_workflow()
    test_guardrail()
    test_reasoner_schema()
    test_prompt_injection()
    test_retrieval_material()

    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()