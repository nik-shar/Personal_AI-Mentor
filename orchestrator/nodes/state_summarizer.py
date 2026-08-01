"""
orchestrator/nodes/state_summarizer.py

Condenses the three memory tiers into a compact text block that the reasoner
can read without blowing its context window.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone

from orchestrator.config import AGENT_CONTEXT_SCHEMAS
from orchestrator.memory.store import MemoryManager


SUMMARY_PROFILE_KEYS = {
    "full_name",
    "current_role",
    "employment_status",
    "target_roles",
    "target_locations",
    "long_term_goal",
    "short_term_goal",
    "ms_target_schools",
    "ms_target_term",
    "leetcode_rating",
    "learning_streak_days",
    "active_learning_path",
    "projects",
    "preferences",
    "energy_level",
    "working_habits",
    "mindset_notes",
    "bio_summary",
}


def build_summary(memory_manager: MemoryManager, user_input: str) -> str:
    """Return a concise state summary string for the reasoner."""
    profile = memory_manager.load_profile_facts(SUMMARY_PROFILE_KEYS)
    recent_events = memory_manager.query_episodic(
        since=datetime.now(timezone.utc) - timedelta(days=7),
        last_n=25,
    )

    profile_lines = [
        "=== PROFILE SNAPSHOT ===",
        _format_profile_line("Full name", profile.get("full_name")),
        _format_profile_line("Current role", profile.get("current_role")),
        _format_profile_line("Employment status", profile.get("employment_status")),
        _format_profile_line("Target roles", profile.get("target_roles")),
        _format_profile_line("Target locations", profile.get("target_locations")),
        _format_profile_line("Long-term goal", profile.get("long_term_goal")),
        _format_profile_line("Short-term goal", profile.get("short_term_goal")),
        _format_profile_line("MS target schools", profile.get("ms_target_schools")),
        _format_profile_line("MS target term", profile.get("ms_target_term")),
        _format_profile_line("LeetCode rating", profile.get("leetcode_rating")),
        _format_profile_line("Learning streak", profile.get("learning_streak_days")),
        _format_profile_line("Energy level", profile.get("energy_level")),
    ]

    event_lines = ["=== LAST 7 DAYS / 25 EVENTS ==="]
    kinds = {}
    for evt in recent_events:
        kind = evt.get("event_type", "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
        occurred = evt.get("occurred_at", "")
        content = (evt.get("content") or "").replace("\n", " ")
        if len(content) > 160:
            content = content[:157] + "..."
        event_lines.append(f"- [{occurred}] {kind}: {content}")

    pattern_lines = [
        "=== SHORT-TERM PATTERNS ===",
        f"Total events last 7 days: {len(recent_events)}",
    ]
    for kind, count in sorted(kinds.items(), key=lambda x: -x[1]):
        pattern_lines.append(f"- {kind}: {count}")

    lines = (
        profile_lines
        + [""]
        + pattern_lines
        + [""]
        + event_lines
        + ["", f"=== USER INPUT ===\n{user_input}"]
    )
    return "\n".join(line for line in lines if line is not None)


def _format_profile_line(label: str, value) -> str | None:
    if value is None or value == "":
        return None
    return f"- {label}: {value}"
