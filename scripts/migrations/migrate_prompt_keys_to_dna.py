"""
scripts/migrations/migrate_prompt_keys_to_dna.py

Phase 5 migration (dna_memory_redesign_v2.md §12): move the prompt-only
profile keys — bio_summary, working_habits, mindset_notes — out of
profile_facts and into DNA memory as natural-language memories.

These keys were always consumed as prompt text (§2.3); after migration the
context_builder supplies agents with an agent-scoped [RELEVANT MEMORIES]
retrieval block instead of structured key reads.

Dedup-aware: every item goes through upsert_with_checks (§6), so content
already present in the store is confirmed/refined, not duplicated.
Idempotent; preview first, then apply.

Usage (from project root):
    uv run python scripts/migrations/migrate_prompt_keys_to_dna.py --dry-run   # preview
    uv run python scripts/migrations/migrate_prompt_keys_to_dna.py --apply     # migrate + delete old rows
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager

PROMPT_ONLY_KEYS = ("bio_summary", "working_habits", "mindset_notes")


def extract_prompt_key_memories(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Pure conversion: profile_facts values → DNA memory write specs.

    The keys were user-authored (bio / onboarding extraction), so they map to
    source="seeded" (§4.1: 0.95 confidence, user_confirmed). bio_summary is
    identity ("fact"); habits/mindset are working style ("preference").
    """
    memories: list[dict[str, Any]] = []

    bio = facts.get("bio_summary")
    if isinstance(bio, str) and bio.strip():
        memories.append({
            "content": bio.strip(),
            "memory_type": "fact",
            "source": "seeded",
            "tags": ["profile_migration", "bio_summary"],
        })

    for key in ("working_habits", "mindset_notes"):
        items = facts.get(key) or []
        if isinstance(items, str):
            items = [items]
        for item in items:
            text = str(item).strip().rstrip(".")
            if text:
                memories.append({
                    "content": f"Nik's {key.replace('_', ' ')}: {text}.",
                    "memory_type": "preference",
                    "source": "seeded",
                    "tags": ["profile_migration", key],
                })

    return memories


def run_migration(
    mm: MemoryManager,
    store: DNAMemoryStore,
    apply: bool = False,
    compare_fn: Callable[..., str] | None = None,
    merge_fn: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Dry-run by default; with apply=True, upsert all items (dedup-aware,
    §6) and delete the legacy profile_facts rows. compare_fn/merge_fn are
    injectable for tests."""
    facts = mm.load_profile_facts(set(PROMPT_ONLY_KEYS))
    memories = extract_prompt_key_memories(facts)

    report: dict[str, Any] = {
        "applied": apply,
        "facts_found": {
            k: (v if isinstance(v, str) else f"{len(v)} item(s)")
            for k, v in facts.items()
            if k in PROMPT_ONLY_KEYS
        },
        "planned": [m["content"] for m in memories],
        "created": [],
        "matched_existing": [],
        "errors": [],
        "profile_facts_deleted": False,
    }
    if not memories:
        report["note"] = "nothing to migrate — the three keys are absent from profile_facts"
        return report
    if not apply:
        return report

    before_ids = {m.id for m in store.list_memories(active_only=False, limit=1000)}
    for spec in memories:
        try:
            record = store.upsert_with_checks(
                spec["content"],
                compare_fn=compare_fn,
                merge_fn=merge_fn,
                memory_type=spec["memory_type"],
                source=spec["source"],
                tags=spec["tags"],
            )
            bucket = report["matched_existing"] if record.id in before_ids else report["created"]
            bucket.append(spec["content"][:60])
        except Exception as exc:
            report["errors"].append(f"{spec['content'][:50]}: {exc}")

    with mm.SessionLocal() as session:
        session.execute(text(
            "DELETE FROM profile_facts "
            "WHERE key IN ('bio_summary', 'working_habits', 'mindset_notes')"
        ))
        session.commit()
    report["profile_facts_deleted"] = True
    return report


def main() -> int:
    apply = "--apply" in sys.argv
    mm = MemoryManager()
    store = DNAMemoryStore()
    store.ensure_schema()

    report = run_migration(mm, store, apply=apply)
    print(f"\nMode: {'APPLY' if apply else 'DRY RUN'}")
    print(f"Facts found: {report['facts_found'] or 'none'}")
    print(f"\nPlanned memories ({len(report['planned'])}):")
    for content in report["planned"]:
        print(f"  - {content[:90]}")
    if report.get("note"):
        print(f"\n{report['note']}")
    if apply:
        print(
            f"\nCreated: {len(report['created'])}, "
            f"matched existing: {len(report['matched_existing'])}, "
            f"errors: {len(report['errors'])}"
        )
        for err in report["errors"]:
            print(f"  ERROR: {err}")
        print(f"profile_facts rows deleted: {report['profile_facts_deleted']}")
    else:
        print("\nDry run — re-run with --apply to migrate and delete the old rows.")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
