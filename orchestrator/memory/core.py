"""
orchestrator/memory/core.py

The single infrastructure owner for every memory store: engine, session,
schema, and embeddings.

Why this module exists
----------------------
Three concerns were each owned more than once, over one database:

    store.py:60      make_sessionmaker(database_url)   -> its own engine
    dna_store.py:214 make_sessionmaker(database_url)   -> a second engine
    store.py:71      Base.metadata.create_all(...)     -> schema owner #1
    dna_store.py:225 Base.metadata.create_all(...)     -> schema owner #2
    store.py:42      _get_embed_model()                -> embedding, owned by a
                                                          *connection* module
                                                          that dna_store.py and
                                                          retriever.py import
                                                          from

`models.make_sessionmaker()` builds a **fresh engine on every call**, so
constructing both stores opened two connection pools against one DSN while each
ran its own schema setup — and the two setups were not equivalent. Only
`store.py` applied the `schedule_events.block_kind` backfill; only
`dna_store.py` created the HNSW vector index. Which store you happened to
construct first decided which half of the schema got initialised.

One database, one engine, one schema owner, one embedding entry point.

Where this sits in the redesign
-------------------------------
Slice 1 of the memory redesign: the shared floor the purpose modules (identity,
state, history, patterns, provenance) get built on. It is additive and
behaviour-preserving — the existing stores delegate here, so their callers do
not change and the regression net stays green while each purpose is migrated
behind the facade.

Fail-open, like the rest of this package: `embed()` returns None instead of
raising, because an embedding failure must never crash a memory write. Schema
setup is the deliberate exception — `ensure_schema()` raises to the caller that
asked for it, because a silently-uninitialised schema is worse than a loud error.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from orchestrator.config import DB_URL
from orchestrator.memory.models import Base, make_sessionmaker

# The one embedding model every store and retriever shares. Two implementations
# would degrade recall *silently* (docs/PI-Mentor Boundary.md, boundary rule 1).
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Engine + session — one pool per database, process-wide
# ---------------------------------------------------------------------------

_ENGINES: dict[str, tuple[Any, Engine]] = {}
_ENGINE_LOCK = threading.Lock()


def get_session_factory(database_url: str = DB_URL) -> tuple[Any, Engine]:
    """(sessionmaker, engine) for this DSN — built once, then reused.

    Keyed by URL, so a suite pointing at `ai_companion_test` gets its own pool
    instead of borrowing the live one.
    """
    key = database_url or DB_URL
    with _ENGINE_LOCK:
        cached = _ENGINES.get(key)
        if cached is None:
            cached = make_sessionmaker(key)
            _ENGINES[key] = cached
        return cached


def get_engine(database_url: str = DB_URL) -> Engine:
    """The shared engine for this DSN."""
    return get_session_factory(database_url)[1]


@contextmanager
def session_scope(database_url: str = DB_URL) -> Generator[Session, None, None]:
    """Commit on success, roll back on error, always close.

    The single copy of the transaction policy both stores used to duplicate.
    """
    factory, _engine = get_session_factory(database_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Schema — the union of what two owners used to apply separately
# ---------------------------------------------------------------------------


def ensure_schema(database_url: str = DB_URL) -> None:
    """Idempotent schema setup: extension, tables, backfills, vector index.

    Applies the UNION of both previous paths, so whichever store is constructed
    first now initialises the whole schema instead of half of it.
    """
    _factory, engine = get_session_factory(database_url)
    with session_scope(database_url) as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(engine)
    _backfill_columns(database_url)
    _ensure_vector_index(database_url)


def _backfill_columns(database_url: str) -> None:
    """Column backfills for pre-existing installs.

    `create_all` creates missing tables but never ALTERs existing ones, so a
    column added after the first deploy needs an explicit pass.
    """
    with session_scope(database_url) as session:
        session.execute(text(
            "ALTER TABLE schedule_events "
            "ADD COLUMN IF NOT EXISTS block_kind VARCHAR NOT NULL DEFAULT 'task';"
        ))
        session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_schedule_events_block_kind "
            "ON schedule_events (block_kind);"
        ))


def _ensure_vector_index(database_url: str) -> None:
    """HNSW index on `dna_memory.embedding`.

    Strictly a performance nicety at this scale: if the pgvector extension is
    too old for hnsw, skip quietly (a sequential scan is fine) rather than
    break schema setup.
    """
    try:
        with session_scope(database_url) as session:
            session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_dna_memory_embedding "
                "ON dna_memory USING hnsw (embedding vector_cosine_ops);"
            ))
    except Exception as exc:
        print(f"[memory.core] vector index skipped ({exc}) — seq scan is fine at this scale.")


# ---------------------------------------------------------------------------
# Embedding — the one entry point
# ---------------------------------------------------------------------------

_embed_model: Any | None = None
_EMBED_LOCK = threading.Lock()


def get_embed_model() -> Any:
    """The shared sentence-transformer. Loaded once, then resident.

    Double-checked locking, so two threads on a cold cache cannot both load a
    ~90MB model.
    """
    global _embed_model
    if _embed_model is None:
        with _EMBED_LOCK:
            if _embed_model is None:
                from sentence_transformers import SentenceTransformer

                _embed_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embed_model


def embed(value: str) -> list[float] | None:
    """Embed one string, L2-normalised. None on failure — never raises.

    A memory written without an embedding is still a valid memory (it is merely
    unreachable by semantic search); a crashed write is a lost memory.
    """
    try:
        return get_embed_model().encode(value, normalize_embeddings=True).tolist()
    except Exception as exc:
        print(f"[memory.core] embedding failed: {exc}")
        return None


def reset_caches() -> None:
    """Dispose cached engines and drop the embedding model. Tests only."""
    global _embed_model
    with _ENGINE_LOCK:
        for _factory, engine in _ENGINES.values():
            try:
                engine.dispose()
            except Exception:
                pass
        _ENGINES.clear()
    with _EMBED_LOCK:
        _embed_model = None
