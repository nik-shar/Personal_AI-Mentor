"""
scripts/tests/test_memory_patterns.py

Verification suite for P4 — PATTERNS (`memory/patterns.py`).

Covers:
 1. The facade exposes the purpose, and `patterns` is the module (not shadowed)
 2. Momentum is read as arithmetic: in range, present, and recomputed per call
 3. A **real** event set genuinely triggers a detector — the fresh-pattern path is
    wired to the same detectors the weekly job uses, not to a mock
 4. The cold-start guard is respected: a thin window derives nothing
 5. Stored observations carry provenance and confidence, most-trusted first
 6. The two sources are labelled differently — a pattern noticed once must not
    read like a pattern that has been earned
 7. Deterministic at a fixed moment
 8. Fail-open: each source fails independently and names itself
 9. Rendering: both sections, and an explicit "do not state as habits" warning
10. Read-only, and drift is a documented NON-goal (nothing invents an intention)

Read-only against live memory; the behavioural checks run against fakes.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator.memory as memory_facade  # noqa: E402
from orchestrator.memory.dna_store import DNAMemoryRecord  # noqa: E402
from orchestrator.memory.patterns import (  # noqa: E402
    DERIVED_TYPES,
    PatternsView,
    read_patterns,
    render_patterns,
)

_passed = 0
_failed = 0
_failed_names: list[str] = []

NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)


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
# Fakes — the fake manager filters by window, so it behaves like the real one
# ---------------------------------------------------------------------------


def _ev(hour: int, day_offset: int, *, completed: bool, category: str = "learning") -> dict:
    start = NOW.replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=day_offset)
    return {
        "id": f"ev-{hour}-{day_offset}",
        "title": f"{category} block",
        "start_time": start.isoformat(),
        "duration_min": 60,
        "status": "completed" if completed else "cancelled",
        "category": category,
        "linked_goal": None,
    }


def _pattern_events() -> list[dict]:
    """8 events that DO trigger a time-of-day pattern: 3/4 mornings vs 0/4 evenings.

    The gap (0.75) clears SIGNIFICANCE_GAP (0.25), each bucket clears
    MIN_BUCKET_EVENTS (3), and the total clears MIN_EVENTS (8) — so if the wiring
    is right, a candidate comes back. Nothing here is mocked.

    Every timestamp is **strictly before** NOW (offsets 1-4 days), because the
    read window ends at NOW: events on the far side of "now" are correctly
    filtered out, and a fixture that straddled it would silently fall below the
    cold-start guard.
    """
    events = [_ev(8, offset, completed=offset != 4) for offset in (1, 2, 3, 4)]
    events += [_ev(20, offset, completed=False) for offset in (1, 2, 3, 4)]
    return events


class FakeManager:
    def __init__(self, events: list[dict] | None = None, explode: bool = False) -> None:
        self.events = events if events is not None else []
        self.explode = explode

    def get_schedule_events(self, start_date=None, end_date=None) -> list[dict]:
        if self.explode:
            raise RuntimeError("schedule store down")
        out: list[dict] = []
        for event in self.events:
            raw = event.get("start_time")
            if not raw:
                continue
            try:
                stamp = datetime.fromisoformat(str(raw))
            except ValueError:
                continue
            if start_date is not None and stamp < start_date:
                continue
            if end_date is not None and stamp > end_date:
                continue
            out.append(event)
        return out


def _record(content: str, memory_type: str = "observation", confidence: float = 0.7,
            confirmed: bool = False) -> DNAMemoryRecord:
    return DNAMemoryRecord(
        id=f"id-{abs(hash(content)) % 100000}",
        content=content,
        memory_type=memory_type,
        confidence=confidence,
        confidence_ceiling=0.95,
        source="data_derived",
        user_confirmed=confirmed,
        tags=[],
        active=True,
    )


class FakeDNA:
    def __init__(self, by_type: dict[str, list[DNAMemoryRecord]] | None = None,
                 explode: bool = False) -> None:
        self.by_type = by_type or {}
        self.explode = explode

    def list_memories(self, memory_type=None, limit=200, **_kw) -> list[DNAMemoryRecord]:
        if self.explode:
            raise RuntimeError("dna store down")
        return list(self.by_type.get(memory_type or "", []))[:limit]


# ---------------------------------------------------------------------------
# 1-2. The facade and the arithmetic half
# ---------------------------------------------------------------------------


def test_facade_and_momentum() -> None:
    print("\n=== 1. The facade exposes the purpose ===")
    check(callable(read_patterns), "orchestrator.memory.read_patterns is callable")
    check("read_patterns" in memory_facade.__all__, "it is advertised in __all__")
    check(
        not callable(memory_facade.patterns),
        "orchestrator.memory.patterns is the MODULE, not a callable",
        type(memory_facade.patterns).__name__,
    )
    check(
        DERIVED_TYPES == ("observation", "insight"),
        "the derived memory types are declared",
        str(DERIVED_TYPES),
    )

    print("\n=== 2. Momentum is arithmetic, computed fresh ===")
    view = read_patterns(
        memory_manager=FakeManager(_pattern_events()), dna_store=FakeDNA(), now=NOW
    )
    check(view.momentum is not None, "momentum is present")
    if view.momentum:
        m = view.momentum
        check(m.streak_days >= 0, "streak is a non-negative day count", str(m.streak_days))
        check(
            0.0 <= m.completion_rate_7d <= 1.0,
            "the 7-day rate is a fraction",
            str(m.completion_rate_7d),
        )
        check(
            0.0 <= m.completion_rate_today <= 1.0,
            "today's rate is a fraction",
            str(m.completion_rate_today),
        )
        check(m.blocks_today >= 0, "blocks today is a count", str(m.blocks_today))
        check(
            m.momentum_trend in ("rising", "declining", "stable", "unknown"),
            "the trend is one of the known labels",
            m.momentum_trend,
        )

    print("\n--- the arithmetic itself is a pure function of the events ---")
    # Asserted on the pure layer, not on compute_momentum: that one builds its
    # windows from the REAL clock, so pinning an exact rate through it would
    # assert the wall clock rather than the arithmetic.
    from orchestrator.cognition.metrics import compute_completion_rate

    check(
        abs(compute_completion_rate(_pattern_events()) - 0.375) < 1e-9,
        "3 of the 8 fixture events completed reads as 0.375",
        str(compute_completion_rate(_pattern_events())),
    )


# ---------------------------------------------------------------------------
# 3-4. Fresh detection — real detectors, real guard
# ---------------------------------------------------------------------------


def test_detection() -> None:
    print("\n=== 3. A real event set triggers a real detector ===")
    view = read_patterns(
        memory_manager=FakeManager(_pattern_events()), dna_store=FakeDNA(), now=NOW
    )
    check(len(view.detected) >= 1, "at least one pattern was detected", f"{len(view.detected)}")
    keys = [p.key or "" for p in view.detected]
    check(
        any(k.startswith("time_of_day") for k in keys),
        "the time-of-day detector fired on 3/4 mornings vs 0/4 evenings",
        str(keys),
    )
    if view.detected:
        first = view.detected[0]
        check(first.source == "detected-now", "detected items are labelled as such", first.source)
        check(bool(first.content), "a detected pattern has content", first.content[:80])
        check(bool(first.evidence), "and carries the numbers behind it", str(first.evidence)[:80])

    print("\n=== 4. The cold-start guard is respected ===")
    thin = read_patterns(
        memory_manager=FakeManager(_pattern_events()[:3]), dna_store=FakeDNA(), now=NOW
    )
    check(
        thin.detected == [],
        "3 events is below MIN_EVENTS, so nothing is derived",
        f"{len(thin.detected)} detected",
    )
    no_events = read_patterns(memory_manager=FakeManager([]), dna_store=FakeDNA(), now=NOW)
    check(no_events.detected == [], "and an idle window derives nothing either")


# ---------------------------------------------------------------------------
# 5-7. Stored patterns, provenance, determinism
# ---------------------------------------------------------------------------


def test_stored_and_provenance() -> None:
    print("\n=== 5. Stored observations carry confidence, most-trusted first ===")
    dna = FakeDNA(
        by_type={
            "observation": [
                _record("Nik finishes deep work before noon.", "observation", 0.72),
                _record("Nik postpones admin blocks.", "observation", 0.55),
            ],
            "insight": [_record("Pressure reduces his scope, not his standards.", "insight", 0.64)],
        }
    )
    view = read_patterns(memory_manager=FakeManager([]), dna_store=dna, now=NOW)
    check(len(view.observations) == 3, "both derived types are read", str(len(view.observations)))
    check(
        view.observations[0].content.startswith("Nik finishes deep work"),
        "ordered by confidence, highest first",
        view.observations[0].content[:50],
    )
    confidences = [p.confidence or 0.0 for p in view.observations]
    check(confidences == sorted(confidences, reverse=True), "a true descending confidence")
    check(all(p.source == "stored" for p in view.observations), "all are labelled stored")

    print("\n=== 6. The two sources are labelled differently ===")
    both = read_patterns(memory_manager=FakeManager(_pattern_events()), dna_store=dna, now=NOW)
    check({p.source for p in both.observations} == {"stored"}, "stored items say stored")
    check({p.source for p in both.detected} == {"detected-now"}, "fresh items say detected-now")
    if both.detected:
        check(
            "not yet re-observed" in both.detected[0].provenance(),
            "and their provenance warns they are unearned",
            both.detected[0].provenance(),
        )
    if both.observations:
        check(
            "conf " in both.observations[0].provenance(),
            "while a stored pattern reports its confidence",
            both.observations[0].provenance(),
        )

    print("\n=== 7. Deterministic at a fixed moment ===")
    first = read_patterns(memory_manager=FakeManager(_pattern_events()), dna_store=FakeDNA(), now=NOW)
    second = read_patterns(memory_manager=FakeManager(_pattern_events()), dna_store=FakeDNA(), now=NOW)
    check(
        [p.content for p in first.detected] == [p.content for p in second.detected],
        "two reads at the same moment detect the same patterns",
    )


# ---------------------------------------------------------------------------
# 8-10. Fail-open, rendering, invariants
# ---------------------------------------------------------------------------


def test_fail_open() -> None:
    print("\n=== 8. Each source fails independently and names itself ===")
    no_momentum = read_patterns(
        memory_manager=FakeManager(_pattern_events(), explode=True), dna_store=FakeDNA(), now=NOW
    )
    check(
        any(d.startswith("momentum:") for d in no_momentum.degraded),
        "a broken schedule store is named for momentum",
        str(no_momentum.degraded),
    )
    check(
        any(d.startswith("detected_patterns:") for d in no_momentum.degraded),
        "and for live detection",
        str(no_momentum.degraded),
    )

    no_dna = read_patterns(
        memory_manager=FakeManager(_pattern_events()), dna_store=FakeDNA(explode=True), now=NOW
    )
    check(
        any(d.startswith("stored_patterns:") for d in no_dna.degraded),
        "a broken DNA store is named",
        str(no_dna.degraded),
    )
    check(
        no_dna.momentum is not None and no_dna.detected,
        "while the other two sources still answer — a partial picture, not an exception",
    )

    original = memory_facade.patterns._default_memory_manager
    try:
        def _no_db():
            raise RuntimeError("no database")

        memory_facade.patterns._default_memory_manager = _no_db
        unwired = read_patterns(dna_store=FakeDNA(), now=NOW)
        check(
            any(d.startswith("memory_manager:") for d in unwired.degraded),
            "a manager that cannot be built names itself",
            str(unwired.degraded),
        )
    finally:
        memory_facade.patterns._default_memory_manager = original


def test_rendering_and_invariants() -> None:
    print("\n=== 9. Rendering ===")
    check(render_patterns(PatternsView()) == "", "an empty view renders to '' — nothing invented")

    dna = FakeDNA(
        by_type={"observation": [_record("Nik finishes deep work before noon.", "observation", 0.72)]}
    )
    text = render_patterns(
        read_patterns(memory_manager=FakeManager(_pattern_events()), dna_store=dna, now=NOW)
    )
    check("WHAT IT MEANS" in text, "the block has a header")
    check("Momentum, computed now" in text, "momentum renders as computed")
    check("Seen repeatedly" in text, "the stored section renders")
    check("conf 0.72" in text, "with its confidence")
    check("NOT yet re-observed" in text, "the fresh section is marked unearned")
    check("do not state these as known habits" in text, "and carries the instruction")
    check(
        len(text.splitlines()) <= 20,
        "the block stays prompt-sized",
        f"{len(text.splitlines())} lines",
    )
    print("\n" + text)

    degraded = render_patterns(
        read_patterns(memory_manager=FakeManager(explode=True), dna_store=FakeDNA(), now=NOW)
    )
    check("Unreadable right now" in degraded, "a degraded read says so in the block")

    print("\n=== 10. Read-only, and the boundary is honest ===")
    source = (ROOT / "orchestrator/memory/patterns.py").read_text(encoding="utf-8")
    mutators = [
        name for name in (
            "set_profile_fact", "create_memory", "confirm_memory", "revise_memory",
            "deactivate", "add_episodic_event", "upsert_with_checks", "place_time_block",
            "set_anchor", "create_schedule_event",
        )
        if name in source
    ]
    check(not mutators, f"the module calls no mutator ({mutators})")
    check(
        "gather_pattern_candidates" in source,
        "fresh detection reuses the SAME detectors the weekly job uses (no second implementation)",
    )
    # The docstring NAMES both of these to explain the boundary, so the check is
    # for a call or an import — a bare substring test would match the prose.
    check(
        "derive_weekly_observations(" not in source
        and "import derive_weekly_observations" not in source,
        "but it does not run the weekly job — that WRITES, and this module reads",
    )
    check(
        "compute_drift(" not in source,
        "and drift is deliberately out of scope: nothing owns the intended time split",
    )


def main() -> int:
    print("=" * 78)
    print("MEMORY P4 — PATTERNS (what it means)")
    print("=" * 78)
    test_facade_and_momentum()
    test_detection()
    test_stored_and_provenance()
    test_fail_open()
    test_rendering_and_invariants()

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