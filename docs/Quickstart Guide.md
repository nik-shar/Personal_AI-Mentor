---
created: 2026-08-22
tags:
  - quickstart
  - setup
---

# 🚀 Quickstart Guide

> **Role:** Setup and run instructions for the Personal AI Mentor.

---

## Prerequisites

- **Python 3.10+**
- **uv** package manager (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **PostgreSQL** running locally with pgvector extension
- **Nebius API key** (for LLM access — Tier 2 and 3)
- **OpenAI API key** (for Tier 1 routing)

## 1. Clone & Configure

```bash
git clone <repo-url>
cd ai
cp .env.example .env
```

Edit `.env`:
```env
NEBIUS_API_KEY=your_nebius_api_key
OPENAI_API_KEY=your_openai_api_key
DATABASE_URL=postgresql://nik:nik@localhost:5432/ai_companion
OBSIDIAN_VAULT_PATH=/path/to/your/Obsidian/Vault
```

## 2. Install Dependencies

```bash
uv sync
```

## 3. Start PostgreSQL

```bash
sudo systemctl start postgresql
# Ensure pgvector extension is available
```

## 4. Run the System

### Web Dashboard (Recommended)

```bash
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

### Terminal CLI

```bash
uv run python -m orchestrator
```

## 5. No Profile Needed — Just Talk

There is no onboarding script. The mentor learns who you are through conversation:
- Every turn is reflected on and stored as DNA memories
- Confidence lifecycle governs what's remembered
- Early chats run in guided discovery mode — the mentor asks, listens, and remembers

## 6. Verify Setup

```bash
# Run the DNA memory test suite
uv run python scripts/tests/test_dna_memory_store.py

# Test multi-agent pipeline
uv run python scripts/tests/test_orchestrator_harness.py

# Test mentor cognition
uv run python scripts/tests/test_mentor_cognition.py
```

## 7. Run Weekly Maintenance

Memory consolidation runs automatically via the scheduler. To run manually:

```bash
uv run python -m scheduler
```

This runs the 3-stage aging pipeline + DNA decay + pattern observations.

## 8. Optional Integrations

```env
# Web search (for fallback agent knowledge)
TAVILY_API_KEY=your_tavily_key

# Job search (for job_hunter agent)
SERP_API_KEY=your_serpapi_key
RAPIDAPI_KEY=your_rapidapi_key

# Proactive notifications (messenger.py)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```


---

> **Category:** 🚀 Getting Started · **Parent:** [[Home]]
