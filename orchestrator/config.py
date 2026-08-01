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

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://nik:nik@localhost:5432/ai_companion",
)

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
    "working_habits",
    "mindset_notes",
    "bio_summary",
    "extra_instructions",
    "procedural_rules",
    "mindset_calibration",
    "topic_graphs",
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
    "working_habits": (PROFILE_CATEGORY_PREFERENCES, "working_habits"),
    "mindset_notes": (PROFILE_CATEGORY_PREFERENCES, "mindset_notes"),
    "bio_summary": (PROFILE_CATEGORY_IDENTITY, "bio_summary"),
    "extra_instructions": (PROFILE_CATEGORY_PREFERENCES, "extra_instructions"),
    "procedural_rules": (PROFILE_CATEGORY_PREFERENCES, "procedural_rules"),
    "mindset_calibration": (PROFILE_CATEGORY_PREFERENCES, "mindset_calibration"),
    "topic_graphs": (PROFILE_CATEGORY_LEARNING, "topic_graphs"),
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
    "daily_planner": {
        "profile_keys": [
            "bio_summary",
            "working_habits",
            "mindset_notes",
            "long_term_goal",
            "short_term_goal",
            "active_learning_path",
            "projects",
            "employment_status",
            "target_roles",
            "preferences",
            "linkedin_posting_frequency",
            "energy_level",
            "extra_instructions",
            "procedural_rules",
            "mindset_calibration",
        ],
        # obsidian_topic_graphs is a virtual key handled by context_builder:
        # reads .md files from the Obsidian vault instead of the DB.
        "virtual_keys": ["obsidian_topic_graphs"],
        "episodic": [
            {"alias": "daily_plans", "event_type": "daily_plan", "last_n": 7},
            {"alias": "learning_log", "event_type": "learning_session", "last_n": 14},
            {"alias": "mood_notes", "event_type": "mood_note", "tags": ["energy"], "last_n": 7},
        ],
        "private_memory": "daily_planner",
    },
    "learning_monitor": {
        "profile_keys": [
            "bio_summary",
            "working_habits",
            "mindset_notes",
            "active_learning_path",
            "learning_streak_days",
            "preferences",
            "extra_instructions",
            "procedural_rules",
            "mindset_calibration",
        ],
        "virtual_keys": ["obsidian_topic_graphs"],
        "episodic": [
            {"alias": "todays_plan", "event_type": "daily_plan", "last_n": 1},
            {"alias": "learning_log", "event_type": "learning_session", "last_n": 14},
            {"alias": "mood_notes", "event_type": "mood_note", "last_n": 7},
        ],
        "private_memory": "learning_monitor",
    },
    "linkedin_writer": {
        "profile_keys": [
            "bio_summary",
            "projects",
            "employment_status",
            "target_roles",
            "preferences",
            "last_linkedin_topic",
            "extra_instructions",
            "procedural_rules",
            "mindset_calibration",
        ],
        "episodic": [
            {"alias": "recent_posts", "event_type": "linkedin_draft", "last_n": 5},
            {"alias": "high_importance_wins", "event_type": None, "importance__gte": 4, "last_n": 10},
        ],
        "private_memory": "linkedin_writer",
    },
    "goal_decomposer": {
        "profile_keys": [
            "bio_summary",
            "working_habits",
            "mindset_notes",
            "preferences",
            "skills",
            "active_learning_path",
            "extra_instructions",
            "procedural_rules",
            "mindset_calibration",
        ],
        "virtual_keys": ["obsidian_topic_graphs"],
        "episodic": [],
        "private_memory": None,
    },
    "fallback": {
        "profile_keys": [
            "full_name",
            "short_term_goal",
            "current_role",
            "procedural_rules",
            "mindset_calibration",
        ],
        "episodic": [{"alias": "recent_activity", "last_n": 10}],
        "private_memory": None,
    },
}

# ---------------------------------------------------------------------------
# Routing hints: simple keyword maps for hard-to-classify free text.
# ---------------------------------------------------------------------------

ROUTING_HINTS: dict[str, list[str]] = {
    "daily_planner": [
        "plan my day",
        "schedule today",
        "what should i do today",
        "tomorrow",
    ],
    "learning_monitor": [
        "i learned",
        "i finished",
        "i studied",
        "i did",
        "completed",
        "skipped",
        "no learning",
    ],
    "linkedin_writer": [
        "linkedin post",
        "draft a post",
        "write a post",
        "linkedin",
    ],
    "goal_decomposer": [
        "create a plan to learn",
        "plan to learn",
        "i want to learn",
        "i want to finish",
        "decompose",
        "break down",
        "5 days",
        "this week",
        "in n days",
    ],
}

DEFAULT_FALLBACK_AGENT = "fallback"
DEFAULT_TASK_TYPES: dict[str, str] = {
    "daily_planner":    "build_daily_plan",
    "learning_monitor": "log_learning_session",
    "linkedin_writer":  "write_linkedin_post",
    "goal_decomposer":  "decompose_goal",
    "fallback":         "direct_response",
}

