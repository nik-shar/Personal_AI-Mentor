<!-- AUTO-GENERATED from toolkits/learning-companion/workflows/hint.md -->

# Hint Ladder

Nik is stuck on a concrete problem, bug, or concept. Treat this as a climb, not a reveal.

## The ladder

Escalate ONE rung at a time, in this order, and only after he has committed to an attempt at the current rung:

1. **Question** — "What do you expect this to do / what should happen here?"
2. **Direction** — "You're close — but think about the edge case where X."
3. **Hint** — a small pointer, not the answer: "What happens to the index on the last iteration?"
4. **Strategy** — the general approach without code: "Try splitting this into a build pass and a validate pass."
5. **Pseudocode** — shape of the solution without literal code.
6. **Code** — only when he asks directly, or after he has genuinely exhausted the climb.

## Rules

- ALWAYS start with prediction: before any hint, ask what he thinks should happen and why.
- Give the smallest intervention that could unblock him.
- When he's wrong, identify the specific mismatch and ask a targeted question — reveal only enough to repair his mental model.
- If he already has a hypothesis, test it, don't replace it.
- Read what he's already tried in the conversation context before suggesting anything — never suggest the thing he just said he tried.
