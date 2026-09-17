<!-- AUTO-GENERATED from toolkits/repo-architect/workflows/extract-concepts.md -->

# Extract Concepts

An inventory, not an essay. Each entry is one concept Nik would have to
genuinely understand.

## The one question that filters everything

> **Would this take Nik a week to figure out alone, or five minutes?**

Keep the week-long ones. That single question removes almost every bad concept.

## Banned shapes

These are not concepts. If one appears, replace it or drop it:

- A **technology name**: "FastAPI Integration", "LangGraph State Management",
  "TypeScript and Python Interoperability". He can learn FastAPI from its docs;
  it is not what makes *this* repo hard.
- A **topic label**: "Memory Management", "Error Handling", "Data Validation".
  These describe a category, not a decision.
- A **verb phrase with no claim**: "API Documentation and Usage" is not
  something to learn, it is something to read.
- Anything you could have written **from the README alone**.

## What a good concept looks like

A concept names a *decision and its consequence*:

- "Why the embedding dimension is fixed at 384 and shared by both stores —
  and how two implementations would degrade recall silently."
- "The single-writer rule: one runtime owns every store, so an invariant cannot
  live in two places."
- "Idempotence is owned by the store, not the caller — why re-running a job is
  safe."

Notice each one has: a specific mechanism, a reason, and a failure mode.

## For each concept, record

| Field | What it is | Rule |
|---|---|---|
| `title` | the concept, in the repo's own vocabulary | short, specific |
| `why_needed` | why he needs it at all | one line |
| `difficulty` | 1-5, where 5 = a week of real work | honest, not flattering |
| `estimated_hours` | your prediction | tie it to scope, not vibes |
| `prerequisites` | which other concepts must come first | real dependency only |
| `path` + `symbol` | where it lives | **must resolve against the real filesystem** |
| `content_type` | `conceptual` / `algorithmic` / `hands_on_code` / `reference` | picks the note's shape |

## Rate difficulty honestly

- **1** — familiar to anyone who codes; he'll skim it
- **2** — a known idea in an unfamiliar setting
- **3** — genuinely new mechanism, needs examples
- **4** — new mechanism plus interacting constraints; real study
- **5** — requires building something to understand; a week

If your estimates come out suspiciously uniform, you are not discriminating.

## Order by real dependency, not by importance

Ask: *could he understand B without A?* If no, A is a prerequisite. If the
answer is "he could, it would just be nicer", it is not a prerequisite — drop
the edge. Inventing prerequisites to look thorough turns a 10-node roadmap into
a 40-node one that never gets finished.

Keep the graph acyclic. If you find a cycle, one of those edges was invented.

## Aim for the timeframe

If Nik gave a timeframe (days, hours per day), size the graph to it. If he
didn't, ask — or default to something finishable (8-14 nodes) and say so.

**Do not pad to look comprehensive.** A short roadmap he finishes beats a
thorough one he abandons in week two.

## Coverage, stated plainly

End the extraction with:

- files read / files skipped
- concepts found and their total hours
- what a deeper pass would add (name it, so it's a known gap, not a silent one)

Then go to `assess-gaps`.
