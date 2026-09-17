---
created: 2026-08-22
tags:
  - architecture
  - design
  - original
---

# 🏛️ Original System Architecture

> **Role:** The original system architecture design document (v1.2) covering multi-agent chaining, 4-bucket goal decomposition, and the Nebius/MiniMax LLM endpoints.

---

## Overview

`docs/originals/ai-mentor-architecture.md` is the original architecture design document. While many decisions have evolved, it still documents:

- **Vision & Design Principles** — 5 core principles: one identity, specificity, proactivity, local-first, earned trust
- **High-Level Architecture** — Router/Reasoner → Context Assembler → Multi-Agent Pipeline
- **Memory System** — 4 tiers (working, episodic, semantic, topic graph)
- **Multi-Agent Chaining** — Original pipeline design for `goal_decomposer → daily_planner`
- **Specialist Agents** — Architecture for Daily Planner, Goal Decomposer (2-phase, 4-bucket)
- **Tech Stack** — LangGraph, Nebius LLMs, PostgreSQL, Chroma, Obsidian, networkx

## Source File

📄 **[`docs/originals/ai-mentor-architecture.md`](originals/docs/originals/ai-mentor-architecture.md)** (8,630 bytes)


---

> **Category:** 🏛️ Architecture · **Parent:** [[Architecture Overview]]
