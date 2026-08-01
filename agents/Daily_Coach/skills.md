# Daily Coach Agent — Skills Blueprint (`agents/Daily_Coach/skills.md`)

This document defines the specialized domain skills, tool capabilities, implementation strategies, and guardrails for the **Daily Coach Agent** (owning both `daily_planner` and `learning_monitor`).

---

## 1. Core Skills & Capabilities

### 📅 Energy-Aware & Time-Boxed Daily Planning
- **Skill:** Builds realistic, balanced daily plans calibrated to the user's current energy (1–5/5) and available time budget.
- **Target Outcome:**
  - High energy (4-5/5): Intensive hands-on study, project coding, optional job search.
  - Medium energy (3/5): Balanced study + review + required break.
  - Low energy (1-2/5): Light review, rest blocks, zero heavy new topics.

### 🎯 Deep-Focus Block Structuring (45–60m Blocks)
- **Skill:** Structures study items into 45–60 minute focus blocks matching Nikhil's habit profile, inserting mandatory 15–30 minute breaks between blocks.

### 🛡️ Anti-Perfectionism & Over-Engineering Guardrails
- **Skill:** Explicitly caps initial exploration tasks to prevent Nikhil from getting stuck in perfectionist rabbit holes when starting new topics.

### 🔥 Streak Protection & Learning Log Parsing
- **Skill:** Extracts completed learning topics from natural language user reports, updates learning streak counters in memory, and marks corresponding Obsidian topic nodes as `completed`.

---

## 2. How to Achieve It (Architecture & Tools)

### A. Pure LLM Parameter Extraction (Zero Fragile Regexes)
- **Integration:** Energy level (`parsed_energy_level`) and available minutes (`parsed_available_minutes`) are extracted by the Orchestrator LLM Reasoner and passed in `AgentTask.params`.
- **Fallback:** If unmentioned in current turn, preserves `todays_plan.total_available_minutes` for session continuity.

### B. Prerequisite-Aware Topic Graph Integration
- Reads Obsidian topic graph nodes (`.md` files) via `context_builder.py`.
- Filters available topics to ONLY show nodes where all prerequisites are `completed`.

### C. Memory Delta Persistence
- Writes generated plan to `profile_facts["daily_plans"]`.
- Writes learning logs to `episodic_events` with `event_type="learning_session"`.

---

## 3. What to be Careful About (Guardrails & Watchouts)

> [!WARNING]
> **Time Budget Strictness:** Sum of all task duration minutes MUST NOT exceed `available_minutes`. Python post-processing (`_trim_to_fit`) enforces this as a hard safety net.

> [!CAUTION]
> **Avoid Re-scheduling Recently Studied Topics:** Always inspect `learning_log` from the past 3 days to avoid scheduling a topic the user completed yesterday.

> [!IMPORTANT]
> **Enum Schema Safety:** All project status values must be coerced to valid `ProjectStatus` enum strings (`active`, `planning`, `paused`, `done`, `abandoned`).

---

## 4. Implementation Roadmap

- [x] **Phase 1:** Implement LangGraph dual-branch graph (`build_daily_plan` vs `log_learning_*`).
- [x] **Phase 2:** Implement LLM-native parameter extraction for energy and time.
- [x] **Phase 3:** Integrate profile DNA facts (`working_habits`, `mindset_notes`, `bio_summary`) into planning prompts.
- [x] **Phase 4:** Add robust Pydantic enum coercions for Project status and DailyPlan items.
- [ ] **Phase 5:** Implement auto-updating of Obsidian topic node Markdown files when user logs completed learning.
