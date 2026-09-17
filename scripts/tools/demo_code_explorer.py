"""
scripts/tools/demo_code_explorer.py

Hands-on demo of the Code-Grounded Mentor layer — NO LLM required.

Runs the actual tools against THIS repo (the default workspace root) and
shows the full pipeline a code question triggers:

  1. Sandbox resolution (reads only inside WORKSPACE_ROOTS)
  2. read_file — inspect real code
  3. grep_search — find a symbol across the repo
  4. git_diff / git_log — what changed, why
  5. run_command — allowlisted test/build
  6. propose_edit — draft-and-confirm (nothing written)
  7. Guardrail wiring — how "why does my code fail…" becomes code-explorer+deep
  8. Scaffold render — the teach-while-building workflow block

Run from project root:
    uv run python scripts/tools/demo_code_explorer.py
"""

from __future__ import annotations

import json
import sys
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
from orchestrator.toolkits import render_toolkit_block

SEP = "=" * 64


def show(title: str) -> None:
    print(f"\n{SEP}\n{title}\n{SEP}")


def main() -> None:
    show("1) SANDBOX — where can the mentor read?")
    print(f"WORKSPACE_ROOTS: {H._get_ws_roots()}")
    print("→ every path resolves under these roots; escapes are refused.")

    show("2) read_file — the mentor's eyes (reads harness.py)")
    f = str(ROOT / "orchestrator" / "harness.py")
    # read first 14 lines of harness.py so the demo is fast and small
    res = H.read_file(f, start=1, end=14)
    print(f"file: {res['path']}")
    print(f"lines {res['start_line']}-{res['end_line']} of {res['total_lines']}\n")
    print(res["content"])

    show("3) grep_search — find a symbol across the repo")
    g = H.grep_search(r"def run_tool_loop|def direct_response_node", "orchestrator", max_results=6)
    print(f"{g['count']} hit(s) for the deep-loop symbols:")
    for hit in g["hits"][:6]:
        print(f"  {hit['path']}:{hit['line']}: {hit['text'][:70]}")

    show("4) git_diff / git_log — what changed, why")
    d = H.git_diff(".")
    print(f"diff stat lines: {len(d['stat'])} → {'; '.join(d['stat'][:4]) or '(clean tree)'}")
    log = H.git_log(".")
    print(f"last commits: {[c[:60] for c in log['log'][:3]]}")

    show("5) run_command — allowlisted tests/build (refuses arbitrary cmds)")
    print(H.run_command("rm -rf /"))
    print("\n→ blocked. Now an allowlisted command:")
    print(H.run_command("uv run python -m py_compile orchestrator/harness.py"))

    show("6) propose_edit — draft-and-confirm, NO write")
    pr = H.propose_edit(
        str(ROOT / "README.md"),
        "current README.md line",
        "…",
        "demo",
    )
    # fall back to a harmless proposal on a real file
    pr = H.propose_edit(
        str(ROOT / "orchestrator" / "config.py"),
        "WORKSPACE_ROOTS: tuple[str, ...] = tuple(",
        "WORKSPACE_ROOTS: tuple[str, ...] = tuple(  # demo proposal only",
        "demo: shows the diff, changes nothing",
    )
    print(json.dumps({k: pr["proposal"][k] for k in ("path", "status", "diff")}, indent=2)[:600])
    after = (ROOT / "orchestrator" / "config.py").read_text(encoding="utf-8")
    print("file actually modified?", "demo proposal only" in after)

    show("7) GUARDRAIL — the trigger you'll feel in chat")
    d0 = ReasoningDecision(action="direct_response", reasoning="t")
    out = apply_toolkit_guardrail(d0, "why does my code fail on the retry path?")
    print("user: 'why does my code fail on the retry path?'")
    print(f"  → toolkit_mode={out.toolkit_mode}  reasoning_depth={out.reasoning_depth}  action={out.action}")
    print("(this is the code-enforced path: a code question ALWAYS reads before answering)")

    show("8) SCAFFOLD — the teach-while-building workflow")
    block = render_toolkit_block("code-explorer", workflow="scaffold")
    print("— transfer step excerpt (the non-negotiable rule):")
    start = block.lower().find("transfer")
    print(block[start : start + 260].split("\n")[:6][0] if start >= 0 else block[:260])

    show("DONE — the layer works end-to-end. Now try the live chat:")
    print("  uv run python -m orchestrator")
    print('  then say:  "why does my orchestrator error? / debug this failing test / explore my repo"')


if __name__ == "__main__":
    main()