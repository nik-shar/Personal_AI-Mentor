---
created: 2026-09-16
tags:
  - plan
  - learning
  - curriculum
  - roadmap
---

# 🎓 Repo-to-Curriculum Blueprint

> **Role:** The target product: hand the mentor a codebase and it derives the
> prerequisite curriculum for understanding it, schedules that curriculum, and
> teaches it Socratically. This document is the honest gap analysis between that
> vision and what exists today, plus the dependency-ordered path to close it.
>
> **Status:** Plan. Nothing here is implemented except where §2 says it is.

---

## 1. The use case

**Repo in → prerequisite curriculum out → taught, not told.**

The learner journey, end to end:

| # | Step | Owner |
|---|---|---|
| 1 | Learner hands over a repository — *"teach me to understand this"* | him |
| 2 | Mentor builds a **concept inventory**: which techniques, libraries and design decisions this code actually embodies, and where | mentor |
| 3 | Inventory is diffed against the **learner model** → the gap | both |
| 4 | Gap becomes a **prerequisite DAG** — topological, cycle-checked, *minimal* | mentor |
| 5 | DAG becomes a **schedule**: timeframes, daily budget, blocks on the grid | mentor |
| 6 | Each node is **taught**, not summarised: question → direction → hint → strategy → pseudocode → code | mentor |
| 7 | The learner is **reminded** when the block starts | system |
| 8 | Comprehension is **assessed against the artifact** — *"why does this file do it that way?"* | mentor |
| 9 | Evidence feeds the learner model — the loop closes, the next repo is faster | system |

Steps 4–7 and 9 are largely built. **Step 2 — the central one — does not exist,
and step 3 has nothing to read.**

### Why this is the right use case

Because **a repo supplies ground truth.** A generic curriculum drifts into
trivia; a curriculum derived from real code has a testable end state — *can the
learner now explain this system?* — which makes the whole thing assessable. That
assessment is what separates a mentor from a course generator.

Concretely, the learner's situation: knows basic Python; did DSA before and
needs algorithmic *revision* rather than instruction; knows the **theory** of RAG
and agents but has not built them. That is a skill inventory with partial credit
and decay — see G2.

---

## 2. What already exists

Most of the bricks are in place, and they are reusable as-is:

| Capability needed | Exists as | Where | Verdict |
|---|---|---|---|
| Prerequisite DAG, cycle-checked, topological unlock | `TopicGraph` + `get_available_nodes` / `get_in_progress_nodes` (networkx) | `orchestrator/memory/topic_graph.py` | ✅ |
| Goal → subtopics with `prerequisite_titles`, per-node hours, day assignment, 4 content buckets | `goal_decomposer` specialist | `agents/Goal_Decomposer/` | ✅ but from a **goal string**, not a repo — see G1 |
| Long-form teaching material per node, generated in parallel | `goal_decomposer` Phase 2 (MiniMax-M3) → Obsidian vault | `agents/Goal_Decomposer/goal_decomposer.py` | ✅ |
| Socratic ladder, prediction-first, teach-back | `learning-companion` toolkit (12 workflows) | `toolkits/learning-companion/` → `mentor/skills/` | ✅ |
| Read a real codebase before reasoning about it | `code-explorer` toolkit (`read-repo`, `trace-flow`, `explain`, `review-code`, `scaffold`) + sandboxed read tools | `toolkits/code-explorer/`, `orchestrator/harness.py` | ✅ |
| Who writes what, allocated by learning value | `scaffold` lanes `learner_writes` / `joint` / `mentor_writes`, plus `owned`/`watched` ownership memory | `docs/Code Explorer.md` | ✅ |
| The pen never leaves the learner | `propose_edit` (drafts, never writes) | `orchestrator/harness.py` | ✅ |
| Scheduling: free windows, blocks, budget caps, anchors | `find_available_slots`, `place_time_block`, `save_daily_plan`, `trim_plan_to_fit` | `harness.py`, `api/tools.py` | ✅ |
| Streaks, completion rates, drift vs intent | `compute_momentum`, `compute_drift` | `orchestrator/cognition/metrics.py` | ✅ |
| Pattern mining → durable memory | 5 detectors, weekly job | `orchestrator/cognition/observations.py` | ✅ |
| Outbound delivery to a phone | `send_proactive_notification` (Telegram verified) | `integrations/messenger.py` | ✅ |
| A record of what actually happened | the day log (`log_day_event` + Telegram listener) | `api/tools.py`, `integrations/telegram_listener.py` | ✅ built, empty |
| Learner identity and voice | `mentor_agent_guidelines.md` §1 + `mentor_persona.md`, read fresh by both runtimes | repo root | ✅ single-user only |

### What landed since this plan was written (Phase 0 of §4)

The **roadmap store** (`orchestrator/memory/roadmap.py`) replaced the vault-as-scratchpad
that this document was written against, because the substrate had to exist before a repo
could be turned into a curriculum on top of it. See
[[PI-Mentor Boundary]] §14 for the full account. In short:

| | Before | Now |
|---|---|---|
| Authority | note frontmatter, plus three other places that disagreed | one `roadmap.yaml` manifest per roadmap |
| Where | personal vault, outside the sandbox | `learning/topics/`, inside the sandbox — the mentor reads it |
| Reads | `available_topic_nodes` answered **0** nodes | **5** (the real unlocked frontier) |
| Status | could never advance (all 22 nodes `not_started`) | `mark_topic_done`, with a code-computed unlock report |
| Timeline | `day` = whatever the LLM said | `allocate_days` packs hours against the budget; overflow is reported |
| Anchors | nowhere to put them | `NodeAnchor` on every node, resolvable via `verify_anchors` |
| Checkpoints | no field | `NodeCheckpoint` exists (empty until Phase D) |

**So Phase B (repo → concepts) has somewhere to write now**, and its falsifiable test has a
number to pass: anchors that do not resolve are reported as `N/M` rather than smoothed over.

Still true from §3: **G1 is the missing link** — the extractor itself does not exist yet,
`stopping_rule` (G3) is unenforced, and the learner model (G2) has no home. The
`source: {kind: repo, path, commit}` slot and the `stopping_rule` field are in the manifest
waiting for it.


---

## 3. The gaps, ranked

### G1 — Nothing turns a REPO into a curriculum *(the missing link)*

Verified against `agents/Goal_Decomposer/state.py`: the agent's inputs are
`goal_topic`, `target_days`, `target_hours_per_day`, `raw_instructions`,
`action_type`, `preferences`, `existing_graphs`, `web_research_summary`, and the
params `topic` / `days` / `hours_per_day` / `mode` / `graph_id` / `node_id`.
**There is no repo, no path, no code, and no dependency manifest anywhere in the
contract.**

So today: `code-explorer` can *read* a repo, and `goal_decomposer` can *plan a
topic*, but nothing bridges them. The curriculum comes from the model's general
knowledge of a topic string, which means it cannot know that

- *this* repo's hard part is embedding drift, the single-writer boundary, or the
  LF-only JSONL framing;
- which node maps to which file;
- and it can never assess comprehension against the artifact, because it never
  saw the artifact.

**This is the novel engineering in the project — an analysis agent, not a
generation agent.** Everything around it is orchestration.

### G2 — There is no learner model, and what exists is unreadable

Three problems stacked, and they compound:

**2.1 — No structured place for it.** *"Knows basic Python; did DSA but needs
algorithmic revision; knows RAG/agent theory but has not built"* is a skill
inventory with partial credit and decay. `profile_facts` is a flat key-value
store; DNA memory holds sentences. Neither expresses *concept → claimed level →
evidence → confidence*.

**2.2 — PI cannot read DNA memory at all.** Verified: `build_dna_context` and
`get_deterministic` are called only from the Python path
(`orchestrator/orchestrator.py`), from `dna_reflection`, and from one UI
endpoint. The default chat engine is `pi`, whose only memory reads are
`get_profile` (profile_facts) and `recall_memories` → `MemoryRetriever` →
**episodic events**. So the mentor writes organic memory every turn and reads
none of it back. A learner model stored there would be inert.

**2.3 — Self-reported level is not trustworthy as stated.** The grounding guard
(`_is_grounded_in`) is lexical: no stemming, and digits are dropped, so
`"sleeps" ≠ "sleep"` and times are not tokens at all. A faithful paraphrase is
downgraded to `mentor_inferred` at ≤0.4 confidence, and that class is capped at
**0.6 forever**. Anything the learner says about himself in his own words is
structurally distrusted — and *"I already know X"* is exactly that kind of
statement.

### G3 — "Prerequisite" is ambiguous, and unbounded

This is the design problem that will make or break the use case.

Every concept has infinite prerequisites. Ask for the prerequisites of *vector
search* and you can justify linear algebra, then calculus, then floating-point
representation. Without a stopping rule the roadmap becomes 200 nodes and the
learner drowns — which is the standard failure mode of generated curricula.

The missing distinction is **comprehension prerequisites vs authorship
prerequisites**:

| To *understand* this repo | To *rebuild* this repo |
|---|---|
| what a 384-dim embedding is, and why the dimension is fixed | how MiniLM is trained: tokenisation, pooling, objectives |
| why two embedding implementations must not diverge | how to write a batching embedding service |
| what the single-writer boundary buys | how to design a transactional boundary from scratch |
| what "fail-open" costs, and what it hides | how to build a supervisor, backoff, and queue |
| why a slot label is a wall-clock reading | how to design a timezone-safe storage layer |

Both are legitimate goals, and they produce very different DAGs. The system must
**ask which one is wanted, per repo**, default to comprehension, and be able to
state its own stopping rule — *"stopping here because you can now read this
module, not rewrite it."* A curriculum that cannot explain its own boundary is
just a long list.

### G4 — Teaching is not bound to the artifact, and there is no assessment

`learning-companion` teaches concepts well and `code-explorer` reads code well,
but nothing closes the loop between them. The end state of this use case is not
"read the tutorial" — it is **"answer questions about this codebase"**. That
needs two things that do not exist:

1. Every DAG node carrying **anchors into the repo** — file, symbol, decision.
2. A **checkpoint per node** whose answer is judged against the artifact. For
example: *"this repo computes embeddings in Python only. What breaks if the
TypeScript side embedded too — and why is that failure silent?"*

The repo's own principle — *if the mentor wrote something, the learner writes
next, same session* — is the right instinct, but it needs a repo-grounded rubric
to become assessable. The `tutorial-writer` content buckets (conceptual /
algorithmic / hands_on_code / reference) already give the shape.

### G5 — The reminder loop is half-built

The pieces exist; the loop does not.

- `scheduler/runner.py` is a **CLI** (`BlockingScheduler`: daily summary at
  23:00, consolidation Sunday 02:00). Nothing keeps it alive as a service.
- `scheduler/engine.py` can schedule one-off wake-ups and notify, and
  `send_proactive_notification` delivers to Telegram — but nothing is wired to
  *"your 10:00 block is starting"* or *"here is today's plan"*.
- The morning briefing was designed and **never built**.
- Block-level reminders depend on the grid being declarative, which depends on
time sense (G7).

### G6 — "For any user" is multi-tenancy, and it fights the differentiator

Nothing in the codebase is multi-user, and the reasons are structural:

| Single-user assumption | Where |
|---|---|
| No `user_id` on any table — `profile_facts`, `episodic_events`, `dna_memory`, `day_slots`, `schedule_events`, `conversation_sessions` | `orchestrator/memory/models.py` |
| The constitution's §1 **is his biography** ("Nikhil Sharma — IIT Roorkee…") | `mentor_agent_guidelines.md` |
| One configured name | `MENTOR_USER_NAME` → `config.USER_NAME` |
| One vault path, one topology folder | `config.OBSIDIAN_*` |
| One Telegram chat id / allowlist | `.env` |
| Local-first, single-machine is the stated premise | README, boundary doc |

Making it multi-user means a schema migration across six tables, per-user
identity files replacing §1, per-user vault paths, per-user scheduler jobs, and
per-user sessions. It also turns a *local-first personal system holding one real
person's memory* into a SaaS-with-a-mentor, which is a crowded and weaker idea.

**Recommendation:** build it for one user, and document the boundary as a
deliberate stance — *local-first, single-user by design; the single memory
boundary is the reason neither invariant can drift*. If multi-user ever happens,
it is a separate axis with its own migration, not a retrofit mid-build.

### G7 — Mechanical gaps this workflow will trip over

| Gap | Effect on this use case | Evidence |
|---|---|---|
| No time sense: no `now` / elapsed / remaining in the grid | "schedule me" and "remind me" both need it; past windows are still offered | `build_day_grid` returns slots + free windows only |
| Anchors are per-date and do not recur | a study plan collides with the fixed sleep/routine window every single day | `toolkits/calendar-manager/workflows/anchors.md` |
| The calendar guard is per-function, not per-choke-point | `save_daily_plan` (model-reachable) bypasses the anchor and overlap guards | boundary doc finding 12 |
| DNA is write-only on the PI path | nothing learned in a session is readable in the next one (see G2.2) | verified call sites |
| Consolidation marks rows `consolidated=true` **before** the observation job mines them | detectors see a half-emptied window once this matures | `scheduler/consolidation_job.py:59` vs `:75` |
| A stray grid date `2099-01-01` with 48 orphan slot rows | the hallucinated-date class, already present in the live database | live query |

---

## 4. The dependency-ordered path

The chain is:
**repo → concepts → (learner model) → gap DAG → schedule → teach → assess → evidence.**

### Phase A — Make memory readable *(prerequisite for everything learner-shaped)*

- A deterministic memory digest for PI, mirroring `build_dna_context`'s Layer 1
  (due / core / usual shape / pending validation) — **tag-selected, not
  similarity-selected**, so it never depends on retrieval luck.
- Fix the grounding guard to check **meaning** (the embedding the store already
  computes) rather than spelling, while keeping the proper-noun fabrication
  guard that caught a real invention.

*Why first:* without it the learner model cannot exist, and every session starts
from zero. It is plumbing, not research — and it is the difference between a
mentor and a very good one-shot assistant.

### Phase B — `understand-repo`: repo → concept inventory  ← **the novel piece**

Given a path (inside the existing `WORKSPACE_ROOTS` sandbox), produce a validated
inventory:

```
concept     : 384-dim embedding / vector-dimension coupling
why needed  : comprehension
appears in  : memory/store.py::_get_embed_model, models.py::Vector(384)
prereq of   : [ ... ]
difficulty  : 2/5
```

Reuses the sandboxed code tools, the `read-repo` / `trace-flow` workflows, the
four content buckets, and a `TopicGraph`-shaped output.

### Phase C — Learner model + gap diff

- A structured inventory: `concept → level (0–3) → evidence → confidence`,
  written by **assessment outcomes**, not by self-report alone.
- Diff repo inventory against learner inventory → the gap DAG. Everything
  downstream reuses `goal_decomposer`'s outline → spec → vault pipeline.

### Phase D — Teach against the artifact

- Extend `learning-companion` with repo-anchored nodes: teach the concept, then
  point immediately at where it lives *here*.
- A per-node checkpoint judged against the artifact; that result is the evidence
  that feeds Phase C.

### Phase E — The loop: schedule, remind, brief

- Time sense in `build_day_grid` (now / elapsed / remaining).
- Recurring routines, so the fixed parts of the day stop being re-litigated.
- The morning briefing job, plus block-start reminders on the real scheduler.

### Recommended order

| Order | Work | Why here |
|---|---|---|
| 1 | **B — repo → concepts**, prototyped on his own repo | the differentiator; falsifiable; independent of everything else |
| 2 | **A — memory readable + grounding fixed** | unblocks the learner model |
| 3 | **C — learner model + gap diff** | needs A and B |
| 4 | **D — teach against the artifact** | needs C |
| 5 | **E — schedule / remind / brief** | mechanical; do it once content exists |


---

## 5. The falsifiable first test

Use **this repository** as the first corpus, because its ground truth is known.

A defensible concept inventory for it includes, at minimum: the single-writer
boundary; JSONL framing over an RPC subprocess, and why LF-only matters;
deterministic session-id derivation; 384-dim embeddings and why two embedding
implementations would degrade recall *silently*; topological unlock over a
prerequisite DAG; fail-open tool contracts; contracts that are generated rather
than mirrored; idempotence enforced by the store's owner rather than by the
transport.

**If the extractor cannot produce a defensible version of that list for this
repo, it will not work on an unfamiliar one.** If it can, it is demonstrated —
and that demonstration is the portfolio artefact.

Second test: hand it a stranger's repo, check the inventory against reality, then
compare against a human's inventory of the same repo. That is an **evaluation
story**, which almost no portfolio project has.

---

## 6. Why this is a strong portfolio project

- **It is not a tutorial app.** Multi-agent orchestration, planning, retrieval
  over code, DAG reasoning, a learner model, and an assessment loop.
- **It has a natural evaluation.** *"Does the curriculum produce
  comprehension?"* is measurable — checkpoint pass rate, time-to-comprehend,
  self-report vs assessment. Almost no portfolio repo can state a metric.
- **The boundary documentation is the interview material.**
  `docs/PI-Mentor Boundary.md` is a decision register with named gates and real
  postmortems: the timezone bug where `22:00` round-tripped as `16:30`, the
  midnight-wrap bug where an 11-hour sleep anchor claimed 4 slots and returned
  `ok`. Walking through *bug → diagnosis → invariant introduced* is a senior
  signal, and it is already written down.
- **"Local-first, single-user by design" is a stance to defend**, not an
  apology — and it is what makes the privacy and single-writer arguments land.

---

## 7. Open decisions

1. **Comprehension or authorship** as the default stopping rule? (G3)
2. **Is "for any user" in scope at all**, given G6? Recommendation: no — one
   user, documented boundary.
3. **Where does the learner model live** — a typed store with an evidence
   ledger, or natural language in DNA plus tags? (Depends on Phase A.)
4. **Does `understand-repo` become a fourth specialist** with its own graph, or
   a new mode inside `goal_decomposer`? The gate test: analysis has no
   single-writer need, but it does need the code sandbox — so likely a
   specialist that reuses `harness`'s read tools.
5. **Who judges the teach-back, and against what rubric?** The honesty of the
   whole loop lives here: an assessment that can be gamed teaches nothing.

---

> **Category:** 🎓 Learning · **Parent:** [[Cognitive Layer]]
