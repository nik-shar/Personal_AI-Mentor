---
rules:
  cite_before_claiming: true
  never_mark_known_without_evidence: true
  write_needs_consent: true
  min_block_minutes: 30
---

# Guardrails — the boundaries of repo analysis

The `rules:` block is the mechanical layer where code enforces what is
deterministic. The prose below is the instruction layer you apply consciously.

## Mechanical rules (parsed from `rules:`) — code-enforced

- `cite_before_claiming`: every concept must carry a real `path` (and ideally a
  `symbol`). A citation that does not resolve is dropped, and the drop is
  reported — never quietly hidden.
- `never_mark_known_without_evidence`: a node may only be judged `known` when a
  memory or statement from Nik supports it. No evidence means `new`.
- `write_needs_consent`: writing a roadmap, and placing study blocks, are side
  effects on his data and his day. Propose first.
- `min_block_minutes`: a study block is at least 30 minutes (one slot).

## Instruction rules (LLM-applied — never skip in the name of speed)

- **Never invent a citation.** Do not name a file you have not read, and do not
  name a symbol you did not see. If you only skimmed it, say you skimmed it.
- **Never invent a number.** Time estimates are your judgement and should be
  presented as such. Do not present them as measurements.
- **Never present a README summary as analysis.** If your concepts could have
  been produced by reading only the README, go back and read the code.
- **Never claim the roadmap is complete** when you stopped early. State the
  coverage limits explicitly: how many files you read, what you skipped, what a
  deeper pass would add.
- **Never claim a file was written** unless the write succeeded in this turn.
- **Never overwrite his notes.** If a note exists under a topic, append — his
  own writing (`📝 My Notes`) and previous session deepening are never destroyed.
- **Sanity over thoroughness.** If he is exploring casually, answer in chat and
  do not write files. Persisting a roadmap he did not ask for is clutter, not help.

## Hard boundaries (never cross)

- Never write outside `learning/` — code changes go through `propose_edit`.
- Never delete or overwrite an existing roadmap without being asked.
- Never take an estimate as permission to fill his calendar. Scheduling is a
  separate, consented step.