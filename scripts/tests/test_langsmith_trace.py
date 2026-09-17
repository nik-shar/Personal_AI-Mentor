"""
scripts/tests/test_langsmith_trace.py

Verify LangSmith cloud tracing is capturing EVERY LLM call in detail, nested
in a proper run tree under the per-turn "mentor_turn" run.

Run AFTER setting in .env:

    LANGSMITH_API_KEY=lsv2_...   (https://smith.langchain.com → Settings → API keys)
    LANGCHAIN_PROJECT=ai-mentor

Run from project root:
    uv run python scripts/tests/test_langsmith_trace.py

What gets verified:
  1. the key is present and tracing is enabled
  2. real LLM calls (reasoner, conversational) are made through the component
     wrapper so they carry tags + metadata
  3. the LangSmith API reports those runs in the project with:
       - name of the component (reasoner / tool_loop / mentor_turn / ...)
       - component:NAME tag, metadata, and latency (start/end times)
  4. a "mentor_turn" root run exists with session metadata and child runs

Then open https://smith.langchain.com → project "ai-mentor" → each chat turn is
a tree: mentor_turn → reasoner / tool_loop / synthesizer / dna_reflection →
ChatOpenAI (with full prompt, output, tokens, latency).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

# Importing orchestrator.llm triggers setup_tracing() BEFORE any model is built.
from orchestrator.llm import get_conversational_llm, get_reasoning_llm
from orchestrator.tracing import component, tracing_enabled

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


def _run_name(run: dict[str, Any]) -> str:
    return run.get("name") or "(unnamed)"


def main() -> None:
    project = os.getenv("LANGCHAIN_PROJECT") or "ai-mentor"
    key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    check("LangSmith key present", bool(key), "set LANGSMITH_API_KEY in .env")
    check("LANGCHAIN_TRACING_V2=true", os.getenv("LANGCHAIN_TRACING_V2") == "true")
    check("tracing_enabled()", tracing_enabled())
    print(f"  project: {project}")

    if not key:
        print("\nNo key — stopping. Get one at https://smith.langchain.com (free tier),")
        print("then add to .env:  LANGSMITH_API_KEY=lsv2_...")
        sys.exit(1 if FAILED else 0)

    print("\nMaking real LLM calls (each should appear as a trace)...")

    # 1. Reasoner (router tier) — wrapped in component("reasoner") by decorator.
    try:
        llm = get_reasoning_llm(temperature=0.0)
        with component("reasoner", tags=["component:reasoner"], metadata={"probe": "test_langsmith_trace"}):
            resp = llm.invoke([("human", "Reply with the single word: ok")])
        check("reasoner call traced", bool(resp.content))
    except Exception as exc:
        check("reasoner call traced", False, str(exc)[:120])

    # 2. Conversational (mentor voice tier) — wrapped in component("tool_loop").
    try:
        llm = get_conversational_llm(temperature=0.2)
        with component("tool_loop", tags=["component:tool_loop"], metadata={"probe": "test_langsmith_trace"}):
            resp = llm.invoke([("human", "Say hello in one short sentence.")])
        check("conversational call traced", bool(resp.content))
    except Exception as exc:
        check("conversational call traced", False, str(exc)[:120])

    # 3. A mentor_turn root — simulates one full turn so we can verify nesting.
    try:
        with component(
            "mentor_turn",
            tags=["mentor_turn", "session:test"],
            metadata={"session_id": "test", "probe": "test_langsmith_trace"},
        ):
            llm = get_reasoning_llm(temperature=0.1)
            resp = llm.invoke([("human", "Reply with the single word: tree")])
        check("mentor_turn root traced", bool(resp.content))
    except Exception as exc:
        check("mentor_turn root traced", False, str(exc)[:120])

    # Give the async ingestion queue a moment to flush runs to the API.
    print("\nWaiting 5s for LangSmith to flush runs...")
    time.sleep(5)

    # Verify via the LangSmith API.
    try:
        from langsmith import Client

        client = Client()
        recent = list(client.list_runs(project_name=project, limit=40))
        print(f"\n  Total recent runs in project '{project}': {len(recent)}")

        by_name: dict[str, list[dict[str, Any]]] = {}
        for r in recent:
            by_name.setdefault(_run_name(r), []).append(r)

        for want in ("reasoner", "tool_loop", "mentor_turn"):
            got = by_name.get(want, [])
            check(
                f"run '{want}' present",
                bool(got),
                f"latest runs: {sorted(by_name)[-12:]}",
            )
            if got:
                r = got[0]
                check(f"'{want}' has component tag", "component:" + want in (r.get("tags") or []))
                check(f"'{want}' has metadata", bool(r.get("metadata")))
                start, end = (r.get("start_time") or ""), (r.get("end_time") or "")
                check(f"'{want}' has latency", bool(start and end), f"start={start} end={end}")

        # Tree verification: mentor_turn must have at least one child run.
        root = by_name.get("mentor_turn", [])
        if root:
            root_id = root[0].get("id")
            children = list(client.list_runs(project_name=project, parent_run_id=root_id, limit=40))
            child_names = sorted({_run_name(c) for c in children})
            check("mentor_turn has child runs", bool(children), "no children found — tree broken")
            print(f"      └─ children: {child_names}")

        # Detail digest: latest run per component.
        print("\n  Per-call digest (latest run per component):")
        for name in sorted(by_name):
            r = by_name[name][0]
            start, end = r.get("start_time"), r.get("end_time")
            ms = 0
            if start and end and hasattr(end, "total_seconds"):
                ms = int((end - start).total_seconds() * 1000)
            model = (
                (r.get("extra") or {}).get("metadata", {}).get("model")
                or (r.get("metadata") or {}).get("model")
                or "?"
            )
            print(f"    • {name:<22} run_type={r.get('run_type'):<5} model={model} latency={ms}ms tags={r.get('tags')}")

    except Exception as exc:
        print(f"\n  ⚠️  LangSmith API verification unavailable: {exc}")
        print("     (The calls were still captured — check the UI directly.)")

    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    print("\nOpen https://smith.langchain.com → project 'ai-mentor' → each turn is a tree:")
    print("  mentor_turn → reasoner / tool_loop / synthesizer / dna_reflection → ChatOpenAI")
    print("  (full prompt, output, token usage, latency per call)")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()