# Branches — the project, and the experiment beside it

**`main` is the project.** The Python-based mentor: a LangGraph orchestrator over
four registered agents, the 4-tier memory stack, the Job Hunter resume pipeline,
the 2-phase Goal Decomposer, a FastAPI backend and the Next.js dashboard.
`README.md` and `docs/*` describe it, and they are true of this code.

**`pi-native` is an archived experiment.** A spike, kept for the record rather
than as a direction being continued: what happens if the Python layer is removed
and the mentor becomes a PI agent core that keeps memory in plain files. It is
**incomplete** — 16 of its tools still address a sidecar that no longer exists.

| | `main` — the project | `pi-native` — the experiment |
|---|---|---|
| Runtime | Python, plus the TypeScript agent core in `mentor/` | TypeScript only |
| Orchestration | LangGraph state graph, 4 registered agents | PI itself — skills and subagents |
| Memory | Postgres + pgvector, ChromaDB, Obsidian, LangGraph checkpointer | plain files under `data/`, curated by a second PI process |
| Docs that match it | `README.md`, `docs/*` | `GOAL.md`, `MEMORY.md`, its own `AGENTS.md` |
| State | feature-complete as shipped | incomplete: its 16 sidecar-bridged tools report the memory service as unreachable |

## Why the experiment lives on a branch and not in the tree

The two approaches hold **opposite** single-writer commitments, so one tree forces
one of them to lie:

- **`main`**: Python is the only writer; every store (Postgres, ChromaDB, the
  vault) belongs to it, and TypeScript reaches it over a local FastAPI sidecar.
- **the experiment**: there is no Python and no sidecar — the store is plain files
  under `data/`, and the boundary is enforced by disjoint write permissions
  rather than by a service.

Documentation is the first casualty of mixing them: a hybrid tree ships
TypeScript code next to Python-era docs, and every "where does this live?" answer
becomes wrong. One approach per branch means each branch's docs are true of its
own code by construction.

## What the experiment is worth reading for

Not its code — its ideas. Four are documented on that branch and are worth keeping
in mind for any future version:

1. **A cursor makes curation safe.** A missed run is caught by the next one,
   re-running is idempotent, and timing stops being critical.
2. **Confidence ceilings belong in code, not in a prompt.** A model told "cap
   inferred memories at 0.60" will eventually write 0.9, so the arithmetic lives
   in the store and refuses the write.
3. **Append-only with supersede, never edit.** A correction is a new line plus a
   pointer, so the store stays auditable.
4. **Disjoint write permissions are stronger separation than a second context
   window** — a second agent that cannot touch what it should not own.

## Which branch am I on?

```bash
git branch --show-current     # main = the project, pi-native = the experiment
```

`git log --oneline` tells them apart: `main`'s ancestry reaches `49190bc`
(*"snapshot: complete mentor state before PI-native pruning"*) directly, while
`pi-native` continues past `03e558d` with the pruning, the file-backed memory and
the memory agent.

## Running the project

```bash
# Two processes: the sidecar owns the data, the agent core owns the conversation.
uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
scripts/run_pi_mentor.sh -p "am I free at 4pm today?"
cd web && npm install && npm run dev      # the dashboard, proxying /api/*
```

Needs `uv sync`, a local PostgreSQL with pgvector, provider keys, and — for the Job
Hunter PDF step — the `tectonic` binary. The project's own honest limits are
listed under *Known gaps* in `README.md`.

## History

`main`'s tip sits on `49190bc`, a direct descendant of the initial commit
`e4cbb8b`: this is the project's real history, not a squash and not a rewrite. The
experiment was branched from `03e558d`, which remains its first parent, so the
commits in between are still in the repository and `git log` shows how one led to
the other. Nothing was discarded in either direction.
