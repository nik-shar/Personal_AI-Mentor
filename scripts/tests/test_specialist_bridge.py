"""
scripts/tests/test_specialist_bridge.py

Verification suite for the Phase-4 specialist bridge — the change that let the
chat default move to the PI core without dropping three shipped capabilities:

  1. `run_specialist` is declared in the sidecar manifest (so the generated TS
     contract includes it)
  2. Bad input comes back as a structured error, never an exception
  3. task_type validation reuses config.VALID_TASK_TYPES rather than a new list
  4. job_hunter's empty default survives (it resolves intent itself)
  5. The bridge is graph-independent — it must not need orchestrator.py
  6. The chat engine defaults to `pi`, with `python` still selectable

Runs without a database and without any LLM call: every case here either
validates before touching memory, or checks wiring directly.

Run from project root:
    uv run python scripts/tests/test_specialist_bridge.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

import api.tools as tools
from orchestrator.config import DEFAULT_TASK_TYPES, VALID_TASK_TYPES

PASSED = 0
FAILED = 0

SPECIALIST_AGENTS = ("goal_decomposer", "job_hunter", "linkedin_writer")


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def main() -> None:
    # ------------------------------------------------------------------
    print("\n[1] Declared in the manifest (so the TS contract carries it)")
    manifest = tools.build_manifest()
    names = [tool.name for tool in manifest.tools]
    check("run_specialist is declared", "run_specialist" in names, str(names))

    descriptor = next((t for t in manifest.tools if t.name == "run_specialist"), None)
    check("it is a write intent", descriptor is not None and descriptor.kind == "write")
    if descriptor:
        required = descriptor.params.get("required", [])
        check("agent and request are required", set(required) == {"agent", "request"}, str(required))
        properties = descriptor.params.get("properties", {})
        check("task_type is optional", "task_type" in properties and "task_type" not in required)

    # ------------------------------------------------------------------
    print("\n[2] Bad input is a structured error, never an exception")

    def call(agent: str, request: str, task_type: str | None = None) -> dict:
        return tools.run_specialist(
            tools.RunSpecialistRequest(agent=agent, request=request, task_type=task_type)
        )

    unknown_agent = call("daily_coach", "plan my day")
    check("unknown agent → status failed", unknown_agent.get("status") == "failed")
    check(
        "unknown agent → error names the available ones",
        all(name in (unknown_agent.get("error") or "") for name in SPECIALIST_AGENTS),
        str(unknown_agent.get("error")),
    )

    empty_request = call("goal_decomposer", "   ")
    check("empty request → status failed", empty_request.get("status") == "failed")
    check("empty request → explains why", "request" in (empty_request.get("error") or ""))

    hallucinated = call("job_hunter", "find me jobs", task_type="find_jobs")
    check("unknown task_type → status failed", hallucinated.get("status") == "failed")
    check(
        "unknown task_type → error lists the valid values",
        "search_jobs" in (hallucinated.get("error") or ""),
        str(hallucinated.get("error")),
    )
    check("failed calls report no output", hallucinated.get("output") == "")

    # ------------------------------------------------------------------
    print("\n[3] task_type validation reuses the reasoner's contract")
    check("goal_decomposer has valid task types", bool(VALID_TASK_TYPES.get("goal_decomposer")))
    check("job_hunter has valid task types", bool(VALID_TASK_TYPES.get("job_hunter")))
    check(
        "every specialist in the bridge is in VALID_TASK_TYPES",
        all(agent in VALID_TASK_TYPES for agent in SPECIALIST_AGENTS),
        str(sorted(VALID_TASK_TYPES)),
    )

    # ------------------------------------------------------------------
    print("\n[4] job_hunter's empty default survives")
    # It is multi-action: parsing the raw message beats any fixed default (a
    # default of "tailor_resume" used to hijack "find me jobs").
    check("job_hunter defaults to no task type", DEFAULT_TASK_TYPES.get("job_hunter") == "")
    check(
        "the other specialists keep a real default",
        DEFAULT_TASK_TYPES.get("goal_decomposer") == "decompose_goal"
        and DEFAULT_TASK_TYPES.get("linkedin_writer") == "write_linkedin_post",
    )

    # ------------------------------------------------------------------
    print("\n[5] The bridge does not depend on the reasoning loop")
    # This is the property that lets orchestrator.py stop being load-bearing for
    # specialists. Checked against source, because an accidental import would be
    # invisible in a passing call.
    for module in ("nodes/context_builder.py", "nodes/memory_merger.py"):
        source = (ROOT / "orchestrator" / module).read_text(encoding="utf-8")
        check(
            f"{module} does not import orchestrator.orchestrator",
            "orchestrator.orchestrator" not in source,
        )

    registry_source = (ROOT / "orchestrator" / "registry.py").read_text(encoding="utf-8")
    check("registry has no graph dependency", "orchestrator.orchestrator" not in registry_source)
    check(
        "the sidecar's bridge reaches no graph module",
        "orchestrator.orchestrator" not in Path(tools.__file__).read_text(encoding="utf-8"),
    )

    # ------------------------------------------------------------------
    print("\n[6] Chat engine default")
    from api import main as api_main

    saved = os.environ.pop("MENTOR_CHAT_ENGINE", None)
    try:
        check("unset env → pi", api_main._chat_engine() == "pi")
        check("the default constant is pi", api_main.DEFAULT_CHAT_ENGINE == "pi")

        os.environ["MENTOR_CHAT_ENGINE"] = "python"
        check("explicit python still works (the escape hatch)", api_main._chat_engine() == "python")

        os.environ["MENTOR_CHAT_ENGINE"] = "pi"
        check("explicit pi works", api_main._chat_engine() == "pi")

        os.environ["MENTOR_CHAT_ENGINE"] = "nonsense"
        check("junk falls back to pi rather than failing", api_main._chat_engine() == "pi")
    finally:
        if saved is None:
            os.environ.pop("MENTOR_CHAT_ENGINE", None)
        else:
            os.environ["MENTOR_CHAT_ENGINE"] = saved

    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()