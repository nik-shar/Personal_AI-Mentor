---
created: 2026-08-22
tags:
  - bugs
  - fixes
  - engineering
---

# 🐛 Challenges & Fixes

> **Role:** Living engineering journal — every non-trivial bug gets an entry with what we saw, why, how we found it, and what fixed it.

---

## LinkedIn Writer Bugs

### Bug 1 — Revision loop wasn't actually revising

**Problem:** Critic feedback was sent back. Draft writer ran again. New draft was byte-for-byte identical. All 3 revisions produced the same text — as if the critic's feedback never happened.

**Root cause:** Feedback was buried at the bottom of a long human turn. The model's attention was dominated by all content before it.

**Fix:** Inject critic feedback as its **own dedicated second system message**:

```python
messages = [
    ("system", WRITER_SYSTEM_PROMPT),
    ("system", f"CRITIC FEEDBACK — you MUST address ALL: {critic_feedback}"),
    ("human", human_prompt),
]
```

Combined with a `[REVISION N]` prefix on the human turn. After this, every revision produced materially different drafts.

### Bug 2 — `max_length` constraint was decorative

**Problem:** Agent given `max_length: 50`. Result came back at 400+ chars with `critic_approved: True`.

**Root cause:** Two failures:
1. `max_length` never surfaced to the draft writer (prompt said "150-200 words" unconditionally)
2. LLMs can't count characters — critic estimated, didn't count

**Fix:** 
1. Surface limit in draft_writer prompt from the first attempt
2. **Programmatic enforcement** in critic: `len(draft) > max_length` → override LLM "APPROVED" verdict

### Bug 3 — Draft writer fabricated a credential

**Problem:** Draft cited PageIndex's 98.7% FinanceBench score (from research notes about a different tool) as if it were Nikhil's own result.

**Fix:** Explicitly forbid citation of external surveys/studies/reports in the system prompt. Research notes may only inform tone/context — concrete claims must come from the user's own project facts.

### Bug 4 — `avoid_topics` matching

**Problem:** `avoid_topics: ["traditional_ml"]` was silently ignored.

**Fix:** Two-layer enforcement:
1. Semantic LLM check in critic prompt
2. Lexical fallback on both raw and humanized (`snake_case` → space-separated) tokens

### Bug 5 — Silent LLM exceptions

**Problem:** LLM call failures in critic or draft writer would print to console but return `critic_approved: True` — the orchestrator had no visibility.

**Fix:** Explicit failure tagging:
- Critic: `[AUTO-APPROVED: critic LLM call failed — ...]`
- Draft writer: `[DRAFT_FAILED: ...]` + force `revision_count = MAX_REVISIONS` to exit immediately

### Bug 6 — `status: "success"` even when critic never approved

**Problem:** After hitting MAX_REVISIONS without approval, `build_result()` returned `status: "success"`. No way to distinguish clean pass from "gave up after 3 tries."

**Fix:** Derive status from critic outcome:
```python
if content.startswith("[DRAFT_FAILED"):
    status = "failed"
elif critic_approved:
    status = "success"
else:
    status = "partial"
```

---

## Memory System Bugs

### DNA Context Builder — Timezone Localization

**Problem:** `classify_time_context()` used UTC time for morning/afternoon/evening classification. A user messaging at 2pm IST (8:30am UTC) would get "morning_weekday" instead of "early_afternoon_weekday."

**Fix:** Time context is now computed in the user's local timezone, configurable via an offset in the context builder.

---

## Orchestrator Bugs

### Multi-Agent Pipeline — Context Loss Between Steps

**Problem:** When chaining `goal_decomposer → daily_planner`, the second agent didn't receive the first agent's output context.

**Fix:** The `format_output_node` accumulates results across pipeline steps and includes prior results in the next agent's task instructions.


---

> **Category:** 📓 Reference · **Parent:** [[Home]]
