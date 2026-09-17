"""
scripts/tests/test_dna_memory_store.py

Phase 1 verification suite for DNAMemoryStore (dna_memory_redesign_v2.md).

Covers the lifecycle math and the §11.1 invariants:
  1. Source-based starting confidence / ceiling / user_confirmed defaults (§4.1)
  2. Explicit confidence capped by source ceiling
  3. mentor_inferred confirmation growth asymptotes at the 0.6 ceiling (§4.2)
  4. User confirmation unlocks the ceiling (§4.2)
  5. Revision supersedes with audit trail; contradiction = user correction (§4.4)
  6. Weekly decay: stale unvalidated memories fade; user-stated exempt (§4.4)
  7. Semantic retrieval ranks the right memory first (§7.1)
  8. Deterministic layer: due dates, core identity, pending validation (§7.1)
  9. Upsert DISTINCT fast path (no LLM compare below the similarity floor) (§6)
 10. Upsert SAME → confirm existing, no duplicate (§6)
 11. Upsert REFINES → merged revision supersedes (§6)
 12. Upsert CONTRADICTS → old belief archived, user_confirmed correction (§6)
 13. Deactivate archives and excludes from retrieval
 14. Every mutation wrote a memory_op audit event (§11.2)

Runs against the real local Postgres DB but wipes only the dna_memory table
(brand-new, unused by anything else) and dna_memory-sourced audit events.
No real LLM calls — compare/merge functions are injected fakes.

Run from project root:
    uv run python scripts/tests/test_dna_memory_store.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.memory.dna_store import DNAMemoryStore

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


def force_stale(store: DNAMemoryStore, memory_id: str, days: int = 60) -> None:
    """Backdate last_confirmed so the decay job treats the memory as stale."""
    with store.SessionLocal() as session:
        session.execute(
            text("UPDATE dna_memory SET last_confirmed = :ts WHERE id = :mid"),
            {"ts": datetime.now(timezone.utc) - timedelta(days=days), "mid": memory_id},
        )
        session.commit()


def clean_slate(store: DNAMemoryStore) -> None:
    with store.SessionLocal() as session:
        session.execute(text("DELETE FROM dna_memory"))
        session.execute(text("DELETE FROM episodic_events WHERE source_agent = 'dna_memory'"))
        session.commit()


def assert_safe_to_wipe() -> None:
    """
    Refuse to run this suite against a database that is not clearly a test DB.

    This file calls `clean_slate()`, which issues `DELETE FROM dna_memory` and
    commits. Because the store is built from `DATABASE_URL`, running it as
    `uv run python scripts/tests/test_dna_memory_store.py` used to silently wipe the
    mentor's REAL organic memory (and its dna_memory-sourced episodic events) on
    every run. There was no guard, no separate test database, and no prompt.

    Failing loudly is the whole point: a data-loss bug must be impossible to hit
    by accident, and recoverable when it is hit on purpose.
    """
    from orchestrator.config import DB_URL

    if os.getenv("DNA_TEST_ALLOW_WIPE") == "1":
        return
    if "test" in str(DB_URL).lower():
        return
    raise SystemExit(
        "\n[test_dna_memory_store] REFUSING TO RUN: this suite deletes every row\n"
        "of `dna_memory` (and the episodic events it wrote) and then commits.\n"
        f"DATABASE_URL points at: {DB_URL}\n"
        "\n"
        "  1. Point DATABASE_URL at a throwaway/test database, e.g.\n"
        "       DATABASE_URL=postgresql://nik:nik@localhost:5432/ai_companion_test \\\n"
        "         uv run python scripts/tests/test_dna_memory_store.py\n"
        "  2. Or, if you truly intend to wipe the live memory store:\n"
        "       DNA_TEST_ALLOW_WIPE=1 uv run python scripts/tests/test_dna_memory_store.py\n"
        "     (export first: `uv run python scripts/tools/manage_memory.py export > memory.json`)\n"
    )


def active_count(store: DNAMemoryStore) -> int:
    with store.SessionLocal() as session:
        return session.execute(
            text("SELECT COUNT(*) FROM dna_memory WHERE active = true")
        ).scalar()


def main() -> int:
    store = DNAMemoryStore()
    print("Setting up schema (dna_memory table + indexes)...")
    store.ensure_schema()
    assert_safe_to_wipe()
    clean_slate(store)

    # ------------------------------------------------------------------
    print("\n[1] Source-based defaults (§4.1)")
    m_user = store.create_memory("Nik is preparing for AI Engineer interviews.", "fact", "user_stated")
    m_seeded = store.create_memory("Nik is 23, from Karauli, Rajasthan.", "fact", "seeded")
    m_data = store.create_memory("Nik completes 85% of morning blocks.", "observation", "data_derived")
    m_inferred = store.create_memory("Nik seems to avoid System Design blocks.", "observation", "mentor_inferred")

    check("user_stated → conf 0.95, ceiling 1.0, user_confirmed",
          m_user.confidence == 0.95 and m_user.confidence_ceiling == 1.0 and m_user.user_confirmed)
    check("seeded → conf 0.95, user_confirmed",
          m_seeded.confidence == 0.95 and m_seeded.user_confirmed)
    check("data_derived → conf 0.70, ceiling 0.95, not user_confirmed",
          m_data.confidence == 0.70 and m_data.confidence_ceiling == 0.95 and not m_data.user_confirmed)
    check("mentor_inferred → conf 0.40, ceiling 0.60, not user_confirmed",
          m_inferred.confidence == 0.40 and m_inferred.confidence_ceiling == 0.60 and not m_inferred.user_confirmed)

    # ------------------------------------------------------------------
    print("\n[2] Explicit confidence is capped by the source ceiling")
    m_capped = store.create_memory("Nik might prefer morning study.", "preference", "mentor_inferred", confidence=0.9)
    check("mentor_inferred with confidence=0.9 → stored 0.60", m_capped.confidence == 0.60,
          f"got {m_capped.confidence}")

    # ------------------------------------------------------------------
    print("\n[3] mentor_inferred growth asymptotes at the 0.6 ceiling (§4.2)")
    confs = []
    for _ in range(8):
        confs.append(store.confirm_memory(m_inferred.id).confidence)
    check("confidence grows above the 0.40 start", confs[0] > 0.40, f"confs={confs[:3]}")
    check("confidence never exceeds 0.6 ceiling across 8 confirms",
          all(c <= 0.60 for c in confs), f"confs={confs}")
    final = store.get_memory(m_inferred.id)
    check("confirmation_count incremented (1 + 8 = 9)", final.confirmation_count == 9,
          f"got {final.confirmation_count}")

    # ------------------------------------------------------------------
    print("\n[4] User confirmation unlocks the ceiling (§4.2)")
    unlocked = store.confirm_memory(m_inferred.id, by_user=True)
    check("user_confirmed=True, ceiling 0.95, confidence ≥ 0.85",
          unlocked.user_confirmed and unlocked.confidence_ceiling == 0.95 and unlocked.confidence >= 0.85,
          f"conf={unlocked.confidence} ceiling={unlocked.confidence_ceiling}")

    # ------------------------------------------------------------------
    print("\n[5] Revision + contradiction (§4.4)")
    refined = store.revise_memory(m_capped.id, "Nik prefers 45-60 min morning deep-focus blocks.", reason="refinement")
    old = store.get_memory(m_capped.id)
    check("old memory archived with superseded_by set",
          not old.active and old.superseded_by == refined.id)
    check("new memory inherits type/source and confidence",
          refined.memory_type == "preference" and refined.source == "mentor_inferred"
          and refined.confidence == m_capped.confidence)

    corrected = store.revise_memory(m_inferred.id, "Nik loves System Design; he only feared interview-level questions.",
                                    reason="user correction", contradiction=True)
    check("contradiction → new memory user_confirmed, conf ≥ 0.85, ceiling 0.95",
          corrected.user_confirmed and corrected.confidence >= 0.85 and corrected.confidence_ceiling == 0.95)
    check("contradiction archives the old belief", not store.get_memory(m_inferred.id).active)

    # ------------------------------------------------------------------
    print("\n[6] Weekly decay (§4.4)")
    m_fade = store.create_memory("Nik seemed tired on Sunday calls.", "observation", "mentor_inferred", confidence=0.5)
    m_hopeless = store.create_memory("Nik once mentioned an obscure tooling interest.", "observation", "mentor_inferred", confidence=0.17)
    m_stated_stale = store.create_memory("Nik said he dislikes early calls.", "preference", "user_stated", confidence=0.7)
    for mid in (m_fade.id, m_hopeless.id, m_stated_stale.id):
        force_stale(store, mid, days=60)

    report = store.decay_stale()
    faded = store.get_memory(m_fade.id)
    hopeless = store.get_memory(m_hopeless.id)
    stated = store.get_memory(m_stated_stale.id)
    check("stale mentor_inferred decays ×0.85 (0.5 → 0.425)",
          abs(faded.confidence - 0.425) < 1e-6, f"got {faded.confidence}")
    check("below 0.15 after decay → archived", not hopeless.active,
          f"conf={hopeless.confidence} active={hopeless.active}")
    check("user_stated memory exempt from decay", stated.active and stated.confidence == 0.7,
          f"conf={stated.confidence}")
    check("decay report counts", report["decayed"] >= 2 and report["archived"] >= 1,
          f"report={report}")


    # ------------------------------------------------------------------
    print("\n[7] Semantic retrieval ranks correctly (§7.1)")
    store.create_memory("Nik is preparing for AI Engineer interviews with a System Design focus.", "goal", "user_stated")
    store.create_memory("Nik enjoys cooking pasta on weekends.", "context", "user_stated")
    store.create_memory("LangGraph checkpointing patterns for production agents.", "fact", "seeded")
    hits = store.retrieve("how is the system design interview prep going?", top_k=3)
    check("top hit is the System Design memory",
          "System Design" in hits[0].content, f"top={hits[0].content[:80]}")
    check("composite scores attached and descending",
          all(h.composite_score is not None for h in hits)
          and hits[0].composite_score >= hits[-1].composite_score)

    # ------------------------------------------------------------------
    print("\n[8] Deterministic layer (§7.1)")
    soon = datetime.now(timezone.utc) + timedelta(days=10)
    far = datetime.now(timezone.utc) + timedelta(days=60)
    m_due = store.create_memory("Nik has a Google interview in ~10 days.", "fact", "user_stated", due_at=soon)
    store.create_memory("Nik has a vague plan to visit Japan someday.", "goal", "user_stated", due_at=far)
    det = store.get_deterministic()
    check("due-soon memory injected", any(m.id == m_due.id for m in det["due"]))
    check("due-far memory NOT injected", all("Japan" not in m.content for m in det["due"]))
    check("core identity = high-confidence facts/goals only",
          all(m.memory_type in ("fact", "goal") and m.confidence >= 0.85 for m in det["core"]))
    check("pending validation = unconfirmed mentor inference",
          all(m.source == "mentor_inferred" and not m.user_confirmed for m in det["pending_validation"]))

    # ------------------------------------------------------------------
    print("\n[9] Upsert DISTINCT fast path — no LLM call below floor (§6)")
    def _exploding_compare(**kwargs):
        raise AssertionError("compare_fn must not be called for clearly distinct content")
    before = active_count(store)
    m_distinct = store.upsert_with_checks(
        "The best sourdough starter feeding ratio is 1:2:2 with rye flour.",
        compare_fn=_exploding_compare,
        memory_type="fact", source="user_stated",
    )
    check("distinct content creates a new memory without compare", active_count(store) == before + 1
          and m_distinct is not None)

    # ------------------------------------------------------------------
    print("\n[10] Upsert SAME → confirm, no duplicate (§6)")
    m_avoid = store.create_memory("Nik avoids System Design study blocks.", "observation", "mentor_inferred")
    before = active_count(store)
    same_result = store.upsert_with_checks(
        "Nik tends to avoid his System Design blocks.",
        compare_fn=lambda **_: "SAME",
        memory_type="observation", source="mentor_inferred",
    )
    check("SAME returns the existing memory (no new row)",
          same_result.id == m_avoid.id and active_count(store) == before,
          f"returned={same_result.id} expected={m_avoid.id}")
    check("SAME bumped confirmation_count", same_result.confirmation_count == 2,
          f"got {same_result.confirmation_count}")

    # ------------------------------------------------------------------
    print("\n[11] Upsert REFINES → merged revision supersedes (§6)")
    merged_text = "Nik avoids System Design study blocks, mostly on low-energy days."
    refined_up = store.upsert_with_checks(
        "Nik avoids System Design blocks on low-energy days.",
        compare_fn=lambda **_: "REFINES",
        merge_fn=lambda **_: merged_text,
        memory_type="observation", source="mentor_inferred",
    )
    check("REFINES supersedes the old memory",
          not store.get_memory(m_avoid.id).active and refined_up.id != m_avoid.id)
    check("REFINES stores the merged content", refined_up.content == merged_text)

    # ------------------------------------------------------------------
    print("\n[12] Upsert CONTRADICTS → correction survives dedup (§6)")
    # NB: the upsert text below is nearest to test [5]'s `corrected` memory,
    # so the similarity search should select THAT row for the revise path.
    contra = store.upsert_with_checks(
        "Nik actually loves System Design — he only feared interview-level questions.",
        compare_fn=lambda **_: "CONTRADICTS",
        memory_type="observation", source="mentor_inferred",
    )
    superseded = store.get_memory(corrected.id)
    check("CONTRADICTS archives the nearest existing memory (superseded_by set)",
          not superseded.active and superseded.superseded_by == contra.id,
          f"superseded_by={superseded.superseded_by} contra.id={contra.id}")
    check("CONTRADICTS creates user_confirmed correction",
          contra.user_confirmed and contra.confidence >= 0.85)

    # ------------------------------------------------------------------
    print("\n[13] Deactivate archives and excludes from retrieval")
    ok = store.deactivate(m_distinct.id)
    hits_after = store.retrieve("sourdough bread starter feeding ratio", top_k=5)
    check("deactivate returns True and archives",
          ok and not store.get_memory(m_distinct.id).active)
    check("deactivated memory absent from retrieval",
          all(m.id != m_distinct.id for m in hits_after))

    # ------------------------------------------------------------------
    print("\n[14] Audit trail (§11.2)")
    with store.SessionLocal() as session:
        audit_rows = session.execute(text(
            "SELECT COUNT(*) FROM episodic_events "
            "WHERE source_agent = 'dna_memory' AND event_type = 'memory_op'"
        )).scalar()
    check("memory_op audit events were written", audit_rows > 0, f"count={audit_rows}")

    # ------------------------------------------------------------------
    clean_slate(store)
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed\n{'=' * 60}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

