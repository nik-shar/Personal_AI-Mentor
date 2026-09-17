# mentor/ — the mentor ecosystem on top of the PI agent harness

PI (`../pi/`, vendored upstream) is the **agent core**: sessions, tool loop,
compaction, skills, subagents, providers. This directory is everything that
makes it *the mentor*.

```
mentor/
├── extensions/
│   ├── mentor-identity.ts    # injects the constitution + persona (read from the repo root)
│   ├── guardrails.ts         # crisis (keywords + LLM classifier) / ship-mode / grounding
│   ├── provider-nebius.ts    # Nebius classifier (Qwen3-30B) + converser + writer tiers
│   ├── read-tools.ts         # Phase 1: 5 intent-shaped read tools
│   ├── specialist-tools.ts   # Phase 4: run_specialist — routes to the Python specialists
│   ├── write-tools.ts        # Phase 3: 5 sidecar writes + 2 NATIVE pure tools
│   └── permission-gate.ts    # blocks destructive shell + protected-path writes
├── skills/                   # GENERATED from toolkits/ — do not edit by hand
│   ├── learning-companion/   # SKILL.md + references/workflows/*.md (12 workflows)
│   ├── code-explorer/        # 6 workflows
│   ├── calendar-manager/     # 3 workflows
│   └── tutorial-writer/      # 4 workflows
├── src/
│   ├── identity.ts           # identity assembly — reads BOTH content files fresh
│   ├── specialist.ts         # run_specialist status contract + formatting (pure)
│   ├── pure/planning.ts      # NATIVE pure math (G3) — no sidecar hop
│   ├── disclosure.ts         # crisis/distress classifier: taxonomy, parsing, budget
│   ├── sidecar.ts            # the only door to the Python data layer (fail-open)
│   └── generated/
│       └── mentor-tools.ts   # AUTO-GENERATED tool contract — do not edit
├── test/
│   ├── identity.test.ts      # node --test: constitution present, no duplication, fail-open
│   ├── specialist-tools.test.ts  # node --test: status contract, transport fail-open, drift
│   ├── planning.test.ts      # node --test parity tests for the native math
│   └── disclosure.test.ts    # node --test: classifier taxonomy + fail-open contract
└── package.json              # pi package manifest + peerDependencies
```

## Where the mentor's identity comes from

Nothing in this directory holds the mentor's identity text. It is read **fresh on
every turn** from two runtime-neutral files at the repo root, which the Python
build reads as well:

| File | Contents | Python reader |
|---|---|---|
| `../mentor_agent_guidelines.md` | the constitution — §1 anchor facts, §2 standing orders, §3 router duties, §5 memory rules, §6 guardrails, §7 success criteria | `cognition/guidelines.py` |
| `../mentor_persona.md` | voice rules, hard rules, few-shot examples (`{name}` placeholder) | `cognition/persona.py` |

`src/identity.ts` parses both and assembles one `<mentor_identity>` block, which
`extensions/mentor-identity.ts` appends to the system prompt. Editing either file
takes effect on the next turn — no restart, no regenerated artifact, nothing to
keep in sync.

That is the point: the voice used to be written twice (as f-strings in
`cognition/persona.py` and again, hand-condensed, inline in the extension) and the
two drifted, while the constitution never reached this core at all — PI loads
`AGENTS.md` / `CLAUDE.md` as *project* context, and neither exists in this repo.
Both runtimes now read the same two files, so drift is structurally impossible.
`test/identity.test.ts` fails if the text is ever copied back into this directory.

§4 (per-agent guidelines) is deliberately **not** injected, only exposed through
`agentGuidelines(name)` — naming specialists this core cannot yet call would invite
the model to claim a delegation that does not exist. It returns with Phase 4.

A missing file degrades that part of the block and warns loudly at startup; it
never breaks a turn.

## Why some things are native and some are not

`write-tools.ts` shows both sides deliberately. `trim_plan_to_fit` and
`compute_learning_streak` are pure functions of their arguments, so they run in
TypeScript with no network hop. Everything that *writes* a store stays in Python
behind the sidecar. Tool names and schemas are identical either way, so moving a
tool across the boundary is a delete-a-`fetch` change (boundary rule 2).

One asymmetry worth knowing: `compute_learning_streak` (native) is *advice* — the
model reasoning about the arithmetic. The streak it should *quote* comes from
`log_learning_session` (sidecar), which is the one that actually persisted.

## Two rules this package exists to enforce

1. **TypeScript never writes a store.** Postgres, Chroma, and the Obsidian vault
   are written by Python only. Reads go through intent-shaped tools.
2. **The tool contract is generated, not hand-mirrored.** `api/tools.py` (pydantic)
   → `scripts/tools/export_mentor_tools_schema.py` → `src/generated/mentor-tools.ts`.
   The extension self-checks against it at session start and warns on drift.

Placement decisions for every feature, with the gate that decided each one, live
in [`docs/PI-Mentor Boundary.md`](../docs/PI-Mentor%20Boundary.md).

## Running it

```bash
# 1. Sidecar (the Python data layer) — required for the memory tools
uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

# 2. PI as the mentor
scripts/run_pi_mentor.sh -p "am I free at 4pm today?"   # one-shot
scripts/run_pi_mentor.sh                                # interactive TUI
scripts/run_pi_mentor.sh --list-models                  # prove extensions loaded
```

`run_pi_mentor.sh` loads `.env` (so `$NEBIUS_API_KEY` reaches the provider
extension), exports `MENTOR_SERVICE_URL`, and passes `-a` so project resources
load in non-interactive modes.

Regenerate the tool contract after changing any tool model:

```bash
uv run python scripts/tools/export_mentor_tools_schema.py
```

Regenerate the skills after editing anything under `toolkits/`:

```bash
uv run python scripts/tools/migrate_toolkits_to_skills.py
```

## Crisis detection

Two tiers, one decision, in `guardrails.ts`:

1. **Keywords** — instant, code-only, explicit language (`CRISIS_SIGNALS`).
2. **LLM classifier** — `src/disclosure.ts` on the fast `nebius-classifier` tier,
   ported from the Python reasoner's STEP 0 `disclosure_type`. Catches indirect
   distress ("I'm just so tired of all of this") that no keyword list reaches.

Either tier can raise crisis; neither can lower it. The classifier runs with a budget
(`PI_CRISIS_BUDGET_MS`, default 1500ms) that caps **waiting, not working** — a verdict
that lands late is still read at `message_end`, where the support pointer is enforced in
code. It fails open: a slow or broken classifier leaves the keyword backstop and the
always-on distress directive in place, and says so in the logs.

```bash
node --test mentor/test/*.test.ts     # 65 checks: identity + planning parity + classifier contract + safety wiring + specialist bridge

PI_CRISIS_CLASSIFIER=0   # keywords + baseline only
PI_CRISIS_BUDGET_MS=2500 # wait longer for the pre-generation verdict
PI_CRISIS_MODEL=nebius-classifier/Qwen/Qwen3-30B-A3B-Instruct-2507
PI_CRISIS_DEBUG=1        # log every verdict and its latency
```

## Specialist reach (Phase 4)

The specialist subagents are still **implemented in Python** — `goal_decomposer` writes the
Obsidian vault, `job_hunter` needs tectonic/pypdf, `linkedin_writer` reads the Chroma voice
store, so all three are G1/G2 and stay there permanently. What moved is *routing*: the model
decides when one is needed and calls `run_specialist`, which the sidecar runs through the
same three steps the old graph used (`build_task` → `spec.run` → `apply_memory_delta`).

That is what let the chat default move to this core. `orchestrator/registry.py` had exactly
one consumer — the graph's `agent_executor_node` — so without this tool the cut-over would
have silently removed roadmap, resume, and post work from chat.

```bash
# The specialist run is the one legitimately slow tool: goal decomposition
# generates tutorials in parallel, so it gets minutes, not the 15s read default.
MENTOR_SPECIALIST_TIMEOUT_MS=300000
```

## Known gaps

- The `burnout` / `crisis` boundary is a judgement call: `burnout` suppresses teaching
  but does not force a support pointer (the Python build drew the same line). See the
  boundary doc §10 for the one-line change if that should be tightened.
- No TypeScript typecheck runs over this package yet (jiti strips types at load).
- `toolkits.py` / `reasoner.py` are still live for the Python app; they retire at
  the surface cut-over, not before.
- No vault writer and no learning-log reader, so the `tutorial-writer` and
  `learning-companion` skills declare those as missing and say so honestly
  (see `SKILL_GAPS` in the migration script — the one place such claims live).
- **The calendar guard is per-function, not per-choke-point.** The overlap/anchor
  guards live in `place_time_block`, but four other paths create events directly
  via `MemoryManager.create_schedule_event` and therefore bypass them — including
  `save_daily_plan`, which is reachable by the model. See the boundary doc
  finding 12 for the recommended fix (guard at the choke point).