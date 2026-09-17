"""
agents/Job_Hunter/__init__.py

Public API for Job_Hunter — the job-application agent: JD-tailored resume
drafts (YAML → fixed LaTeX template → PDF) plus application pipeline tracking.
"""

from agents.Job_Hunter.job_hunter import (
    STALE_APPLICATION_DAYS,
    TERMINAL_STAGES,
    _coerce_stage,
    _days_since,
    _find_application,
    run_job_hunter,
)

# Public aliases so the API layer (api/main.py) can reuse the agent's
# matching/coercion semantics without reaching into private members.
coerce_stage = _coerce_stage
days_since = _days_since
find_application = _find_application

__all__ = [
    "STALE_APPLICATION_DAYS",
    "TERMINAL_STAGES",
    "coerce_stage",
    "days_since",
    "find_application",
    "run_job_hunter",
]
