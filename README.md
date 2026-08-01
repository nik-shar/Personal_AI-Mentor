# 🧠 Personal AI Mentor — Local-First Multi-Agent Ecosystem

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Framework-LangGraph-orange.svg)](https://github.com/langchain-ai/langgraph)
[![LLM Provider](https://img.shields.io/badge/LLM-Nebius%20(Llama--3.3%20%7C%20MiniMax)-purple.svg)](https://nebius.com/)
[![Database](https://img.shields.io/badge/Database-PostgreSQL%20%7C%20ChromaDB-green.svg)](https://www.postgresql.org/)
[![UI](https://img.shields.io/badge/UI-FastAPI%20%2B%20Vanilla%20Dashboard-cyan.svg)](http://localhost:8000)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An intelligent, local-first personal AI mentor built with **LangGraph**, **PostgreSQL**, **ChromaDB**, and **Obsidian Vault**. Unlike stateless chatbots, this system maintains long-term cognitive continuity across learning roadmaps, daily planning, skill growth, and technical content creation.

---

## 🌟 Key System Capabilities

- **🤖 Unified Multi-Agent Orchestration**: Built on LangGraph with state graph pipeline chaining (`goal_decomposer` ➔ `daily_planner` in a single execution turn).
- **🧠 4-Tier Hybrid Memory System**:
  1. *Working Memory*: LangGraph turn checkpointer (`SqliteSaver` / `PostgresSaver`).
  2. *Episodic Memory*: Local vector store (ChromaDB) with semantic similarity + recency retrieval.
  3. *Semantic Memory*: PostgreSQL relational tables tracking user identity, active learning paths, daily plans, and activity logs.
  4. *Topic Graph Memory*: Automated creation of markdown notes with YAML frontmatter and `[[wiki-links]]` inside an Obsidian Vault.
- **📚 2-Phase Pedagogical Goal Decomposer**:
  - Computes subtopic topology & DAG cycle validation using `networkx`.
  - Classifies learning nodes into 4 structured content buckets: `conceptual`, `algorithmic`, `hands_on_code`, and `reference`.
  - Synthesizes complete Markdown tutorial files in parallel using `MiniMaxAI/MiniMax-M3`.
- **⏱️ Energy-Aware Daily Coach**: Generates optimized daily time blocks aligned with learner goals while programmatically enforcing safety budget caps (max 8 hours/day).
- **✍️ Technical Content Architect**: Multi-stage RAG draft writer for technical posts with automated critic verification and strict programmatic rule enforcement.
- **🖥️ Web Dashboard & Interactive Graph Visualizer**: Built with FastAPI, featuring real-time chat execution, interactive Obsidian DAG network topology graphs, and learning progress analytics.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Triggers
        A[User Web UI / Chat]
        B[Proactive Scheduler]
        C[API Requests]
    end

    subgraph Orchestrator ["LangGraph State Orchestrator"]
        D[Reasoner / Router Node] --> E[Context Assembler]
        E --> F[Agent Execution Pipeline]
        F --> G[Memory Merger & Delta Sync]
    end

    subgraph MemoryLayer ["4-Tier Memory System"]
        M1[(1. Working Memory: Checkpointer)]
        M2[(2. Episodic Memory: ChromaDB)]
        M3[(3. Semantic Memory: PostgreSQL)]
        M4[(4. Topic Graph: Obsidian Vault)]
    end

    subgraph SpecialistAgents ["Specialist Agents"]
        AG1[Goal Decomposer DAG]
        AG2[Daily Coach & Planner]
        AG3[LinkedIn Content Writer]
    end

    Triggers --> D
    E <--> MemoryLayer
    F --> AG1
    F --> AG2
    F --> AG3
    G --> MemoryLayer
```

---

## 🛠️ Tech Stack & Infrastructure

| Component | Technology | Role & Purpose |
|---|---|---|
| **Orchestration** | [LangGraph](https://github.com/langchain-ai/langgraph) | Cyclic state graph routing, context injection, and agent chaining |
| **Reasoner Model** | `meta-llama/Llama-3.3-70B-Instruct` | Intent recognition, dynamic context selection, and agent dispatch |
| **Deep Writer Model** | `MiniMaxAI/MiniMax-M3` | Comprehensive tutorial note synthesis and multi-step content expansion |
| **API Backend** | [FastAPI](https://fastapi.tiangolo.com/) | REST API endpoints, Swagger OpenAPI docs, and static dashboard server |
| **Database** | PostgreSQL | Relational storage for learner profile, activity logs, and daily schedules |
| **Vector DB** | ChromaDB | Disk-persisted embeddings for semantic episodic memory |
| **Graph Computation** | NetworkX | In-memory DAG topological sorting and critical path calculations |
| **Vault Storage** | Obsidian Markdown | Native markdown node notes linked with YAML metadata |

---

## 📁 Repository Structure

```
├── agents/
│   ├── Daily_Coach/         # Daily schedule planner & 8-hour budget manager
│   ├── Goal_Decomposer/     # 2-Phase DAG curriculum generator & Obsidian note expander
│   └── Linkedin_writer/     # RAG technical content draft generator & verifier
├── api/
│   └── main.py              # FastAPI server endpoints (/api/chat, /api/roadmaps, etc.)
├── ui/
│   ├── index.html           # Web UI dashboard layout
│   ├── app.js               # Interactive frontend logic & D3 graph visualizer
│   └── style.css            # Dark mode glassmorphism design system
├── orchestrator/
│   ├── orchestrator.py      # Core LangGraph pipeline state graph
│   ├── llm.py               # Nebius OpenAI-compatible API client bindings
│   ├── config.py            # Central constants & agent context schemas
│   └── memory/              # Postgres, Chroma, and Obsidian vault managers
├── schemas/
│   ├── memory.py            # Typed Pydantic models for profile, episodic, & graph memory
│   └── agent_io.py          # Unified AgentTask & AgentResult contracts
├── scripts/                 # Verification suites, profile onboarding, & benchmarks
├── ai-mentor-architecture.md# Technical Architecture & Design Document
├── COMMANDS.md              # CLI & execution reference guide
└── pyproject.toml           # Project dependencies & environment specification
```

---

## 🚀 Quickstart Guide

### Prerequisites
- **Python 3.10+**
- **uv** package manager (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **PostgreSQL** running locally

### 1. Environment Setup

Clone the repository and prepare your configuration:

```bash
git clone https://github.com/your-username/personal-ai-mentor.git
cd personal-ai-mentor

# Copy configuration template
cp .env.example .env
```

Edit `.env` with your Nebius API credentials and local PostgreSQL details:
```env
NEBIUS_API_KEY=your_nebius_api_key
DATABASE_URL=postgresql://nik:nik@localhost:5432/ai_companion
OBSIDIAN_VAULT_PATH=/path/to/your/Obsidian/Vault
```

### 2. Install Dependencies

```bash
uv sync
```

### 3. Initialize Database & Profile

```bash
# Seed initial profile facts
uv run python scripts/onboard_profile.py
```

### 4. Run Web Application Dashboard

Launch the FastAPI backend and web visualizer:

```bash
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

- **Dashboard:** [http://localhost:8000](http://localhost:8000)
- **API Documentation (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)

### 5. Run Interactive Terminal CLI

```bash
uv run python -m orchestrator
```

---

## 🧪 Verification & Quality Validation

Run automated benchmark tests to verify pipeline functionality:

```bash
# Goal Decomposer Quality & Parallel Node Expansion Test
uv run python scripts/test_goal_decomposer_quality.py

# Multi-Agent Pipeline & Budget Capping Test
uv run python scripts/test_4_day_prep_bugfix.py
```

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for details.
