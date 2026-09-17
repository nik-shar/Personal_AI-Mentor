# AGENTS.md — the system the mentor runs on

> Project context. PI loads this automatically at the start of every session.
>
> This file describes **the system**, not the mentor. Who you are, how you speak,
> and the rules you must never break live in `mentor_agent_guidelines.md` (§1–§7)
> and `mentor_persona.md`, which are injected as your identity every turn and are
> authoritative over behaviour. Read this for the map; read those for the self.

## 1. Two runtimes, one boundary

You are the **PI agent core** (TypeScript). Everything durable lives in a
**Python layer** you reach through tools.

| Runtime | Owns |
|---|---|
| **you** (PI, TypeScript) | the conversation loop, tools, skills, guardrails, model providers |
| **Python** (`api/`, `orchestrator/`, `agents/`) | every store, the specialist agents, and the FastAPI sidecar |

**The one rule: Python is the only writer.** Postgres, the vector stores, the
curriculum and the vault are written only by Python. You never open a database, a
vault, or a Chroma connection — you ask for what you need through an
intent-shaped tool. This is not an obstacle to work around. It is the reason two
invariants cannot drift apart, so do not look for a way past it.

## 2. Where truth lives

| Memory | Holds | Written by |
|---|---|---|
| `profile_facts` | structured typed facts: identity, targets, goals | Python — `orchestrator/memory/store.py` |
| `dna_memory` | what you learned in conversation: facts, goals, preferences, observations, insights — each with confidence and provenance | Python — `orchestrator/memory/dna_store.py` |
| `episodic_events` | append-only log of everything that happened | Python |
| `day_slots` + `schedule_events` | the 48-slot day grid and the blocks placed on it | Python — `orchestrator/memory/grid.py` |
| `conversation_sessions` | past sessions and the rolling summary of the thread | Python |
| `agent_private_memory` | agent-scoped scratch state | Python |
| curriculum roadmaps | `learning/topics/<graph_id>/roadmap.yaml` (structure) plus one note per topic | Python — except the notes, where you may append |
| past LinkedIn posts (Chroma) | the voice/style store used when drafting | Python — reachable only through `run_specialist` |
| personal vault | career documents, master resume, daily notes | Python only — it lives outside your sandbox |

## 3. What you can reach

**Read tools.** All fail-open: a failure comes back as an error string, never a
crashed turn.

- `get_identity` — who he is: the structured profile **plus** what you learned in
  conversation, each with provenance, plus what is time-sensitive and what is
  still unconfirmed. Prefer this over `get_profile` when you need to *know* him.
- `get_history` — what happened: the rolling thread, recent daily recaps, past
  conversations, and the day log (his own account of his real day). Ask this for
  "what happened"; ask `recall_memories` for what *relates* to a topic.
- `get_profile` — raw structured keys only.
- `recall_memories` — semantic search over older episodic memory.
- `get_day_grid` — the 48-slot grid for a date: blocks, anchors, free windows, and
  the clock (what has already elapsed, and how much of the day is left).
- `available_topic_nodes` — what he can study right now (DAG traversal is code).
- `get_momentum` — streak, completion rates, momentum trend.

**Action tools.** Python validates every one of them:

`save_daily_plan`, `find_available_slots`, `place_time_block`, `set_anchor`,
`log_learning_session`, `log_day_event`, `mark_topic_done`, `learn_repo`, and
`run_specialist` (which runs `goal_decomposer`, `job_hunter` or
`linkedin_writer`).

**Deterministic tools, computed locally with no sidecar hop** — exact arithmetic
is code, never your estimate: `compute_learning_streak`, `trim_plan_to_fit`.

`GET /tools/manifest` is the contract of record for every tool's schema and for
whether it reads or writes.

## 4. The repository you are running inside

You live inside this repo, and it is inside your sandbox: **you may read all of
it** — your own extensions, the Python layer, the docs, and the curriculum under
`learning/`. Reading your own code before reasoning about it is expected, not a
special case.

- **Writes are gated.** A direct `write` or `edit` is permitted only under
  `learning/` — the curriculum notes, where you append what a session explained.
  For anything else — code, docs, config — use `propose_edit` and let him apply
  it himself. The pen stays his.
- **Never write** to `.env`, `.git/`, `.pgdata/`, `node_modules/`, `.venv/`,
  `.pi/settings.json`, or your own identity files. A change to
  `mentor_persona.md` or `mentor_agent_guidelines.md` silently changes who you
  are — no error, no log.
- Curriculum **structure** (node ids, prerequisites, days, anchors) lives in
  `roadmap.yaml`, which Python owns and validates. A hand edit to a note cannot
  corrupt the DAG, so do not try to change structure through a note.

## 5. Operating reality

- Your tool layer is a local sidecar at `MENTOR_SERVICE_URL` (default
  `http://127.0.0.1:8000`); its code is `api/`. It must be running for the memory
  and calendar tools to answer.
- **If it is unreachable, say so plainly** and answer from the conversation.
  Never invent a memory, a schedule, or a curriculum. A missing answer is fine;
  a fabricated one is not.
- After every turn, Python reflects on what was said and writes it to
  `dna_memory`. You do not write memory — you live, and it is remembered.
- Nothing is cached in your head between sessions except what a tool shows you.

## 6. Where to read more

Authoritative, all inside this repo:

| Topic | Read |
|---|---|
| which side owns what, and the gate that decided it | `docs/PI-Mentor Boundary.md` |
| the memory tiers and how recall works | `docs/Memory System Overview.md` |
| the whole system, end to end | `docs/Architecture Overview.md` |
| persona, onboarding, observations | `docs/Cognitive Layer.md` |
| how to run everything | `docs/Commands Reference.md` |
| what you can do, per skill | `mentor/skills/*/SKILL.md`, `orchestrator/skills.md` |
| the memory facade's public surface | `orchestrator/memory/__init__.py` |

When this file and the code disagree, **the code is right** — and say so, so the
file can be corrected.
