---
created: 2026-08-22
tags:
  - api
  - infrastructure
---

# 📡 API Reference

> **Role:** FastAPI REST endpoints for the web dashboard and programmatic access.

---

## Server

```bash
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

- **Dashboard:** http://localhost:8000
- **Swagger:** http://localhost:8000/docs

## Chat & Orchestration

### `POST /api/chat`

Execute a mentor turn (reasoning + agent chaining).

**Request:**
```json
{
    "message": "plan my day, I have 4 hours",
    "session_id": null
}
```

**Response:**
```json
{
    "session_id": "uuid",
    "response_text": "Here's your plan...",
    "agent_pipeline": ["goal_decomposer"],
    "pipeline_step": 1,
    "results": [...]
}
```

### `POST /api/session/{session_id}/close`

Close an active session and persist the transcript.

## Memory & DNA

### `GET /api/memories`
List all active DNA memories.

### `GET /api/memories/pending`
List pending validation candidates (unconfirmed inferences).

### `POST /api/memories/{id}/confirm`
User confirms a memory → unlocks confidence ceiling.

### `POST /api/memories/{id}/correct`
User corrects a memory → triggers contradiction-aware revise.

### `DELETE /api/memories/{id}`
Remove a memory.

## Roadmaps & Topic Graphs

### `GET /api/roadmaps`
List all Obsidian vault roadmaps with progress stats:
```json
[
    {
        "topic_id": "...",
        "title": "4-Day Interview Preparation Roadmap",
        "total_nodes": 12,
        "completed": 4,
        "in_progress": 2,
        "not_started": 6,
        "total_hours": 18.5,
        "nodes": [...]
    }
]
```

### `GET /api/roadmaps/{graph_id}`
Get DAG network topology for interactive Obsidian graph view.

### `GET /api/nodes/detail?filename={name}`
Read full Markdown tutorial note from vault.

## Profile

### `GET /api/profile`
Fetch current learner profile facts (structured store).

### `PUT /api/profile`
Update profile facts.

### `GET /api/profile/discovery`
Get discovery-mode status and remaining facets.

## Job Board & Applications

### `GET /api/applications`
Pipeline entries enriched with staleness flags and artifact paths.

### `GET /api/applications/history`
Append-only application timeline (episodic `job_application` events).

### `POST /api/applications`
Create a pipeline entry from the board form (dedup: re-add = refresh).

### `POST /api/applications/stage`
Move an application to a new stage.

### `POST /api/applications/note`
Append a timestamped note without stage transition.

### `DELETE /api/applications/{company}`
Remove an entry from the pipeline (history kept).

### `GET /api/applications/artifact?company={c}&role={r}&kind={jd|resume|fit}`
Read application markdown artifacts from the vault.

## Job Board (Unified)

### `GET /api/jobs`
Unified board: search-result listings + applications in one list.

### `DELETE /api/jobs/{listing_id}`
Remove one row from the board.

## Cognitive Layer

### `GET /api/cognition/status`
Get current discovery mode status, relationship phase, and personality config.


---

> **Category:** ⚙️ Infrastructure · **Parent:** [[Quickstart Guide]]
