---
created: 2026-08-22
tags:
  - context
  - claude
  - project
---

# 🤖 CLAUDE.md — AI Companion Context

> **Role:** Project context file for AI coding assistants (Claude, Cline, etc.) — what this system is and how to build it.

---

## Overview

`CLAUDE.md` is the AI assistant's project context file. It documents the system architecture from a builder's perspective — what the system is, its core design decisions, and practical implementation details that an AI assistant needs to know to work on the codebase effectively.

## What It Documents

### Core Architecture
The trigger → orchestrator → agents → outputs flow, with the key insight that **DNA memory store is the only persistent state** — sub-agents never read/write it directly.

### Key Design Decisions
1. Agents only draft/decide — never execute side effects
2. Routing and memory-slicing are one orchestrator step
3. Scheduler is a dumb trigger, not a smart one
4. Constraints enforced programmatically, not by LLM judgment
5. Sub-agent internal subgraphs are private

### Repo Structure
The old-style directory tree showing `agents/Linkedin_writer/`, `orchestrator/`, `scheduler/`, etc.

### LinkedIn Writer Deep Dive
Complete design walkthrough of the LinkedIn Writer agent (internal graph, revision loop, bugs found and fixed).

## Source File

📄 **[`CLAUDE.md`](../CLAUDE.md)** (10,909 bytes)


---

> **Category:** 📓 Reference · **Parent:** [[Home]]
