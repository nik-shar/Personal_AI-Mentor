---
created: 2026-09-09
tags:
  - cognition
  - toolkits
  - code-explorer
  - scaffold
---

# 🔍 Code Explorer — Code-Grounded Mentor

> **Role:** the Shape-1 mentor — reads actual code, diagnoses with verification,
> and teaches while building. It never guesses what code does and never silently
> writes to disk.

---

## The problems it solves

Before this layer the mentor **could not read any of Nik's actual code** — its
only "outside" tool was web search. A question like "why is my orchestrator
erroring?" was answered by guessing. The `code-explorer` toolkit + the
code-grounded tool suite give the mentor eyes, ears, and a reasoning loop.

## Three pillars

### 1. Sandboxed code tools (the *eyes*) — `orchestrator/harness.py`

| Tool | Purpose | Safety |
|---|---|---|
| `read_file(path, start?, end?)` | read real files, line-bounded | path sandbox (`WORKSPACE_ROOTS`) |
| `grep_search(pattern, path?, n)` | find usages / definitions | sandboxed, capped |
| `list_directory(path?)` | repo structure | sandboxed |
| `git_status` / `git_log` / `git_diff` | what changed, when, why | read-only |
| `run_command(cmd)` | tests / lint / build | **allowlist only**, shell metachars refused |
| `propose_edit(...)` | propose a diff | **read-only** — never writes to disk |

No write-to-disk tool exists in v1. The human owns the pen (draft-and-confirm).

### 2. Deep reasoning loop (the *brain*) — `reasoning_depth`

`ReasoningDecision.reasoning_depth`: `quick | standard | deep`.

Deep turns run **two phases** in `direct_response_node`:
**Phase 1 (investigation):** read/grep/git/test — form a hypothesis, VERIFY it
(*never reason about code it hasn't read*).
**Phase 2 (synthesis):** the mentor-voiced answer grounded in what was verified.

The guardrail (`apply_toolkit_guardrail` rule 5) is code-enforced: code-turns
with an active toolkit are upgraded to `deep`; code-turns with no toolkit get
the `code-explorer` overlay forced.

### 3. Teach-While-Building (the *pedagogy*) — `scaffold` workflow

Inside `toolkits/code-explorer/workflows/scaffold.md` (6 workflows total:
`read-repo`, `diagnose-error`, `review-code`, `trace-flow`, `compare-versions`,
`scaffold`).

Three writing lanes chosen per-piece by learning value (`writing_split`):

- **`learner_writes`** — the core conceptual chunk: Nik writes, mentor reviews.
- **`joint`** — mentor skeleton → Nik fills → mentor reviews.
- **`mentor_writes`** — worked example / plumbing; **ALWAYS closes with a
  transfer task** ("now you do the analogous piece").

The transfer step is **non-negotiable** and written into the workflow: if the
mentor wrote, the learner writes next — same session. Ownership memory
(`owned` / `watched` tags via `dna_reflection`) feeds the next session's lane
choice ("last time you watched the rate-limiter — today you try it first").

## Decision flow (full loop)

1. Reasoner STEP 3.6 classifies the turn → sets `reasoning_depth`.
2. `apply_toolkit_guardrail` (code) enforces: code turn → `code-explorer` +
   `deep`; ship-mode / crisis / venting / agent-route invariants unchanged.
3. `direct_response_node` binds `CODE_TOOLS` when deep/code-explorer, runs the
   two-phase loop, and injects ownership history for `scaffold` turns.
4. Reflection records `owned`/`watched` memories → future scaffold lane choice.

## Wiring map

| Concern | Where |
|---|---|
| Tool surface + sandbox | `orchestrator/harness.py` (`CODE_TOOLS`) |
| Sandbox roots config | `orchestrator/config.py` (`WORKSPACE_ROOTS`, `MENTOR_WORKSPACE_ROOTS`) |
| Depth + lane fields | `orchestrator/nodes/reasoner.py` (`ReasoningDecision`) |
| Depth detection prompt | `orchestrator/nodes/reasoner.py` (STEP 3.6) |
| Code backstop guardrail | `orchestrator/nodes/reasoner.py` (`apply_toolkit_guardrail` rule 5) |
| Two-phase loop + tool binding | `orchestrator/orchestrator.py` (`direct_response_node`) |
| Toolkit + 6 workflows | `toolkits/code-explorer/` |
| Ownership memory | `orchestrator/memory/dna_reflection.py` (+ `RETRIEVAL_TAGS` in `toolkits.py`) |

## Verification

```bash
uv run python scripts/tests/test_code_explorer.py
```

41 checks: sandbox, read/grep/propose, command allowlist, guardrail backstops,
schema, toolkit rendering, prompt injection, ownership pipeline, decision
wiring. All existing suites (toolkit-steering 51, guidelines 22, harness,
first-contact 25, transcript 22) remain green.

---
> **Category:** 🔍 Code Grounding · **Parent:** [[Cognitive Layer]]