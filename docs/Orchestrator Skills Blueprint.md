---
created: 2026-08-22
tags:
  - orchestrator
  - skills
---

# ⚙️ Orchestrator Skills Blueprint

> **Role:** Skills blueprint for the Orchestrator/Mentor Agent — core capabilities, architecture, and guardrails.

---

## Overview

`orchestrator/skills.md` defines the specialised domain skills and implementation roadmap for the Orchestrator/Mentor Agent. It covers the skills the orchestrator should have, how to achieve them architecturally, and what to be careful about.

## Key Capabilities

| Skill | Description |
|-------|-------------|
| Strategic Routing | Accurately routes to specialist agents |
| Context Sufficiency Calibration | 1-2-3 rubric for when to ask vs. act |
| Empathy & Active Listening | Emotional detection before routing |
| Market Awareness | Live web search for job/tech trends |
| Question Queueing | Non-blocking questions stored in memory |

## Implementation Status

- ✅ Phase 1: 1-2-3 Sufficiency Rubric in `reasoner.py`
- ✅ Phase 2: Hard-capped questions (max 2) + `open_questions` queue
- ✅ Phase 3: LLM-native parameter extraction (energy, time)
- ❌ Phase 4: Live Web Search Tool integration (fallback node)
- ❌ Phase 5: Opportunistic Question Surfacing during nudges

## Source File

📄 **[`orchestrator/skills.md`](../orchestrator/skills.md)**


---

> **Category:** 📁 Blueprints · **Parent:** [[Orchestrator Pipeline]]
