"""
scheduler/consolidation_job.py

The memory consolidation job — runs the three-stage aging pipeline plus the
weekly DNA memory passes: decay (dna_memory_redesign_v2 §4.4) and data-derived
pattern observations (§13.5).

Completely independent of the LangGraph graph and the orchestrator's
conversation loop. Imports only from orchestrator/memory/ and
orchestrator/cognition/.

Run manually:        python -m scheduler
Run via cron:        0 2 * * 0   (2am every Sunday)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from dotenv import load_dotenv

from orchestrator.cognition.observations import derive_weekly_observations
from orchestrator.memory.consolidator import MemoryConsolidator
from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager

load_dotenv()


def run_consolidation() -> dict:
    """
    Connect to the DB and run the full three-stage consolidation pipeline,
    then the weekly DNA passes (decay §4.4, pattern observations §13.5).
    Returns the pipeline report dict for logging.
    """
    print(f"\n[ConsolidationJob] Starting at {datetime.now(UTC).isoformat()}")

    memory_manager = MemoryManager()
    memory_manager.ensure_schema()

    # The report ACCUMULATES: the prune result, then the pipeline's own keys,
    # then the DNA passes below. It must be initialised before the first write.
    # It previously was not — `report` was first referenced here and only
    # assigned after, so this job raised NameError twice (once in the `try`, once
    # in its `except`) and the weekly consolidation could never complete.
    report: dict = {}

    # Rolling day-grid window: raw 48-slot rows older than the window are
    # pruned — their story lives on in daily_summary events + DNA memory.
    try:
        report["day_slots_pruned"] = memory_manager.prune_old_day_slots(window_days=21)
    except Exception as exc:
        print(f"[ConsolidationJob] day-slot pruning failed: {exc}")
        report["day_slots_pruned"] = {"error": str(exc)}

    consolidator = MemoryConsolidator(memory_manager)
    # Merge rather than replace, so the prune result above survives.
    report.update(consolidator.run_full_pipeline())

    # DNA memory weekly passes — decay (§4.4) then pattern observations
    # (§13.5). A failure in either must never sink the consolidation report.
    dna_store: DNAMemoryStore | None = None
    try:
        dna_store = DNAMemoryStore()
        dna_store.ensure_schema()
        report["dna_decay"] = dna_store.decay_stale()
    except Exception as exc:
        print(f"[ConsolidationJob] DNA decay failed: {exc}")
        report["dna_decay"] = {"error": str(exc)}

    try:
        if dna_store is None:
            raise RuntimeError("DNAMemoryStore unavailable (see dna_decay error)")
        report["dna_observations"] = derive_weekly_observations(memory_manager, dna_store)
    except Exception as exc:
        print(f"[ConsolidationJob] DNA observation derivation failed: {exc}")
        report["dna_observations"] = {"error": str(exc)}

    print("[ConsolidationJob] Completed. Report:")
    print(json.dumps(report, indent=2, default=str))
    return report
