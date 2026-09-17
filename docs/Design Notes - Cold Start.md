---
created: 2026-08-22
tags:
  - notes
  - design
  - cold-start
  - emotional-intelligence
---

# 📓 Design Notes — Cold-Start Emotional Intelligence

> **Role:** Design notes about how the mentor should handle first conversations and emotional disclosures before it has data.

---

## Overview

`Notes.md` captures a critical design discussion about **cold-start emotional intelligence**. The core insight: the mentor has zero data on Day 1, but the user might share something emotionally significant in their very first message. The mentor must respond well before it has data.

## Two Problems

### 1. Intelligent First-Contact Handling (Cold Start)
How the mentor responds before it has data. Solved by:
- **Layer A**: Emotional checkpoint before routing — acknowledge before acting
- **Layer B**: Structured onboarding conversation (not a form) — guided discovery
- **Layer C**: Cold-start personality defaults (gentle, warm, lower accountability)
- **Layer D**: Emotional disclosure categories (venting, seeking guidance, crisis, context sharing, burnout)

### 2. Ongoing Psychological Calibration
How the mentor gets progressively smarter about specific emotional patterns over time.

## Emotional Disclosure Categories

| Category | Example | Appropriate Response |
|----------|---------|---------------------|
| Venting | "Today was garbage" | Mirror + validate. Don't problem-solve. |
| Seeking guidance | "Should I focus on DSA or system design?" | Analyze, suggest |
| Crisis signal | "I don't see the point anymore" | Acknowledge seriously. Suggest professional support. |
| Context sharing | "I'm between jobs right now" | Store as profile fact. Adapt plans. |
| Burnout disclosure | "I'm exhausted" | Switch to recovery mode. Reduce intensity. |

## Professional Boundary

The mentor must **never**:
- Pretend to be a therapist
- Diagnose or treat
- Use clinical language

The mentor **should**:
- Acknowledge the emotion
- Adapt the plan
- Gently encourage professional support for crisis signals

## What Changed Architecturally

From these notes, the following were implemented:
1. `orchestrator/cognition/onboarding.py` — Discovery mode (guided first conversations)
2. `reason_node` — STEP 0 emotional checkpoint before routing
3. `disclosure_type` classification in `ReasoningDecision`
4. Cold-start personality defaults in `cognition/personality.py`
5. Crisis guardrail code-enforced in `orchestrator.py`

## Source File

📄 **[`Notes.md`](originals/Notes.md)** (8,069 bytes)


---

> **Category:** 📓 Reference · **Parent:** [[Cognitive Layer]]
