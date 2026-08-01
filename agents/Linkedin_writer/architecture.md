# LinkedIn Writer Agent — Architecture

> **Role:** Stateless sub-agent that drafts a LinkedIn post from scratch for any given topic.
> Invoked by the orchestrator when the user asks to write or improve a post.

---

## Why this is a separate agent

LinkedIn content writing is a creative, multi-step task with its own internal revision loop. It needs:
- A web search pass for current facts and trends
- A RAG pass against past posts to match voice and avoid repetition
- A draft–critic–revise loop to enforce quality and constraints

Mixing this into the orchestrator or another agent would couple an inherently iterative, latency-tolerant task with the low-latency routing layer. Keeping it separate means the revision loop can run as long as needed without stalling the main graph.

---

## Trigger / source

Invoked by the orchestrator when user intent maps to LinkedIn content:

- `"write a linkedin post on my Vectorless RAG project"`
- `"draft a post showing I can build production AI"`
- `"i want to post about finishing the LangGraph course"`

`task.source` will be `TaskSource.CHAT`. Future: scheduler could trigger a weekly post nudge with `source=SCHEDULER`.

---

## Inputs from the orchestrator (`AgentTask`)

```python
AgentTask(
    task_id="...",
    agent_name="linkedin_writer",
    task_type="write_linkedin_post",
    instructions="write a linkedin post on my Vectorless RAG project...",
    source=TaskSource.CHAT,
    memory_slice=MemorySlice(
        agent_name="linkedin_writer",
        task_type="write_linkedin_post",
        relevant_profile={
            "projects": [{"name": "Vectorless RAG", "status": "active",
                          "tech_stack": ["LangChain", "ChromaDB", "FastAPI"], ...}],
            "employment_status": "unemployed_job_searching",
            "target_roles": ["AI Engineer", "Data Scientist"],
            "preferences": {"content_tone": "concise, no fluff"},
            "last_linkedin_topic": "LangGraph state machines",
            "recent_posts": [...],           # last 5 drafted posts
            "high_importance_wins": [...],   # episodic wins worth referencing
        },
        private_memory={"post_history": [...], "avoided_topics": [...]},
        constraints={
            "max_length": 300,              # optional character cap
            "avoid_topics": ["traditional_ml"],  # semantic avoidance
        },
    ),
    params={
        "topic":    "Vectorless RAG project",
        "goal":     "show recruiters I can build production AI",
        "audience": "AI/ML recruiters",
    },
)
```

### `relevant_profile` fields used

| Field | Why |
|---|---|
| `projects` | Source of ground-truth facts (tech stack, repo, status) — prevents hallucination |
| `employment_status` / `target_roles` | Calibrate tone and emphasis (job-search mode vs. thought leadership) |
| `preferences.content_tone` | Standing voice preference (e.g. "concise, no fluff") |
| `last_linkedin_topic` | Avoid repeating the most recent post topic |
| `recent_posts` | RAG source to match writing voice |
| `high_importance_wins` | Facts worth referencing for credibility |
| `private_memory` | Agent's own history of past topics and avoided phrases |

### `constraints` the agent enforces

| Key | Enforcement |
|---|---|
| `max_length` | Checked **programmatically** in `critic_node` — LLMs are unreliable at counting characters |
| `avoid_topics` | Checked **semantically** (LLM critic prompt) + **lexically** (substring fallback) |

---

## Internal LangGraph nodes

```
[START]
    │
    ▼
input_brief ──────────────────────────────────────────────────┐
    │                                                          │
    ├── web_search_node (TavilySearch → distillation LLM)     │
    │                                                          │
    └── voice_style_node (Chroma RAG, top-3 past posts)       │
              │                           │                    │
              └──────────────┬────────────┘                    │
                             ▼                                 │
                    draft_writer_node  ◄───────────────────────┘
                             │
                             ▼
                        critic_node
                             │
                    ┌────────┴────────┐
                 "revise"          "done"
                    │                 │
                    ▼                 ▼
          draft_writer_node    format_output_node
          (max 3 revisions)          │
                                   [END]
```

| Node | Responsibility |
|---|---|
| `input_brief` | Extracts `topic`, `goal`, `audience` from instructions — LLM-assisted if not in params |
| `web_search_node` | Calls TavilySearch for current facts, then a distillation LLM call strips noise into 3-5 attributable bullet points |
| `voice_style_node` | Queries Chroma vectorstore (past posts) for the 3 most topically similar examples, merges with static style guide |
| `draft_writer_node` | Writes (or revises) the post; on revisions, critic feedback is injected as a dedicated second system message |
| `critic_node` | LLM editor reviews draft; programmatic checks enforce `max_length` and `avoid_topics` regardless of LLM verdict |
| `format_output_node` | NFKC unicode normalisation + strip, then packages into `AgentResult` |

### Revision loop cap

`MAX_REVISIONS = 3`. If the critic has not approved after 3 rewrites, the last draft is accepted and the critic's final feedback is included in the result's `error_message` field for transparency.

---

## Tools used

| Tool | Used in | Purpose |
|---|---|---|
| `TavilySearch(max_results=3)` | `web_search_node` | Current facts and trends for the topic |
| Chroma vectorstore | `voice_style_node` | RAG over past LinkedIn posts for voice matching |
| LLM (Nemotron via Nebius) | Every node except voice_style | Extraction, distillation, drafting, critiquing |

---

## Output (`AgentResult`)

```python
AgentResult(
    task_id="...",
    agent_name="linkedin_writer",
    task_type="write_linkedin_post",
    status=ResultStatus.SUCCESS,
    output="Drafted a LinkedIn post about: Vectorless RAG project",
    memory_delta={
        "last_linkedin_topic": "Vectorless RAG project",
    },
    draft_suggestions=[
        DraftSuggestion(
            kind="linkedin_post",
            content="I built something I didn't think was worth sharing...",
            metadata={
                "char_count":      612,
                "revision_count":  1,
                "critic_approved": True,
                "human_reviewed":  False,
            },
        )
    ],
)
```

### `memory_delta` shape

```json
{
  "last_linkedin_topic": "Vectorless RAG project"
}
```

The orchestrator merges this into `profile_facts` so future turns know the last post topic.

---

## RAG setup (`RAG.py`)

The Chroma vectorstore is built from a local collection of your past LinkedIn posts. It is loaded at module import time (not per-request) to avoid rebuild latency:

```python
_vectorstore = _load_or_build_vectorstore()
```

If the store does not exist yet, it is built on first import from the source documents in `chroma_store/`. Adding new posts requires rebuilding the store.

---

## What this agent does NOT do

- Does **not** post to LinkedIn — it only produces a `DraftSuggestion`. Publishing is a deliberate manual step.
- Does **not** read or write the DB — only the orchestrator's `context_builder_node` and `memory_merger_node` touch Postgres.
- Does **not** keep state between turns — it is fully stateless; every call is independent.
- Does **not** decide when to post — that is the orchestrator's scheduling concern.

---

## Files in this directory

| File | Purpose |
|---|---|
| `linkedin_writer.py` | All nodes, graph assembly, `run_linkedin_writer()` entry point |
| `State.py` | `LinkedInWriterState` TypedDict + `build_initial_state()` + `build_result()` |
| `RAG.py` | Chroma vectorstore loader for voice-matching past posts |
| `__init__.py` | Package init, exports `run_linkedin_writer` |
