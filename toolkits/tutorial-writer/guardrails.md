---
rules:
  min_length: 800
  min_headings: 2
  banned_placeholders: ["...", "TODO", "TBD", "lorem"]
---

# Guardrails — the quality standard every note must meet

The YAML `rules:` block above is the MECHANICAL layer: code parses it and
enforces it deterministically (a failing note is rejected and the exact
violation is fed back for revision). The prose below is the INSTRUCTION
layer: the writer must apply it consciously on every draft.

## Mechanical rules (parsed from `rules:`) — code-enforced

- `min_length`: a note shorter than this is a stub, not a tutorial.
  (Default 800 chars — honest floor, not a target.)
- `min_headings`: at least 2 real markdown headings. One wall of text is
  not teachable.
- `banned_placeholders`: `...`, `TODO`, `TBD`, `lorem` — lazy truncation is
  a rejection every time. Write every section in full or do not write it.

## Instruction rules (LLM-applied — never skip in the name of brevity)

- **No fabricated facts.** Every example, number, benchmark, or API must be
  real and sourced from the research step. External stats from other results
  never appear as the learner's own achievements.
- **Complete runnable examples** for hands-on-code topics: full imports,
  type hints where helpful, error handling, and a note on what "it works"
  looks like.
- **Concrete practice** closes the note: a scoped task with input/output
  requirements (hands-on), a practice problem with difficulty (algorithmic),
  or a reflection prompt (conceptual).
- **Prerequisites are links, not guesswork** — `[[wiki-links]]` to notes
  that actually exist in the graph.
- **Never pad.** A note is finished when the topic is correctly covered,
  not when a length target is hit.

## Hard boundaries (never cross these, in any mode)

- Never write outside the vault's topic folder.
- Never delete or overwrite an existing note without being asked to.
- Never claim a tutorial is complete when a section is still a stub.
