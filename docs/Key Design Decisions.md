---
created: 2026-08-22
tags:
  - decisions
  - architecture
---

# 💡 Key Design Decisions

> **Role:** Architectural choices and the rationale behind them.

---

## 1. Stateless Agents, Stateful Orchestrator

**Decision:** Sub-agents never touch the database. The orchestrator reads memory, builds a `MemorySlice`, hands it to the agent, and merges the returned `memory_delta`.

**Why:** 
- Adding new agents requires no DB integration — just a new entry in the registry
- Memory consistency is enforced in one place (the `memory_merger_node`)
- Agents can be tested in isolation with mock slices
- Prevents fragmented writes from multiple sources

## 2. DNA Memory Over Schema-Heavy State

**Decision:** Replace the 23-column `mentor_user_state` table with organic natural-language memories (`dna_memory` table + pgvector).

**Why:**
- Don't pre-decide what matters about the user before meeting them
- Natural language captures nuance that columns can't (e.g., "Nik thrives on scope reduction, not pressure")
- Confidence lifecycle prevents hallucinated insights from persisting unchecked
- See [[DNA Memory System]] for full design

## 3. Two-Consumer Boundary

**Decision:** Structured data (energy level, employment status, skills) stays in `profile_facts` as typed values. Free-text understanding goes into DNA memory. Agents never receive free-text when they need typed values.

**Why:** Agents read values programmatically (`ENERGY_DEFAULT_MINUTES[energy_level]`, Pydantic coercion). Free-text memory cannot feed code paths. This boundary keeps agents deterministic and testable.

## 4. Three-Tier LLM Strategy

**Decision:** Use three model tiers rather than one:
- **Router** (`gpt-4o-mini`): Fast routing/classification
- **Converser** (`Qwen3-235B-A22B-Instruct`): High-quality responses
- **Writer** (`MiniMax-M3`): Deep content generation

**Why:** Cost and latency optimization. Not every turn needs a 235B model; routing decisions are simple classification tasks that a small model handles faster and cheaper.

## 5. Agents Draft, Never Execute

**Decision:** Agents return `DraftSuggestion` objects — they never auto-post to LinkedIn, never auto-send recruiter messages, never auto-write to the vault without confirmation.

**Why:** Reversibility. A drafted LinkedIn post that isn't published costs nothing. A published one that's wrong costs reputation. This applies to all external-facing outputs.

## 6. Constraints Enforced in Code, Not Just Prompts

**Decision:** `max_length`, `avoid_topics`, and metric integrity are checked programmatically, not left to LLM judgment.

**Why:** LLMs are unreliable at counting characters and enforcing negative constraints. The critic node in the LinkedIn writer checks character count programmatically and overrides an LLM "APPROVED" verdict if violated. The Job Hunter's metric guard verifies every number in tailored bullets exists verbatim in the source.

## 7. Long-Term Recall Is Conditional

**Decision:** `long_term_recall_node` only runs when `reason_node` sets `needs_long_term_context=True`.

**Why:** Routine turns ("plan my day", "log learning") don't need semantic search over months of history. Making recall conditional keeps routine turns fast (~3s vs ~8s).

## 8. Memory Is 4-Tier, Not Flat

**Decision:** Four tiers split by role:
- **Tier 0** — Working Memory (in-process, ephemeral)
- **Tier 1** — Episodic Events (append-only log, pgvector)
- **Tier 2/3** — Warm/Cold (consolidated summaries)
- **Tier 4** — Profile Facts (stable structured data)

**Why:** Different access patterns demand different storage. Working memory is fast and ephemeral. Episodic events support semantic search. Profile facts are typed and slow-changing.

## 9. Confidence Lifecycle for Inferred Knowledge

**Decision:** `mentor_inferred` memories start at 0.4 confidence, cap at 0.6 until user-confirmed, use sigmoid growth on confirmation.

**Why:** Prevent confirmation-bias loops where the LLM asserts its own prior inferences as new facts. User validation is the gate for psychological insights.

## 10. Multi-Agent Pipeline Chaining

**Decision:** Agents can be chained sequentially in a single turn via `agent_pipeline: list[str]` in state.

**Why:** Enables natural flows like "Teach me LangGraph and schedule Day 1" — the Goal Decomposer creates the DAG, then the Daily Planner schedules the first day, all in one conversational turn. Without this, the user would need two separate interactions.


---

> **Category:** 🏛️ Architecture · **Parent:** [[Architecture Overview]]
