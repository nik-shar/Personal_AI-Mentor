---
created: 2026-08-22
tags:
  - cognition
  - constitution
  - guidelines
---

# 📜 Mentor Constitution

> **Role:** The mentor's operating constitution — defines how the mentor should think, decide, and behave.

---

## Overview

The constitution lives in `mentor_agent_guidelines.md` at the project root. It is the **single source of truth** for behavioral rules, loaded fresh on every turn by `orchestrator/cognition/guidelines.py` — never copied into memory to prevent drift.

## Sections

### §1 — Who This Agent Serves
Nikhil's identity anchor: IIT Roorkee B.Tech, self-taught AI/ML, ex-Data Scientist at Turing, job-hunting for AI Engineer roles, long-term MS Robotics goal.

### §2 — Core Principles (6 standing orders)
1. Ground everything in actual state — never fabricate
2. Continuity across turns is non-negotiable
3. Context assembly before routing, even for "routine"
4. Calibrate confidence before acting (1-3 rubric)
5. No silent failure
6. Honesty over polish in external-facing content

### §3 — Orchestrator/Router Responsibilities
Context assembly before computing the Context Sufficiency Score. The reasoner must always ground decisions in actual stored data.

### §4 — Per-Agent Operating Guidelines
Specific rules for each specialist sub-agent. Loaded via `get_agent_guidelines(agent_name)` from `guidelines.py`.

### §5 — Memory Model Rules
Profile store (stable facts) vs episodic store (append-only log) vs working memory (session thread).

### §6 — Guardrails
- Never overstate experience
- Never manufacture urgency
- Draft-and-confirm for reversible actions
- Planning aid, not decision-maker on high-stakes calls

### §7 — Success Criteria
Plans traceable to real goals, context persists correctly, failures visible, output matches Nikhil's voice.

## Source File

📄 **[`mentor_agent_guidelines.md`](../mentor_agent_guidelines.md)** (10,416 bytes)


---

> **Category:** 🧠 Cognition · **Parent:** [[Cognitive Layer]]
