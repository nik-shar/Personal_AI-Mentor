# GOAL — what this system is for

> **Status:** the north star, written before the product exists. This is the
> document that decides what gets built and what gets refused.
>
> When a feature is proposed, the first question is *"does this serve the loop in
> §4?"* If it doesn't, it is not this product.
>
> **Where it lives:** `GOAL.md`, at the repo root, so it is the first thing
> anyone reads — including the mentor.

---

## 1. The goal, in one sentence

**A local-first personal mentor that knows you, works out what you are missing,
plans your time around it, and teaches you — across sessions, and without ever
making things up.**

The distinctive claim is not "an AI that can talk about learning." It is that the
mentor holds **a real model of you** — what you know, what you're confused by,
what you've actually done with your days — and that every plan it makes is
derived from that model rather than from plausible-sounding general advice.

## 2. The problem

Five failures of every existing option, in order of how much they cost:

1. **Advice is generic.** Ask for a "30-day AI roadmap" and you get the same one
   whether you've never programmed or have five years of Python. It cannot know
   what you already know, so it wastes your time by design.
2. **Material isn't sequenced to *your* gaps.** Courses teach a fixed path. The
   specific things standing between you and being able to build the thing are
   never actually computed.
3. **Plans don't survive a real calendar.** A roadmap is a list of intentions
   until it becomes blocks on specific days. Nobody bridges that.
4. **Nothing remembers your struggles.** The derivation that tripped you in March
   is invisible in June, so it trips you again.
5. **Everything is a cloud service.** Your career, your confusion, your doubts —
   uploaded.

## 3. Who it serves, and the shape of the relationship

One person, on their own machine, over years. Not a search engine, not a course
platform. It is *the person in your corner who has been paying attention* — which
means its value comes from continuity, not from any single answer.

The relationship has phases: cold start → getting to know → building trust →
established. Early conversations are discovery, not delivery. It earns the right
to plan your time by first proving it understands you.

## 4. The end-state loop

This is the product. Everything else is support.

### Stage 0 — It learns you
You tell it about yourself: what you know, what you're shaky on, what confuses
you, what you're aiming at. It remembers — with provenance and confidence, so it
can distinguish *"he told me"* from *"I inferred it"*, and so it can be corrected.
Over time it also learns from your actual days, not just your self-description.

> **Requirement:** what you say must still be there next week. A mentor that
> forgets is a search engine with a personality.

### Stage 1 — You point at a goal
Usually a repository. *"This is the project I want to build — teach me to build it
myself."* Or a non-repo goal: *"I want to be ready for a DSA interview."* The goal
is a first-class object with a target and a stopping rule.

### Stage 2 — It reads the real material
Not a summary of the README. It reads the code: greps for the invariants, follows
imports, opens the files that carry decisions, and cites what it found. Concepts
come from what the artefact *is*, not from what its author claimed in prose. For a
non-code goal, the same discipline applies to whatever the real source is.

> **Requirement:** every concept carries a citation that resolves against the real
> artefact. A citation you did not read is worse than no citation, because it
> looks checkable.

### Stage 3 — It finds your gaps
It diffs the concepts against what it knows about you and marks each: **known**
(skip it), **shaky** (short review), or **new** (full treatment). A "known" verdict
must cite its evidence. No evidence means it is not skipped.

> **Requirement:** the mentor must be able to say *why* it decided you already
> know something — and be wrong out loud, so you can correct it.

### Stage 4 — It builds the roadmap
A real artefact on disk: nodes with prerequisites, a time estimate each, its
citation, and the order. Plus notes you can study from.

### Stage 5 — It checks the estimates
Two separate checks, and it does both:

- **Is this estimate sane for this topic?** — a judgement, stated *as* a judgement,
  and challenged out loud when it looks wrong.
- **Does he actually have this time?** — arithmetic against his real calendar.

An estimate that is never checked is a number nobody should trust.

### Stage 6 — It puts it on your calendar
Blocks on specific days, respecting sleep, meals, commute and gym as hard walls.
Proposed with reasons, written with your consent.

### Stage 7 — You study, and it teaches
While you work: prediction before explanation, the smallest useful hint,
hypothesis before diagnosis. It does not hand you the answer before you commit.
Sessions deepen the notes, so the material improves as you go.

### Stage 8 — It adapts
When you miss a block it asks whether the *estimate* was wrong or the *day* was —
and adjusts the estimate, out loud. When a topic proves harder than predicted, the
plan changes. A plan that never learns from reality is decoration.

### Across all of it — many goals at once
You will always have more than one thing going: build this repo, prepare for that
interview, revise DSA. The mentor holds them together, decides how a day's hours
split between them, and shows you the trade.

**One schedule serving several roadmaps is the hard part, and the reason this
system exists.** A single-roadmap planner is a to-do list; the value appears when
competing goals have to share a finite day honestly.

---

## 5. The principles — non-negotiable

These are the difference between this product and a wrapper around a model.
Violating one is a defect, not a style choice.

1. **Never fabricate.** Every claim about your calendar, streak, roadmap or memory
   comes from reading a real file. If the data is missing, say so plainly. A
   missing answer is fine; an invented one is not.
2. **Code owns the arithmetic.** Streaks, free windows, prerequisite unlocking,
   budget trimming — computed in code. The model reasons *with* those numbers and
   never calculates them.
3. **Evidence before claims.** If it says you already know something, it can point
   at why. If it says a topic takes three hours, it says that is an estimate.
4. **The pen stays yours.** Calendar writes are proposed first. Code changes come
   as a diff you apply. It drafts; you decide.
5. **Local-first.** Your memory, your goals, your confusion — on your machine, in
   files you can read and edit.
6. **Fail open, never silently.** A broken tool degrades the answer and says so.
   It must never crash a turn, and never paper over a failure.
7. **Honest about limits.** When it could not read something, skipped a directory,
   or is unsure — it says so. Naming the edges is what makes the rest trustworthy.

---

## 6. What the product is, concretely

| Aspect | Decision |
|---|---|
| **Form** | A local agent you run on your machine. Chat is the interface |
| **Runtime** | PI agent harness (TypeScript); the mentor is its extensions + skills |
| **Memory** | Plain files under `data/` — `profile.yaml`, `memories.jsonl`, episodes, the day grid. Readable and editable by hand |
| **Curriculum** | `learning/topics/<name>/roadmap.yaml` plus one note per topic, in-repo and git-tracked |
| **Models** | A strong converser for the mentor's voice; a fast tier for classification (crisis detection) |
| **Proactive behaviour** | A scheduled run (cron) for nightly summaries and nudges — not a daemon |
| **UI** | None yet. A dashboard is a later, optional view over the same files |

## 7. Deliberately out of scope

Naming these keeps the goal from drifting:

- **Not a chatbot wrapper.** If it cannot ground a claim in a file, it does not
  make the claim.
- **Not a course platform.** No content library. It reads *your* material and
  generates what *you* are missing.
- **Not a cloud service.** No accounts, no sync, no telemetry.
- **No auto-applied code edits.** Diffs only. Writing is where you learn.
- **No auto-posting.** Drafts for LinkedIn or applications are returned, never
  published by the system.
- **No guilt mechanics.** No streak-shaming, no manufactured urgency, no fake
  deadlines. If a plan fails, the plan was wrong.

## 8. Career work — adjacent, deferred

The original design included a job hunter (resume tailoring, application pipeline)
and a LinkedIn writer. They serve the same person and belong to the same product
eventually, but they are **not part of the loop in §4** and are not being built
now. Keeping them out is deliberate: the learning loop has to work before the
product has a second module.

---

## 9. How we will know it works

One end-to-end journey, judged as a whole:

> From a cold start, you tell the mentor ten things about yourself. **It remembers
> them next week.** You point it at a repo. It reads the actual code and produces
> concepts with citations you can check. It marks two as already-known **and says
> why**. You get per-topic hours and a total. It challenges one estimate that
> looks wrong. It proposes week one on your calendar with reasons; you approve; the
> blocks appear. A second roadmap for DSA exists beside it, and one schedule serves
> both. You miss a block, and it asks about the estimate rather than silently
> re-planning.

If that journey is not fluent, the product does not work yet — no matter how many
other features exist.

---

## 10. Open decisions

Recorded here so they are not silently assumed. Each one changes the shape of the
product.

| # | Question | Leaning | Status |
|---|---|---|---|
| **D1** | Is a "gap" measured against this artefact only, or against a declared **target** (e.g. "ready for a DSA interview")? | Against a declared target, per roadmap | **open** |
| **D2** | Does "verify the estimate" mean *is the estimate sane*, *do you have the time*, or both? | Both, as two separate checks | leaning |
| **D3** | How much calendar autonomy: propose-then-confirm, standing consent, or write-then-review? | Propose-then-confirm, with standing consent available | leaning |
| **D4** | Are there real deadlines to plan against? | If yes, count backwards; if no, split by priority | **open** |
| **D5** | How much of a day may it claim? | A budget you set (e.g. 2h/day), never every free window | leaning |
| **D6** | One roadmap artefact or several? | Several files, one shared schedule | leaning |
| **D7** | When you fall behind? | Ask: was the estimate wrong, or the day? | leaning |
| **D8** | Does *"build it myself"* mean rebuild this exact repo, build something equivalent, or go deep on part of it? | Build something equivalent | **open** |

The three marked **open** (D1, D4, D8) are the ones where a wrong assumption costs
the most: they decide what the roadmap is measured against, whether time pressure
exists, and how large the whole thing is.