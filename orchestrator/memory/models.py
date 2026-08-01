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

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

from pgvector.sqlalchemy import Vector
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


def _ensure_engine(url: str = DB_URL):
    engine = create_engine(url, future=True)
    return engine


def make_sessionmaker(url: str = DB_URL):
    engine = _ensure_engine(url)
    return sessionmaker(bind=engine), engine
