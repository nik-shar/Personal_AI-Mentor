---
created: 2026-09-14
tags:
  - architecture
  - pi
  - boundary
  - reference
---

# 🧭 PI–Mentor Boundary (Draft)

> **Role:** Where each mentor feature lives, now that the **PI agent harness is
> the core** and the Python mentor stack is the data/domain layer underneath it.
> This is a decision *register*: every placement names the gate that decided it,
> so moving a feature is a deliberate re-decision rather than drift.
>
> **Status:** Draft. Built from the Phase 0/1 prototype (identity overlay, Nebius
> provider, permission gate, and the five read bridge tools). Rows marked
> **open** are intentionally undecided until the prototype produces evidence.

---

## 1. The gates

Applied in order. The first gate that fires wins — the ordering is what makes
mixed placement predictable instead of arbitrary.

| Gate | Test | Verdict |
|------|------|---------|
| **G1 — Single writer** | Does it *write* Postgres, Chroma, or the Obsidian vault? | **Python** (hard) |
| **G2 — Python-only dep** | Needs torch embeddings (384-dim pgvector), `psycopg2`/pgvector, `networkx`, ChromaDB, tectonic/LaTeX, `pypdf`? | **Python** (hard) |
| **G3 — Pure function** | Output depends only on its arguments? | **TypeScript** |
| **G4 — Prompt / policy / hook** | Identity text, guardrail, routing policy, event reaction? | **TypeScript** |
| **G5 — Large-data aggregate** | Streak over N days, weekly pattern mining, consolidation? | **Python** |
| **G6 — Hot path** | Runs unconditionally every turn? | **TypeScript**, unless G1/G2 forbids |
| **G7 — Batch / autonomous** | Weekly jobs, proactive triggers, long-running work? | **Python** |
| **G8 — Uncertain** | Can't justify a verdict yet | Place behind a **narrow intent tool**; revisit |

---

## 2. Boundary rules (non-negotiable)

1. **Sole-writer.** Python is the only writer of Postgres, Chroma, and the
   vault — permanently. TypeScript reads through tools and never opens a
   driver. This one rule removes the dual-writer corruption class *and* the
   dual-embedding hazard: whoever computes embeddings owns vector search, and
   two embedding implementations degrade recall **silently**.
2. **The tool contract is the seam.** A tool's name and schema do not change
   when its implementation moves sides. Moving `get_momentum` to TypeScript
   later means deleting a `fetch` and adding a local function.
3. **Intent-shaped tools, never CRUD.** `recall_memories(query)` — not
   `GET /profile_facts?key=`. CRUD-shaped tools get sprinkled across extensions
   and become unportable.
4. **One schema source of truth, generated not hand-mirrored.** The pydantic
   models in `api/tools.py` are authoritative. `scripts/tools/export_mentor_tools_schema.py`
   generates `mentor/src/generated/mentor-tools.ts`; the extension self-checks
   against it at session start and warns on drift.

---

## 3. Fail-open is part of the contract

Every sidecar handler returns a structured `error` field instead of raising, and
the TypeScript client returns `{ ok: false, error }` instead of throwing. A
broken tool degrades the advisor's answer; it never crashes a turn. This
preserves the property `orchestrator/harness.py` guaranteed ("nothing here can
crash a turn").

---

## 4. Implemented in the prototype

| Tool | Gate | Why Python |
|------|------|-----------|
| `get_profile` | G1 | Reads `profile_facts`; Python owns the table |
| `recall_memories` | G2 | Postgres + the `all-MiniLM-L6-v2` 384-dim embedding model |
| `get_day_grid` | G5 | 48-slot aggregate over `day_slots` |
| `available_topic_nodes` | G1 | DAG traversal + node progress writes |
| `get_momentum` | G5 | Aggregate over `schedule_events`, computed fresh |

Realised as:

| Layer | Artifact |
|-------|----------|
| Sidecar surface | `api/tools.py` (`/tools/*`, read-only, fail-open) |
| Contract generator | `scripts/tools/export_mentor_tools_schema.py` |
| Bridge tools | `mentor/extensions/read-tools.ts` |
| Transport | `mentor/src/sidecar.ts` |
| Identity | `mentor/extensions/mentor-identity.ts` |
| Provider | `mentor/extensions/provider-nebius.ts` |
| Safety | `mentor/extensions/permission-gate.ts` |
| Project config | `.pi/settings.json` |
| Runner | `scripts/run_pi_mentor.sh` |

---

## 5. Findings from the first live turns

Two things the prototype surfaced that no amount of design would have caught:

1. **The model invents optional arguments.** Asked "am I free at 4pm today?",
   Qwen3-235B called `get_day_grid` with `{"date":"2024-05-22"}` — a
   training-era date it fabricated rather than omitting the argument. The bridge
   passed it through faithfully, so the mentor reported the wrong day. Fixed by
   injecting an authoritative `CURRENT DATE` block in the identity overlay plus
   an explicit "omit the date for today" guideline. Verified: the next live turn
   sent `{}` and reported the correct date. **Lesson:** ground every
   time-relative fact in the prompt; never rely on a model leaving an optional
   argument empty.
2. **`recall_memories` often reports "nothing found" on a small corpus.**
   `MemoryRetriever.recall()` discards results with cosine distance ≥ 0.5.
   Against the current 26 episodic events the closest match is ~0.65, so recall
   returns `found: false` even with `hot_threshold_days=0`. This is the
   retriever's pre-existing threshold behaviour, relayed faithfully — not a
   bridge defect. Worth revisiting as a memory-quality question (threshold, or
   embedding choice), separately from this migration.
3. **Skills discovery works, and the model picks the right one.** Asked to list
   its skills, the model returned exactly `calendar-manager`, `code-explorer`,
   `learning-companion`, `tutorial-writer`, and chose `code-explorer` for "help
   me debug a traceback in my repo". The `description` field is doing real work
   here — it is the trigger, which is why `USE_WHEN` in the migration script is
   human-written and coverage-checked.
4. **`message_end` replacement reaches headless callers — verified, not assumed.**
   The crisis footer depends on `MessageEndEventResult.message` actually
   mutating the outbound message. A disposable probe extension
   (`.probe-message-end.ts`, since deleted) appended a marker and it appeared in
   `message_end`, `turn_end`, `agent_end`, **and** `-p` print-mode stdout. This
   matters because if that mechanism silently stopped working, the code-enforced
   safety net would vanish with no error — the worst possible failure mode.
   A measurement note for future debugging: the marker does *not* appear in the
   streamed `text_delta` events, so accumulating deltas is the wrong way to
   check a `message_end` mutation.
5. **PI fails loudly on a broken extension.** During this phase an undefined
   identifier in `mentor-identity.ts` made PI refuse to start with
   `Failed to load extension ... IDENTITY_OPEN is not defined` rather than
   running with a half-loaded mentor. That is worth relying on:
   `scripts/run_pi_mentor.sh --list-models` is a fast, zero-token smoke test for
   the whole extension set.
6. **The write path exposed a real bug in the existing overlap guard — the most
   valuable thing this phase produced.** `place_time_block`'s docstring
   (`orchestrator/harness.py:332-335`) promises "overlap with existing task
   blocks" is a code-owned check. It is not. The occupancy test at
   `harness.py:372` reads
   `if state not in (None, "", "free", "task")` — it *allows* `"task"`, and
   `reflect_schedule_on_day` (`memory/store.py:753`) sets exactly `state="task"`
   for every booked slot. So a second block can be placed on top of a first.

   Verified over the bridge: booking `DSA drill` 09:00–10:00 succeeded, then
   `Clash` 09:30–10:00 was **accepted** (`status: ok`) on the same slots. Your
   own `toolkits/calendar-manager/guardrails.md` says "Never silently overwrite
   an existing task block; propose a move instead", so the intent is documented
   and the code does not implement it.

   **FIXED.** `place_time_block` now rejects occupied slots with an actionable
   message (`slot 08:00 already holds a task block (event cdf9528a) — never
   overwrite an existing block; propose a move instead`), and the fallback
   occupancy test no longer accepts `"task"`. Four regression checks were added to
   `scripts/tests/test_calendar_grid.py` section [7] and the suite passes (21 checks):
   exact overlap rejected; partial overlap rejected (the realistic "move it 30 min
   later" case); rejection creates no event; and **a non-overlapping block on the
   same day is still allowed** — the guard rejects overlaps, not all placements.
   `test_orchestrator_harness.py` and `test_calendar_awareness.py` (23 checks) were
   re-run afterwards to prove nothing else moved.

   See finding 12 — the fix closed the tool it was reported against, not the class.
7. **`set_anchor` has a dead parameter.** `set_anchor(mm, date, start_time, ...)`
   never reads `date` — it writes slots using `start_time`'s own date
   (`harness.py:398-430`). The sidecar passes the derived date so the call site
   stays honest, and notes it in the docstring. Left as-is: removing a parameter
   is an API change, not a migration task.
8. **Invariants that DID survive the bridge** (verified over HTTP, throwaway date
   2099-01-01, all test rows deleted afterwards):
   - anchor guard: placing on a `meal` anchor → `anchor conflict: slot 12:30 is 'meal' (lunch) — never place a task over a life anchor`
   - slot-range sanity: 23:45 + 60 min → `block runs past end of day (slot 47, need 2)`
   - 30-minute alignment: 18:17 + 45 min → booked 18:00–19:00
   - `save_daily_plan` round-trip: wrote, then restored the prior
     `todays_plan` / `daily_plans` values and confirmed restoration equal to the
     snapshot. No test data left behind.
9. **A verification lesson worth keeping: "restore" must distinguish absent from
   null.** My first `save_daily_plan` test snapshotted two profile facts that did
   not exist (`None`), then wrote them back — which *created* two rows holding
   JSONB `null` instead of removing nothing. The equality check passed
   (`str(None) == str(None)`) while a side effect had occurred, and the count went
   from 6 to 8 facts. Two further subtleties: SQLAlchemy stores Python `None` as
   **JSONB `null`**, so `value is null` matches 0 rows — it has to be
   `value = 'null'::jsonb`; and the `save_daily_plan` call also wrote an episodic
   event (26 → 27). All of it was found and cleaned by counting rows before and
   after, which is why a write-path test must always assert on *counts*, never
   only on returned values. Final state verified back to baseline: 6 profile
   facts, 26 episodic events, 0 schedule events, 4 DNA memories.
10. **`get_day_grid` permanently materialises 48 grid rows per date it is read
    for.** Reading the grid for 2024-05-22 — the date the model hallucinated in
    finding 1 — left 48 slot rows behind (cleaned up). This is normal grid
    behaviour (`ensure_day_slots` lazily creates the day), but it means a
    hallucinated date has a *cost*: a stray day of slot rows until the
    `prune_old_day_slots` job ages it out. Another argument for grounding the
   date in the prompt rather than trusting the model.
11. **Stale capability claims made the mentor deny its own abilities — and the
    root cause was the design, not the wording.** Asked "do you have access to my
    calendar?", the mentor answered **no** while holding five calendar tools. Cause:
    in Phase 2 I hand-wrote *"Booking is not yet possible on the PI side … say you
    cannot write it yet"* into two places — the calendar guardrail directive
    (`guardrails.ts`, injected on every turn containing the word "calendar") and
    the generated `calendar-manager` skill. Phase 3 added `place_time_block` /
    `set_anchor` / `find_available_slots` and I updated the docs but not those two
    strings, so the system prompt was actively instructing the model to deny the
    capability.

    This is finding 6's failure mode in different clothes: **a claim about the
    system that no longer matches the system.** The fix is structural, not
    textual. Skill tool lists are now *generated* from the sidecar's pydantic
    manifest (`SKILL_TOOLS` + `manifest_descriptions()` in
    `scripts/tools/migrate_toolkits_to_skills.py`), and `validate_skill_tools()` fails the
    build if a skill names a tool that does not exist — verified by injecting a
    fake name, which the build refused. Only genuine *gaps* stay hand-written, in
    one auditable place (`SKILL_GAPS`).

    Verified after the fix: *"Yes, I can both read your calendar and add or move
    blocks on it… but only with your agreement."* With no tool call — a capability
    question is answered from the prompt, which is exactly why a wrong sentence
    there was so damaging.

    **Lesson:** capability statements are a contract, not prose. If a sentence
    enumerates what the system can do, generate it from the thing that knows.
12. **The calendar guard is per-function, not per-choke-point — four bypasses
    remain.** `create_schedule_event` is the real single write choke point and it
    validates nothing, so guarding only `place_time_block` closes one door:

    | Path | Reachable by |
    |---|---|
    | `harness.py:1118` — `save_daily_plan` mirrors items carrying `scheduled_time` | **the mentor LLM** (it is a registered tool) |
    | `api/main.py:461` — `POST /api/schedule` | the web board UI |
    | `orchestrator/orchestrator.py:344` — proposed-action execution | the Python orchestrator's consent flow |
    | `scripts/test_*.py` | tests only |

    So the mentor could still double-book by calling `save_daily_plan` with
    overlapping `scheduled_time` values.

    **Recommended fix (not applied):** move slot validation into
    `MemoryManager.create_schedule_event`, with an explicit
    `allow_overlap: bool = False` escape hatch so the board UI can still perform a
    deliberate move. That is finding 11's lesson applied to validation instead of
    prose — enforce at the thing that knows, not at each caller. It is a broader
    behaviour change (it would reject overlapping UI writes too), so it belongs in
    its own pass with its own regression suite.

---

## 6. Open decisions (the point of prototyping)

| Feature | Candidate gates | Evidence needed before deciding |
|---|---|---|
| `trim_plan_to_fit`, `compute_learning_streak`, `format_duration`, slot clock math | G3 → TypeScript | Measure the IPC cost on a real planning turn; these are pure functions |
| `search_jobs` (`integrations/job_search.py`) | G5 or G8 | Is it actually stateful, or just multi-provider HTTP? |
| `integrations/search.py` (Tavily/DDG) | G3/G6 → TypeScript | Plain fetch; only the fallback logic is non-trivial |
| `messenger.py` (Slack/Telegram) | G3 → TypeScript | Stateless HTTP POST |
| `topic_graph.py` DAG traversal | G2 (networkx) vs G3 (a ~40-line TS walk) | Is `get_available_topic_nodes` truly coupled to the store, or only to the graph shape? |
| `reasoner.py` routing + toolkit guardrail | **Done (Phase 2)** | Guardrail half ported to `mentor/extensions/guardrails.ts`; the routing half waits for Phase 4 subagents |
| **Identity + constitution injection** | **Done (Phase 2)** | G4 → TypeScript, but the *text* is runtime-neutral. `mentor/src/identity.ts` reads `mentor_agent_guidelines.md` (§1–§7) and `mentor_persona.md` fresh every turn; `cognition/guidelines.py` + `cognition/persona.py` read the same two files. The voice used to exist twice (Python f-strings + a hand-condensed inline copy in the extension) and drifted; the constitution never reached the core at all. See finding 17 |
| `cognition/persona.py` | **Not retired — re-scoped** | It stays as the Python *reader* of `mentor_persona.md` (same public API). The content moved out of code because content shared by two runtimes cannot live in either one's source |
| `toolkits/*` instruction sets | **Done (Phase 2)** | Generated into `mentor/skills/`; `toolkits/` stays the editable source |
| Write intents | **Done (Phase 3)** | 5 sidecar-backed writes + 2 native pure tools; see the Phase 3 table above |
| **Missing overlap guard in `place_time_block`** | **Fixed** | Found and fixed in this migration (finding 6). Guard + 4 regression checks in `scripts/tests/test_calendar_grid.py` |
| **Stale capability claims** | **Fixed structurally** | Finding 11. Tool availability in skills is now generated from the manifest; `validate_skill_tools()` fails the build on drift |
| Plan/edit vault writes (`write_tutorial` → vault) | G1/G2 → Python | Needed before the `tutorial-writer` skill can stop saying "I cannot file that" |
| Learning-log reader | G5 → Python | Needed before `learning-companion`'s `retrieve` workflow can use real history instead of `recall_memories` fallback |
| Crisis detection quality | G4 → TypeScript | **Done (Phase 2)** — the disclosure classifier is ported as `mentor/src/disclosure.ts` + `guardrails.ts`, on a fast `nebius-classifier` tier. Two tiers (keywords + LLM verdict), budgeted, fail-open; neither can lower a crisis once raised. See §10 and findings 15–16 |
| `mentor/` has no TypeScript typecheck | — | jiti strips types at load, so nothing catches type drift today. Add a tsconfig + `tsgo --noEmit` before the package grows |
| Python `toolkits.py` / `reasoner.py` retirement | — | Blocked on the surface cut-over: `api/main.py` still serves the old path |
| `observations.py`, `consolidator.py`, `daily_summary.py` | G5/G7 → Python | Aggregates over stored history + writes DNA memories |
| Goal Decomposer LLM half | G4 (subagent) vs G1/G2 (vault writes are Python) | The vault-writing half cannot move; the outline half might |
| LinkedIn critic loop | G4 | Chroma voice store stays Python (G2); only the critique loop could move |
| Working memory / checkpointer | G6 → TypeScript (done) | PI sessions replace the LangGraph checkpointer |
| **Web chat surface** (`/api/chat`, `/api/session/end`) | **Done — default flipped** | `MENTOR_CHAT_ENGINE` now defaults to `pi` (`DEFAULT_CHAT_ENGINE`); `python` remains one env var away as the escape hatch. Python stays the only writer: the per-turn write-back and the session transcript mirror both go through Python. See §12 |
| **Specialist reach from the core** (`run_specialist`) | **Done (Phase 4 routing)** | The routing half of `reasoner.py` moved to the model (G4); execution stayed in Python (G1/G2). Required before the default could flip — `registry.py`'s only consumer was the graph, so without it the flip silently removed roadmap/resume/post work. See §12 |
| **The roadmap store** (`orchestrator/memory/roadmap.py`) | **G1/G5 → Python**, with the notes handed to the sandbox | One manifest (`roadmap.yaml`) per roadmap is the authoritative structure: identity, prerequisites, status, anchors, provenance. Python owns and validates it. The **notes** are a git-tracked tree inside the repo — deliberately inside `WORKSPACE_ROOTS` — so the PI core reads them with its normal file tools and appends in-session explanations. That is a *reclassification, not a bypass*: the invariant-bearing state (structure) never leaves Python, and the notes are no longer where structure is parsed from, so a hand edit cannot corrupt the DAG. See §14 |
| **Curriculum status mutator** (`mark_topic_done`) | **G1 → Python** | `update_node_status` had exactly one caller, inside the Python graph, so on the default engine **no node could ever be marked done** — all 22 live nodes sat at `not_started` and the prerequisite frontier could never advance. The unlock report is computed in Python (`topic_graph.get_available_nodes`), never by the model, because the DAG is code's business |
| **`available_topic_nodes` source** | **Fixed (G1/G5)** | It read `profile_facts["topic_graphs"]` — a key nothing ever wrote — so the tool answered **zero** nodes while the curriculum held **five** unlocked ones. It now reads the manifests. The same fix went into `harness.assemble_situation_facts` and `context_builder`'s virtual key, so the PI path and the Python path finally agree on where a topic graph lives |
| **Direct writes outside the curriculum** | **Blocked (G4)** | `permission-gate.ts` now protects the identity files (`mentor_persona.md`, `mentor_agent_guidelines.md`), `.pi/settings.json` and the code directories, and permits direct `write`/`edit` **only** under `learning/`. Everything else must go through `propose_edit`. PI ships no permission system (see its README), and the mentor runs headless — the dashboard, the scheduler — where nobody is present to approve, so this list is the only guard there is |

---

## 7. Anti-patterns to refuse

- ❌ "Just this once" TypeScript read of Postgres — it becomes permanent.
- ❌ Computing embeddings on both sides — recall degrades *silently*; everything
  looks fine while results get subtly wrong.
- ❌ Duplicating a rule for speed that a gate already placed — two divergent
  implementations of a safety invariant.
- ❌ Porting to TypeScript because it is "nicer" without a gate firing.
- ❌ Letting a tool's schema change when its implementation moves sides.
- ❌ Prompts or tools that need the current date, streak, or availability without
  a tool call — the live-turn finding above is what that failure looks like.

---

## 8. How to move a feature between sides

1. Confirm a gate fires for the new side (record it in this table).
2. Keep the tool name and schema byte-identical.
3. Replace the implementation: delete the `callTool(...)` call, add a local
   module — or the reverse.
4. Re-run `scripts/tools/export_mentor_tools_schema.py` if the contract changed at all.
5. Delete the Python endpoint only once nothing else calls it.
6. Update this register, including *why* the gate changed.

---

## 9. Cut-over log — web chat on the PI core (Phase 1)

Shipped behind `MENTOR_CHAT_ENGINE` (`pi` | `python`, default `python`).

| Layer | Artifact |
|-------|----------|
| Transport | `orchestrator/pi_bridge.py` — `pi --mode rpc` as a child process, one per web session, respawn-safe because PI persists the session JSONL |
| Surface switch | `api/main.py` → `_chat_engine()` / `_chat_turn_pi()` |
| Per-turn write-back | `PiSession._record_turn` — mirrors `_log_turn` plus the reflection dispatch: episodic turn log, Obsidian daily note, DNA reflection |
| Session mirror | `PiSession.persist_transcript` → the existing `close_active_session` hook, so `conversation_sessions` is written exactly as the Python path writes it |
| Contract | `ChatResponse` is unchanged. `trace` is synthesised from the PI event stream (`agent_start → turn → tool_execution_* → message_end → agent_settled`), and the bridge appends its own `memory_writeback` node so `/flow` shows the write-back step rather than hiding it |
| Regression net | `scripts/tests/test_pi_bridge.py` (30 checks) — framing, `agent_settled` semantics, authoritative text, trace contract; no PI process and no LLM call required |
| Specialist reach | `run_specialist` in `api/tools.py`, registered by `mentor/extensions/specialist-tools.ts`, logic in `src/specialist.ts`, per-call timeout in `src/sidecar.ts`. `scripts/tests/test_specialist_bridge.py` (25) + `mentor/test/specialist-tools.test.ts` (10) |
| Default engine | `DEFAULT_CHAT_ENGINE = "pi"` in `api/main.py`; `MENTOR_CHAT_ENGINE=python` restores the old path with no redeploy |

Findings from the first live turns — both were **silent** failures, which is why they
are recorded here rather than in a commit message. (Numbering continues §5.)

13. **Environment precedence matters more than environment contents.** The bridge
    merges `.env` *over* `os.environ`, matching `set -a; source .env` in
    `run_pi_mentor.sh`. The first version let an already-exported variable win, and a
    stale `NEBIUS_API_KEY` in the shell shadowed the real one: every turn returned
    `401 status code (no body)` with an empty assistant message. The credential looked
    present — right name, right length — so nothing pointed at the environment. Rule
    that came out of it: **.env wins, exactly as bash does it.**
14. **PI reports a provider failure as an assistant message, not an exception.** The
    message carries `stopReason: "error"`, `content: []`, and `errorMessage`. The
    first bridge version treated any settled turn as success, so the API answered
    `200` with a blank reply — "no silent failure" broken at the seam. The bridge now
    surfaces the provider error as a failed turn, and treats "settled with no text"
    as a defect worth reporting instead of rendering an empty bubble.

Verified in this pass: a tool round-trip survives the move end-to-end. Asked whether
he was free at 4pm, the mentor called `get_day_grid` with `{}` (no fabricated date),
used the result in the answer, and the transcript landed in Postgres at session end
(`turns_saved: 2`). Both engines answer the same `ChatResponse` contract, so the
switch is reversible per process.

---

## 10. Crisis detection ported to the PI core (Phase 2)

The Python build classified disclosures inside its *reasoning* call, so classification
cost nothing extra. PI has no reasoning schema, so it became its own call — and that
changed the design space enough to be worth writing down.

| Layer | Artifact |
|-------|----------|
| Taxonomy, parsing, budget | `mentor/src/disclosure.ts` — pure logic with an injected `ModelCall` |
| Decision, directives, footer | `mentor/extensions/guardrails.ts` — one file owns the crisis path, so there is one place to audit |
| Model | `nebius-classifier` tier (`provider-nebius.ts`) — `Qwen/Qwen3-30B-A3B-Instruct-2507`, resolved through the registry rather than named twice |
| Tests | `mentor/test/disclosure.test.ts` (24 — taxonomy, parsing, fail-open) and `mentor/test/guardrails.test.ts` (13 — the wiring, incl. the pointer guarantee). No provider and no network: `node --test mentor/test/*.test.ts` |
| Knobs | `PI_CRISIS_CLASSIFIER=0` (keywords + baseline only), `PI_CRISIS_BUDGET_MS`, `PI_CRISIS_MODEL`, `PI_CRISIS_DEBUG=1` |

The rules between the tiers:

1. **Either tier can raise crisis; neither can lower it.** Keywords are instant; the
   classifier catches what keywords cannot see.
2. The classifier's budget caps **waiting, not working** (finding 15).
3. `venting` / `burnout` suppress teaching and planning but do **not** trigger the
   support pointer. That is the Python build's boundary and it is kept deliberately —
   the always-on baseline handles tone there, and in testing it did so well.

15. **A safety budget must stop the waiting, not the work.** The first classifier
    cancelled its own model call when the budget expired — which discarded precisely
    the verdict `message_end` needs in order to enforce a support pointer on a message
    no keyword reaches. Measured against the live endpoint: ~0.9s for a benign turn,
    up to ~2.8s under load, against a 1.2s budget. Expiry now means "no verdict yet":
    the call keeps running, `guardrails.ts` waits `withinBudget()` before generation
    and reads `final()` at `message_end`. The fast path stays fast and a late verdict
    still counts.
16. **The baseline carried the turn while the classifier was unavailable — which is
    the point of having both.** In the first live probes the classifier timed out and
    the mentor still answered indirect distress warmly, with no plan and with a
    support pointer, purely from the always-on distress directive. The hierarchy is
    working as designed: the LLM verdict widens coverage, the directive is the floor,
    and the code-enforced footer is the guarantee. One layer timing out never means
    the safety net disappeared.

Known boundary, stated rather than hidden: the `burnout` / `crisis` line is a
judgement call. A message like "I am just so tired of all of this" classified as
`burnout` suppresses teaching but does not force the pointer — the model supplied one
anyway, unprompted, because of the baseline. If that should be tightened (pointer on
`burnout` too), it is a one-line change in `guardrails.ts`; it is not changed here
without deciding that a support pointer on every exhausted-sounding message is wanted.

---

## 11. Identity single-sourcing (Phase 2 follow-through)

Finding 5 established that PI fails loudly on a broken extension, and §7 forbids
"duplicating a rule for speed that a gate already placed". The identity layer broke
both rules in the quietest possible way: it was duplicated, and nothing failed.

**What was wrong.**

| Copy | Read by | Problem |
|---|---|---|
| `cognition/persona.py` (f-strings) | the Python graph | the original |
| `extensions/mentor-identity.ts` (condensed, inline) | the PI core | hand-copied; drifted |
| `mentor_agent_guidelines.md` (§1–§7) | the Python graph **only** | **never reached the PI core** |

PI loads `AGENTS.md` / `CLAUDE.md` as project context (`core/resource-loader.ts`), and
neither exists in this repo. So the extension had been asking for the constitution in a
comment since Phase 1 — *"Phase 2 replaces the inline text below with … the constitution
from `mentor_agent_guidelines.md`"* — and it never happened. The PI mentor ran with no
anchor facts, no standing orders and no guardrails section, while the Python path had all
three and the comment implied parity. It was invisible because both paths still *talked*
like a mentor: the framing was ported, the constitution was not.

**The rule this produced (17): identity text shared by two runtimes is content, not code,
so it lives in a file neither runtime owns.** `mentor_persona.md` was extracted from
`persona.py`, and both runtimes now read both files fresh:

| File | Python reader | TypeScript reader |
|---|---|---|
| `mentor_agent_guidelines.md` | `cognition/guidelines.py` | `mentor/src/identity.ts` |
| `mentor_persona.md` | `cognition/persona.py` | `mentor/src/identity.ts` |

Consequences worth keeping:

1. **The seam is parsing, not text.** Both readers use the same `## N. Title` convention
   and the same strip-then-substitute-then-join contract, so they inject byte-identical
   strings. Verified, not assumed: the TypeScript render was diffed against the
   pre-extraction Python output at 755 / 734 / 2991 / 4507 characters.
2. **`persona.py` is re-scoped, not retired.** It keeps its public API, so the Python
   path's prompts are unchanged — also proven byte-identical to the pre-change output.
   Deleting it would mean the Python side stops reading the shared file.
3. **§4 is excluded from the PI overlay on purpose**, exposed only as
   `agentGuidelines(name)`. Injecting the per-agent section while the core cannot call
   those agents would invite the model to claim a delegation that does not exist — the
   stale-capability failure of finding 11, in prompt form.
4. **A missing file fails loudly at extension load, not silently at turn time.** The
   extension warns with the path and the resolved repo root. Fail-open still holds (the
   block shrinks, the turn runs), but the degradation is visible the moment PI starts.
5. **The UTC date is deliberate.** It matches the sidecar's own default for an omitted
   date (`api/tools.py` → `datetime.now(timezone.utc).date()`), so the injected "today"
   and the tool's "today" cannot disagree.

Verified end to end: `--list-models` loads the extension set clean, and a live turn
answered "what do you know about who I am?" with a paraphrase of §1 (IIT Roorkee,
LLM training and evaluation, AI Engineer search, MS in Robotics) — content that existed
only in the constitution file and was unreachable from this core before the change.

Two failure modes surfaced while writing the tests, both silent, both worth recording:

18. **`path.dirname` over a URL produces plausible-looking garbage.** The first
    `identity.ts` resolved the repo root as `resolve(dirname(import.meta.url), "..", "..")`
    — missing the `fileURLToPath` step — and got
    `/home/nik/Desktop/AI/file:/home/nik/Desktop/AI`, because `path` collapsed the
    `file:///` prefix to a single slash and then treated it as a relative segment. Nothing
    threw; the identity block just came back empty and the mentor would have run voiceless.
19. **An empty wrapper dresses a failed read up as a real block.** With the content file
    missing, `build_persona_block()` still emitted `<examples></examples>` — non-empty
    output from empty input, which any downstream `if block:` guard accepts. Both runtimes
    now wrap only when there is something to wrap: identical output on the normal path,
    honest on the broken one.

---

## 12. Surface cut-over — `pi` is the default

`MENTOR_CHAT_ENGINE` now defaults to `pi` (`DEFAULT_CHAT_ENGINE` in `api/main.py`).
The Python graph is retained as an escape hatch, not as a co-equal path.

**Why the flip needed `run_specialist` first.** `orchestrator/registry.py` had exactly one
consumer — `agent_executor_node` in the graph. So before the bridge tool existed, moving
the default to PI would have removed three shipped capabilities from chat: roadmap
generation, resume tailoring, and LinkedIn drafting. The flip and the bridge are therefore
one change, not two — which also means "Phase 4" was never really a separate phase: the
routing half of `reasoner.py` had to land before the graph could leave the serving path.

**What the flip does, and does not, do.**

| | Status |
|---|---|
| `/api/chat` | PI by default (bridge → `orchestrator/pi_bridge.py`) |
| `/api/session/end` | PI branch (already existed, unchanged) |
| Reversibility | one env var; both engines answer the same `ChatResponse` |
| Python's ownership | unchanged — sole writer of Postgres, Chroma, and the vault |

**What is NOT deleted, and why.** Deleting the reasoning loop now would break things that
still legitimately depend on it:

| Still depends on `orchestrator.py` | Why it cannot go yet |
|---|---|
| `orchestrator/pi_bridge.py` | imports `close_active_session` — the PI path itself uses it to mirror the session transcript. Must move (to `memory/store.py` or a session module) first |
| `scheduler/engine.py` | autonomous wake-up turns still run through `OrchestratorRunner`; needs a bridge call instead |
| `orchestrator/__main__.py` | the CLI chat loop (`python -m orchestrator`) |
| 8 suites in `scripts/tests/` | `test_orchestrator_flow`, `test_checkpointing`, `test_mentor_cognition`, `test_job_hunter`, `test_code_explorer`, `test_calendar_awareness`, `test_toolkit_steering`, `test_proactive_scheduler` |

So "retired" here means **demoted from the serving path** — which is the part that decides
whether the mentor runs on PI. The deletion is a follow-on with its own gates, in the order
§8 prescribes: move `close_active_session`, migrate the scheduler, migrate or retire each
suite, then delete — and re-run `scripts/tools/export_mentor_tools_schema.py` if any
contract moved.

Two operational findings from wiring the bridge:

20. **A read timeout applied to a specialist run guarantees failure.** `sidecar.ts` gave
    every call 15s — correct for a memory read, catastrophic for goal decomposition, which
    generates tutorials in parallel and legitimately runs for minutes (that is why
    `PI_TURN_TIMEOUT_SECONDS` is 420). `callTool` now takes a per-call override and
    `run_specialist` uses 300s, deliberately under the bridge's bound so the bridge owns
    the outer limit. The timeout message is also explicit that *nothing was reported* and
    the model must not conclude either way — a cut-off run may still have written the
    vault, so "it failed" would be a fabrication.
21. **`typebox` resolves only inside PI's loader, so a tool-registering extension file
    cannot be unit tested.** It is a host-provided peerDependency (`pi/node_modules`),
    which is why no pre-existing suite imports anything from `extensions/` that registers
    a tool. The specialist logic therefore lives in `src/specialist.ts` with the extension
    reduced to registration — the same `src/` + thin-wiring split as `planning.ts` and
    `disclosure.ts`. The contract-drift check is asserted against source instead: every
    tool the sidecar declares must appear as a registration under `mentor/extensions/`.

---

## 13. The clock: one timezone basis, and a span that crosses midnight

Two schedule defects, found together because the first masked the second.

**Defect 1 — a cross-midnight span was truncated and reported as success.**
`set_anchor` (and `reflect_schedule_on_day`) mapped a duration onto slots with
`min(SLOTS_PER_DAY, start + need)`. Sleep 22:00 → 09:00 is 660 minutes = 22 slots;
the loop claimed 4 (22:00–24:00), silently discarded 18 (nine hours), and returned
`status: "ok"`. The mentor would then tell the user their sleep was protected while
nine hours of it were not. `SetAnchorRequest.duration_min` also capped at 600, so the
request was rejected outright at the API boundary before it could even reach the bug.

The fix is a single choke point, not a check at every caller (the lesson of finding 12):
`memory/grid.py` exposes `slot_span(start, duration) -> [(date, slot), ...]`, which walks
slot by slot and *wraps*. Callers either write every slot or refuse. `set_anchor` writes
both dates and reports `slot_count`, `dates`, `spans_midnight` and a ready-to-quote
`summary`, so a partial can no longer look complete. Deliberate asymmetry: **anchors
wrap, task placements do not** — a 23:45 block for an hour is refused with "block runs
past end of day", because that is a request to put work where the user has no day left.

**Defect 2 — the grid had no timezone basis, and write and read disagreed.**
Write paths did `datetime.fromisoformat(t).astimezone(timezone.utc)` and took the slot
from the UTC hour; read paths printed `slot_clock(idx)`, the raw index label. On a
UTC+5:30 machine `"22:00"` round-tripped as `"16:30"`, and the slot depended on whether
the model formatted `+00:00` or `+05:30` — the same request, two different schedules.
`get_day_grid`'s default date was UTC's today, i.e. *yesterday* for the first 5.5 hours
of every day, and the identity block the model was reading said "today is … (UTC)" with
no clock at all.

**The rule (22): a slot label is a wall-clock reading in ONE configured timezone.**
`MENTOR_TIMEZONE` (unset → the machine's zone) is that basis, on both sides:
`config.local_tz()` → `grid.wall_clock()` in Python, `identity.mentorTimeZone()` in
TypeScript. The clock fields are authoritative — `22:00+00:00` and `22:00+05:30` both
mean "22:00 on the user's day". Day boundaries (`daily_summary._day_window`, streak math,
the grid's "today" default) are local; **absolute instants** (`created_at`,
`occurred_at`, decay and consolidation cutoffs) stay UTC, because they are instants.

Rejected alternative: convert aware inputs to local. It is the purist reading of an
instant, but it makes the slot depend on the offset the model happened to write — the
exact ambiguity being removed — and it silently re-times every existing fixture in
`scripts/tests/test_calendar_grid.py`. Recorded so the choice is a decision rather than
an accident.

What the model is now told, instead of a UTC date:

| | Before | After |
|---|---|---|
| Identity block | "Today is Tuesday, 2026-09-15 (UTC)" | "Now: Tuesday, 2026-09-15, 19:14 (Asia/Kolkata)" |
| Remaining time | unanswerable (no clock) | **still not code-computed** — the block instructs the model to read `get_day_grid` in-turn and forbids estimating |
| Tool contract | "ISO 8601 datetime with offset, e.g. …+00:00" | "Wall-clock start in his timezone; 22:00 means 22:00 for him" |
| `set_anchor` duration | capped at 600 (10h) | capped at 1440, because a sleep anchor is 660 |

Verified live: asked "what time is it, and how much time do I have left today?", the
mentor answered "Right now, at 19:14 … 4 hours and 46 minutes left in your day before
midnight" — correct against the wall clock, and derived from the injected clock rather
than invented. It also read the grid for the free window, as instructed.

Migration: `scripts/migrations/migrate_grid_to_local_time.py` (dry-run by default). The
dry run on the real database showed two live events carrying exactly the wrong offset —
`DSA in JS` stored at 15:00 IST where 09:30 was meant, `DSA Revision - Start` at 01:30
where 20:00 was meant — which is the bug in production data, and it confirms the
corrective re-interpretation the script offers. Anchors live only in `day_slots`, so a
rebuild drops them; the script lists them so they can be re-set by hand.

Still open (the P4 slice): `get_day_grid` should return `now` and mark elapsed slots, so
"how much time is left" is arithmetic the code does rather than arithmetic the model
approximates. The current answer is right when the free window runs to midnight and would
be wrong for a window that has already passed.

---

## 14. The roadmap store — one artifact, and a vault the mentor can actually read

**The defect this closed was not a bug, it was a shape.** Topic-graph state lived in
*four* places that disagreed:

| Where | Reality |
|---|---|
| `profile_facts["topic_graph_<id>"]` | claimed by `TopicGraph`'s own docstring; **nothing ever wrote it** |
| `profile_facts["topic_graphs"]` | declared in `PROFILE_KEY_MAP` and written by `pack_result`; **the row did not exist** |
| A read-time merge patching the vault over the DB | `context_builder` only — so the PI path never saw it |
| The notes' frontmatter | the only place the data actually was |

Consequences, measured rather than inferred:
- `available_topic_nodes` answered **0** nodes while the vault held **5** unlocked ones
  (22 nodes, exactly 5 with empty prerequisites). The mentor's "what can I study right
  now?" was silently blind on the default engine.
- **Nothing could ever mark a node done.** `update_node_status` had one caller, inside the
  Python graph, so the frontier could never advance: *all 22 nodes were `not_started`*, and
  had been since the day they were created.
- Regeneration **merged instead of replacing** — one live roadmap folder held files from
  three separate runs — because the folder was named after the mutable title and node ids
  were random `uuid4` fragments.
- Destructive paths matched **substrings**: with `SQL Basics` and `SQL Practice` both
  present, deleting `"SQL"` removed whichever `rglob` yielded first.

**The shape now.** `<curriculum_root>/<graph_id>/roadmap.yaml` is the single authoritative
artifact (identity, prerequisites, status, anchors, provenance, budget); notes and the
index are generated from it and carry their own ownership zones:

| Zone in a note | Owner | On regeneration |
|---|---|---|
| generated tutorial body | the specialist | replaced, and only when new content is supplied |
| `🏫 Deepened in session` | the mentor, during study | **never rewritten** |
| `📝 My Notes` | him | never touched by anything |

A note with **no** zone headings is never replaced either — new content is appended and a
warning is raised, because we cannot tell generated prose from his.

**Why the vault moved inside the repo.** The curriculum lives at `learning/topics/`, inside
`WORKSPACE_ROOTS`, so the PI core can read a note the moment a doubt comes up and append
the explanation to it. This is a *reclassification*: the vault was never one kind of thing.
Invariant-bearing state (structure, with a DAG that must stay acyclic and a budget that
must add up) stays Python-owned and validated; the notes are a git-tracked artifact tree,
which also means history, diffs and revert for free. The **personal** vault —
career documents, the master resume, daily notes — stays outside the repo and outside git
(it has a GitHub remote), and Python remains its only writer. Two vaults, split by purpose
rather than by accident.

**Consequences worth recording:**
- `find_roadmap_any` / `resolve_node` replace every substring lookup. Ambiguity returns
  `None` and a message, never an arbitrary match — the same rule for deletes.
- Writes are atomic (`os.replace`), and the **manifest is written last** as the commit
  point, so a crash leaves the previous consistent state instead of a half-applied one.
- `TopicNode.day` is assigned by `allocate_days`, not by the outline. The model proposes
  hours; code packs them against the requested budget in topological order. Before this,
  `day` was whatever the LLM said and nothing checked the arithmetic — the live
  "4-Day Interview Prep" roadmap holds **22 hours** of nodes (5.5h/day), and an unreported
  budget overflow was the default outcome.
- `verify_anchors` turns "this node is about X" into a resolvable claim
  (`N/M anchors resolved`), which is what the Repo-to-Curriculum plan needs to be
  falsifiable.
- The legacy `obsidian_graph` module is **deleted**. It was app-dead — only the one-shot
  migration imported it — so the migration was repointed onto
  `roadmap.parse_frontmatter` / `roadmap.sanitize_filename`, its two still-live helpers
  (`render_pedagogical_body`, `find_node_anywhere`) were confirmed to belong in
  `roadmap.py`, and `scripts/tests/test_roadmap_store.py` now fails if anything imports
  the old name **or** the module reappears. The second writer of the roadmap notes is
  gone, not merely unreferenced.

**Still open:** the notes-are-editable contract is only as good as the paths it allows —
`permission-gate.ts` permits direct writes under `learning/` and nowhere else, but that
list is policy, not architecture. (`obsidian_graph` is now deleted — see the bullet above.)

---

---

> **Category:** 🏛️ Architecture · **Parent:** [[Home]]
