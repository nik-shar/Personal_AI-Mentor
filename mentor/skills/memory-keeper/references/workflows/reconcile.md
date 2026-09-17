<!-- AUTO-GENERATED from toolkits/memory-keeper/workflows/reconcile.md -->

# Reconcile

Reached from `curate` step 3 when something he said conflicts with a memory you
hold. Getting this wrong leaves the store holding two contradicting claims with no
link between them — the worst state it can be in.

## The rule that decides it

**His words win over your inference. Every time.**

- A `user_stated` memory is never contradicted by a `mentor_inferred` one. He said
  it; you guessed. You lose.
- A `data_derived` memory (computed from logs) *can* be corrected by him — he knows
  things the logs do not. If he says the misses were cancellations, that is the
  truth and the pattern was wrong.
- Two `user_stated` memories that conflict means he changed his mind, or he was
  imprecise the first time. The **newer** one wins, and say so in the content.

## How to resolve

1. **Find the memory you are correcting** — `grep` the id or content in
   `data/memories.jsonl` so you supersede the right row.
2. **Write the correction** with a `source` at least as strong as the old one, and
   a quote from the new statement.
3. **Call `memory_supersede`** with `old_id`. The old row stays; a pointer records
   the replacement.
4. **If the contradiction itself is interesting**, raise it — the mentor may want
   to ask which is true:

```
attention_raise kind=contradiction
  what: "He says he is fluent in Python; an earlier memory says his Python is rusty."
  evidence: "<the two quotes>"
  suggestion: "Worth checking which is current before planning anything Python-heavy."
```

## What not to do

- **Do not delete the old memory.** There is no mechanism, and there should not be.
- **Do not write a third memory and leave both old ones active.** That is how the
  store accumulates contradictions.
- **Do not silently pick a side.** The supersede is the record that a change
  happened and when — which is often more useful than either claim alone.
- **Do not raise attention for every correction.** A routine "he changed his mind"
  is handled by the supersede. Raise it only when the *disagreement* matters.
