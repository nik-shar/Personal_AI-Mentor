"""
orchestrator/cognition/personality.py

Personality System (Module 3)
Handles chat command parsing for personality overrides.
"""

from __future__ import annotations

from typing import Any

from orchestrator.memory.store import MemoryManager

# Cold-start defaults (Notes.md Layer C): start gentle and warm — the mentor
# earns the right to push as the relationship and the data deepen. Escalation
# happens only via the user's explicit command or an explicit proposal they
# accept, never silently. Stored user config always overrides these.
DEFAULT_PERSONALITY = {
    "accountability_level": 3,
    "tone": "warm",
    "humor": False,
    "custom_instructions": "",
    "coaching_emphasis": "consistency",
    "auto_adjust": False
}

def get_personality(mm: MemoryManager) -> dict[str, Any]:
    pers = mm.get_profile_fact("preferences", "mentor_personality")
    if not pers:
        return dict(DEFAULT_PERSONALITY)
    
    # Merge defaults for missing keys
    merged = dict(DEFAULT_PERSONALITY)
    merged.update(pers)
    return merged

def _set_personality(mm: MemoryManager, pers: dict[str, Any]) -> None:
    mm.set_profile_fact("preferences", "mentor_personality", pers)
