---
created: 2026-08-22
tags:
  - tracing
  - observability
---

# 🔍 Tracing & Observability

> **Role:** Two complementary observability layers — LangSmith for cloud tracing, local latency spans for terminal debugging.

---

## Architecture

Every mentor turn is traced through two parallel systems:

### 1. LangSmith (Cloud)

Enabled when `LANGSMITH_API_KEY` or `LANGCHAIN_API_KEY` is set. Every LLM `.invoke()` call (reasoner, synthesizer, agent LLMs, reflection, consolidation, compare) is auto-traced with prompts, outputs, and latency.

Additionally, each manual orchestration span is mirrored as a LangSmith run so the entire pipeline tree lands in one project.

**Setup:** Automatic on module import of `orchestrator/llm.py` — `setup_tracing()` is called before any ChatModel is built.

**Env:**
```env
LANGCHAIN_TRACING_V2=true
LANGSMITH_API_KEY=lsv2_...
LANGCHAIN_PROJECT=ai-mentor
```

### 2. Local Spans (Always Available)

Every orchestration seam records a span with wall-clock time and ok/error status. Errors carry the exception string.

**Activation:** Set `MENTOR_DEBUG=1` in environment:
```bash
MENTOR_DEBUG=1 uv run python -m orchestrator
```

**Output Format:**
```
⏱️ mentor_turn  12.34s  [ok]
  ✓ intake               0.00s
  ✓ summarize_node       2.10s
  ✓ reasoner_llm         1.50s
  ✓ context_builder      0.80s
  ✓ agent_executor       6.50s
  ✓ memory_merger        0.40s
  ✓ format_output        1.04s
```

## Decorator: `@traced`

Any orchestration seam can be instrumented with the `@traced` decorator:

```python
@traced("reasoner_llm", "llm")
def run_reasoner(summary_text: str) -> ReasoningDecision:
    ...
```

This automatically records:
- Wall-clock duration
- Status (ok/error)
- Error message on failure

## Context: `measure`

For non-decorator usage (context blocks):

```python
with measure("long_term_recall"):
    results = retriever.recall(user_input)
```

## TurnTracer

The `TurnTracer` class collects all spans for one mentor turn:

- `begin_turn()` — Push a new tracer onto the thread
- `end_turn()` — Finalize, push to LangSmith, return local report
- `current_tracer()` — Get the active tracer (for decorators)

## Fail-Open Philosophy

Every tracing layer is fail-open:
- LangSmith unconfigured → degrades silently
- LangSmith post fails → warning printed, turn continues
- Local tracer not available → decorators pass through without recording

Tracing must **never** break a mentor turn.


---

> **Category:** ⚙️ Infrastructure · **Parent:** [[LLM Strategy]]
