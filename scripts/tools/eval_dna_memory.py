"""
scripts/tools/eval_dna_memory.py

Golden-set evaluation harness for DNA memory (dna_memory_redesign_v2.md §11.1).

Runs scripted conversation turns against a clean store and asserts the
system-level invariants that make organic memory trustworthy:

  1. NO SELF-CONFIRMATION LOOP — neutral turns leave confidence/last_confirmed
     untouched, and even a burst of behavioral confirms never crosses the
     0.6 ceiling without explicit user affirmation.
  2. CORRECTION SURVIVES DEDUP — a contradicting statement revises (archives)
     the old belief instead of confirming or duplicating it.
  3. DEADLINES ARE DETERMINISTIC — a due_at memory appears in the context
     document even for a semantically unrelated query.
  4. SELECTIVITY HOLDS — routine turns create zero new memories.

Reflections are INJECTED (simulating what a correct LLM should output) so the
harness is deterministic and tests store + context assembly, not LLM luck.
Run it after any change to reflection prompts, compare prompts, or §7 weights.

    uv run python scripts/tools/eval_dna_memory.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.memory.dna_context import build_dna_context
from orchestrator.memory.dna_reflection import (
    Confirmation,
    MemoryReflection,
    MemoryRevision,
    apply_reflection,
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


def clean_slate(store: DNAMemoryStore) -> None:
    with store.SessionLocal() as session:
        session.execute(text("DELETE FROM dna_memory"))
        session.execute(text("DELETE FROM episodic_events WHERE source_agent = 'dna_memory'"))
        session.commit()


# ---------------------------------------------------------------------------
# Invariant 1 — no self-confirmation loop
# ---------------------------------------------------------------------------

def eval_self_confirmation_guard(store: DNAMemoryStore) -> None:
    print("\n[Eval 1] No self-confirmation loop")
    mem = store.create_memory(
        "Nik seems distracted in evening study blocks.",
        "observation", "mentor_inferred",
    )
    before = store.get_memory(mem.id)

    # Three neutral turns that merely *mention* the topic — a correct
    # reflection yields nothing (the confirmation-bias trap stays shut).
    for turn in range(3):
        apply_reflection(store, MemoryReflection(reasoning=f"neutral turn {turn}"))

    after = store.get_memory(mem.id)
    check("neutral turns: confidence unchanged",
          after.confidence == before.confidence,
          f"{before.confidence} → {after.confidence}")
    check("neutral turns: confirmation_count unchanged",
          after.confirmation_count == before.confirmation_count)

    # Even aggressive behavioral confirms asymptote at the 0.6 ceiling.
    for _ in range(10):
        apply_reflection(store, MemoryReflection(
            confirmations=[Confirmation(memory_id=mem.id, evidence_class="user_action")]
        ))
    capped = store.get_memory(mem.id)
    check("10 user_action confirms never cross the 0.6 ceiling",
          capped.confidence <= 0.6 and not capped.user_confirmed,
          f"conf={capped.confidence}")

    # One explicit affirmation unlocks it.
    apply_reflection(store, MemoryReflection(
        confirmations=[Confirmation(memory_id=mem.id, evidence_class="user_affirmed")]
    ))
    affirmed = store.get_memory(mem.id)
    check("single user_affirmed unlocks to ≥ 0.85",
          affirmed.user_confirmed and affirmed.confidence >= 0.85)


# ---------------------------------------------------------------------------
# Invariant 2 — correction survives dedup
# ---------------------------------------------------------------------------

def eval_correction_survives_dedup(store: DNAMemoryStore) -> None:
    print("\n[Eval 2] Correction survives dedup")
    old = store.create_memory(
        "Nik hates grinding DSA problem sets.",
        "preference", "mentor_inferred", confidence=0.5,
    )
    # User corrects — the reflection issues REVISE with contradicts_prior.
    apply_reflection(store, MemoryReflection(
        revisions=[MemoryRevision(
            memory_id=old.id,
            new_content="Nik actually enjoys DSA practice when framed as interview prep.",
            reason="user explicitly corrected",
            contradicts_prior=True,
        )]
    ))
    archived = store.get_memory(old.id)
    check("old belief archived with superseded_by",
          not archived.active and archived.superseded_by is not None)

    hits = store.retrieve("how does Nik feel about DSA practice?", top_k=3)
    check("retrieval surfaces the correction, not the old belief",
          hits and "enjoys DSA" in hits[0].content and all(m.id != old.id for m in hits),
          f"top={hits[0].content[:60] if hits else 'none'}")
    check("correction is user_confirmed",
          hits and hits[0].user_confirmed)



# ---------------------------------------------------------------------------
# Invariant 3 — deadlines are deterministic
# ---------------------------------------------------------------------------

def eval_deadlines_deterministic(store: DNAMemoryStore) -> None:
    print("\n[Eval 3] Deadlines are deterministic (§7.1)")
    mm = MemoryManager()
    soon = datetime.now(timezone.utc) + timedelta(days=9)
    store.create_memory(
        "Nik has a Google interview coming up.",
        "fact", "user_stated", due_at=soon,
    )
    # Query about something semantically unrelated — the deadline must still
    # appear via the deterministic layer, not retrieval luck.
    context = build_dna_context(mm, store, "what should I cook for dinner tonight?")
    check("deadline section present for unrelated query",
          "Deadlines & Time-Sensitive" in context)
    check("deadline memory injected for unrelated query",
          "Google interview" in context)


# ---------------------------------------------------------------------------
# Invariant 4 — selectivity holds on routine turns
# ---------------------------------------------------------------------------

def eval_selectivity(store: DNAMemoryStore) -> None:
    print("\n[Eval 4] Selectivity holds (§5.3)")
    with store.SessionLocal() as session:
        before = session.execute(text("SELECT COUNT(*) FROM dna_memory WHERE active")).scalar()

    routine_turns = [
        MemoryReflection(reasoning="routine schedule question — nothing to store"),
        MemoryReflection(reasoning="user asked for a definition — nothing to store"),
        MemoryReflection(),  # completely empty
    ]
    for r in routine_turns:
        apply_reflection(store, r)

    with store.SessionLocal() as session:
        after = session.execute(text("SELECT COUNT(*) FROM dna_memory WHERE active")).scalar()
    check("routine turns create zero memories", before == after, f"{before} → {after}")


# ---------------------------------------------------------------------------
# Retrieval regression — composite ordering (§7.1)
# ---------------------------------------------------------------------------

def eval_retrieval_ordering(store: DNAMemoryStore) -> None:
    print("\n[Eval 5] Retrieval composite ordering (§7.1)")
    store.create_memory("Nik is targeting AI Engineer roles at LLM product companies.",
                        "goal", "user_stated")
    store.create_memory("Nik's grandfather was his biggest motivator.",
                        "context", "seeded")
    store.create_memory("Nik prefers 45-60 minute focus blocks.",
                        "preference", "seeded")

    hits = store.retrieve("career goals and target companies", top_k=3)
    check("goal memory ranks first for a career query",
          hits and hits[0].memory_type == "goal",
          f"top={hits[0].memory_type if hits else 'none'}")
    check("composite scores descending",
          all(h.composite_score is not None for h in hits)
          and all(hits[i].composite_score >= hits[i + 1].composite_score
                  for i in range(len(hits) - 1)))


# ---------------------------------------------------------------------------
# Context document shape (§7.2)
# ---------------------------------------------------------------------------

def eval_context_document(store: DNAMemoryStore) -> None:
    print("\n[Eval 6] Context document shape (§7.2)")
    mm = MemoryManager()
    context = build_dna_context(mm, store, "plan my study day")
    for section in (
        "Who Nik Is", "How Nik Works", "What's Happening Now",
        "Today's Schedule", "Personality Configuration", "Nik's Message",
    ):
        check(f"section present: {section}", section in context)
    check("provenance labels inline", "[goal · user-stated" in context)
    check("document is compact (< 4000 chars)", len(context) < 4000,
          f"len={len(context)}")


def assert_safe_to_wipe() -> None:
    """
    Refuse to run this eval against a database that is not clearly a test DB.

    This script wipes `dna_memory` (and the episodic events it wrote) before
    seeding its own fixtures. Since the store is built from DATABASE_URL, running
    it unguarded destroys the mentor's real organic memory. Same trap as
    scripts/tests/test_dna_memory_store.py — fail loudly instead.
    """
    from orchestrator.config import DB_URL

    if os.getenv("DNA_TEST_ALLOW_WIPE") == "1":
        return
    if "test" in str(DB_URL).lower():
        return
    raise SystemExit(
        "\n[eval_dna_memory] REFUSING TO RUN: this eval deletes every row of\n"
        "`dna_memory` (and its episodic events) before seeding fixtures.\n"
        f"DATABASE_URL points at: {DB_URL}\n"
        "\n"
        "  Use a throwaway DB:  DATABASE_URL=postgresql://nik:nik@localhost:5432/ai_companion_test \\\n"
        "                         uv run python scripts/tools/eval_dna_memory.py\n"
        "  Or force it:         DNA_TEST_ALLOW_WIPE=1 uv run python scripts/tools/eval_dna_memory.py\n"
    )


def main() -> int:
    store = DNAMemoryStore()
    store.ensure_schema()
    assert_safe_to_wipe()
    clean_slate(store)

    start = time.time()
    eval_self_confirmation_guard(store)
    eval_correction_survives_dedup(store)
    eval_deadlines_deterministic(store)
    eval_selectivity(store)
    eval_retrieval_ordering(store)
    eval_context_document(store)
    clean_slate(store)

    elapsed = time.time() - start
    print(f"\n{'=' * 60}\nEVAL RESULT: {PASSED} passed, {FAILED} failed ({elapsed:.1f}s)\n{'=' * 60}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

