"""
_migrate_to_files.py — one-shot: Postgres memory -> files under data/.

Run ONCE, while the Python layer still exists. After this, `data/` IS the memory
and nothing needs PostgreSQL, SQLAlchemy, or a sidecar.

Layout produced:

    data/profile.yaml            structured facts, grouped by category
    data/memories.jsonl          DNA memory, one JSON object per line
    data/episodes/YYYY-MM.jsonl  episodic events, one JSON object per line
    data/schedule/events.jsonl   every schedule event
    data/schedule/days/YYYY-MM-DD.yaml   the 48-slot grid per date
    data/conversations/*.jsonl   closed session transcripts
    data/daylog.jsonl            day-log entries only (the record half)
    data/_migration_report.json  what was moved, so nothing is silent
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def jsonable(value):
    """Make a value safe for json/yaml: datetimes -> ISO, everything else as-is."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def dump(table: str) -> list[dict]:
    try:
        with engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(f"select * from {table}"))]
    except Exception as exc:
        print(f"  !! {table}: {exc}")
        return []


engine = create_engine(os.environ["DATABASE_URL"])
report: dict[str, int] = {}

for sub in ("episodes", "schedule/days", "conversations"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)

# --- profile_facts -> data/profile.yaml -----------------------------------
facts = dump("profile_facts")
grouped: dict[str, dict] = defaultdict(dict)
for row in facts:
    grouped[row["category"]][row["key"]] = {
        "value": jsonable(row["value"]),
        "source": row.get("source"),
        "updated_at": jsonable(row.get("updated_at")),
    }
(DATA / "profile.yaml").write_text(
    yaml.safe_dump(
        {
            "_note": "Structured facts about Nik. Categories mirror the old profile_facts table.",
            "facts": dict(grouped),
        },
        sort_keys=False,
        allow_unicode=True,
    )
)
report["profile.yaml"] = len(facts)
print(f"  profile.yaml          {len(facts)} facts in {len(grouped)} categories")

# --- dna_memory -> data/memories.jsonl ------------------------------------
memories = dump("dna_memory")
with (DATA / "memories.jsonl").open("w") as fh:
    for row in sorted(memories, key=lambda r: str(r.get("created_at") or "")):
        fh.write(json.dumps(jsonable(row), ensure_ascii=False) + "\n")
report["memories.jsonl"] = len(memories)
print(f"  memories.jsonl        {len(memories)} memories")

# --- episodic_events -> data/episodes/YYYY-MM.jsonl (+ daylog) ------------
episodes = dump("episodic_events")
by_month: dict[str, list[dict]] = defaultdict(list)
for row in episodes:
    occurred = str(row.get("occurred_at") or "")
    by_month[occurred[:7] or "unknown"].append(jsonable(row))

for month, items in by_month.items():
    with (DATA / "episodes" / f"{month}.jsonl").open("w") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

daylog = [e for e in episodes if e.get("event_type") == "day_log"]
with (DATA / "daylog.jsonl").open("w") as fh:
    for item in sorted(daylog, key=lambda r: str(r.get("occurred_at") or "")):
        fh.write(json.dumps(jsonable(item), ensure_ascii=False) + "\n")
report["episodes"] = len(episodes)
report["daylog.jsonl"] = len(daylog)
print(f"  episodes/             {len(episodes)} events across {len(by_month)} months")
print(f"  daylog.jsonl          {len(daylog)} day-log entries")

# --- schedule_events -> data/schedule/events.jsonl ------------------------
events = dump("schedule_events")
with (DATA / "schedule" / "events.jsonl").open("w") as fh:
    for row in events:
        fh.write(json.dumps(jsonable(row), ensure_ascii=False) + "\n")
report["schedule/events.jsonl"] = len(events)
print(f"  schedule/events.jsonl {len(events)} events")

# --- day_slots -> data/schedule/days/YYYY-MM-DD.yaml ----------------------
slots = dump("day_slots")
by_date: dict[str, list[dict]] = defaultdict(list)
for row in slots:
    by_date[str(row.get("date"))].append(jsonable(row))

for day, items in by_date.items():
    items.sort(key=lambda r: int(r.get("slot_index") or 0))
    (DATA / "schedule" / "days" / f"{day}.yaml").write_text(
        yaml.safe_dump({"date": day, "slots": items}, sort_keys=False, allow_unicode=True)
    )
report["schedule/days"] = len(by_date)
print(f"  schedule/days/        {len(by_date)} days, {len(slots)} slots")

# --- conversation_sessions -> data/conversations/*.jsonl ------------------
sessions = dump("conversation_sessions")
for row in sessions:
    sid = str(row.get("session_id") or row.get("id"))
    started = str(row.get("started_at") or "")[:10]
    path = DATA / "conversations" / f"{started}-{sid}.jsonl"
    with path.open("w") as fh:
        fh.write(json.dumps(jsonable(row), ensure_ascii=False) + "\n")
report["conversations"] = len(sessions)
print(f"  conversations/        {len(sessions)} sessions")

# --- agent_private_memory: checked, not migrated --------------------------
private = dump("agent_private_memory")
report["agent_private_memory"] = len(private)
print(f"  agent_private_memory  {len(private)} rows (not migrated - dead store)")

(DATA / "_migration_report.json").write_text(
    json.dumps(
        {
            "migrated_at": datetime.now().isoformat(),
            "source": "postgres memory tables",
            "counts": report,
        },
        indent=2,
    )
)
print("\nMigration complete. data/ is now the memory.")