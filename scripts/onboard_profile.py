"""
scripts/onboard_profile.py

Interactive onboarding and profiling script for Nikhil's AI companion.
Uses the LLM to conduct a conversational interview, then extracts
structured profile facts and saves them to the PostgreSQL database.
"""

from __future__ import annotations

import json
import os
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

from orchestrator.memory.store import MemoryManager
from orchestrator.llm import get_reasoning_llm
from schemas import Project, ActiveLearningPath, LearningPathEntry, Preferences, SkillRating


# ---------------------------------------------------------------------------
# Structured Extraction Schemas
# ---------------------------------------------------------------------------

class CareerExtraction(BaseModel):
    education: Optional[str] = Field(None, description="E.g., B.Tech Civil Engineering, IIT Roorkee, 2025")
    location: Optional[str] = Field(None, description="Current city/state/country")
    long_term_goal: Optional[str] = Field(None, description="Nikhil's primary long term aspiration")
    employment_status: Optional[str] = Field(None, description="employed | unemployed_job_searching | freelancing | studying")
    target_roles: List[str] = Field(default_factory=list, description="Roles sought, e.g., ['AI Engineer', 'Data Scientist']")
    target_locations: List[str] = Field(default_factory=list, description="Locations targeted, e.g., ['India', 'UAE']")


class ProjectsExtraction(BaseModel):
    projects: List[Project] = Field(default_factory=list, description="List of portfolio projects mentioned by the user")


class LearningPathExtraction(BaseModel):
    title: str = Field(..., description="E.g., LangGraph for Production")
    description: Optional[str] = Field(None, description="Brief summary of the course or path")
    total_weeks: Optional[int] = Field(None, description="Total duration of the path in weeks")
    weekly_time_budget_hours: Optional[float] = Field(None, description="Hours allocated per week")
    entries: List[LearningPathEntry] = Field(
        default_factory=list,
        description="Structured topics mapped to weeks. Status must start as 'not_started'."
    )


class PreferencesExtraction(BaseModel):
    content_tone: Optional[str] = Field(None, description="E.g., 'concise, direct, dry humor'")
    preferred_language: str = Field("en", description="Default language")
    linkedin_posting_frequency: Optional[str] = Field(None, description="E.g., '2x/week'")


# ---------------------------------------------------------------------------
# Onboarding Interview Controller
# ---------------------------------------------------------------------------

class OnboardingSession:
    def __init__(self) -> None:
        self.mm = MemoryManager()
        self.mm.ensure_schema()
        self.llm = get_reasoning_llm(temperature=0.2)

    def speak(self, text: str) -> None:
        print(f"\n[Mentor] {text}")

    def ask(self, prompt_text: str) -> str:
        self.speak(prompt_text)
        try:
            return input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting onboarding.")
            sys.exit(0)

    def run(self) -> None:
        print("=" * 70)
        print("         NIKHIL'S AI COMPANION — INTERACTIVE ONBOARDING")
        print("=" * 70)
        self.speak(
            "Hello Nikhil! I'm your AI mentor. Let's build your context "
            "so that all helping agents have a rich, structured memory to work with. "
            "I will ask you questions section by section."
        )

        # Phase 1: Identity & Career Goals
        self._onboard_career()

        # Phase 2: Active Projects
        self._onboard_projects()

        # Phase 3: Learning Path
        self._onboard_learning_path()

        # Phase 4: Preferences
        self._onboard_preferences()

        print("\n" + "=" * 70)
        self.speak("Fantastic! Your profile memory is fully initialized and structured.")
        print("Done. You can now start the main chat using: `uv run python -m orchestrator`.")
        print("=" * 70)

    # -----------------------------------------------------------------------
    # Phase 1: Career
    # -----------------------------------------------------------------------
    def _onboard_career(self) -> None:
        print("\n--- PHASE 1: CAREER & GOALS ---")
        user_input = self.ask(
            "Tell me about your background and career goals. "
            "What did you study, where are you located, what target roles "
            "and locations are you looking for, and what is your long-term goal?"
        )

        extraction_prompt = (
            "You are parsing an onboarding conversation. "
            "Extract the career parameters from this message. "
            f"Message: {user_input}"
        )
        
        try:
            extractor = self.llm.with_structured_output(CareerExtraction)
            extracted = extractor.invoke([("human", extraction_prompt)])
            
            # Save extracted profile facts
            self.mm.set_profile_fact("identity", "full_name", "Nikhil", source="onboarding")
            if extracted.education:
                self.mm.set_profile_fact("education", "degrees", [extracted.education], source="onboarding")
            if extracted.location:
                self.mm.set_profile_fact("identity", "location", extracted.location, source="onboarding")
            if extracted.long_term_goal:
                self.mm.set_profile_fact("goals", "long_term_goal", extracted.long_term_goal, source="onboarding")
            if extracted.employment_status:
                self.mm.set_profile_fact("career", "employment_status", extracted.employment_status, source="onboarding")
            if extracted.target_roles:
                self.mm.set_profile_fact("career", "target_roles", extracted.target_roles, source="onboarding")
            if extracted.target_locations:
                self.mm.set_profile_fact("career", "target_locations", extracted.target_locations, source="onboarding")

            print("\n[DEBUG] Extracted Career Details:")
            print(extracted.model_dump_json(indent=2))
        except Exception as e:
            print(f"Error parsing career info: {e}. Using seed fallbacks.")

    # -----------------------------------------------------------------------
    # Phase 2: Projects
    # -----------------------------------------------------------------------
    def _onboard_projects(self) -> None:
        print("\n--- PHASE 2: PORTFOLIO PROJECTS ---")
        user_input = self.ask(
            "What portfolio projects are you currently working on or have completed recently? "
            "Please describe them, mentioning their name, tech stack, and deployment status (planning/active/done)."
        )

        extraction_prompt = (
            "You are parsing an onboarding conversation. "
            "Extract the list of projects from this message. "
            f"Message: {user_input}"
        )

        try:
            extractor = self.llm.with_structured_output(ProjectsExtraction)
            extracted = extractor.invoke([("human", extraction_prompt)])
            
            if extracted.projects:
                # Store projects array in profile facts
                projects_list = [p.model_dump() for p in extracted.projects]
                self.mm.set_profile_fact("career", "projects", projects_list, source="onboarding")
                print("\n[DEBUG] Extracted Projects:")
                print(extracted.model_dump_json(indent=2))
            else:
                print("No projects detected.")
        except Exception as e:
            print(f"Error parsing projects info: {e}.")

    # -----------------------------------------------------------------------
    # Phase 3: Learning Path
    # -----------------------------------------------------------------------
    def _onboard_learning_path(self) -> None:
        print("\n--- PHASE 3: ACTIVE LEARNING PATH ---")
        user_input = self.ask(
            "What is your active learning path right now? "
            "Give me a title (e.g. 'LangGraph for Production') and a list of topics "
            "you plan to cover week-by-week. How many weeks is it, and what is your weekly hours budget?"
        )

        extraction_prompt = (
            "You are parsing an onboarding conversation. "
            "Extract the active learning path from this message. "
            "Map topics to week numbers. Make sure the status of each entry is 'not_started'. "
            f"Message: {user_input}"
        )

        try:
            extractor = self.llm.with_structured_output(LearningPathExtraction)
            extracted = extractor.invoke([("human", extraction_prompt)])
            
            # Save learning path to profile facts
            path_dict = extracted.model_dump()
            self.mm.set_profile_fact("learning", "active_learning_path", path_dict, source="onboarding")
            print("\n[DEBUG] Extracted Active Learning Path:")
            print(extracted.model_dump_json(indent=2))
        except Exception as e:
            print(f"Error parsing learning path: {e}.")

    # -----------------------------------------------------------------------
    # Phase 4: Preferences
    # -----------------------------------------------------------------------
    def _onboard_preferences(self) -> None:
        print("\n--- PHASE 4: PREFERENCES & TONE ---")
        user_input = self.ask(
            "What are your preferences for content tone (e.g., direct, concise, informal) "
            "and how often do you plan to post on LinkedIn (e.g., '2x/week')?"
        )

        extraction_prompt = (
            "You are parsing an onboarding conversation. "
            "Extract the standing preferences from this message. "
            f"Message: {user_input}"
        )

        try:
            extractor = self.llm.with_structured_output(PreferencesExtraction)
            extracted = extractor.invoke([("human", extraction_prompt)])
            
            # Save preferences in profile facts
            prefs_dict = extracted.model_dump()
            self.mm.set_profile_fact("system", "preferences", prefs_dict, source="onboarding")
            print("\n[DEBUG] Extracted Preferences:")
            print(extracted.model_dump_json(indent=2))
        except Exception as e:
            print(f"Error parsing preferences: {e}.")


if __name__ == "__main__":
    session = OnboardingSession()
    session.run()
