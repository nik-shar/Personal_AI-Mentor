"""
scripts/tests/test_history_tool.py

Verification suite for the `get_history` sidecar tool — the wire that puts P3
history in front of the agent.

Covers:
 1. Declared in the manifest as a READ intent, with both pydantic schemas
 2. The generated TypeScript contract carries it (codegen is in sync)
 3. The TypeScript extension registers it and renders the PYTHON block, so there
    is one rendering of the past rather than two
 4. Over real HTTP: a live narrative, counts, and no degradation
 5. Parameters are honoured, and schema-enforced (a bad limit is a 422)
 6. Fail-open: a broken facade answers 200 with an `error` field, never a 500
 7. Read-only: the intent is declared read and the handler opens no write path
 8. The facade naming convention holds — `history` is the module

Read-only against live memory.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import orchestrator.memory as memory_facade  # noqa: E402
from api import tools as sidecar_tools  # noqa: E402
from api.main import app  # noqa: E402

_passed = 0
_failed = 0
_failed_names: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    """`check(condition, label)` — the shape the other suites use."""
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1
        _failed_names.append(name)


def test_manifest_and_ts_contract() -> None:
    print("\n=== 1. Declared in the manifest ===")
    manifest = sidecar_tools.build_manifest()
    names = [tool.name for tool in manifest.tools]
    check("get_history" in names, "get_history is a declared tool", f"{len(names)} tools")

    descriptor = next((t for t in manifest.tools if t.name == "get_history"), None)
    check(descriptor is not None and descriptor.kind == "read", "it is a READ intent")
    if descriptor:
        check(bool(descriptor.intent), "it declares the question it answers", descriptor.intent)
        check(
            descriptor.params.get("title") == "GetHistoryRequest",
            "params schema comes from the pydantic request model",
            str(descriptor.params.get("title")),
        )
        check(
            descriptor.result.get("title") == "GetHistoryResponse",
            "result schema comes from the pydantic response model",
            str(descriptor.result.get("title")),
        )
        check(
            "found" in (descriptor.result.get("required") or []),
            "`found` is required, so empty can never be mistaken for an answer",
        )
        check(
            "not by similarity" in (descriptor.description or ""),
            "the description distinguishes it from semantic recall",
        )
        check(
            "does not change with how the question is phrased" in (descriptor.description or ""),
            "and states the determinism guarantee",
        )

    print("\n=== 2. The generated TS contract is in sync ===")
    generated = (ROOT / "mentor/src/generated/mentor-tools.ts").read_text(encoding="utf-8")
    check('"name": "get_history"' in generated, "mentor-tools.ts carries get_history")
    check('"degraded"' in generated, "and the response contract includes degraded")

    print("\n=== 3. The TypeScript extension registers it ===")
    ext = (ROOT / "mentor/extensions/read-tools.ts").read_text(encoding="utf-8")
    check('name: "get_history"' in ext, "read-tools.ts registers the tool")
    check(
        'callTool<HistoryPayload>("get_history"' in ext,
        "it calls the sidecar through the fail-open client",
    )
    check("function formatHistory" in ext, "it has a formatter")
    check(
        "return payload.block" in ext,
        "it renders the PYTHON block rather than rebuilding the text",
    )
    check(
        "WHAT HAPPENED" not in ext,
        "and it does not duplicate the block's wording (one implementation)",
    )


def test_http_read() -> None:
    client = TestClient(app)

    print("\n=== 4. A live read over HTTP ===")
    response = client.post("/tools/get_history", json={})
    check(response.status_code == 200, "POST /tools/get_history returns 200", str(response.status_code))
    payload = response.json()
    check(payload.get("error") is None, "no error on a healthy call", str(payload.get("error")))
    block = payload.get("block") or ""
    check(payload.get("found") is True, "found is True — there is a past to report")
    check("WHAT HAPPENED" in block, "the block has the history header")
    counts = payload.get("counts") or {}
    check(
        set(counts) == {"sessions", "recaps", "day_log", "has_rolling_summary"},
        "counts cover every source",
        str(counts),
    )
    check(
        payload.get("degraded") == [],
        "nothing degraded on a healthy read",
        str(payload.get("degraded")),
    )
    check(
        len(block.splitlines()) <= 40,
        "the block stays prompt-sized",
        f"{len(block.splitlines())} lines",
    )
    print("\n" + block)

    print("\n=== 5. Parameters are honoured and enforced ===")
    one = client.post("/tools/get_history", json={"sessions": 1, "day_log": 2}).json()
    check(
        (one.get("counts") or {}).get("sessions", 0) <= 1,
        "sessions is capped by the parameter",
        str((one.get("counts") or {}).get("sessions")),
    )
    check(
        (one.get("counts") or {}).get("day_log", 0) <= 2,
        "day_log is capped by the parameter",
        str((one.get("counts") or {}).get("day_log")),
    )
    bad = client.post("/tools/get_history", json={"sessions": 0})
    check(bad.status_code == 422, "sessions=0 is rejected by the schema (422)", str(bad.status_code))
    bad2 = client.post("/tools/get_history", json={"day_log": 9999})
    check(bad2.status_code == 422, "day_log=9999 is rejected too", str(bad2.status_code))


def test_fail_open_and_invariants() -> None:
    print("\n=== 6. Fail-open: a broken facade is an error field, not a 500 ===")
    original = memory_facade.read_history
    try:
        def _boom(**_kwargs):
            raise RuntimeError("facade exploded")

        memory_facade.read_history = _boom  # the handler resolves it per call
        response = TestClient(app).post("/tools/get_history", json={})
        check(response.status_code == 200, "the endpoint still answers 200", str(response.status_code))
        payload = response.json()
        check(
            (payload.get("error") or "").startswith("get_history failed:"),
            "the failure is reported in the error field",
            str(payload.get("error")),
        )
        check(payload.get("found") is False, "found is False, so nothing is claimed")
        check(payload.get("block") is None, "and there is no partial block to misread")
    finally:
        memory_facade.read_history = original

    print("\n=== 7. Read-only ===")
    descriptor = next(t for t in sidecar_tools.build_manifest().tools if t.name == "get_history")
    check(descriptor.kind == "read", "declared read in the manifest")

    source = (ROOT / "api/tools.py").read_text(encoding="utf-8")
    start = source.index('@router.post("/get_history")')
    end = source.index("@router.post", start + 10)
    handler = source[start:end]
    mutators = [
        name for name in (
            "set_profile_fact", "create_memory", "confirm_memory", "revise_memory",
            "deactivate", "add_episodic_event", "place_time_block", "set_anchor",
            "save_conversation_session", "log_day_event",
        )
        if name in handler
    ]
    check(not mutators, f"the handler calls no mutator ({mutators})")

    print("\n=== 8. The facade naming convention holds ===")
    check(
        not callable(memory_facade.history),
        "orchestrator.memory.history is the MODULE, not a callable",
        type(memory_facade.history).__name__,
    )
    check(callable(memory_facade.read_history), "the reader is read_history()")
    check(
        "read_history" in memory_facade.__all__ and "history" not in memory_facade.__all__,
        "__all__ advertises the reader, not the purpose name",
    )


def main() -> int:
    print("=" * 78)
    print("GET_HISTORY — the sidecar tool that puts P3 history in front of the agent")
    print("=" * 78)
    test_manifest_and_ts_contract()
    test_http_read()
    test_fail_open_and_invariants()

    print("\n" + "=" * 78)
    if _failed:
        print(f"RESULT: {_passed} passed, {_failed} failed")
        for name in _failed_names:
            print(f"  ❌ {name}")
        return 1
    print(f"RESULT: {_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())