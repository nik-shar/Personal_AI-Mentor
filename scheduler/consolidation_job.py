"""
scheduler/consolidation_job.py

The memory consolidation job — runs the three-stage aging pipeline.

Completely independent of the LangGraph graph and the orchestrator's
conversation loop. Imports only from orchestrator/memory/.

Run manually:        python -m scheduler
Run via cron:        0 2 * * 0   (2am every Sunday)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dotenv import load_dotenv

from orchestrator.memory.store import MemoryManager
from orchestrator.memory.consolidator import MemoryConsolidator

load_dotenv()


def run_consolidation() -> dict:
    """
    Connect to the DB and run the full three-stage consolidation pipeline.
    Returns the pipeline report dict for logging.
    """
    print(f"\n[ConsolidationJob] Starting at {datetime.now(timezone.utc).isoformat()}")

    memory_manager = MemoryManager()
    memory_manager.ensure_schema()

    consolidator = MemoryConsolidator(memory_manager)
    report = consolidator.run_full_pipeline()

    print(f"[ConsolidationJob] Completed. Report:")
    print(json.dumps(report, indent=2, default=str))
    return report
