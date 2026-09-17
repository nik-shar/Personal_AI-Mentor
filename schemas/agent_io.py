"""
agent_io.py

Defines the contract between the orchestrator and every stateless sub-agent
(LinkedIn writer, learning monitor, and whatever gets added later).

Design principle: this contract does NOT change as you add agents. New
agents get a new task_type string and, if needed, their own params/output
sub-model — the envelope itself (AgentTask in, AgentResult out) stays fixed.
This is what makes it safe to add agent #11 without touching agent #1-10.

Also encodes the earlier decision: agents only draft/decide, they never
execute side effects. A LinkedIn agent returns drafted post text, not a
"post it now" API call — actually posting (if you ever add that) would be
a separate, deliberate step outside the agent's own logic.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .memory import MemorySlice

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"          # e.g. user explicitly asked for this right now


class TaskSource(str, Enum):
    """Where this task originated — mirrors the trigger types from the
    architecture diagram. Useful for agents/logging to know whether a task
    was self-initiated by the system or directly requested by Nikhil."""
    SCHEDULER = "scheduler"
    CHAT = "chat"
    OBSIDIAN_EVENT = "obsidian_event"
    ORCHESTRATOR_FOLLOWUP = "orchestrator_followup"   # e.g. one agent's output triggers another


class ResultStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"          # agent did something but flagged an issue
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"   # agent can't proceed without more input from Nikhil


# ---------------------------------------------------------------------------
# Draft / suggestion objects — outputs that are NOT executed automatically.
# Since agents never act (no auto-posting, no auto-writing), this is just a
# structured "here's something you might want to do with this" note.
# ---------------------------------------------------------------------------

class DraftSuggestion(BaseModel):
    """
    A drafted artifact an agent produced that implies a follow-up action,
    without the agent (or system) performing that action itself.
    e.g. LinkedIn agent produces a DraftSuggestion(kind="linkedin_post", ...)
    and it's on Nikhil to actually post it.
    """
    kind: str                      # e.g. "linkedin_post", "obsidian_note", "resume_edit"
    content: str                   # the actual drafted text/content
    suggested_destination: Optional[str] = None   # e.g. "Obsidian: Daily/2026-07-06.md"
    metadata: dict[str, Any] = Field(default_factory=dict)   # e.g. {"char_count": 280, "hashtags": [...]}


# ---------------------------------------------------------------------------
# AgentTask — orchestrator -> sub-agent
# ---------------------------------------------------------------------------

class AgentTask(BaseModel):
    """
    What the orchestrator sends to a sub-agent to invoke it for one run.
    This is the entire input a stateless agent receives — nothing else.
    """
    task_id: str                          # unique id, useful for logging/tracing this specific run
    agent_name: str                       # which agent this is routed to, e.g. "linkedin_writer"
    task_type: str                        # more granular than agent_name if one agent handles several task types
    instructions: str                     # natural-language description of what's needed this run

    source: TaskSource
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: datetime = Field(default_factory=datetime.utcnow)

    memory_slice: MemorySlice             # the trimmed context (see memory.py)

    params: dict[str, Any] = Field(default_factory=dict)
    # agent-specific extra inputs, deliberately loose since sub-agents
    # aren't finalized yet. e.g. for linkedin_writer: {"topic_hint": "...", "max_length": 300}
    # Once an agent stabilizes, you can optionally replace this dict with a
    # dedicated typed model (e.g. LinkedInParams) without touching AgentTask itself.

    # Set by the orchestrator if this task is itself a followup from a
    # previous agent's output (chained agents) — lets you trace causality.
    parent_task_id: Optional[str] = None


# ---------------------------------------------------------------------------
# AgentResult — sub-agent -> orchestrator
# ---------------------------------------------------------------------------

class AgentResult(BaseModel):
    """
    What a sub-agent returns after running. The orchestrator uses this to:
      1. Show/log the output
      2. Merge memory_delta into DNAMemory
      3. Surface any draft_suggestions to Nikhil (never auto-executed)
    """
    task_id: str                          # matches the AgentTask.task_id this responds to
    agent_name: str
    task_type: str
    status: ResultStatus
    completed_at: datetime = Field(default_factory=datetime.utcnow)

    output: str                           # the primary human-readable result of this run
    # e.g. for the planner skill: "You logged a React deep-dive today, 5 day streak"
    # e.g. for linkedin_writer: could duplicate the draft text, or a short summary
    # pointing to draft_suggestions below — pick one convention and stick to it.

    memory_delta: dict[str, Any] = Field(default_factory=dict)
    # fields to merge into DNAMemory. e.g. {"learning_streak_days": 5} or
    # {"applications": [...]} — orchestrator handles the actual merge logic,
    # this is just the proposed diff.

    draft_suggestions: list[DraftSuggestion] = Field(default_factory=list)
    # zero or more drafted artifacts (see above) — nothing here is executed
    # automatically, Nikhil (or a future explicit action step) decides.

    confidence: Optional[float] = None    # 0-1, optional self-reported confidence in the output
    error_message: Optional[str] = None   # populated if status is FAILED or PARTIAL
    clarification_needed: Optional[str] = None   # populated if status is NEEDS_CLARIFICATION

    # Free-form bucket for anything agent-specific that doesn't fit above,
    # without needing to modify this shared schema.
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Optional: batch wrapper, only needed if/when the orchestrator fans out to
# multiple agents in a single cycle (e.g. an evening run that triggers both
# (e.g. an evening run that triggers both the planner skill and a daily-digest agent together). Not required for the
# single-agent-per-turn case — include only if you decide to support fan-out.
# ---------------------------------------------------------------------------

class AgentTaskBatch(BaseModel):
    batch_id: str
    tasks: list[AgentTask]
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentResultBatch(BaseModel):
    batch_id: str
    results: list[AgentResult]
    completed_at: datetime = Field(default_factory=datetime.utcnow)