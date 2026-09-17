---
created: 2026-08-22
tags:
  - schemas
  - memory
---

# 📐 Memory Schemas

> **Role:** The Pydantic models that define the shape of all memory data — DNAMemory, MemorySlice, and all sub-models.

---

## DNAMemory — Top-Level Persistent Object

The orchestrator's complete picture of Nikhil. Stored across `profile_facts` (structured) and `dna_memory` (organic), but conceptually one object.

```python
class DNAMemory(BaseModel):
    schema_version: str = "1.0"
    user_id: str = "nikhil"
    last_updated: datetime
    
    # Identity
    name: str
    education: Optional[str]
    location: Optional[str]
    long_term_goal: Optional[str]
    
    # Career
    employment_status: EmploymentStatus
    current_role: Optional[str]
    target_roles: list[str]
    target_locations: list[str]
    applications: list[JobApplication]
    
    # Skills & Projects
    skills: list[SkillRating]
    projects: list[Project]
    
    # Learning
    active_learning_path: Optional[ActiveLearningPath]
    learning_log: list[LearningLogEntry]
    learning_streak_days: int
    
    # Planning
    daily_plans: list[DailyPlan]
    schedule_events: list[ScheduleEvent]
    
    # Preferences & System
    preferences: Preferences
    flags: SystemFlags
    procedural_rules: list[ProceduralRule]
    mindset_calibration: MindsetCalibration
    energy_level: Optional[int]
    recent_activity: list[ActivityLogEntry]
    agent_private_memory: dict[str, dict]
```

## MemorySlice — Agent Context

The trimmed subset of memory sent to a sub-agent. Only the fields relevant to that agent/task_type are populated.

```python
class MemorySlice(BaseModel):
    agent_name: str
    task_type: str
    generated_at: datetime
    relevant_profile: dict[str, Any]   # Agent-specific fields
    recent_activity: list[ActivityLogEntry]
    private_memory: Optional[dict]
    constraints: dict[str, Any]         # max_length, avoid_topics, etc.
```

### Per-Agent Profile Fields

| Agent | Keys in `relevant_profile` |
|-------|---------------------------|
| `linkedin_writer` | `projects`, `employment_status`, `target_roles`, `last_linkedin_topic`, `preferences` |
| `goal_decomposer` | `preferences`, `topic_graphs` |
| `job_hunter` | `job_pipeline`, `application_history`, `skills`, `master_resume_path` |

## Sub-Models

| Model | Fields | Used By |
|-------|--------|---------|
| `SkillRating` | `name`, `rating_out_of_10`, `last_self_assessed`, `notes` | Skills tracking |
| `Project` | `name`, `description`, `status`, `tech_stack`, `repo_url`, `deployed_url` | Projects tracking |
| `JobApplication` | `company`, `role_title`, `stage`, `applied_date`, `job_url`, `notes` | Pipeline tracking |
| `DailyPlan` | `date`, `items: list[PlanItem]`, `notes` | Daily planning |
| `PlanItem` | `title`, `category`, `priority`, `duration_min`, `linked_goal`, `start_time` | Schedule blocks |
| `LearningLogEntry` | `date`, `topics: list[TopicEntry]`, `source`, `duration_min` | Learning log |
| `ActivityLogEntry` | `timestamp`, `source`, `summary`, `task_type`, `linked_memory_delta` | Recent activity |
| `TopicGraph` | `topic_id`, `title`, `version`, `nodes: dict[str, TopicNode]` | Learning roadmaps |
| `TopicNode` | `id`, `title`, `status`, `estimated_hours`, `prerequisites`, `resources`, `notes` | DAG nodes |
| `Preferences` | `content_tone`, `max_length`, `quiet_hours`, `mentor_personality` | System settings |
| `SystemFlags` | `onboarded`, `discovery_complete`, `first_contact_made` | State tracking |
| `ProceduralRule` | `trigger`, `action`, `active` | Custom behavior rules |
| `MindsetCalibration` | `accountability_level`, `coaching_emphasis`, `framing_that_works`, `framing_that_bounces` | Coaching adaptation |

## Enums

- `EmploymentStatus`: `employed`, `unemployed_job_searching`, `freelancing`, `studying`
- `ProjectStatus`: `planning`, `active`, `paused`, `done`, `abandoned`
- `ApplicationStage`: `wishlist`, `applied`, `screening`, `interviewing`, `offer`, `rejected`, `withdrawn`


---

> **Category:** 📐 Schemas · **Parent:** [[Agent IO Contract]]
