"""
scheduler/runner.py

CLI entry point for the scheduler.

Usage:
    python -m scheduler                  # run consolidation once now
    python -m scheduler --weekly         # run only weekly stage
    python -m scheduler --schedule       # run on a recurring weekly cron

For production use, prefer a cron job:
    0 2 * * 0   uv run python -m scheduler
"""

from __future__ import annotations

import argparse
import sys

from scheduler.consolidation_job import run_consolidation
from scheduler.daily_summary_job import run_daily_summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Memory consolidation scheduler for the AI companion."
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run recurring jobs with APScheduler (nightly daily-summary + weekly consolidation).",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run only the end-of-day summary job once (idempotent per date).",
    )
    parser.add_argument(
        "--force-daily",
        action="store_true",
        help="Force a rewrite of today's daily summary even if one exists.",
    )
    args = parser.parse_args()

    if args.daily:
        report = run_daily_summary(force=args.force_daily)
        sys.exit(0 if report else 1)
    elif args.schedule:
        _run_scheduled()
    else:
        # Single run.
        report = run_consolidation()
        sys.exit(0 if report else 1)


def _run_scheduled() -> None:
    """Run the daily-summary job nightly + consolidation weekly using APScheduler."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print(
            "APScheduler not installed. Install with: pip install apscheduler\n"
            "Or run without --schedule for a one-shot execution."
        )
        sys.exit(1)

    scheduler = BlockingScheduler()
    # End-of-day narrative memory: 11pm nightly (idempotent per date).
    scheduler.add_job(
        run_daily_summary,
        trigger=CronTrigger(hour=23, minute=0),
        id="daily_summary",
        name="End-of-day narrative memory summary",
        replace_existing=True,
    )
    # Weekly consolidation every Sunday at 2am.
    scheduler.add_job(
        run_consolidation,
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="memory_consolidation",
        name="Weekly memory consolidation",
        replace_existing=True,
    )

    print("AI mentor scheduler started.")
    print("  - daily_summary : every night at 23:00 (idempotent per date)")
    print("  - consolidation : every Sunday at 02:00")
    print("Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


if __name__ == "__main__":
    main()
