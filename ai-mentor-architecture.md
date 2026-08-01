# Personal AI Mentor — System Architecture Design Document

**Version:** 1.2
**Status:** Living document — updated with Multi-Agent Chaining, Nebius/MiniMax LLM Endpoints, and 4-Bucket Obsidian Goal Decomposer
**Scope:** Local-first personal AI mentor/assistant covering learning, daily planning, mindset & goals, and social media presence

---

## 1. Vision & Design Principles

The system acts as a persistent personal mentor — not a stateless chatbot. It should feel like
talking to someone who actually remembers your life, notices patterns, and occasionally reaches
out first instead of always waiting to be asked.

**Core principles:**

1. **One identity, many domains.** Every domain agent (planning, learning, mindset, social) draws
   from and updates the *same* underlying picture of who you are. No domain gets its own private,
   fragmented memory.
2. **Specificity over genericity.** No agent generates from vague prompts. The context assembler's
   job is to force concrete, specific inputs before generation ever happens.
3. **Proactive, not just reactive.** The system can initiate contact (check-ins, nudges, reflections)
   on a schedule, not only respond to messages.
4. **Local-first, privacy by default.** All personal data — memory, goals, reflections — is stored
   on your own machine. Nothing about your inner life leaves your device unless you explicitly
   choose to sync it.
5. **Earn trust in low-stakes domains before high-stakes ones.** Mindset/goals coaching carries
   real risk if done badly. Planning and learning tracking don't. Build and prove the foundation
   on the safe domains first.

---

## 2. High-Level Architecture

```
                 ┌────────────────────┐
                 │ Proactive Scheduler │
                 │ (time/event based)  │
                 └──────────┬──────────┘
                            │ synthetic "trigger" message
                            ▼
   User message ──────► Router/Reasoner ──────► Context Assembler ◄──────► Memory System
                                                    │                       (PostgreSQL / Obsidian)
                                                    ▼
                               ┌───────────────────────────────────────────┐
                               │           Multi-Agent Pipeline            │
                               │  Step 1: Goal Decomposer (Obsidian DAG)   │
                               │  Step 2: Daily Planner (6-8h Schedule)    │
                               └────────────────────┬──────────────────────┘
                                                    ▼
                                                Response
                                          (+ memory write-back)
```

Both the user and the scheduler enter through the **same LangGraph state graph**. When a multi-step task is requested (e.g. creating a multi-day learning roadmap and immediately scheduling Day 1), the Reasoner specifies an `agent_pipeline: ["goal_decomposer", "daily_planner"]`, which executes sequentially in a **single turn**.

---

## 3. Memory System

Four tiers, split by **role**, not by time.

### 3.1 Working memory
- The current conversation thread's live state.
- Implementation: LangGraph's built-in checkpointer (`SqliteSaver` / Postgres checkpointer).
- Ephemeral by design; gets summarized into episodic memory when a session ends.

### 3.2 Episodic memory
- Summaries of past sessions/events, retrieved by semantic similarity + recency scoring.
- Implementation: local vector store (Chroma) storing vectors on disk, with embeddings computed via cloud embeddings.

### 3.3 Semantic memory (the identity/profile layer)
- Compact, always-current picture of user profile: bio summary, goals, active learning paths, target roles, working habits, and procedural rules.
- Implementation: PostgreSQL relational tables (`profile`, `learning_log`, `activity_log`, `daily_plans`).

### 3.4 Topic Graph memory (Obsidian Vault)
- Multi-day pedagogical roadmaps and subtopic notes stored as markdown files with YAML frontmatter in `/home/nik/Documents/AI-Mentor/Learning/Topics/`.

---

## 4. Orchestration & LLM Tier

### 4.1 LLM Endpoint Architecture (`orchestrator/llm.py`)
- **Reasoner & Routing Model:** Nebius OpenAI-compatible endpoint running `meta-llama/Llama-3.3-70B-Instruct` (`https://api.tokenfactory.nebius.com/v1/`).
- **Writer & Deep Reasoning Model:** Nebius US-Central endpoint running `MiniMaxAI/MiniMax-M3` (`https://api.tokenfactory.us-central1.nebius.com/v1/`).

### 4.2 Multi-Agent Pipeline Chaining (`orchestrator/orchestrator.py`)
- **Graph State (`orchestrator/state.py`):**
  - `agent_pipeline: list[str]` (e.g. `["goal_decomposer", "daily_planner"]`).
  - `pipeline_step: int` (tracks current position in the chain).
  - `results: Annotated[list[AgentResult], add]` (accumulates output from each pipeline step).
- **Execution Loop:**
  `reason_node` ➔ `dispatch_node` ➔ `context_builder_node` ➔ `agent_executor_node` ➔ `memory_merger_node` ➔ `route_after_merger` ➔ `next_pipeline_agent_node` (loop until all agents complete) ➔ `format_output_node`.

---

## 5. Specialist Domain Agents

### 5.1 Daily Planner / Coach (`agents/Daily_Coach/`)
- Input: `available_minutes` (capped at max 8h / 480m per day), energy level (1-5), active goals.
- Output: Structured `DailyPlan` object (`items: list[PlanItem]`).
- Safety Cap: Overbudget inputs (>840 mins) are automatically capped to 8 hours max.

### 5.2 Goal Decomposer & Curriculum Architect (`agents/Goal_Decomposer/`)
- 2-Phase Generation Architecture:
  1. **Phase 1 (`llm_architect`):** Generates DAG topology (`GoalDecompositionOutline`) and classifies each subtopic into one of **4 Content Types**:
     - `"conceptual"`: Theory, architecture, mental models, trade-offs (No code required).
     - `"algorithmic"`: DSA, problem-solving, complexity analysis, approach breakdown, pseudocode/code.
     - `"hands_on_code"`: Practical tool/API/library tutorials with complete runnable code snippets.
     - `"reference"`: Quick lookup cheat-sheet, comparison table, canonical syntax.
  2. **Phase 2 (`deep_node_expander`):** Content-type-aware web search + deep tutorial note expansion using `MiniMaxAI/MiniMax-M3`. Writes markdown notes directly into Obsidian Vault (`Learning/Topics/`) and builds a master roadmap index file (`<Roadmap Title> Roadmap.md`).

#### Content Type Schemas (`agents/Goal_Decomposer/state.py`):
- `ConceptualContent`: `concept_overview`, `tradeoffs_and_comparisons`, `common_misconceptions`, `reflection_prompt`.
- `AlgorithmicContent`: `concept_overview`, `approach_breakdown`, `pseudocode_or_code`, `complexity_analysis`, `common_pitfalls`, `practice_problem`.
- `HandsOnCodeContent`: `concept_overview`, `code_example`, `common_pitfalls`, `hands_on_challenge`.
- `ReferenceContent`: `concept_overview`, `comparison_table`, `canonical_usage`, `common_pitfalls`.

---

## 6. Tech Stack Overview

| Component | Choice | Status / Endpoint |
|---|---|---|
| Orchestration | LangGraph | Active state graph with multi-agent pipeline looping |
| Reasoner LLM | Nebius `Llama-3.3-70B-Instruct` | `https://api.tokenfactory.nebius.com/v1/` |
| Deep Writer LLM | Nebius `MiniMaxAI/MiniMax-M3` | `https://api.tokenfactory.us-central1.nebius.com/v1/` |
| Working Memory | Postgres / SQLite Saver | Active turn persistence |
| Semantic Memory | PostgreSQL Store | Profile, active goals, daily plans, learning log |
| Topic Graph Storage | Obsidian Vault (`.md` + YAML) | `/home/nik/Documents/AI-Mentor/Learning/Topics/` |
| Graph Computation | `networkx` | In-memory DAG cycle check & critical path |
| Schemas | Pydantic v2 | Typed context & multi-content pedagogical models |

---

## 7. Status & Recent Milestones (v1.2)

- [x] Configured Nebius `Llama-3.3-70B` and `MiniMaxAI/MiniMax-M3` models in `orchestrator/llm.py`.
- [x] Fixed `LearningLogEntry` date coercion bug in memory schema.
- [x] Implemented Multi-Agent Pipeline Chaining in LangGraph orchestrator (`goal_decomposer` ➔ `daily_planner`).
- [x] Implemented 4-Bucket Content Classification (`conceptual`, `algorithmic`, `hands_on_code`, `reference`) in `Goal_Decomposer`.
- [x] Added dynamic heading formatting in `write_topic_node` (`orchestrator/memory/obsidian_graph.py`).
