# Branches — two eras of one project

This repository holds **two complete systems** that share one idea: a local-first
mentor that actually knows the learner. They live on separate branches so that
neither has to pretend to be the other — and so every claim either one makes can
be checked against its own code.

| Branch | What it is | Runtime | Docs written for it |
|---|---|---|---|
| **`main`** | **v1 — the shipped system.** A LangGraph orchestrator over four registered agents, a 4-tier memory stack (LangGraph checkpointer → ChromaDB → PostgreSQL + pgvector → Obsidian topic graph), the Job Hunter resume pipeline (YAML master → LLM bullet selection → LaTeX → tectonic PDF, with a code-enforced metric guard and ATS keyword coverage), the 2-phase Goal Decomposer (NetworkX DAG + 4 content buckets + parallel MiniMax-M3 notes), a FastAPI backend and a Next.js dashboard. | Python (+ the TypeScript agent core in `mentor/`) | `README.md`, `docs/*` |
| **`pi-native`** | **v2 — the rewrite.** The entire Python layer removed. The mentor is a PI agent core that reads `data/` directly, and memory is kept by a **separate agent** — its own process, its own cheap model, its own `data/`-only write gate — that walks the session transcript forward from a cursor, with code-owned confidence ceilings and append-only supersede. | TypeScript only | `GOAL.md`, `MEMORY.md`, `AGENTS.md` |

## Why two branches and not one tree

The two systems have **opposite** architectural commitments, and a single tree
forces one of them to lie:

- **v1**: Python is the only writer; every store (Postgres, ChromaDB, the vault)
  is Python's, and TypeScript reaches it over a local FastAPI sidecar.
- **v2**: there is no Python and no sidecar — the store is plain files under
  `data/`, written by a second PI process, and the boundary is enforced by
  **disjoint write permissions** rather than by a service.

Documentation is the first casualty of mixing them: a hybrid tree ships
TypeScript code next to Python-era docs, and every "where does this live?" answer
becomes wrong. Keeping one era per branch means each branch's docs are true of
its own code by construction.

## Which branch am I looking at?

```bash
git branch --show-current          # main = v1, pi-native = v2 rewrite
```

`git log --oneline` is enough to tell them apart: `main` ends at *"snapshot:
complete mentor state before PI-native pruning"*, and `pi-native` continues past
it with the pruning, the file-backed memory, and the memory agent.

## Running each era

```bash
# v1 (main) — two processes: the sidecar owns the data, the agent core talks
uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
scripts/run_pi_mentor.sh -p "am I free at 4pm today?"
cd web && npm install && npm run dev        # the dashboard, proxying /api/*

# v2 (pi-native) — one process, no sidecar
git checkout pi-native
npm run mentor                              # interactive
npm test                                    # 84 checks: identity, safety, memory, planning
```

## Known state of each

- **v1 (`main`)** is feature-complete as shipped and is what the project's
  written-up resume bullets describe. It needs `uv sync`, a local PostgreSQL with
  pgvector, provider keys, and (for the Job Hunter PDF step) a `tectonic` binary.
  Its own honest gaps are listed under *Known gaps* in `README.md`.
- **`pi-native` (`v2`)** is mid-migration: the memory agent, the curator's
  `data/`-only gate, `remember` / `read_memory`, and the deterministic tools work;
  the 16 sidecar-bridged tools are **not yet ported to read `data/`**, so they
  report the memory service as unreachable rather than answering. `README.md` on
  that branch says so plainly.

## History, and why `main` is safe to read as v1

`main`'s tip is commit `49190bc`, a direct descendant of the initial commit, so
this is the project's real history — not a squash and not a rewrite. The v2 work
was kept by branching from `03e558d`, which is that branch's first parent and is
tagged `pre-v1-restore-03e558d` (pushed, so a fresh clone can see it too).
