---
created: 2026-08-22
tags:
  - memory
  - schema
  - database
---

# 🗄️ Memory Data Models

> **Role:** SQLAlchemy ORM models and table schemas for the mentor's persistent memory.

---

## Overview

All models live in `orchestrator/memory/models.py`. The database is PostgreSQL with the `pgvector` extension for vector similarity search.

---

## `profile_facts` — Structured Profile Data

Stable, slow-changing facts about Nikhil. Each row is one key-value pair.

```sql
CREATE TABLE profile_facts (
    id          SERIAL PRIMARY KEY,
    category    VARCHAR NOT NULL,          -- identity, career, skills, etc.
    key         VARCHAR NOT NULL,          -- full_name, employment_status, etc.
    value       JSONB NOT NULL,            -- the actual value
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    source      VARCHAR,                   -- who wrote this (orchestrator, agent, user)
    UNIQUE(category, key)
);
```

**Categories:** `identity`, `career`, `education`, `goals`, `skills`, `preferences`, `projects`, `learning`, `system`

---

## `episodic_events` — Append-Only Activity Log

Everything that happened, in order. Supports pgvector semantic search for memory retrieval.

```sql
CREATE TABLE episodic_events (
    id              VARCHAR(36) PRIMARY KEY,   -- UUID
    occurred_at     TIMESTAMPTZ DEFAULT NOW(),
    source_agent    VARCHAR NOT NULL,          -- orchestrator or sub-agents
    event_type      VARCHAR NOT NULL,          -- conversation_turn, daily_plan, etc.
    tags            VARCHAR[] DEFAULT '{}',    -- for filtering
    content         TEXT NOT NULL,             -- human-readable summary
    payload         JSONB,                     -- structured details
    embedding       VECTOR(384),              -- all-MiniLM-L6-v2 embedding
    importance      INTEGER DEFAULT 3,        -- 1-5 priority
    consolidated    BOOLEAN DEFAULT FALSE,    -- tier 1 → tier 2
    archived        BOOLEAN DEFAULT FALSE     -- tier 2 → tier 3
);

CREATE INDEX ix_episodic_events_hot ON episodic_events (occurred_at)
    WHERE consolidated = FALSE AND archived = FALSE;
```

**Event Types:** `conversation_turn`, `daily_plan`, `learning_session`, `job_application`, `linkedin_draft`, `agent_run`, `memory_op`, `weekly_summary`, `monthly_summary`, `mood_note`

---

## `dna_memory` — Organic Memory Store

Natural-language memories with confidence lifecycle and pgvector embedding.

```sql
CREATE TABLE dna_memory (
    id                  VARCHAR(36) PRIMARY KEY,   -- UUID
    content             TEXT NOT NULL,              -- the memory text
    memory_type         VARCHAR(20) NOT NULL,       -- fact|observation|insight|preference|goal|reflection|context
    confidence          FLOAT DEFAULT 0.5,          -- current confidence level
    confidence_ceiling  FLOAT DEFAULT 1.0,          -- max possible confidence
    source              VARCHAR(20) NOT NULL,       -- user_stated|mentor_inferred|data_derived|seeded
    user_confirmed      BOOLEAN DEFAULT FALSE,
    tags                VARCHAR[] DEFAULT '{}',
    active              BOOLEAN DEFAULT TRUE,
    due_at              TIMESTAMPTZ,                -- time-sensitive (deadlines, interviews)
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_confirmed      TIMESTAMPTZ DEFAULT NOW(),
    superseded_by       VARCHAR(36) REFERENCES dna_memory(id),  -- audit trail
    confirmation_count  INTEGER DEFAULT 1,
    embedding           VECTOR(384)                 -- all-MiniLM-L6-v2 embedding
);
```

---

## `conversation_sessions` — Transcript Archive

Full session transcripts persisted when a session closes.

```sql
CREATE TABLE conversation_sessions (
    id              VARCHAR(36) PRIMARY KEY,
    session_id      VARCHAR NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ NOT NULL,
    turn_count      INTEGER DEFAULT 0,
    transcript      JSONB NOT NULL,     -- [{role, content, timestamp}]
    summary         TEXT,               -- reserved for future LLM summary
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## `schedule_events` — Calendar & Planning

Scheduled events and daily time blocks, used for momentum computation and schedule management.

```sql
CREATE TABLE schedule_events (
    id              VARCHAR(36) PRIMARY KEY,
    session_id      VARCHAR,
    occurred_at     TIMESTAMPTZ NOT NULL,
    title           VARCHAR NOT NULL,
    category        VARCHAR,
    start_time      TIMESTAMPTZ,
    duration_min    INTEGER DEFAULT 30,
    status          VARCHAR DEFAULT 'scheduled',  -- scheduled|completed|cancelled|postponed
    priority        VARCHAR DEFAULT 'should',
    linked_goal     VARCHAR,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## `agent_private_memory` — Agent Scratch State

Per-agent private scratch state for internal bookkeeping.

```sql
CREATE TABLE agent_private_memory (
    agent_name  VARCHAR PRIMARY KEY,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```


---

> **Category:** 🧬 Memory · **Parent:** [[Memory System Overview]]
