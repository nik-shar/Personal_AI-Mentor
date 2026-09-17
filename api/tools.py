"""
api/tools.py

PI mentor bridge — the sidecar's intent-shaped tool surface.

The PI agent core (TypeScript, `mentor/`) reaches the mentor's Python data
layer through this router and nothing else. Endpoints are named after mentor
*intents*, never tables, so an implementation can later move to the TypeScript
side without changing the tool contract (see docs/PI-Mentor Boundary.md).

Ownership rules:
  - Python stays the ONLY writer of Postgres, Chroma, and the Obsidian vault.
  - Read intents are read-only. The write intents and `run_specialist` are the
    only paths that mutate anything, and every guard on them is enforced here.
  - Fail-open: handlers return a structured `error` field instead of raising,
    so a broken tool can never crash a mentor turn (the same property the old
    `orchestrator/harness.py` tool loop guaranteed).

`GET /tools/manifest` is the single source of truth for tool schemas.
`scripts/export_mentor_tools_schema.py` reads it and generates the TypeScript
descriptor module the PI extensions import, so the two runtimes cannot drift.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestrator.config import local_tz, now_local, today_local

router = APIRouter(prefix="/tools", tags=["mentor-tools"])

SERVICE_NAME = "mentor-sidecar"
SERVICE_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Lazy MemoryManager singleton
# ---------------------------------------------------------------------------

_memory_manager: Any | None = None


def get_memory_manager():
    """Return the shared MemoryManager singleton, ensuring schema once."""
    global _memory_manager
    if _memory_manager is None:
        from orchestrator.memory.store import MemoryManager

        mm = MemoryManager()
        mm.ensure_schema()
        _memory_manager = mm
    return _memory_manager


# Profile facts a mentor turn needs by default. Deliberately excludes the two
# heavy keys (`learning_log`, `topic_graphs`) — those have their own tools with
# their own shaping, and dumping them into a prompt is exactly the
# "specificity over genericity" failure the architecture warns about.
DEFAULT_PROFILE_KEYS: tuple[str, ...] = (
    "full_name",
    "current_role",
    "employment_status",
    "target_roles",
    "target_locations",
    "long_term_goal",
    "active_learning_path",
    "learning_streak_days",
    "todays_plan",
    "relationship_phase",
)


# ---------------------------------------------------------------------------
# Manifest models
# ---------------------------------------------------------------------------


class ToolDescriptor(BaseModel):
    """One tool contract, as advertised to the TypeScript side."""

    name: str
    intent: str = Field(..., description="The mentor question this tool answers")
    description: str
    kind: str = Field(default="read", description="'read' or 'write'")
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class ToolManifest(BaseModel):
    service: str
    version: str
    generated_at: str
    tools: list[ToolDescriptor]


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------


class GetProfileRequest(BaseModel):
    keys: Optional[list[str]] = Field(
        default=None,
        description="Explicit profile fact keys to read. Omit for the default mentor set.",
    )
    max_facts: int = Field(default=40, ge=1, le=500)


class ProfileFact(BaseModel):
    key: str
    value: Any
    updated_at: Optional[str] = None


class GetProfileResponse(BaseModel):
    facts: list[ProfileFact]
    count: int
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# get_identity — P1 identity, the composed picture (memory facade)
#
# Distinct from get_profile on purpose: that one reads raw structured keys,
# this one composes BOTH homes of identity — profile_facts (typed, feeds code)
# and dna_memory (organic, feeds voice) — and carries provenance, time-sensitive
# items, and anything still awaiting his confirmation. See memory/identity.py.
# ---------------------------------------------------------------------------


class GetIdentityRequest(BaseModel):
    limit: int = Field(
        default=40,
        ge=1,
        le=200,
        description="Maximum learned memories (facts/goals/preferences) to include.",
    )
    include_profile: bool = Field(
        default=True,
        description=(
            "Include the structured profile half. Disable to read only what was "
            "learned through conversation."
        ),
    )


class GetIdentityResponse(BaseModel):
    block: Optional[str] = Field(
        default=None,
        description=(
            "The identity block, ready to read: profile facts, learned memories with "
            "inline provenance, time-sensitive items, and unconfirmed inferences. "
            "None when nothing is known yet."
        ),
    )
    found: bool = Field(
        ...,
        description="False when the mentor knows nothing about him yet — ask, don't guess.",
    )
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="Section sizes (profile_facts / who / due / pending_validation).",
    )
    degraded: list[str] = Field(
        default_factory=list,
        description="Sources that could not be read. A partial answer, and it says which part.",
    )
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# get_history — P3 history, the narrative (memory facade)
#
# The compliment to get_identity: that answers "who is he", this answers "what
# happened". Four deterministic sources — the rolling thread, daily recaps, past
# sessions, and the day log — read chronologically, never by similarity, so the
# answer does not change with how the question was phrased. See memory/history.py.
# ---------------------------------------------------------------------------


class GetHistoryRequest(BaseModel):
    sessions: int = Field(
        default=4, ge=1, le=20, description="How many past conversations to include."
    )
    recaps: int = Field(default=3, ge=1, le=14, description="How many daily recaps to include.")
    day_log: int = Field(
        default=25,
        ge=1,
        le=200,
        description="How many day-log entries to read (rendered oldest-first).",
    )


class GetHistoryResponse(BaseModel):
    block: Optional[str] = Field(
        default=None,
        description=(
            "The narrative, ready to read: the rolling thread, recent daily recaps, past "
            "conversations, and what he said he was doing. None when there is no past yet."
        ),
    )
    found: bool = Field(
        ...,
        description="False when nothing has happened yet — say so rather than inventing a past.",
    )
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="Source sizes (sessions / recaps / day_log / has_rolling_summary).",
    )
    degraded: list[str] = Field(
        default_factory=list,
        description=(
            "Sources that could not be read. A partial story, and it names the missing part — "
            "an unreadable source is NOT the same as an empty one."
        ),
    )
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# recall_memories
# ---------------------------------------------------------------------------


class RecallMemoriesRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language query to search memory with")
    limit: int = Field(default=5, ge=1, le=20)
    hot_threshold_days: int = Field(
        default=14,
        ge=0,
        le=365,
        description="Only search memories older than this many days (the hot tier is already in context)",
    )


class RecallMemoriesResponse(BaseModel):
    block: Optional[str] = Field(default=None, description="Formatted memory block, or None when nothing relevant")
    found: bool
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# get_day_grid
# ---------------------------------------------------------------------------


class GetDayGridRequest(BaseModel):
    date: Optional[str] = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today in the user's timezone.",
    )


class GetDayGridResponse(BaseModel):
    date: str
    grid: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Slots (each carrying state, clock, and an `elapsed` / `current` mark) plus "
            "code-computed free windows and any read errors."
        ),
    )
    is_today: bool = Field(
        default=True,
        description=(
            "False for any date other than today. Then nothing is marked elapsed and "
            "elapsed_minutes is 0 — asking about tomorrow must not report hours already gone."
        ),
    )
    now_clock: Optional[str] = Field(
        default=None, description="The clock reading the elapsed marks were computed against."
    )
    elapsed_minutes: Optional[int] = Field(
        default=None, description="Minutes of the day already gone, computed in code."
    )
    remaining_minutes: Optional[int] = Field(
        default=None,
        description=(
            "Minutes left in the day, computed in code. Do not estimate this — and do not "
            "offer a window that has already elapsed."
        ),
    )
    errors: list[str] = Field(
        default_factory=list,
        description=(
            "Read failures collected while building the grid. An empty grid WITH errors here "
            "is unreadable, not unplanned — say so rather than claiming the day is free."
        ),
    )
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# available_topic_nodes
# ---------------------------------------------------------------------------


class AvailableTopicNodesRequest(BaseModel):
    limit: int = Field(default=15, ge=1, le=100)


class TopicNode(BaseModel):
    graph_title: str
    node_id: str
    title: str
    status: str


class AvailableTopicNodesResponse(BaseModel):
    nodes: list[TopicNode]
    count: int
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# get_momentum
# ---------------------------------------------------------------------------


class GetMomentumResponse(BaseModel):
    streak_days: int
    completion_rate_today: float
    completion_rate_7d: float
    momentum_trend: str
    blocks_today: int
    error: Optional[str] = None


class GetMomentumRequest(BaseModel):
    """No parameters — momentum is always computed for the current moment."""


# ---------------------------------------------------------------------------
# Write intents (Phase 3)
#
# Everything here mutates a store, so everything here is Python (G1). The two
# pure-math helpers stay on the TypeScript side as tools registered natively —
# nothing above changes when the implementation moves.
# ---------------------------------------------------------------------------


class PlanItemModel(BaseModel):
    title: str = Field(..., min_length=1)
    category: str = Field(default="learning", description="learning | project | admin | break ...")
    priority: str = Field(default="should", description="must | should | nice-to-have")
    duration_min: int = Field(default=30, ge=5, le=600)
    linked_goal: Optional[str] = None
    notes: Optional[str] = None
    scheduled_time: Optional[str] = None


class SaveDailyPlanRequest(BaseModel):
    items: list[PlanItemModel] = Field(..., min_length=1)
    available_minutes: int = Field(..., ge=0, le=1440)
    date: Optional[str] = Field(default=None, description="ISO date; defaults to today in the user's timezone")


class SaveDailyPlanResponse(BaseModel):
    saved: bool
    message: str
    total_minutes: Optional[int] = None
    error: Optional[str] = None


class FindAvailableSlotsRequest(BaseModel):
    date: Optional[str] = Field(default=None, description="ISO date; defaults to today in the user's timezone")
    duration_min: int = Field(default=60, ge=30, le=600)
    energy_level: Optional[int] = Field(default=None, ge=1, le=5)


class FindAvailableSlotsResponse(BaseModel):
    date: str
    duration_min: int
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None


class PlaceTimeBlockRequest(BaseModel):
    title: str = Field(..., min_length=1)
    start_time: str = Field(..., description="ISO 8601 datetime, offset allowed, e.g. 2099-01-01T09:00:00+00:00")
    duration_min: int = Field(..., ge=30, le=600)
    category: str = "learning"
    priority: str = Field(default="should", description="must | should | nice-to-have")
    block_kind: str = "task"
    notes: Optional[str] = None


class PlaceTimeBlockResponse(BaseModel):
    status: str
    event: Optional[dict[str, Any]] = None
    slots: list[str] = Field(default_factory=list)
    start_clock: Optional[str] = None
    end_clock: Optional[str] = None
    error: Optional[str] = None


class SetAnchorRequest(BaseModel):
    start_time: str = Field(
        ...,
        description=(
            "Wall-clock ISO 8601 datetime for the anchor's start, in the user's "
            "timezone (e.g. '2026-09-15T22:00:00'). Offsets are tolerated but the "
            "clock reading is what counts — 22:00 always means 22:00 for him."
        ),
    )
    duration_min: int = Field(
        ...,
        ge=30,
        le=1440,
        description=(
            "Length in minutes (rounded to 30). An anchor may span midnight — sleep "
            "22:00 -> 09:00 is 660 — so the cap is a full day, not ten hours."
        ),
    )
    state: str = Field(default="meal", description="sleep | meal | commute | gym")
    label: Optional[str] = None


class SetAnchorResponse(BaseModel):
    status: str
    slots: list[str] = Field(
        default_factory=list,
        description="Clock labels claimed, in order (may span two dates).",
    )
    slot_count: Optional[int] = Field(
        default=None,
        description="How many 30-minute slots were actually written.",
    )
    dates: list[str] = Field(
        default_factory=list,
        description="Every date written. Two entries means the anchor crosses midnight.",
    )
    spans_midnight: Optional[bool] = None
    summary: Optional[str] = Field(
        default=None,
        description="Ready-to-quote sentence naming both windows, e.g. 'sleep anchored 2026-09-15 22:00-24:00 and 2026-09-16 00:00-09:00'.",
    )
    anchor: Optional[str] = None
    label: Optional[str] = None
    error: Optional[str] = None


class LogLearningSessionRequest(BaseModel):
    topics: list[str] = Field(default_factory=list)
    is_no_learning_day: bool = False
    source: Optional[str] = None
    date: Optional[str] = Field(default=None, description="ISO date; defaults to today in the user's timezone")


class LogLearningSessionResponse(BaseModel):
    logged: bool
    new_streak: int
    note: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# log_day_event — the day log (the *record* half of memory)
#
# Everything else in this file either plans his day or reports what the system
# already knows. This is the only intent that records what he actually DID, at
# the moment he did it, usually from his phone.
#
# It deliberately stores no interpretation. An entry is evidence, not a claim
# about who he is — patterns are derived from this record afterwards, in code
# (orchestrator/cognition/observations.py), so the arithmetic is testable and
# the mentor never has to trust its own counting.
# ---------------------------------------------------------------------------

DAY_LOG_EVENT_TYPE = "day_log"

# Kinds that leave an interval OPEN — he is now doing this thing until his next
# message says otherwise. `switch` opens a different one; `wake` opens the day;
# and `sleep` opens the NIGHT, which is what lets a later `wake` report how long
# he actually slept. Currently that is the single most valuable duration here, and
# an earlier version of this rule made it uncomputable by closing an interval
# without opening one.
DAY_LOG_OPEN_KINDS: tuple[str, ...] = ("start", "switch", "wake", "sleep")

# Kinds that close the open interval and leave nothing open: he stopped rather
# than switched. A break ends the session; it does not begin the next one.
DAY_LOG_CLOSE_ONLY_KINDS: tuple[str, ...] = ("break", "done")

# Kinds that touch no interval at all — an aside is not a transition. This is how
# "exhausted" gets recorded mid-session WITHOUT ending the session, which is what
# lets the duration rules survive a passing remark.
DAY_LOG_INTERVAL_NEUTRAL: tuple[str, ...] = ("note",)

# A repeated (kind, activity) inside this window is the transport re-delivering
# the same message, not a second event. See the handler.
DAY_LOG_DUPLICATE_WINDOW_SECONDS = 120


class LogDayEventRequest(BaseModel):
    kind: str = Field(
        ...,
        description=(
            "What kind of moment this is. 'wake' (up for the day), 'start' "
            "(beginning an activity), 'switch' (moving to a different one), "
            "'break' (stepping away), 'done' (finished for now), 'sleep' (going to "
            "bed — pair it with 'wake' so the night itself gets a duration), or "
            "'note' (an aside that changes nothing)."
        ),
    )
    activity: Optional[str] = Field(
        default=None,
        description=(
            "What he is doing, in HIS words — 'learning', 'applying to jobs', "
            "'outreach'. Never normalised into a tidier category: his phrasing is "
            "itself the signal, and a tidy label throws it away."
        ),
    )
    at: Optional[str] = Field(
        default=None,
        description=(
            "Wall-clock ISO 8601 for when it happened, in his timezone. Omit for "
            "'right now' — then the arrival time of his message is the timestamp, "
            "which is nearly always what he means."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Anything else he said, verbatim — 'exhausted', 'nothing to do, will "
            "do something random'. Vagueness is data here; pass it through."
        ),
    )
    energy: Optional[int] = Field(
        default=None, ge=1, le=5, description="Only when he actually states it."
    )


class LogDayEventResponse(BaseModel):
    logged: bool
    kind: str
    activity: Optional[str] = None
    at: Optional[str] = Field(default=None, description="Resolved wall-clock ISO timestamp.")
    clock: Optional[str] = Field(default=None, description="'HH:MM' in his timezone.")
    closed: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "The interval this moment closed, when one was open: "
            "{activity, start, end, duration_min}. Duration is computed here, in code."
        ),
    )
    open_now: Optional[bool] = Field(
        default=None, description="True when this moment left a new interval open."
    )
    duplicate: bool = Field(
        default=False,
        description="True when an identical entry had already been logged moments before.",
    )
    message: str = ""
    error: Optional[str] = None


class RunSpecialistRequest(BaseModel):
    agent: str = Field(
        ...,
        description=(
            "Which specialist to run: 'goal_decomposer' (learning roadmaps), "
            "'job_hunter' (resume tailoring / application pipeline), or "
            "'linkedin_writer' (post drafts)."
        ),
    )
    request: str = Field(
        ...,
        description=(
            "What the user asked for, in their own words. Becomes the task's user "
            "request verbatim — never a summary, so the specialist sees the real ask."
        ),
    )
    task_type: Optional[str] = Field(
        default=None,
        description=(
            "Optional explicit action. Omit to let the specialist resolve intent from "
            "`request` — required for job_hunter, which is multi-action."
        ),
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Session the request came from, for tracing.",
    )


class RunSpecialistResponse(BaseModel):
    status: str
    agent: str
    task_type: str
    output: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# mark_topic_done — the missing status mutator
# ---------------------------------------------------------------------------

class MarkTopicDoneRequest(BaseModel):
    node: str = Field(
        ...,
        description=(
            "The curriculum topic to update — its exact title or node id. Exact match only; "
            "I never guess at near-matches for a status change."
        ),
    )
    roadmap: Optional[str] = Field(
        default=None,
        description=(
            "Which roadmap (graph_id or exact title), when the node name alone is ambiguous. "
            "Omit it when the title is unique across roadmaps."
        ),
    )
    status: str = Field(
        default="done",
        description=(
            "'done' (he finished it), 'in_progress' (he started it), 'skipped' "
            "(deliberately skipped), or 'not_started' to undo a mistake."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Optional: what he actually did or found, in his words. Appended to the node's "
            "note as session evidence — never stored as an interpretation."
        ),
    )
    session_id: Optional[str] = Field(default=None, description="Session the update came from.")


class MarkTopicDoneResponse(BaseModel):
    updated: bool
    roadmap: str = ""
    roadmap_title: str = ""
    node: str = ""
    node_title: str = ""
    status: str = ""
    unlocked: list[str] = Field(
        default_factory=list,
        description="Topics that this change just made study-ready (prerequisites now all done).",
    )
    remaining: int = 0
    note_appended: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# learn_repo — repo → concept inventory (G1 of the Repo-to-Curriculum plan)
# ---------------------------------------------------------------------------

class LearnRepoRequest(BaseModel):
    repo_path: str = Field(
        ...,
        description=(
            "Path to the repository to analyse. Must be inside the workspace sandbox "
            "(the student's own project, or a path under it)."
        ),
    )
    title: str = Field(default="", description="Optional curriculum title; derived from the repo when empty.")
    stopping_rule: str = Field(
        default="comprehension",
        description=(
            "'comprehension' — concepts needed to READ this repo (default). "
            "'authorship' — concepts needed to REBUILD it. These produce very different "
            "curricula, so ask him which one he wants rather than guessing."
        ),
    )
    max_nodes: int = Field(
        default=10, ge=2, le=30,
        description="Hard cap on concepts. Every concept has infinite prerequisites, so this is the stopping rule's teeth.",
    )
    persist: bool = Field(
        default=False,
        description=(
            "False = read the inventory back and discuss it. True = also save it as a real "
            "roadmap he can study and mark topics complete against."
        ),
    )
    target_days: Optional[int] = Field(default=None, description="Optional: spread it over N days.")
    hours_per_day: Optional[float] = Field(default=None, description="Optional: hours of study per day.")
    session_id: Optional[str] = Field(default=None, description="Session the request came from.")


class LearnedConcept(BaseModel):
    concept: str
    difficulty: Optional[int] = None
    estimated_hours: float = 0.0
    day: Optional[int] = None
    anchors: list[str] = Field(default_factory=list, description="Where it lives: 'path::symbol'.")
    prerequisites: list[str] = Field(default_factory=list)
    what_to_cover: Optional[str] = None


class LearnRepoResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    repo: str = ""
    commit: Optional[str] = None
    files_scanned: int = 0
    summary: str = ""
    roadmap: str = ""
    title: str = ""
    concept_count: int = 0
    concepts: list[LearnedConcept] = Field(default_factory=list)
    anchor_coverage: Optional[str] = Field(
        default=None,
        description="How many cited locations actually resolve, e.g. '9/11 anchors resolved (82%)'.",
    )
    dropped_anchors: list[str] = Field(
        default_factory=list, description="Citations that did not resolve and were removed."
    )
    dropped_concepts: list[str] = Field(default_factory=list, description="Concepts cut to honour max_nodes.")
    broken_cycles: list[str] = Field(default_factory=list)
    ground_truth_found: int = 0
    ground_truth_total: int = 0
    total_hours: float = 0.0
    persisted: bool = False
    roadmap_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Manifest — the contract source of truth for schema generation
# ---------------------------------------------------------------------------

_MANIFEST_SPECS: list[tuple[str, str, str, str, type[BaseModel], type[BaseModel]]] = [
    (
        "get_profile",
        "What do I know about him?",
        "Read structured profile facts (identity, career targets, goals, learning state).",
        "read",
        GetProfileRequest,
        GetProfileResponse,
    ),
    (
        "get_identity",
        "Who is he, and what have I learned about him?",
        "The composed picture of who Nik is: the structured profile PLUS what has been learned through conversation (facts, goals, preferences), each with its provenance and confidence, plus anything time-sensitive and anything still awaiting his confirmation. Richer than get_profile.",
        "read",
        GetIdentityRequest,
        GetIdentityResponse,
    ),
    (
        "get_history",
        "What happened — what did we talk about, what did he do?",
        "The narrative of the past, from four deterministic sources: the rolling summary of the thread so far, recent daily recaps, past conversations (when, how long, what about), and what he said he was doing in his own words. Read chronologically, not by similarity — so it does not change with how the question is phrased.",
        "read",
        GetHistoryRequest,
        GetHistoryResponse,
    ),
    (
        "recall_memories",
        "What did he say or do around this?",
        "Semantic recall over older episodic memories (warm/cold tiers).",
        "read",
        RecallMemoriesRequest,
        RecallMemoriesResponse,
    ),
    (
        "get_day_grid",
        "Am I free at four?",
        "The 48-slot day grid with slot states, code-computed free windows, and the clock: which slots have already elapsed, and exactly how much of the day is left. Never offer a window the grid marks as elapsed.",
        "read",
        GetDayGridRequest,
        GetDayGridResponse,
    ),
    (
        "available_topic_nodes",
        "What can he study right now?",
        "DAG traversal: in-progress nodes first, then unlocked not-started nodes.",
        "read",
        AvailableTopicNodesRequest,
        AvailableTopicNodesResponse,
    ),
    (
        "get_momentum",
        "Is he actually consistent?",
        "Streak, completion rates, and momentum trend computed fresh from schedule events.",
        "read",
        GetMomentumRequest,
        GetMomentumResponse,
    ),
    (
        "save_daily_plan",
        "Lock in today's plan",
        "Persist a daily plan (profile facts + episodic event) and echo the budget the code used.",
        "write",
        SaveDailyPlanRequest,
        SaveDailyPlanResponse,
    ),
    (
        "find_available_slots",
        "Where could this actually fit?",
        "Candidate placement windows for a duration, computed from the grid; a high energy level nudges earlier windows first.",
        "read",
        FindAvailableSlotsRequest,
        FindAvailableSlotsResponse,
    ),
    (
        "place_time_block",
        "Put this on my calendar",
        "Validate and persist one booked block. Code-enforced: 30-minute alignment, overlap check, and an anchor guard that refuses to place tasks over sleep/meal/commute/gym.",
        "write",
        PlaceTimeBlockRequest,
        PlaceTimeBlockResponse,
    ),
    (
        "set_anchor",
        "Protect my sleep/meals/commute/gym",
        "Reserve contiguous slots as a recurring life anchor that task placement can never overwrite.",
        "write",
        SetAnchorRequest,
        SetAnchorResponse,
    ),
    (
        "log_learning_session",
        "Record what I studied today",
        "Log a learning session and advance/reset the streak using the same arithmetic the mentor reasons with.",
        "write",
        LogLearningSessionRequest,
        LogLearningSessionResponse,
    ),
    (
        "log_day_event",
        "Log what I'm doing right now",
        "Record one moment of his actual day (waking, starting, switching, lunch, sleep) in his own words, and get back the interval it closed with its duration. Raw evidence — no interpretation is stored, and durations are computed here.",
        "write",
        LogDayEventRequest,
        LogDayEventResponse,
    ),
    (
        "run_specialist",
        "Build me a roadmap / tailor my resume / draft a post",
        "Run one specialist agent — goal_decomposer (learning roadmaps), job_hunter (resume tailoring, application pipeline), or linkedin_writer (post drafts) — and persist its memory delta. Python keeps these specialists and owns every store they touch; this is only the routing seam.",
        "write",
        RunSpecialistRequest,
        RunSpecialistResponse,
    ),
    (
        "mark_topic_done",
        "I finished that topic — what's next?",
        "Mark a curriculum topic done (or started / skipped) and report which downstream topics it just unlocked. Python owns the DAG, so the frontier is computed here and never guessed.",
        "write",
        MarkTopicDoneRequest,
        MarkTopicDoneResponse,
    ),
    (
        "learn_repo",
        "Teach me to understand this repo",
        "Derive a prerequisite curriculum from a real codebase: which concepts the code embodies, where each one lives, and how hard it is. Citations are verified against the filesystem and unresolvable ones are reported, not trusted. Optionally saves it as a roadmap.",
        "write",
        LearnRepoRequest,
        LearnRepoResponse,
    ),
]


def build_manifest() -> ToolManifest:
    """Assemble the tool manifest from the pydantic models (no DB access)."""
    tools = [
        ToolDescriptor(
            name=name,
            intent=intent,
            description=description,
            kind=kind,
            params=request_model.model_json_schema(),
            result=response_model.model_json_schema(),
        )
        for name, intent, description, kind, request_model, response_model in _MANIFEST_SPECS
    ]
    return ToolManifest(
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        tools=tools,
    )


@router.get("/manifest", response_model=ToolManifest)
def tool_manifest() -> ToolManifest:
    """The declared tool surface, used to generate the TypeScript descriptors."""
    return build_manifest()


# ---------------------------------------------------------------------------
# Read intents
#
# Handlers deliberately do NOT declare `response_model`: every one of them must
# be able to return a fail-open payload (`{"error": ...}` plus safe defaults)
# without pydantic validation getting in the way. The response shape is still
# declared in the manifest above, which is what generates the TS contract.
# ---------------------------------------------------------------------------


@router.post("/get_profile")
def get_profile(req: GetProfileRequest) -> dict[str, Any]:
    """Read structured profile facts — the mentor's memory of who Nik is."""
    try:
        mm = get_memory_manager()
        keys = list(req.keys) if req.keys else list(DEFAULT_PROFILE_KEYS)
        facts = mm.load_profile_facts(keys)
        # Fall back to every stored fact when no default key exists yet (fresh
        # install) so the tool is never uselessly empty.
        if not req.keys and not facts:
            facts = mm.load_profile_facts()
        # `updated_at` is not exposed by the current flat read API; Phase 3 can
        # add it without changing the tool contract.
        items = [{"key": k, "value": v, "updated_at": None} for k, v in facts.items()]
        items = items[: req.max_facts]
        return {"facts": items, "count": len(items), "error": None}
    except Exception as exc:
        return {"facts": [], "count": 0, "error": f"get_profile failed: {exc}"}


@router.post("/get_identity")
def get_identity(req: GetIdentityRequest) -> dict[str, Any]:
    """P1 identity — the composed picture of who he is.

    One call, two homes. The facade (`orchestrator/memory`) composes the
    structured profile half with the organic DNA half, attaches provenance to
    every item, and includes the deterministic layer: what is time-sensitive and
    what is still unconfirmed. The *rendering* also stays in Python — returning a
    pre-formatted block is what stops the TypeScript side from re-implementing
    the same text and drifting from it.

    Read-only: it opens no write path and mutates nothing.
    """
    try:
        from orchestrator.memory import read_identity, render_identity

        snapshot = read_identity(limit=req.limit, include_profile=req.include_profile)
        block = render_identity(snapshot) or None
        return {
            "block": block,
            "found": block is not None,
            "counts": snapshot.counts,
            "degraded": snapshot.degraded,
            "error": None,
        }
    except Exception as exc:
        return {
            "block": None,
            "found": False,
            "counts": {},
            "degraded": [],
            "error": f"get_identity failed: {exc}",
        }


@router.post("/get_history")
def get_history(req: GetHistoryRequest) -> dict[str, Any]:
    """P3 history — the narrative of what happened.

    The four sources are read deterministically and rendered in Python, so the
    same question always produces the same story: this is not semantic recall
    (`recall_memories` answers "what relates to this query?").

    `degraded` matters here more than elsewhere. Each source fails independently,
    so a broken day log leaves the rolling summary and the sessions intact — and
    an unreadable source is reported as unreadable rather than silently reading
    as "nothing happened".

    Read-only: no write path is opened and nothing is mutated.
    """
    try:
        from orchestrator.memory import read_history, render_history

        view = read_history(sessions=req.sessions, recaps=req.recaps, day_log=req.day_log)
        block = render_history(view) or None
        return {
            "block": block,
            "found": block is not None,
            "counts": view.counts,
            "degraded": view.degraded,
            "error": None,
        }
    except Exception as exc:
        return {
            "block": None,
            "found": False,
            "counts": {},
            "degraded": [],
            "error": f"get_history failed: {exc}",
        }


@router.post("/recall_memories")
def recall_memories(req: RecallMemoriesRequest) -> dict[str, Any]:
    """
    Semantic recall over older episodic memories.

    Uses the SAME embedding model as event storage (all-MiniLM-L6-v2, 384-dim).
    This is the reason memory search must never be reimplemented on the
    TypeScript side: two embedding implementations silently degrade recall.
    """
    try:
        from orchestrator.memory.retriever import MemoryRetriever

        block = MemoryRetriever(get_memory_manager()).recall(
            user_input=req.query,
            limit=req.limit,
            hot_threshold_days=req.hot_threshold_days,
        )
        return {"block": block, "found": block is not None, "error": None}
    except Exception as exc:
        return {"block": None, "found": False, "error": f"recall_memories failed: {exc}"}


@router.post("/get_day_grid")
def get_day_grid(req: GetDayGridRequest) -> dict[str, Any]:
    """
    The 48-slot day grid for a date: slot states, event titles, free windows,
    **and the clock**.

    Free windows are contiguous runs of `free` slots computed in code, so the
    mentor never guesses availability. Slot math stays in Python because it owns
    the `day_slots` table (single-writer rule).

    Time sense (docs/PI-Mentor Boundary.md §13): each slot is marked `elapsed` /
    `current` and the response carries `elapsed_minutes` / `remaining_minutes`.
    That closes the G7 gap — "how much time is left?" is now arithmetic the code
    does rather than arithmetic the model approximates, and a window that has
    already passed is visibly behind the clock instead of being offered.
    """
    date_str = req.date or today_local().isoformat()
    try:
        from orchestrator.harness import build_day_grid

        grid = build_day_grid(get_memory_manager(), date_str)
        return {
            "date": date_str,
            "grid": grid,
            "is_today": bool(grid.get("is_today", True)),
            "now_clock": grid.get("now_clock"),
            "elapsed_minutes": grid.get("elapsed_minutes"),
            "remaining_minutes": grid.get("remaining_minutes"),
            "errors": list(grid.get("errors") or []),
            "error": None,
        }
    except Exception as exc:
        return {
            "date": date_str,
            "grid": {},
            "is_today": False,
            "now_clock": None,
            "elapsed_minutes": None,
            "remaining_minutes": None,
            "errors": [],
            "error": f"get_day_grid failed: {exc}",
        }


@router.post("/available_topic_nodes")
def available_topic_nodes(req: AvailableTopicNodesRequest) -> dict[str, Any]:
    """
    Study-ready topic nodes across every roadmap: in-progress first, then
    unlocked (all prerequisites done) not-started nodes.

    DAG traversal stays in code — the LLM should never guess prerequisites.

    The graphs come from the curriculum manifests. Until this was fixed it read
    `profile_facts["topic_graphs"]`, a key nothing ever wrote, so this tool
    answered ZERO nodes while the vault held five unlocked ones — the mentor's
    "what can I study right now?" was silently blind on the default engine.
    """
    try:
        from orchestrator.config import MENTOR_CURRICULUM_PATH
        from orchestrator.harness import get_available_topic_nodes
        from orchestrator.memory.roadmap import list_roadmaps

        graphs = [
            g.model_dump(mode="json") for g in list_roadmaps(MENTOR_CURRICULUM_PATH)
        ]
        nodes = get_available_topic_nodes(graphs)[: req.limit]
        return {"nodes": nodes, "count": len(nodes), "error": None}
    except Exception as exc:
        return {"nodes": [], "count": 0, "error": f"available_topic_nodes failed: {exc}"}


@router.post("/get_momentum")
def get_momentum() -> dict[str, Any]:
    """
    Streak + completion rates + momentum trend, computed fresh from
    `schedule_events` on every call (nothing is pre-computed or cached).
    """
    try:
        from orchestrator.cognition.metrics import compute_momentum

        return {**compute_momentum(get_memory_manager()), "error": None}
    except Exception as exc:
        return {
            "streak_days": 0,
            "completion_rate_today": 0.0,
            "completion_rate_7d": 0.0,
            "momentum_trend": "unknown",
            "blocks_today": 0,
            "error": f"get_momentum failed: {exc}",
        }


# ---------------------------------------------------------------------------
# Write intents (Phase 3)
#
# These wrap the EXISTING production implementations instead of re-porting them:
#
#   - `find_available_slots`, `place_time_block`, `set_anchor` are plain
#     module-level functions in `orchestrator/harness.py` (whose own docstring
#     calls them "the source of truth").
#   - `save_daily_plan` / `log_learning_session` exist only as closures inside
#     `make_memory_tools()`, so they are invoked through the tool object. That is
#     deliberate: re-implementing them here would create a second copy of the
#     streak and budget logic, and the sidecar's whole job is to BE the same
#     code. Two implementations of a rule is how a boundary rots.
#
# Every invariant (30-minute alignment, overlap rejection, anchor guard, streak
# reset) therefore stays in exactly one place. When Python retires in Phase 6,
# these move behind the same tool names.
# ---------------------------------------------------------------------------


def _invoke_memory_tool(name: str, args: dict[str, Any]) -> Any:
    """Invoke one of the harness's memory tools, reusing its exact logic."""
    from orchestrator.harness import make_memory_tools

    tools = {getattr(tool, "name", ""): tool for tool in make_memory_tools(get_memory_manager())}
    target = tools.get(name)
    if target is None:
        raise KeyError(f"memory tool '{name}' not found (have: {sorted(tools)})")
    return target.invoke(args)


@router.post("/save_daily_plan")
def save_daily_plan(req: SaveDailyPlanRequest) -> dict[str, Any]:
    """Persist today's plan to profile facts + an episodic event."""
    try:
        payload: dict[str, Any] = {
            "items": [item.model_dump() for item in req.items],
            "available_minutes": req.available_minutes,
        }
        if req.date:
            payload["date"] = req.date
        message = _invoke_memory_tool("save_daily_plan", payload)
        total_minutes = sum(int(item.duration_min) for item in req.items)
        return {"saved": True, "message": str(message), "total_minutes": total_minutes, "error": None}
    except Exception as exc:
        return {
            "saved": False,
            "message": "",
            "total_minutes": None,
            "error": f"save_daily_plan failed: {exc}",
        }


@router.post("/find_available_slots")
def find_available_slots_endpoint(req: FindAvailableSlotsRequest) -> dict[str, Any]:
    """Candidate windows for a duration, computed from the grid in code."""
    date_str = req.date or today_local().isoformat()
    try:
        from orchestrator.harness import find_available_slots

        result = find_available_slots(
            get_memory_manager(), date_str, req.duration_min, req.energy_level
        )
        if result.get("error"):
            return {
                "date": date_str,
                "duration_min": req.duration_min,
                "candidates": [],
                "error": str(result["error"]),
            }
        return {
            "date": str(result.get("date", date_str)),
            "duration_min": int(result.get("duration_min", req.duration_min)),
            "candidates": list(result.get("candidates") or []),
            "error": None,
        }
    except Exception as exc:
        return {
            "date": date_str,
            "duration_min": req.duration_min,
            "candidates": [],
            "error": f"find_available_slots failed: {exc}",
        }


@router.post("/place_time_block")
def place_time_block_endpoint(req: PlaceTimeBlockRequest) -> dict[str, Any]:
    """
    Validate and persist one booked block.

    All the checks live in the harness function, not here: 30-minute alignment,
    overlap rejection, and the anchor guard. This endpoint only shapes the
    payload, which is exactly why the invariant cannot drift.
    """
    try:
        from orchestrator.harness import place_time_block

        result = place_time_block(
            get_memory_manager(),
            title=req.title,
            start_time=req.start_time,
            duration_min=req.duration_min,
            category=req.category,
            priority=req.priority,
            block_kind=req.block_kind,
            notes=req.notes,
        )
        return {
            "status": str(result.get("status", "error")),
            "event": result.get("event"),
            "slots": list(result.get("slots") or []),
            "start_clock": result.get("start_clock"),
            "end_clock": result.get("end_clock"),
            "error": result.get("error"),
        }
    except Exception as exc:
        return {
            "status": "error",
            "event": None,
            "slots": [],
            "start_clock": None,
            "end_clock": None,
            "error": f"place_time_block failed: {exc}",
        }


@router.post("/set_anchor")
def set_anchor_endpoint(req: SetAnchorRequest) -> dict[str, Any]:
    """
    Reserve contiguous slots as a life anchor.

    Note: the harness `set_anchor(mm, date, start_time, ...)` takes a `date`
    argument it never reads — it writes slots using `start_time`'s own date. We
    pass the date derived from `start_time` so the call site stays honest.
    """
    try:
        from orchestrator.harness import set_anchor

        result = set_anchor(
            get_memory_manager(),
            date=req.start_time[:10],
            start_time=req.start_time,
            duration_min=req.duration_min,
            state=req.state,
            label=req.label,
        )
        return {
            "status": str(result.get("status", "error")),
            "slots": list(result.get("slots") or []),
            "slot_count": result.get("slot_count"),
            "dates": list(result.get("dates") or []),
            "spans_midnight": result.get("spans_midnight"),
            "summary": result.get("summary"),
            "anchor": result.get("anchor"),
            "label": result.get("label"),
            "error": result.get("error"),
        }
    except Exception as exc:
        return {
            "status": "error",
            "slots": [],
            "slot_count": None,
            "dates": [],
            "spans_midnight": None,
            "summary": None,
            "anchor": None,
            "label": None,
            "error": f"set_anchor failed: {exc}",
        }


@router.post("/log_learning_session")
def log_learning_session_endpoint(req: LogLearningSessionRequest) -> dict[str, Any]:
    """
    Log a learning session and return the streak the code computed.

    The streak arithmetic is NOT re-done here — `compute_learning_streak` also
    lives on the TypeScript side as a native tool for the model to reason with,
    but this write path must use the same call the Python build used, or the two
    would eventually disagree about what "today was already logged" means.
    """
    try:
        payload: dict[str, Any] = {
            "topics": req.topics,
            "is_no_learning_day": req.is_no_learning_day,
        }
        if req.source:
            payload["source"] = req.source
        if req.date:
            payload["date"] = req.date

        result = _invoke_memory_tool("log_learning_session", payload)
        if isinstance(result, dict):
            return {
                "logged": bool(result.get("logged", False)),
                "new_streak": int(result.get("new_streak", 0)),
                "note": str(result.get("note", "")),
                "error": None,
            }
        return {"logged": False, "new_streak": 0, "note": str(result), "error": None}
    except Exception as exc:
        return {
            "logged": False,
            "new_streak": 0,
            "note": "",
            "error": f"log_learning_session failed: {exc}",
        }
# ---------------------------------------------------------------------------
# Day log
# ---------------------------------------------------------------------------


def _day_log_clock(iso: Any) -> str:
    """'HH:MM' for an *instant* (ISO with offset), read in his timezone.

    Note the asymmetry with `grid.parse_wall_clock`: a stored `occurred_at` is an
    instant and must be CONVERTED into his zone, whereas the `at` the model types
    is already a wall-clock reading. Reading an instant with the wall-clock reader
    would shift every duration by the machine's offset — the exact class of bug
    the timezone basis exists to prevent.
    """
    try:
        return datetime.fromisoformat(str(iso)).astimezone(local_tz()).strftime("%H:%M")
    except Exception:
        return ""


def _slug(value: str) -> str:
    """A tag-safe form of his wording (tags are queried, never shown)."""
    return "_".join(str(value or "").lower().split())[:40]


@router.post("/log_day_event")
def log_day_event(req: LogDayEventRequest) -> dict[str, Any]:
    """
    Record one moment of his real day, and report the interval it closed.

    This is the *record* half of memory: what happened, when, in his words. It
    carries no interpretation — nothing here decides what he is like — because
    patterns are derived from this record afterwards, in code, so the arithmetic
    is testable and the mentor never has to trust its own counting.

    Two rules live HERE rather than in the transport, because whoever owns the
    store owns the invariant:

    1. **One open interval at a time.** A moment that starts something closes
       whatever came before it. That single rule is what turns a day of
       one-liners ("waking up", "starting my learning", "off to lunch") into
       durations, without ever asking him to report one.
    2. **Idempotence.** Telegram re-delivers an update until it is acked, so the
       same message can legitimately arrive twice. A repeat of the same
       (kind, activity) moments later is reported as `duplicate` instead of being
       logged again — a double-logged transition invents an interval that never
       happened and quietly corrupts every pattern derived from it later.
    """
    from orchestrator.harness import format_duration
    from orchestrator.memory.grid import parse_wall_clock

    kind = (req.kind or "").strip().lower()
    if not kind:
        return {
            "logged": False,
            "kind": "",
            "message": "",
            "error": (
                "`kind` is empty — say what kind of moment this is "
                "(wake, start, switch, break, done, sleep, note)"
            ),
        }

    activity = (req.activity or "").strip() or None
    note = (req.note or "").strip() or None

    # `at` absent means "right now" — the arrival time is the timestamp.
    try:
        at = parse_wall_clock(req.at) if req.at else now_local()
    except Exception as exc:
        return {
            "logged": False,
            "kind": kind,
            "activity": activity,
            "message": "",
            "error": f"invalid `at` '{req.at}': {exc}",
        }

    try:
        mm = get_memory_manager()
        # A night's sleep spans midnight, so the look-back has to reach past the
        # start of today: 20 hours covers any plausible night without dragging
        # yesterday afternoon in as the "open" interval.
        recent = mm.query_episodic(
            event_type=DAY_LOG_EVENT_TYPE,
            since=at - timedelta(hours=20),
            last_n=50,
        )
    except Exception as exc:
        return {
            "logged": False,
            "kind": kind,
            "activity": activity,
            "message": "",
            "error": f"day log unavailable: {exc}",
        }

    clock = at.strftime("%H:%M")
    last = recent[0] if recent else None
    last_payload = (last or {}).get("payload") or {}
    last_kind = str(last_payload.get("kind") or "")
    last_activity = last_payload.get("activity") or None

    # --- Idempotence: the same moment, moments ago, is a re-delivery ---------
    if last is not None and last_kind == kind and last_activity == activity:
        try:
            gap = abs(
                (
                    at
                    - datetime.fromisoformat(str(last["occurred_at"])).astimezone(local_tz())
                ).total_seconds()
            )
        except Exception:
            gap = None
        if gap is not None and gap <= DAY_LOG_DUPLICATE_WINDOW_SECONDS:
            return {
                "logged": True,
                "kind": kind,
                "activity": activity,
                "at": at.isoformat(),
                "clock": clock,
                "duplicate": True,
                "message": f"Already logged at {clock} — nothing new recorded.",
                "error": None,
            }

    # --- Open-interval resolution: close whatever this moment ends ----------
    # The open interval is the newest event that is NOT interval-neutral. A `note`
    # is skipped rather than ending whatever came before it, because "exhausted"
    # said mid-session is not a transition out of it. Everything else either
    # opened an interval or closed one, so the newest of those is what is running.
    open_event = next(
        (
            ev
            for ev in recent
            if str((ev.get("payload") or {}).get("kind") or "") not in DAY_LOG_INTERVAL_NEUTRAL
        ),
        None,
    )
    open_payload = (open_event or {}).get("payload") or {}
    open_kind = str(open_payload.get("kind") or "")
    open_activity = open_payload.get("activity") or None

    closed: dict[str, Any] | None = None
    if (
        kind not in DAY_LOG_INTERVAL_NEUTRAL
        and open_event is not None
        and open_kind in DAY_LOG_OPEN_KINDS
    ):
        try:
            started = datetime.fromisoformat(str(open_event["occurred_at"])).astimezone(local_tz())
            duration = int((at - started).total_seconds() // 60)
            if duration >= 0:
                closed = {
                    "activity": open_activity or open_kind,
                    "start": started.isoformat(),
                    "end": at.isoformat(),
                    "duration_min": duration,
                }
        except Exception as exc:  # fail-open: a bad prior row must not lose this one
            print(f"[tools] log_day_event: open-interval math failed: {exc}")

    content = f"{clock} {kind}" + (f" — {activity}" if activity else "")
    if note:
        content += f" ({note})"

    try:
        mm.add_episodic_event(
            source_agent="day_log",
            event_type=DAY_LOG_EVENT_TYPE,
            content=content,
            payload={
                "kind": kind,
                "activity": activity,
                "note": note,
                "energy": req.energy,
                "at": at.isoformat(),
                "closed": closed,
            },
            tags=["day_log", kind] + ([_slug(activity)] if activity else []),
            # An instant, so it is stored in UTC like every other instant in this
            # system. The wall-clock reading he meant is kept in the payload.
            occurred_at=at.astimezone(timezone.utc),
            importance=2,
        )
    except Exception as exc:
        return {
            "logged": False,
            "kind": kind,
            "activity": activity,
            "at": at.isoformat(),
            "clock": clock,
            "message": "",
            "error": f"day log write failed: {exc}",
        }

    message = f"Logged {kind} at {clock}"
    if activity:
        message += f" — {activity}"
    if closed:
        message += (
            f". Closed {closed['activity']} "
            f"{_day_log_clock(closed['start'])}–{clock} "
            f"({format_duration(closed['duration_min'])})"
        )
    if note:
        message += f". Said: {note}"

    return {
        "logged": True,
        "kind": kind,
        "activity": activity,
        "at": at.isoformat(),
        "clock": clock,
        "closed": closed,
        # A note changes nothing, so the interval state stays whatever it was.
        "open_now": (
            open_kind in DAY_LOG_OPEN_KINDS
            if kind in DAY_LOG_INTERVAL_NEUTRAL
            else kind in DAY_LOG_OPEN_KINDS
        ),
        "duplicate": False,
        "message": message,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Specialist routing (Phase 4)
#
# The reasoning loop's *routing* half — "which specialist does this turn need?" —
# moves to the PI core (G4: policy belongs in TypeScript, and the model can read
# intent better than a keyword table). The specialists themselves stay here:
# goal_decomposer writes the Obsidian vault, job_hunter needs tectonic/pypdf,
# linkedin_writer reads the Chroma voice store — all G1/G2, all Python, all
# permanent. So this endpoint is a routing seam, not a re-implementation, and it
# deliberately runs the SAME three steps the graph's dispatch → executor →
# merger path ran:
#
#     build_task()        (context_builder — per-agent memory slice + §4 guidelines)
#     spec.run(task)      (registry — the agent's own internal subgraph)
#     apply_memory_delta() (memory_merger — profile facts + episodic events)
#
# Nothing else from `orchestrator.py` is involved, which is the point: the graph
# stops being load-bearing for specialists the moment this exists.
# ---------------------------------------------------------------------------

# Imported here, not in the manifest block: an unknown agent or a hallucinated
# task_type is a *model* error and must come back as a readable tool result, not
# a 500. Mirrors dispatch_node's validation (config.VALID_TASK_TYPES).
_SPECIALIST_AGENTS: tuple[str, ...] = ("goal_decomposer", "job_hunter", "linkedin_writer")


@router.post("/run_specialist")
def run_specialist(req: RunSpecialistRequest) -> dict[str, Any]:
    """
    Run one specialist agent and persist its memory delta.

    Fail-open like every other handler: a bad agent name, an unknown task_type,
    or a crashing specialist all come back as a structured `error` with a
    non-success status, never an exception.
    """
    agent = (req.agent or "").strip()
    requested_task_type = (req.task_type or "").strip()

    def _fail(message: str, task_type: str = "") -> dict[str, Any]:
        return {
            "status": "failed",
            "agent": agent,
            "task_type": task_type or requested_task_type,
            "output": "",
            "error": message,
        }

    if agent not in _SPECIALIST_AGENTS:
        return _fail(
            f"unknown specialist '{agent}' — available: {', '.join(_SPECIALIST_AGENTS)}"
        )
    if not (req.request or "").strip():
        return _fail("`request` is empty — the specialist needs the user's actual ask")

    try:
        from orchestrator.config import DEFAULT_TASK_TYPES, VALID_TASK_TYPES
        from orchestrator.nodes.context_builder import build_task
        from orchestrator.nodes.memory_merger import apply_memory_delta
        from orchestrator.registry import get_agent_spec
    except Exception as exc:
        return _fail(f"specialist modules unavailable: {exc}")

    allowed = VALID_TASK_TYPES.get(agent, [])
    if requested_task_type and requested_task_type not in allowed:
        # Observed in the old path: the reasoner invented task types
        # ("find_jobs", "search_openings") that no agent implements.
        return _fail(
            f"unknown task_type '{requested_task_type}' for {agent} — "
            f"valid: {', '.join(allowed) or 'none'}",
            requested_task_type,
        )

    # An empty task_type is legitimate for job_hunter: it is multi-action and its
    # own input_parser resolves intent from the raw message far more reliably
    # than any fixed default could (see config.DEFAULT_TASK_TYPES).
    task_type = requested_task_type or DEFAULT_TASK_TYPES.get(agent, "")

    try:
        memory_manager = get_memory_manager()
        task = build_task(
            memory_manager,
            req.session_id or "specialist_run",
            req.request,
            agent,
            task_type,
        )
        result = get_agent_spec(agent).run(task)
    except Exception as exc:
        return _fail(f"{agent} failed: {exc}", task_type)

    # Persist exactly as memory_merger_node did. A storage failure must not
    # discard an output the specialist already produced (it may have written the
    # vault), so the error is reported alongside the result rather than replacing
    # it — the same "no silent failure" rule the graph's merger followed.
    merge_error: str | None = None
    try:
        apply_memory_delta(memory_manager, result)
    except Exception as exc:
        merge_error = f"memory merge failed: {exc}"
        print(f"[tools] run_specialist merge error ({agent}): {exc}")

    status = getattr(result.status, "value", str(result.status))
    return {
        "status": status,
        "agent": agent,
        "task_type": task.task_type,
        "output": result.output or "",
        "error": result.error_message or merge_error,
    }


# ---------------------------------------------------------------------------
# mark_topic_done — the status mutator the PI path was missing entirely
# ---------------------------------------------------------------------------


@router.post("/mark_topic_done")
def mark_topic_done(req: MarkTopicDoneRequest) -> dict[str, Any]:
    """Move a curriculum topic to done / in_progress / skipped, and say what it unlocked.

    Why this exists: `update_node_status` had exactly one caller, inside the Python
    graph. On the default engine nothing could ever mark a node done, so the
    prerequisite frontier never advanced and every node stayed `not_started` —
    all 22 of them, in the live curriculum. This is that missing mutator.

    The unlock report is computed here rather than by the model: the DAG is code's
    business, and "what can I study next" must not be guessed.

    Fail-open: an unknown node, an ambiguous name, or an invalid status comes back
    as a structured error, never an exception.
    """
    def _fail(message: str, graph_id: str = "", title: str = "") -> dict[str, Any]:
        return {
            "updated": False,
            "roadmap": graph_id,
            "roadmap_title": title,
            "node": "",
            "node_title": "",
            "status": "",
            "unlocked": [],
            "remaining": 0,
            "note_appended": False,
            "error": message,
        }

    try:
        from orchestrator.config import MENTOR_CURRICULUM_PATH
        from orchestrator.memory.roadmap import (
            append_deepened,
            find_node_anywhere,
            find_roadmap_any,
            resolve_node,
            set_node_status,
            try_load_roadmap,
        )
        from orchestrator.memory.topic_graph import get_available_nodes
    except Exception as exc:
        return _fail(f"roadmap modules unavailable: {exc}")

    root = MENTOR_CURRICULUM_PATH
    wanted = (req.node or "").strip()
    if not wanted:
        return _fail("`node` is empty — tell me which topic to update.")

    graph = None
    if req.roadmap:
        graph = find_roadmap_any(root, req.roadmap)
        if graph is None:
            return _fail(f"no roadmap exactly matching '{req.roadmap}'.")

    if graph is not None:
        node = resolve_node(graph, wanted)
        if node is None:
            return _fail(
                f"no topic exactly matching '{wanted}' in '{graph.title}' "
                f"(exact match only — {len(graph.nodes)} topics there).",
                graph.topic_id,
                graph.title,
            )
        graph_id = graph.topic_id
    else:
        hit = find_node_anywhere(root, wanted)
        if hit is None:
            return _fail(
                f"no topic exactly matching '{wanted}' (it may be absent, or the name may "
                "exist in more than one roadmap — pass `roadmap` to disambiguate)."
            )
        graph_id, node = hit
        graph = try_load_roadmap(root, graph_id)
        if graph is None:
            return _fail(f"roadmap '{graph_id}' could not be read.")

    # What was study-ready BEFORE, so the change can report what it unlocked.
    before_ids = {n.id for n in get_available_nodes(graph)}

    status = (req.status or "done").strip().lower()
    ok, message = set_node_status(root, graph_id, node.id, status)
    if not ok:
        return _fail(message, graph_id, graph.title)

    updated_graph = try_load_roadmap(root, graph_id) or graph
    after = get_available_nodes(updated_graph)
    unlocked = [n.title for n in after if n.id not in before_ids and n.id != node.id]
    remaining = sum(1 for n in updated_graph.nodes.values() if n.status != "done")

    note_appended = False
    if (req.note or "").strip():
        target = updated_graph.nodes.get(node.id, node)
        note_appended, _ = append_deepened(
            root, graph_id, target, req.note.strip(), source="mentor:mark_topic_done"
        )

    return {
        "updated": True,
        "roadmap": graph_id,
        "roadmap_title": updated_graph.title,
        "node": node.id,
        "node_title": node.title,
        "status": status,
        "unlocked": unlocked,
        "remaining": remaining,
        "note_appended": note_appended,
        "error": None,
    }


# ---------------------------------------------------------------------------
# learn_repo — repo → concept inventory (the missing link, G1)
# ---------------------------------------------------------------------------


@router.post("/learn_repo")
def learn_repo(req: LearnRepoRequest) -> dict[str, Any]:
    """Derive a prerequisite curriculum from a repository, and optionally keep it.

    This is the analysis half nothing else in the system could do: `code-explorer`
    reads a repo and `goal_decomposer` plans a *topic*, but nothing bridged them, so
    a curriculum could never know that THIS repo's hard part is embedding drift, or
    which node maps to which file.

    The model proposes concepts and citations; code verifies every citation against
    the filesystem, caps the graph, resolves prerequisites, breaks cycles and assigns
    days. Anything it had to correct comes back in the response, so the mentor can
    say what was dropped rather than presenting a cleaned-up story.

    The extraction takes a minute or two (a real model call), so the TS side gives it
    a longer timeout than the 15s read default.
    """
    try:
        from orchestrator.cognition.repo_curriculum import extract
    except Exception as exc:
        return {"ok": False, "error": f"repo-curriculum module unavailable: {exc}"}

    report = extract(
        req.repo_path,
        title=(req.title or "").strip() or None,
        stopping_rule=(req.stopping_rule or "comprehension").strip().lower(),
        max_nodes=req.max_nodes,
        persist=bool(req.persist),
        target_days=req.target_days,
        hours_per_day=req.hours_per_day,
    )

    gt = report.get("ground_truth") or []
    concepts = [
        {
            "concept": c["concept"],
            "difficulty": c.get("difficulty"),
            "estimated_hours": c.get("estimated_hours") or 0.0,
            "day": c.get("day"),
            "anchors": [
                f"{a['path']}::{a['symbol']}" if a.get("symbol") else a["path"]
                for a in c.get("anchors") or []
            ],
            "prerequisites": c.get("prerequisites") or [],
            "what_to_cover": c.get("what_to_cover"),
        }
        for c in report.get("concepts") or []
    ]

    return {
        "ok": bool(report.get("ok")),
        "error": report.get("error"),
        "repo": report.get("repo") or "",
        "commit": report.get("commit"),
        "files_scanned": report.get("files_scanned") or 0,
        "summary": report.get("summary") or "",
        "roadmap": report.get("graph_id") or "",
        "title": report.get("title") or "",
        "concept_count": report.get("concept_count") or 0,
        "concepts": concepts,
        "anchor_coverage": report.get("anchor_coverage"),
        "dropped_anchors": report.get("dropped_anchors") or [],
        "dropped_concepts": report.get("dropped_concepts") or [],
        "broken_cycles": report.get("broken_cycles") or [],
        "ground_truth_found": sum(1 for r in gt if r.get("found")),
        "ground_truth_total": len(gt),
        "total_hours": report.get("total_hours") or 0.0,
        "persisted": bool(report.get("persisted")),
        "roadmap_path": report.get("roadmap_path"),
    }
