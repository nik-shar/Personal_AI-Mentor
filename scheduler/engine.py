"""
scheduler/engine.py

Background scheduler engine managing dynamic AI self-scheduling wake-up jobs.
Uses APScheduler BackgroundScheduler to trigger autonomous mentor execution turns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("scheduler_engine")

_SCHEDULER_INSTANCE: Optional[SchedulerEngine] = None


class SchedulerEngine:
    """
    Singleton background scheduler engine for personal AI mentor self-scheduling.
    """

    def __init__(self, callback: Optional[Callable[[str, dict[str, Any]], None]] = None) -> None:
        self.callback = callback
        self._scheduler = None
        self._init_scheduler()

    def _init_scheduler(self) -> None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._scheduler = BackgroundScheduler(daemon=True)
            self._scheduler.start()
            logger.info("[SchedulerEngine] APScheduler BackgroundScheduler started successfully.")
        except Exception as exc:
            logger.warning(f"[SchedulerEngine] Failed to initialize APScheduler: {exc}")
            self._scheduler = None

    def start(self) -> None:
        if self._scheduler and not self._scheduler.running:
            self._scheduler.start()

    def schedule_wake_up(
        self,
        minutes_from_now: int,
        reason: str = "Autonomous check-in",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Schedule a one-off autonomous wake-up run `minutes_from_now` minutes into the future.
        Returns the job ID string if scheduled, or None if scheduler unavailable.
        """
        if not self._scheduler:
            logger.warning("[SchedulerEngine] Cannot schedule wake-up: APScheduler not running.")
            return None

        if minutes_from_now <= 0:
            minutes_from_now = 5  # minimum 5 minute buffer for immediate wake up

        run_time = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
        job_id = f"autowake_{int(run_time.timestamp())}"

        try:
            # Cancel any existing auto-wake job so we maintain a single dynamic wake-up trigger
            self.cancel_existing_autowakes()

            self._scheduler.add_job(
                func=self._trigger_wake_up,
                trigger="date",
                run_date=run_time,
                id=job_id,
                name=f"Mentor Auto-Wake: {reason}",
                args=[reason, metadata or {}],
                replace_existing=True,
            )
            print(f"⏰ [Self-Scheduler] Scheduled next autonomous wake-up in {minutes_from_now}m ({run_time.strftime('%H:%M UTC')}) — Reason: '{reason}'")
            return job_id
        except Exception as exc:
            print(f"[SchedulerEngine] Error scheduling wake-up job: {exc}")
            return None

    def cancel_existing_autowakes(self) -> None:
        """Cancel existing pending autowake jobs."""
        if not self._scheduler:
            return
        try:
            for job in self._scheduler.get_jobs():
                if job.id.startswith("autowake_"):
                    self._scheduler.remove_job(job.id)
        except Exception:
            pass

    def get_upcoming_jobs(self) -> list[dict[str, Any]]:
        """Return list of pending scheduled wake-up jobs."""
        if not self._scheduler:
            return []
        out = []
        for job in self._scheduler.get_jobs():
            out.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        return out

    def _trigger_wake_up(self, reason: str, metadata: dict[str, Any]) -> None:
        """Callback invoked when the wake-up timer fires."""
        print(f"\n🔔 [Autonomous Wake-Up Triggered] Reason: '{reason}'")
        if self.callback:
            try:
                self.callback(reason, metadata)
            except Exception as exc:
                print(f"[SchedulerEngine] Callback execution error: {exc}")
        else:
            # Fallback: invoke orchestrator turn
            try:
                from integrations.messenger import send_proactive_notification
                from orchestrator.memory.store import get_memory_manager
                from orchestrator.runner import OrchestratorRunner

                mm = get_memory_manager()
                runner = OrchestratorRunner(mm)
                state = runner.create_state(session_id="autonomous_scheduler")
                prompt = f"[AUTONOMOUS WAKE-UP CHECK-IN] Trigger Reason: {reason}"
                res_state = runner.run_turn(state, prompt)
                output_text = res_state.get("response_text", "")
                print(f"🤖 [Autonomous Mentor Output]:\n{output_text}")

                # Deliver notification to external channels (Slack / Telegram / Webhook)
                send_proactive_notification(output_text, title=f"Proactive Check-In: {reason}")
            except Exception as exc:
                print(f"[SchedulerEngine] Autonomous turn execution error: {exc}")


def get_scheduler_engine() -> SchedulerEngine:
    """Return shared singleton SchedulerEngine instance."""
    global _SCHEDULER_INSTANCE
    if _SCHEDULER_INSTANCE is None:
        _SCHEDULER_INSTANCE = SchedulerEngine()
    return _SCHEDULER_INSTANCE
