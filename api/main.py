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
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.tools import router as mentor_tools_router
from orchestrator.config import (
    MENTOR_CURRICULUM_PATH,
    OBSIDIAN_CAREER_FOLDER,
    OBSIDIAN_VAULT_PATH,
    today_local,
)
from orchestrator.memory.roadmap import (
    find_node_anywhere,
    find_roadmap_any,
    parse_frontmatter,
    read_note,
    roadmap_summaries,
)
from orchestrator.memory.store import MemoryManager
from orchestrator.runner import OrchestratorRunner
from orchestrator.state import OrchestratorState

if TYPE_CHECKING:  # resolves the forward reference on `_dna_store` without a runtime import
    from orchestrator.memory.dna_store import DNAMemoryStore

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

# PI mentor bridge — the intent-shaped, read-only tool surface the PI agent
# core calls through mentor/extensions/*.ts. See api/tools.py.
app.include_router(mentor_tools_router)

# In-memory session store for OrchestratorState instances
_SESSIONS: dict[str, OrchestratorState] = {}

# Single MemoryManager instance
_memory_manager: MemoryManager | None = None
_runner: OrchestratorRunner | None = None
_dna_store: DNAMemoryStore | None = None


def get_dna_store():
    """Return the shared DNAMemoryStore singleton, ensuring schema once (Phase 2)."""
    global _dna_store
    if _dna_store is None:
        from orchestrator.memory.dna_store import get_dna_store as _get
        _dna_store = _get()
        try:
            _dna_store.ensure_schema()
        except Exception as exc:
            print(f"[API] Warning: DNAMemoryStore schema warning: {exc}")
    return _dna_store


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
    # Per-turn execution trace for the architecture visualizer: ordered
    # node + tool events with latency and decision detail.
    trace: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chat engine switch — PI agent core (bridge) vs the Python LangGraph pipeline
#
#   MENTOR_CHAT_ENGINE=pi      (default) browser → mentor (PI) via
#                              orchestrator/pi_bridge.py. Python keeps
#                              sole-writer ownership of every store (tool surface,
#                              specialists, and the per-turn memory write-back).
#   MENTOR_CHAT_ENGINE=python  the pre-migration LangGraph pipeline. Retained as
#                              the escape hatch for one release: it still serves
#                              the same ChatResponse contract, so flipping back
#                              is one env var and no redeploy.
#
# The default flipped to `pi` at the end of the side-by-side comparison pass. It
# became safe to flip only once PI could reach the specialists: without
# `run_specialist` (api/tools.py) the change would have silently removed roadmap
# generation, resume tailoring, and post drafting from chat. See
# docs/PI-Mentor Boundary.md §9 and §12.
# ---------------------------------------------------------------------------

CHAT_ENGINES = ("pi", "python")
DEFAULT_CHAT_ENGINE = "pi"


def _chat_engine() -> str:
    """Which engine serves chat turns (read per request, so it can be flipped live)."""
    engine = (os.getenv("MENTOR_CHAT_ENGINE") or DEFAULT_CHAT_ENGINE).strip().lower()
    return engine if engine in CHAT_ENGINES else DEFAULT_CHAT_ENGINE


def _chat_turn_pi(req: ChatRequest) -> ChatResponse:
    """One mentor turn through the PI bridge.

    Fail-open in the API's sense: a bridge failure is *reported* (503/502) rather
    than dressed up as a reply — the mentor never invents an answer it did not have.
    """
    from orchestrator.pi_bridge import get_pi_bridge

    session_id = req.session_id or str(uuid4())
    try:
        session = get_pi_bridge().session(session_id)
        result = session.prompt(req.message)
    except Exception as exc:  # spawn/protocol-level failure
        raise HTTPException(status_code=503, detail=f"Mentor bridge unavailable: {exc}")

    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail=result.error or "The mentor core did not complete the turn.",
        )

    return ChatResponse(
        session_id=session_id,
        response_text=result.text,
        agent_pipeline=result.pipeline,
        pipeline_step=len(result.tool_calls),
        # No AgentResult envelopes on the PI path — the mentor answers directly and
        # writes through tools, so there is nothing to hand back here.
        results=[],
        trace=result.trace,
    )


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


@app.get("/api/architecture")
def get_architecture_descriptor():
    """Static architecture descriptor (orchestrator graph, agents, tools)."""
    try:
        from orchestrator.architecture_graph import get_architecture

        return get_architecture()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading architecture: {exc}")


@app.post("/api/chat", response_model=ChatResponse)
def chat_turn(req: ChatRequest):
    """Execute one full turn of the multi-agent companion loop.

    Two engines, one response contract — see `_chat_engine()`. The PI path drives
    the mentor core through the RPC bridge; the Python path is the LangGraph
    pipeline it is replacing.
    """
    if _chat_engine() == "pi":
        return _chat_turn_pi(req)

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
            trace=updated_state.get("execution_trace") or [],
        )
    except Exception as exc:
        print(f"[API] Error running turn: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class SessionEndRequest(BaseModel):
    session_id: str = Field(..., description="Active session ID to close")


@app.post("/api/session/end")
def end_session(req: SessionEndRequest):
    """
    Close an active chat session: persist its full transcript (with timestamps)
    to the conversation_sessions table and drop it from the live session store.
    The next message with this session_id starts a fresh session.
    """
    if _chat_engine() == "pi":
        from orchestrator.pi_bridge import get_pi_bridge

        turns_saved = get_pi_bridge().close_session(req.session_id)
        if turns_saved is None:
            raise HTTPException(status_code=404, detail="No active session with that id.")
        return {
            "status": "closed",
            "session_id": req.session_id,
            "turns_saved": turns_saved,
        }

    state = _SESSIONS.pop(req.session_id, None)
    if state is None:
        raise HTTPException(status_code=404, detail="No active session with that id.")
    from orchestrator.orchestrator import close_active_session
    turns_saved = close_active_session(state, _memory_manager)
    return {
        "status": "closed",
        "session_id": req.session_id,
        "turns_saved": turns_saved,
    }


@app.get("/api/roadmaps")
def list_roadmaps():
    """List all roadmaps from the curriculum root (the manifesto store)."""
    try:
        roadmaps = roadmap_summaries(MENTOR_CURRICULUM_PATH)
        return {"roadmaps": roadmaps, "vault_path": MENTOR_CURRICULUM_PATH}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list roadmaps: {exc}")


@app.get("/api/roadmaps/{graph_id}")
def get_roadmap_graph(graph_id: str):
    """Fetch DAG graph topology (nodes and edges) for a specific graph_id.

    Resolution is exact (graph_id, then exact title). It used to match on
    `graph_id.lower() in g.topic_id.lower()`, which returned the first roadmap
    whose id merely *contained* the query.
    """
    target = find_roadmap_any(MENTOR_CURRICULUM_PATH, graph_id)

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
            "day": n.day,
            "content_type": n.content_type,
            "anchors": [a.model_dump(exclude_none=True) for a in n.anchors],
            "checkpoint": n.checkpoint.model_dump(exclude_none=True) if n.checkpoint else None,
        })
        for prereq in n.prerequisites:
            edges_list.append({"from": prereq, "to": n.id})

    return {
        "graph_id": target.topic_id,
        "title": target.title,
        "total_nodes": len(nodes_list),
        "nodes": nodes_list,
        "edges": edges_list,
        "source": target.source.model_dump(exclude_none=True),
        "stopping_rule": target.stopping_rule,
        "target_days": target.target_days,
        "hours_per_day": target.hours_per_day,
    }


@app.get("/api/nodes/detail")
def get_node_detail(filename: str = Query(..., description="Markdown filename or node title")):
    """Read a topic note from the curriculum and return frontmatter + body.

    The old version scanned the vault for a filename *substring*
    (`clean_target in file_path.stem.lower()`) and broke on the first hit, so
    "SQL" returned whichever of SQL Basics / SQL Practice the filesystem listed
    first. Resolution is now exact via the manifest.
    """
    clean = (filename or "").replace(".md", "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="filename is required")

    hit = find_node_anywhere(MENTOR_CURRICULUM_PATH, clean)
    if hit is None:
        raise HTTPException(
            status_code=404,
            detail=f"No node exactly matching '{clean}' (ambiguous or absent).",
        )
    graph_id, node = hit
    found = read_note(MENTOR_CURRICULUM_PATH, graph_id, node)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Note file for '{node.title}' is missing.")

    path, text = found
    data, body = parse_frontmatter(text)
    return {
        "filename": path.name,
        "filepath": str(path),
        "graph_id": graph_id,
        "frontmatter": data,
        "body": body,
    }


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

    # Fallback profile response (prompt-only keys live in DNA memory — Phase 5)
    return {
        "profile": {
            "goals": {"short_term_goal": "Interview Preparation & Mastery"},
        }
    }


class ProfileUpdate(BaseModel):
    value: Any = Field(..., description="New JSON value for the fact (any JSON type)")
    category: str | None = Field(default=None, description="Optional explicit category (else inferred from existing row / PROFILE_KEY_MAP)")


@app.get("/api/profile/facts")
def list_profile_facts_rows():
    """Categorized profile_facts rows (category, key, value) for the manual memory panel.

    Returns per-row category so the UI can show (and fix) duplicate keys like
    the double 'projects' / 'preferences' rows instead of a flat deduped dict.
    """
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")
    try:
        from sqlalchemy import text as _text

        with mm.SessionLocal() as session:
            rows = session.execute(
                _text("SELECT category, key, value, source, updated_at FROM profile_facts ORDER BY category, key")
            ).fetchall()
        return {
            "count": len(rows),
            "facts": [
                {
                    "category": r[0],
                    "key": r[1],
                    "value": r[2],
                    "source": r[3],
                    "updated_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading profile facts: {exc}")


@app.patch("/api/profile/{key}")
def update_profile_fact(key: str, update: ProfileUpdate):
    """Manually set one profile fact (upsert). Powers the manual memory UI."""
    from orchestrator.config import PROFILE_KEY_MAP

    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")

    # Category resolution: explicit flag → existing row → PROFILE_KEY_MAP → system.
    category = update.category
    if not category:
        try:
            with mm.SessionLocal() as session:
                from sqlalchemy import text as _text
                row = session.execute(
                    _text("SELECT category FROM profile_facts WHERE key = :k"),
                    {"k": key},
                ).fetchone()
            if row:
                category = row[0]
        except Exception:
            category = None
    if not category and key in PROFILE_KEY_MAP:
        category = PROFILE_KEY_MAP[key][0]
    if not category:
        category = "system"

    try:
        mm.set_profile_fact(category, key, update.value, source="manual_api")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update profile fact: {exc}")

    return {"status": "updated", "category": category, "key": key, "value": update.value}


@app.delete("/api/profile/{key}")
def delete_profile_fact(key: str):
    """Delete all profile_facts rows with the given key (manual memory cleanup)."""
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")
    try:
        with mm.SessionLocal() as session:
            from sqlalchemy import text as _text
            deleted = session.execute(
                _text("DELETE FROM profile_facts WHERE key = :k"),
                {"k": key},
            ).rowcount
            session.commit()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete profile fact: {exc}")

    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"No profile fact with key '{key}'.")
    return {"status": "deleted", "key": key, "rows": deleted}


# ---------------------------------------------------------------------------
# Schedule / Calendar Events API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/schedule")
def get_schedule(
    date: str | None = Query(default=None, description="ISO date (YYYY-MM-DD) to filter a single day"),
    status: str | None = Query(default=None, description="Filter by status (scheduled, in_progress, completed, cancelled)"),
    category: str | None = Query(default=None, description="Filter by category"),
):
    """Retrieve calendar schedule events from PostgreSQL (optionally one day)."""
    runner = get_runner()
    if not runner.memory_manager:
        raise HTTPException(status_code=503, detail="Database connection unavailable.")
    try:
        from datetime import datetime, time, timezone

        start_date = end_date = None
        if date:
            day = datetime.fromisoformat(str(date)).date()
            start_date = datetime.combine(day, time.min, tzinfo=timezone.utc)
            end_date = datetime.combine(day, time.max, tzinfo=timezone.utc)
        events = runner.memory_manager.get_schedule_events(
            start_date=start_date, end_date=end_date, status=status, category=category,
        )
        return {"count": len(events), "events": events}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading schedule events: {exc}")


@app.get("/api/schedule/availability")
def get_day_grid(
    date: str | None = Query(default=None, description="ISO date (YYYY-MM-DD) or datetime; defaults to today UTC"),
):
    """The 48-slot day grid for a date: slot states, life anchors, and code-computed free windows."""
    runner = get_runner()
    if not runner.memory_manager:
        raise HTTPException(status_code=503, detail="Database connection unavailable.")
    try:
        from datetime import datetime

        d = today_local()
        if date:
            d = datetime.fromisoformat(str(date))
        from orchestrator.harness import build_day_grid

        grid = build_day_grid(runner.memory_manager, d)
        return {
            "date": grid.get("date"),
            "slots": grid.get("slots"),
            "free_windows": grid.get("free_windows"),
            "free_minutes": sum(w.get("duration_min", 0) for w in (grid.get("free_windows") or [])),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading day grid: {exc}")


@app.post("/api/schedule")
def create_schedule_event(event: dict[str, Any]):
    """Create a new schedule event in PostgreSQL."""
    runner = get_runner()
    if not runner.memory_manager:
        raise HTTPException(status_code=503, detail="Database connection unavailable.")
    try:
        res = runner.memory_manager.create_schedule_event(event)
        return {"status": "created", "event": res}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error creating schedule event: {exc}")


@app.patch("/api/schedule/{event_id}")
def update_schedule_event(event_id: str, updates: dict[str, Any]):
    """Update an existing schedule event by ID."""
    runner = get_runner()
    if not runner.memory_manager:
        raise HTTPException(status_code=503, detail="Database connection unavailable.")
    try:
        res = runner.memory_manager.update_schedule_event(event_id, updates)
        if not res:
            raise HTTPException(status_code=404, detail=f"Schedule event '{event_id}' not found.")
        if updates.get("start_time"):
            try:
                runner.memory_manager.clear_event_slots(event_id)
                runner.memory_manager.reflect_schedule_on_day(updates["start_time"])
            except Exception:
                pass
        return {"status": "updated", "event": res}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error updating schedule event: {exc}")


@app.delete("/api/schedule/{event_id}")
def delete_schedule_event(event_id: str):
    """Delete a schedule event by ID (also releases its day-grid slots)."""
    runner = get_runner()
    if not runner.memory_manager:
        raise HTTPException(status_code=503, detail="Database connection unavailable.")
    try:
        runner.memory_manager.clear_event_slots(event_id)
        success = runner.memory_manager.delete_schedule_event(event_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Schedule event '{event_id}' not found.")
        return {"status": "deleted", "event_id": event_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error deleting schedule event: {exc}")


# ---------------------------------------------------------------------------
# DNA Memory API — "What my mentor knows about me" (dna_memory_redesign_v2 §10)
# ---------------------------------------------------------------------------

class MemoryCorrection(BaseModel):
    content: str = Field(..., description="The corrected memory text")
    reason: str = Field(default="user panel correction", description="Why this correction happened")


class StageUpdate(BaseModel):
    company: str = Field(..., description="Company name of the tracked application")
    role_title: str | None = Field(default=None, description="Optional role disambiguator")
    stage: str = Field(..., description="New pipeline stage (applied/screening/interviewing/offer/rejected/withdrawn/wishlist/referral_requested)")
    notes: str | None = Field(default=None, description="Optional note appended to the entry")


class ApplicationCreate(BaseModel):
    company: str = Field(..., description="Company name")
    role_title: str = Field(..., description="Role / position title")
    stage: str = Field(default="applied", description="Initial pipeline stage")
    applied_date: str | None = Field(default=None, description="ISO date (YYYY-MM-DD); defaults to today")
    job_url: str | None = None
    location: str | None = None
    referral_contact: str | None = None
    notes: str | None = None
    jd_text: str | None = Field(default=None, description="Full job description text — stored in the vault, powers tailoring + fit checks")


class ApplicationNote(BaseModel):
    company: str = Field(..., description="Company name of the tracked application")
    role_title: str | None = Field(default=None, description="Optional role disambiguator")
    note: str = Field(..., description="Free-form note appended with a timestamp")


class JdAttach(BaseModel):
    company: str = Field(..., description="Company name of the tracked application")
    role_title: str | None = Field(default=None, description="Optional role disambiguator")
    jd_text: str = Field(..., description="Full job description text to store in the vault")


def _memory_to_dict(record) -> dict[str, Any]:
    return record.model_dump(mode="json")


@app.get("/api/memories/pending")
def list_pending_memories():
    """Unconfirmed mentor inferences awaiting user validation (§10.2)."""
    try:
        store = get_dna_store()
        det = store.get_deterministic()
        pending = det["pending_validation"]
        # Also surface all unconfirmed inferences at/above the validation floor.
        awaiting = store.list_memories(source="mentor_inferred", limit=50)
        awaiting = [m for m in awaiting if not m.user_confirmed and m.confidence >= 0.5]
        seen = {m.id for m in pending}
        combined = pending + [m for m in awaiting if m.id not in seen]
        return {"count": len(combined), "memories": [_memory_to_dict(m) for m in combined]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading pending memories: {exc}")


@app.get("/api/memories")
def list_memories(
    type: str | None = Query(default=None, description="Filter by memory_type"),
    source: str | None = Query(default=None, description="Filter by source"),
    include_archived: bool = Query(default=False),
):
    """List DNA memories for the transparency panel."""
    try:
        store = get_dna_store()
        records = store.list_memories(
            active_only=not include_archived,
            memory_type=type,
            source=source,
            limit=500,
        )
        return {"count": len(records), "memories": [_memory_to_dict(r) for r in records]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error listing memories: {exc}")


@app.post("/api/memories/{memory_id}/confirm")
def confirm_memory(memory_id: str):
    """User affirms a memory — unlocks the confidence ceiling (§4.2)."""
    store = get_dna_store()
    try:
        record = store.confirm_memory(memory_id, by_user=True)
        return {"status": "confirmed", "memory": _memory_to_dict(record)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error confirming memory: {exc}")


@app.post("/api/memories/{memory_id}/correct")
def correct_memory(memory_id: str, correction: MemoryCorrection):
    """User corrects a memory — routes through the contradiction revise path (§6)."""
    store = get_dna_store()
    try:
        record = store.revise_memory(
            memory_id,
            correction.content,
            reason=correction.reason,
            contradiction=True,
        )
        return {"status": "corrected", "memory": _memory_to_dict(record)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error correcting memory: {exc}")


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str):
    """User archives a memory ('forget this'). Kept for audit (§10.1)."""
    store = get_dna_store()
    try:
        success = store.deactivate(memory_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found.")
        return {"status": "deleted", "memory_id": memory_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error deleting memory: {exc}")


# ---------------------------------------------------------------------------
# Job Applications API (job_hunter agent — pipeline board)
# ---------------------------------------------------------------------------

from datetime import datetime, timezone

from agents.Job_Hunter import (
    STALE_APPLICATION_DAYS,
    TERMINAL_STAGES,
    coerce_stage,
    days_since,
    find_application,
)
from agents.Job_Hunter.render import application_dir, application_folder_path

_STALE_PRONE_STAGES = ("wishlist", "applied", "referral_requested", "screening")
_STAGE_ORDER = {
    "wishlist": 0, "applied": 1, "referral_requested": 2, "screening": 3,
    "interviewing": 4, "offer": 5, "rejected": 6, "withdrawn": 7,
}


def _load_pipeline(mm: MemoryManager) -> list[dict[str, Any]]:
    raw = mm.get_profile_fact("career", "job_pipeline") or []
    if not isinstance(raw, list):
        return []
    return [dict(a) for a in raw if isinstance(a, dict)]


def _enrich_application(app: dict[str, Any], now: datetime) -> dict[str, Any]:
    days = days_since(app.get("last_updated"), now)
    stage = app.get("stage", "applied")
    return {
        **app,
        "days_since_update": days,
        "is_stale": bool(days is not None and days >= STALE_APPLICATION_DAYS and stage in _STALE_PRONE_STAGES),
        "is_terminal": stage in TERMINAL_STAGES,
        "has_jd": bool(app.get("jd_path")),
        "has_tailored_resume": bool(app.get("tailored_resume_path")),
    }


def _pipeline_stats(apps: list[dict[str, Any]]) -> dict[str, Any]:
    active = [a for a in apps if not a["is_terminal"]]
    by_stage: dict[str, int] = {}
    for a in apps:
        s = a.get("stage", "unknown")
        by_stage[s] = by_stage.get(s, 0) + 1
    non_wishlist = [a for a in apps if a.get("stage") != "wishlist"]
    responded = [a for a in non_wishlist if a.get("stage") in ("screening", "interviewing", "offer")]
    return {
        "total": len(apps),
        "active": len(active),
        "closed": len(apps) - len(active),
        "stale": sum(1 for a in apps if a["is_stale"]),
        "response_rate": round(100 * len(responded) / max(1, len(non_wishlist))),
        "by_stage": by_stage,
    }


@app.get("/api/applications")
def list_applications():
    """Job pipeline: enriched current-state list + computed stats for the board."""
    runner = get_runner()
    if runner.memory_manager is None:
        raise HTTPException(status_code=503, detail="Database not connected.")
    try:
        now = datetime.now(timezone.utc)
        apps = [_enrich_application(a, now) for a in _load_pipeline(runner.memory_manager)]
        apps.sort(key=lambda a: (_STAGE_ORDER.get(a.get("stage", "applied"), 1), -(a.get("days_since_update") or 0)))
        return {"count": len(apps), "stats": _pipeline_stats(apps), "applications": apps}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading job pipeline: {exc}")


@app.get("/api/applications/history")
def application_history(limit: int = Query(default=30, ge=1, le=100)):
    """Append-only timeline of job_application episodic events (newest first)."""
    runner = get_runner()
    if runner.memory_manager is None:
        raise HTTPException(status_code=503, detail="Database not connected.")
    try:
        events = runner.memory_manager.query_episodic(event_type="job_application", last_n=limit)
        return {"count": len(events), "events": events}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading application history: {exc}")

@app.post("/api/applications/stage")
def update_application_stage(update: StageUpdate):
    """
    Move a tracked application to a new stage from the board UI.
    Same write semantics as the job_hunter application_logger: profile
    current-state (wholesale list) + one episodic job_application event.
    """
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")

    stage = coerce_stage(update.stage)
    if not stage:
        raise HTTPException(status_code=400, detail=f"Unknown stage '{update.stage}'.")

    pipeline = _load_pipeline(mm)
    match = find_application(pipeline, update.company, update.role_title)
    if not match:
        raise HTTPException(status_code=404, detail=f"No tracked application for '{update.company}'.")

    now = datetime.now(timezone.utc)
    old_stage = match.get("stage", "applied")
    match["stage"] = stage
    match["last_updated"] = now.isoformat()
    if update.notes and update.notes.strip():
        prior = (match.get("notes") or "").strip()
        match["notes"] = f"{prior}\n[{now.date().isoformat()}] {update.notes.strip()}".strip()

    try:
        mm.set_profile_fact("career", "job_pipeline", pipeline, source="api_ui")
        mm.add_episodic_event(
            source_agent="job_hunter",
            event_type="job_application",
            content=f"Job application: {match.get('role_title')} @ {match.get('company')} ({stage})",
            payload={
                "company": match.get("company"),
                "role": match.get("role_title"),
                "stage": stage,
                "notes": update.notes,
            },
            tags=["job_application", stage],
            importance=3,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist stage update: {exc}")

    return {
        "status": "updated",
        "company": match.get("company"),
        "role_title": match.get("role_title"),
        "old_stage": old_stage,
        "new_stage": stage,
        "stats": _pipeline_stats([_enrich_application(a, now) for a in pipeline]),
    }


def _write_jd_to_vault(company: str, role_title: str, jd_text: str) -> str | None:
    """Write JD.md into the application's vault folder. Returns the path, None on failure."""
    text = (jd_text or "").strip()
    if not text:
        return None
    try:
        folder = application_dir(OBSIDIAN_VAULT_PATH, OBSIDIAN_CAREER_FOLDER, company, role_title or "Role")
        jd_path = folder / "JD.md"
        header = (
            "---\ntype: job_description\n"
            f"company: '{company}'\nrole: '{role_title or 'Role'}'\nsource: board_form\n---\n\n"
        )
        jd_path.write_text(header + text, encoding="utf-8")
        return str(jd_path)
    except Exception as exc:
        print(f"[API] JD vault write failed: {exc}")
        return None


@app.post("/api/applications")
def create_application(payload: ApplicationCreate):
    """
    Create a pipeline entry from the board's Add Application form.
    Same write semantics as job_hunter's application_logger: dedup by
    company+role (an existing entry is refreshed, never duplicated),
    wholesale profile write + one episodic job_application event.
    """
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")

    stage = coerce_stage(payload.stage)
    if not stage:
        raise HTTPException(status_code=400, detail=f"Unknown stage '{payload.stage}'.")

    pipeline = _load_pipeline(mm)
    match = find_application(pipeline, payload.company, payload.role_title)
    now = datetime.now(timezone.utc)

    if match:
        # Refresh path — the default stage never silently downgrades an
        # existing entry (use the card dropdown for real transitions).
        if stage != "applied" or match.get("stage") in (None, "wishlist"):
            match["stage"] = stage
        match["last_updated"] = now.isoformat()
        for k in ("job_url", "location", "referral_contact", "notes"):
            v = getattr(payload, k)
            if v:
                match[k] = v
        status = "refreshed"
    else:
        match = {
            "company": payload.company.strip(),
            "role_title": payload.role_title.strip(),
            "stage": stage,
            "applied_date": payload.applied_date or now.date().isoformat(),
            "last_updated": now.isoformat(),
            "referral_contact": payload.referral_contact,
            "job_url": payload.job_url,
            "location": payload.location,
            "notes": payload.notes,
        }
        pipeline.append(match)
        status = "created"

    # A pasted JD is stored as a vault artifact and linked onto the entry —
    # the job_hunter reads it back for tailoring / fit checks.
    if payload.jd_text and payload.jd_text.strip():
        jd_path = _write_jd_to_vault(match["company"], match.get("role_title") or "Role", payload.jd_text)
        if jd_path:
            match["jd_path"] = jd_path

    try:
        mm.set_profile_fact("career", "job_pipeline", pipeline, source="api_ui")
        mm.add_episodic_event(
            source_agent="job_hunter",
            event_type="job_application",
            content=f"Job application: {match['role_title']} @ {match['company']} ({match['stage']})",
            payload={
                "company": match["company"],
                "role": match["role_title"],
                "stage": match.get("stage", "applied"),
                "applied_date": match.get("applied_date"),
                "referral_contact": match.get("referral_contact"),
                "job_url": match.get("job_url"),
                "location": match.get("location"),
                "notes": match.get("notes"),
            },
            tags=["job_application", match.get("stage", "applied")],
            importance=3,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist application: {exc}")

    return {
        "status": status,
        "application": _enrich_application(match, now),
        "stats": _pipeline_stats([_enrich_application(a, now) for a in pipeline]),
    }


@app.post("/api/applications/note")
def add_application_note(payload: ApplicationNote):
    """
    Append a timestamped note to a tracked application WITHOUT a stage
    transition — the episodic event is tagged kind=note so the timeline
    stays honest (no phantom stage changes).
    """
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")

    pipeline = _load_pipeline(mm)
    match = find_application(pipeline, payload.company, payload.role_title)
    if not match:
        raise HTTPException(status_code=404, detail=f"No tracked application for '{payload.company}'.")

    now = datetime.now(timezone.utc)
    prior = (match.get("notes") or "").strip()
    match["notes"] = f"{prior}\n[{now.date().isoformat()}] {payload.note.strip()}".strip()
    match["last_updated"] = now.isoformat()

    try:
        mm.set_profile_fact("career", "job_pipeline", pipeline, source="api_ui")
        mm.add_episodic_event(
            source_agent="job_hunter",
            event_type="job_application",
            content=f"Note on {match.get('role_title')} @ {match.get('company')}: {payload.note.strip()[:140]}",
            payload={
                "company": match.get("company"),
                "role": match.get("role_title"),
                "stage": match.get("stage"),
                "notes": payload.note.strip(),
                "kind": "note",
            },
            tags=["job_application", "note"],
            importance=2,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist note: {exc}")

    return {"status": "noted", "application": _enrich_application(match, now)}


@app.post("/api/applications/jd")
def attach_job_description(payload: JdAttach):
    """
    Attach/replace the job description on an existing pipeline entry.
    Writes JD.md into the application's vault folder and links jd_path so
    tailor_resume / assess_fit can use it without a re-paste.
    """
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")

    if not payload.jd_text.strip():
        raise HTTPException(status_code=400, detail="jd_text is empty.")

    pipeline = _load_pipeline(mm)
    match = find_application(pipeline, payload.company, payload.role_title)
    if not match:
        raise HTTPException(status_code=404, detail=f"No tracked application for '{payload.company}'.")

    jd_path = _write_jd_to_vault(match["company"], match.get("role_title") or "Role", payload.jd_text)
    if not jd_path:
        raise HTTPException(status_code=500, detail="Failed to write JD.md to the vault.")

    now = datetime.now(timezone.utc)
    match["jd_path"] = jd_path
    match["last_updated"] = now.isoformat()
    try:
        mm.set_profile_fact("career", "job_pipeline", pipeline, source="api_ui")
        mm.add_episodic_event(
            source_agent="job_hunter",
            event_type="job_application",
            content=f"JD attached: {match.get('role_title')} @ {match.get('company')}",
            payload={
                "company": match.get("company"),
                "role": match.get("role_title"),
                "stage": match.get("stage"),
                "kind": "jd_attached",
            },
            tags=["job_application", "jd_attached"],
            importance=2,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist JD link: {exc}")

    return {"status": "attached", "jd_path": jd_path, "application": _enrich_application(match, now)}


_JOB_ARTIFACT_FILES = {
    "jd": "JD.md",
    "resume": "Tailored Resume.md",
    "fit": "Fit Notes.md",
}


@app.get("/api/applications/artifact")
def read_application_artifact(
    company: str = Query(...),
    role: str = Query(default=""),
    kind: str = Query(default="resume", description="jd | resume | fit"),
):
    """Read a markdown artifact (JD / Tailored Resume / Fit Notes) from the vault."""
    filename = _JOB_ARTIFACT_FILES.get(kind)
    if not filename:
        raise HTTPException(status_code=400, detail=f"Unknown artifact kind '{kind}'.")

    folder = application_folder_path(OBSIDIAN_VAULT_PATH, OBSIDIAN_CAREER_FOLDER, company, role or "Role")
    if not (folder / filename).exists():
        # Role strings drift between logging and tailoring — fall back to the
        # company's folder regardless of the role suffix.
        apps_root = Path(OBSIDIAN_VAULT_PATH) / OBSIDIAN_CAREER_FOLDER / "Applications"
        company_prefix = application_folder_path(OBSIDIAN_VAULT_PATH, OBSIDIAN_CAREER_FOLDER, company, "").name
        if apps_root.exists():
            for candidate in sorted(apps_root.iterdir()):
                if candidate.is_dir() and candidate.name.startswith(company_prefix) and (candidate / filename).exists():
                    folder = candidate
                    break

    file_path = folder / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"'{filename}' not found for {company}.")
    try:
        return {"filename": filename, "path": str(file_path), "body": file_path.read_text(encoding="utf-8")}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading artifact: {exc}")


@app.delete("/api/applications/{company}")
def delete_application(company: str, role: str = Query(default="")):
    """
    Remove an entry from the job_pipeline (board UI). The episodic
    job_application history is intentionally left intact as an audit trail.
    """
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")

    pipeline = _load_pipeline(mm)
    match = find_application(pipeline, company, role or None)
    if not match:
        raise HTTPException(status_code=404, detail=f"No tracked application for '{company}'.")

    kept = [a for a in pipeline if a is not match]
    try:
        mm.set_profile_fact("career", "job_pipeline", kept, source="api_ui")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete application: {exc}")

    now = datetime.now(timezone.utc)
    return {
        "status": "deleted",
        "company": match.get("company"),
        "role_title": match.get("role_title"),
        "stats": _pipeline_stats([_enrich_application(a, now) for a in kept]),
    }


# ---------------------------------------------------------------------------
# Job Board API (job_hunter agent — single unified job_pipeline board)
# job_pipeline (profile_facts career.job_pipeline) is the single storage for
# BOTH search-result listings and application-pipeline entries. Search results
# are written there by job_hunter's digest_formatter_node as wishlist rows
# (source="serpapi"); the /api/applications* endpoints mutate the same rows.
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def list_job_board():
    """The unified job board: search-result listings + applications in one list."""
    runner = get_runner()
    if runner.memory_manager is None:
        raise HTTPException(status_code=503, detail="Database not connected.")
    try:
        now = datetime.now(timezone.utc)
        apps = [_enrich_application(a, now) for a in _load_pipeline(runner.memory_manager)]
        apps.sort(key=lambda a: (_STAGE_ORDER.get(a.get("stage", "applied"), 1), -(a.get("days_since_update") or 0)))
        return {"count": len(apps), "stats": _pipeline_stats(apps), "listings": apps}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading job board: {exc}")


@app.delete("/api/jobs/{listing_id}")
def delete_job_listing(listing_id: str):
    """Remove one row (a search-result listing or an application) from the board."""
    runner = get_runner()
    mm = runner.memory_manager
    if mm is None:
        raise HTTPException(status_code=503, detail="Database not connected.")
    board = _load_pipeline(mm)
    kept = [row for row in board if str(row.get("id", "")) != listing_id]
    if len(kept) == len(board):
        raise HTTPException(status_code=404, detail=f"No job board row with id '{listing_id}'.")
    try:
        mm.set_profile_fact("career", "job_pipeline", kept, source="api_ui")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete listing: {exc}")
    return {"status": "deleted", "listing_id": listing_id, "count": len(kept)}


# ---------------------------------------------------------------------------
# Root
#
# The legacy vanilla dashboard (`ui/`) was removed — the modern dashboard is the
# Next.js app in `web/` (see COMMANDS.md) and talks to this API over /api/*.
# Serving a pointer here beats letting `/` 404 confusingly.
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    """Describe the real entry points instead of serving a removed dashboard."""
    return {
        "service": "Personal AI Mentor API",
        "docs": "/docs",
        "health": "/api/health",
        "dashboard": "cd web && npm run dev  →  http://localhost:3000",
        "mentor_tools": "/tools/manifest",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
