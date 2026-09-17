<!-- AUTO-GENERATED from toolkits/repo-architect/workflows/assess-gaps.md -->

# Assess Gaps

The step that makes a roadmap *his* rather than generic. Skip it and you hand him
"Python Basics" when he has been writing Python for years.

## 1. Read what you know about him — first, always

- Call `get_identity`. It returns structured facts **and** what you learned in
  conversation, each with provenance and a confidence.
- Call `recall_memories` with a query built from the concept list, so you pull
  the memories that actually relate (e.g. "DSA C++ Python, algorithms,
  interview preparation").
- Read `learning/` — his existing roadmaps and the nodes already marked `done`
  are direct evidence of what he has covered.

**Never assume his level from his job title or a single sentence.** He may have
told you something three sessions ago that contradicts your assumption.

## 2. Classify every concept

| Verdict | Meaning | What the roadmap does |
|---|---|---|
| `known` | he can explain it and use it | **skip** the node entirely |
| `shaky` | he's seen it, needs review | short review node, low hours |
| `new` | not covered, or unclear | full node, real hours |

## 3. The evidence rule (this is enforced, not optional)

A `known` verdict **must cite its source**: a memory, a profile fact, or his own
statement in this conversation.

- ✅ "C++ DSA — he stated it; `dna_memory` fact, user-stated, conf 0.95."
- ✅ "Python dict/list comprehensions — he's used them in his own code this session."
- ❌ "He's an AI engineer so he probably knows Python." ← **not evidence**

**No evidence means `new`.** When unsure between `known` and `new`, choose `shaky`
— it costs him a short review, while a wrong `known` silently removes something
he needed.

## 4. Ask instead of guessing, when it matters

For any `shaky` verdict on a concept that is a prerequisite for several others,
ask him directly — one question, not five:

> "You mentioned DSA in C++ — how comfortable are you with Python's collections
> and generators? That decides whether two nodes stay in the plan or become a
> quick review."

His answer is better evidence than any inference you could make, and it takes
one line of conversation.

## 5. Report the delta honestly

The output of this step is a diff, and it is the single most motivating thing
you can show him:

```
Roadmap: 14 concepts, 33.5h total
  known   (skip)  : 3  — 6.5h removed   ← with evidence cited
  shaky   (review): 4  — 8.0h → 3.5h
  new     (full)  : 7  — 19.0h
  ─────────────────────────────────────
  Adjusted plan   : 11 nodes, 22.5h
```

If nothing came back `known`, say so plainly. It is a real answer, and it is
much better than inventing a skip to look efficient.

## 6. Watch the two failure modes

- **Over-skipping.** Marking things `known` to make the roadmap look tight.
  You are removing his learning, silently. When in doubt, keep the node.
- **Under-skipping.** Making him re-learn what he knows. That is how a roadmap
  loses credibility in the first week. If he told you he knows it, skip it.

Then go to `build-roadmap`.
