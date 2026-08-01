"""
scripts/seed_bio.py

Parses a free-form narrative bio document (about_me.md) using LLM JSON extraction,
extracting hard facts and soft personality/working-habit traits into PostgreSQL DNA Memory.

Usage:
    uv run python scripts/seed_bio.py                  # reads about_me.md
    uv run python scripts/seed_bio.py custom_bio.md    # reads custom file
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(override=True)

from orchestrator.llm import get_reasoning_llm
from orchestrator.memory.store import MemoryManager


# ---------------------------------------------------------------------------
# Structured Bio Extraction Sub-schemas
# ---------------------------------------------------------------------------

class SimpleSkill(BaseModel):
    name: str
    rating_out_of_10: Optional[float] = Field(7.0, description="Float between 1.0 and 10.0")
    notes: Optional[str] = None


class SimpleProject(BaseModel):
    name: str
    description: str
    status: Optional[str] = "active"
    tech_stack: List[str] = Field(default_factory=list)


class SimpleLearningPath(BaseModel):
    title: str
    description: Optional[str] = None
    topics: List[str] = Field(default_factory=list)


class BioDNAExtraction(BaseModel):
    full_name: Optional[str] = None
    location: Optional[str] = None
    degrees: List[str] = Field(default_factory=list)
    bio_summary: Optional[str] = None

    employment_status: Optional[str] = None
    current_role: Optional[str] = None
    target_roles: List[str] = Field(default_factory=list)
    target_locations: List[str] = Field(default_factory=list)
    long_term_goal: Optional[str] = None
    short_term_goal: Optional[str] = None

    skills: List[SimpleSkill] = Field(default_factory=list)
    projects: List[SimpleProject] = Field(default_factory=list)

    active_learning_path: Optional[SimpleLearningPath] = None

    working_habits: List[str] = Field(default_factory=list)
    mindset_notes: List[str] = Field(default_factory=list)
    content_tone: Optional[str] = None
    linkedin_posting_frequency: Optional[str] = None


# ---------------------------------------------------------------------------
# Seeding Execution
# ---------------------------------------------------------------------------

def seed_from_bio(bio_path: Path) -> None:
    if not bio_path.exists():
        print(f"ERROR: File not found: {bio_path}")
        sys.exit(1)

    print(f"📖 Reading narrative bio from: {bio_path}")
    raw_text = bio_path.read_text(encoding="utf-8").strip()

    if not raw_text:
        print("ERROR: Narrative bio file is empty.")
        sys.exit(1)

    print("🧠 Extracting DNA Memory facts via LLM...")
    llm = get_reasoning_llm(temperature=0.1)

    system_prompt = (
        "You are an expert profile analyst. Read the user's narrative bio document "
        "and extract a structured JSON object matching this schema:\n"
        "{\n"
        '  "full_name": string | null,\n'
        '  "location": string | null,\n'
        '  "degrees": [string],\n'
        '  "bio_summary": string (2-3 sentence overview),\n'
        '  "employment_status": "employed" | "unemployed_job_searching" | "freelancing" | "studying" | null,\n'
        '  "current_role": string | null,\n'
        '  "target_roles": [string],\n'
        '  "target_locations": [string],\n'
        '  "long_term_goal": string | null,\n'
        '  "short_term_goal": string | null,\n'
        '  "skills": [{"name": string, "rating_out_of_10": float, "notes": string | null}],\n'
        '  "projects": [{"name": string, "description": string, "status": string, "tech_stack": [string]}],\n'
        '  "active_learning_path": {"title": string, "description": string | null, "topics": [string]} | null,\n'
        '  "working_habits": [string],\n'
        '  "mindset_notes": [string],\n'
        '  "content_tone": string | null,\n'
        '  "linkedin_posting_frequency": string | null\n'
        "}\n\n"
        "Return ONLY raw JSON. No markdown codeblocks, no extra explanation."
    )

    try:
        res = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Narrative Bio Document:\n\n{raw_text}"},
        ])
        content = res.content.strip()
        # Clean potential markdown fences
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)

        extracted = BioDNAExtraction.model_validate_json(content)
    except Exception as exc:
        print(f"❌ Failed to extract profile from bio: {exc}")
        sys.exit(1)

    print("💾 Connecting to memory store and writing profile facts...")
    mm = MemoryManager()
    mm.ensure_schema()

    # Map extracted model fields to profile facts
    facts_to_save: dict[str, Any] = {}

    if extracted.full_name:
        facts_to_save["full_name"] = extracted.full_name
    if extracted.location:
        facts_to_save["location"] = extracted.location
    if extracted.degrees:
        facts_to_save["degrees"] = extracted.degrees
    if extracted.bio_summary:
        facts_to_save["bio_summary"] = extracted.bio_summary

    if extracted.employment_status:
        facts_to_save["employment_status"] = extracted.employment_status
    if extracted.current_role:
        facts_to_save["current_role"] = extracted.current_role
    if extracted.target_roles:
        facts_to_save["target_roles"] = extracted.target_roles
    if extracted.target_locations:
        facts_to_save["target_locations"] = extracted.target_locations
    if extracted.long_term_goal:
        facts_to_save["long_term_goal"] = extracted.long_term_goal
    if extracted.short_term_goal:
        facts_to_save["short_term_goal"] = extracted.short_term_goal

    if extracted.skills:
        skills_data = []
        for s in extracted.skills:
            s_dict = s.model_dump(mode="json")
            if s_dict.get("rating_out_of_10") is None:
                s_dict["rating_out_of_10"] = 7.0
            skills_data.append(s_dict)
        facts_to_save["skills"] = skills_data
    if extracted.projects:
        facts_to_save["projects"] = [p.model_dump(mode="json") for p in extracted.projects]

    if extracted.active_learning_path:
        path_data = extracted.active_learning_path
        entries = [
            {
                "topic": topic,
                "week": 1 + idx // 2,
                "order": idx + 1,
                "status": "in_progress" if idx == 0 else "not_started",
            }
            for idx, topic in enumerate(path_data.topics)
        ]
        facts_to_save["active_learning_path"] = {
            "title": path_data.title,
            "description": path_data.description,
            "entries": entries,
            "current_week": 1,
        }

    if extracted.working_habits:
        facts_to_save["working_habits"] = extracted.working_habits
    if extracted.mindset_notes:
        facts_to_save["mindset_notes"] = extracted.mindset_notes

    # Preferences
    prefs: dict[str, Any] = {}
    if extracted.content_tone:
        prefs["content_tone"] = extracted.content_tone
    if extracted.linkedin_posting_frequency:
        prefs["linkedin_posting_frequency"] = extracted.linkedin_posting_frequency
        facts_to_save["linkedin_posting_frequency"] = extracted.linkedin_posting_frequency
    if prefs:
        facts_to_save["preferences"] = prefs

    # Persist all extracted facts
    mm.merge_profile_dict(facts_to_save, source="seed_bio.py")

    # Also log an episodic event for RAG semantic memory
    mm.add_episodic_event(
        source_agent="seed_bio",
        event_type="profile_seeded",
        content=f"Profile DNA memory seeded from bio file ({bio_path.name}). Summary: {extracted.bio_summary or 'Full narrative bio imported.'}",
        payload={"extracted_keys": list(facts_to_save.keys())},
        tags=["bio_seed", "onboarding"],
        importance=4,
    )

    print("\n✅ DNA Memory Seeded Successfully!")
    print("--------------------------------------------------")
    for key, value in facts_to_save.items():
        if isinstance(value, list):
            print(f"  • {key}: {len(value)} item(s)")
        elif isinstance(value, dict):
            print(f"  • {key}: {list(value.keys())}")
        else:
            val_str = str(value)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            print(f"  • {key}: {val_str}")
    print("--------------------------------------------------")


if __name__ == "__main__":
    target_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "about_me.md"
    seed_from_bio(target_path)
