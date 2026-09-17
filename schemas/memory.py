"""
memory.py

Defines the orchestrator's persistent "DNA memory" — the single source of
truth about Nikhil that survives across sessions — and the MemorySlice,
which is the trimmed-down subset of that memory handed to any one stateless
sub-agent for a single task.

Design principle: DNAMemory is the ONLY thing that persists. Sub-agents never
read or write it directly — the orchestrator reads it, builds a MemorySlice,
sends that to an agent, gets back a memory_delta (see agent_io.py), and
merges the delta back into DNAMemory. This file only defines the *shape* of
that data, not the read/write logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EmploymentStatus(str, Enum):
    EMPLOYED = "employed"
    UNEMPLOYED_JOB_SEARCHING = "unemployed_job_searching"
    FREELANCING = "freelancing"
    STUDYING = "studying"


class ProjectStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    ABANDONED = "abandoned"


class ApplicationStage(str, Enum):
    WISHLIST = "wishlist"
    APPLIED = "applied"
    REFERRAL_REQUESTED = "referral_requested"
    SCREENING = "screening"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class SkillRating(BaseModel):
    name: str
    rating_out_of_10: float
    last_self_assessed: Optional[datetime] = None
    notes: Optional[str] = None


class Project(BaseModel):
    name: str
    description: str
    status: ProjectStatus
    tech_stack: list[str] = Field(default_factory=list)
    repo_url: Optional[str] = None
    deployed_url: Optional[str] = None
    last_worked_on: Optional[datetime] = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v: Any) -> ProjectStatus:
        if isinstance(v, ProjectStatus):
            return v
        if isinstance(v, str):
            clean = v.strip().lower().replace(" ", "_").replace("-", "_")
            if clean in ("active", "in_progress", "inprogress", "building", "ongoing"):
                return ProjectStatus.ACTIVE
            if clean in ("planning", "planned", "plan", "concept"):
                return ProjectStatus.PLANNING
            if clean in ("paused", "pause", "on_hold", "hold"):
                return ProjectStatus.PAUSED
            if clean in ("done", "completed", "complete", "finished"):
                return ProjectStatus.DONE
            if clean in ("abandoned", "cancelled", "canceled", "dropped"):
                return ProjectStatus.ABANDONED
        return ProjectStatus.ACTIVE


class JobApplication(BaseModel):
    company: str
    role_title: str
    job_id: Optional[str] = None
    stage: ApplicationStage
    referral_contact: Optional[str] = None
    applied_date: Optional[datetime] = None
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: Optional[str] = None
    # job_hunter enrichments (all optional — backward compatible with existing rows)
    job_url: Optional[str] = None
    location: Optional[str] = None
    jd_path: Optional[str] = None               # vault path to the saved job description
    tailored_resume_path: Optional[str] = None  # vault path to the tailored resume for this JD


class LearningPathEntry(BaseModel):
    topic: str
    week: int
    order: int = 0
    status: str = "not_started"   # not_started | in_progress | completed | skipped
    planned_date: Optional[datetime] = None
    completed_date: Optional[datetime] = None
    source: Optional[str] = None
    notes: Optional[str] = None


class ActiveLearningPath(BaseModel):
    title: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_weeks: Optional[int] = None
    weekly_time_budget_hours: Optional[float] = None
    current_week: int = 1
    entries: list[LearningPathEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Topic Graph  (§5.5 of architecture)
# ---------------------------------------------------------------------------

class NodeAnchor(BaseModel):
    """
    Where one concept actually lives in an artifact — the bridge between a
    curriculum node and the thing it teaches.

    This is what makes a repo-derived curriculum *assessable*: the claim "this
    node is about X" is only checkable because X sits at a specific file/symbol.
    `orchestrator/memory/roadmap.py:verify_anchors()` resolves every anchor
    against the workspace sandbox, so an anchor that does not resolve is caught
    by code instead of being trusted from the model's prose. That resolution
    ratio is the falsifiable signal the Repo-to-Curriculum plan (§5) asks for.

    kind:
      "file"     → the concept is embodied by this file as a whole
      "symbol"   → a function/class/constant at `path` (optionally `line`)
      "decision" → a design decision documented at `path`
    """
    kind: Literal["file", "symbol", "decision"] = "symbol"
    path: str                                  # repo-relative, e.g. "orchestrator/memory/store.py"
    symbol: Optional[str] = None               # e.g. "_get_embed_model"
    line: Optional[int] = None
    note: Optional[str] = None                 # why this anchor matters


class NodeCheckpoint(BaseModel):
    """
    The per-node comprehension check — asked against the artifact, not the prose.

    Phase D of the Repo-to-Curriculum plan. These fields exist from the start so
    the manifest/note format never needs a second migration when assessment
    lands: `evidence_ref` is filled by an assessment run (a memory id, a
    transcript turn), and that reference is what feeds the learner model.
    """
    question: Optional[str] = None
    rubric: Optional[str] = None
    evidence_ref: Optional[str] = None


class RoadmapSource(BaseModel):
    """
    Where a roadmap came from — the provenance that makes it re-derivable.

    `kind="topic"` is today's behaviour (decomposed from a goal string).
    `kind="repo"` is the Repo-to-Curriculum case: the concepts were extracted
    from real code, so `commit` pins WHICH revision the curriculum describes
    and `stopping_rule` records the boundary the extractor committed to
    (comprehension vs authorship — see the blueprint's G3).
    """
    kind: Literal["topic", "repo"] = "topic"
    path: Optional[str] = None                 # repo-relative for kind="repo"
    commit: Optional[str] = None               # git sha the inventory was taken at
    stopping_rule: Optional[str] = None        # "comprehension" | "authorship"


class TopicNode(BaseModel):
    """
    One node in a Topic Graph — a single learnable skill or concept.

    Status semantics:
      None          → not started yet
      "in_progress" → currently being studied
      "done"        → completed and self-verified
      "skipped"     → deliberately skipped

    "available" and "locked" are NOT stored here — they are computed from
    the prerequisites list on read (a node is available once all its
    prerequisite nodes are "done"). Storing derived state invites drift;
    computing it on read keeps it always consistent.

    A node's note in the vault has three ownership zones (see
    `orchestrator/memory/roadmap.py`): the GENERATED tutorial body (the
    specialist regenerates it), "🏫 Deepened in session" (the mentor appends,
    never rewritten), and "My Notes" (the learner's; never touched). Only the
    structural fields here — id, prerequisites, day, anchors, checkpoint —
    are authoritative, and they live in the roadmap manifest.
    """
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str
    prerequisites: list[str] = Field(default_factory=list)   # ids of prerequisite nodes
    status: Optional[Literal["not_started", "in_progress", "done", "skipped"]] = "not_started"
    estimated_hours: float = 1.0
    resources: list[str] = Field(default_factory=list)        # URLs, book/course titles
    notes: Optional[str] = None

    # --- Roadmap-store additions. All defaulted, so every note and stored
    #     graph written before this existed parses unchanged. ---
    day: Optional[int] = None
    content_type: Optional[Literal["conceptual", "algorithmic", "hands_on_code", "reference"]] = None
    anchors: list[NodeAnchor] = Field(default_factory=list)
    checkpoint: Optional[NodeCheckpoint] = None


class TopicGraph(BaseModel):
    """
    A directed acyclic graph of learning topics for one major learning goal.

    Used by:
    - Daily Planner: to find available (unlocked) nodes and offer them as
      candidate plan items, linked via PlanItem.linked_goal.
    - Learning Mentor: to mark nodes done when he reports a topic complete,
      which recomputes the frontier of available nodes.

    Persisted two ways, deliberately separated by ownership:
      - **Vault manifest** (`<curriculum_root>/<graph_id>/roadmap.yaml`) — the
        authoritative structure (identity, prerequisites, status, anchors,
        provenance). Written and validated only by
        `orchestrator/memory/roadmap.py`.
      - **`profile_facts["topic_graphs"]`** — a cache for readers that have no
        vault access.

    This docstring previously claimed the graph was persisted under
    `topic_graph_{topic_id}`; nothing ever wrote that key. The manifest is now
    the single authoritative home, which is what lets the notes themselves be
    safely hand-editable.
    """
    topic_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str                          # e.g. "AI Agent Systems & LangGraph"
    nodes: dict[str, TopicNode] = Field(default_factory=dict)
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Roadmap-store additions (all defaulted; older graphs parse unchanged) ---
    schema_version: int = 1             # manifest format version
    source: RoadmapSource = Field(default_factory=RoadmapSource)
    rel_path: Optional[str] = None      # e.g. "langgraph_basics" — the roadmap's folder
    stopping_rule: Optional[str] = None # mirrors source.stopping_rule for easy filtering

    # The declared time budget. Stored so the TIME PLAN can be checked rather
    # than assumed: the live vault holds a "4-Day" roadmap whose nodes sum to 22
    # hours, i.e. 5.5h/day against a ~3h/day intent, and nothing ever noticed.
    # Only `roadmap.allocate_days()` may assign `day` values from these.
    target_days: Optional[int] = None
    hours_per_day: Optional[float] = None


# ---------------------------------------------------------------------------
# Plan schemas
# ---------------------------------------------------------------------------

class PlanTask(BaseModel):
    """
    Legacy plan task — kept for backward-compatibility with plans stored
    before the PlanItem schema was introduced. New plans use PlanItem.
    """
    category: str           # learning | linkedin | job_search | project | admin | break
    description: str
    duration_minutes: Optional[int] = None
    status: str = "planned" # planned | done | partially_done | skipped | moved
    source: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanItem(BaseModel):
    """
    One item in a daily plan — the new, richer schema (§5.1).

    Key additions over PlanTask:
    - id: stable reference for tool-call modifications (no full regen needed)
    - priority: must / should / nice-to-have (drives trim logic when over time)
    - linked_goal: foreign key into a TopicNode in the Topic Graph
    - duration_min: replaces duration_minutes (shorter)
    - scheduled_time: optional time-blocking slot
    - richer status Literal
    """
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str
    category: Literal[
        "learning",     # topic-graph linked learning block
        "project",      # portfolio / side-project work
        "job_search",   # applications, interview prep, networking
        "linkedin",     # content creation
        "health",       # exercise, rest, mental health
        "admin",        # fixed obligations, errands
        "break",        # scheduled recharge
    ]
    priority: Literal["must", "should", "nice-to-have"] = "should"
    scheduled_time: Optional[str] = None        # "09:00" — optional time-blocking
    duration_min: int = 30
    status: Literal["pending", "in_progress", "done", "skipped", "moved"] = "pending"
    linked_goal: Optional[str] = None           # TopicNode.id this item maps to
    notes: Optional[str] = None


class DailyPlan(BaseModel):
    """
    A single day's proposed schedule.

    Both `items` (new PlanItem list) and `tasks` (legacy PlanTask list) are
    present. New plans populate `items`; old stored plans have `tasks`.
    On read, prefer `items` if non-empty.
    """
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    generated_by: str = "orchestrator"
    energy_level_assumed: Optional[int] = None
    total_available_minutes: Optional[int] = None

    # New schema (populated by updated planner)
    items: list[PlanItem] = Field(default_factory=list)

    # Legacy schema (kept to read old stored plans without migration)
    tasks: list[PlanTask] = Field(default_factory=list)

    version: int = 1
    reflection: Optional[str] = None   # end-of-day review note
    notes: Optional[str] = None


class ScheduleEvent(BaseModel):
    """
    Generic calendar schedule event stored in PostgreSQL.
    Supports read, create, update, delete operations by the Daily Coach agent.
    """
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str
    category: str = "learning"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_min: int = 30
    status: Literal["scheduled", "in_progress", "completed", "cancelled", "postponed"] = "scheduled"
    priority: Literal["must", "should", "nice-to-have"] = "should"
    linked_goal: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



class LearningLogEntry(BaseModel):
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    topics: list[str] = Field(default_factory=list)
    source: Optional[str] = None
    confirmed_by_user: bool = False


class ActivityLogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str
    summary: str
    task_type: Optional[str] = None
    linked_memory_delta: dict[str, Any] = Field(default_factory=dict)


class Preferences(BaseModel):
    content_tone: Optional[str] = None
    preferred_language: str = "en"
    quiet_hours: Optional[str] = None
    linkedin_posting_frequency: Optional[str] = None
    do_not_disturb_topics: list[str] = Field(default_factory=list)


class SystemFlags(BaseModel):
    paused_agents: list[str] = Field(default_factory=list)
    debug_mode: bool = False
    last_manual_review: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Procedural Memory Schemas (§3.4 & §5.4.3 of architecture)
# ---------------------------------------------------------------------------

class ProceduralRule(BaseModel):
    """
    Learned behavioral rule or habit specific to Nikhil.
    e.g. "User prefers directive tone over exploratory questions during morning planning"
    """
    rule_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    domain: Literal["planning", "learning", "mindset", "social", "general"] = "general"
    rule: str
    source: Literal["feedback_loop", "user_explicit", "system_default"] = "feedback_loop"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    observed_count: int = 1
    last_validated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["active", "proposed", "deprecated"] = "active"


class MindsetCalibration(BaseModel):
    """
    Calibration parameters for mentor tone and framing (§5.4.3).
    """
    directness: Literal["blunt", "moderate", "gentle"] = "moderate"
    nudge_frequency_cap: str = "max 1x/day"
    framing_that_lands: list[str] = Field(default_factory=list)
    framing_that_bounces: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# DNAMemory — top-level persistent object
# ---------------------------------------------------------------------------

class DNAMemory(BaseModel):
    schema_version: str = "1.0"
    user_id: str = "nikhil"
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # identity
    name: str = "Nikhil"
    education: Optional[str] = None
    location: Optional[str] = None
    long_term_goal: Optional[str] = None

    # career
    employment_status: EmploymentStatus = EmploymentStatus.UNEMPLOYED_JOB_SEARCHING
    current_role: Optional[str] = None
    target_roles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    applications: list[JobApplication] = Field(default_factory=list)

    # skills & projects
    skills: list[SkillRating] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)

    # learning
    active_learning_path: Optional[ActiveLearningPath] = None
    learning_log: list[LearningLogEntry] = Field(default_factory=list)
    learning_streak_days: int = 0

    # Topic graphs are stored as separate profile_facts keys
    # (topic_graph_{id}) and loaded dynamically by the memory manager,
    # not embedded here (they can be large and are graph-structured).

    # planning & schedule
    daily_plans: list[DailyPlan] = Field(default_factory=list)
    schedule_events: list[ScheduleEvent] = Field(default_factory=list)

    # preferences & system
    preferences: Preferences = Field(default_factory=Preferences)
    flags: SystemFlags = Field(default_factory=SystemFlags)

    # procedural rules & mindset calibration (§3.4, §5.4.3)
    procedural_rules: list[ProceduralRule] = Field(default_factory=list)
    mindset_calibration: MindsetCalibration = Field(default_factory=MindsetCalibration)

    # self-reported bandwidth
    energy_level: Optional[int] = None

    # rolling activity log
    recent_activity: list[ActivityLogEntry] = Field(default_factory=list)

    # per-agent private memory
    agent_private_memory: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# MemorySlice — trimmed context sent to a sub-agent
# ---------------------------------------------------------------------------

class MemorySlice(BaseModel):
    """
    What the orchestrator hands to a stateless sub-agent for one task.
    Only the fields relevant to that agent/task_type are included.

    relevant_profile is a loose dict (not a partial DNAMemory) because
    which fields matter differs per agent and per task type.
    """
    agent_name: str
    task_type: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    relevant_profile: dict[str, Any] = Field(default_factory=dict)
    # planner skill:   {"topic_graphs": [...], "energy_level": 4, "projects": [...]}
    # learner skill:   {"active_learning_path": {...}, "topic_graphs": [...], "learning_log": [...]}
    # linkedin_writer:  {"projects": [...], "tone": "...", "recent_post_topics": [...]}

    recent_activity: list[ActivityLogEntry] = Field(default_factory=list)
    private_memory: Optional[dict[str, Any]] = None
    constraints: dict[str, Any] = Field(default_factory=dict)