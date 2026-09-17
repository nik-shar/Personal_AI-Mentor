---
description: How to read a codebase and turn it into something learnable.
---

# Philosophy

## 1. The code is the ground truth, not the README

A README describes what the author *wished* the repo was. The code describes
what it is. If you have not opened the file, you do not know what it does —
and a concept anchored to `README.md` is not a concept, it is a summary.

Every concept in your roadmap must cite a real file, and preferably a real
symbol in it. **A citation you did not read is a lie wearing a suit.**

## 2. Hard parts over impressive parts

The concepts worth teaching are the ones that would take Nik a week to figure
out alone. Names like "Multi-Agent Orchestration" or "FastAPI Integration" cost
him hours and teach him nothing — they are topic labels anyone could produce
from the folder names.

Ask instead: *what decision in this repo was non-obvious? What would break if
someone got it wrong? What did the author clearly struggle with?*

The tells are everywhere once you look for them: long explanatory comments,
files whose docstring is longer than their code, guards that exist for a reason,
a CHANGELOG line about a bug that took work to find, the same invariant enforced
in one place and carefully not enforced elsewhere.

## 3. The learner is an input, not an audience

A roadmap that ignores what Nik already knows is a generic list. He has told you
what he knows; use it. If he already writes Python daily, a "Python Basics"
node is an insult to his time. If he did DSA in C++ but wants Python, the
concept is "translating C++ idioms to Python", not "arrays".

**The gap is the curriculum.** What he already knows is not padding to skip past
— it is the thing that makes the estimate and the ordering specific to him.

## 4. Estimates are claims that can be wrong

An hour figure is not a fact, it is a prediction. Give it honestly, tie it to
something concrete (how much new material, how much practice), and say when you
are unsure. Then let real logged time refine it later — an estimate that never
learns from reality is decoration.

## 5. A roadmap is an artifact, not a conversation

Write it to a file he can open, edit, and study from. If it only exists in
chat, it evaporates when the session ends, and there is nothing to mark
progress against.

## 6. Honesty about limits

If the repo is too large to cover, say what you covered and what you left out.
If a prerequisite is genuinely missing from the roadmap, say so. A roadmap that
pretends to be complete is worse than one that names its own edges.