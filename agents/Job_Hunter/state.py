"""
agents/Job_Hunter/state.py

LangGraph state + data models for the Job Hunter agent.

Three-layer resume architecture:
  Layer 1 — MasterResume: YAML source of truth in the Obsidian vault
            (Career/master_resume.yaml), human-editable.
  Layer 2 — TailoredResumeDraft: the ONLY thing the tailoring LLM produces —
            it selects/rephrases master bullets by ID, never free-form text,
            never LaTeX. This makes fabrication structurally auditable.
  Layer 3 — rendering (render.py): fixed Jinja2 LaTeX template + tectonic.

One graph, six branches:
  "tailor_resume"              → jd_analyzer → resume_tailor → quality_critic → render
  "log_application"            → application_logger
  "update_application_status"  → application_logger
  "job_search_review"          → pipeline_analyzer
  "search_jobs"                → job_searcher → job_scorer → (persist_search_results)
  "add_manual_job"             → manual_job_adder
  "delete_job"                 → job_deleter

Job board:
  job_pipeline (JobHunterState) is the canonical table — a list of
  JobPipelineEntry dicts persisted via memory_delta into profile_facts.
  Every branch that creates, scores, or mutates a row does so through
  JobPipelineEntry so the shape stays consistent across search results,
  manual entries, and status updates.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

from schemas import AgentResult, AgentTask

# ---------------------------------------------------------------------------
# Layer 1 — Master resume data model (source of truth: Career/master_resume.yaml)
# ---------------------------------------------------------------------------


class Bullet(BaseModel):
    """One resume bullet. `id` is the stable handle the LLM references when tailoring."""

    id: str
    text: str
    tags: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)  # informational — the guard re-derives from text
    optional: bool = False  # droppable when the one-page budget overflows


class Experience(BaseModel):
    id: str
    company: str
    role: str
    location: str = ""
    period: str = ""  # e.g. "Jun 2025 – Apr 2026"
    bullets: list[Bullet] = Field(default_factory=list)


class Project(BaseModel):
    id: str
    name: str
    tech: list[str] = Field(default_factory=list)
    link: Optional[str] = None
    bullets: list[Bullet] = Field(default_factory=list)

class Education(BaseModel):
    institution: str
    degree: str
    period: str = ""
    details: list[str] = Field(default_factory=list)


class SkillCategory(BaseModel):
    label: str  # "Languages", "AI / ML", ...
    items: list[str] = Field(default_factory=list)


class Contact(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    website: Optional[str] = None


class MasterResume(BaseModel):
    name: str
    headline: Optional[str] = None
    contact: Contact = Field(default_factory=Contact)
    summary: Optional[str] = None
    skills: list[SkillCategory] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)

    def bullet_map(self) -> dict[str, Bullet]:
        """bullet_id → Bullet across all experience + project entries."""
        out: dict[str, Bullet] = {}
        for entry in (*self.experience, *self.projects):
            for b in entry.bullets:
                out[b.id] = b
        return out

    def entry_map(self) -> dict[str, Experience | Project]:
        """entry_id → Experience/Project."""
        return {e.id: e for e in (*self.experience, *self.projects)}

    def skill_labels(self) -> set[str]:
        return {c.label for c in self.skills}


# ---------------------------------------------------------------------------
# LLM structured-output schemas
# ---------------------------------------------------------------------------

# Canonical pipeline stages (mirror schemas.memory.ApplicationStage as plain
# strings for prompt simplicity — coercion helpers live in job_hunter.py).
APPLICATION_STAGES = (
    "wishlist",
    "applied",
    "referral_requested",
    "screening",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
)

ActionType = Literal[
    "tailor_resume",
    "log_application",
    "update_status",
    "review",
    "assess_fit",
    "search_jobs",
    "add_manual_job",
    "delete_job",
]


class ParsedRequest(BaseModel):
    """What the user wants from the job hunter, extracted from raw instructions."""

    action: ActionType
    company: Optional[str] = None
    role_title: Optional[str] = None
    jd_text: Optional[str] = None        # pasted job description, verbatim
    stage: Optional[str] = None          # coerced to APPLICATION_STAGES in code
    applied_date: Optional[str] = None   # ISO date if mentioned
    referral_contact: Optional[str] = None
    job_url: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    job_id: Optional[str] = None         # explicit row id, or resolved "#N" from search_cache


class JDAnalysis(BaseModel):
    job_title: str = ""
    company: str = ""
    seniority: str = ""  # intern | junior | mid | senior | staff
    required_keywords: list[str] = Field(default_factory=list)
    preferred_keywords: list[str] = Field(default_factory=list)
    domain_focus: list[str] = Field(default_factory=list)
    summary: str = ""  # 1-2 sentence honest read of what the employer wants


# ---------------------------------------------------------------------------
# Job search models (search_jobs branch)
# ---------------------------------------------------------------------------

class JobSearchParams(BaseModel):
    """Extracted search intent from the user's query."""
    role_queries: list[str] = Field(default_factory=list)   # ["AI Engineer LLM", "Data Scientist"]
    locations: list[str] = Field(default_factory=list)       # ["India", "Remote"]
    remote_ok: bool = True
    max_experience_years: int = 3                          # drop roles requiring more
    exclude_keywords: list[str] = Field(default_factory=list)
    max_results: int = 10
    include_ats_direct: bool = True


class ScoredJobListing(BaseModel):
    """A single job listing after filtering and LLM scoring — shown in the digest.
    Ephemeral / per-turn shape. Rows that get kept move into JobPipelineEntry."""
    rank: int
    title: str
    company: str
    location: str
    source_platform: str
    apply_url: str
    fit_score: int                              # 0–100
    match_reasons: list[str] = Field(default_factory=list)  # 2-3 honest strengths
    honest_gaps: list[str] = Field(default_factory=list)    # 1-2 real gaps
    recommendation: str = ""                    # "apply_now" | "tailor_first" | "skip"
    description_snippet: str = ""              # first 300 chars, for display
    description_full: str = ""                 # full JD for tailoring handoff
    salary_range: Optional[str] = None
    posted_date: Optional[str] = None
    description_truncated: bool = False
    experience_required: Optional[str] = None  # e.g. "2-4 years" — a board column
    is_remote: bool = False


class TailoredBullet(BaseModel):
    source_id: str = Field(description="ID of the master-resume bullet this derives from")
    text: str = Field(
        description="Final bullet text — lightly rephrased for the JD, every number/metric preserved verbatim from the source bullet"
    )
    reason: str = ""


class TailoredEntry(BaseModel):
    """Tailored bullet selection for one master experience/project entry."""

    source_id: str = Field(description="ID of the master experience or project")
    bullets: list[TailoredBullet] = Field(default_factory=list)


class TailoredResumeDraft(BaseModel):
    """Layer 2 — the ONLY thing the tailoring LLM produces. Never LaTeX, never free text."""

    summary_line: Optional[str] = None
    skills: list[SkillCategory] = Field(
        default_factory=list,
        description="Skill categories reordered for the JD; labels and items must come from the master resume",
    )
    experience: list[TailoredEntry] = Field(default_factory=list)
    projects: list[TailoredEntry] = Field(default_factory=list)
    changes_summary: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Job board — the persisted table (job_pipeline)
# ---------------------------------------------------------------------------


def _dedup_key(company: str, title: str, location: str = "") -> str:
    """Stable hash used to prevent the same posting being added twice —
    from repeated searches, or a manual entry duplicating a search result."""

    def norm(value: str | None) -> str:
        return (value or "").strip().lower()

    raw = f"{norm(company)}|{norm(title)}|{norm(location)}"
    return hashlib.sha256(raw.encode()).hexdigest()


class JobPipelineEntry(BaseModel):
    """One row in the job board. This is the canonical unit stored in
    profile_facts['job_pipeline'] — every dict in job_pipeline is a
    .model_dump() of one of these. search_jobs, add_manual_job, and
    update_status all read/write through this shape."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str
    company: str
    location: str = ""
    apply_url: Optional[str] = None
    source: Literal["serpapi", "manual"] = "manual"
    stage: str = "wishlist"  # must be one of APPLICATION_STAGES
    fit_score: Optional[int] = None
    match_reasons: list[str] = Field(default_factory=list)
    honest_gaps: list[str] = Field(default_factory=list)
    description_snippet: str = ""
    description_full: str = ""
    salary_range: Optional[str] = None
    posted_date: Optional[str] = None
    applied_date: Optional[str] = None
    referral_contact: Optional[str] = None
    notes: str = ""
    resume_tailored: bool = False
    dedup_hash: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def model_post_init(self, __context: Any) -> None:
        if not self.dedup_hash:
            self.dedup_hash = _dedup_key(self.company, self.title, self.location)


def scored_listing_to_pipeline_entry(listing: ScoredJobListing) -> JobPipelineEntry:
    """Converts a ranked search result into a persisted board row."""
    return JobPipelineEntry(
        title=listing.title,
        company=listing.company,
        location=listing.location,
        apply_url=listing.apply_url,
        source="serpapi",
        stage="wishlist",
        fit_score=listing.fit_score,
        match_reasons=listing.match_reasons,
        honest_gaps=listing.honest_gaps,
        description_snippet=listing.description_snippet,
        description_full=listing.description_full,
        salary_range=listing.salary_range,
        posted_date=listing.posted_date,
    )


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class JobHunterState(TypedDict):
    task: AgentTask
    raw_instructions: str
    user_text: str  # the [USER REQUEST] payload, unwrapped
    action_type: str  # tailor_resume | log_application | update_status | review | search_jobs | add_manual_job | delete_job

    # Parsed entities (from ParsedRequest / task params)
    company: Optional[str]
    role_title: Optional[str]
    jd_text: Optional[str]
    stage: Optional[str]
    applied_date: Optional[str]
    referral_contact: Optional[str]
    job_url: Optional[str]
    location: Optional[str]
    notes: Optional[str]
    job_id: Optional[str]  # explicit row id targeted by update_status / delete_job

    # Working data
    master_resume: Optional[MasterResume]
    job_pipeline: list[dict[str, Any]]  # THE TABLE — list of JobPipelineEntry dicts
    application_history: list[dict[str, Any]]  # episodic job_application events
    jd_analysis: Optional[JDAnalysis]
    jd_assumed: bool  # True when the JD came from web search, not the user
    tailored: Optional[TailoredResumeDraft]
    tailored_markdown: str
    critic_feedback: Optional[str]
    revision_count: int
    needs_clarification: Optional[str]
    fit_report: dict[str, Any]

    written_files: list[str]
    pdf_status: str  # "skipped" | "compiled" | "no_compiler" | "failed" | "overflow"

    feedback_message: str
    memory_delta: dict[str, Any]
    result: Optional[AgentResult]

    # Search branch (search_jobs action)
    search_params: Optional[JobSearchParams]
    raw_job_listings: list[dict[str, Any]]        # from all sources, before filtering
    filtered_job_listings: list[dict[str, Any]]   # after hard filters + dedup
    scored_job_listings: list[ScoredJobListing]   # ranked, top 10
    search_digest: str                             # formatted user-facing output
    # Cache key used by orchestrator working_memory["last_job_search"]
    # so "#N" shorthand resolves across turns without re-searching
    search_cache: dict[str, Any]                  # {str(rank): ScoredJobListing.model_dump()}

    # Job board mutation tracking — set by persist_search_results,
    # manual_job_adder, job_deleter. Read by the orchestrator to build
    # the ui_action diff instead of forcing a full table re-fetch.
    new_pipeline_entries: list[dict[str, Any]]    # JobPipelineEntry dicts added this turn
    deleted_job_ids: list[str]                     # ids removed this turn
    skipped_duplicate_count: int                   # dedup hits during search-persist


def build_initial_state(task: AgentTask) -> JobHunterState:
    """Unpack the incoming AgentTask into JobHunterState."""
    profile = task.memory_slice.relevant_profile or {}

    pipeline = profile.get("job_pipeline") or []
    if not isinstance(pipeline, list):
        pipeline = []
    history = profile.get("application_history") or []
    if not isinstance(history, list):
        history = []

    return JobHunterState(
        task=task,
        raw_instructions=(task.instructions or "").strip(),
        user_text="",
        action_type="review",
        company=None,
        role_title=None,
        jd_text=None,
        stage=None,
        applied_date=None,
        referral_contact=None,
        job_url=None,
        location=None,
        notes=None,
        job_id=None,
        master_resume=None,
        job_pipeline=[a for a in pipeline if isinstance(a, dict)],
        application_history=[e for e in history if isinstance(e, dict)],
        jd_analysis=None,
        jd_assumed=False,
        tailored=None,
        tailored_markdown="",
        critic_feedback=None,
        revision_count=0,
        needs_clarification=None,
        fit_report={},
        written_files=[],
        pdf_status="skipped",
        feedback_message="",
        memory_delta={},
        result=None,
        # Search branch defaults
        search_params=None,
        raw_job_listings=[],
        filtered_job_listings=[],
        scored_job_listings=[],
        search_digest="",
        search_cache={},
        # Job board mutation tracking defaults
        new_pipeline_entries=[],
        deleted_job_ids=[],
        skipped_duplicate_count=0,
    )
