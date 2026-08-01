# Orchestrator — Architecture

> **Role:** The mentor-layer reasoning loop that sits above all stateless sub-agents.
> Reads memory, decides what the user needs, dispatches to the right agent,
> merges results back into memory, and returns a response.

---

## Position in the system

```
User (chat / scheduler trigger)
        │
        ▼
  run_orchestrator_turn()          ← single entry point
        │
        ├── reads   PostgreSQL (3-tier memory)
        ├── decides (LLM reasoning core)
        ├── builds  AgentTask (sealed envelope)
        ├── calls   sub-agent via registry
        ├── writes  PostgreSQL (memory_delta + conversation turn)
        └── returns response_text to caller
```

The orchestrator is the **only component that touches the database**. Sub-agents never read or write Postgres directly — they receive a `MemorySlice` and return a `memory_delta`.

---

## Graph topology

```
[START]
    │
    ▼
intake_node              ← inject user_input, reset per-turn fields
    │
    ▼
summarize_node           ← DB READ: profile + last 14 days → compact summary_text
    │
    ▼
reason_node              ← LLM: what does the user need? (ReasoningDecision)
    │
    ├─ needs_long_term_context=True
    │       ▼
    │  long_term_recall_node   ← pgvector semantic search on Tier 2/3 memories
    │       │
    │       └──────────────────┐
    │                          │
    ├─ action="clarify"        ▼
    │       clarify_node       dispatch_node
    │           │                    │
    ├─ action="nudge"                ▼
    │       nudge_node     context_builder_node  ← DB READ: targeted profile slice → AgentTask
    │           │                    │
    │           │                    ▼
    │           │          agent_executor_node   ← calls sub-agent via registry
    │           │                    │
    │           │                    ▼
    │           │          memory_merger_node    ← DB WRITE: memory_delta + episodic event
    │           │                    │
    └───────────┴────────────────────┤
                                     ▼
                           format_output_node    ← build response_text + log conversation turn
                                     │
                                   [END]
```

---

## Node descriptions

| Node | DB? | LLM? | Responsibility |
|---|---|---|---|
| `intake_node` | — | — | Injects `user_input`, bumps `turn_count`, resets stale fields |
| `summarize_node` | READ | — | Loads profile facts + recent 25 events → `summary_text` block |
| `reason_node` | — | ✅ | Structured LLM output → `ReasoningDecision` (action + routing + flags) |
| `long_term_recall_node` | READ | — | pgvector cosine search → appends "RELEVANT OLDER MEMORIES" to `summary_text` |
| `clarify_node` | — | — | Extracts `clarifying_question` from decision, sets `response_text` |
| `nudge_node` | — | — | Extracts `proactive_message` from decision, sets `response_text` |
| `dispatch_node` | — | — | Resolves `agent_name` + `task_type` from decision (keyword fallback if LLM left it blank) |
| `context_builder_node` | READ | — | Targeted DB read per-agent schema → assembles `AgentTask` |
| `agent_executor_node` | — | ✅ (sub-agent) | Looks up agent in registry, calls `spec.run(task)` |
| `memory_merger_node` | WRITE | — | Merges `memory_delta` into profile facts, writes `agent_run` episodic event |
| `format_output_node` | WRITE | — | Builds `response_text`, logs `conversation_turn` event |

---

## The three conditional paths from `reason_node`

| Condition | Path |
|---|---|
| `needs_long_term_context=True` | `→ long_term_recall_node → dispatch_node → ...` |
| `action="ask_clarifying_question"` | `→ clarify_node → format_output_node → END` |
| `action="proactive_nudge"` | `→ nudge_node → format_output_node → END` |
| `action="route"` (default) | `→ dispatch_node → ... → END` |

---

## Memory tiers used per node

| Tier | What | Access nodes |
|---|---|---|
| **Tier 0** — Working memory | Current session dict | All nodes (in-process, not persisted) |
| **Tier 1** — Recent episodic | Last 14 days, full detail | `summarize_node` |
| **Tier 2/3** — Warm/cold | Consolidated summaries, 2+ weeks old | `long_term_recall_node` (conditional) |
| **Tier 4** — Profile facts | Stable structured data | `summarize_node`, `context_builder_node` |

---

## Sub-agent registry (`registry.py`)

The orchestrator does not import sub-agents directly. It looks them up via `get_agent_spec(agent_name)` which returns an `AgentSpec(name, run, task_types)`. Adding a new agent = one entry in the registry dict.

Currently registered agents:

| `agent_name` | Entry point | Task types |
|---|---|---|
| `daily_planner` | `run_daily_planner` | `build_daily_plan` |
| `learning_monitor` | `run_learning_monitor` | `log_learning_session` |
| `linkedin_writer` | `run_linkedin_writer` | `write_linkedin_post` |
| `fallback` | inline handler | `direct_response` |

---

## Key design rules

1. **Only the orchestrator touches Postgres.** Sub-agents are stateless — they receive a `MemorySlice` and return a `memory_delta`.
2. **Long-term recall is conditional, not automatic.** `long_term_recall_node` only runs when `reason_node` sets `needs_long_term_context=True`. Routine turns (plan my day, log learning) skip it entirely.
3. **Conversation turns are always logged.** `format_output_node` calls `log_conversation_turn()` at the end of every turn so the consolidation pipeline has real content to summarise.
4. **Agents never fail silently.** `agent_executor_node` wraps all sub-agent calls in try/except; a failed agent produces an `AgentResult(status=FAILED)` rather than an exception.

---

## Entry point

```python
from orchestrator.orchestrator import run_orchestrator_turn
from orchestrator.state import build_initial_state
from orchestrator.memory.store import MemoryManager

memory_manager = MemoryManager()
memory_manager.ensure_schema()

state = build_initial_state(memory_manager, session_id)
state = run_orchestrator_turn(state, "plan my day")
print(state["response_text"])
```

---

## Files in this directory

| File | Purpose |
|---|---|
| `orchestrator.py` | All node functions, `StateGraph` assembly, `app`, `run_orchestrator_turn()`, CLI demo |
| `state.py` | `OrchestratorState` TypedDict + `build_initial_state()` |
| `runner.py` | Thin backward-compat wrapper (`OrchestratorRunner` class) |
| `registry.py` | Agent registry — `get_agent_spec()`, `AgentSpec` |
| `config.py` | DB URL, embedding dimension, routing hints, agent context schemas, profile key map |
| `llm.py` | Shared `get_reasoning_llm()` factory |
| `__main__.py` | `python -m orchestrator` interactive CLI |
| `memory/` | Three-tier memory service (see `memory/architecture.md`) |
| `nodes/` | Pure helper functions used as graph node implementations |
