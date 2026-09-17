"""
schemas package

Re-exports the core models so the rest of the codebase can do:
    from schemas import DNAMemory, AgentTask, AgentResult
instead of reaching into schemas.memory / schemas.agent_io directly.

Keep this file as a thin re-export layer only — no logic, no validation,
no I/O. If it starts growing helper functions, that's a signal they belong
in a different module (e.g. a schemas/utils.py or the orchestrator itself).
"""

from .agent_io import (
    AgentResult,
    AgentResultBatch,
    AgentTask,
    AgentTaskBatch,
    DraftSuggestion,
    ResultStatus,
    TaskPriority,
    TaskSource,
)
from .memory import (
    ActiveLearningPath,
    ActivityLogEntry,
    ApplicationStage,
    DailyPlan,
    DNAMemory,
    EmploymentStatus,
    JobApplication,
    LearningLogEntry,
    LearningPathEntry,
    MemorySlice,
    MindsetCalibration,
    NodeAnchor,
    NodeCheckpoint,
    PlanItem,
    PlanTask,
    Preferences,
    ProceduralRule,
    Project,
    ProjectStatus,
    RoadmapSource,
    ScheduleEvent,
    SkillRating,
    SystemFlags,
    TopicGraph,
    TopicNode,
)

__all__ = [
    # memory.py
    "ActiveLearningPath",
    "ActivityLogEntry",
    "ApplicationStage",
    "DailyPlan",
    "DNAMemory",
    "EmploymentStatus",
    "JobApplication",
    "LearningLogEntry",
    "LearningPathEntry",
    "MemorySlice",
    "MindsetCalibration",
    "NodeAnchor",
    "NodeCheckpoint",
    "PlanItem",
    "PlanTask",
    "Preferences",
    "ProceduralRule",
    "Project",
    "ProjectStatus",
    "RoadmapSource",
    "ScheduleEvent",
    "SkillRating",
    "SystemFlags",
    "TopicGraph",
    "TopicNode",
    # agent_io.py
    "AgentTask",
    "AgentResult",
    "AgentTaskBatch",
    "AgentResultBatch",
    "DraftSuggestion",
    "TaskSource",
    "TaskPriority",
    "ResultStatus",
]