---
created: 2026-09-09
tags:
  - cognition
  - toolkits
  - orchestrator
---

# 🎒 Toolkit System (Instruction-Set Overlays)

> **Role:** The orchestrator is a universal host. It does NOT contain a teacher,
> psychologist, friend, etc. It loads **toolkits** — folder-per-role markdown
> instruction sets — and activates one per turn as an *overlay* over the
> persistent mentor identity.

---

## The Mental Model

```
Identity = relationship core (memory, phase, personality — ALWAYS ON)
         + role frame   (toolkit manifest — swapped)
         + behaviors    (toolkit philosophy/workflows/skills — swapped)
```

- The **base mentor** (persona, constitution, DNA memory, relationship phase,
  emotional checkpoint, guardrails) never changes.
- A **toolkit** adds situational *how-to-help* behavior on top. It modulates
  HOW the mentor speaks — never WHAT is true, never routing.
- Toolkits are pure markdown + frontmatter. Adding one requires **zero code
  changes** — just a new folder.

## Layout

```
toolkits/<name>/
├── toolkit.md            # manifest: YAML frontmatter {name, title, role, description}
├── philosophy.md         # core doctrine (applied while active)
├── behaviors.md          # always-on behavior rules while active
├── guardrails.md         # toolkit-specific boundaries
└── workflows/*.md        # one file per invocable workflow
```

Each workflow file starts with YAML frontmatter `description:` (used for the
workflow index) followed by the workflow body.

## How It Wires In

| Layer | Where | What happens |
|---|---|---|
| Loader | `orchestrator/toolkits.py` | `list_toolkits()` / `load_toolkit()` (cached, mtime-invalidated, fail-open) |
| Selection | `orchestrator/nodes/reasoner.py` | `ReasoningDecision.toolkit_mode` + `workflow` fields; **STEP 3.5** classifies learning turns |
| Guardrail | `apply_toolkit_guardrail()` in `reasoner.py` | ship-mode override, teacher backstop, direct-response-only, crisis/venting invalidation |
| Injection | `orchestrator/orchestrator.py` | `_build_direct_system_prompt(toolkit_mode, workflow)` injects the overlay; `direct_response_node` passes decision fields; `retrieve` workflow adds real memory material |

## Decision Flow

1. Reasoner classifies the turn (STEP 0 emotional → STEP 3 coaching → **STEP 3.5 toolkit** → STEP 4 route).
2. For learning turns it picks `toolkit_mode="learning-companion"` + a workflow; action stays `direct_response`.
3. `apply_toolkit_guardrail` (code) enforces:
   - `"ship this"` / `"just give me the code"` → clears toolkit (teaching exits)
   - `"teach me X"` / `"act as my teacher"` → forces `learning-companion` (backstop)
   - agent routing / crisis / venting / burnout → never carries a toolkit
4. `direct_response_node` builds the system prompt with the overlay and runs it.

## Toolkits in the repo

- **`learning-companion`** — the Socratic teaching overlay (Manware-adapted):
  philosophy (smallest intervention ladder: Question → Direction → Hint →
  Strategy → Pseudocode → Code; prediction-first; adaptive difficulty) +
  12 workflows (`hint`, `debug`, `explain`, `test`, `read`, `review`,
  `explore`, `api`, `arch`, `autopsy`, `retrieve`, `learn`) + 4 skills
  (examination, debugging, code review, retrieval) + guardrails.

  The **`retrieve`** workflow is the differentiator: it builds spaced-practice
  questions from Nik's **real learning history** (learning_log + tagged DNA
  memories + daily summaries) — something the stateless Manware toolkit
  cannot do.

## Adding a New Toolkit

1. `mkdir toolkits/<name>/` and add `toolkit.md` (manifest), `philosophy.md`,
   `behaviors.md`, `guardrails.md`, and a `workflows/` folder.
2. Restart the process (loader is mtime-aware — no restart strictly needed).
3. (Optional) add a `TOOLKIT_<NAME>_SWITCH_SIGNALS` entry to
   `apply_toolkit_guardrail` for explicit user-initiated switching.

## Code-Enforced Invariants (toolkits cannot override)

- **Crisis handling** — a psychologist-style toolkit can never cause diagnosis;
  the crisis guardrail + professional boundary stay hardware-enforced.
- **Continuity** — job-state changes still route to `job_hunter` regardless of
  active toolkit.
- **Honesty & grounding** — no toolkit can fabricate facts, deadlines, or
  urgency.
- **Direct-response-only** — toolkits never ride on agent routing.

## Verification

```bash
uv run python scripts/tests/test_toolkit_steering.py
```

51 checks: loader, renderer, workflow lookup, guardrail rules, reasoner schema,
prompt injection, and retrieval material assembly.

---
> **Category:** 🧠 Cognition · **Parent:** [[Cognitive Layer]]