---
created: 2026-08-22
tags:
  - design
  - memory
  - dna
---

# 🧬 DNA Memory Redesign v2

> **Role:** The design document for the organic DNA memory system — the foundational design doc that supersedes v1.

---

## Overview

`dna_memory_redesign_v2.md` is the canonical design document for the DNA memory system. It documents the 15-section proposal that replaced the schema-heavy `mentor_user_state` + `mentor_meta_patterns` with an organic memory store.

## What v2 Changed

| Change | v1 Problem | v2 Fix |
|--------|-----------|--------|
| Two-consumer boundary | All profile_facts → DNA memory broke code paths | Structured keys stay structured forever |
| Single Postgres table | Double-write to ChromaDB + Postgres could drift | `pgvector` column in `dna_memory` table |
| Confirmation discipline | LLM could confirm its own inferences | Only user affirmation or unambiguous action |
| Confidence ceiling 0.6 | Inferred insights competed at full weight | 0.4 base → 0.6 cap until user-confirmed |
| Contradiction-aware upsert | Cosine could not detect negation | LLM compare: SAME/REFINES/CONTRADICTS/DISTINCT |
| Deterministic injection | Deadlines depended on embedding similarity | `due_at` always injected |
| Golden-set eval | No regression tests | Required before Phase 3 |
| Memory visibility panel | Open question | Required in Phase 2 |

## Source File

📄 **[`dna_memory_redesign_v2.md`](../dna_memory_redesign_v2.md)** (34,246 bytes)

Also see the original v1: [`dna_memory_redesign.md`](../dna_memory_redesign.md) (33,351 bytes)


---

> **Category:** 🧬 Memory · **Parent:** [[DNA Memory System]]
