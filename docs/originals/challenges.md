# challenges.md — LinkedIn Writer: Bugs, Investigations & Fixes

A living engineering journal. Every non-trivial bug gets an entry: what we saw,
why it was happening, how we found the root cause, what didn't work, and what
finally fixed it.

---

## Bug 1 — Revision loop wasn't actually revising

### What was it?
The critic sent back feedback. The draft writer ran again. The new draft was
byte-for-byte identical to the previous one. All three revision attempts produced
the same text, as if the critic's feedback had never happened.

### Why did it happen?
The critic's feedback was appended to the *human turn* as an extra paragraph
at the bottom of a long context block that already contained topic, goal,
audience, style notes, and the previous draft. The model was reading it, but
its attention was effectively dominated by all the content that came before it.
On a revision pass, the human message looked like:

```
Topic: ...
Goal: ...
...style notes...
...previous draft...

Critic says: [feedback buried here]

Write an improved version.
```

The model treated the revision as a gentle suggestion on top of an already-long
prompt, not as a mandatory directive it had to act on.

### What made us see the bug?
Running the agent end-to-end and printing the raw state after each node. The
`draft` field was identical across all three `revision_count` increments.
The critic was clearly unhappy (feedback was non-null each time), but the draft
never changed.

### What we tried
1. Moving the feedback text to the end of the human message — didn't help,
   same problem.
2. Adding "YOU MUST ADDRESS ALL OF THIS" in caps around the feedback — mild
   improvement, not reliable.

### What got us success
Injecting the critic feedback as its **own dedicated second system message**,
separate from the writer's main system prompt. System messages have higher
implicit weight in chat-format models than human-turn content. Making it
impossible to miss:

```python
messages = [
    ("system", WRITER_SYSTEM_PROMPT),
    ("system", f"CRITIC FEEDBACK — you MUST address ALL of the following before writing:\n{critic_feedback}"),
    ("human", human_prompt),
]
```

Combined with a `[REVISION N]` prefix on the human turn so both the model
and the logs make it clear this is a retry, not a fresh attempt. After this
change, every revision produced a materially different draft that addressed
the specific issues raised.

---

## Bug 2 — `max_length` constraint was decorative

### What was it?
The task was given a `max_length: 50` constraint. The agent ran, the critic
said "APPROVED", and the result came back as a 400-character post. The length
limit had been silently ignored.

### Why did it happen?
Two compounding failures:
1. The `max_length` value was never surfaced to the draft writer — the system
   prompt said "aim for 150–200 words" unconditionally, so the model had no
   idea the constraint existed.
2. Even when the critic was told about the limit, LLMs are notoriously bad at
   counting characters precisely. The critic approved drafts that were over
   the limit because it estimated, not counted.

### What made us see the bug?
Deliberately setting `max_length: 50` (an intentionally impossible constraint
for this style guide, but useful for testing the enforcement path) and seeing
the result come back at 400+ characters with `critic_approved: True`.

### What we tried
1. Adding "keep it under N characters" to the human turn — the model
   interpreted this as a soft preference, not a hard limit.

### What got us success
Two separate fixes working together:
- **Draft writer awareness**: inject the limit into the system prompt from
  token one so the model writes to the constraint, not toward it:
  ```
  HARD LIMIT: the final post must be {max_length} characters or fewer
  (not words — characters). Count carefully before finishing. If over
  the limit, cut ruthlessly.
  ```
- **Programmatic enforcement in the critic node**: after the LLM review,
  the code itself checks `len(draft) > max_length`. If violated, it
  appends a `[CONSTRAINT VIOLATIONS — MUST FIX BEFORE APPROVAL]` block
  and overrides any LLM "APPROVED" verdict. The LLM is not trusted to
  count characters.

The test case (50-char limit) correctly exited after 3 revisions with the
revision cap — the constraint was mathematically incompatible with the style
guide's requirements (first-person narrative + line breaks can't fit in 50
chars). That 3x-cap exit was the **correct failure mode**, not a bug.

---

## Bug 3 — Draft writer fabricated a credential

### What was it?
The draft cited "PageIndex's 98.7% FinanceBench benchmark score" as a fact
about Nikhil's own project. This score was from raw research notes about a
*different tool* entirely. The model had paraphrased an unverifiable aggregate
statistic from Tavily search results and presented it as a first-person claim.

### Why did it happen?
The research node was passing raw Tavily JSON directly into the draft writer's
context. Tavily results are noisy — they include snippets from comparison
articles, vendor marketing, and survey summaries. The draft writer had no
instruction distinguishing "facts you may cite as your own" from "background
context you found on the internet." It treated all research notes as equally
citable.

### What made us see the bug?
Reading the actual draft output carefully. The benchmark number was suspicious
— too precise, too impressive. Cross-referencing with the raw research notes
revealed it came from a third-party tool's benchmark, not Nikhil's project.

### What we tried
1. Telling the model "only use verified facts" — too vague, the model still
   paraphrased from research notes.

### What got us success
Two targeted fixes:
- **Distillation step in `web_search_node`**: instead of dumping raw Tavily
  JSON at the draft writer, a separate LLM call first extracts only
  "concrete, attributable facts with a clear source." Vague snippets and
  ad copy are stripped before the draft writer ever sees the notes. This
  made the research notes auditable.
- **Explicit prohibition in the writer's system prompt**:
  ```
  NEVER cite external surveys, studies, or reports. Only use facts from the
  distilled research notes below or the user's own project details. If no
  research note supports a claim, do not make it.
  ```
  The critic was also told to flag "any claim citing a survey/study/report
  not in the research notes" as a numbered issue.

---

## Bug 4 — `avoid_topics` lexical matching didn't fire on natural language

### What was it?
The `avoid_topics` constraint used snake_case tokens like `'traditional_ml'`.
The literal substring check against the draft looked for `"traditional_ml"` —
which never appears in natural prose. "Traditional machine learning" or
"classical ML approaches" would sail through undetected.

### Why did it happen?
The constraint was designed as a machine-readable token for the schema
(`avoid_topics: ["traditional_ml"]`), but the enforcement was a raw substring
match against the generated text. Natural language and config tokens don't
share vocabulary.

### What made us see the bug?
Writing a test prompt that should have triggered the constraint and watching
it pass through with a CONSTRAINT VIOLATIONS block that showed zero violations
despite the draft containing the topic.

### What we tried
1. Plain substring match on the raw token — never matched real prose.

### What got us success
A two-layer defence:
- **Humanization**: convert snake_case tokens to readable phrases before
  any matching (`'traditional_ml'` → `'traditional ml'`). The lexical
  fallback now checks both the raw token AND the humanized phrase.
- **Semantic LLM check in the critic prompt**: the critic is explicitly
  asked to check whether the draft discusses any forbidden topic
  "even indirectly or by paraphrase." This catches `"classical ML approaches"`
  and `"conventional machine learning"` — things no substring check can catch.
  The lexical fallback is the safety net if the LLM misses it.

---

## Bug 5 — Silent LLM failures were invisible in the AgentResult

### What was it?
If the LLM call inside `draft_writer_node` threw an exception (network error,
rate limit, bad response), the node would:
- Print a `print()` to the console.
- Silently return `previous_draft or ""` — either an unchanged old draft or
  an empty string.

The `AgentResult` would come back as `status: "success"` with content that was
either stale or blank, with no indication anything had gone wrong.

### Why did it happen?
The initial exception handler was written with "fail safe" logic — keep the
pipeline moving rather than crashing. The intent was correct, but it made a
broken LLM call indistinguishable from a genuine successful generation.

The same problem existed in `critic_node`, where an exception would silently
set `critic_approved: True` — meaning a failed critique would let any draft
through unchecked.

### What made us see the bug?
Code review against the CLAUDE.md design principle: *"Constraints are enforced
programmatically — silent failures aren't acceptable."* Reviewing the exception
handler made it obvious that a console `print` is ephemeral and leaves no
trace in the returned `AgentResult`.

### What we tried
1. Logging the error to a file — captures it but still doesn't surface it
   to the orchestrator.

### What got us success
Explicit failure tagging at every exception site:

**In `critic_node`**: on exception, return the auto-approve but embed the
failure in the feedback string so it flows into the `AgentResult`:
```python
return {
    "critic_approved": True,
    "critic_feedback": f"[AUTO-APPROVED: critic LLM call failed — draft was not reviewed. Error: {e}]",
}
```

**In `draft_writer_node`**: distinguish two cases:
- If a prior draft exists (failure on a *revision* pass): keep the old
  draft, don't increment revision count (the failed attempt doesn't count
  as a valid try).
- If no prior draft exists (failure on the *first* attempt): tag the draft
  explicitly and force `revision_count = MAX_REVISIONS` so the critic loop
  bails immediately rather than retrying three times on a broken state:
  ```python
  draft = f"[DRAFT_FAILED: LLM call failed on attempt {revision_count + 1} — {e}]"
  return {"draft": draft, "revision_count": MAX_REVISIONS}
  ```

The `build_result()` function then checks for the `[DRAFT_FAILED` prefix and
sets `status = "failed"` instead of `"success"`.

---

## Bug 6 — `status: "success"` even when the critic never approved

### What was it?
When the revision loop hit `MAX_REVISIONS` without the critic ever approving
the draft, `build_result()` still returned `status: "success"`. The orchestrator
(and any human reading the result) had no way to tell a clean critic-approved
pass from a "gave up after 3 tries" exit.

### Why did it happen?
`build_result()` in `State.py` hardcoded `status="success"` unconditionally.
This was written early in development when the focus was on getting the loop
to run at all — the status field was a placeholder.

### What made us see the bug?
The CLAUDE.md open-questions section called it out explicitly:
> *"considering PARTIAL instead so downstream consumers can tell 'clean pass'
> apart from 'gave up after 3 tries.'"*

And the 50-char test run confirmed it: the loop correctly exited after 3
revisions (the right behaviour), but the result said `SUCCESS`, which was
misleading.

### What we tried
N/A — once the root cause was clear (a hardcoded string), the fix was direct.

### What got us success
Deriving `status` from the actual critic outcome in `build_result()`:

```python
if content.startswith("[DRAFT_FAILED"):
    status = "failed"
elif critic_approved:
    status = "success"
else:
    status = "partial"   # revision cap hit, best effort
```

Also added `"critic_approved"` to `DraftSuggestion.metadata` so the
orchestrator has full visibility without needing to parse the status string.

**Confirmed working** — the real-world run (2026-07-06, `max_length: 500`,
topic: vectorless RAG) returned:
```
status: SUCCESS
critic_approved: True
revision_count: 1
char_count: 428
```
Exactly the expected outcome: one clean revision, under the limit, critic satisfied.

---

*Last updated: 2026-07-06*
