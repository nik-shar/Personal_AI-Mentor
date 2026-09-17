"""
scripts/tests/test_first_contact.py

Phase 6 verification suite (Notes.md — Cold-Start Emotional Intelligence):

  1. Onboarding detection: cold store → onboarding mode; ≥ threshold of
     user_stated memories → normal mode; store failure → fail-open to normal
  2. DNA context renders the onboarding block only during cold start
  3. ReasoningDecision.disclosure_type schema (defaults None, parses classes)
  4. Reasoner prompt carries the STEP 0 checkpoint, taxonomy, and boundary —
     and no stale "SITUATION REPORT" terminology
  5. Crisis guardrail is code-enforced: a crisis decision with action="route"
     is forced to direct_response with crisis guidance — never dispatched;
     non-crisis decisions pass through untouched (prompt-guided, not forced)
  6. Crisis footer: support line appended when the model omits it, never
     duplicated when the model includes it
  7. Personality cold-start defaults: accountability 3 / warm / consistency

Wipes only dna_memory + dna audit events (caller harness restores). The
mentor_personality profile row is saved and restored around section [7].

Run from project root:
    uv run python scripts/tests/test_first_contact.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.cognition.onboarding import (
    DISCOVERY_FACETS,
    get_onboarding_status,
    mark_facets_covered,
)
from orchestrator.memory.dna_context import build_dna_context
from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager
from orchestrator.nodes.reasoner import _REASONING_SYSTEM_PROMPT, ReasoningDecision

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


def clean_dna(store: DNAMemoryStore) -> None:
    with store.SessionLocal() as session:
        session.execute(text("DELETE FROM dna_memory"))
        session.execute(text("DELETE FROM episodic_events WHERE source_agent = 'dna_memory'"))
        session.commit()


class BrokenStore:
    def list_memories(self, **kwargs):
        raise RuntimeError("simulated store outage")


def main() -> int:
    store = DNAMemoryStore()
    store.ensure_schema()
    mm = MemoryManager()
    clean_dna(store)

    # ------------------------------------------------------------------
    print("\n[1] Discovery-mode status (coverage-based, fail-open)")
    saved_coverage = mm.get_profile_fact("system", "discovery_coverage")
    mm.set_profile_fact("system", "discovery_coverage", [], source="test")

    status = get_onboarding_status(store, mm)
    check("empty store + no coverage → discovery active", status["active"] is True)
    check("all facets still remaining", len(status["remaining"]) == len(DISCOVERY_FACETS))
    has_history = mm.get_last_conversation_session() is not None
    check("first-contact flag consistent with session history",
          status["first_contact"] == (not has_history))

    mark_facets_covered(mm, list(DISCOVERY_FACETS))
    status = get_onboarding_status(store, mm)
    check("all facets covered → discovery ends", status["active"] is False)
    check("store outage → fail-open to normal mode",
          get_onboarding_status(BrokenStore(), mm)["active"] is False)

    # ------------------------------------------------------------------
    print("\n[2] DNA context renders the discovery block only while active")
    clean_dna(store)
    mm.set_profile_fact("system", "discovery_coverage", [], source="test")
    cold_doc = build_dna_context(mm, store, "hello")
    check("discovery active → guidance block in context",
          "Discovery Mode" in cold_doc or "First Contact" in cold_doc)
    mark_facets_covered(mm, list(DISCOVERY_FACETS))
    warm_doc = build_dna_context(mm, store, "hello")
    check("coverage complete → no discovery block",
          "Discovery Mode" not in warm_doc and "First Contact" not in warm_doc)

    # Restore the real coverage row (test infrastructure, not user data)
    mm.set_profile_fact("system", "discovery_coverage",
                        saved_coverage if saved_coverage is not None else [],
                        source="restore")

    # ------------------------------------------------------------------
    print("\n[3] ReasoningDecision.disclosure_type schema")
    d = ReasoningDecision(action="direct_response", reasoning="x")
    check("defaults to None", d.disclosure_type is None)
    d2 = ReasoningDecision(action="direct_response", reasoning="x",
                           disclosure_type="venting")
    check("parses a disclosure class", d2.disclosure_type == "venting")

    # ------------------------------------------------------------------
    print("\n[4] Reasoner prompt carries the Phase 6 behavioral layer")
    check("STEP 0 checkpoint present", "STEP 0: EMOTIONAL CHECKPOINT" in _REASONING_SYSTEM_PROMPT)
    check("taxonomy classes present",
          all(k in _REASONING_SYSTEM_PROMPT for k in
              ("venting", "seeking_guidance", "burnout", "context_sharing", "crisis")))
    check("professional boundary present", "PROFESSIONAL BOUNDARY" in _REASONING_SYSTEM_PROMPT)
    check("never-a-therapist rule present", "never a" in _REASONING_SYSTEM_PROMPT
          and "therapist" in _REASONING_SYSTEM_PROMPT)
    check("no stale SITUATION REPORT terminology",
          "SITUATION REPORT" not in _REASONING_SYSTEM_PROMPT)

    # ------------------------------------------------------------------
    print("\n[5] Crisis guardrail is code-enforced (never LLM judgment alone)")
    import orchestrator.orchestrator as orch_mod

    crisis_decision = ReasoningDecision(
        action="route", agent_name="linkedin_writer",
        agent_pipeline=["linkedin_writer"], reasoning="route anyway",
        disclosure_type="crisis",
        needs_long_term_context=True,
    )
    original = orch_mod.run_reasoner
    orch_mod.run_reasoner = lambda summary: crisis_decision
    try:
        out = orch_mod.reason_node({"summary_text": "ctx", "working_memory": {}})
    finally:
        orch_mod.run_reasoner = original

    decision = out["reasoning_decision"]
    check("crisis route → forced direct_response", decision["action"] == "direct_response")
    check("crisis route → pipeline wiped", out["agent_pipeline"] == [])
    check("crisis route → crisis guidance injected as notes",
          decision.get("notes_for_agent") == orch_mod.CRISIS_RESPONSE_GUIDANCE)
    check("crisis route → long-term recall suppressed",
          decision.get("needs_long_term_context") is False)
    check("post-reasoner routing respects the override",
          orch_mod.route_after_reason({"reasoning_decision": decision}) == "direct_response_node")

    venting_route = ReasoningDecision(
        action="route", agent_name="linkedin_writer", reasoning="route",
        disclosure_type="venting",
    )
    orch_mod.run_reasoner = lambda summary: venting_route
    try:
        out2 = orch_mod.reason_node({"summary_text": "ctx", "working_memory": {}})
    finally:
        orch_mod.run_reasoner = original
    check("non-crisis disclosure passes through (prompt-guided, not forced)",
          out2["reasoning_decision"]["action"] == "route")

    # ------------------------------------------------------------------
    print("\n[6] Crisis footer — appended when missing, never duplicated")
    import orchestrator.llm as llm_mod

    class FakeLLM:
        def __init__(self, content):
            self.content = content
        def invoke(self, msgs):
            return SimpleNamespace(content=self.content)

    state = {
        "working_memory": {"user_input": "I don't see the point anymore."},
        "summary_text": "ctx",
        "reasoning_decision": {"disclosure_type": "crisis",
                               "notes_for_agent": orch_mod.CRISIS_RESPONSE_GUIDANCE},
    }
    real_get_llm = llm_mod.get_conversational_llm
    llm_mod.get_conversational_llm = lambda **kw: FakeLLM("I hear you — that sounds really heavy.")
    try:
        out3 = orch_mod.direct_response_node(state)
    finally:
        llm_mod.get_conversational_llm = real_get_llm
    check("support line appended when model omits it",
          "mental-health professional" in out3["response_text"], out3["response_text"][:120])

    llm_mod.get_conversational_llm = lambda **kw: FakeLLM(
        "That sounds heavy — please consider talking to a therapist you trust.")
    try:
        out4 = orch_mod.direct_response_node(state)
    finally:
        llm_mod.get_conversational_llm = real_get_llm
    check("no duplicate footer when model already points to support",
          out4["response_text"].count("therapist") == 1
          and "mental-health professional" not in out4["response_text"])

    # ------------------------------------------------------------------
    print("\n[7] Personality cold-start defaults (3 / warm / consistency)")
    import json as _json

    from orchestrator.cognition.personality import get_personality

    with mm.SessionLocal() as session:
        saved = session.execute(text(
            "SELECT value, source FROM profile_facts "
            "WHERE category = 'preferences' AND key = 'mentor_personality'"
        )).fetchone()
        session.execute(text(
            "DELETE FROM profile_facts "
            "WHERE category = 'preferences' AND key = 'mentor_personality'"
        ))
        session.commit()
    try:
        defaults = get_personality(mm)
        check("cold-start accountability is 3", defaults["accountability_level"] == 3,
              f"got {defaults['accountability_level']}")
        check("cold-start tone is warm", defaults["tone"] == "warm")
        check("cold-start emphasis is consistency",
              defaults["coaching_emphasis"] == "consistency")
    finally:
        if saved:
            with mm.SessionLocal() as session:
                session.execute(text(
                    "INSERT INTO profile_facts (category, key, value, source, updated_at) "
                    "VALUES ('preferences', 'mentor_personality', CAST(:val AS jsonb), :src, NOW())"
                ), {"val": _json.dumps(saved[0]), "src": saved[1]})
                session.commit()

    # ------------------------------------------------------------------
    clean_dna(store)
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed\n{'=' * 60}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())


