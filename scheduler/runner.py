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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Memory consolidation scheduler for the AI companion."
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run on a recurring weekly schedule using APScheduler (requires apscheduler).",
    )
    args = parser.parse_args()

    if args.schedule:
        _run_scheduled()
    else:
        # Single run.
        report = run_consolidation()
        sys.exit(0 if report else 1)


def _run_scheduled() -> None:
    """Run the consolidation job every Sunday at 2am using APScheduler."""
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
    scheduler.add_job(
        run_consolidation,
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="memory_consolidation",
        name="Weekly memory consolidation",
        replace_existing=True,
    )

    print("Memory consolidation scheduler started. Running every Sunday at 2am.")
    print("Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


if __name__ == "__main__":
    main()
