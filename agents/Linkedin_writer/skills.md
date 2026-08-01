# LinkedIn Writer Agent — Skills Blueprint (`agents/Linkedin_Writer/skills.md`)

This document defines the specialized domain skills, tool capabilities, implementation strategies, and guardrails for the **LinkedIn Writer Agent**.

---

## 1. Core Skills & Capabilities

### 💼 Technical Copywriting & Brand Positioning
- **Skill:** Crafts authentic, high-impact LinkedIn posts that showcase Nikhil's journey as an AI/ML Engineer (building multi-agent systems, RAG pipelines, LangGraph workflows).
- **Target Outcome:** Recruiter-attracting posts that demonstrate real technical depth, problem-solving, and continuous learning.

### 📖 Storytelling Frameworks
- **Skill:** Uses proven engineering post structures:
  - **Hook:** A compelling observation, failure, or counter-intuitive insight.
  - **Problem / Struggle:** Real engineering friction faced during a project.
  - **Technical Solution:** How it was solved (with code pattern or architectural diagram description).
  - **Key Takeaway:** Actionable learning point for fellow engineers.

### 🌐 Industry Trend & Research Integration
- **Skill:** Researches trending AI papers, library updates (e.g. LangGraph 0.2+, vLLM, MCP protocol), or industry discussions to create relevant content.

### 🎯 Tone & Cadence Control
- **Skill:** Strictly matches Nikhil's preferred `content_tone` ("concise, direct, technical but accessible") and respects `linkedin_posting_frequency` (e.g., 2x/week).

---

## 2. How to Achieve It (Architecture & Tools)

### A. Live Web Search Tool (`trend_search`)
- **Integration:** Give LinkedIn Writer access to web search before drafting.
- **Workflow:**
  1. Search trending AI engineering topics or specific framework updates.
  2. Extract key technical concepts to feature in the post.

### B. High-Importance Win Retriever (Episodic RAG)
- **Integration:** Queries `episodic_events` in PostgreSQL where `importance >= 4` (e.g. project milestones, competition wins, completed learning paths).
- **Workflow:** Automatically suggests post ideas based on real accomplishments logged in memory.

### C. Post Output Schema & Draft Saver
- Returns structured output:
  - `hook`: Opening 1-2 lines.
  - `body`: Main post body formatted with line breaks.
  - `call_to_action`: Engaging closing question.
  - `hashtags`: 3-5 relevant technical tags (`#AI #LangGraph #Python`).
- Saves generated draft to `profile_facts["linkedin_drafts"]`.

---

## 3. What to be Careful About (Guardrails & Watchouts)

> [!WARNING]
> **Avoid AI Buzzword Fluff:** NEVER use generic AI cliches ("In today's fast-paced digital world", "Game changer", "Delve into"). Keep language grounded, technical, and human.

> [!CAUTION]
> **Avoid False Claims:** Only feature tech stack tools and project results actually present in Nikhil's profile facts or logged learning events.

> [!IMPORTANT]
> **Formatting for Mobile Readability:** Keep paragraphs short (1-2 sentences max), use whitespace cleanly, and avoid bullet-list dumps.

---

## 4. Implementation Roadmap

- [x] **Phase 1:** Implement basic LinkedIn Writer agent prompt & draft saver.
- [ ] **Phase 2:** Integrate High-Importance Win Fetcher (`importance >= 4`) from episodic memory.
- [ ] **Phase 3:** Integrate Live Web Search Tool (`search_web`) for AI trend research.
- [ ] **Phase 4:** Build post review & approval CLI command (`uv run python -m agents.Linkedin_Writer.approve`).
