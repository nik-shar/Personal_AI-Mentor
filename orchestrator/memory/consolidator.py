"""
orchestrator/memory/consolidator.py

MemoryConsolidator — the memory aging and compression pipeline.

NOT part of the LangGraph graph. Called only from the scheduler job
(scheduler/consolidation_job.py). Runs on a weekly schedule.

Three-stage pipeline:
  Stage 1 — Weekly consolidation
    Finds all hot-tier events older than CONSOLIDATION_AGE_DAYS that haven't
    been consolidated yet. Groups them by ISO week. For each week, asks the
    LLM to write a 2-3 sentence summary. Stores the summary as a new
    'weekly_summary' event. Marks originals as consolidated=True.

  Stage 2 — Monthly consolidation
    Finds all weekly_summary events older than ARCHIVE_AGE_DAYS. Groups by
    month. LLM produces a monthly abstract. Stores as 'monthly_summary'.
    Marks weekly_summaries as archived=True.

  Stage 3 — Fact extraction
    Reads monthly_summary events and asks the LLM to identify any stable
    facts worth promoting to profile_facts (e.g. "completed LangGraph course"
    → skills entry). Writes them via memory_manager.merge_profile_dict().
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

from orchestrator.config import DB_URL, PROFILE_KEY_MAP
from orchestrator.memory.store import MemoryManager, _get_embed_model

load_dotenv()


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Events older than this become candidates for weekly consolidation.
CONSOLIDATION_AGE_DAYS = 14

# Weekly summaries older than this get compressed into monthly summaries.
ARCHIVE_AGE_DAYS = 35

# Event types that carry no useful semantic content for summaries —
# skip them during weekly consolidation to keep summaries clean.
_SKIP_EVENT_TYPES = {"agent_run"}


from orchestrator.llm import get_reasoning_llm


def _get_llm():
    return get_reasoning_llm(temperature=0.1)


# ---------------------------------------------------------------------------
# MemoryConsolidator
# ---------------------------------------------------------------------------

class MemoryConsolidator:
    """
    Runs the three-stage memory aging pipeline against the Postgres store.

    Usage (from scheduler):
        consolidator = MemoryConsolidator(memory_manager)
        report = consolidator.run_full_pipeline()
    """

    def __init__(self, memory_manager: MemoryManager) -> None:
        self._mm = memory_manager
        self._llm = _get_llm()

    # -----------------------------------------------------------------------
    # Public: full pipeline entry point
    # -----------------------------------------------------------------------

    def run_full_pipeline(self) -> dict[str, Any]:
        """
        Run all three stages in order. Returns a summary report dict so the
        scheduler runner can log what happened.
        """
        print("[Consolidator] Starting full memory consolidation pipeline...")

        weekly_report  = self._stage1_weekly()
        monthly_report = self._stage2_monthly()
        facts_report   = self._stage3_extract_facts()

        report = {
            "weekly":  weekly_report,
            "monthly": monthly_report,
            "facts":   facts_report,
        }
        print(f"[Consolidator] Done: {report}")
        return report

    # -----------------------------------------------------------------------
    # Stage 1: Weekly consolidation
    # -----------------------------------------------------------------------

    def _stage1_weekly(self) -> dict[str, Any]:
        """
        Consolidate events older than CONSOLIDATION_AGE_DAYS into weekly
        summary events. Skips event types in _SKIP_EVENT_TYPES.
        Returns a report dict.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=CONSOLIDATION_AGE_DAYS)

        # Fetch raw events that need consolidation.
        with self._mm._session() as session:
            rows = session.execute(
                text("""
                    SELECT id, occurred_at, event_type, content, payload
                    FROM episodic_events
                    WHERE consolidated = false
                      AND archived = false
                      AND occurred_at < :cutoff
                    ORDER BY occurred_at ASC
                """),
                {"cutoff": cutoff},
            ).fetchall()

        if not rows:
            print("[Stage 1] No events to consolidate.")
            return {"weeks_processed": 0, "events_consolidated": 0}

        # Group by ISO week string e.g. "2026-W29".
        by_week: defaultdict[str, list] = defaultdict(list)
        for row in rows:
            if row.event_type in _SKIP_EVENT_TYPES:
                continue
            week_key = row.occurred_at.strftime("%Y-W%V")
            by_week[week_key].append(row)

        events_consolidated = 0
        weeks_processed = 0

        for week_key, week_rows in by_week.items():
            summary_text = self._summarise_week(week_key, week_rows)
            if not summary_text:
                continue

            # Write the weekly_summary event.
            # Parse the week start date for occurred_at.
            year, week_num = week_key.split("-W")
            week_start = datetime.strptime(f"{year}-W{week_num}-1", "%Y-W%W-%w")
            week_start = week_start.replace(tzinfo=timezone.utc)

            summary_event_id = self._mm.add_episodic_event(
                source_agent="consolidator",
                event_type="weekly_summary",
                content=summary_text,
                payload={
                    "week":               week_key,
                    "source_event_count": len(week_rows),
                },
                tags=["weekly_summary", week_key],
                importance=4,
                occurred_at=week_start,
            )
            # Embed the summary for future RAG retrieval.
            self._mm.embed_and_store(summary_event_id, summary_text)

            # Mark originals as consolidated.
            ids = [row.id for row in week_rows]
            with self._mm._session() as session:
                session.execute(
                    text(
                        "UPDATE episodic_events SET consolidated = true "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )

            events_consolidated += len(week_rows)
            weeks_processed += 1
            print(f"[Stage 1] Consolidated week {week_key}: {len(week_rows)} events.")

        return {
            "weeks_processed":    weeks_processed,
            "events_consolidated": events_consolidated,
        }

    def _summarise_week(self, week_key: str, rows: list) -> str:
        """Ask the LLM to summarise a week's events in 2-3 sentences."""
        events_block = "\n".join(
            f"- [{row.occurred_at.strftime('%Y-%m-%d')}] ({row.event_type}): {row.content[:200]}"
            for row in rows
        )
        prompt = (
            f"The following are activity log entries for the week of {week_key}.\n"
            f"Write a 2-3 sentence factual summary capturing: what was learned, "
            f"what was worked on, any notable achievements or setbacks.\n"
            f"Be specific. Include numbers (streak days, tasks completed) if present.\n\n"
            f"Events:\n{events_block}\n\nSummary:"
        )
        try:
            response = self._llm.invoke([("human", prompt)])
            return response.content.strip()
        except Exception as exc:
            print(f"[Stage 1] LLM summarisation failed for {week_key}: {exc}")
            return ""

    # -----------------------------------------------------------------------
    # Stage 2: Monthly consolidation
    # -----------------------------------------------------------------------

    def _stage2_monthly(self) -> dict[str, Any]:
        """
        Compress weekly_summary events older than ARCHIVE_AGE_DAYS into
        monthly_summary events. Archives the source weeklies.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AGE_DAYS)

        with self._mm._session() as session:
            rows = session.execute(
                text("""
                    SELECT id, occurred_at, content, payload
                    FROM episodic_events
                    WHERE event_type = 'weekly_summary'
                      AND archived = false
                      AND occurred_at < :cutoff
                    ORDER BY occurred_at ASC
                """),
                {"cutoff": cutoff},
            ).fetchall()

        if not rows:
            print("[Stage 2] No weekly summaries to archive.")
            return {"months_processed": 0, "weeklies_archived": 0}

        # Group by "YYYY-MM".
        by_month: defaultdict[str, list] = defaultdict(list)
        for row in rows:
            month_key = row.occurred_at.strftime("%Y-%m")
            by_month[month_key].append(row)

        months_processed = 0
        weeklies_archived = 0

        for month_key, month_rows in by_month.items():
            summary_text = self._summarise_month(month_key, month_rows)
            if not summary_text:
                continue

            year, month = month_key.split("-")
            month_start = datetime(int(year), int(month), 1, tzinfo=timezone.utc)

            monthly_event_id = self._mm.add_episodic_event(
                source_agent="consolidator",
                event_type="monthly_summary",
                content=summary_text,
                payload={
                    "month":                month_key,
                    "source_weekly_count":  len(month_rows),
                },
                tags=["monthly_summary", month_key],
                importance=5,
                occurred_at=month_start,
            )
            self._mm.embed_and_store(monthly_event_id, summary_text)

            # Archive the source weeklies.
            ids = [row.id for row in month_rows]
            with self._mm._session() as session:
                session.execute(
                    text(
                        "UPDATE episodic_events SET archived = true "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )

            months_processed += 1
            weeklies_archived += len(month_rows)
            print(f"[Stage 2] Archived month {month_key}: {len(month_rows)} weeklies.")

        return {
            "months_processed":  months_processed,
            "weeklies_archived": weeklies_archived,
        }

    def _summarise_month(self, month_key: str, rows: list) -> str:
        """Compress weekly summaries into a single monthly abstract."""
        weeklies_block = "\n\n".join(
            f"Week of {row.occurred_at.strftime('%Y-%m-%d')}:\n{row.content}"
            for row in rows
        )
        prompt = (
            f"The following are weekly summaries for {month_key}.\n"
            f"Write a single paragraph (4-6 sentences) that captures the most "
            f"important things that happened that month: key learnings, project "
            f"progress, job search activity, and notable achievements or struggles.\n\n"
            f"Weekly summaries:\n{weeklies_block}\n\nMonthly abstract:"
        )
        try:
            response = self._llm.invoke([("human", prompt)])
            return response.content.strip()
        except Exception as exc:
            print(f"[Stage 2] LLM summarisation failed for {month_key}: {exc}")
            return ""

    # -----------------------------------------------------------------------
    # Stage 3: Fact extraction → profile_facts
    # -----------------------------------------------------------------------

    def _stage3_extract_facts(self) -> dict[str, Any]:
        """
        Scan recent monthly_summary events for stable facts that should be
        promoted to profile_facts (e.g. completed courses, new skills).

        Only processes monthly summaries that haven't been processed for fact
        extraction yet (tracked via the payload flag 'facts_extracted').
        """
        with self._mm._session() as session:
            rows = session.execute(
                text("""
                    SELECT id, content, payload
                    FROM episodic_events
                    WHERE event_type = 'monthly_summary'
                      AND (payload->>'facts_extracted') IS DISTINCT FROM 'true'
                    ORDER BY occurred_at DESC
                    LIMIT 6
                """),
            ).fetchall()

        if not rows:
            print("[Stage 3] No monthly summaries to extract facts from.")
            return {"summaries_processed": 0, "facts_extracted": 0}

        total_facts = 0
        summaries_processed = 0

        for row in rows:
            facts = self._extract_facts_from_summary(row.content)
            if facts:
                self._mm.merge_profile_dict(facts, source="consolidator")
                total_facts += len(facts)
                print(f"[Stage 3] Extracted {len(facts)} facts from monthly summary {row.id}.")

            # Mark the monthly summary so we don't reprocess it.
            updated_payload = dict(row.payload or {})
            updated_payload["facts_extracted"] = "true"
            with self._mm._session() as session:
                session.execute(
                    text(
                        "UPDATE episodic_events SET payload = :p WHERE id = :id"
                    ),
                    {"p": updated_payload, "id": row.id},
                )
            summaries_processed += 1

        return {
            "summaries_processed": summaries_processed,
            "facts_extracted":     total_facts,
        }

    def _extract_facts_from_summary(self, summary: str) -> dict[str, Any]:
        """
        Ask the LLM to pull out any stable facts from a monthly summary that
        belong in profile_facts. Returns a flat dict matching PROFILE_KEY_MAP keys.

        Only extracts facts that map to known profile keys — anything else is
        ignored to avoid polluting the profile with noise.
        """
        known_keys = ", ".join(sorted(PROFILE_KEY_MAP.keys()))
        prompt = (
            "You are extracting stable facts from a monthly activity summary.\n"
            "Return ONLY facts that belong in these profile fields: "
            f"{known_keys}.\n"
            "Format as a JSON object with profile field names as keys.\n"
            "Only include fields where you have clear evidence from the summary.\n"
            "If nothing is promotable, return an empty JSON object {{}}.\n\n"
            f"Monthly summary:\n{summary}\n\nExtracted facts (JSON only):"
        )
        try:
            import json
            response = self._llm.invoke([("human", prompt)])
            raw = response.content.strip()
            # Strip markdown code fences if present.
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:-1])
            facts = json.loads(raw)
            if not isinstance(facts, dict):
                return {}
            # Only keep keys that are in PROFILE_KEY_MAP.
            return {k: v for k, v in facts.items() if k in PROFILE_KEY_MAP}
        except Exception as exc:
            print(f"[Stage 3] Fact extraction LLM call failed: {exc}")
            return {}
