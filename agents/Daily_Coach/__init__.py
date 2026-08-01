"""
agents/Daily_Coach/__init__.py

Public API for Daily_Coach — the merged agent that handles both daily planning
and learning progress logging.

The registry imports these two functions by the same names as before, so
orchestrator/registry.py needs only its import paths updated, nothing else.
"""

from agents.Daily_Coach.daily_coach import run_daily_planner, run_learning_monitor

__all__ = ["run_daily_planner", "run_learning_monitor"]
