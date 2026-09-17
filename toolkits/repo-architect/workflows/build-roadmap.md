---
description: Write the roadmap as a real file — nodes, estimates, citations, days — and state the total honestly.
---

# Build Roadmap

Write it down. A roadmap that lives only in chat is gone when the session ends,
and there is nothing to mark progress against.

## Where it goes

```
learning/topics/<roadmap-name>/
├── roadmap.yaml          the structure — authoritative
├── <Topic>.md            one note per node
└── <Title> Roadmap.md    the index
```

You may write here directly (`learning/` is the one place your `write` tool is
allowed). Nothing outside it.

## roadmap.yaml

```yaml
schema_version: 1
graph_id: repo_<name>
title: <what this roadmap teaches>
source:
  kind: repo
  path: /path/to/repo
  commit: <git sha if you have it>
  stopping_rule: comprehension      # or: authorship
stopping_rule: comprehension
target_days: 14
hours_per_day: 2.5
nodes:
  - id: tn_<short_slug>
    title: <the concept, in the repo's vocabulary>
    status: not_started             # known nodes: status: skipped
    estimated_hours: 3.0
    content_type: conceptual        # conceptual | algorithmic | hands_on_code | reference
    prerequisites: [tn_other_id]
    anchors:
      - kind: file                  # or: symbol
        path: src/thing.py
        symbol: the_function
        note: why this is where the concept lives
    notes: >
      What to understand here, and why it matters. Concrete, in the repo's words.
```

Rules that matter:

- **`prerequisites` are node ids**, and the graph must stay acyclic. If you find
  a cycle, one of those edges was invented — remove it.
- **`estimated_hours` per node.** These are your predictions. Say so when you
  present them.
- **`anchors` must resolve.** Only cite paths you actually opened. A citation
  that doesn't resolve is worse than none, because it looks checkable.
- **Nodes judged `known` in `assess-gaps` get `status: skipped`** and a one-line
  reason in `notes`. That is how the frontier advances — the tooling computes
  what's unlocked from prerequisites, so skipping is what makes the next node
  available.
- **`stopping_rule`**: `comprehension` if he wants to read and work in the repo;
  `authorship` if he wants to rebuild it. **These produce very different
  curricula** — authorship is far deeper. If it's ambiguous, ask.

## The notes

One per node, and they are where the learning actually happens. Each note:

- leads with **why this matters in this repo**
- points at the anchors — "read `store.py::get_embed_model` first, the comment
  explains the constraint"
- carries a **checkpoint**: the question he should be able to answer when done
  ("why does a second embedding implementation degrade recall *silently*?")

Never overwrite a note that already exists. Append, and never touch a
`📝 My Notes` section — that is his.

## The index

`<Title> Roadmap.md` lists the nodes in dependency order with their day, hours,
and status. This is the page he actually opens each morning.

## Present the total honestly

```
AI Mentor repo — 11 concepts, 22.5h (after 3 already known)
  Day 1  ·  3.0h  The single-writer boundary           · diff 4
  Day 2  ·  2.5h  Fail-open tool contracts             · diff 3
  ...
  Coverage: read 41 files of 335; skipped web/ and pi/ (vendored).
  A deeper pass would add: <name the gaps>.
```

Then state the limits: how many files you read, what you skipped, what you're
unsure about. **A roadmap that names its own edges is trustworthy.** One that
pretends to be complete is not.

## Then offer to schedule it

Do not place it on his calendar without asking. That is the next workflow, and
it is a separate decision.