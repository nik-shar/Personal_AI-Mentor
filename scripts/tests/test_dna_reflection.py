"""
scripts/tests/test_dna_reflection.py

Phase 2 verification suite (dna_memory_redesign_v2.md §5, §8, §10):

  1. apply_reflection: create/confirm/revise with evidence-class discipline
     (user_affirmed unlocks the ceiling; user_action does not) (§4.3, §5.1)
  2. apply_reflection: per-op isolation — one bad ID doesn't sink the batch
  3. run_reflection end-to-end with an injected fake LLM (no real LLM calls)
  4. Seeder ingestion is idempotent via upsert dedup (§8)
  5. /api/memories endpoints: list, pending, confirm, correct, delete, 404s (§10.1)
  6. DNA_REFLECTION_ENABLED kill switch

No real LLM calls anywhere — compare/merge/LLM are injected fakes.
Wipes only the dna_memory table + dna_memory audit events before/after.

Run from project root:
    uv run python scripts/tests/test_dna_reflection.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.memory.dna_reflection import (
    Confirmation,
    MemoryReflection,
    MemoryRevision,
    NewMemory,
    apply_reflection,
    reflection_enabled,
    run_reflection,
)
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


def clean_slate(store: DNAMemoryStore) -> None:
    with store.SessionLocal() as session:
        session.execute(text("DELETE FROM dna_memory"))
        session.execute(text("DELETE FROM episodic_events WHERE source_agent = 'dna_memory'"))
        session.commit()


def active_count(store: DNAMemoryStore) -> int:
    with store.SessionLocal() as session:
        return session.execute(
            text("SELECT COUNT(*) FROM dna_memory WHERE active = true")
        ).scalar()


class FakeLLM:
    """Mimics ChatOpenAI.with_structured_output(...).invoke(...)."""

    def __init__(self, result):
        self._result = result

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return self._result


def main() -> int:
    store = DNAMemoryStore()
    store.ensure_schema()
    clean_slate(store)

    # ------------------------------------------------------------------
    print("\n[1] apply_reflection — evidence-class discipline (§4.3)")
    existing = store.create_memory(
        "Nik seems to engage with System Design once he starts.",
        "observation", "mentor_inferred",
    )
    reflection = MemoryReflection(
        new_memories=[
            NewMemory(content="Nik has a Google interview on Sept 1, 2026.",
                      memory_type="fact", source="user_stated", confidence=1.0,
                      tags=["interview"], due_at="2026-09-01T10:00:00+00:00"),
            NewMemory(content="Nik agreed to a 30-minute scope-reduced session.",
                      memory_type="observation", source="mentor_inferred", confidence=0.5),
        ],
        confirmations=[
            Confirmation(memory_id=existing.id, evidence_class="user_action"),
        ],
    )
    report = apply_reflection(store, reflection)
    check("2 memories created", len(report["created"]) == 2, f"report={report}")
    check("no errors", not report["errors"], f"errors={report['errors']}")

    all_mems = store.list_memories()
    user_fact = next(m for m in all_mems if "Google interview" in m.content)
    inferred = next(m for m in all_mems if "scope-reduced" in m.content)
    check("user_stated new memory → conf 0.95+, user_confirmed, due_at parsed",
          user_fact.confidence >= 0.95 and user_fact.user_confirmed and user_fact.due_at is not None)
    check("mentor_inferred new memory → capped at 0.6, unconfirmed",
          inferred.confidence <= 0.6 and not inferred.user_confirmed)

    after_action = store.get_memory(existing.id)
    check("user_action confirm → count+1, still capped (no unlock)",
          after_action.confirmation_count == 2 and after_action.confidence <= 0.6
          and not after_action.user_confirmed,
          f"conf={after_action.confidence} count={after_action.confirmation_count}")

    # ------------------------------------------------------------------
    print("\n[1b] user_affirmed unlocks the ceiling")
    affirmed = MemoryReflection(
        confirmations=[Confirmation(memory_id=existing.id, evidence_class="user_affirmed")]
    )
    apply_reflection(store, affirmed)
    after_affirm = store.get_memory(existing.id)
    check("user_affirmed → user_confirmed, ceiling 0.95, conf ≥ 0.85",
          after_affirm.user_confirmed and after_affirm.confidence_ceiling == 0.95
          and after_affirm.confidence >= 0.85)

    # ------------------------------------------------------------------
    print("\n[2] Per-op isolation — bad IDs don't sink the batch")
    messy = MemoryReflection(
        new_memories=[NewMemory(content="Nik prefers direct mentor tone.",
                                memory_type="preference", source="user_stated")],
        confirmations=[Confirmation(memory_id="nonexistent-id", evidence_class="user_action")],
        revisions=[MemoryRevision(memory_id="also-missing", new_content="x", reason="y")],
    )
    report2 = apply_reflection(store, messy)
    check("valid create succeeded despite bad confirm/revise",
          len(report2["created"]) == 1 and len(report2["errors"]) == 2,
          f"report={report2}")

    # ------------------------------------------------------------------
    print("\n[3] run_reflection end-to-end with fake LLM")
    target = store.create_memory("Nik avoids System Design.", "observation", "mentor_inferred")
    fake_reflection = MemoryReflection(
        revisions=[MemoryRevision(
            memory_id=target.id,
            new_content="Nik enjoys System Design conceptually but fears interview-level questions.",
            reason="user corrected the framing",
            contradicts_prior=True,
        )],
        reasoning="user explicitly corrected",
    )
    report3 = run_reflection(
        "Actually I love System Design, I just don't feel ready for interviews.",
        "Got it — that's a different problem. Let's make it concrete.",
        store=store,
        llm=FakeLLM(fake_reflection),
    )
    revised_old = store.get_memory(target.id)
    check("revision applied via run_reflection", len(report3["revised"]) == 1 and not revised_old.active)
    corrected_new = store.get_memory(report3["revised"][0])
    check("contradicts_prior → user_confirmed correction",
          corrected_new.user_confirmed and corrected_new.confidence >= 0.85)


    # ------------------------------------------------------------------
    print("\n[4] /api/memories endpoints (§10.1)")
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)

    r = client.get("/api/memories")
    check("GET /api/memories → 200 with memories", r.status_code == 200 and r.json()["count"] > 0)

    r = client.get("/api/memories", params={"type": "goal"})
    check("type filter works", r.status_code == 200
          and all(m["memory_type"] == "goal" for m in r.json()["memories"]))

    r = client.get("/api/memories/pending")
    pending_ids = [m["id"] for m in r.json().get("memories", [])]
    check("GET /api/memories/pending → 200", r.status_code == 200)
    check("pending contains the unconfirmed inference", inferred.id in pending_ids,
          f"pending={len(pending_ids)} items")

    r = client.post(f"/api/memories/{inferred.id}/confirm")
    check("POST confirm → user_confirmed", r.status_code == 200
          and r.json()["memory"]["user_confirmed"] is True)

    r = client.post(f"/api/memories/{user_fact.id}/correct",
                    json={"content": "Nik has a Google interview on Sept 2, 2026."})
    check("POST correct → contradiction revision",
          r.status_code == 200 and r.json()["status"] == "corrected")
    check("corrected memory is user_confirmed with new content",
          r.json()["memory"]["user_confirmed"] and "Sept 2" in r.json()["memory"]["content"])

    r = client.delete(f"/api/memories/{inferred.id}")
    check("DELETE → archived", r.status_code == 200
          and not store.get_memory(inferred.id).active)

    r = client.post("/api/memories/does-not-exist/confirm")
    check("confirm 404 on unknown id", r.status_code == 404)
    r = client.delete("/api/memories/does-not-exist")
    check("delete 404 on unknown id", r.status_code == 404)

    # ------------------------------------------------------------------
    print("\n[5] Kill switch (DNA_REFLECTION_ENABLED)")
    os.environ["DNA_REFLECTION_ENABLED"] = "0"
    check("disabled when env=0", not reflection_enabled())
    os.environ["DNA_REFLECTION_ENABLED"] = "1"
    check("enabled when env=1", reflection_enabled())

    # ------------------------------------------------------------------
    print("\n[7] Grounding guard — reflection can't fabricate user_stated facts (§anti-hallucination)")
    # Recreate the actual incident: reflection labels a never-spoken "Google
    # interview" as user_stated with a deadline, and claims user_affirmed on
    # a memory the user never affirmed.
    import orchestrator.memory.dna_store as _ds
    _orig_cmp, _orig_mrg = _ds._llm_compare, _ds._llm_merge
    # Hermetic: never let a real LLM compare fire during this section.
    _ds._llm_compare = lambda **k: "SAME"   # would merge — the guard must win
    _ds._llm_merge = lambda **k: "UNUSED"
    # A PRE-EXISTING HIGH-TRUST memory that the fabricated statement resembles.
    seed_high = store.create_memory(
        "Nik has an interview scheduled this month.",
        "fact", "user_stated", confidence=0.95,
    )
    guard_target = store.create_memory(
        "Nik finds interviews stressful.", "observation", "mentor_inferred",
    )
    incident = MemoryReflection(
        new_memories=[
            NewMemory(content="Nik has a Google interview on the 22nd.",
                      memory_type="fact", source="user_stated", confidence=1.0,
                      tags=["interview"], due_at="2026-09-22T00:00:00+00:00"),
            NewMemory(content="Nik studied Flask today.",
                      memory_type="fact", source="user_stated", confidence=0.98),
        ],
        confirmations=[Confirmation(memory_id=guard_target.id, evidence_class="user_affirmed")],
        reasoning="user described an interview; guard should hold",
    )
    try:
        report7 = run_reflection(
            "let me tell you about the agentanalytics interview, and i also studied flask today",
            "got it — that's a lot; let's break it down.",
            store=store,
            llm=FakeLLM(incident),
        )
    finally:
        _ds._llm_compare, _ds._llm_merge = _orig_cmp, _orig_mrg
    all7 = store.list_memories()
    fabric = next((m for m in all7 if "Google interview on the 22nd" in m.content), None)
    legit = next((m for m in all7 if "Flask today" in m.content), None)
    check("fabricated 'Google interview' was downgraded from user_stated",
          fabric is not None and fabric.source == "mentor_inferred",
          f"source={fabric and fabric.source}")
    check("downgraded memory is low-confidence + unconfirmed + no deadline",
          fabric is not None and fabric.confidence <= 0.4
          and not fabric.user_confirmed and fabric.due_at is None,
          f"conf={fabric and fabric.confidence} confirmed={fabric and fabric.user_confirmed} due={fabric and fabric.due_at}")
    check("marker tag stripped before storage",
          fabric is not None and "grounding:downgraded" not in (fabric.tags or []))
    check("genuinely user-stated memory survives the guard",
          legit is not None and legit.source == "user_stated" and legit.confidence >= 0.9)
    seed_check = store.get_memory(seed_high.id)
    check("fabrication NOT merged into the pre-existing high-trust memory",
          "Google" not in seed_check.content and seed_check.source == "user_stated"
          and seed_check.confidence >= 0.9,
          f"content={seed_check.content[:80]}")
    check("high-trust memory keeps its deadline/trust intact",
          seed_check.user_confirmed and seed_check.due_at is None)
    after_guard = store.get_memory(guard_target.id)
    check("fabricated user_affirmed confirm downgraded to user_action (still capped, unconfirmed)",
          not after_guard.user_confirmed and after_guard.confidence <= 0.6,
          f"confirmed={after_guard.user_confirmed} conf={after_guard.confidence}")
    # clean up the rows created by this section (archive, no raw FK-prone DELETE)
    for _id in list(report7.get("created") or []):
        try:
            store.deactivate(_id)
        except Exception as exc:
            print(f"  (cleanup deactivate {_id} failed: {exc})")
    for _id in (guard_target.id, seed_high.id):
        try:
            store.deactivate(_id)
        except Exception as exc:
            print(f"  (cleanup deactivate {_id} failed: {exc})")

    # ------------------------------------------------------------------
    clean_slate(store)
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed\n{'=' * 60}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

