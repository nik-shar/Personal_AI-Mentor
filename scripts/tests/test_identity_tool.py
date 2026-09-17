"""
scripts/tests/test_identity_tool.py

Verification suite for the `get_identity` sidecar tool — the wire that puts P1
identity in front of the agent.

Covers:
 1. Declared in the manifest as a READ intent, with both pydantic schemas
 2. The generated TypeScript contract carries it (codegen is in sync)
 3. The TypeScript extension registers it, and renders from the Python block
    rather than re-implementing the text (one implementation, not two)
 4. Over real HTTP: a live answer with provenance, and counts that add up
 5. `include_profile=false` drops the structured half
 6. Request validation is enforced by the schema (a bad limit is a 422)
 7. Fail-open: a broken facade answers 200 with an `error` field, never a 500
 8. Read-only: the intent is declared read and the handler opens no write path
 9. The facade's naming convention holds — `orchestrator.memory.identity` is the
    MODULE, not the function (the shadowing the rename removed)

Read-only against live memory: it POSTs to the read endpoint and mutates nothing.
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
    """`check(condition, label)` — the shape the other memory suites use."""
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1
        _failed_names.append(name)


# ---------------------------------------------------------------------------
# 1-3. The contract, on both sides
# ---------------------------------------------------------------------------


def test_manifest_and_ts_contract() -> None:
    print("\n=== 1. Declared in the manifest ===")
    manifest = sidecar_tools.build_manifest()
    names = [tool.name for tool in manifest.tools]
    check("get_identity" in names, "get_identity is a declared tool", f"{len(names)} tools")

    descriptor = next((t for t in manifest.tools if t.name == "get_identity"), None)
    check(descriptor is not None and descriptor.kind == "read", "it is a READ intent")
    if descriptor:
        check(bool(descriptor.intent), "it declares the question it answers", descriptor.intent)
        check(
            descriptor.params.get("title") == "GetIdentityRequest",
            "params schema comes from the pydantic request model",
            str(descriptor.params.get("title")),
        )
        check(
            descriptor.result.get("title") == "GetIdentityResponse",
            "result schema comes from the pydantic response model",
            str(descriptor.result.get("title")),
        )
        check(
            "found" in (descriptor.result.get("required") or []),
            "`found` is required, so empty can never be mistaken for an answer",
        )

    print("\n=== 2. The generated TS contract is in sync ===")
    generated = (ROOT / "mentor/src/generated/mentor-tools.ts").read_text(encoding="utf-8")
    check('"name": "get_identity"' in generated, "mentor-tools.ts carries get_identity")
    check(
        '"block"' in generated and '"found"' in generated,
        "and the response contract includes block + found",
    )

    print("\n=== 3. The TypeScript extension registers it ===")
    ext = (ROOT / "mentor/extensions/read-tools.ts").read_text(encoding="utf-8")
    check('name: "get_identity"' in ext, "read-tools.ts registers the tool")
    check(
        'callTool<IdentityPayload>("get_identity"' in ext,
        "it calls the sidecar endpoint through the fail-open client",
    )
    check("function formatIdentity" in ext, "it has a formatter for the payload")
    check(
        "return payload.block" in ext,
        "it renders the PYTHON-rendered block rather than rebuilding the text",
    )
    check(
        "WHO HE IS" not in ext,
        "and it does not duplicate the block's wording (one implementation)",
    )


# ---------------------------------------------------------------------------
# 4-7. Over real HTTP
# ---------------------------------------------------------------------------


def test_http_read() -> None:
    client = TestClient(app)

    print("\n=== 4. A live read over HTTP ===")
    response = client.post("/tools/get_identity", json={})
    check(response.status_code == 200, "POST /tools/get_identity returns 200", str(response.status_code))
    payload = response.json()
    check(payload.get("error") is None, "no error on a healthy call", str(payload.get("error")))
    check(payload.get("found") is True, "found is True — there is something it knows")
    block = payload.get("block") or ""
    check("WHO HE IS" in block, "the block has the identity header")
    check("conf " in block, "provenance confidence is rendered inline")
    counts = payload.get("counts") or {}
    check(
        set(counts) == {"profile_facts", "who", "due", "pending_validation"},
        "counts cover every section",
        str(counts),
    )
    check(
        len(block.splitlines()) <= 40,
        "the block stays a prompt-sized read",
        f"{len(block.splitlines())} lines",
    )
    print("\n" + block)

    print("\n=== 5. include_profile=false drops the structured half ===")
    without = client.post("/tools/get_identity", json={"include_profile": False}).json()
    check(
        (without.get("counts") or {}).get("profile_facts") == 0,
        "profile_facts is 0 when excluded",
        str((without.get("counts") or {}).get("profile_facts")),
    )

    print("\n=== 6. Schema validation is enforced ===")
    bad = client.post("/tools/get_identity", json={"limit": 0})
    check(bad.status_code == 422, "limit=0 is rejected by the schema (422)", str(bad.status_code))
    bad2 = client.post("/tools/get_identity", json={"limit": 9999})
    check(bad2.status_code == 422, "limit=9999 is rejected too", str(bad2.status_code))


def test_fail_open() -> None:
    print("\n=== 7. Fail-open: a broken facade is an error field, not a 500 ===")
    original = memory_facade.read_identity
    try:
        def _boom(**_kwargs):
            raise RuntimeError("facade exploded")

        memory_facade.read_identity = _boom  # the handler resolves it on every call
        response = TestClient(app).post("/tools/get_identity", json={})
        check(response.status_code == 200, "the endpoint still answers 200", str(response.status_code))
        payload = response.json()
        check(
            (payload.get("error") or "").startswith("get_identity failed:"),
            "the failure is reported in the error field",
            str(payload.get("error")),
        )
        check(payload.get("found") is False, "found is False, so nothing is claimed")
        check(payload.get("block") is None, "and there is no partial block to misread")
    finally:
        memory_facade.read_identity = original


# ---------------------------------------------------------------------------
# 8-9. Invariants
# ---------------------------------------------------------------------------


def test_read_only_and_naming() -> None:
    print("\n=== 8. Read-only ===")
    manifest = sidecar_tools.build_manifest()
    descriptor = next(t for t in manifest.tools if t.name == "get_identity")
    check(descriptor.kind == "read", "declared read in the manifest")

    source = (ROOT / "api/tools.py").read_text(encoding="utf-8")
    start = source.index('@router.post("/get_identity")')
    end = source.index("@router.post", start + 10)
    handler = source[start:end]
    mutators = [
        m for m in (
            "set_profile_fact", "create_memory", "confirm_memory", "revise_memory",
            "deactivate", "add_episodic_event", "place_time_block", "set_anchor",
        )
        if m in handler
    ]
    check(not mutators, f"the handler calls no mutator ({mutators})")

    print("\n=== 9. The facade naming convention holds ===")
    check(
        not callable(memory_facade.identity),
        "orchestrator.memory.identity is the MODULE, not a callable",
        type(memory_facade.identity).__name__,
    )
    check(
        callable(memory_facade.read_identity),
        "the reader is read_identity(), so modules never get shadowed",
    )
    check(
        "read_identity" in memory_facade.__all__ and "identity" not in memory_facade.__all__,
        "__all__ advertises the reader, not the purpose name",
        str(memory_facade.__all__),
    )


def main() -> int:
    print("=" * 78)
    print("GET_IDENTITY — the sidecar tool that puts P1 identity in front of the agent")
    print("=" * 78)
    test_manifest_and_ts_contract()
    test_http_read()
    test_fail_open()
    test_read_only_and_naming()

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