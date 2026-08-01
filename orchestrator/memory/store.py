"""
orchestrator/memory/store.py

The unified interface to the three memory tiers.

- Profile store  → `profile_facts` table (slow-changing facts)
- Episodic store → `episodic_events` table (append-only log)
- Working memory → in-process only; not persisted here
"""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Generator, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.config import DB_URL, EMBEDDING_DIMENSION
from orchestrator.memory.models import (
    AgentPrivateMemory,
    Base,
    EpisodicEvent,
    ProfileFact,
    make_sessionmaker,
)

# ---------------------------------------------------------------------------
# Lazy embedding model — loaded once on first use, stays in memory after.
# Using all-MiniLM-L6-v2: 384-dim, fast, good for semantic similarity.
# ---------------------------------------------------------------------------

_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embed_model


class MemoryManager:
    """
    Single access point for all persisted memory.

    Expects a Postgres database with the pgvector extension. Call
    `ensure_schema()` once before first use.
    """

    def __init__(self, database_url: str = DB_URL) -> None:
        self.database_url = database_url
        self.SessionLocal, self.engine = make_sessionmaker(database_url)

    # -----------------------------------------------------------------------
    # Schema management
    # -----------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create pgvector extension and tables if they do not exist."""
        with self._session() as session:
            session.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            session.commit()
        Base.metadata.create_all(self.engine)

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # -----------------------------------------------------------------------
    # Profile facts
    # -----------------------------------------------------------------------

    def get_profile_fact(self, category: str, key: str) -> Optional[Any]:
        """Return the JSONB value for one profile fact, or None."""
        with self._session() as session:
            row = (
                session.query(ProfileFact.value)
                .filter_by(category=category, key=key)
                .first()
            )
            return row[0] if row else None

    def set_profile_fact(
        self,
        category: str,
        key: str,
        value: Any,
        source: Optional[str] = None,
    ) -> None:
        """Upsert a single profile fact."""
        with self._session() as session:
            row = session.query(ProfileFact).filter_by(category=category, key=key).first()
            if row is None:
                row = ProfileFact(category=category, key=key, value=value, source=source)
                session.add(row)
            else:
                row.value = value
                row.source = source or row.source

    def load_profile_facts(self, keys: Optional[Iterable[str]] = None) -> dict[str, Any]:
        """
        Return a flat dict of profile facts keyed by their `key`.

        If `keys` is given, only facts whose key is in the set are returned.
        """
        with self._session() as session:
            query = session.query(ProfileFact)
            if keys is not None:
                key_set = set(keys)
                query = query.filter(ProfileFact.key.in_(key_set))
            rows = query.all()
            return {row.key: row.value for row in rows}

    def merge_profile_dict(self, data: dict[str, Any], source: Optional[str] = None) -> None:
        """
        Convenience merge from a flat dict keyed by profile_keys.
        Values are routed to their configured (category, key) in profile_facts.
        """
        from orchestrator.config import PROFILE_KEY_MAP

        with self._session() as session:
            for flat_key, value in data.items():
                mapping = PROFILE_KEY_MAP.get(flat_key)
                if not mapping:
                    continue
                category, db_key = mapping
                row = (
                    session.query(ProfileFact)
                    .filter_by(category=category, key=db_key)
                    .first()
                )
                if row is None:
                    session.add(
                        ProfileFact(
                            category=category,
                            key=db_key,
                            value=value,
                            source=source,
                        )
                    )
                else:
                    row.value = value
                    row.source = source or row.source

    # -----------------------------------------------------------------------
    # Procedural memory & calibration (§3.4, §5.4.3)
    # -----------------------------------------------------------------------

    def get_procedural_rules(self, domain: Optional[str] = None, status: str = "active") -> list[dict[str, Any]]:
        """Return list of procedural rules matching domain and status."""
        rules = self.get_profile_fact("preferences", "procedural_rules") or []
        if not isinstance(rules, list):
            return []
        res = []
        for r in rules:
            if isinstance(r, dict):
                if status and r.get("status") != status:
                    continue
                if domain and r.get("domain") not in (domain, "general"):
                    continue
                res.append(r)
        return res

    def upsert_procedural_rule(self, rule_data: dict[str, Any], source: str = "feedback_loop") -> None:
        """Add or update a procedural rule by rule_id or matching text."""
        existing = self.get_profile_fact("preferences", "procedural_rules") or []
        if not isinstance(existing, list):
            existing = []
        
        rule_id = rule_data.get("rule_id")
        rule_text = (rule_data.get("rule") or "").strip().lower()

        found = False
        updated = []
        for r in existing:
            if not isinstance(r, dict):
                continue
            r_id = r.get("rule_id")
            r_text = (r.get("rule") or "").strip().lower()
            if (rule_id and r_id == rule_id) or (rule_text and r_text == rule_text):
                found = True
                merged = {**r, **rule_data}
                merged["observed_count"] = r.get("observed_count", 1) + 1
                updated.append(merged)
            else:
                updated.append(r)
        if not found:
            updated.append(rule_data)
        
        self.set_profile_fact("preferences", "procedural_rules", updated, source=source)

    def get_mindset_calibration(self) -> dict[str, Any]:
        """Return mindset calibration dict."""
        cal = self.get_profile_fact("preferences", "mindset_calibration")
        if isinstance(cal, dict):
            return cal
        return {"directness": "moderate", "nudge_frequency_cap": "max 1x/day", "framing_that_lands": [], "framing_that_bounces": []}

    def set_mindset_calibration(self, cal_data: dict[str, Any], source: str = "feedback_loop") -> None:
        """Save mindset calibration dict."""
        current = self.get_mindset_calibration()
        merged = {**current, **cal_data}
        self.set_profile_fact("preferences", "mindset_calibration", merged, source=source)


    # -----------------------------------------------------------------------
    # Episodic events
    # -----------------------------------------------------------------------

    def add_episodic_event(
        self,
        source_agent: str,
        event_type: str,
        content: str,
        payload: Optional[dict[str, Any]] = None,
        tags: Optional[list[str]] = None,
        occurred_at: Optional[datetime] = None,
        importance: int = 3,
        embedding: Optional[list[float]] = None,
    ) -> str:
        """Append one event to the episodic log. Returns the new event id."""
        if embedding is not None and len(embedding) != EMBEDDING_DIMENSION:
            raise ValueError(
                f"Embedding dimension must be {EMBEDDING_DIMENSION}, got {len(embedding)}"
            )

        event = EpisodicEvent(
            source_agent=source_agent,
            event_type=event_type,
            content=content,
            payload=payload or {},
            tags=tags or [],
            occurred_at=occurred_at or datetime.now(timezone.utc),
            importance=importance,
            embedding=embedding,
        )
        with self._session() as session:
            session.add(event)
            session.flush()
            return str(event.id)

    def query_episodic(
        self,
        event_type: Optional[str] = None,
        source_agent: Optional[str] = None,
        tags: Optional[list[str]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        importance__gte: Optional[int] = None,
        last_n: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Query episodic events with optional filters, ordered newest first.
        """
        with self._session() as session:
            query = session.query(EpisodicEvent)

            if event_type is not None:
                query = query.filter(EpisodicEvent.event_type == event_type)
            if source_agent is not None:
                query = query.filter(EpisodicEvent.source_agent == source_agent)
            if since is not None:
                query = query.filter(EpisodicEvent.occurred_at >= since)
            if until is not None:
                query = query.filter(EpisodicEvent.occurred_at <= until)
            if importance__gte is not None:
                query = query.filter(EpisodicEvent.importance >= importance__gte)
            if tags:
                query = query.filter(EpisodicEvent.tags.overlap(tags))

            query = query.order_by(EpisodicEvent.occurred_at.desc())

            if last_n is not None:
                query = query.limit(last_n)

            rows = query.all()
            return [self._event_to_dict(row) for row in rows]

    def query_episodic_recent_days(
        self,
        days: int = 7,
        **filters,
    ) -> list[dict[str, Any]]:
        """Convenience wrapper for events in the last N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return self.query_episodic(since=since, **filters)

    @staticmethod
    def _event_to_dict(event: EpisodicEvent) -> dict[str, Any]:
        if event.embedding is not None:
            embedding_val = event.embedding.tolist() if hasattr(event.embedding, "tolist") else list(event.embedding)
        else:
            embedding_val = None

        return {
            "id": str(event.id),
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "source_agent": event.source_agent,
            "event_type": event.event_type,
            "tags": event.tags,
            "content": event.content,
            "payload": event.payload,
            "importance": event.importance,
            "embedding": embedding_val,
        }

    # -----------------------------------------------------------------------
    # Agent private memory
    # -----------------------------------------------------------------------

    def get_private_memory(self, agent_name: str) -> dict[str, Any]:
        """Return the private scratch state for an agent, or {} if missing."""
        with self._session() as session:
            row = (
                session.query(AgentPrivateMemory)
                .filter_by(agent_name=agent_name)
                .first()
            )
            return row.data if row else {}

    def set_private_memory(self, agent_name: str, data: dict[str, Any]) -> None:
        """Upsert private scratch state for an agent."""
        with self._session() as session:
            row = (
                session.query(AgentPrivateMemory)
                .filter_by(agent_name=agent_name)
                .first()
            )
            if row is None:
                session.add(
                    AgentPrivateMemory(agent_name=agent_name, data=data)
                )
            else:
                row.data = data

    # -----------------------------------------------------------------------
    # Conversation turn logging
    # -----------------------------------------------------------------------

    def log_conversation_turn(
        self,
        user_input: str,
        response_text: str,
        agent_invoked: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Persist one user ↔ assistant exchange as an episodic event.

        Stored under event_type='conversation_turn' so the consolidation job
        can include real message content in weekly summaries, not just
        'agent_run' meta-events. Returns the new event id.
        """
        content = (
            f"User: {user_input[:300]}\n"
            f"Assistant: {response_text[:300]}"
        )
        payload: dict[str, Any] = {
            "user_input":    user_input[:1000],
            "response_text": response_text[:1000],
        }
        if agent_invoked:
            payload["agent_invoked"] = agent_invoked
        if session_id:
            payload["session_id"] = session_id

        event_id = self.add_episodic_event(
            source_agent="orchestrator",
            event_type="conversation_turn",
            content=content,
            payload=payload,
            tags=["conversation", agent_invoked or "direct"],
            importance=2,
        )
        # Store the embedding inline — small latency hit acceptable here
        # because conversation turns are low-frequency (one per user message).
        self.embed_and_store(event_id, content)
        return event_id

    # -----------------------------------------------------------------------
    # Embedding
    # -----------------------------------------------------------------------

    def embed_and_store(self, event_id: str, content: str) -> None:
        """
        Compute a sentence-transformer embedding for `content` and persist it
        on the episodic_events row identified by `event_id`.

        The model is loaded lazily once at module level (see _get_embed_model)
        so subsequent calls within a process are fast.
        """
        try:
            model = _get_embed_model()
            vector = model.encode(content, normalize_embeddings=True).tolist()
            with self._session() as session:
                session.execute(
                    text(
                        "UPDATE episodic_events SET embedding = :vec "
                        "WHERE id = :eid"
                    ),
                    {"vec": str(vector), "eid": event_id},
                )
        except Exception as exc:
            # Embedding failure must never crash the main flow.
            print(f"[embed_and_store] failed for event {event_id}: {exc}")

    # -----------------------------------------------------------------------
    # Semantic search (Tier 2 / 3 recall)
    # -----------------------------------------------------------------------

    def semantic_search(
        self,
        query_embedding: list[float],
        hot_threshold_days: int = 14,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Find the most semantically similar episodic events to `query_embedding`,
        restricted to the warm / cold tiers (older than hot_threshold_days).

        Only searches rows that HAVE an embedding and are NOT archived
        (archived rows are monthly summaries — retriever handles those separately).

        Returns a list of plain dicts with keys:
          id, occurred_at, event_type, content, importance, distance
        ordered by ascending cosine distance (closest first).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=hot_threshold_days)
        query_vec_str = str(query_embedding)

        sql = text("""
            SELECT id, occurred_at, event_type, content, importance,
                   embedding <=> CAST(:vec AS vector) AS distance
            FROM episodic_events
            WHERE embedding IS NOT NULL
              AND archived = false
              AND occurred_at < :cutoff
            ORDER BY distance ASC
            LIMIT :lim
        """)
        with self._session() as session:
            rows = session.execute(
                sql,
                {"vec": query_vec_str, "cutoff": cutoff, "lim": limit},
            ).fetchall()

        return [
            {
                "id":          str(r.id),
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                "event_type":  r.event_type,
                "content":     r.content,
                "importance":  r.importance,
                "distance":    float(r.distance),
            }
            for r in rows
        ]


_memory_manager_instance: Optional[MemoryManager] = None

def get_memory_manager(database_url: str = DB_URL) -> MemoryManager:
    """Return a shared singleton instance of MemoryManager."""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        _memory_manager_instance = MemoryManager(database_url)
    return _memory_manager_instance

