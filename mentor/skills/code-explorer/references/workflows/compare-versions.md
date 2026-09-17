<!-- AUTO-GENERATED from toolkits/code-explorer/workflows/compare-versions.md -->

# Compare Versions

Nik wants to understand a change — his own or upstream — and its consequences.

## Flow

1. `git_log` to see the commit he means; `git_diff` to read the change.
2. Identify: WHAT changed, WHEN, and (from the message/history) WHY.
3. Help him read the diff in chunks — one hunk at a time, prediction-first:
   "what behavior do you think this hunk changes?"
4. Connect the change to consequences: which callers are affected, what tests
   cover it, what could break.
5. End with the one question that matters most about the change ("is this
   behavior you want to keep?") and whether a test covers that intent.

## Rules

- Diff-hunk teaching is the objective, not a prose review of the commit.
- If the work is uncommitted (`git_diff` shows it), frame it as "what you have
  in hand" rather than "what changed" — same read, honest framing.
