"""
orchestrator/memory/dna_store.py

DNAMemoryStore — the organic memory layer (dna_memory_redesign_v2.md).

One Postgres table (`dna_memory`, pgvector-embedded) holding natural-language
memories about Nikhil with a full confidence lifecycle:

  create  → confirm (capped growth) → revise (supersede, audit trail)
          → decay (weekly job) → archive

Key v2 design rules implemented here:
  - §4.1  starting confidence + ceiling depend on source
  - §4.2  mentor_inferred memories cap at 0.6 until the USER confirms them
  - §4.3  confirmation discipline is enforced by callers (reflection node);
          this layer enforces the ceilings mechanically
  - §6    upsert_with_checks: embedding similarity selects candidates, an LLM
          compare verdict (SAME / REFINES / CONTRADICTS / DISTINCT) decides
  - §7.1  retrieval = deterministic layer (due dates, core identity, pending
          validations) + composite-scored semantic layer
  - §11.2 every mutation writes a `memory_op` row to episodic_events

Wiring: the reflection node writes after every turn (Phase 2), the DNA
context builder reads it as the reasoner's only context path (Phases 3-4),
and the weekly decay pass runs from scheduler/consolidation_job.py.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel
from sqlalchemy import text

from orchestrator.config import DB_URL
from orchestrator.memory import core as memory_core
from orchestrator.memory.models import (
    DNAMemoryRow,
    EpisodicEvent,
)
from orchestrator.tracing import component_span

# ---------------------------------------------------------------------------
# Constants (dna_memory_redesign_v2.md §4, §6, §7, §9)
# ---------------------------------------------------------------------------

MEMORY_TYPES = (
    "fact", "observation", "insight", "preference",
    "goal", "reflection", "context",
)
SOURCES = ("user_stated", "mentor_inferred", "data_derived", "seeded")

# Per-source lifecycle defaults (§4.1).
SOURCE_DEFAULTS: dict[str, dict[str, Any]] = {
    "user_stated":     {"confidence": 0.95, "ceiling": 1.0,  "user_confirmed": True},
    "seeded":          {"confidence": 0.95, "ceiling": 1.0,  "user_confirmed": True},
    "data_derived":    {"confidence": 0.70, "ceiling": 0.95, "user_confirmed": False},
    "mentor_inferred": {"confidence": 0.40, "ceiling": 0.60, "user_confirmed": False},
}

# Confirmation growth curve for mentor_inferred memories (§4.2).
GROWTH_BASE = 0.3
GROWTH_SPAN = 0.65
GROWTH_RATE = 0.3
USER_CONFIRM_CONFIDENCE = 0.85   # floor once the user affirms a memory
USER_CONFIRM_CEILING = 0.95      # unlocked ceiling after user confirmation

# Composite retrieval weights (§7.1) — decay job owns aging; recency is a tiebreaker.
W_SEMANTIC = 0.45
W_CONFIDENCE = 0.30
W_TYPE = 0.15
W_RECENCY = 0.10

TYPE_WEIGHTS = {
    "fact": 1.0, "goal": 0.9, "preference": 0.85, "insight": 0.75,
    "reflection": 0.65, "observation": 0.55, "context": 0.50,
}

RECENCY_HALF_LIFE_DAYS = 30
OVER_RETRIEVE_FACTOR = 3
SIMILARITY_FLOOR = 0.75          # §6: at/above → LLM compare before deciding

# Decay (§4.4) — weekly job.
DECAY_STALE_DAYS = 45
DECAY_FACTOR = 0.85
DECAY_ARCHIVE_THRESHOLD = 0.15
DECAY_MAX_CONFIDENCE = 0.8       # memories at/above this resist decay

# Deterministic layer (§7.1).
CORE_TOP_K = 5

# ---------------------------------------------------------------------------
# Public record type
# ---------------------------------------------------------------------------

class DNAMemoryRecord(BaseModel):
    """Plain read-model returned by every DNAMemoryStore method."""

    id: str
    content: str
    memory_type: str
    confidence: float
    confidence_ceiling: float
    source: str
    user_confirmed: bool
    tags: list[str]
    active: bool
    due_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    last_confirmed: Optional[datetime] = None
    superseded_by: Optional[str] = None
    confirmation_count: int = 1
    # Populated by retrieve()/similarity search for inspection and debugging.
    distance: Optional[float] = None
    composite_score: Optional[float] = None


# ---------------------------------------------------------------------------
# LLM compare / merge helpers (§6)
# ---------------------------------------------------------------------------

CompareVerdict = Literal["SAME", "REFINES", "CONTRADICTS", "DISTINCT"]


class _CompareOutput(BaseModel):
    verdict: CompareVerdict


_COMPARE_PROMPT = """Two statements about the same person. Classify their relationship:
- SAME: same claim, different words
- REFINES: B adds detail/scope to A without opposing it
- CONTRADICTS: B opposes or corrects A (watch for negation:
  "hates X" vs "loves X", "always" vs "never", "can't" vs "can")
- DISTINCT: different claims that happen to share topic words

A: {existing}
B: {new}

Answer with exactly one word: SAME, REFINES, CONTRADICTS, or DISTINCT."""


@component_span("dna_upsert_compare", tags=["component:dna_upsert_compare"])
def _llm_compare(new: str, existing: str) -> CompareVerdict:
    """
    Classify the relationship between two near-duplicate memories.

    Embedding similarity cannot detect negation, so this reasoning-tier call
    makes every confirm/refine/archive decision at the dedup boundary (§6).
    On any failure, returns DISTINCT — over-creating is recoverable, a wrong
    auto-confirm corrupts memory integrity.
    """
    from orchestrator.llm import get_reflection_llm

    try:
        llm = get_reflection_llm(temperature=0.0).with_structured_output(_CompareOutput)
        raw = llm.invoke([("human", _COMPARE_PROMPT.format(existing=existing, new=new))])
        verdict = raw.verdict if isinstance(raw, _CompareOutput) else _CompareOutput(**raw).verdict
        return verdict
    except Exception as exc:
        print(f"[DNAMemoryStore] compare failed ({exc}) — defaulting to DISTINCT.")
        return "DISTINCT"


@component_span("dna_upsert_merge", tags=["component:dna_upsert_merge"])
def _llm_merge(new: str, existing: str) -> str:
    """
    Merge a refinement into the existing memory's content (§6 REFINES path).
    Falls back to the new content on failure — the revision still captures
    the newer, more detailed statement.
    """
    from orchestrator.llm import get_reflection_llm

    prompt = (
        "Merge these two statements about the same person into one concise "
        "statement (1-3 sentences, third person) that preserves ALL detail "
        "from both. Do not invent anything.\n\n"
        f"A: {existing}\nB: {new}\n\nMerged statement:"
    )
    try:
        response = get_reflection_llm(temperature=0.0).invoke([("human", prompt)])
        merged = response.content.strip()
        return merged or new
    except Exception as exc:
        print(f"[DNAMemoryStore] merge failed ({exc}) — using new content.")
        return new

CORE_MIN_CONFIDENCE = 0.85
DUE_WINDOW_DAYS = 30
VALIDATION_MIN_CONFIDENCE = 0.5

AUDIT_SOURCE = "dna_memory"

# ---------------------------------------------------------------------------
# DNAMemoryStore
# ---------------------------------------------------------------------------

class DNAMemoryStore:
    """
    Single access point for organic DNA memory.

    Expects the same Postgres + pgvector database as MemoryManager.
    Call `ensure_schema()` once before first use.
    """

    def __init__(self, database_url: str = DB_URL, audit: bool = True) -> None:
        self.database_url = database_url
        # The same shared per-URL pool MemoryManager uses (memory/core.py).
        # This store used to open a second engine against the same database.
        self.SessionLocal, self.engine = memory_core.get_session_factory(database_url)
        self._audit_enabled = audit

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the extension, tables, column backfills and vector index.

        Delegates to `memory/core.py`. This store used to be the *only* place
        the HNSW index was created, and the *only* schema owner that skipped
        the `schedule_events` backfill — so which half of the schema existed
        depended on which store you happened to construct first.
        """
        memory_core.ensure_schema(self.database_url)

    def _session(self):
        """A commit-on-success transaction scope, owned by `memory/core.py`."""
        return memory_core.session_scope(self.database_url)

    # ------------------------------------------------------------------
    # Embedding + conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _embed(content: str) -> Optional[list[float]]:
        """Embed memory content; failure must never crash a memory write.

        Delegates to `memory/core.py` — one embedding implementation, one model
        instance and one failure policy for the whole system. Two would degrade
        recall silently (docs/PI-Mentor Boundary.md, boundary rule 1).
        """
        return memory_core.embed(content)

    @staticmethod
    def _to_record(row: DNAMemoryRow) -> DNAMemoryRecord:
        return DNAMemoryRecord(
            id=str(row.id),
            content=row.content,
            memory_type=row.memory_type,
            confidence=row.confidence,
            confidence_ceiling=row.confidence_ceiling,
            source=row.source,
            user_confirmed=row.user_confirmed,
            tags=list(row.tags or []),
            active=row.active,
            due_at=row.due_at,
            created_at=row.created_at,
            last_confirmed=row.last_confirmed,
            superseded_by=str(row.superseded_by) if row.superseded_by else None,
            confirmation_count=row.confirmation_count,
        )

    @staticmethod
    def _recency_score(last_confirmed: Optional[datetime], now: datetime) -> float:
        """Half-life of 30 days — tiebreaker only (§7.1)."""
        if not last_confirmed:
            return 0.5
        if last_confirmed.tzinfo is None:
            last_confirmed = last_confirmed.replace(tzinfo=timezone.utc)
        days_ago = max(0.0, (now - last_confirmed).total_seconds() / 86400.0)
        return math.exp(-0.693 * days_ago / RECENCY_HALF_LIFE_DAYS)


    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_memory(
        self,
        content: str,
        memory_type: str,
        source: str,
        tags: Optional[list[str]] = None,
        confidence: Optional[float] = None,
        due_at: Optional[datetime] = None,
    ) -> DNAMemoryRecord:
        """
        Insert one new memory. Starting confidence and ceiling come from the
        source (§4.1); an explicit `confidence` is capped by the ceiling.
        """
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Invalid memory_type '{memory_type}'. Must be one of {MEMORY_TYPES}.")
        if source not in SOURCES:
            raise ValueError(f"Invalid source '{source}'. Must be one of {SOURCES}.")
        if not content or not content.strip():
            raise ValueError("Memory content cannot be empty.")

        defaults = SOURCE_DEFAULTS[source]
        ceiling = defaults["ceiling"]
        conf = confidence if confidence is not None else defaults["confidence"]
        conf = max(0.0, min(float(conf), ceiling))

        row = DNAMemoryRow(
            content=content.strip(),
            memory_type=memory_type,
            source=source,
            confidence=conf,
            confidence_ceiling=ceiling,
            user_confirmed=defaults["user_confirmed"],
            tags=tags or [],
            active=True,
            due_at=due_at,
            embedding=self._embed(content),
        )
        with self._session() as session:
            session.add(row)
            session.flush()
            record = self._to_record(row)

        self._audit("create", record, summary=record.content[:160])
        return record

    def get_memory(self, memory_id: str) -> Optional[DNAMemoryRecord]:
        with self._session() as session:
            row = session.query(DNAMemoryRow).filter_by(id=memory_id).first()
            return self._to_record(row) if row else None

    # ------------------------------------------------------------------
    # Confirm (§4.2 — ceilings enforced mechanically)
    # ------------------------------------------------------------------

    def confirm_memory(self, memory_id: str, by_user: bool = False) -> DNAMemoryRecord:
        """
        Record confirming evidence for a memory.

        by_user=True  → the user explicitly affirmed it: unlock ceiling to
                        0.95, floor confidence at 0.85, mark user_confirmed.
        by_user=False → source-specific growth, hard-capped at the memory's
                        confidence_ceiling (mentor_inferred asymptotes at 0.6
                        until user-confirmed).
        """
        with self._session() as session:
            row = session.query(DNAMemoryRow).filter_by(id=memory_id).first()
            if row is None:
                raise KeyError(f"Memory '{memory_id}' not found.")

            row.confirmation_count += 1
            row.last_confirmed = datetime.now(timezone.utc)

            if by_user:
                row.user_confirmed = True
                row.confidence_ceiling = USER_CONFIRM_CEILING
                row.confidence = max(row.confidence, USER_CONFIRM_CONFIDENCE)
            elif row.source == "mentor_inferred":
                growth = GROWTH_BASE + (
                    GROWTH_SPAN * (1 - math.exp(-GROWTH_RATE * row.confirmation_count))
                )
                row.confidence = min(row.confidence_ceiling, growth)
            elif row.source == "data_derived":
                row.confidence = min(row.confidence_ceiling, row.confidence + 0.05)
            # user_stated / seeded: already at ceiling — only count/last_confirmed move.

            row.confidence = min(row.confidence, row.confidence_ceiling)
            session.flush()
            record = self._to_record(row)

        self._audit(
            "confirm_user" if by_user else "confirm",
            record,
            summary=f"conf={record.confidence:.2f} count={record.confirmation_count}",
        )
        return record


    # ------------------------------------------------------------------
    # Revise (§4.4 — supersede with audit trail)
    # ------------------------------------------------------------------

    def revise_memory(
        self,
        old_memory_id: str,
        new_content: str,
        reason: str,
        contradiction: bool = False,
        new_confidence: Optional[float] = None,
        due_at: Optional[datetime] = None,
    ) -> DNAMemoryRecord:
        """
        Replace a memory with a corrected/refined version. The old row is
        archived (active=False, superseded_by set) and kept for audit.

        contradiction=True marks a user correction: the new memory is
        user_confirmed with its ceiling unlocked (§4.4).
        """
        embedding = self._embed(new_content)

        with self._session() as session:
            old = session.query(DNAMemoryRow).filter_by(id=old_memory_id).first()
            if old is None:
                raise KeyError(f"Memory '{old_memory_id}' not found.")

            if contradiction:
                ceiling = USER_CONFIRM_CEILING
                conf = max(new_confidence or old.confidence, USER_CONFIRM_CONFIDENCE)
                user_confirmed = True
            else:
                ceiling = old.confidence_ceiling
                conf = min(new_confidence if new_confidence is not None else old.confidence, ceiling)
                user_confirmed = old.user_confirmed

            new_row = DNAMemoryRow(
                content=new_content.strip(),
                memory_type=old.memory_type,
                source=old.source,
                confidence=conf,
                confidence_ceiling=ceiling,
                user_confirmed=user_confirmed,
                tags=list(old.tags or []),
                active=True,
                due_at=due_at if due_at is not None else old.due_at,
                embedding=embedding,
                confirmation_count=old.confirmation_count,
            )
            session.add(new_row)
            session.flush()

            old.active = False
            old.superseded_by = new_row.id
            session.flush()
            record = self._to_record(new_row)

        self._audit(
            "revise",
            record,
            summary=f"supersedes={old_memory_id} reason={reason} contradiction={contradiction}",
        )
        return record

    def deactivate(self, memory_id: str) -> bool:
        """Archive a memory (user said 'forget this'). Kept for audit."""
        with self._session() as session:
            row = session.query(DNAMemoryRow).filter_by(id=memory_id).first()
            if row is None:
                return False
            row.active = False
            session.flush()
            record = self._to_record(row)
        self._audit("deactivate", record, summary=record.content[:160])
        return True

    # ------------------------------------------------------------------
    # Decay (§4.4 — weekly job)
    # ------------------------------------------------------------------

    def decay_stale(self, now: Optional[datetime] = None) -> dict[str, int]:
        """
        Gently decay stale, unvalidated memories; archive the hopeless.

        Exempt: user_stated memories and anything the user has confirmed.
        Memories at/above DECAY_MAX_CONFIDENCE resist decay entirely.
        Returns a small report for the scheduler job to log.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=DECAY_STALE_DAYS)
        report = {"decayed": 0, "archived": 0}

        with self._session() as session:
            rows = (
                session.query(DNAMemoryRow)
                .filter(
                    DNAMemoryRow.active.is_(True),
                    DNAMemoryRow.last_confirmed < cutoff,
                    DNAMemoryRow.source != "user_stated",
                    DNAMemoryRow.user_confirmed.is_(False),
                    DNAMemoryRow.confidence < DECAY_MAX_CONFIDENCE,
                )
                .all()
            )
            for row in rows:
                row.confidence *= DECAY_FACTOR
                report["decayed"] += 1
                if row.confidence < DECAY_ARCHIVE_THRESHOLD:
                    row.active = False
                    report["archived"] += 1
            session.flush()
            archived_summaries = [r.content[:120] for r in rows if not r.active]

        for summary in archived_summaries:
            self._audit("decay_archive", None, summary=summary)
        return report


    # ------------------------------------------------------------------
    # Similarity search (shared by upsert dedup and retrieve)
    # ------------------------------------------------------------------

    def _similarity_search(
        self,
        query_embedding: list[float],
        limit: int,
        memory_types: Optional[list[str]] = None,
    ) -> list[DNAMemoryRecord]:
        """Cosine-similarity search over active memories, closest first."""
        vec_str = str(query_embedding)
        type_clause = ""
        params: dict[str, Any] = {"vec": vec_str, "lim": limit}
        if memory_types:
            type_clause = "AND memory_type = ANY(:types)"
            params["types"] = memory_types

        sql = text(f"""
            SELECT *, embedding <=> CAST(:vec AS vector) AS distance
            FROM dna_memory
            WHERE active = true
              AND embedding IS NOT NULL
              {type_clause}
            ORDER BY distance ASC
            LIMIT :lim
        """)
        with self._session() as session:
            rows = session.execute(sql, params).fetchall()

        results = []
        for r in rows:
            record = self._to_record(r)
            record.distance = float(r.distance)
            results.append(record)
        return results

    # ------------------------------------------------------------------
    # Upsert with dedup + contradiction handling (§6)
    # ------------------------------------------------------------------

    def upsert_with_checks(
        self,
        content: str,
        compare_fn: Optional[Callable[..., str]] = None,
        merge_fn: Optional[Callable[..., str]] = None,
        force_distinct: bool = False,
        **create_kwargs,
    ) -> DNAMemoryRecord:
        """
        Dedup-aware memory creation. Embedding similarity only *selects*
        candidates — an LLM compare verdict decides (§6):

          SAME        → confirm the existing memory
          REFINES     → merge content, revise (supersede) the existing one
          CONTRADICTS → revise as user correction (archives the old belief)
          DISTINCT    → create a new memory

        compare_fn / merge_fn are injectable for testing; they default to the
        reasoning-tier LLM helpers in this module.

        force_distinct=True bypasses dedup entirely and always creates a fresh
        row with the caller's stated source/confidence/due_at. Used by the
        reflection grounding guard for downgraded (untrusted) statements so a
        hallucination can never be merged into — or inherit the trust of — a
        pre-existing high-confidence memory.
        """
        compare_fn = compare_fn or _llm_compare
        merge_fn = merge_fn or _llm_merge

        if force_distinct:
            return self.create_memory(content, **create_kwargs)

        query_vec = self._embed(content)
        candidates: list[DNAMemoryRecord] = []
        if query_vec is not None:
            candidates = self._similarity_search(query_vec, limit=3)

        best = candidates[0] if candidates else None
        best_similarity = (1.0 - best.distance) if best and best.distance is not None else 0.0

        if best is None or best_similarity < SIMILARITY_FLOOR:
            return self.create_memory(content, **create_kwargs)

        verdict = compare_fn(new=content, existing=best.content)

        if verdict == "SAME":
            return self.confirm_memory(best.id)
        if verdict == "REFINES":
            merged = merge_fn(new=content, existing=best.content)
            return self.revise_memory(best.id, merged, reason="refinement")
        if verdict == "CONTRADICTS":
            return self.revise_memory(
                best.id, content, reason="contradiction", contradiction=True,
            )
        return self.create_memory(content, **create_kwargs)


    # ------------------------------------------------------------------
    # Retrieval (§7.1 — composite-scored semantic layer)
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        memory_types: Optional[list[str]] = None,
    ) -> list[DNAMemoryRecord]:
        """
        Semantic retrieval with composite re-ranking (§7.1 Layer 2):

            composite = 0.45·semantic + 0.30·confidence
                      + 0.15·type_weight + 0.10·recency

        Falls back to most-recently-confirmed active memories if the
        embedding model is unavailable.
        """
        now = datetime.now(timezone.utc)
        query_vec = self._embed(query)

        if query_vec is None:
            with self._session() as session:
                rows = (
                    session.query(DNAMemoryRow)
                    .filter(DNAMemoryRow.active.is_(True))
                    .order_by(DNAMemoryRow.last_confirmed.desc())
                    .limit(top_k)
                    .all()
                )
                return [self._to_record(r) for r in rows]

        candidates = self._similarity_search(
            query_vec,
            limit=top_k * OVER_RETRIEVE_FACTOR,
            memory_types=memory_types,
        )

        for cand in candidates:
            semantic = 1.0 - (cand.distance if cand.distance is not None else 1.0)
            recency = self._recency_score(cand.last_confirmed, now)
            cand.composite_score = (
                W_SEMANTIC * semantic
                + W_CONFIDENCE * cand.confidence
                + W_TYPE * TYPE_WEIGHTS.get(cand.memory_type, 0.5)
                + W_RECENCY * recency
            )

        candidates.sort(key=lambda r: r.composite_score or 0.0, reverse=True)
        return candidates[:top_k]

    # ------------------------------------------------------------------
    # Deterministic layer (§7.1 Layer 1 — never semantically retrieved)
    # ------------------------------------------------------------------

    def get_deterministic(
        self,
        core_top_k: int = CORE_TOP_K,
        due_window_days: int = DUE_WINDOW_DAYS,
    ) -> dict[str, list[DNAMemoryRecord]]:
        """
        Memories that must ALWAYS reach the reasoner regardless of the query:

          due                — time-sensitive memories (due_at within the
                               window, overdue included), soonest first
          core               — top fact/goal memories by confidence
          pending_validation — up to 1 unconfirmed mentor inference awaiting
                               user validation (§10.2)
        """
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=due_window_days)

        with self._session() as session:
            due_rows = (
                session.query(DNAMemoryRow)
                .filter(
                    DNAMemoryRow.active.is_(True),
                    DNAMemoryRow.due_at.isnot(None),
                    DNAMemoryRow.due_at <= horizon,
                )
                .order_by(DNAMemoryRow.due_at.asc())
                .all()
            )
            core_rows = (
                session.query(DNAMemoryRow)
                .filter(
                    DNAMemoryRow.active.is_(True),
                    DNAMemoryRow.memory_type.in_(["fact", "goal"]),
                    DNAMemoryRow.confidence >= CORE_MIN_CONFIDENCE,
                )
                .order_by(DNAMemoryRow.confidence.desc())
                .limit(core_top_k)
                .all()
            )
            validation_rows = (
                session.query(DNAMemoryRow)
                .filter(
                    DNAMemoryRow.active.is_(True),
                    DNAMemoryRow.source == "mentor_inferred",
                    DNAMemoryRow.user_confirmed.is_(False),
                    DNAMemoryRow.confidence >= VALIDATION_MIN_CONFIDENCE,
                )
                .order_by(DNAMemoryRow.confidence.desc())
                .limit(1)
                .all()
            )

            # Convert inside the session — ORM objects expire after commit.
            return {
                "due": [self._to_record(r) for r in due_rows],
                "core": [self._to_record(r) for r in core_rows],
                "pending_validation": [self._to_record(r) for r in validation_rows],
            }


    def list_memories(
        self,
        active_only: bool = True,
        memory_type: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 200,
    ) -> list[DNAMemoryRecord]:
        """Flat listing for the transparency panel / debug CLI."""
        with self._session() as session:
            query = session.query(DNAMemoryRow)
            if active_only:
                query = query.filter(DNAMemoryRow.active.is_(True))
            if memory_type:
                query = query.filter(DNAMemoryRow.memory_type == memory_type)
            if source:
                query = query.filter(DNAMemoryRow.source == source)
            rows = (
                query.order_by(DNAMemoryRow.last_confirmed.desc())
                .limit(limit)
                .all()
            )
            return [self._to_record(r) for r in rows]

    # ------------------------------------------------------------------
    # Audit (§11.2 — every mutation leaves a queryable trail)
    # ------------------------------------------------------------------

    def _audit(
        self,
        action: str,
        record: Optional[DNAMemoryRecord],
        summary: str = "",
    ) -> None:
        """Write a `memory_op` row to episodic_events. Never raises."""
        if not self._audit_enabled:
            return
        try:
            payload: dict[str, Any] = {"action": action, "summary": summary}
            if record is not None:
                payload["memory"] = record.model_dump(mode="json")
            with self._session() as session:
                session.add(EpisodicEvent(
                    source_agent=AUDIT_SOURCE,
                    event_type="memory_op",
                    content=f"memory_op/{action}: {summary}"[:500],
                    payload=payload,
                    tags=["memory_op", action],
                    importance=2,
                ))
        except Exception as exc:
            print(f"[DNAMemoryStore] audit write failed: {exc}")


# ---------------------------------------------------------------------------
# Shared singleton (mirrors get_memory_manager in store.py)
# ---------------------------------------------------------------------------

_dna_store_instance: Optional[DNAMemoryStore] = None


def get_dna_store(database_url: str = DB_URL) -> DNAMemoryStore:
    """Return a shared singleton instance of DNAMemoryStore."""
    global _dna_store_instance
    if _dna_store_instance is None:
        _dna_store_instance = DNAMemoryStore(database_url)
    return _dna_store_instance

