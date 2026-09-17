"""
scheduler/daily_summary_job.py

The end-of-day narrative memory job — writes a daily_summary episodic event.

Design (see orchestrator/memory/daily_summary.py):
  - Code reads the day's events + profile facts.
  - The conversational LLM writes a compact 4-6 sentence narrative recap.
  - Code persists it as a `daily_summary` event with a streak marker.
  - If the LLM fails, a deterministic factual recap is written instead.

The last 3 daily summaries are injected into the DNA context document
(dna_context.py) and the harness situation facts so the reasoner carries
day-over-day continuity ("yesterday you ...").

Run manually:    python -m scheduler.daily_summary_job
Scheduled:       hourly-safe (idempotent per date) — wire a nightly cron / APScheduler
"""

from __future__ import annotations

from datetime import datetime, timezone

from dotenv import load_dotenv

from orchestrator.memory.daily_summary import build_daily_summary
from orchestrator.memory.store import MemoryManager

load_dotenv()


def run_daily_summary(
    memory_manager: MemoryManager | None = None,
    target_date: datetime | None = None,
    force: bool = False,
) -> dict:
    """
    Generate today's (or target_date's) daily summary.

    Idempotent per date — safe to run more than once a day; the first run
    writes the summary and later runs skip it (unless force=True).
    """
    print(f"[DailySummaryJob] Starting at {datetime.now(timezone.utc).isoformat()}")
    mm = memory_manager or MemoryManager()
    mm.ensure_schema()

    report = build_daily_summary(mm, target_date=target_date, force=force)

    print(f"[DailySummaryJob] status={report.get('status')} date={report.get('date')}")
    print(f"[DailySummaryJob] summary:\n{report.get('summary', '')}")
    return report


if __name__ == "__main__":
    report = run_daily_summary()
    import json
    print(json.dumps(report, default=str, indent=2))