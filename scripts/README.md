# scripts/

Every runnable entry point lives here, grouped by what it's for. Nothing in the
application imports these — they are developer-facing tools, so they are free to
be blunt and are excluded from the strict lint config.

```
scripts/
├── tests/        # verification suites — the regression net (45)
├── migrations/   # one-shot data migrations (4 applied, 1 still actionable)
├── tools/        # utilities + codegen, safe to re-run anytime (8)
└── run_pi_mentor.sh   # run the PI agent core as the mentor
```

## tests/ — the regression net

Run a suite directly; each prints its own pass/fail summary and exits non-zero on
failure.

```bash
uv run python scripts/tests/test_calendar_grid.py
uv run python scripts/tests/test_orchestrator_harness.py
```

| Suite | Covers |
|---|---|
| `test_pi_bridge.py` | PI RPC bridge protocol: JSONL framing (U+2028/U+2029 safety), `agent_settled` semantics, authoritative `message_end` text, and the event → `/flow` trace contract |
| `test_calendar_grid.py` | 48-slot grid math, anchors, availability, **overlap guard** |
| `test_calendar_awareness.py` | calendar toolkit wiring + tool surface |
| `test_orchestrator_harness.py` | deterministic harness tools (`save_daily_plan`, `log_learning_session`) |
| `test_memory_core.py` | **the shared memory floor** (`memory/core.py`) — one engine per DSN, both stores sharing that pool, the *union* schema (backfill **and** HNSW index, either half of which was previously missing depending on which store you built first), embedding singleton + normalisation, fail-open embedding, rollback. Read-only against real memory: its one INSERT is rolled back and then swept |
| `test_agents_context.py` | **`AGENTS.md`** — the system context PI loads every session. Guards both ends of the tension: *completeness* (every table in `memory/models.py` is described, every document it points at exists) and *distinctness* (it must not restate the constitution or persona, whose markers are first proven real so the check is not vacuous), plus a hard line/char budget because the text is paid for on every turn |
| `test_identity_tool.py` | **the `get_identity` wire** — the tool that puts P1 in front of the agent: declared read in the manifest with both pydantic schemas, the generated TS contract carries it, the extension renders the **Python** block rather than rebuilding it, a real HTTP read with provenance and counts, `include_profile=false`, schema-enforced validation (422s), fail-open as an `error` field rather than a 500, no mutator in the handler, and the facade naming convention (`identity` is the module, `read_identity` the reader) |
| `test_history_tool.py` | **the `get_history` wire** — the tool that puts P3 in front of the agent: declared read with both schemas, the generated TS contract carries it, the extension renders the **Python** block, a real HTTP read with counts, parameters honoured and schema-enforced (422s), fail-open as an `error` field, no mutator in the handler, and the naming convention |
| `test_memory_patterns.py` | **P4 patterns** — what it means. Momentum read as arithmetic; a **real** event set (3/4 mornings vs 0/4 evenings) genuinely triggers the same detectors the weekly job uses, so the fresh path is not mocked; the cold-start guard refuses a thin window; stored observations carry confidence, most-trusted first; `stored` and `detected-now` are labelled differently so a pattern noticed once cannot read as one earned; each source fails independently; and the boundary is asserted — drift is deliberately out of scope |
| `test_memory_history.py` | **P3 history** — the narrative: the rolling thread, daily recaps, past sessions (with a human distance and an excerpt when there is no summary), and the **day log** read forwards. Each of the four sources fails **independently** and names itself; `_ago` tolerates a naive/aware timestamp mix; rendering is capped and says `""` when there is genuinely no past. The day-log source is the one `log_day_event` has been writing since it shipped with nothing able to read it back |
| `test_memory_state.py` | **P2 present state** — the day as it stands: `elapsed`/`remaining` computed in **code** (closing the G7 "no time sense" gap) from an injected `now`, contiguous occupied slots grouped into committed blocks with anchors and tasks classified, free windows from the code-computed grid, plan + streak from profile facts, deterministic at a fixed moment, fail-open that names what failed, rendering that always says something, and the P1/P2 key split asserted |
| `test_memory_identity.py` | **P1 identity** — the first purpose behind the memory facade: the structured + organic halves composed, identity-only memory types, provenance preserved, deterministic ordering (identical across calls, never retrieval luck), the due / pending-validation layer, the limit, dependency injection, and fail-open that **names** what failed. Reads live memory read-only; the logic is checked against fakes |
| `test_toolkit_steering.py` | toolkit loader, guardrails, prompt injection |
| `test_guidelines.py` | constitution loading — **requires `mentor_agent_guidelines.md` at the repo root** |
| `test_persona_single_source.py` | persona lives in `mentor_persona.md`, not in `persona.py` — reader, `{name}` substitution, assembly, fail-open, and that the PI side holds no copy either |
| `test_specialist_bridge.py` | Phase-4 `run_specialist`: manifest declaration, structured errors for bad input, task_type validation reusing `VALID_TASK_TYPES`, graph-independence, and the `pi` chat-engine default |
| `test_roadmap_store.py` | **the roadmap store** — manifest round-trip, zone-preserving rewrites (the mentor's in-session notes and your own notes survive a regeneration), idempotence, prune-only-what-we-own, exact-match deletes, DAG-checked mutators, anchor verification, `allocate_days` budget packing, `validate_roadmap`; plus a guard that the **retired vault writer stays deleted** (nothing imports it, and the module does not come back) |
| `test_mark_topic_done.py` | the `mark_topic_done` intent — status persistence, the **code-computed unlock report**, `roadmap` disambiguation, ambiguity/unknown/invalid-status refusal, and that a `note` lands in the session log rather than your notes |
| `test_pi_bridge.py` | PI RPC bridge: JSONL framing, `agent_settled`, authoritative `message_end`, trace contract |
| `test_dna_*.py` | DNA memory store / context / reflection / observations |
| `test_job_hunter.py`, `test_toolkit_steering.py` … | agent-specific suites |

⚠️ **Two suites delete rows from whatever database `DATABASE_URL` points at**
(`test_dna_memory_store.py`, and `tools/eval_dna_memory.py`). Both now refuse to
run unless the DB name contains `test`, or `DNA_TEST_ALLOW_WIPE=1` is set. Point
them at a throwaway database:

```bash
DATABASE_URL=postgresql://nik:nik@localhost:5432/ai_companion_test \
  uv run python scripts/tests/test_dna_memory_store.py
```

Several other suites (`test_conversation_thread.py`, `test_dna_context.py`,
`test_dna_observations.py`, `test_dna_reflection.py`, `test_first_contact.py`,
`test_job_hunter.py`, `test_conversation_transcript.py`) delete narrower scopes of
live data and are **not yet guarded**. Review before running them against real memory.

**Curriculum suites are sandboxed, not guarded — by construction.** The three that
drive the real `goal_decomposer` (`test_goal_decomposer_quality.py`,
`test_adaptive_tutorial.py`, `test_deep_tutorial_generator.py`) set
`MENTOR_CURRICULUM_PATH` to a temp dir *before* `orchestrator.config` is imported, and
remove it at exit; `test_roadmap_store.py` and `test_mark_topic_done.py` run entirely
inside their own temp roots. None of them can reach the live curriculum, so no
guard flag is needed.

## migrations/ — one-shot

Four are already applied. `migrate_grid_to_local_time.py` is the exception: it is the
corrective pass for the timezone-basis change (boundary doc §13), dry-run by default,
and worth reading before it runs because it re-times real schedule events.

`migrate_roadmaps_to_manifests.py` has also been applied (3 roadmaps / 22 nodes moved
into the in-repo curriculum store). It is safe to re-run: it **copies** rather than
moves, never touches the legacy vault, backs up the source notes to
`data/memory_backups/roadmaps_legacy_<stamp>/` before writing, and skips any roadmap
that already has a manifest. `--force` re-converts, `--no-backup` skips the copy.

```bash
uv run python scripts/migrations/migrate_roadmaps_to_manifests.py            # dry run
uv run python scripts/migrations/migrate_roadmaps_to_manifests.py --apply    # convert
```

Keep these: they are what you run to bring a fresh database or vault up to date.

```bash
uv run python scripts/migrations/migrate_vault_to_folders.py --dry-run
```

## tools/ — utilities and codegen

| Tool | Purpose |
|---|---|
| `manage_memory.py` | the memory interface: export/import JSON, list/set/delete facts, prune stale pipeline entries |
| `export_mentor_tools_schema.py` | **codegen** — regenerate `mentor/src/generated/mentor-tools.ts` from `api/tools.py` |
| `migrate_toolkits_to_skills.py` | **codegen** — regenerate `mentor/skills/` from `toolkits/` |
| `reset_memory.py` | destructive: truncates every memory table. Requires explicit intent |
| `eval_dna_memory.py` | scores refection/compare prompts (guarded — see above) |
| `view_history.py` | print a conversation thread |
| `demo_code_explorer.py`, `generate_sample_note.py` | demos / sample generation |

The two codegen tools are the single source of truth for generated artifacts — edit
their *inputs* (`api/tools.py`, `toolkits/`), never the generated output.

## Path convention

Every script finds the repo root by walking up from its own location:

```python
ROOT = Path(__file__).resolve().parent.parent.parent
```

Three parents, because scripts live two levels below the root. If you move a
script to a different depth, that line must change with it.