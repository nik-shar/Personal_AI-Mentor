"""
scripts/tests/test_onboarding_discovery.py

Phase 3+4 verification suite — discovery-mode arc & memory sentence style:

  1. Curiosity agenda sanity (DISCOVERY_FACETS structure)
  2. Coverage store round-trip on the real DB (save/restore around it)
  3. get_onboarding_status logic via stubs: first contact, mid-discovery,
     coverage-based exit boundaries, fail-open on store outage
  4. Guidance blocks: first-contact opener vs. remaining-facets guidance
  5. Reflection integration: discovered_facets advances coverage (fake LLM)
  6. Reflection prompt carries the discovery facets + the emotionally-
     weighted sentence style rules (Phase 4), and .format() placeholders
     all resolve
  7. dna_context renders a discovery block iff status is active (real stores)

Non-destructive: stub-based where possible; the real discovery_coverage row
is saved and restored; no tables are wiped.

Run from project root:
    uv run python scripts/tests/test_onboarding_discovery.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.cognition.onboarding import (
    DISCOVERY_FACETS,
    FIRST_CONTACT_GUIDANCE,
    build_onboarding_guidance,
    get_covered_facets,
    get_onboarding_status,
    mark_facets_covered,
)
from orchestrator.memory.dna_context import build_dna_context
from orchestrator.memory.dna_reflection import (
    MEMORY_REFLECTION_PROMPT,
    MemoryReflection,
    _discovery_facet_block,
    run_reflection,
)
from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager

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


class StubStore:
    """Minimal DNAMemoryStore stand-in for status-logic tests."""

    def __init__(self, user_stated_count: int = 0):
        self._n = user_stated_count

    def list_memories(self, source=None, limit=100):
        return [object()] * self._n if source == "user_stated" else []


class StubMM:
    """In-memory MemoryManager stand-in (coverage row + session lookup)."""

    def __init__(self, coverage=None, has_session=False):
        self._facts = {"discovery_coverage": coverage or []}
        self._has_session = has_session

    def get_profile_fact(self, category, key):
        return self._facts.get(key)

    def set_profile_fact(self, category, key, value, source=None):
        self._facts[key] = value

    def get_last_conversation_session(self):
        return {"id": "past"} if self._has_session else None


class BrokenStore:
    def list_memories(self, **kwargs):
        raise RuntimeError("simulated store outage")


class FakeLLM:
    """Mimics ChatOpenAI.with_structured_output(...).invoke(...)."""

    def __init__(self, result):
        self._result = result

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return self._result


def main() -> None:
    # ------------------------------------------------------------------
    print("\n[1] Curiosity agenda sanity")
    check("10 discovery facets defined", len(DISCOVERY_FACETS) == 10,
          f"got {len(DISCOVERY_FACETS)}")
    check("facet ids unique + descriptions non-empty",
          len(set(DISCOVERY_FACETS)) == len(DISCOVERY_FACETS)
          and all(len(d) > 20 for d in DISCOVERY_FACETS.values()))

    # ------------------------------------------------------------------
    print("\n[2] Coverage store round-trip (real DB, save/restore)")
    mm = MemoryManager()
    saved = mm.get_profile_fact("system", "discovery_coverage")
    try:
        mm.set_profile_fact("system", "discovery_coverage", [], source="test")
        covered = mark_facets_covered(mm, ["daily_routine", "energy_patterns", "bogus_facet"])
        check("valid facets stored, unknown ids ignored",
              covered == {"daily_routine", "energy_patterns"}, f"got {covered}")
        check("get_covered_facets reads them back",
              get_covered_facets(mm) == {"daily_routine", "energy_patterns"})
        covered = mark_facets_covered(mm, ["daily_routine"])
        check("re-marking is idempotent (union, no duplicates)",
              covered == {"daily_routine", "energy_patterns"})
    finally:
        mm.set_profile_fact("system", "discovery_coverage",
                            saved if saved is not None else [], source="restore")

    # ------------------------------------------------------------------
    print("\n[3] get_onboarding_status logic (stubs)")
    s = get_onboarding_status(StubStore(0), StubMM(coverage=[], has_session=False))
    check("nothing known → active + first contact",
          s["active"] is True and s["first_contact"] is True)
    check("all facets remaining", len(s["remaining"]) == 10)

    s = get_onboarding_status(StubStore(5), StubMM(
        coverage=["daily_routine", "energy_patterns", "current_projects"], has_session=True))
    check("mid-discovery → active, not first contact, 7 remaining",
          s["active"] is True and s["first_contact"] is False and len(s["remaining"]) == 7)

    seven = list(DISCOVERY_FACETS)[:7]
    s = get_onboarding_status(StubStore(12), StubMM(coverage=seven, has_session=True))
    check("exit boundary: 7/10 facets + 12 memories → discovery ends",
          s["active"] is False)
    s = get_onboarding_status(StubStore(20), StubMM(coverage=seven[:6], has_session=True))
    check("below boundary: 6/10 facets (even with 20 memories) → still active",
          s["active"] is True)
    s = get_onboarding_status(StubStore(3), StubMM(coverage=list(DISCOVERY_FACETS), has_session=True))
    check("all facets covered → done regardless of memory count", s["active"] is False)
    check("store outage → fail-open to normal mode",
          get_onboarding_status(BrokenStore(), StubMM())["active"] is False)

    # ------------------------------------------------------------------
    print("\n[4] Guidance blocks")
    check("first-contact guidance: introduce + one opener + no interrogation",
          "introduce yourself" in FIRST_CONTACT_GUIDANCE
          and "ONE natural opener" in FIRST_CONTACT_GUIDANCE
          and "interrogation" in FIRST_CONTACT_GUIDANCE)
    g = build_onboarding_guidance({"first_contact": False,
                                   "remaining": ["daily_routine", "energy_patterns"]})
    check("discovery guidance lists remaining facets",
          "Still curious about" in g
          and DISCOVERY_FACETS["daily_routine"] in g
          and DISCOVERY_FACETS["energy_patterns"] in g)
    check("discovery guidance enforces the one-question rule",
          "ONE discovery question per turn" in g)
    g_full = build_onboarding_guidance({"first_contact": False, "remaining": []})
    check("no remaining facets → no curiosity list", "Still curious about" not in g_full)
    check("first_contact flag → opener block returned",
          build_onboarding_guidance({"first_contact": True}) == FIRST_CONTACT_GUIDANCE)


    # ------------------------------------------------------------------
    print("\n[5] Reflection integration: discovered_facets advances coverage")
    reflection = MemoryReflection(discovered_facets=["daily_routine", "bogus"])
    stub_mm = StubMM(coverage=[])
    store = DNAMemoryStore()
    store.ensure_schema()
    report = run_reflection(
        "I usually study late at night, that's when I focus best.",
        "Good to know — night owl deep work it is.",
        store=store,
        llm=FakeLLM(reflection),
        memory_manager=stub_mm,
    )
    check("coverage advanced with valid facets only",
          stub_mm._facts["discovery_coverage"] == ["daily_routine"],
          f"got {stub_mm._facts['discovery_coverage']}")
    check("report carries facets_covered", "facets_covered" in report)

    # ------------------------------------------------------------------
    print("\n[6] Reflection prompt: facets + emotionally-weighted style (Phase 4)")
    check("facet block generated from the single source of truth",
          all(fid in _discovery_facet_block() for fid in DISCOVERY_FACETS))
    check("style section present", "HOW TO WRITE A MEMORY" in MEMORY_REFLECTION_PROMPT)
    check("emotional-weight examples present",
          "feels pointless" in MEMORY_REFLECTION_PROMPT
          and "GOOD" in MEMORY_REFLECTION_PROMPT and "BAD" in MEMORY_REFLECTION_PROMPT)
    check("key-value phrasing explicitly forbidden",
          "a database field, not a memory" in MEMORY_REFLECTION_PROMPT)
    check("no manufactured drama rule", "manufacture drama" in MEMORY_REFLECTION_PROMPT)
    try:
        formatted_prompt = MEMORY_REFLECTION_PROMPT.format(
            retrieved_memories="x", user_input="y", mentor_response="z",
            timestamp="t", discovery_facets=_discovery_facet_block(),
        )
        format_ok = True
    except (KeyError, IndexError) as exc:
        formatted_prompt = ""
        format_ok = False
        print(f"    format error: {exc}")
    check("all .format() placeholders resolve", format_ok)
    check("DISCOVERY FACETS section present in the formatted prompt",
          "DISCOVERY FACETS" in formatted_prompt)

    # ------------------------------------------------------------------
    print("\n[7] dna_context renders a discovery block iff status is active")
    real_mm = MemoryManager()
    real_store = DNAMemoryStore()
    status = get_onboarding_status(real_store, real_mm)
    doc = build_dna_context(real_mm, real_store, "hey")
    has_block = ("Discovery Mode" in doc) or ("First Contact" in doc)
    check("discovery block presence consistent with status",
          has_block == status["active"],
          f"active={status['active']} block={has_block}")

    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()

