# Goal Decomposer Agent — Skills Blueprint (`agents/Goal_Decomposer/skills.md`)

This document defines the specialized domain skills, tool capabilities, implementation strategies, and guardrails for the **Goal Decomposer Agent**.

---

## 1. Core Skills & Capabilities

### 🗺️ Graph-Based Learning Decomposition
- **Skill:** Decomposes major learning aspirations (e.g., "Master LangGraph", "System Design for AI Engineers", "MCP Protocol") into a DAG (Directed Acyclic Graph) of topic nodes with explicit prerequisite dependencies.
- **Target Outcome:** Clean, logical topic graphs saved into your Obsidian Vault (`/home/nik/Documents/AI-Mentor/Learning/Topics`).

### 🧑‍🏫 Master Pedagogical Tutorial Drafting
- **Skill:** Generates high-quality, tutorial-like Markdown notes for each topic node, rather than just generating empty node titles.
- **Target Outcome:** Each generated Obsidian note contains:
  1. **Concept Overview:** Simple analogy + formal technical definition.
  2. **Minimal Production Code Example:** Runnable, clean Python snippet showing the pattern.
  3. **Common Pitfalls & Bugs:** What developers get wrong when building this in production.
  4. **Hands-On Mini Challenge:** A 30-minute practice exercise for Nikhil to complete.

### 🌐 Live Documentation & Syntax Research
- **Skill:** Searches live web documentation (e.g. LangChain, LangGraph, PyDantic v2, vLLM, MCP specs) before drafting tutorials to ensure exact, current API syntax.
- **Target Outcome:** Zero outdated code syntax in generated Obsidian topic notes.

### ⚡ Energy-Aware Topic Sizing
- **Skill:** Sizes topics into 45-60 minute digestible chunks matching Nikhil's deep-focus habit profile, avoiding massive 5-hour monolithic topics.

---

## 2. How to Achieve It (Architecture & Tools)

### A. Live Web Search Tool (`docs_search`)
- **Integration:** Give Goal Decomposer access to a web search tool before tutorial file generation.
- **Workflow:**
  1. Receive target topic (e.g. "LangGraph Checkpointing").
  2. Perform live search: `"LangGraph Postgres Checkpointer syntax 2026 python"`.
  3. Use retrieved documentation snippets to draft exact code examples in the Obsidian note.

### B. Obsidian Vault Manager Tool (`vault_io`)
- **Functions:**
  - `create_topic_note(title, content, tags, prerequisites)`
  - `update_index_roadmap(title, nodes_list)`
  - `link_nodes(source_id, target_id)`

### C. Standardized Pedagogical Note Template
Every created note uses this Markdown template:
```markdown
# [Topic Title]

> **Prerequisites:** [[Prerequisite Note 1]]
> **Estimated Time:** 45 mins
> **Status:** #not_started

## 1. Concept Overview
[Analogy + Technical Definition]

## 2. Production Code Example
```python
# Minimal runnable code snippet
```

## 3. Common Pitfalls & Mistakes
- [Pitfall 1]
- [Pitfall 2]

## 4. Hands-on Challenge (30 mins)
- [ ] Build X using the code pattern above.
```

---

## 3. What to be Careful About (Guardrails & Watchouts)

> [!WARNING]
> **Avoid Code Syntax Hallucinations:** Never guess API signatures for fast-evolving libraries (LangGraph, MCP, Pydantic). Always verify via web search or internal schema.

> [!CAUTION]
> **Avoid Information Overload:** Keep tutorial notes focused and concise (1-2 pages per node). Do not generate 10-page textbook notes that overwhelm the user.

> [!IMPORTANT]
> **Preserve Obsidian Vault Links:** Use strict WikiLink syntax `[[Topic Title]]` so Obsidian graph view connects nodes visually without broken links.

---

## 4. Implementation Roadmap

- [x] **Phase 1:** Implement basic DAG graph generation & Obsidian vault index writing.
- [ ] **Phase 2:** Integrate Live Documentation Web Search (`search_web`) into Goal Decomposer.
- [ ] **Phase 3:** Implement 4-part Pedagogical Tutorial Generator for individual node notes.
- [ ] **Phase 4:** Add interactive roadmap review flow (user can ask to rewrite or adjust specific nodes).
