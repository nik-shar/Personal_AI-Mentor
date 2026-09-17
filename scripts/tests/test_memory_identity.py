"""
scripts/tests/test_memory_identity.py

Verification suite for P1 — IDENTITY (`memory/identity.py`), the first purpose
behind the memory facade.

Covers:
 1. The facade exposes the purpose (and keeps the transitional export working)
 2. A real read of live memory: profile half + organic half + counts
 3. Only identity memory types are admitted (fact/goal/preference)
 4. Provenance survives the read — source, confidence, user_confirmed
 5. Deterministic ordering: two calls produce an identical sequence
 6. Deterministic layer: due / pending_validation come from get_deterministic
 7. The limit is honoured across types
 8. Dependency injection: injected stores are the ones actually read
 9. Fail-open: a broken profile store and a broken DNA store each degrade the
    answer and NAME what failed instead of raising
10. Rendering: empty snapshot → "", populated → header + provenance, and the
    pending-validation block is explicitly marked "do not state as fact"

Read-only against live memory: it constructs no rows and deletes none. The fake
stores in checks 7–9 mean the logic is verified without depending on what
happens to be in the database today.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.memory import (  # noqa: E402
    IdentitySnapshot,
    MemoryManager,
    read_identity,
    render_identity,
)
from orchestrator.memory.dna_store import DNAMemoryRecord  # noqa: E402
from orchestrator.memory.identity import IDENTITY_PROFILE_KEYS, IDENTITY_TYPES  # noqa: E402

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
# Fakes — so the logic is verified independently of today's database contents
# ---------------------------------------------------------------------------


def _record(
    content: str,
    memory_type: str = "fact",
    *,
    confidence: float = 0.9,
    source: str = "user_stated",
    confirmed: bool = True,
    due_at: datetime | None = None,
) -> DNAMemoryRecord:
    """A real DNAMemoryRecord — the same read-model the store hands back."""
    return DNAMemoryRecord(
        id=f"id-{abs(hash(content)) % 100000}",
        content=content,
        memory_type=memory_type,
        confidence=confidence,
        confidence_ceiling=1.0,
        source=source,
        user_confirmed=confirmed,
        tags=[],
        active=True,
        due_at=due_at,
    )


class FakeDNA:
    """Stands in for DNAMemoryStore, with per-type contents we control."""

    def __init__(self, by_type: dict[str, list[DNAMemoryRecord]] | None = None,
                 due: list[DNAMemoryRecord] | None = None,
                 pending: list[DNAMemoryRecord] | None = None) -> None:
        self.by_type = by_type or {}
        self._due = due or []
        self._pending = pending or []
        self.calls: list[str | None] = []

    def list_memories(self, memory_type: str | None = None, limit: int = 200,
                      **_kw) -> list[DNAMemoryRecord]:
        self.calls.append(memory_type)
        return list(self.by_type.get(memory_type or "", []))[:limit]

    def get_deterministic(self) -> dict[str, list[DNAMemoryRecord]]:
        return {"due": self._due, "core": [], "pending_validation": self._pending}


class FakeProfile:
    def __init__(self, facts: dict | None = None) -> None:
        self.facts = facts or {}

    def load_profile_facts(self, keys=None) -> dict:
        if keys is None:
            return dict(self.facts)
        return {k: v for k, v in self.facts.items() if k in set(keys)}


class BrokenProfile:
    def load_profile_facts(self, keys=None) -> dict:
        raise RuntimeError("profile store down")


class BrokenDNA:
    def list_memories(self, **_kw):
        raise RuntimeError("dna store down")

    def get_deterministic(self):
        raise RuntimeError("dna store down")


# ---------------------------------------------------------------------------
# 1. The facade
# ---------------------------------------------------------------------------


def test_facade_surface() -> None:
    print("\n=== 1. The facade exposes the purpose ===")
    check(callable(read_identity), "orchestrator.memory.read_identity is callable")
    check(callable(render_identity), "render_identity is callable")
    check(MemoryManager is not None, "the transitional MemoryManager export still works")
    check(
        isinstance(IDENTITY_TYPES, tuple) and "fact" in IDENTITY_TYPES,
        "identity memory types are declared",
        str(IDENTITY_TYPES),
    )
    check(
        "todays_plan" not in IDENTITY_PROFILE_KEYS,
        "present-state keys are NOT in the identity key set (they are P2)",
    )
    check(
        "learning_streak_days" not in IDENTITY_PROFILE_KEYS,
        "and neither is the streak — the purpose split is real",
    )


# ---------------------------------------------------------------------------
# 2-5. A real read of live memory
# ---------------------------------------------------------------------------


def test_real_read() -> None:
    print("\n=== 2. A real read of live memory ===")
    snap = read_identity()
    check(isinstance(snap, IdentitySnapshot), "read_identity() returns an IdentitySnapshot")
    check(isinstance(snap.profile, dict), "the structured half is a dict", f"{len(snap.profile)} keys")
    check(not snap.degraded, "nothing degraded on a healthy read", str(snap.degraded))
    check(
        set(snap.counts) == {"profile_facts", "who", "due", "pending_validation"},
        "counts are reported for every section",
        str(snap.counts),
    )
    print(
        f"      live: profile={snap.counts['profile_facts']} who={snap.counts['who']} "
        f"due={snap.counts['due']} pending={snap.counts['pending_validation']}"
    )

    print("\n=== 3. Only identity memory types are admitted ===")
    types = {f.memory_type for f in snap.who}
    check(types <= set(IDENTITY_TYPES), "who only holds fact/goal/preference", str(sorted(types)))
    check(
        not (types & {"observation", "insight", "reflection", "context"}),
        "patterns and history types are excluded from identity",
    )

    print("\n=== 4. Provenance survives the read ===")
    fx = read_identity(
        memory_manager=FakeProfile(),
        dna_store=FakeDNA(by_type={"fact": [_record("a known fact")]}),
    )
    fact = fx.who[0] if fx.who else None
    check(fact is not None, "a fact came back from the fake store")
    if fact:
        check(0.0 <= fact.confidence <= 1.0, "confidence in range", f"{fact.confidence:.2f}")
        check(bool(fact.source), "source is recorded", fact.source)
        check(isinstance(fact.user_confirmed, bool), "user_confirmed is a bool")
        check(
            fact.provenance().startswith("[") and "conf" in fact.provenance(),
            "provenance renders in the house format",
            fact.provenance(),
        )

    print("\n=== 5. Deterministic ordering ===")
    a = [f.content for f in read_identity().who]
    b = [f.content for f in read_identity().who]
    check(a == b, "two calls return an identical sequence (no retrieval luck)")


# ---------------------------------------------------------------------------
# 6-9. Determinism, limits, injection, fail-open
# ---------------------------------------------------------------------------


def test_determinism_limit_and_injection() -> None:
    print("\n=== 6. Deterministic layer: due + pending validation ===")
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    fake = FakeDNA(
        by_type={"fact": [_record("knows Python")]},
        due=[_record("interview at Stripe", "context", due_at=soon)],
        pending=[
            _record(
                "prefers mornings",
                "insight",
                confidence=0.55,
                source="mentor_inferred",
                confirmed=False,
            )
        ],
    )
    snap = read_identity(memory_manager=FakeProfile(), dna_store=fake)
    check(len(snap.due) == 1 and snap.due[0].due_at is not None, "due memories carry their date")
    check(len(snap.pending_validation) == 1, "a pending inference is surfaced")
    check(
        snap.pending_validation[0].user_confirmed is False,
        "and is marked unconfirmed, so the mentor cannot state it as fact",
    )

    print("\n=== 7. The limit is honoured across types ===")
    many = FakeDNA(
        by_type={
            "fact": [_record(f"fact {i}") for i in range(10)],
            "goal": [_record(f"goal {i}", "goal") for i in range(10)],
            "preference": [_record(f"pref {i}", "preference") for i in range(10)],
        }
    )
    capped = read_identity(memory_manager=FakeProfile(), dna_store=many, limit=3)
    check(len(capped.who) <= 3, "who respects the limit", f"{len(capped.who)} <= 3")
    check(
        all(t in many.calls for t in IDENTITY_TYPES),
        "every identity type was queried",
        str(sorted({c for c in many.calls if c})),
    )

    print("\n=== 8. Dependency injection ===")
    injected = FakeDNA(by_type={"fact": [_record("injected fact")]})
    snap2 = read_identity(memory_manager=FakeProfile({"full_name": "Nik"}), dna_store=injected)
    check(snap2.profile.get("full_name") == "Nik", "the injected profile store was read")
    check(any(f.content == "injected fact" for f in snap2.who), "the injected DNA store was read")

    print("\n=== 9. Fail-open — and it names what failed ===")
    no_profile = read_identity(memory_manager=BrokenProfile(), dna_store=FakeDNA())
    check(
        any(d.startswith("profile_facts:") for d in no_profile.degraded),
        "a broken profile store is named in `degraded`",
        str(no_profile.degraded),
    )
    check(not no_profile.profile, "and the structured half is simply empty")

    no_dna = read_identity(memory_manager=FakeProfile({"full_name": "Nik"}), dna_store=BrokenDNA())
    check(
        any(d.startswith("dna_memory:") for d in no_dna.degraded),
        "a broken DNA store is named in `degraded`",
        str(no_dna.degraded),
    )
    check(not no_dna.who and not no_dna.due, "and the organic half is empty")
    check(
        no_dna.profile.get("full_name") == "Nik",
        "the surviving half still answers — a partial answer, not an exception",
    )


# ---------------------------------------------------------------------------
# 10. Rendering
# ---------------------------------------------------------------------------


def test_rendering() -> None:
    print("\n=== 10. Rendering ===")
    check(
        render_identity(IdentitySnapshot()) == "",
        "an empty snapshot renders to an empty string (nothing invented)",
    )

    soon = datetime.now(timezone.utc) + timedelta(days=1)
    fake = FakeDNA(
        by_type={"goal": [_record("targeting AI Engineer roles", "goal")]},
        due=[_record("interview Friday", "context", due_at=soon)],
        pending=[
            _record(
                "may prefer evenings",
                "insight",
                confidence=0.5,
                source="mentor_inferred",
                confirmed=False,
            )
        ],
    )
    text = render_identity(
        read_identity(memory_manager=FakeProfile({"full_name": "Nik"}), dna_store=fake)
    )
    check("WHO HE IS" in text, "the block has a header")
    check("full name: Nik" in text, "structured facts render")
    check("[goal · user-stated" in text, "provenance is inline")
    check("due " in text, "due dates render")
    check("do NOT state these as fact" in text, "unconfirmed inferences are marked as not-fact")
    check(len(text.splitlines()) <= 12, "the block stays compact", f"{len(text.splitlines())} lines")
    print("\n" + text)


def main() -> int:
    print("=" * 78)
    print("MEMORY P1 — IDENTITY (who is he, and what does he want)")
    print("=" * 78)
    test_facade_surface()
    test_real_read()
    test_determinism_limit_and_injection()
    test_rendering()

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