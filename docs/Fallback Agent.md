---
created: 2026-08-22
tags:
  - agent
  - fallback
---

# 🗣️ Fallback Agent

> **Role:** General conversation catch-all — handles chat that doesn't match any specialist agent, with web search and opportunistic question surfacing.

---

## When It's Used

The fallback agent is invoked when:
1. The `reason_node` chooses `action="direct_response"` 
2. Keyword routing in `dispatch_node` doesn't match any specialist agent
3. A specialist agent errors and the orchestrator degrades to fallback

## Capabilities

### 1. Natural Conversation

The fallback uses the conversational LLM (Qwen3-235B-A22B-Instruct) with full context:
- Mentor persona (voice rules, hard rules, few-shot examples from `cognition/persona.py`)
- Profile data from memory slice
- Web search findings (when applicable)
- Queued questions for opportunistic surfacing

### 2. Web Search

Triggered by keywords like `latest`, `news`, `trend`, `framework`, `what is`, `how to`, plus market/job-related terms. Uses [[search.py]] (Tavily → DuckDuckGo fallback).

### 3. Opportunity Question Surfacing

If there are `open_questions` in the profile, the fallback can surface at most one question opportunistically at the end of its response, making the mentor feel proactive even on general chat.

## Behavioral Rules

From `registry.py`'s `_fallback_agent`:

- **Greeting** (`hi`, `hey`, `hello`) → Warm 1-2 sentence reply. No advice, plans, or lists.
- **Emotional sharing** → Acknowledge first, then ask what he needs. Never jump to problem-solving.
- **Specific question** → Answer using profile context.
- **Open-ended request** → Focused, personalized guidance.
- **Never dump unsolicited career advice** — generic tips are forbidden. Use actual profile data.


---

> **Category:** 🤖 Agent · **Parent:** [[Agent IO Contract]]
