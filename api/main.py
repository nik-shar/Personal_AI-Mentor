"""
api/main.py

FastAPI Web Backend for Personal AI Mentor.
Exposes REST endpoints for:
- Live Chat Turn Orchestration
- Obsidian Roadmaps & Graph Visualizations
- Detailed Topic Note Reading
- Profile & Memory Summaries
- Serving Static Web UI
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from orchestrator.config import OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER
from orchestrator.memory.obsidian_graph import (
    load_topic_graphs,
    list_vault_roadmaps,
    _parse_frontmatter,
)
from orchestrator.memory.store import MemoryManager
from orchestrator.runner import OrchestratorRunner
from orchestrator.state import OrchestratorState

app = FastAPI(
    title="Personal AI Mentor API",
    version="1.0.0",
    description="Backend API for Personal AI Mentor with Multi-Agent Orchestration & Obsidian Graph Integration.",
)

# Enable CORS for local UI development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store for OrchestratorState instances
_SESSIONS: dict[str, OrchestratorState] = {}

# Single MemoryManager instance
_memory_manager: MemoryManager | None = None
_runner: OrchestratorRunner | None = None


def get_runner() -> OrchestratorRunner:
    global _memory_manager, _runner
    if _runner is None:
        try:
            _memory_manager = MemoryManager()
            _memory_manager.ensure_schema()
        except Exception as exc:
            print(f"[API] Warning: MemoryManager DB connect warning: {exc}")
            _memory_manager = None
        _runner = OrchestratorRunner(_memory_manager)
    return _runner


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., description="User prompt or instructions")
    session_id: str | None = Field(default=None, description="Active session ID")


class ChatResponse(BaseModel):
    session_id: str
    response_text: str
    agent_pipeline: list[str]
    pipeline_step: int
    results: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    """System health check endpoint."""
    return {
        "status": "healthy",
        "obsidian_vault": OBSIDIAN_VAULT_PATH,
        "active_sessions": len(_SESSIONS),
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat_turn(req: ChatRequest):
    """Execute one full turn of the multi-agent companion loop."""
    runner = get_runner()
    session_id = req.session_id or str(uuid4())

    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = runner.create_state(session_id)

    state = _SESSIONS[session_id]

    try:
        updated_state = runner.run_turn(state, req.message)
        _SESSIONS[session_id] = updated_state

        raw_results = updated_state.get("results") or []
        serialized_results = []
        for r in raw_results:
            if hasattr(r, "model_dump"):
                serialized_results.append(r.model_dump(mode="json"))
            elif isinstance(r, dict):
                serialized_results.append(r)
            else:
                serialized_results.append({"output": str(r)})

        return ChatResponse(
            session_id=session_id,
            response_text=updated_state.get("response_text", ""),
            agent_pipeline=updated_state.get("agent_pipeline", []),
            pipeline_step=updated_state.get("pipeline_step", 0),
            results=serialized_results,
        )
    except Exception as exc:
        print(f"[API] Error running turn: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/roadmaps")
def list_roadmaps():
    """List all available roadmaps stored in the Obsidian vault."""
    try:
        roadmaps = list_vault_roadmaps(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
        return {"roadmaps": roadmaps, "vault_path": OBSIDIAN_VAULT_PATH}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list roadmaps: {exc}")


@app.get("/api/roadmaps/{graph_id}")
def get_roadmap_graph(graph_id: str):
    """Fetch DAG graph topology (nodes and edges) for a specific graph_id."""
    graphs = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
    target = next((g for g in graphs if g.topic_id == graph_id or graph_id.lower() in g.topic_id.lower()), None)
    
    if not target:
        raise HTTPException(status_code=404, detail=f"Roadmap '{graph_id}' not found.")

    nodes_list = []
    edges_list = []

    for n_id, n in target.nodes.items():
        nodes_list.append({
            "id": n.id,
            "title": n.title,
            "status": n.status,
            "estimated_hours": n.estimated_hours,
            "resources": n.resources,
            "notes": n.notes,
        })
        for prereq in n.prerequisites:
            edges_list.append({"from": prereq, "to": n.id})

    return {
        "graph_id": target.topic_id,
        "title": target.title,
        "total_nodes": len(nodes_list),
        "nodes": nodes_list,
        "edges": edges_list,
    }


@app.get("/api/nodes/detail")
def get_node_detail(filename: str = Query(..., description="Markdown filename or title")):
    """Read a specific topic note Markdown file from Obsidian and return frontmatter + body."""
    target_dir = Path(OBSIDIAN_VAULT_PATH) / OBSIDIAN_TOPIC_FOLDER
    if not target_dir.exists():
        raise HTTPException(status_code=404, detail="Obsidian topic directory not found.")

    clean_target = filename.replace(".md", "").strip().lower()
    matched_file: Path | None = None

    for file_path in target_dir.rglob("*.md"):
        if file_path.stem.lower() == clean_target or clean_target in file_path.stem.lower():
            matched_file = file_path
            break

    if not matched_file:
        raise HTTPException(status_code=404, detail=f"Topic note '{filename}' not found in vault.")

    try:
        content = matched_file.read_text(encoding="utf-8")
        data, body = _parse_frontmatter(content)
        return {
            "filename": matched_file.name,
            "filepath": str(matched_file),
            "frontmatter": data,
            "body": body,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading file: {exc}")


@app.get("/api/profile")
def get_profile_facts():
    """Retrieve current user profile facts from DB or default profile."""
    runner = get_runner()
    if runner.memory_manager:
        try:
            facts = runner.memory_manager.load_profile_facts()
            return {"profile": facts}
        except Exception as exc:
            print(f"[API] Profile read error: {exc}")

    
    # Fallback profile response
    return {
        "profile": {
            "identity": {"bio_summary": "Software Engineer & AI Enthusiast"},
            "goals": {"short_term_goal": "Interview Preparation & Mastery"},
            "preferences": {"working_habits": ["Self-study", "Obsidian Note Taking"]},
        }
    }


# ---------------------------------------------------------------------------
# Mount Static Files (Web UI)
# ---------------------------------------------------------------------------

ui_path = ROOT_DIR / "ui"
if ui_path.exists():
    app.mount("/", StaticFiles(directory=str(ui_path), html=True), name="ui")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
