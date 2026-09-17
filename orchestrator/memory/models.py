"""
orchestrator/memory/models.py

SQLAlchemy models for the mentor's two persistent memory tiers.
Matches the mentor's concrete Postgres schema:
- profile_facts (slow-changing facts)
- episodic_events (append-only log)
- agent_private_memory (agent-scoped scratch state)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

from orchestrator.config import DB_URL, EMBEDDING_DIMENSION

Base = declarative_base()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProfileFact(Base):
    """Slow-changing structured facts about Nikhil."""

    __tablename__ = "profile_facts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String, nullable=False)
    key = Column(String, nullable=False)
    value = Column(JSONB, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )
    source = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("category", "key", name="uq_profile_fact_category_key"),
        Index("ix_profile_facts_category_key", "category", "key"),
    )


class EpisodicEvent(Base):
    """Append-only log of everything that happened."""

    __tablename__ = "episodic_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    occurred_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
        server_default=text("NOW()"),
    )
    source_agent = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    tags = Column(ARRAY(String), default=list)
    content = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=True)
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=True)
    importance = Column(Integer, default=3)

    # Memory tier management — drives the tiered recall access pattern.
    # consolidated=True: event was summarised into a weekly_summary; no longer
    #   included in the default hot-tier query (but still queryable directly).
    # archived=True: weekly_summary was further compressed into a monthly_summary;
    #   effectively cold storage — only surface via RAG or explicit query.
    consolidated = Column(Boolean, default=False, nullable=False, server_default="false")
    archived = Column(Boolean, default=False, nullable=False, server_default="false")

    __table_args__ = (
        Index("ix_episodic_events_tags", "tags", postgresql_using="gin"),
        Index("ix_episodic_events_source_occurred", "source_agent", "occurred_at"),
        Index("ix_episodic_events_type_occurred", "event_type", "occurred_at"),
        # Partial index for hot-tier reads — skips consolidated/archived rows.
        Index(
            "ix_episodic_events_hot",
            "occurred_at",
            postgresql_where=text("consolidated = false AND archived = false"),
        ),
    )


class AgentPrivateMemory(Base):
    """Per-agent private scratch state."""

    __tablename__ = "agent_private_memory"

    agent_name = Column(String, primary_key=True)
    data = Column(JSONB, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )


class ScheduleEvent(Base):
    """Calendar/Schedule events for day planning, meetings, and activity tracking."""

    __tablename__ = "schedule_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False)
    category = Column(String, nullable=False, default="learning", index=True)
    start_time = Column(DateTime(timezone=True), nullable=True, index=True)
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration_min = Column(Integer, nullable=False, default=30)
    status = Column(String, nullable=False, default="scheduled", index=True)  # scheduled, in_progress, completed, cancelled, postponed
    priority = Column(String, nullable=False, default="should")  # must, should, nice-to-have
    linked_goal = Column(String, nullable=True)  # TopicNode ID or project ID
    notes = Column(Text, nullable=True)
    # 'task'  — a mentor/user-placed actionable block (study, work, meeting)
    # 'anchor' — a recurring life fixture (sleep, dinner, commute, gym)
    #            used by the day-grid availability logic as a hard reservation.
    block_kind = Column(String, nullable=False, default="task", index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_schedule_events_start_status", "start_time", "status"),
    )


class DaySlot(Base):
    """
    One 30-minute slot in the rolling per-day calendar grid.

    48 slots per day (00:00 → 23:30), indexed 0..47. Raw rows are kept for a
    rolling window (default 21 days) and then pruned back to the day's
    narrative summary + histogram (daily_summary). `schedule_events` stays the
    canonical layer for booked blocks; this grid mirrors those bookings into
    slot units and is the ONLY place where free-time, life anchors (sleep,
    meal) and per-slot completion live.
    """

    __tablename__ = "day_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    slot_index = Column(Integer, nullable=False)  # 0..47 → 30-min unit from 00:00

    # free | sleep | meal | task | commute | gym | blocked | learned_work
    state = Column(String, nullable=False, default="free", index=True)
    # 'user' (explicit anchor/user edit) | 'mentor' (placed by the mentor) | 'learned'
    planned_by = Column(String, nullable=False, default="user")
    # planned | completed | skipped | cancelled  (meaningful for task slots)
    status = Column(String, nullable=False, default="planned")
    label = Column(String, nullable=True)  # extra descriptor: "dinner", "DSA block"
    event_id = Column(String(36), ForeignKey("schedule_events.id"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("date", "slot_index", name="uq_day_slots_date_slot"),
    )


class DNAMemoryRow(Base):
    """
    Organic natural-language memory (dna_memory_redesign_v2.md §3).

    One row per discrete thing the mentor knows about Nikhil — facts,
    observations, insights, preferences, goals, reflections, context —
    with a confidence lifecycle (confirmation growth, user-validation
    ceiling, decay) and inline pgvector embedding for semantic retrieval.
    """

    __tablename__ = "dna_memory"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    content = Column(Text, nullable=False)
    memory_type = Column(String(20), nullable=False)
    # fact | observation | insight | preference | goal | reflection | context

    confidence = Column(Float, nullable=False, default=0.5)
    confidence_ceiling = Column(Float, nullable=False, default=1.0)
    # mentor_inferred memories are capped (0.6) until the user confirms them.

    source = Column(String(20), nullable=False)
    # user_stated | mentor_inferred | data_derived | seeded

    user_confirmed = Column(Boolean, nullable=False, default=False)
    tags = Column(ARRAY(String), default=list)
    active = Column(Boolean, nullable=False, default=True)

    due_at = Column(DateTime(timezone=True), nullable=True)
    # Time-sensitive memories (deadlines, interviews) — always injected into
    # context while upcoming, never dependent on semantic retrieval (§7.1).

    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    last_confirmed = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    superseded_by = Column(String(36), ForeignKey("dna_memory.id"), nullable=True)
    confirmation_count = Column(Integer, nullable=False, default=1)

    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=True)

    __table_args__ = (
        Index("ix_dna_memory_type", "memory_type"),
        Index(
            "ix_dna_memory_active",
            "active",
            postgresql_where=text("active = true"),
        ),
        Index(
            "ix_dna_memory_due_at",
            "due_at",
            postgresql_where=text("due_at IS NOT NULL AND active = true"),
        ),
        Index("ix_dna_memory_tags", "tags", postgresql_using="gin"),
    )


class ConversationSession(Base):
    """
    A finished conversation session's full transcript, with timestamps.

    Written when a session closes — CLI exit, explicit POST /api/session/end,
    or the inactivity-gap auto-close in intake_node. Gives the mentor temporal
    context across days: when the last conversation happened, how long it ran,
    and exactly what was said.
    """

    __tablename__ = "conversation_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=False, index=True)
    turn_count = Column(Integer, nullable=False, default=0)
    transcript = Column(JSONB, nullable=False)   # [{role, content, timestamp}]
    summary = Column(Text, nullable=True)        # reserved for a future LLM summary
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)


def _ensure_engine(url: str = DB_URL):
    engine = create_engine(url, future=True)
    return engine


def make_sessionmaker(url: str = DB_URL):
    engine = _ensure_engine(url)
    return sessionmaker(bind=engine), engine
