---
created: 2026-08-22
tags:
  - memory
  - dna
  - design
---

# 🧬 DNA Memory System

> **Role:** Organic memory with confidence lifecycle — the mentor's understanding of Nikhil grows through conversation.

---

## Overview

DNA Memory is the cornerstone of the mentor's continuity. It replaces the old `mentor_user_state` (23 pre-determined columns) with natural-language memories that the mentor learns and refines over time. Think of it as an organic, growing knowledge base about who Nikhil is, how he works, and what's happening in his life.

## Core Design (v2)

The full design is documented in [[dna_memory_redesign_v2.md]].

### Key improvements over v1:

1. **Two-consumer boundary** — Structured keys (`energy_level`, `employment_status`) stay in `profile_facts` for code paths; DNA memory holds natural-language understanding
2. **Single Postgres table with pgvector** — No double-write to ChromaDB; one DB, transactional
3. **Confirmation discipline** — Only explicit user affirmation or unambiguous action counts as evidence
4. **Confidence ceiling for inferences** — `mentor_inferred` memories cap at 0.6 until user-confirmed
5. **Contradiction-aware upsert** — LLM compare verdict (SAME / REFINES / CONTRADICTS / DISTINCT) before confirming
6. **Deterministic injection** — Deadlines, today's schedule, core identity never depend on semantic retrieval
7. **Golden-set eval** — Required regression tests for scoring knobs

## Memory Types

| Type | Description | Example |
|------|-------------|---------|
| `fact` | Concrete, verifiable fact | "Nik has a B.Tech from IIT Roorkee" |
| `observation` | Behavioral observation | "Nik consistently finishes deep-work blocks in the morning" |
| `insight` | Deeper understanding | "Nik thrives when given scope reduction, not pressure" |
| `preference` | Known preference | "Nik prefers concise, no-fluff communication" |
| `goal` | Active or stated goal | "Nik is targeting AI Engineer roles" |
| `reflection` | Mentor's reflective thought | "Nik's avoidance of system design seems related to format, not difficulty" |
| `context` | Situational context | "Nik has an interview at Stripe next Friday" |

## Confidence Lifecycle

### Starting values by source

| Source | Confidence | Ceiling | Description |
|--------|-----------|---------|-------------|
| `user_stated` | 0.95 | 1.0 | User said it directly |
| `seeded` | 0.95 | 1.0 | Pre-seeded anchor facts |
| `data_derived` | 0.70 | 0.95 | Pattern mined from schedule data |
| `mentor_inferred` | 0.40 | 0.60 | Mentor inferred it (capped until confirmed) |

### Confirmation growth

When a memory is re-observed (SAME), confidence grows via sigmoid:

```
f(n) = 0.3 + 0.65 / (1 + exp(-0.3 * (n - 8)))
```

Where `n` is the confirmation count. After ~15 observations, confidence approaches 0.95.

User confirmation unlocks the ceiling:
- `USER_CONFIRM_CONFIDENCE = 0.85` (floor after user affirms)
- `USER_CONFIRM_CEILING = 0.95` (unlocked ceiling)

### Decay (weekly job)

- After 45 days without re-confirmation, confidence decays by factor 0.85
- Below 0.15 confidence → archived (hidden from active retrieval)

## Retrieval

Two-layer retrieval in `DNAMemoryStore.retrieve()`:

### Layer 1 — Deterministic injection (never semantically retrieved)
- Due-at memories (deadlines, interviews)
- Core identity facts / goals
- Pending validation candidates (max 1)

### Layer 2 — Semantic retrieval (composite scored)
```
score = 0.45 × semantic_similarity
      + 0.30 × confidence
      + 0.15 × type_weight
      + 0.10 × recency_boost
```

Type weights: `fact` (1.0) > `goal` (0.9) > `preference` (0.85) > `insight` (0.75) > ...

## Reflection Pipeline

After every conversation turn, `reflect_on_turn_async()` runs in a background thread:

1. Retrieves context memories relevant to the exchange
2. LLM reviews the turn → produces `MemoryReflection` (new memories, confirmations, revisions)
3. Applies changes to the DNA store via `apply_reflection()`
4. Advances discovery-facet coverage if applicable

---

> **Category:** 🧬 Memory · **Parent:** [[Memory System Overview]]