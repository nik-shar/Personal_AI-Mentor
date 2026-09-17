"""
scripts/migrations/migrate_drop_mentor_state.py

Phase 4 migration (dna_memory_redesign_v2.md §12): drop the legacy
`mentor_user_state` (23-column pre-computed snapshot) and
`mentor_meta_patterns` (hardcoded pattern detectors) tables.

Their replacements:
  - momentum math → orchestrator/cognition/metrics.py (computed on demand)
  - patterns      → dna_memory rows (source="data_derived" | "mentor_inferred")

Idempotent (DROP TABLE IF EXISTS). Preview first, then apply.

Usage (from project root):
    uv run python scripts/migrations/migrate_drop_mentor_state.py --dry-run   # preview
    uv run python scripts/migrations/migrate_drop_mentor_state.py --apply     # drop
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.memory.store import MemoryManager

LEGACY_TABLES = ("mentor_user_state", "mentor_meta_patterns")


def table_info(mm: MemoryManager) -> dict[str, int]:
    """Existing legacy tables → row count (absent tables are omitted)."""
    info: dict[str, int] = {}
    with mm.SessionLocal() as session:
        for table in LEGACY_TABLES:
            exists = session.execute(
                text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
            ).scalar()
            if exists:
                info[table] = session.execute(
                    text(f'SELECT COUNT(*) FROM "{table}"')
                ).scalar()
    return info


def main() -> int:
    apply = "--apply" in sys.argv
    mm = MemoryManager()

    info = table_info(mm)
    if not info:
        print("Legacy tables already absent — nothing to do.")
        return 0

    print("Legacy tables found:")
    for table, rows in info.items():
        print(f"  - {table}: {rows} row(s)")

    if not apply:
        print("\nDry run — re-run with --apply to drop these tables.")
        return 0

    with mm.SessionLocal() as session:
        for table in LEGACY_TABLES:
            session.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
        session.commit()

    remaining = table_info(mm)
    if remaining:
        print(f"\nERROR: tables still present: {list(remaining)}")
        return 1
    print("\nDropped. The math survives in code (cognition/metrics.py); the tables are gone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
