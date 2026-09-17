# MEMORY — the design of the memory layer

> **Status:** design, agreed in discussion, not yet built. Companion to `GOAL.md`
> (§4 stage 0 is this document's job).
>
> **The claim:** continuity across sessions is not achieved by keeping the context
> window — it cannot be kept. It is achieved by **promoting what mattered out of
> the transcript into a durable store, and pulling it back in on the next turn.**

---

## 1. The problem, stated exactly

Two different things get confused:

| | What it is | Survives a restart? |
|---|---|---|
| **The record** | everything that was said — the transcript | Yes. PI already writes every session to `~/.pi/agent/sessions/<cwd>/*.jsonl`, append-only, on disk |
| **The context** | what the model can see *right now* | **No.** A finite window, compacted at a threshold and on overflow |

So nothing is actually being *deleted* today. What is missing is **the path back**:
the transcript is on disk, but nothing promotes it into memory, and nothing pulls
memory back into the next session.

The failure is continuity, not storage.

## 2. The architecture

```
Tier 0 ── THE RECORD                    ~/.pi/agent/sessions/*.jsonl
          Everything said. Append-only. Free. Never curated.
          PI writes this already. We do not touch it.

              ↓   the memory agent reads forward from a cursor

Tier 1 ── CURATED MEMORY                data/
          profile.yaml    facts about him
          memories.jsonl  what was learned, with provenance + confidence
          daylog.jsonl    what he actually did

              ↓   injected each turn (a small core) + read on demand (depth)

Tier 2 ── CONTEXT                       the window, this turn
          Small, curated, current. Rebuilt every session from Tier 1.
```

**The one-line version:** Tier 0 is the past, kept for free. Tier 1 is what
survived judgement. Tier 2 is what the mentor has in mind right now — and it is
*rebuilt*, not preserved.

## 3. The memory agent

A **second PI process** — not a bespoke model call, and not a skill the mentor
switches into. PI is already an agent with file tools, so running it again with
different instructions is the cheapest way to get a genuinely separate worker.

It is separated on **six** axes. The first five come free from the CLI; the sixth
is the one that actually holds the design together.

| Axis | How |
|---|---|
| Model | `--model <cheap tier>` — extraction is not a reasoning-heavy job |
| Context | `--session-dir <its own>` + `--no-session` — it never sees the mentor's window |
| Instructions | `--skill memory-keeper` — its own skill, its own procedure |
| Tools | `--tools read,grep,write` — it cannot touch the calendar or run commands |
| Context files | `--no-context-files` — it does not load `AGENTS.md`; it is not the mentor |
| **Write permission** | **Its own permission gate allows `data/` only** — see §4 |

```bash
pi -p "<curate from cursor>" \
   --no-extensions -e mentor/extensions/memory-writer.ts \
   --no-skills --skill memory-keeper --no-context-files \
   --model <cheap-tier> --session-dir .pi/memory-sessions --no-session \
   --tools read,grep,write
```

## 4. The separation invariant — the load-bearing part

Different context is not separation. **Disjoint write permissions are.**

The mentor's gate today allows `learning/` and nothing else. The memory agent's
gate should allow `data/` and nothing else. Then:

- the mentor **cannot corrupt memory**
- the memory agent **cannot touch the curriculum**
- neither can reach the other's domain, by construction

That is the old "single writer" rule, but enforced **per agent instead of per
runtime** — and it is stronger, because it is structural rather than a convention.

### One writer per file

To keep it true, no file may have two writers:

| File | Written by | Read by |
|---|---|---|
| `data/memories.jsonl` | **the memory agent only** | the mentor |
| `data/profile.yaml` | **the memory agent only** | the mentor |
| `data/daylog.jsonl` | **the mentor** (via a `log_day_event` tool) | the memory agent |
| `data/requests.jsonl` | **the mentor** (explicit "remember this") | the memory agent |

`data/requests.jsonl` is the seam: the mentor appends a *request* instead of
writing memory itself, and the agent drains it. Two writers, two files, no shared
mutable state — which also solves concurrency, since appending a line is safe
where rewriting a file is not.

### Control flow: state, not instructions

The memory agent **writes memory. It does not tell the mentor what to do.**

This is the boundary most likely to be re-litigated, so it is written down
explicitly. The naive version — *the memory agent watches the chat and instructs
the mentor to change strategy* — turns it into a supervisor, and that breaks four
things at once:

1. **Two directors, no accountability.** The mentor owns the persona, the
   constitution, the crisis guardrail and the consent rules. A background process
   issuing orders bypasses all of it — the guardrails fire on *the mentor's turn*
   and have no hook on "an instruction arrived."
2. **It would be instructing blind.** The curator reads the transcript *after the
   fact*. It does not know the current mood, or what was just said. Directing from
   there produces the classic failure: *"why is it suddenly pushing this?"*
3. **No confidence gate applies.** A memory at `mentor_inferred` is capped at 0.60
   and the mentor must *ask*. As an **instruction** there is no cap — it is an
   order, obeyed.
4. **The separation collapses.** An agent that steers the mentor's behaviour is
   writing into the mentor's domain. §4 stops being true.

**The same effect, through readable state:**

| | Instruction | State |
|---|---|---|
| Curator writes | `{"instruction": "raise accountability to 4"}` | `{"content": "Missed 4 of the last 6 scheduled blocks", "source": "data_derived", "confidence": 0.70, "user_confirmed": false}` |
| Mentor | obeys — cannot explain why | reads it, **decides**, and can be argued with |
| Correction | none available | he can say *"those were cancelled, not missed"* |

The behavioural outcome is the same. The difference is that the mentor **owns the
response**, the confidence gate still applies, and the belief is traceable to a
sentence.

> **The rule:** the memory layer maintains the *state that strategy is derived
> from*. It never adjusts strategy itself. Continuity comes from the mentor
> re-reading state each session and choosing — not from being commanded.

### What the curator may flag

There is a useful middle ground between "remember a fact" and "issue an order":
the curator may **queue items for the mentor's attention** — signals it noticed,
which the mentor decides whether to act on, and when.

- an **unresolved thread** — *"he said he'd revisit the argmax step three times"*
- a **contradiction** — *"he said he's fluent in Python; he also said he's shaky on it"*
- **system state** — *"no day has been planned in nine days"*

These go to `data/attention.jsonl`, and they are **inputs to a decision, not
decisions**. A mentor sentence built from one sounds like *"you've mentioned that
derivation a few times — want to actually work it, or is it parked?"* That is
influence. An instruction would be *"tell him to study the argmax step."*

**The old design drew exactly this line, and it is worth honouring.** Its
personality layer held `accountability_level`, `coaching_emphasis`,
`framing_that_works` and `framing_that_bounces` — real strategy state — but it
shipped with **`auto_adjust: False`**, and the documented rule was *"escalation
only via explicit command."* Observations were written as `data_derived`
memories, never as behavioural changes. Memory **informed**; it did not steer.
That judgement survives the move to a separate agent.

## 5. The cursor — why *when* it runs stops mattering

The agent reads the transcript **forward from where it last stopped**, tracked in
`data/.curator_cursor.json`:

```json
{ "session": "2026-09-17_abc123", "entry": 418, "curated_at": "..." }
```

This is the piece that makes the whole design robust:

- **It cannot skip anything.** A missed run is caught on the next one.
- **It is idempotent.** Re-running over the same range changes nothing.
- **Timing is no longer critical.** Because the source of truth is the transcript
  on disk — not an in-memory payload — the agent does not have to run at the exact
  moment context is lost. It can run late, or twice, or after a crash.

Without the cursor, the design depends on a hook firing at precisely the right
instant. With it, the hooks are conveniences rather than requirements.

## 6. What gets promoted

The promotion filter is the judgement call, and it is deliberately narrow. A
memory is written **only** if it is one of:

- a **fact** about him (`user_stated` — he said it)
- a **preference** or **constraint** that should shape future plans
- a **goal**, target, or commitment
- a **struggle**: something that confused him, or a mistake worth not repeating
- an **observation** with a repeated pattern behind it (`data_derived`)

Everything else stays in Tier 0. **The agent's silence is the forgetting** — that
is the whole mechanism. It never has to file things away for disposal, because
the low-priority bucket is the transcript that already exists.

> This is the part that makes it tractable: an **append-only promotion filter**
> over a log, not a filing system with a trash can.

### Every memory carries its provenance

| `source` | Starts at | Ceiling | Meaning |
|---|---|---|---|
| `user_stated` | 0.95 | 1.0 | He told me — trusted |
| `data_derived` | 0.70 | 0.95 | Computed from his actual behaviour |
| `mentor_inferred` | 0.40 | **0.60** | A guess — the mentor must **ask**, not assert |

Plus **the quote it came from**, so a wrong memory is traceable to the sentence
that produced it.

## 7. Guardrails — this is a fabrication surface

A second agent that reads a transcript and writes *"facts about him"* can be
wrong, and a wrong memory **persists and compounds**. The mentor then asserts it
confidently — which is the exact failure `GOAL.md` §5.1 forbids.

So the memory agent is held to the same standard as the mentor:

1. **Provenance is mandatory.** No memory without a `source` and a supporting
   quote. If it cannot cite the sentence, it does not write the memory.
2. **`mentor_inferred` is capped at 0.60 and marked unconfirmed.** The mentor
   must *ask* about those ("am I reading that wrong?"), never assert them.
3. **Append-only. Corrections supersede, never delete.** A wrong memory is
   corrected by adding the new one and setting `superseded_by` on the old.
4. **It decides what is worth keeping, never what is true.** The two judgements
   are different, and only the first is its job.
5. **It never touches the curriculum, the calendar, or code** — its gate denies
   that, and its prompt says so anyway.
6. **Fail soft.** A broken curator means memory goes stale, not that the mentor
   breaks. It must never block a turn.

## 8. When it runs

Because of the cursor, these are conveniences, not requirements. In order of
value:

| Trigger | Why it is here |
|---|---|
| **`session_before_compact`** | PI fires this exactly when context is about to be lost and hands over `branchEntries` — the messages being discarded. Letting the curator read those means **compaction can never lose something that mattered** |
| **`session_shutdown`** | Session ends — a cheap sweep so the next session starts from current memory |
| **Daily (cron)** | A backstop. Catches anything the hooks missed |
| **On demand** | Explicit *"remember this"* — appended to `data/requests.jsonl`, drained immediately |

**Deliberately NOT per-turn.** It would double cost and latency for a need the
compaction hook already covers.

> **Note on the compaction hook:** do not make the turn wait for a full PI run.
> The curator reads the transcript from disk, so it can be fired and forgotten —
> the cursor guarantees nothing is skipped.

## 9. Pulling memory back in

Promotion is only half the loop. The other half is getting Tier 1 into Tier 2:

- **A small always-on core** injected each turn — anchor facts, active goals,
  current roadmap state. Keep it small; it is paid for on every turn.
- **`recall_memories`** for depth on demand — a grep over `memories.jsonl`, not a
  vector search. At this corpus size, reading beats embedding.
- **`get_identity` / `get_history`** for the shaped views.

This is what actually delivers continuity: the mentor does not *remember* last
week, it **re-reads** what was promoted, every session.

## 10. Deliberately deferred

Naming these so they are not built early — the same discipline `GOAL.md` §7 uses.

- **Semantic search / embeddings.** Deleted with the Python layer, and correctly.
  At hundreds of memories, `grep` + read wins. Revisit at thousands.
- **Consolidation tiers** (episodic → weekly → monthly → facts). The old system
  had it and the idea was sound — it is the human "sleep" layer — but the curator
  plus the compaction hook covers the real need. Add it when the curated store is
  big enough to need summarising.
- **A dashboard over memory.** The files are readable. A UI is a later view, not
  the thing itself.

## 11. Open decisions

| # | Question | Leaning |
|---|---|---|
| **M1** | Explicit *"remember this"* — queue it or trigger the curator immediately? | **Trigger immediately.** Instant confirmation is worth it, and the cursor keeps it safe |
| **M2** | Which tier writes memory? | Cheap/fast for extraction. The strong tier only when merging contradictions |
| **M3** | Does the mentor get *any* write access to `data/`? | **No.** Requests go through `data/requests.jsonl`. One writer per file is the invariant |
| **M4** | How much is injected every turn vs. read on demand? | Small core injected; everything else on demand. Budget it — it is per-turn cost |
| **M5** | Does the curator see `branchEntries` or the session JSONL? | The JSONL, via the cursor. `branchEntries` is the fallback if a session is mid-flight |

**M3 is the one to get right.** The moment the mentor can write memory files
directly, there are two writers again, and the whole separation in §4 stops being
true.

---

## 12. What this replaces

For the record, because the old design got some of this right and it is worth
being explicit about what is being kept:

| Old (Python) | Now |
|---|---|
| `dna_memory` table + a 4-tier confidence model | kept — `data/memories.jsonl` with the same provenance/confidence idea |
| Async reflection after every turn | replaced — a separate agent on a cursor, not inline work |
| 384-dim embeddings for recall | dropped — `grep` over text at this scale |
| Chroma for a voice store | dropped with the career module |
| Postgres for 77 rows | `data/`, as files |
| Consolidation tiers | deferred, not deleted from the plan |

The parts that were load-bearing were the **provenance model** and the **idea that
not everything deserves remembering**. Those are kept. The infrastructure around
them was the problem, not the ideas.

---

## 13. What is actually built

Written after the code, so the doc reflects reality rather than intent.

| Piece | Where | State |
|---|---|---|
| The store — append-only jsonl, code-owned ceilings, supersede, profile, attention, requests | `mentor/src/memory/store.ts` | ✅ built, 17 tests |
| The cursor + transcript reader | `mentor/src/memory/transcript.ts` | ✅ built, incl. the trailing-newline fix below |
| The curator's tools + its `data/`-only gate | `mentor/curator/extension.ts` | ✅ built |
| The curator's instruction set | `toolkits/memory-keeper/` → `mentor/skills/memory-keeper/` | ✅ generated |
| The launcher (six-axis isolation) | `scripts/run_memory_curator.sh` | ✅ works end to end |
| The mentor's side: `remember` + `read_memory` | `mentor/extensions/remember.ts` | ✅ built |

**Decisions, resolved:**

| # | Decision | Resolution |
|---|---|---|
| M1 | Queue or trigger immediately? | The mentor queues to `requests.jsonl`; a curator run drains it. Immediate triggering is a launcher call away |
| M2 | Which tier writes memory? | A separate, configurable tier (`CURATOR_PROVIDER`/`CURATOR_MODEL`) |
| M3 | Does the mentor get `data/` write access? | **No.** `remember` appends a request; the gate denies everything else |
| M4 | Injected each turn vs. read on demand? | `read_memory` on demand. Always-on injection is not built |
| M5 | `branchEntries` or the JSONL? | The JSONL, via the cursor. The compaction hook is not wired |

**Two bugs the tests caught, worth remembering:**

1. **The trailing-newline cursor bug** (`transcript.ts`). `split("\n")` on a file
   ending in a newline yields a trailing `""`. Counting it as consumed put the
   cursor *at* the next append instead of before it, so the first message of every
   new range was silently skipped — data loss with no error. Fixed by counting
   consumed lines, not array length.
2. **`includeInactive` was still excluding superseded rows.** The pointer check ran
   before the flag, so "show me everything" showed less. The store would have been
   unauditable in exactly the case it mattered.

**Still not built** (deliberately, per §10): the always-on context injection, the
compaction/shutdown hooks, consolidation tiers, semantic search, any UI.

