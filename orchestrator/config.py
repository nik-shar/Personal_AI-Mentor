"""
orchestrator/config.py

Central constants for the orchestrator. Includes:
- Database connection defaults
- Obsidian vault configuration
- Agent context schemas (what each agent reads from the three memory tiers)
- Reasoning prompt templates
- Memory merge policy (which memory_delta keys go to profile vs. episodic)
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://nik:nik@localhost:5432/ai_companion",
)

# The mentor's operating constitution (guidelines + user-authored anchor
# identity facts). Loaded by orchestrator/cognition/guidelines.py.
MENTOR_GUIDELINES_PATH = os.getenv(
    "MENTOR_GUIDELINES_PATH",
    str(Path(__file__).resolve().parent.parent / "mentor_agent_guidelines.md"),
)

# The mentor's voice layer (voice rules + hard rules + few-shot examples). A
# runtime-neutral content file: this repo's Python build and the PI agent core
# (`mentor/src/identity.ts`) both read the same file, so the voice cannot drift
# between them. Loaded fresh by orchestrator/cognition/persona.py.
MENTOR_PERSONA_PATH = os.getenv(
    "MENTOR_PERSONA_PATH",
    str(Path(__file__).resolve().parent.parent / "mentor_persona.md"),
)

# Display name for the user in transcripts and prompts. Everything else about
# the user is learned through conversation (or the guidelines identity anchor).
USER_NAME = os.getenv("MENTOR_USER_NAME", "Nik")

# ---------------------------------------------------------------------------
# Timezone basis
#
# Every slot clock in the calendar grid ("22:00") means a wall-clock time in
# ONE timezone: this one. Before it existed, the system mixed two bases — the
# write paths converted input to UTC and the read paths printed the raw index
# label, so "22:00" could round-trip as "16:30" on a UTC+5:30 machine, and the
# same request could produce two different schedules depending on whether the
# model passed +00:00 or +05:30.
#
# Unset → the machine's own timezone. Set it explicitly when the mentor runs
# somewhere other than the user's clock (a server, a container).
#   MENTOR_TIMEZONE=Asia/Kolkata
# ---------------------------------------------------------------------------

MENTOR_TIMEZONE = os.getenv("MENTOR_TIMEZONE", "").strip()


def local_tz():
    """The single timezone every slot clock is expressed in."""
    if MENTOR_TIMEZONE:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(MENTOR_TIMEZONE)
        except Exception as exc:  # ZoneInfoNotFoundError, missing tzdata, ...
            print(f"[config] MENTOR_TIMEZONE='{MENTOR_TIMEZONE}' unusable ({exc}) — using the system zone.")
    return datetime.now().astimezone().tzinfo


def now_local() -> datetime:
    """Now, in the grid's timezone. The one clock the system should read."""
    return datetime.now(local_tz())


def today_local() -> date:
    """The user's current date. Every 'today' default uses this, never UTC's."""
    return now_local().date()

EMBEDDING_DIMENSION = 384   # sentence-transformers all-MiniLM-L6-v2 output dimension

# ---------------------------------------------------------------------------
# Obsidian vault configuration
# ---------------------------------------------------------------------------

OBSIDIAN_VAULT_PATH = os.getenv(
    "OBSIDIAN_VAULT_PATH",
    "/home/nik/Documents/AI-Mentor",
)

OBSIDIAN_TOPIC_FOLDER = os.getenv(
    "OBSIDIAN_TOPIC_FOLDER",
    "Learning/Topics",
)

OBSIDIAN_DAILY_NOTES_FOLDER = os.getenv(
    "OBSIDIAN_DAILY_NOTES_FOLDER",
    "Daily Notes",
)

# Career folder: master resume (master_resume.yaml) + per-application folders
# used by the job_hunter agent.
OBSIDIAN_CAREER_FOLDER = os.getenv(
    "OBSIDIAN_CAREER_FOLDER",
    "Career",
)

# ---------------------------------------------------------------------------
# Curriculum root — the in-repo roadmap store (Phase 0 of the roadmap redesign)
#
# Two vaults, split by purpose rather than by accident:
#
#   OBSIDIAN_VAULT_PATH      personal artifacts — career documents, the master
#                            resume, daily notes. PRIVATE. It stays outside any
#                            git repository because this repo has a GitHub
#                            remote, and Python remains its only writer.
#
#   MENTOR_CURRICULUM_PATH   the roadmaps and their node notes. This one lives
#                            INSIDE the repo on purpose: it is therefore inside
#                            WORKSPACE_ROOTS, so the PI core can read a note the
#                            moment he asks a doubt mid-study and can append
#                            deeper explanations with its normal file tools.
#                            Structure (the manifest) is still Python-written
#                            and validated, so a hand edit to a note can never
#                            corrupt the DAG.
#
# Layout: <root>/<graph_id>/roadmap.yaml          — the manifest (authoritative)
#         <root>/<graph_id>/<Node Title>.md       — notes (generated + appended)
# ---------------------------------------------------------------------------

MENTOR_CURRICULUM_PATH = os.getenv(
    "MENTOR_CURRICULUM_PATH",
    str(Path(__file__).resolve().parent.parent / "learning" / "topics"),
)

# ---------------------------------------------------------------------------
# Profile facts categories
# ---------------------------------------------------------------------------

PROFILE_CATEGORY_IDENTITY = "identity"
PROFILE_CATEGORY_CAREER = "career"
PROFILE_CATEGORY_EDUCATION = "education"
PROFILE_CATEGORY_GOALS = "goals"
PROFILE_CATEGORY_SKILLS = "skills"
PROFILE_CATEGORY_PREFERENCES = "preferences"
PROFILE_CATEGORY_PROJECTS = "projects"
PROFILE_CATEGORY_LEARNING = "learning"
PROFILE_CATEGORY_SYSTEM = "system"

# ---------------------------------------------------------------------------
# Profile keys that live in profile_facts.
# This list is used by the memory manager and merger.
# ---------------------------------------------------------------------------

PROFILE_KEYS = {
    "full_name",
    "date_of_birth",
    "location",
    "degrees",
    "current_role",
    "employment_status",
    "target_roles",
    "target_locations",
    "long_term_goal",
    "short_term_goal",
    "ms_target_schools",
    "ms_target_term",
    "leetcode_rating",
    "skills",
    "preferences",
    "linkedin_posting_frequency",
    "active_learning_path",
    "learning_streak_days",
    "projects",
    "energy_level",
    "flags",
    "agent_private_memory",
    "last_linkedin_topic",
    "extra_instructions",
    "procedural_rules",
    "mindset_calibration",
    "topic_graphs",
    "job_pipeline",
}

# Mapping from flat memory_delta keys to (category, key) in profile_facts.
PROFILE_KEY_MAP: dict[str, tuple[str, str]] = {
    "full_name": (PROFILE_CATEGORY_IDENTITY, "full_name"),
    "location": (PROFILE_CATEGORY_IDENTITY, "location"),
    "degrees": (PROFILE_CATEGORY_EDUCATION, "degrees"),
    "current_role": (PROFILE_CATEGORY_CAREER, "current_role"),
    "employment_status": (PROFILE_CATEGORY_CAREER, "employment_status"),
    "target_roles": (PROFILE_CATEGORY_CAREER, "target_roles"),
    "target_locations": (PROFILE_CATEGORY_CAREER, "target_locations"),
    "long_term_goal": (PROFILE_CATEGORY_GOALS, "long_term_goal"),
    "short_term_goal": (PROFILE_CATEGORY_GOALS, "short_term_goal"),
    "ms_target_schools": (PROFILE_CATEGORY_GOALS, "ms_target_schools"),
    "ms_target_term": (PROFILE_CATEGORY_GOALS, "ms_target_term"),
    "leetcode_rating": (PROFILE_CATEGORY_GOALS, "leetcode_rating"),
    "skills": (PROFILE_CATEGORY_SKILLS, "skills"),
    "preferences": (PROFILE_CATEGORY_PREFERENCES, "preferences"),
    "linkedin_posting_frequency": (PROFILE_CATEGORY_PREFERENCES, "linkedin_posting_frequency"),
    "active_learning_path": (PROFILE_CATEGORY_LEARNING, "active_learning_path"),
    "learning_streak_days": (PROFILE_CATEGORY_LEARNING, "learning_streak_days"),
    "projects": (PROFILE_CATEGORY_PROJECTS, "projects"),
    "energy_level": (PROFILE_CATEGORY_SYSTEM, "energy_level"),
    "flags": (PROFILE_CATEGORY_SYSTEM, "flags"),
    "agent_private_memory": (PROFILE_CATEGORY_SYSTEM, "agent_private_memory"),
    "last_linkedin_topic": (PROFILE_CATEGORY_SYSTEM, "last_linkedin_topic"),
    "extra_instructions": (PROFILE_CATEGORY_PREFERENCES, "extra_instructions"),
    "procedural_rules": (PROFILE_CATEGORY_PREFERENCES, "procedural_rules"),
    "mindset_calibration": (PROFILE_CATEGORY_PREFERENCES, "mindset_calibration"),
    "topic_graphs": (PROFILE_CATEGORY_LEARNING, "topic_graphs"),
    # Current-state list of job applications (JobApplication dicts), written
    # wholesale by job_hunter. The append-only timeline lives in episodic
    # "job_application" events via the separate "applications" delta key.
    "job_pipeline": (PROFILE_CATEGORY_CAREER, "job_pipeline"),
}

# ---------------------------------------------------------------------------
# Memory-delta keys that produce episodic events.
# Value: the event_type tag written to episodic_events.
# ---------------------------------------------------------------------------

EPISODIC_DELTA_KEYS: dict[str, str] = {
    "daily_plans": "daily_plan",
    "learning_log": "learning_session",
    "applications": "job_application",
    "linkedin_drafts": "linkedin_draft",
    "mood_notes": "mood_note",
    "activity_log": "agent_run",
}


# ---------------------------------------------------------------------------
# Agent context schemas.
# Each entry defines what the context_builder pulls from each tier.
#
# Fields:
#   profile_keys: list of keys to read from profile_facts (flat keys above)
#   episodic: list of {"alias": ..., "event_type": ..., "last_n": int, "tags": [...]}
#   private_memory: agent name for private memory lookup (optional)
# ---------------------------------------------------------------------------

AGENT_CONTEXT_SCHEMAS: dict[str, dict] = {
    "linkedin_writer": {
        "profile_keys": [
            "projects",
            "employment_status",
            "target_roles",
            "preferences",
            "last_linkedin_topic",
        ],
        "episodic": [
            {"alias": "recent_posts", "event_type": "linkedin_draft", "last_n": 5},
            {"alias": "high_importance_wins", "event_type": None, "importance__gte": 4, "last_n": 10},
        ],
        "private_memory": "linkedin_writer",
    },
    "goal_decomposer": {
        "profile_keys": [
            "preferences",
            "skills",
            "active_learning_path",
        ],
        "virtual_keys": ["obsidian_topic_graphs"],
        "episodic": [],
        "private_memory": None,
    },
    "job_hunter": {
        "profile_keys": [
            "target_roles",
            "target_locations",
            "job_pipeline",
        ],
        "episodic": [
            {"alias": "application_history", "event_type": "job_application", "last_n": 20},
        ],
        "private_memory": "job_hunter",
    },
    "fallback": {
        "profile_keys": [
            "full_name",
            "short_term_goal",
            "current_role",
        ],
        "episodic": [{"alias": "recent_activity", "last_n": 10}],
        "private_memory": None,
    },
}

# ---------------------------------------------------------------------------
# Routing hints: simple keyword maps for hard-to-classify free text.
# ---------------------------------------------------------------------------

ROUTING_HINTS: dict[str, list[str]] = {
    "linkedin_writer": [
        "linkedin post",
        "draft a post",
        "write a post",
        "linkedin",
    ],
    "goal_decomposer": [
        "write a tutorial",
        "write tutorial",
        "tutorial on",
        "tutorial for",
        "full tutorial",
        "deep dive on",
        "deep note on",
        "study note on",
        "explain to me",
        "make a note on",
        "create a plan to learn",
        "plan to learn",
        "i want to learn",
        "i want to finish",
        "decompose",
        "break down",
        "5 days",
        "this week",
        "in n days",
        "roadmap",
        "vault",
        "prebuilt",
        "what to follow",
    ],
    "job_hunter": [
        "tailor my resume",
        "tailor the resume",
        "resume for",
        "modify my resume",
        "job description",
        " jd",
        "i applied",
        "applied to",
        "applying to",
        "job application",
        "interview at",
        "interview with",
        "recruiter",
        "offer from",
        "rejected by",
        "job hunt",
        "job search",
        "should i apply",
        "good fit",
        "how relevant",
        "relevant for me",
        "worth applying",
        "find jobs",
        "search jobs",
        "show me jobs",
        "find me jobs",
        "job listings",
        "openings",
        "any jobs",
        "job openings",
        "new roles",
        "jobs for me",
        "jobs in ",
        "roles for me",
        " jobs",   # catch-all for "...AI Engineer jobs" phrasings (fallback only)
        "hiring",
    ],
}

DEFAULT_FALLBACK_AGENT = "fallback"

# Canonical task types per agent — the reasoner must choose from these (see the
# "VALID task_type VALUES" block in nodes/reasoner.py). dispatch_node validates
# the LLM's task_type against this map and drops hallucinated values (observed:
# "find_jobs", "search_openings", "job_listings" — none of which exist).
# NOTE: mirrors registry.REGISTRY's allowed_task_types; keep both in sync.
VALID_TASK_TYPES: dict[str, list[str]] = {
    "linkedin_writer":  ["write_linkedin_post"],
    "goal_decomposer":  ["decompose_goal", "write_tutorial"],
    "job_hunter": [
        "tailor_resume",
        "log_application",
        "update_application_status",
        "job_search_review",
        "assess_fit",
        "search_jobs",
    ],
    "fallback":         ["direct_response"],
}

# Default task type when the reasoner routes to an agent without naming one.
# job_hunter is deliberately EMPTY: it is a multi-action agent, and its
# input_parser (LLM parse + keyword heuristic) resolves intent from the raw
# message far more reliably than any single default could — a fixed default of
# "tailor_resume" used to hijack "find me jobs" into the tailor branch.
DEFAULT_TASK_TYPES: dict[str, str] = {
    "linkedin_writer":  "write_linkedin_post",
    "goal_decomposer":  "decompose_goal",
    "job_hunter":       "",   # multi-action — let the agent parse intent itself
    "fallback":         "direct_response",
}

# ---------------------------------------------------------------------------
# Instruction-set toolkits (the toolkit host layer)
# ---------------------------------------------------------------------------

# Root folder holding toolkit bundles. Each subfolder is one instruction-set
# toolkit (manifest + philosophy + behaviors + guardrails + workflows/*.md),
# loaded by orchestrator/toolkits.py and activated per-turn as an overlay over
# the persistent mentor identity. Overridable for tests via TOOLKITS_DIR.
TOOLKITS_DIR = os.getenv(
    "TOOLKITS_DIR",
    str(Path(__file__).resolve().parent.parent / "toolkits"),
)

# ---------------------------------------------------------------------------
# Code-explorer workspace sandbox (Shape-1 code grounding)
# ---------------------------------------------------------------------------

# The mentor may read/grep/git/test ONLY under these roots. Default: this repo
# (the mentor's own codebase) + anything in MENTOR_WORKSPACE_ROOTS
# (os.pathsep-separated). Tools refuse any path outside these roots.
WORKSPACE_ROOTS: tuple[str, ...] = tuple(
    p
    for p in (
        [str(Path(__file__).resolve().parent.parent)]
        + os.getenv("MENTOR_WORKSPACE_ROOTS", "").split(os.pathsep)
    )
    if p
)

