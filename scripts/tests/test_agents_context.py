"""
scripts/tests/test_agents_context.py

Verification suite for `AGENTS.md` — the system context PI loads automatically at
the start of every session.

Why this needs a suite: AGENTS.md is injected into the system prompt on **every**
turn, so it is the one file where "complete" and "small" pull against each other.
This suite holds both ends:

  - **completeness** — every store in the schema is described and every document
    it points at exists, so the map cannot silently go stale;
  - **distinctness** — it must NOT restate the constitution or the persona, which
    are injected separately as the identity overlay (the same single-source rule
    `test_identity.test.ts` enforces on the TypeScript side);
  - **budget** — a hard line cap, because this text is paid for on every turn.

Covers:
 1. It exists at the repo root and is non-empty
 2. Nothing shadows it (PI tries `AGENTS.override.md` first)
 3. PI's loader actually discovers `AGENTS.md` (read from the vendored harness)
 4. It stays within the per-turn line budget
 5. It states the sole-writer rule and the real write boundary
 6. Every table in `memory/models.py` is described — the completeness guard
 7. It does not duplicate the identity files (and the markers are real, so the
    check is not vacuous)
 8. It names the read tools, so they can be found
 9. Every document it points at exists
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.tools import build_manifest  # noqa: E402
from orchestrator.memory.models import Base  # noqa: E402

AGENTS = ROOT / "AGENTS.md"
OVERRIDE = ROOT / "AGENTS.override.md"
MAX_LINES = 150

REFERENCED = (
    "docs/PI-Mentor Boundary.md",
    "docs/Memory System Overview.md",
    "docs/Architecture Overview.md",
    "docs/Cognitive Layer.md",
    "docs/Commands Reference.md",
    "orchestrator/skills.md",
    "orchestrator/memory/__init__.py",
)

# Phrases that belong to the identity overlay. They are injected on every turn
# by `mentor/extensions/mentor-identity.ts`; a copy here would be a second source
# of the same truth, which is what the single-source rule forbids.
IDENTITY_MARKERS = (
    ("YOUR VOICE:", "mentor_persona.md"),
    ("HARD RULES:", "mentor_persona.md"),
    ("IIT Roorkee", "mentor_agent_guidelines.md §1"),
)

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


def test_exists_and_is_not_shadowed() -> str:
    print("\n=== 1. It exists at the repo root ===")
    check(AGENTS.is_file(), "AGENTS.md exists at the repo root")
    text = AGENTS.read_text(encoding="utf-8") if AGENTS.is_file() else ""
    check(len(text.strip()) > 500, "and it is not a stub", f"{len(text)} chars")

    print("\n=== 2. Nothing shadows it ===")
    check(
        not OVERRIDE.exists(),
        "no AGENTS.override.md — it would take precedence over AGENTS.md silently",
    )

    print("\n=== 3. PI's loader actually discovers it ===")
    loader = ROOT / "pi/packages/coding-agent/src/core/resource-loader.ts"
    if loader.is_file():
        src = loader.read_text(encoding="utf-8")
        check('"AGENTS.md"' in src, "the vendored loader lists AGENTS.md as a candidate")
        check(
            '"AGENTS.override.md"' in src and '"CLAUDE.md"' in src,
            "and the same loader still honours override / CLAUDE.md precedence",
        )
    else:
        print("  (vendored pi/ is absent — loader discovery skipped, not asserted)")
    return text


def test_budget(text: str) -> None:
    print("\n=== 4. It stays within the per-turn budget ===")
    lines = text.count("\n") + 1
    check(lines <= MAX_LINES, f"under {MAX_LINES} lines (paid for every turn)", f"{lines} lines")
    check(len(text) <= 8000, "under 8k characters", f"{len(text)} chars")
    check("<!--" not in text, "carries no HTML comments (they are tokens too)")


def test_rules_and_boundary(text: str) -> None:
    print("\n=== 5. The rule and the write boundary are stated ===")
    low = text.lower()
    check("only writer" in low, "the sole-writer rule is stated")
    check("python" in low, "and names Python as the writer")
    check("learning/" in text, "the write allowlist (learning/) is stated")
    check("propose_edit" in text, "the propose-and-confirm path is stated")
    check("mentor_service_url" in low, "the sidecar location is named")
    check("plainly" in low, "and the instruction to admit an outage is present")


def test_store_completeness(text: str) -> None:
    print("\n=== 6. Every store in the schema is described ===")
    tables = sorted(Base.metadata.tables)
    check(len(tables) >= 7, "the schema was actually read", f"{len(tables)} tables")
    missing = [t for t in tables if f"`{t}`" not in text]
    check(
        not missing,
        f"all {len(tables)} tables in memory/models.py are named in AGENTS.md",
        f"missing={missing}",
    )


def test_distinct_from_identity(text: str) -> None:
    print("\n=== 7. It does not duplicate the identity ===")
    persona = (ROOT / "mentor_persona.md").read_text(encoding="utf-8")
    guidelines = (ROOT / "mentor_agent_guidelines.md").read_text(encoding="utf-8")

    # Precondition: the markers must exist in the identity files, or every check
    # below would pass vacuously.
    check(
        "YOUR VOICE:" in persona and "HARD RULES:" in persona,
        "the persona markers are real (so the checks below are not vacuous)",
    )
    check("IIT Roorkee" in guidelines, "the §1 anchor fact is real")

    for marker, where in IDENTITY_MARKERS:
        check(marker not in text, f"does not restate {where}", repr(marker))

    check(
        "injected as your identity" in text,
        "and it points AT the identity rather than reproducing it",
    )


def test_tool_surface_and_pointers(text: str) -> None:
    print("\n=== 8. Every declared READ tool is named ===")
    # Derived from the tool contract rather than a hand-kept list: add a read
    # intent without documenting it and this fails, which is the only way a map
    # of the tool surface stays true.
    read_tools = sorted(t.name for t in build_manifest().tools if t.kind == "read")
    check(len(read_tools) >= 6, "the manifest was read", f"{len(read_tools)} read tools")
    missing = [name for name in read_tools if f"`{name}`" not in text]
    check(not missing, f"all {len(read_tools)} read intents are described", f"missing={missing}")
    check("run_specialist" in text, "the specialist seam is named")
    check(
        "compute_learning_streak" in text and "trim_plan_to_fit" in text,
        "the two native deterministic tools are named",
    )
    check("manifest" in text.lower(), "the manifest is named as the contract of record")

    print("\n=== 9. Every document it points at exists ===")
    absent = [p for p in REFERENCED if not (ROOT / p).exists()]
    check(not absent, f"all {len(REFERENCED)} referenced paths exist", f"missing={absent}")
    check((ROOT / "mentor/skills").is_dir(), "the skills directory it points at exists")


def main() -> int:
    print("=" * 78)
    print("AGENTS.md — the system context the mentor is loaded with")
    print("=" * 78)
    text = test_exists_and_is_not_shadowed()
    test_budget(text)
    test_rules_and_boundary(text)
    test_store_completeness(text)
    test_distinct_from_identity(text)
    test_tool_surface_and_pointers(text)

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