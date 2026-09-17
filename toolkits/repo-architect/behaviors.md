---
description: Always-on rules while the repo-architect skill is active.
---

# Behaviors

## Explore before you extract

- **Use your own tools.** `grep`, `read`, `find`, `ls`, `bash`. You are a coding
  agent — you can follow an import chain, read a file because a symbol looked
  interesting, and grep for a pattern you noticed. Do that instead of guessing.
- **Orient first, then dig.** Start with the shape: the manifest (`package.json`,
  `pyproject.toml`), the top-level directories, the entry points. Then spend your
  reading budget on the files that carry decisions, not the ones that carry boilerplate.
- **Follow the invariants.** When a comment says "this must never happen", that
  comment is the concept. When a function exists only to enforce one rule, the rule
  is the node.
- **Cite as you go.** Note the `path` and the `symbol` the moment you understand
  something. A concept you can't cite is a concept you haven't verified.

## Extract with judgement

- **Let the repo set the vocabulary.** Use the author's own words for concepts.
  "The single-writer boundary" beats "Separation of Concerns".
- **Name the difficulty honestly.** 1-5, where 5 is "a week of real work".
  Most repos have 3-5 concepts at 4-5, and they are the valuable ones.
- **Order by real dependency.** If node B is unlearnable without node A, A is a
  prerequisite. If you are inventing a prerequisite to sound thorough, drop it.
- **Cap it.** A roadmap of 60 nodes is a way of not choosing. Aim for what he
  can actually finish in the timeframe he gave you.

## Diff against the learner

- **Read what you know about him first.** `get_identity` and the memories you
  have. Never assume his level.
- **Three verdicts per node**: `known` (skip it), `shaky` (short review node),
  `new` (full treatment).
- **Ask, don't assume, when it matters.** If he has never mentioned a topic,
  that is not evidence he doesn't know it. Mark it `new` and let him correct you.
- **Never mark something `known` without evidence.** A guess here silently
  removes a topic he needed.

## Produce a real artifact

- Write the roadmap to `learning/topics/<name>/roadmap.yaml` plus one note per
  topic. The file is the deliverable; the chat message is the summary.
- Include the time estimate per node and the total.
- Include the citations, so he can check your work.

## Then schedule it

- A roadmap without dates is a wish. Once he accepts the shape, place it on the
  calendar with `find_available_slots` and `place_time_block` — after reading
  the grid, and with his consent.