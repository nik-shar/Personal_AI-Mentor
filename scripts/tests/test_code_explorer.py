"""
scripts/tests/test_code_explorer.py

Verification suite for the Code-Grounded Mentor (Shape-1 + reasoning core):

  1. Sandbox — read_file/grep/list resolve under workspace roots; escapes blocked
  2. read_file — line-bounded reads, dir rejection
  3. grep_search — case-insensitive regex, result caps
  4. propose_edit — read-only diff proposal, file untouched
  5. run_command — allowlist + shell-metacharacter rejection
  6. apply_toolkit_guardrail — code backstop forces code-explorer + deep;
     deep-upgrade with an active toolkit; non-code untouched; route precedence
  7. ReasoningDecision — reasoning_depth + writing_split serialize
  8. code-explorer toolkit — all 6 workflows load + scaffold renders transfer
  9. _build_direct_system_prompt — toolkit overlay for code-explorer
 10. OWNERSHIP pipeline — reflection prompt carries teaching-ownership rules;
     assemble_retrieval_material surfaces owned/watched tags
 11. Scenario wiring — code question → decision dict carries code-explorer + deep

Run from project root:
    uv run python scripts/tests/test_code_explorer.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from orchestrator import harness as H
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


_TMP: Path | None = None
_FIXTURE: Path | None = None


def setup() -> None:
    global _TMP, _FIXTURE
    _TMP = Path(tempfile.mkdtemp(prefix="mentor_codeexp_"))
    _FIXTURE = _TMP / "fixture.py"
    _FIXTURE.write_text(
        "def compute_retry_delay(attempt):\n"
        "    # exponential backoff with a cap\n"
        "    base = 2 ** attempt\n"
        "    return min(base, 60)\n\n"
        "def is_cached(key):\n"
        "    return key in _cache\n\n"
        "_cache = {}\n",
        encoding="utf-8",
    )
    H._set_ws_roots([str(_TMP)])


def teardown() -> None:
    H._set_ws_roots([])


def test_sandbox() -> None:
    print("\n=== [1] Sandbox enforcement ===")
    try:
        H.read_file(str(Path("/etc/passwd")))
        check("absolute escape blocked", False, "read /etc/passwd did not raise")
    except RuntimeError:
        check("absolute escape blocked", True)
    try:
        H.read_file("/../../etc/passwd")
        check("traversal escape blocked", False, "resolved outside sandbox")
    except RuntimeError:
        check("traversal escape blocked", True)
    ok = H.read_file("fixture.py")
    check("relative path resolves inside sandbox", "def compute_retry_delay" in ok["content"])


def test_read_file() -> None:
    print("\n=== [2] read_file ===")
    r = H.read_file("fixture.py")
    check("full read returns content", "def is_cached" in r["content"])
    r2 = H.read_file("fixture.py", start=1, end=1)
    check("line-bounded read", r2["content"] == "def compute_retry_delay(attempt):")
    check("line bounds reported", r2["start_line"] == 1 and r2["end_line"] == 1)
    rdir = H.read_file(".")
    check("directory → error dict", "error" in rdir and "directory" in rdir["error"])


def test_grep() -> None:
    print("\n=== [3] grep_search ===")
    g = H.grep_search("retry_delay", "fixture.py")
    check("grep single file finds match", g["count"] == 1 and "compute_retry_delay" in g["hits"][0]["text"])
    g2 = H.grep_search("def ", ".")  # directory walk
    check("grep directory walk", g2["count"] >= 2, f"count={g2['count']}")


def test_propose_edit() -> None:
    print("\n=== [4] propose_edit (read-only) ===")
    before = _FIXTURE.read_text(encoding="utf-8") if _FIXTURE else ""
    pr = H.propose_edit("fixture.py", "return min(base, 60)", "return min(base, 120)", "teach: why cap matters")
    check("proposal created", pr["proposal"]["status"] == "pending_approval")
    check("proposal has diff", "diff" in pr["proposal"] and len(pr["proposal"]["diff"]) > 0)
    after = _FIXTURE.read_text(encoding="utf-8") if _FIXTURE else ""
    check("file NOT modified", before == after)
    bad = H.propose_edit("fixture.py", "this text does not exist", "x", "nope")
    check("bad search_text → error", "error" in bad and "not found" in bad["error"])


def test_run_command() -> None:
    print("\n=== [5] run_command allowlist ===")
    blocked = H.run_command("rm -rf /")
    check("arbitrary command blocked", "blocked" in str(blocked))
    meta = H.run_command("pytest; ls")
    check("shell metacharacters blocked", "blocked" in str(meta))
    allowed = H.run_command("uv run python -m py_compile fixture.py")
    check("allowlisted command runs", "error" not in allowed or "blocked" not in str(allowed))


def test_guardrail_code() -> None:
    print("\n=== [6] apply_toolkit_guardrail code backstop ===")
    d = ReasoningDecision(action="direct_response", reasoning="t")
    out = apply_toolkit_guardrail(d, "why does my code fail on retry?")
    check("code backstop → code-explorer", out.toolkit_mode == "code-explorer")
    check("code backstop → deep reasoning", out.reasoning_depth == "deep")
    check("code backstop → action direct", out.action == "direct_response")

    d2 = ReasoningDecision(action="direct_response", toolkit_mode="learning-companion", workflow="debug", reasoning="t")
    out2 = apply_toolkit_guardrail(d2, "this test is failing, trace it")
    check("active toolkit + code turn → deep upgrade", out2.reasoning_depth == "deep")
    check("active toolkit preserved", out2.toolkit_mode == "learning-companion")

    d3 = ReasoningDecision(action="direct_response", reasoning="t")
    out3 = apply_toolkit_guardrail(d3, "how do I apply to jobs in Japan?")
    check("non-code untouched", out3.toolkit_mode is None and out3.reasoning_depth == "standard")

    d4 = ReasoningDecision(action="route", agent_name="job_hunter", reasoning="t", agent_pipeline=["job_hunter"])
    out4 = apply_toolkit_guardrail(d4, "debug this, then update my applications")
    check("agent route outranks code backstop", out4.action == "route" and out4.toolkit_mode is None)

    d5 = ReasoningDecision(action="direct_response", disclosure_type="crisis", toolkit_mode="code-explorer", reasoning="t")
    out5 = apply_toolkit_guardrail(d5, "nothing works anymore, my code is broken")
    check("crisis clears code toolkit", out5.toolkit_mode is None)


def test_schema() -> None:
    print("\n=== [7] ReasoningDecision schema ===")
    d = ReasoningDecision(
        action="direct_response", reasoning="t",
        toolkit_mode="code-explorer", workflow="scaffold",
        reasoning_depth="deep", writing_split="joint",
    )
    dumped = d.model_dump()
    check("reasoning_depth serializes", dumped["reasoning_depth"] == "deep")
    check("writing_split serializes", dumped["writing_split"] == "joint")
    d2 = ReasoningDecision(action="direct_response", reasoning="t")
    check("defaults are standard/None", d2.reasoning_depth == "standard" and d2.writing_split is None)


def test_toolkit() -> None:
    print("\n=== [8] code-explorer toolkit ===")
    from orchestrator.toolkits import list_toolkits, load_toolkit, render_toolkit_block

    names = list_toolkits()
    check("code-explorer discoverable", "code-explorer" in names, f"{names}")
    tk = load_toolkit("code-explorer")
    expected = {"read-repo", "diagnose-error", "review-code", "trace-flow", "compare-versions", "scaffold"}
    check("all 6 workflows", expected <= set(tk.workflows.keys()), f"{set(tk.workflows.keys())}")
    block = render_toolkit_block("code-explorer", workflow="scaffold")
    check("scaffold renders", "WORKFLOW — scaffold" in block)
    check("scaffold has transfer obligation", "transfer" in block.lower() and "NON-NEGOTIABLE" in block.upper())


def test_prompt_injection() -> None:
    print("\n=== [9] _build_direct_system_prompt code-explorer overlay ===")
    from orchestrator.orchestrator import _build_direct_system_prompt

    with_tk = _build_direct_system_prompt(toolkit_mode="code-explorer", workflow="diagnose-error")
    check("code-explorer overlay injected", "TOOLKIT OVERLAY" in with_tk)
    check("code-explorer role frame", "ROLE FRAME" in with_tk)
    base = _build_direct_system_prompt()
    check("no toolkit → no overlay", "TOOLKIT OVERLAY" not in base)


def test_ownership_pipeline() -> None:
    print("\n=== [10] teaching-ownership memory pipeline ===")
    from types import SimpleNamespace

    from orchestrator.memory.dna_reflection import MEMORY_REFLECTION_PROMPT
    from orchestrator.toolkits import RETRIEVAL_TAGS, assemble_retrieval_material

    check("reflection prompt carries ownership rules", "TEACHING-OWNERSHIP" in MEMORY_REFLECTION_PROMPT)
    check("reflection prompt mentions owned/watched tags",
          '"owned"' in MEMORY_REFLECTION_PROMPT and '"watched"' in MEMORY_REFLECTION_PROMPT)

    check("owned tag in retrieval tags", "owned" in RETRIEVAL_TAGS)
    check("watched tag in retrieval tags", "watched" in RETRIEVAL_TAGS)

    fake_dna = SimpleNamespace(
        list_memories=lambda **kw: [
            SimpleNamespace(
                content="Nik watched a worked example of a vector-store layer; hasn't built one solo yet",
                tags=["watched", "learning"],
                confidence=0.8,
            ),
        ]
    )

    class FakeMM:
        def load_profile_facts(self, keys=None):
            return {"learning_log": []}

        def query_episodic(self, **kwargs):
            return []

    material = assemble_retrieval_material(FakeMM(), dna_store=fake_dna)
    check("watched memory surfaced", "vector-store layer" in material)


def test_scenario() -> None:
    print("\n=== [11] end-to-end decision wiring (no LLM) ===")
    from orchestrator.orchestrator import _build_direct_system_prompt

    decision = ReasoningDecision(
        action="direct_response",
        reasoning="code diagnosis",
        toolkit_mode="code-explorer",
        workflow="diagnose-error",
        reasoning_depth="deep",
        writing_split="mentor_writes",
    ).model_dump()

    check("decision carries reasoner fields",
          decision["toolkit_mode"] == "code-explorer" and decision["reasoning_depth"] == "deep")
    prompt = _build_direct_system_prompt(
        coaching_mode=None,
        toolkit_mode=decision["toolkit_mode"],
        workflow=decision["workflow"],
    )
    check("deep code turn → overlay carries diagnose workflow", "WORKFLOW — diagnose-error" in prompt)


def main() -> None:
    setup()
    try:
        test_sandbox()
        test_read_file()
        test_grep()
        test_propose_edit()
        test_run_command()
        test_guardrail_code()
        test_schema()
        test_toolkit()
        test_prompt_injection()
        test_ownership_pipeline()
        test_scenario()
    finally:
        teardown()

    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()