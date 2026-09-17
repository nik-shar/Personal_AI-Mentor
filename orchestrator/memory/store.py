"""
orchestrator/memory/store.py

The unified interface to the three memory tiers.

- Profile store  → `profile_facts` table (slow-changing facts)
- Episodic store → `episodic_events` table (append-only log)
- Working memory → in-process only; not persisted here
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from orchestrator.config import DB_URL, EMBEDDING_DIMENSION, local_tz, now_local
from orchestrator.memory import core as memory_core
from orchestrator.memory.grid import ANCHOR_STATES, grid_date_of, parse_wall_clock, slot_span
from orchestrator.memory.models import (
    AgentPrivateMemory,
    ConversationSession,
    DaySlot,
    EpisodicEvent,
    ProfileFact,
    ScheduleEvent,
)

# ---------------------------------------------------------------------------
# Embedding — owned by `memory/core.py`
#
# This block used to *define* the model, and `dna_store.py` + `retriever.py`
# imported `_get_embed_model` from here — a connection module owning a concern
# two other modules depended on. `dna_store.py` now calls core directly; this
# thin re-export keeps `retriever.py` working until it is migrated too.
# ---------------------------------------------------------------------------


def _get_embed_model():
    """Deprecated re-export of `memory_core.get_embed_model()` (retriever.py)."""
    return memory_core.get_embed_model()


class MemoryManager:
    """
    Single access point for all persisted memory.

    Expects a Postgres database with the pgvector extension. Call
    `ensure_schema()` once before first use.
    """

    def __init__(self, database_url: str = DB_URL) -> None:
        self.database_url = database_url
        # Shared per-URL factory — one connection pool per database, not one
        # per store instance (see memory/core.py).
        self.SessionLocal, self.engine = memory_core.get_session_factory(database_url)

    # -----------------------------------------------------------------------
    # Schema management
    # -----------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the extension, tables, column backfills and vector index.

        Delegates to `memory/core.py`, which owns the union of what this store
        and `dna_store.py` used to apply separately — so whichever store is
        constructed first now initialises the whole schema instead of half.
        """
        memory_core.ensure_schema(self.database_url)

    def _session(self):
        """A commit-on-success transaction scope, owned by `memory/core.py`.

        Kept as a method so the internal call sites are untouched; the
        rollback/commit/close policy is no longer duplicated per store.
        """
        return memory_core.session_scope(self.database_url)

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

    # -----------------------------------------------------------------------
    # Schedule / Calendar Events (PostgreSQL Table)
    # -----------------------------------------------------------------------

    def create_schedule_event(self, event_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new schedule event in PostgreSQL."""
        event = ScheduleEvent(
            id=event_data.get("id"),
            title=event_data.get("title", "Untitled Task"),
            category=event_data.get("category", "learning"),
            start_time=event_data.get("start_time"),
            end_time=event_data.get("end_time"),
            duration_min=event_data.get("duration_min", 30),
            status=event_data.get("status", "scheduled"),
            priority=event_data.get("priority", "should"),
            linked_goal=event_data.get("linked_goal"),
            notes=event_data.get("notes"),
            block_kind=event_data.get("block_kind", "task"),
        )
        with self._session() as session:
            session.add(event)
            session.flush()
            result = self._schedule_event_to_dict(event)
        # Mirror the booking into the day grid when we have a concrete start
        # time — the grid is the mentor's calendar surface.
        if result.get("start_time"):
            try:
                self.reflect_schedule_on_day(datetime.fromisoformat(result["start_time"]))
            except Exception as exc:
                print(f"[store] day-grid mirror failed: {exc}")
        return result

    def get_schedule_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query schedule events within optional date range, status, or category."""
        with self._session() as session:
            query = session.query(ScheduleEvent)
            if start_date is not None:
                query = query.filter(ScheduleEvent.start_time >= start_date)
            if end_date is not None:
                query = query.filter(ScheduleEvent.start_time <= end_date)
            if status is not None:
                query = query.filter(ScheduleEvent.status == status)
            if category is not None:
                query = query.filter(ScheduleEvent.category == category)
            
            query = query.order_by(ScheduleEvent.start_time.asc().nulls_last(), ScheduleEvent.created_at.asc())
            rows = query.all()
            return [self._schedule_event_to_dict(row) for row in rows]

    def update_schedule_event(self, event_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Update an existing schedule event by ID."""
        with self._session() as session:
            event = session.query(ScheduleEvent).filter_by(id=event_id).first()
            if not event:
                return None
            for key, val in updates.items():
                if hasattr(event, key) and key not in ("id", "created_at"):
                    setattr(event, key, val)
            event.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._schedule_event_to_dict(event)

    def delete_schedule_event(self, event_id: str) -> bool:
        """Delete a schedule event by ID."""
        with self._session() as session:
            event = session.query(ScheduleEvent).filter_by(id=event_id).first()
            if not event:
                return False
            session.delete(event)
            return True

    def bulk_sync_schedule(self, events: list[dict[str, Any]], date: datetime) -> list[dict[str, Any]]:
        """
        Overwrite or replace schedule events for a specific date with a new list of events.
        Useful when generating a fresh daily plan.
        """
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)

        with self._session() as session:
            # Clear existing scheduled events for this date
            session.query(ScheduleEvent).filter(
                ScheduleEvent.start_time >= start_of_day,
                ScheduleEvent.start_time <= end_of_day,
            ).delete(synchronize_session=False)

            created_records = []
            for ev_data in events:
                if not ev_data.get("start_time"):
                    ev_data["start_time"] = start_of_day
                ev = ScheduleEvent(
                    id=ev_data.get("id"),
                    title=ev_data.get("title", "Task"),
                    category=ev_data.get("category", "learning"),
                    start_time=ev_data.get("start_time"),
                    end_time=ev_data.get("end_time"),
                    duration_min=ev_data.get("duration_min", 30),
                    status=ev_data.get("status", "scheduled"),
                    priority=ev_data.get("priority", "should"),
                    linked_goal=ev_data.get("linked_goal"),
                    notes=ev_data.get("notes"),
                    block_kind=ev_data.get("block_kind", "task"),
                )
                session.add(ev)
                session.flush()
                created_records.append(self._schedule_event_to_dict(ev))

            return created_records

    @staticmethod
    def _schedule_event_to_dict(event: ScheduleEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "title": event.title,
            "category": event.category,
            "start_time": event.start_time.isoformat() if event.start_time else None,
            "end_time": event.end_time.isoformat() if event.end_time else None,
            "duration_min": event.duration_min,
            "status": event.status,
            "priority": event.priority,
            "linked_goal": event.linked_goal,
            "notes": event.notes,
            "block_kind": event.block_kind,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
        }

    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Day grid (rolling 48-slot calendar surface)
    #
    # The grid mirrors bookings from schedule_events into slot units and is the
    # only place where free time, life anchors (sleep/meal) and per-slot
    # completion live. Raw rows are kept for a rolling window (default 21 days)
    # then pruned — the day's narrative summary carries the long-term story.
    # -----------------------------------------------------------------------

    @staticmethod
    def _grid_date(value: Any) -> Any:
        """Normalize a date/datetime/ISO-string to a date for grid lookups."""
        return grid_date_of(value)

    def ensure_day_slots(self, date: Any) -> int:
        """Create the 48 free slots for a date if they do not exist yet. Returns created count."""
        d = self._grid_date(date)
        if d is None:
            return 0
        with self._session() as session:
            existing = {
                r[0]
                for r in session.query(DaySlot.slot_index).filter(DaySlot.date == d).all()
            }
            created = 0
            for idx in range(48):
                if idx in existing:
                    continue
                session.add(DaySlot(date=d, slot_index=idx, state="free"))
                created += 1
        return created

    def get_day_slots(self, date: Any) -> list[dict[str, Any]]:
        """All 48 slots for a date, ordered by slot_index (missing rows default to free)."""
        d = self._grid_date(date)
        if d is None:
            return []
        self.ensure_day_slots(d)
        with self._session() as session:
            rows = (
                session.query(DaySlot)
                .filter(DaySlot.date == d)
                .order_by(DaySlot.slot_index.asc())
                .all()
            )
            return [self._day_slot_to_dict(r) for r in rows]

    def set_day_slot_fields(
        self,
        date: Any,
        slot_index: int,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Upsert one slot's fields (state/status/planned_by/label/event_id)."""
        d = self._grid_date(date)
        if d is None or not (0 <= int(slot_index) < 48):
            return None
        self.ensure_day_slots(d)
        with self._session() as session:
            slot = (
                session.query(DaySlot)
                .filter(DaySlot.date == d, DaySlot.slot_index == int(slot_index))
                .first()
            )
            if slot is None:
                slot = DaySlot(date=d, slot_index=int(slot_index))
                session.add(slot)
            allowed = {"state", "status", "planned_by", "label", "event_id"}
            for key, val in fields.items():
                if key in allowed:
                    setattr(slot, key, val)
            session.flush()
            return self._day_slot_to_dict(slot)

    def clear_event_slots(self, event_id: str) -> int:
        """Release any grid slots attached to an event (e.g. on event delete)."""
        with self._session() as session:
            slots = session.query(DaySlot).filter(DaySlot.event_id == event_id).all()
            for slot in slots:
                slot.event_id = None
                slot.state = "free"
                slot.status = "planned"
            return len(slots)


    def reflect_schedule_on_day(self, date: Any) -> dict[str, Any]:
        """
        Mirror a day's schedule_events into grid task slots.

        Only `free` slots are claimed by an event: anchors (sleep/meal/commute/
        gym) are left untouched, and the count is reported rather than hidden —
        silently overwriting an anchor with a task is exactly what
        toolkits/calendar-manager/guardrails.md forbids. Task slots whose event
        has gone are released back to free.

        Spans wrap across midnight here too. A 23:30 -> 01:30 block claims slots
        on both dates and releasing it clears both; the previous implementation
        clamped at 48 and mirrored only the start date, so the tail was
        invisible to availability. The event query deliberately looks back a day,
        otherwise the tail of yesterday's cross-midnight block would be treated
        as stale and released on every reflect.
        """
        d = self._grid_date(date)
        if d is None:
            return {"reflected": 0, "released": 0}
        self.ensure_day_slots(d)

        day_start = datetime.combine(d, time.min, tzinfo=local_tz())
        day_end = datetime.combine(d, time.max, tzinfo=local_tz())
        events = self.get_schedule_events(
            start_date=day_start - timedelta(days=1),
            end_date=day_end,
        )

        active_event_ids: set[str] = set()
        claimed: list[tuple[int, str]] = []
        for ev in events:
            ev_start = ev.get("start_time")
            if not ev_start:
                continue
            try:
                ev_start_dt = parse_wall_clock(ev_start)
            except ValueError:
                continue
            duration = int(ev.get("duration_min") or 30)
            # Day membership is a wall-clock question, so an event starting
            # 23:30 belongs to this date and its tail to the next one.
            for span_day, idx in slot_span(ev_start_dt, duration):
                if span_day != d:
                    continue
                active_event_ids.add(str(ev["id"]))
                claimed.append((idx, str(ev["id"])))

        reflected = 0
        skipped_anchor_slots = 0
        existing = {int(s["slot_index"]): s for s in self.get_day_slots(d)}
        for idx, event_id in claimed:
            current = existing.get(idx, {})
            if current.get("state") in ANCHOR_STATES:
                skipped_anchor_slots += 1
                continue
            try:
                slot = self.set_day_slot_fields(
                    d, idx, state="task", event_id=event_id,
                    status="scheduled", planned_by="mentor",
                )
            except Exception as exc:
                print(f"[store] reflect slot {idx} failed: {exc}")
                continue
            if slot and slot.get("state") == "task":
                reflected += 1

        released = 0
        with self._session() as session:
            for slot in session.query(DaySlot).filter(DaySlot.date == d, DaySlot.event_id.isnot(None)).all():
                if slot.event_id not in active_event_ids:
                    slot.event_id = None
                    slot.state = "free"
                    slot.status = "planned"
                    released += 1
        return {
            "reflected": reflected,
            "released": released,
            "skipped_anchor_slots": skipped_anchor_slots,
        }

    def list_day_slot_dates(self, window_days: int = 21) -> list[Any]:
        """Distinct dates that currently have grid rows, oldest first."""
        cutoff = (now_local() - timedelta(days=max(1, int(window_days)))).date()
        with self._session() as session:
            rows = (
                session.query(DaySlot.date)
                .filter(DaySlot.date >= cutoff)
                .distinct()
                .order_by(DaySlot.date.asc())
                .all()
            )
        return [row[0] for row in rows]

    def clear_day_slots(self, date: Any) -> int:
        """Delete every grid row for a date (used to re-derive after a basis change)."""
        d = self._grid_date(date)
        if d is None:
            return 0
        with self._session() as session:
            removed = session.query(DaySlot).filter(DaySlot.date == d).delete()
        return int(removed or 0)

    def prune_old_day_slots(self, window_days: int = 21) -> int:
        """Drop raw grid rows older than the rolling window. Returns deleted count."""
        cutoff = (now_local() - timedelta(days=max(1, int(window_days)))).date()
        with self._session() as session:
            deleted = (
                session.query(DaySlot)
                .filter(DaySlot.date < cutoff)
                .delete(synchronize_session=False)
            )
        return deleted

    @staticmethod
    def _day_slot_to_dict(slot: DaySlot) -> dict[str, Any]:
        return {
            "date": slot.date.isoformat(),
            "slot_index": slot.slot_index,
            "state": slot.state,
            "planned_by": slot.planned_by,
            "status": slot.status,
            "label": slot.label,
            "event_id": slot.event_id,
            "clock": f"{slot.slot_index * 30 // 60:02d}:{slot.slot_index * 30 % 60:02d}",
        }


    # Conversation sessions (full transcripts with timestamps)
    # -----------------------------------------------------------------------

    def save_conversation_session(
        self,
        session_id: str,
        started_at: datetime,
        ended_at: datetime,
        transcript: list[dict[str, Any]],
        summary: Optional[str] = None,
    ) -> Optional[str]:
        """Persist a finished conversation session transcript. Returns row id."""
        try:
            with self._session() as session:
                row = ConversationSession(
                    session_id=session_id,
                    started_at=started_at,
                    ended_at=ended_at,
                    turn_count=len(transcript),
                    transcript=transcript,
                    summary=summary,
                )
                session.add(row)
                session.flush()
                return str(row.id)
        except Exception as exc:
            print(f"[store] save_conversation_session failed: {exc}")
            return None

    def get_last_conversation_session(self) -> Optional[dict[str, Any]]:
        """The most recently ended conversation session, or None."""
        with self._session() as session:
            row = (
                session.query(ConversationSession)
                .order_by(ConversationSession.ended_at.desc())
                .first()
            )
            return self._conversation_session_to_dict(row) if row else None

    def get_recent_conversation_sessions(self, limit: int = 3) -> list[dict[str, Any]]:
        """Recent conversation sessions, newest first."""
        with self._session() as session:
            rows = (
                session.query(ConversationSession)
                .order_by(ConversationSession.ended_at.desc())
                .limit(limit)
                .all()
            )
            return [self._conversation_session_to_dict(r) for r in rows]

    def get_conversation_session_count(self) -> int:
        """Total number of conversation sessions."""
        with self._session() as session:
            return session.query(ConversationSession).count()

    # -----------------------------------------------------------------------
    # Continuous conversation thread (rolling transcript + summary)
    # -----------------------------------------------------------------------

    def save_conversation_thread(
        self,
        transcript: list[dict[str, Any]],
        summary: str = "",
        since: str | None = None,
    ) -> None:
        """
        Persist the continuous thread: the live transcript window and the
        rolling summary (profile_facts system/conversation_thread + *..running_summary).

        `since` is the first-summarized-at marker; when omitted it is preserved
        from the existing row (or set to now on first write).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self.set_profile_fact(
            "system", "conversation_thread",
            {"transcript": list(transcript or []), "updated_at": now_iso},
            source="orchestrator",
        )
        existing = self.get_profile_fact("system", "conversation_running_summary") or {}
        if not isinstance(existing, dict):
            existing = {}
        merged_since = since if since is not None else existing.get("since")
        if merged_since is None:
            merged_since = now_iso
        self.set_profile_fact(
            "system", "conversation_running_summary",
            {
                "summary": summary if summary else existing.get("summary", ""),
                "since": merged_since,
                "updated_at": now_iso,
            },
            source="orchestrator",
        )

    def load_conversation_thread(self) -> dict[str, Any]:
        """
        Load the continuous thread state. Returns
        {transcript, updated_at, summary, since} with safe empties.
        """
        thread = {}
        summary_row = {}
        try:
            thread = self.get_profile_fact("system", "conversation_thread") or {}
            summary_row = self.get_profile_fact("system", "conversation_running_summary") or {}
        except Exception as exc:
            print(f"[store] conversation thread load failed: {exc}")
        if not isinstance(thread, dict):
            thread = {}
        if not isinstance(summary_row, dict):
            summary_row = {}
        return {
            "transcript": list(thread.get("transcript") or []),
            "updated_at": thread.get("updated_at"),
            "summary": summary_row.get("summary") or "",
            "since": summary_row.get("since"),
        }

    @staticmethod
    def _conversation_session_to_dict(row: ConversationSession) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "session_id": row.session_id,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
            "turn_count": row.turn_count,
            "transcript": row.transcript or [],
            "summary": row.summary,
        }


_memory_manager_instance: Optional[MemoryManager] = None

def get_memory_manager(database_url: str = DB_URL) -> MemoryManager:
    """Return a shared singleton instance of MemoryManager."""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        _memory_manager_instance = MemoryManager(database_url)
    return _memory_manager_instance

