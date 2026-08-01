# AI Mentor — Command Reference & Documentation

All commands are run from the project root directory: `/home/nik/Desktop/AI`

---

## 🌐 Web Application & FastAPI Server (UI Mode)

Launch the FastAPI backend and dedicated Web UI dashboard:

```bash
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

- **Web Dashboard Interface:** [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger OpenAPI Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **REST Endpoints:**
  - `POST /api/chat` — Execute interactive mentor turn (Reasoning + Pipeline Chaining).
  - `GET /api/roadmaps` — List all Obsidian Vault roadmaps with progress stats.
  - `GET /api/roadmaps/{graph_id}` — Get DAG network topology for interactive Obsidian Graph View.
  - `GET /api/nodes/detail?filename={name}` — Read full Markdown tutorial note from vault.
  - `GET /api/profile` — Fetch current learner profile facts.

---

## 💬 Terminal CLI Chat Loop

Run the interactive text-based chat loop directly in your terminal:

```bash
uv run python -m orchestrator
```

---

## 🧪 Verification & Test Suites

```bash
# Goal Decomposer Quality Validation & Parallel Node Expansion Test
uv run python scripts/test_goal_decomposer_quality.py

# Multi-Day Preparation Routing & 8-Hour Daily Budget Capping Test
uv run python scripts/test_4_day_prep_bugfix.py
```

---

## 📋 Onboarding & Profile Setup

```bash
# Run first-time onboarding or update profile facts
uv run python scripts/onboard_profile.py
```

---

## 📁 Obsidian Vault & Topic Graphs

- **Vault Path:** `/home/nik/Documents/AI-Mentor`  
- **Topic Files Directory:** `/home/nik/Documents/AI-Mentor/Learning/Topics`

### How to use Goal Decomposition in Chat or Web UI
Ask the mentor naturally:
- `"I want to learn LangGraph end to end in 5 days, 5 hours per day"`
- `"Create a 3-day roadmap for Rust programming with 2 hours daily"`
- `"Break down Vector Databases for me into subtopics"`

The `goal_decomposer` agent will:
1. Decompose the goal using LLM into DAG subtopics classified into **4 content types** (`conceptual`, `algorithmic`, `hands_on_code`, `reference`).
2. Run targeted web searches and generate full Markdown tutorial files inside `/home/nik/Documents/AI-Mentor/Learning/Topics`.
3. Perform programmatic quality checks (`validate_tutorial_quality`) to prevent `...` stubs.
4. Process subtopic nodes in parallel using `ThreadPoolExecutor` (max_workers=3) for 60-70% faster generation.
5. Create a main `[Topic] Roadmap.md` index file and link subtopics via `[[wiki-links]]` for Obsidian Graph View visualization.

---

## 🗄️ Environment & Database Config

```bash
# Check PostgreSQL status
sudo systemctl status postgresql

# Key .env configuration variables
# NEBIUS_API_KEY=...
# OBSIDIAN_VAULT_PATH=/home/nik/Documents/AI-Mentor
# OBSIDIAN_TOPIC_FOLDER=Learning/Topics
```
