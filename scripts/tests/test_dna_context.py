"""
scripts/tests/test_dna_context.py

Phase 3 verification suite (dna_memory_redesign_v2.md §7, §12):

  1. build_dna_context renders all required sections with provenance labels
  2. Deterministic layer: due memories + pending validation (max 1)
  3. Pending validation disappears after user confirmation
  4. Empty store → graceful document (no crash, no empty sections break)
  5. summarize_node (§12 Phase 4): DNA context is the ONLY path; a
     catastrophic context failure falls back to a minimal inline document
  6. Dedup: a core memory never appears twice in the document

Wipes only the dna_memory table + dna_memory audit events before/after.

Run from project root:
    uv run python scripts/tests/test_dna_context.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.memory.dna_context import build_dna_context
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


def main() -> int:
    store = DNAMemoryStore()
    store.ensure_schema()
    clean_slate(store)
    mm = MemoryManager()

    # ------------------------------------------------------------------
    print("\n[1] Empty store → graceful document")
    empty_doc = build_dna_context(mm, store, "hello")
    check("renders without crash on empty store", "Who Nik Is" in empty_doc)
    check("empty sections degrade gracefully", "(no identity memories yet)" in empty_doc)
    check("no deadline section when nothing is due",
          "Deadlines & Time-Sensitive" not in empty_doc)

    # ------------------------------------------------------------------
    print("\n[2] Populated store → full document")
    soon = datetime.now(timezone.utc) + timedelta(days=12)
    store.create_memory("Nik has a Google interview soon.", "fact", "user_stated", due_at=soon)
    store.create_memory("Nik is 23, from Karauli, Rajasthan.", "fact", "seeded")
    store.create_memory("Nik's long-term dream is robotics in Japan.", "goal", "seeded")
    store.create_memory("Nik learns best by building first, theory second.", "preference", "seeded")
    pending = store.create_memory("Nik seems to avoid System Design blocks.",
                                  "observation", "mentor_inferred", confidence=0.55)

    doc = build_dna_context(mm, store, "should I study system design today?")
    for section in ("Deadlines & Time-Sensitive", "Who Nik Is", "How Nik Works",
                    "What's Happening Now", "Today's Schedule",
                    "Profile Snapshot", "Personality Configuration", "Nik's Message"):
        check(f"section present: {section}", section in doc)
    check("due memory rendered with date", "Google interview" in doc and "due" in doc)
    check("provenance labels inline", "[fact · seeded" in doc or "[fact · user-stated" in doc)
    check("unconfirmed inference labeled", "unconfirmed" in doc)
    check("pending validation surfaced (max 1)", "Worth Validating" in doc)

    # ------------------------------------------------------------------
    print("\n[3] Pending validation disappears after user confirmation")
    store.confirm_memory(pending.id, by_user=True)
    doc2 = build_dna_context(mm, store, "should I study system design today?")
    check("no validation section once user_confirmed", "Worth Validating" not in doc2)

    # ------------------------------------------------------------------
    print("\n[4] Dedup — core memory never appears twice")
    doc3 = build_dna_context(mm, store, "robotics japan dream career")
    check("'robotics' memory appears exactly once",
          doc3.count("robotics in Japan") == 1,
          f"count={doc3.count('robotics in Japan')}")

    # ------------------------------------------------------------------
    print("\n[5] summarize_node — DNA context is the only path (§12 Phase 4)")
    import orchestrator.orchestrator as orch_mod

    state = {
        "working_memory": {"user_input": "hello mentor", "trigger": "user_message"},
        "memory_manager": mm,
    }

    out_dna = orch_mod.summarize_node(state)
    check("context_source is 'dna'",
          out_dna["working_memory"].get("context_source") == "dna")
    check("DNA document reaches summary_text",
          "Who Nik Is" in out_dna["summary_text"]
          and "Nik's Message" in out_dna["summary_text"])

    # Catastrophic failure → minimal inline fallback keeps the turn alive.
    # summarize_node imports build_dna_context lazily from dna_context, so
    # patch it at the source module for the deferred import to pick up.
    import orchestrator.memory.dna_context as dna_ctx_mod

    original = dna_ctx_mod.build_dna_context

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated catastrophic failure")

    dna_ctx_mod.build_dna_context = _explode
    try:
        out_fb = orch_mod.summarize_node(state)
    finally:
        dna_ctx_mod.build_dna_context = original
    check("catastrophic failure → minimal_fallback source",
          out_fb["working_memory"].get("context_source") == "minimal_fallback")
    check("fallback document keeps the turn alive",
          "minimal fallback" in out_fb["summary_text"]
          and "hello mentor" in out_fb["summary_text"])

    # ------------------------------------------------------------------
    clean_slate(store)
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed\n{'=' * 60}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

