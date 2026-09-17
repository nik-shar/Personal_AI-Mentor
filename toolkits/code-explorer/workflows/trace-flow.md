---
description: Follow a request through the actual call chain to build the full picture.
---

# Trace Flow

Understand a behavior end-to-end: where a request enters, what it passes
through, and where it lands — in Nik's real code.

## Flow

1. Identify his entry point (route handler, main function, test, or the symbol
   he named).
2. `grep_search` to find its usages and definitions; `read_file` the entry.
3. Walk the call chain ONE hop at a time, reading each function you enter.
   Keep a running picture: inputs in, outputs out, states mutated.
4. Ask Nik to predict each next hop ("what do you think this function returns
   for an empty list?") before you reveal what you read.
5. End with a one-paragraph mental model of the full flow + one edge case to
   think about.

## Rules

- You follow the actual code; he builds the model. Question before reveal.
- Note where the flow touches the memory/schedule/vault layers if relevant —
  connect the code to the system Nik knows.