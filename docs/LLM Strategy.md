---
created: 2026-08-22
tags:
  - llm
  - infrastructure
---

# 🤖 LLM Strategy

> **Role:** The 3-tier model selection strategy for cost and latency optimization.

---

## Tier Overview

| Tier | Model | Provider | Purpose | Latency | Cost |
|------|-------|----------|---------|---------|------|
| **1 — Router** | `gpt-4o-mini` | OpenAI | Fast routing, classification, reflection, extraction | ~1s | Low |
| **2 — Converser** | `Qwen/Qwen3-235B-A22B-Instruct-2507` | Nebius | High-quality conversational responses, deep reasoning | ~3-5s | Medium |
| **3 — Writer** | `MiniMaxAI/MiniMax-M3` | Nebius | Deep content generation, tutorials, drafts, long-form writing | ~5-10s | Medium |

## Tier 1 — Router (`get_reasoning_llm`)

**Used by:**
- `reason_node` — Context sufficiency scoring, routing decisions
- `dna_reflection.py` — Memory reflection LLM calls
- `dna_store.py` — §6 compare/merge LLM calls
- `consolidator.py` — Summary generation, fact extraction

**Config:**
```python
ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
```

## Tier 2 — Converser (`get_conversational_llm`)

**Used by:**
- `direct_response` path — When the reasoner chooses not to route to any agent
- `synthesizer.py` — Response voice framing (the mentor's voice layer)
- `fallback_agent` — General chat responses

**Config:**
```python
ChatOpenAI(
    model="Qwen/Qwen3-235B-A22B-Instruct-2507",
    base_url="https://api.tokenfactory.nebius.com/v1/",
    temperature=0.4
)
```
**Fallback:** OpenAI `gpt-4o-mini` if Nebius is unreachable.

## Tier 3 — Writer (`get_writer_llm`)

**Used by:**
- `goal_decomposer` — Deep tutorial note generation (Phase 2)
- `linkedin_writer` — Draft writing and revision

**Config:**
```python
ChatOpenAI(
    model="MiniMaxAI/MiniMax-M3",
    base_url="https://api.tokenfactory.us-central1.nebius.com/v1/",
    temperature=0.4
)
```
**Fallback:** OpenAI `gpt-4o-mini` if Nebius is unreachable.

## Health Checking

At module import, the system probes the Nebius endpoint:
- **Success:** Caches as healthy, uses Nebius for Tier 2/3
- **Failure:** Falls back to OpenAI `gpt-4o-mini` for all tiers
- One probe per process lifetime — `_NEBIUS_HEALTHY` singleton

## Environment Variables

```env
# Required for Tier 2/3 (Nebius)
NEBIUS_API_KEY=your_key_here

# Required for Tier 1 (OpenAI)
OPENAI_API_KEY=your_key_here

# Optional overrides
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/
WRITER_BASE_URL=https://api.tokenfactory.us-central1.nebius.com/v1/
NEBIUS_MODEL=Qwen/Qwen3-235B-A22B-Instruct-2507
WRITER_MODEL=MiniMaxAI/MiniMax-M3
```


---

> **Category:** ⚙️ Infrastructure · **Parent:** [[Home]]
