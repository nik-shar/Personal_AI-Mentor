"""
scripts/tests/test_memory_core.py

Verification suite for the shared memory infrastructure (`memory/core.py`) —
slice 1 of the memory redesign.

Covers:
 1. One engine per DSN — repeated construction reuses the pool
 2. Both stores share that pool (they used to open one engine each)
 3. session_scope commits on success, rolls back on error, propagates
 4. ensure_schema is idempotent AND complete — the union of what the two
    previous schema owners applied separately, either half of which could be
    missing depending on which store you constructed first
 5. The embedding model is a singleton, 384-dim, L2-normalised
 6. Fail-open: a broken embedder returns None instead of raising
 7. Both stores still work through the delegated transaction scope

Read-only with respect to live data: it creates no lasting rows and deletes
none. The one INSERT is deliberately rolled back and then swept, so the suite
is safe to point at real memory.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from orchestrator.config import DB_URL, EMBEDDING_DIMENSION  # noqa: E402
from orchestrator.memory import core  # noqa: E402
from orchestrator.memory.dna_store import DNAMemoryStore  # noqa: E402
from orchestrator.memory.store import MemoryManager  # noqa: E402

_passed = 0
_failed = 0
_failed_names: list[str] = []

# A profile-fact key that exists only for the duration of one rolled-back
# transaction. Swept unconditionally at the end, so a failed rollback cannot
# leave anything behind in real memory.
_PROBE_KEY = "_memory_core_rollback_probe"

_EXPECTED_TABLES = {
    "profile_facts",
    "episodic_events",
    "agent_private_memory",
    "schedule_events",
    "day_slots",
    "dna_memory",
    "conversation_sessions",
}


def check(ok: bool, name: str, detail: str = "") -> None:
    """`check(condition, label)` — the shape the other memory suites use."""
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1
        _failed_names.append(name)


def test_one_engine_per_dsn() -> None:
    print("\n=== 1. One engine per DSN ===")
    a = core.get_engine(DB_URL)
    b = core.get_engine(DB_URL)
    check(a is b, "repeated get_engine() returns the same engine (no second pool)")
    other = core.get_engine(DB_URL + "_other")
    check(other is not a, "a different DSN gets its own engine (test DBs stay isolated)")
    check(core.get_session_factory(DB_URL)[1] is a, "get_session_factory agrees with get_engine")


def test_stores_share_the_pool() -> None:
    print("\n=== 2. Both stores share one pool ===")
    mm = MemoryManager(DB_URL)
    dna = DNAMemoryStore(DB_URL, audit=False)
    check(mm.engine is dna.engine, "MemoryManager.engine is DNAMemoryStore.engine")
    check(mm.engine is core.get_engine(DB_URL), "and it is the shared core engine")


def test_session_scope() -> None:
    print("\n=== 3. session_scope transaction policy ===")
    with core.session_scope(DB_URL) as session:
        got = session.execute(text("SELECT 1")).scalar()
    check(got == 1, "yields a live session and commits on success")

    raised = False
    try:
        with core.session_scope(DB_URL) as session:
            session.execute(
                text(
                    "INSERT INTO profile_facts (category, key, value, source, updated_at) "
                    "VALUES ('system', :k, 'null'::jsonb, 'test', NOW())"
                ),
                {"k": _PROBE_KEY},
            )
            raise RuntimeError("intentional failure inside the scope")
    except RuntimeError:
        raised = True
    check(raised, "propagates the exception to the caller")

    with core.session_scope(DB_URL) as session:
        left = session.execute(
            text("SELECT COUNT(*) FROM profile_facts WHERE key = :k"), {"k": _PROBE_KEY}
        ).scalar()
    check(left == 0, "the failed write was rolled back (no row survives)", f"rows={left}")


def test_ensure_schema_is_complete_and_idempotent() -> None:
    print("\n=== 4. ensure_schema is idempotent and complete ===")
    core.ensure_schema(DB_URL)
    core.ensure_schema(DB_URL)  # a second call must be a no-op, not an error
    check(True, "called twice in a row without error")

    with core.session_scope(DB_URL) as session:
        rows = session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).fetchall()
    names = {r[0] for r in rows}
    missing = _EXPECTED_TABLES - names
    check(not missing, f"all {len(_EXPECTED_TABLES)} tables present", f"missing={sorted(missing)}")

    # The union property: store.py used to own the backfill and dna_store.py
    # used to own the vector index, so either was missing depending on which
    # store was constructed first. One core call must now deliver both.
    with core.session_scope(DB_URL) as session:
        col = session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'schedule_events' AND column_name = 'block_kind'"
            )
        ).fetchone()
    check(col is not None, "the schedule_events.block_kind backfill ran from core")

    with core.session_scope(DB_URL) as session:
        idx = session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'dna_memory' AND indexname = 'ix_dna_memory_embedding'"
            )
        ).fetchone()
    check(idx is not None, "the HNSW vector index exists too (the union, not half)")


def test_embedding_singleton_and_shape() -> None:
    print("\n=== 5. Embedding is one model, 384-dim, normalised ===")
    check(core.get_embed_model() is core.get_embed_model(), "get_embed_model() is a singleton")
    vec = core.embed("the single-writer boundary")
    check(
        vec is not None and len(vec) == EMBEDDING_DIMENSION,
        f"embed() returns a {EMBEDDING_DIMENSION}-dim vector",
        f"len={len(vec) if vec else None}",
    )
    if vec:
        norm = sum(v * v for v in vec) ** 0.5
        check(abs(norm - 1.0) < 1e-3, "the vector is L2-normalised", f"norm={norm:.4f}")
    check(
        core.EMBEDDING_MODEL_NAME == "all-MiniLM-L6-v2",
        "the model name is the shared one",
        core.EMBEDDING_MODEL_NAME,
    )


def test_embed_fails_open() -> None:
    print("\n=== 6. Fail-open: a broken embedder returns None ===")
    original = core._embed_model
    try:
        core._embed_model = object()  # no .encode -> AttributeError inside embed()
        result = core.embed("anything at all")
        check(result is None, "embed() returned None instead of raising")
    finally:
        core._embed_model = original
    check(core.embed("restored") is not None, "the real model works again afterwards")


def test_stores_use_the_delegated_scope() -> None:
    print("\n=== 7. Both stores work through the delegated scope ===")
    mm = MemoryManager(DB_URL)
    dna = DNAMemoryStore(DB_URL, audit=False)

    facts = mm.load_profile_facts()
    check(
        isinstance(facts, dict),
        "MemoryManager reads through core.session_scope",
        f"{len(facts)} profile facts",
    )

    det = dna.get_deterministic()
    check(
        isinstance(det, dict) and {"due", "core", "pending_validation"} <= set(det),
        "DNAMemoryStore reads through core.session_scope",
        f"due={len(det.get('due', []))} core={len(det.get('core', []))}",
    )

    mems = dna.list_memories(limit=5)
    check(isinstance(mems, list), "DNA listing works through the delegated scope", f"{len(mems)} rows")

    sessions = mm.get_recent_conversation_sessions(limit=1)
    check(isinstance(sessions, list), "conversation sessions read through it too",
          f"{len(sessions)} session(s)")


def _sweep_probe() -> None:
    """Unconditional cleanup — a failed rollback must not leave a probe row."""
    try:
        with core.session_scope(DB_URL) as session:
            session.execute(text("DELETE FROM profile_facts WHERE key = :k"), {"k": _PROBE_KEY})
    except Exception as exc:
        print(f"  (probe sweep skipped: {exc})")


def main() -> int:
    print("=" * 78)
    print("MEMORY CORE — shared engine / session / schema / embedding")
    print("=" * 78)
    print(f"  DSN: {DB_URL}")
    try:
        test_one_engine_per_dsn()
        test_stores_share_the_pool()
        test_session_scope()
        test_ensure_schema_is_complete_and_idempotent()
        test_embedding_singleton_and_shape()
        test_embed_fails_open()
        test_stores_use_the_delegated_scope()
    finally:
        _sweep_probe()

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
