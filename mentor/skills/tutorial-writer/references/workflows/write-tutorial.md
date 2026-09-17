<!-- AUTO-GENERATED from toolkits/tutorial-writer/workflows/write-tutorial.md -->

# Write Tutorial

The core craft: one note, one subtopic, built to teach.

## Flow

1. **Open with the learner's gap.** If mentor guidance names the weakness
   (a derivation step that failed, an interview round that shook confidence),
   the very first sections explain THAT step — slowly, line by line, with
   why done wrong and how it goes right. Never bury the hard part.
2. **Follow the content-type structure** for the subtopic:
   - *conceptual*: analogy → precise definition → tradeoffs/comparisons →
     common misconceptions → reflection prompt.
   - *algorithmic*: intuitive framing → brute-force → key insight → optimal
     approach with code/pseudocode → complexity analysis → pitfalls →
     practice problem with difficulty.
   - *hands-on_code*: analogy → complete runnable example (imports, type
     hints, error handling) → production pitfalls → 20–40 min challenge
     with concrete input/output.
   - *reference*: what it is / when to reach for it → comparison table →
     canonical minimal usage → quick pitfalls. Concise is a feature here.
3. **Weave in the learner world.** Use profile context (skills, path, tech
   background) to make every example land — your reader is one person.
4. **Keep every claim sourced.** Research findings drive the specifics;
   anything unverifiable gets an honest `> ⚠️ verify` marker.
5. **Close with a move-forward task.** Practice problem, challenge, or
   reflection prompt — the note is finished when the learner has something
   to *do*, not just something to read.

## Rules

- Everyday sections are written in full; placeholders are rejections
  (see guardrails).
- Prerequisites use `[[wiki-links]]` to real notes only.
- Length follows the topic, not a template; honest floors are enforced by
  code, not gamed by padding.
