# Orchestrator & Mentor Agent — Skills Blueprint (`orchestrator/skills.md`)

This document defines the specialized domain skills, tool capabilities, implementation strategies, and guardrails for the **Orchestrator / Mentor Agent**.

---

## 1. Core Skills & Capabilities

### 🧠 Strategic Routing & Intent Recognition
- **Skill:** Accurately determines whether a user's request requires specialist sub-agents (`goal_decomposer`, `linkedin_writer`, `job_hunter`) or direct high-level mentorship.
- **Absorbed skills (no sub-agent):** Daily planning and learning logging are now orchestrator skills
  handled directly via the harness tool loop — `save_daily_plan`, `trim_plan_to_fit`,
  `log_learning_session`, `compute_learning_streak`, `get_available_topic_nodes`.
- **Target Outcome:** Zero misrouted requests; seamless routing to specialist agents when tasks are clear.

### 🔍 Context Sufficiency Calibration (1–2–3 Rubric)
- **Skill:** Evaluates whether current working memory + static DNA profile contains enough real-time context to answer open-ended or strategy requests non-generically.
- **Target Outcome:**
  - **Score 1 (Insufficient):** Asks 1–2 high-value gut-check questions immediately; queues non-urgent questions into memory.
  - **Score 2 (Marginally Sufficient):** Provides initial direction with a targeted follow-up.
  - **Score 3 (Sufficient):** Proceed directly to synthesis and agent dispatch.

### 💬 Empathy, Psychology & Active Listening
- **Skill:** Detects user mood, burnout signals, procrastination patterns, and perfectionism traps.
- **Target Outcome:** Delivers mentor responses that feel supportive, strategic, and mature — balancing long-term career accountability with immediate empathy.

### 🌐 Real-World Market Awareness
- **Skill:** Integrates live information about AI engineering job market trends, company hiring in target locations (e.g., Japan, India, Remote), and framework releases.
- **Target Outcome:** Answers questions about career options or skills with real-time accuracy rather than frozen model cutoffs.

### 📥 Question Queueing & Opportunistic Surfacing
- **Skill:** Holds non-blocking questions in `open_questions` memory and surfaces at most **one question opportunistically** during weekly check-ins or natural conversational lulls.

### ⏱️ Calendar Ownership (48-slot day grid)
- **Skill:** Reads and writes Nik's real calendar — a rolling 48×30-minute grid — with code-validated tools. Availability is always computed by code, never guessed.
- **Tools:** `get_day_grid` (read free/busy/anchors), `find_available_slots` (candidate windows for a duration), `place_time_block` (book a validated block — 30-min aligned, overlap + anchor guarded), `set_anchor` (reserve sleep/meal/commute/gym).
- **Target Outcome:** When Nik says "I have 3 hours," the mentor confidently answers "here's where they fit" — and can lock them in with his consent. When he asks "am I free at 4?", the mentor answers from the grid, not from memory.
- **Anchor discipline:** sleep/meal/commute/gym anchors are hard walls; task placement never crosses them. Anchor conflicts are surfaced honestly, and Nik owns final say over his anchors.
- **Activated via:** the `calendar-manager` toolkit overlay + `TOOLKIT_CALENDAR_SWITCH_SIGNALS` backstop in `apply_toolkit_guardrail` (scheduling/availability/anchor turns force `direct_response` + calendar-manager).

### 📜 Tutorial Command Execution (`write_tutorial`)
- **Skill:** Executes direct "write a tutorial / deep note on X" commands through the goal_decomposer's **single-topic mode** — one note, built around the mentor's guidance (weaknesses, subtopics to focus) rather than a full roadmap decomposition.
- **Mechanics:** reasoner routes to `goal_decomposer` with task_type `write_tutorial`; the decompiser's `tutorial_architect` produces a one-node spec whose `what_to_cover` is the mentor's emphasis; the standard it must meet lives in the external `tutorial-writer` toolkit (philosophy/behaviors/guardrails/workflows) — including the parsed `rules:` block enforced by code with rejection-feedback revision.
- **Target Outcome:** "write me a tutorial on DPO derivation — I kept tripping on the argmax step" produces a single vault note built *around* that step.

---

## 2. How to Achieve It (Architecture & Tools)

### A. Live Web Search Tool (`tavily_search` / `duckduckgo`)
- **Integration:** Equip the Orchestrator Reasoner / Fallback node with a web search tool call function.
- **Trigger Condition:** Executed when the user asks about external topics (e.g., "What AI roles in Japan are open for freshers?", "What is the latest release in LangGraph?").

### B. Structured Reasoner Decision Schema (`ReasoningDecision`)
- Enforces structured extraction of:
  - `context_sufficiency_score` (1-3)
  - `target_questions_now` (Max 2 questions)
  - `queued_questions` (Saved to DB `open_questions` array)
  - `parsed_energy_level` & `parsed_available_minutes`

### C. 3-Tier Memory Integration
- **Tier 1 (DNA Profile):** `bio_summary`, `working_habits`, `mindset_notes`, `long_term_goal`.
- **Tier 2 (Episodic Event RAG):** Vector search over past chat history via `pgvector`.
- **Tier 3 (Working Memory):** Current turn conversation thread.

---

## 3. What to be Careful About (Guardrails & Watchouts)

> [!WARNING]
> **Avoid Question Fatigue:** Never ask more than 2 questions in a single turn. Enforce the `target_questions_now[:2]` hard cap in Python code, not just in LLM prompts.

> [!CAUTION]
> **Latency Overhead:** Do NOT trigger Web Search on routine planning or greeting turns. Reserve web search strictly for turns requiring external real-world factual data.

> [!IMPORTANT]
> **Preserve User Intent:** Do not override explicit user time or energy statements with assumptions. Honor user corrections immediately.

---

## 4. Implementation Roadmap

- [x] **Phase 1:** Implement 1-2-3 Sufficiency Rubric & Few-Shot prompt examples in `reasoner.py`.
- [x] **Phase 2:** Implement hard-capped question enforcement (Max 2 questions) and `open_questions` DB queueing in `clarify_node`.
- [x] **Phase 3:** Replace fragile regexes with LLM-native parameter extraction for energy and time.
- [ ] **Phase 4:** Integrate Live Web Search Tool (`search_web`) into Orchestrator Fallback node for market/tech queries.
- [ ] **Phase 5:** Build Opportunistic Question Surfacing during weekly check-in nudges.
