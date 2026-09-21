> **Archived — superseded by [nik-shar/steward](https://github.com/nik-shar/steward).**
> Steward is a smaller, cleaner restart of the same idea: personal memory with
> confidence ceilings, a consent gate before every write that touches the world, and
> capability-scoped subagents. It is Python-only and single-process, and it embeds the
> agent loop as Tau's harness (a library) rather than vendoring one.
> This repository is kept as a read-only record of the earlier approach; nothing here
> is being continued.

# 🧠 Personal AI Mentor — Local-First Multi-Agent Ecosystem

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Agent Core](https://img.shields.io/badge/Agent%20Core-Pi%20harness%20(TS)-black.svg)](pi/README.md)
[![Memory Layer](https://img.shields.io/badge/Memory%20Layer-PostgreSQL%20%2B%20pgvector-2ea44f.svg)](orchestrator/memory)
[![LLM Provider](https://img.shields.io/badge/LLM-Nebius%20(Qwen3--235B%20%7C%20MiniMax)-purple.svg)](https://nebius.com/)
[![Dashboard](https://img.shields.io/badge/Dashboard-Next.js-cyan.svg)](web/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **Branch note.** This branch is **the project.** A TypeScript-only experiment —
> the Python layer removed and memory moved to plain files — is kept on
> **`pi-native`** as a record of what was tried. It is incomplete and is not being
> continued; see [`BRANCHES.md`](BRANCHES.md).

An intelligent, local-first personal AI mentor. Unlike stateless chatbots, this system maintains long-term cognitive continuity across learning roadmaps, daily planning, skill growth, and technical content creation.

It runs on **two runtimes that share one boundary**:

| Runtime | Language | Owns |
|---|---|---|
| **PI agent core** — `mentor/`, on the vendored [`pi/`](pi/README.md) harness | TypeScript | the conversation loop, tools, skills, guardrails, model providers |
| **Memory & domain layer** — `orchestrator/`, `agents/`, `api/`, `scheduler/` | Python | every store (PostgreSQL + pgvector, ChromaDB, the Obsidian vault), the specialist agents, and the FastAPI sidecar |

**The rule that keeps it sane:** Python is the *only* writer of every store.
TypeScript reaches it through intent-shaped tools (`get_profile`, `place_time_block`)
over a local FastAPI sidecar. Whoever owns the store owns the validation, so an
invariant can never live in two places and drift. Every placement decision — and
the gate that decided it — is recorded in [`docs/PI-Mentor Boundary.md`](docs/PI-Mentor%20Boundary.md).

> **Status:** the surface cut-over has happened. The PI core answers `/api/chat` by
> default (15 tools — 13 sidecar-bridged + 2 deterministic local — and 4 generated
> skills); the Python graph is the escape hatch one env var away
> (`MENTOR_CHAT_ENGINE=python`) and still owns the scheduler, the specialists, and
> every store. See [Known gaps](#-known-gaps).

---

## 🌟 Key System Capabilities

- **🤖 Unified Multi-Agent Orchestration**: Built on LangGraph with state graph pipeline chaining plus **absorbed orchestrator skills** — daily planning and learning logging run directly in the mentor's harness with deterministic tools (budget trimming, streak math, DAG traversal), while `goal_decomposer` / `job_hunter` / `linkedin_writer` remain specialist agents.
- **🧠 4-Tier Hybrid Memory System**:
  1. *Working Memory*: LangGraph turn checkpointer (`SqliteSaver` / `PostgresSaver`).
  2. *Episodic Memory*: Local vector store (ChromaDB) with semantic similarity + recency retrieval.
  3. *Semantic Memory*: PostgreSQL relational tables tracking user identity, active learning paths, daily plans, and activity logs.
  4. *Topic Graph Memory*: Automated creation of markdown notes with YAML frontmatter and `[[wiki-links]]` inside an Obsidian Vault.
- **📚 2-Phase Pedagogical Goal Decomposer**:
  - Computes subtopic topology & DAG cycle validation using `networkx`.
  - Classifies learning nodes into 4 structured content buckets: `conceptual`, `algorithmic`, `hands_on_code`, and `reference`.
  - Synthesizes complete Markdown tutorial files in parallel using `MiniMaxAI/MiniMax-M3`.
- **⏱️ Energy-Aware Daily Planning (orchestrator skill)**: Generates optimized daily time blocks aligned with learner goals while programmatically enforcing safety budget caps (max 8 hours/day) — no separate agent, handled by the harness tool loop (`trim_plan_to_fit`, `save_daily_plan`).
- **✍️ Technical Content Architect**: Multi-stage RAG draft writer for technical posts with automated critic verification and strict programmatic rule enforcement.
- **💼 Job Application Specialist**: JD-tailored resume drafting on a 3-layer architecture (YAML master resume ➔ LLM bullet-selection by ID ➔ fixed LaTeX template + tectonic PDF) with a code-enforced metric-integrity guard, ATS keyword coverage reports, and an application pipeline tracker (stage timeline in episodic memory, stale-application detection).
- **🎒 Instruction-Set Toolkit Host**: The orchestrator is a universal host that loads pluggable markdown "toolkits" (`toolkits/<name>/` — philosophy + behaviors + workflows + guardrails) and activates one per turn as an overlay over the persistent mentor identity. Ships with four: `learning-companion` (Socratic teaching: question→hint→strategy ladder, prediction-first, hypothesis-driven debugging, educational review, and **spaced retrieval from real learning history**), `calendar-manager` (grid-grounded availability, justified placement, consent before any write), `code-explorer` (read-explore-diagnose grounded in real files, plus the teach-while-building `scaffold` lane), and `tutorial-writer` (research-first tutorial-note composition). Ship-mode override ("just give me the code") is code-enforced, and toolkits can never override crisis/continuity/honesty guardrails.
- **🔍 Code-Grounded Mentor (Shape-1)**: the mentor reads your actual code before reasoning about it — sandboxed `read_file`/`grep_search`/`git_log`/`git_diff`/`run_command` tools, a two-phase **deep reasoning** loop (investigate → verify → then answer), and a `code-explorer` toolkit with teach-while-building `scaffold` lanes (`learner_writes` / `joint` / `mentor_writes`). Every mentor-written piece ends in a transfer task, ownership (`owned`/`watched`) is remembered in DNA memory to pick next session's lane, and **no tool writes to disk** — the pen stays yours (propose-and-confirm).
- **🖥️ Web Dashboard & Interactive Graph Visualizer**: A Next.js dashboard (`web/`) proxying the FastAPI sidecar — streaming chat with pipeline tracing, an architecture **replay visualizer** (`/flow`), interactive Obsidian DAG topology graphs, the 48-slot day grid, the job-hunt board, and memory/profile inspectors.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Triggers
        A[User Web UI / Chat]
        B[Proactive Scheduler]
        C[API Requests]
    end

    subgraph Orchestrator ["LangGraph State Orchestrator"]
        D[Reasoner / Router Node] --> E[Context Assembler]
        E --> F[Agent Execution Pipeline]
        F --> G[Memory Merger & Delta Sync]
    end

    subgraph MemoryLayer ["4-Tier Memory System"]
        M1[(1. Working Memory: Checkpointer)]
        M2[(2. Episodic Memory: ChromaDB)]
        M3[(3. Semantic Memory: PostgreSQL)]
        M4[(4. Topic Graph: Obsidian Vault)]
    end

    subgraph SpecialistAgents ["Specialist Agents (Python)"]
        AG1[Goal Decomposer DAG]
        AG2[Job Hunter Tailoring]
        AG3[LinkedIn Content Writer]
    end

    subgraph HarnessSkills ["Absorbed Orchestrator Skills (in the mentor harness)"]
        H1[Daily Planning · Calendar Grid]
        H2[Learning Log · Streak Math]
    end

    Triggers --> D
    A --> H1
    A --> H2
    E <--> MemoryLayer
    F --> AG1
    F --> AG2
    F --> AG3
    H1 --> MemoryLayer
    H2 --> MemoryLayer
    G --> MemoryLayer
```

---

##  Running the Mentor (PI agent core)

Two processes: the sidecar owns the data, the agent core owns the conversation.

```bash
# 1. Sidecar — the Python data layer the tools read and write through
uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

# 2. PI as the mentor (loads .env, approves project resources, points at the sidecar)
scripts/run_pi_mentor.sh -p "am I free at 4pm today?"   # one-shot turn
scripts/run_pi_mentor.sh                                # interactive TUI
scripts/run_pi_mentor.sh --list-models                  # zero-token smoke test: did extensions load?
scripts/run_pi_mentor.sh --mode json -p "..."            # raw event stream (every tool call + payload)
```

Use `scripts/run_pi_mentor.sh`, **not** `pi/pi-test.sh` — the latter is PI's own dev
runner and does not load `.env`, so the provider key is empty and every call 401s.

If the sidecar is down the mentor still talks, but says plainly that it cannot reach
the memory service rather than inventing an answer.

---
## 🛠️ Tech Stack & Infrastructure

| Component | Technology | Role & Purpose |
|---|---|---|
| **Agent Core** | [Pi harness](pi/README.md) (vendored) | Conversation loop, tool calling, compaction, skills, subagents, model providers |
| **Mentor Package** | `mentor/` (TypeScript) | Identity overlay, 12 tools (10 sidecar-bridged + 2 local deterministic), guardrails, 4 generated skills |
| **Dashboard** | [Next.js](https://nextjs.org/) (App Router) | Chat, architecture replay (`/flow`), DAG graphs, day grid, job board — proxies `/api/*` to the sidecar |
| **Scheduler** | [APScheduler](https://apscheduler.readthedocs.io/) | Proactive triggers: memory consolidation and end-of-day summaries |
| **Orchestration** | [LangGraph](https://github.com/langchain-ai/langgraph) | Python-path state graph routing, context injection, and agent chaining |
| **Conversational Model** | `Qwen/Qwen3-235B-A22B-Instruct-2507` | Direct mentor responses, conversational reasoning, and context synthesis |
| **Deep Writer Model** | `MiniMaxAI/MiniMax-M3` | Comprehensive tutorial note synthesis and multi-step content expansion |
| **API Backend** | [FastAPI](https://fastapi.tiangolo.com/) | REST API endpoints, Swagger OpenAPI docs, and the mentor tool sidecar (`/tools/*`) |
| **Database** | PostgreSQL | Relational storage for learner profile, activity logs, and daily schedules |
| **Vector DB** | ChromaDB | Disk-persisted embeddings for semantic episodic memory |
| **Graph Computation** | NetworkX | In-memory DAG topological sorting and critical path calculations |
| **Vault Storage** | Obsidian Markdown | Native markdown node notes linked with YAML metadata |

---

## 📁 Repository Structure

```
├── agents/                      # Specialist agents (stateless, Python)
│   ├── Goal_Decomposer/         #   DAG curriculum generator + Obsidian note expander
│   ├── Job_Hunter/              #   JD-tailored resumes + application pipeline tracker
│   └── Linkedin_writer/         #   RAG draft writer + critic verifier
├── api/
│   ├── main.py                  # FastAPI server (/api/chat, /api/roadmaps, /api/health …)
│   └── tools.py                 # /tools/* — the 10 sidecar-bridged mentor tools + /manifest
├── mentor/                      # PI agent core (TypeScript)
│   ├── extensions/              #   identity, 12 tools, guardrails, Nebius provider
│   ├── skills/                  #   4 generated skills (codegen from toolkits/)
│   └── src/generated/           #   mentor-tools.ts (codegen from api/tools.py)
├── orchestrator/                # Python reasoning core
│   ├── orchestrator.py          #   LangGraph state graph + traced nodes
│   ├── harness.py               #   deterministic tools: budget trim, streak math, grid
│   ├── registry.py              #   agent registry (AgentSpec)
│   ├── tracing.py               #   LangSmith + local span capture
│   ├── toolkits.py              #   toolkit loader (cached, fail-open)
│   ├── nodes/                   #   reasoner, context_builder, memory_merger, synthesizer
│   ├── memory/                  #   Postgres, DNA store, Chroma, Obsidian vault, summaries
│   └── cognition/               #   persona, guidelines, onboarding, observations, metrics
├── schemas/                     # Pydantic contracts — agent_io.py, memory.py
├── integrations/                # search, job_search, messenger, obsidian_daily
├── scheduler/                   # APScheduler engine + consolidation/daily-summary jobs
├── toolkits/                    # Markdown instruction sets: calendar-manager, code-explorer,
│                                #   learning-companion, tutorial-writer
├── scripts/                     # Developer entry points (nothing in the app imports these)
│   ├── run_pi_mentor.sh         #   run the PI agent core as the mentor
│   ├── tests/                   #   38 verification suites (the regression net)
│   ├── tools/                   #   utilities + codegen (memory, exports, evals)
│   └── migrations/              #   one-shot data migrations (already applied)
├── web/                         # Next.js dashboard (proxies /api/* → FastAPI :8000)
├── docs/                        # Obsidian documentation vault — start at docs/Home.md
│   └── originals/               #   Original architecture doc & bug journal
├── learning/                    # The CURRICULUM root — learning/topics/<graph_id>/roadmap.yaml
│                                #   + one note per topic. Inside the repo on purpose: the
│                                #   mentor can read it when you ask a doubt mid-study
├── data/                        # Runtime artifacts: checkpoints, curated posts, backups
├── pi/                          # Vendored PI harness (gitignored — see note below)
├── .pi/settings.json            # PI provider + extension/skill registration
├── LICENSE                      # MIT
└── pyproject.toml               # Dependencies + ruff/pyright configuration
```

> **Not tracked in git:** `pi/` is a separate upstream repository
> ([`earendil-works/pi`](https://github.com/earendil-works/pi)) vendored in place, so it
> carries its own `.git` and is gitignored. Clone and build it once before running the
> mentor: `git clone https://github.com/earendil-works/pi.git pi && cd pi && npm install --ignore-scripts && npm run build`.
> `web/node_modules/`, `web/.next/`, `chroma_store/`, `data/checkpoints.sqlite`,
> `__pycache__/`, and `.ruff_cache/` are regenerable build/runtime output and are
> gitignored too. **Two vaults, split by purpose.** The repo root is an Obsidian
> vault (hence the local `.obsidian/`), and it now holds the **curriculum**:
> `learning/topics/<graph_id>/` with a `roadmap.yaml` manifest plus one note per
> topic. That one lives *inside* the repo deliberately — it is therefore inside the
> PI sandbox, so the mentor can read a note the moment you ask a doubt mid-study and
> append the explanation to it. Structure (ids, prerequisites, days, anchors) stays
> in the manifest, which Python owns and validates, so a hand edit to a note can
> never corrupt the DAG.
>
> **The personal vault is separate and stays out of git.** `OBSIDIAN_VAULT_PATH`
> (default `~/Documents/AI-Mentor`) holds career documents, the master resume, daily
> notes — private data, and Python is still its only writer. The two were briefly
> the same thing, which is how the curriculum ended up somewhere no tool could read
> it. See `docs/PI-Mentor Boundary.md` §5 for the register row.
>
> **Retired root docs:** `COMMANDS.md` and `CLAUDE.md` no longer sit at the repo root.
> The vault keeps their successors — [`docs/Commands Reference.md`](docs/Commands%20Reference.md)
> (CLI quick reference) and [`docs/CLAUDE Project Context.md`](docs/CLAUDE%20Project%20Context.md)
> (builder-facing context).

---

## 🚀 Quickstart Guide

### Prerequisites
- **Python 3.12+** (pinned in `.python-version`; `pyproject.toml` requires `>=3.12`)
- **uv** package manager (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Node.js 20+** — for the Next.js dashboard and to build the vendored PI harness
- **PostgreSQL** running locally, with the `pgvector` extension available

### 1. Environment Setup

Clone the repository and prepare your configuration:

```bash
git clone https://github.com/nik-shar/Personal_AI-Mentor.git
cd Personal_AI-Mentor

# Copy configuration template
cp .env.example .env
```

Edit `.env` with your Nebius API credentials and local PostgreSQL details:
```env
NEBIUS_API_KEY=your_nebius_api_key
DATABASE_URL=postgresql://nik:nik@localhost:5432/ai_companion
OBSIDIAN_VAULT_PATH=/path/to/your/Obsidian/Vault
```

### 2. Install Dependencies

```bash
uv sync                                                            # Python memory & domain layer

cd pi && npm install --ignore-scripts && npm run build && cd ..    # PI agent core (once)
cd web && npm install && cd ..                                     # Next.js dashboard
```

### 3. No Profile Setup Needed — Just Talk

There is no profile file to seed and no onboarding script to run. The mentor
learns who you are **through conversation**: every turn is reflected on and
stored as organic DNA memories (`orchestrator/memory/dna_store.py`) with a
confidence lifecycle. Early chats run in guided-discovery mode — the mentor
asks, listens, and remembers.

### 4. Run the Mentor Core (PI)

```bash
scripts/run_pi_mentor.sh -p "hey"     # one-shot turn
scripts/run_pi_mentor.sh              # interactive TUI
```

If the sidecar is down the mentor still talks — it says plainly that it cannot reach
the memory service rather than inventing an answer. The flags for raw event streams
and the zero-token smoke test are in **Running the Mentor (PI agent core)** above.

### 5. Run the Web Dashboard & API

Two processes: the sidecar owns the data, the dashboard renders it.

```bash
# Terminal 1 — backend (repo root): REST API + the mentor tool sidecar
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# Terminal 2 — dashboard
cd web && npm run dev
```

- **Dashboard:** [http://localhost:3000](http://localhost:3000)
- **API documentation (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)

### 6. Run Interactive Terminal CLI

```bash
uv run python -m orchestrator
```

---

## 🧪 Verification & Quality Validation

Run automated benchmark tests to verify pipeline functionality. All 32 suites live in
`scripts/tests/`, and each one prints its own pass/fail summary:

```bash
# Multi-agent pipeline, budget capping & deterministic harness tools
uv run python scripts/tests/test_orchestrator_harness.py

# Goal Decomposer quality & parallel node expansion
uv run python scripts/tests/test_goal_decomposer_quality.py

# 48-slot grid math, anchors, availability, and the overlap guard
uv run python scripts/tests/test_calendar_grid.py
```

⚠️ Several suites delete live data. Two are guarded — they refuse to run unless the
database name contains `test` or `DNA_TEST_ALLOW_WIPE=1` is set — but seven more are
**not yet guarded**. Read [`scripts/README.md`](scripts/README.md) before pointing any
of them at real memory.

---

##  Known gaps

Honest status, so nothing here reads as working when it isn't:

- **The constitution is a reconstruction, not the original wording.** The original
  `mentor_agent_guidelines.md` was never committed and was lost; the file now in the repo
  root was rebuilt from `docs/Mentor Constitution.md` on 2026-09-15. It loads — the path
  `orchestrator/config.py` resolves is exactly where the file lives — but §1 (anchor facts)
  and §2 (standing orders) are a faithful rebuild rather than his own words, and are the
  parts meant to be re-read and edited by hand.
- **Two suites can delete live memory.** `scripts/tests/test_dna_memory_store.py` and
  `scripts/tools/eval_dna_memory.py` wipe `dna_memory`; both now refuse to run unless the
  database name contains `test` or `DNA_TEST_ALLOW_WIPE=1` is set. Seven more suites delete
  narrower scopes of live data and are **not yet guarded** — see `scripts/README.md`.
- **Specialist agents are Python-only — but now reachable from the PI core.**
  `goal_decomposer`, `job_hunter`, and `linkedin_writer` stay in Python (vault writes,
  tectonic/pypdf, the Chroma voice store — all G1/G2), and PI reaches them through the
  `run_specialist` bridge tool (`api/tools.py` → `build_task` → `spec.run` →
  `apply_memory_delta`). Routing moved to the model (G4); execution did not move at all.
  Before this, moving the chat default to `pi` would have silently removed roadmap,
  resume, and post work from chat.
- **No composer for the vault writer on the PI side**, so `tutorial-writer` declares that
  gap explicitly rather than pretending.
- **Documentation drift — narrowed.** `docs/Home.md`'s index is repointed to the files
  that actually exist (`docs/Mentor Constitution.md`, `docs/DNA Memory Redesign v2.md`,
  `docs/CLAUDE Project Context.md`, `docs/Original System Architecture.md`,
  `docs/Original Bug Journal.md`, `mentor_persona.md`, `BRANCHES.md`), and
  `docs/Project Structure.md` already accounts for `web/` replacing the legacy `ui/`.
  What remains: the vault pages still describe the pre-PI Python-only layout (their
  diagrams have no agent core in them), and a few source comments (`api/main.py`,
  `mentor/extensions/*.ts`) cite retired docs.

---
## 📜 License

Distributed under the MIT License. See `LICENSE` for details.
