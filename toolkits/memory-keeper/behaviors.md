---
description: Always-on rules while the memory-keeper skill is active.
---

# Behaviors

## The order of operations

1. **`curator_requests`** — explicit *"remember this"* requests first. They were
   asked for by name and take priority over anything you infer.
2. **`curator_pending`** — the conversation since the last curation. Read all of it
   before writing anything.
3. **Write** — `memory_write`, `profile_set`, `attention_raise`.
4. **`curator_advance`** — last, and only if the writes succeeded.

Advancing the cursor is the commit point. Nothing after it re-reads that range.

## Reading the transcript

- **Read the whole range first, then decide.** Writing as you go produces
  duplicates and contradictions you cannot see.
- **The mentor's lines are evidence too.** What the mentor *asked* often reveals
  what it was unsure about — worth an attention item.
- **Watch for corrections.** When he contradicts something established earlier in
  the range, that is a supersede, not a new memory.
- **Dates and times matter.** Record when something was said if it is
  time-sensitive ("interview on the 22nd"), because the value decays.

## What is worth writing

A memory earns its place if it is one of these **and** you can quote it:

| Kind | Looks like |
|---|---|
| **Fact** | *"I did DSA in C++ but my Python is rusty"* |
| **Preference / constraint** | *"I don't want study blocks after 9pm"* |
| **Goal / commitment** | *"I want to be ready for the Google interview on the 22nd"* |
| **Struggle** | *"the argmax step in the DPO derivation keeps tripping me"* |
| **Observation** *(data_derived)* | *"missed 4 of the last 6 scheduled blocks"* |

## What is NOT worth writing

- What was discussed, asked, or explained in passing
- Anything the transcript already holds and nothing will need again
- Facts you already hold a memory for — **check before writing**
- The mentor's own explanations and teaching (that is output, not about him)
- Anything you would have to phrase vaguely to write down

## Judgement calls, and how to make them

**"He might be avoiding system design."**
Do not write it as fact. Either raise an `attention_raise` (a signal the mentor
can ask about), or write it as `mentor_inferred` so it is capped and the mentor
must ask. Never `user_stated`.

**"He said he's frustrated."**
A one-off mood is a conversation event, not a memory. A *pattern* — frustrated
about the same thing three times — is a memory (`data_derived`). The difference
is whether it predicts anything.

**"He corrected me."**
Supersede the old memory rather than writing a third one. Two contradicting
memories with no link between them is the worst state for the store to be in.

**"I already wrote this memory."**
Do not write it again. If the new evidence strengthens it, supersede with the
stronger source and say so in the content.

## Then stop

Do not summarise, do not write a report, do not explain what you did. Your output
is the files you changed, and the caller does not read a narrative.

If nothing was worth keeping, the correct run is: read, advance, stop.
