---
created: 2026-08-22
tags:
  - schemas
  - contract
---

# 📐 Agent IO Contract

> **Role:** The sealed-envelope contract between the orchestrator and every stateless sub-agent — `AgentTask` in, `AgentResult` out.

---

## Design Principle

This contract does NOT change as you add agents. New agents get a new `task_type` string and, if needed, their own params/output sub-model — the envelope itself (`AgentTask → AgentResult`) stays fixed. This makes it safe to add agent #11 without touching agents #1-10.

## AgentTask (Orchestrator → Agent)

```python
class AgentTask(BaseModel):
    task_id: str                          # Unique ID for tracing
    agent_name: str                       # Target agent registry name
    task_type: str                        # e.g. "write_linkedin_post", "build_daily_plan"
    instructions: str                     # Natural language task description
    source: TaskSource                    # SCHEDULER | CHAT | OBSIDIAN_EVENT | ORCHESTRATOR_FOLLOWUP
    priority: TaskPriority = NORMAL       # LOW | NORMAL | HIGH
    created_at: datetime
    memory_slice: MemorySlice             # Trimmed context (see Memory Schemas)
    params: dict[str, Any]               # Agent-specific extra inputs
    parent_task_id: Optional[str]         # For chained agent causality tracing
```

### TaskSource

| Source | Meaning |
|--------|---------|
| `SCHEDULER` | Fired by the background scheduler |
| `CHAT` | Direct user message |
| `OBSIDIAN_EVENT` | Triggered by an Obsidian vault change |
| `ORCHESTRATOR_FOLLOWUP` | Output of one agent triggered another |

### TaskPriority

| Priority | When |
|----------|------|
| `HIGH` | User explicitly asked for this right now |
| `NORMAL` | Standard task |
| `LOW` | Background / opportunistic |

## AgentResult (Agent → Orchestrator)

```python
class AgentResult(BaseModel):
    task_id: str                          # Matches the AgentTask.task_id
    agent_name: str
    task_type: str
    status: ResultStatus                  # SUCCESS | PARTIAL | FAILED | NEEDS_CLARIFICATION
    completed_at: datetime
    output: str                           # Primary human-readable result
    memory_delta: dict[str, Any]          # Proposed changes to memory
    draft_suggestions: list[DraftSuggestion]  # Drafted artifacts (never auto-executed)
    confidence: Optional[float]           # 0-1 self-reported confidence
    error_message: Optional[str]          # Populated on FAILED/PARTIAL
    clarification_needed: Optional[str]   # Populated on NEEDS_CLARIFICATION
    extra: dict[str, Any]                 # Agent-specific data
```

### ResultStatus

| Status | Meaning |
|--------|---------|
| `SUCCESS` | Completed cleanly |
| `PARTIAL` | Completed with issues (e.g., revision cap hit) |
| `FAILED` | Error during execution |
| `NEEDS_CLARIFICATION` | Cannot proceed without more input |

## DraftSuggestion

```python
class DraftSuggestion(BaseModel):
    kind: str                             # "linkedin_post", "resume_edit", "obsidian_note"
    content: str                          # The actual drafted text
    suggested_destination: Optional[str]  # e.g., "Obsidian: Daily/2026-07-06.md"
    metadata: dict[str, Any]             # e.g., {"char_count": 612, "critic_approved": True}
```

Agents only draft — they never execute. A `DraftSuggestion` is always surfaced to the user for confirmation before any action.

## MemorySlice

The trimmed context sent to each agent. See [[Memory Schemas]] for full detail.

```python
class MemorySlice(BaseModel):
    agent_name: str
    task_type: str
    generated_at: datetime
    relevant_profile: dict[str, Any]      # Agent-specific profile fields
    recent_activity: list[ActivityLogEntry]
    private_memory: Optional[dict]
    constraints: dict[str, Any]           # max_length, avoid_topics, etc.
```

## Registry

All agents are registered in `orchestrator/registry.py`:

| Agent Name | Entry Point | Task Types |
|-----------|------------|------------|
| `linkedin_writer` | `run_linkedin_writer` | `write_linkedin_post` |
| `goal_decomposer` | `run_goal_decomposer` | `decompose_goal` |
| `job_hunter` | `run_job_hunter` | `tailor_resume`, `log_application`, `update_application_status`, `job_search_review`, `assess_fit`, `search_jobs` |
| `fallback` | inline lambda | `direct_response` |

## Memory Delta Shape Per Agent

| Agent | Common Keys |
|-------|------------|
| `linkedin_writer` | `last_linkedin_topic` |
| `goal_decomposer` | `topic_graphs` |
| `job_hunter` | `job_pipeline`, `applications` (episodic event) |


---

> **Category:** 📐 Schemas · **Parent:** [[Architecture Overview]]
