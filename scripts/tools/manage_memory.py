"""
scripts/tools/manage_memory.py

Manual memory management CLI — inspect, correct, prune everything the mentor
knows about you. Gives you direct control over the two knowledge stores that
drive the mentor:

  PROFILE FACTS  (profile_facts — typed, structured: goals, roles, skills, ...)
  DNA MEMORY     (dna_memory    — organic natural-language with confidence)

Plus job-pipeline / episodic-noise cleanup and session browsing.

Usage (from project root):

  # --- THE SIMPLE WAY: edit memory as one JSON file ---
  uv run python scripts/tools/manage_memory.py export > memory.json   # dump ALL memory
  # ... open memory.json in any editor, change what you want ...
  uv run python scripts/tools/manage_memory.py import memory.json --dry-run  # preview diff
  uv run python scripts/tools/manage_memory.py import memory.json      # push it back

  # --- quick single edits / housekeeping ---
  uv run python scripts/tools/manage_memory.py profile list | show <key> | set <key> <json> [--yes]
  uv run python scripts/tools/manage_memory.py dna list | confirm <id> | correct <id> <text>
  uv run python scripts/tools/manage_memory.py pipeline prune-stale [--days 14] [--apply]
  uv run python scripts/tools/manage_memory.py cleanup agent-run [--apply]
  uv run python scripts/tools/manage_memory.py status
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.config import PROFILE_KEY_MAP
from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager

# ---------------------------------------------------------------------------
# Small output helpers
# ---------------------------------------------------------------------------


def _short(s: Any, n: int = 80) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        print("  ".join((c or "").ljust(widths[i]) for i, c in enumerate(r)))

# ---------------------------------------------------------------------------
# Profile facts (profile_facts table — typed structured store)
# ---------------------------------------------------------------------------


def _all_profile_rows(mm: MemoryManager) -> list[dict[str, Any]]:
    from sqlalchemy import text

    with mm.SessionLocal() as session:
        rows = session.execute(
            text("SELECT category, key, value, source, updated_at FROM profile_facts ORDER BY category, key")
        ).fetchall()
    return [
        {"category": r[0], "key": r[1], "value": r[2], "source": r[3], "updated_at": r[4]}
        for r in rows
    ]


def _resolve_category(mm: MemoryManager, key: str, category_opt: str | None) -> str:
    """Category precedence: --category flag, existing row, PROFILE_KEY_MAP, 'system'."""
    if category_opt:
        return category_opt
    for row in _all_profile_rows(mm):
        if row["key"] == key:
            return row["category"]
    if key in PROFILE_KEY_MAP:
        return PROFILE_KEY_MAP[key][0]
    return "system"


def cmd_profile_list(args) -> int:
    rows = _all_profile_rows(args.mm)
    if not rows:
        print("(no profile facts stored)")
        return 0
    _print_table(
        [[r["key"], r["category"], _short(r["value"], 90), r["source"] or ""] for r in rows],
        ["KEY", "CATEGORY", "VALUE", "SOURCE"],
    )
    print(f"\n{len(rows)} profile fact(s). Use: manage_memory.py profile show <key>")
    return 0


def cmd_profile_show(args) -> int:
    matches = [r for r in _all_profile_rows(args.mm) if r["key"] == args.key]
    if not matches:
        print(f"No profile fact with key '{args.key}'. (Hint: check the exact spelling.)")
        return 1
    for r in matches:
        print(f"category : {r['category']}")
        print(f"source   : {r['source'] or 'n/a'}")
        print(f"updated  : {r['updated_at']}")
        print("value    :")
        print(json.dumps(r["value"], indent=2, ensure_ascii=False))
        print()
    return 0


def cmd_profile_set(args) -> int:
    # Accept valid JSON, else store the raw string as a scalar.
    try:
        value: Any = json.loads(args.json_value)
    except json.JSONDecodeError:
        value = args.json_value

    category = _resolve_category(args.mm, args.key, args.category)
    existing = [r for r in _all_profile_rows(args.mm) if r["key"] == args.key]

    print(f"category : {category}")
    if existing:
        print("BEFORE   :")
        print(json.dumps(existing[0]["value"], indent=2, ensure_ascii=False)[:400])
    else:
        print("BEFORE   : (no existing row)")
    print("AFTER    :")
    print(json.dumps(value, indent=2, ensure_ascii=False)[:400])

    if not args.yes:
        print("\nAdd --yes to apply this change.")
        return 0
    args.mm.set_profile_fact(category, args.key, value, source="manual_edit")
    print(f"\n✅ Updated profile_facts[{category}.{args.key}]")
    return 0


def cmd_profile_delete(args) -> int:
    existing = [r for r in _all_profile_rows(args.mm) if r["key"] == args.key]
    if not existing:
        print(f"No profile fact with key '{args.key}'.")
        return 1
    for r in existing:
        print(f"  - [{r['category']}] {r['key']} = {_short(r['value'], 100)}")
    if not args.yes:
        print("\nAdd --yes to delete these row(s).")
        return 0
    from sqlalchemy import text

    with args.mm.SessionLocal() as session:
        session.execute(text("DELETE FROM profile_facts WHERE key = :k"), {"k": args.key})
        session.commit()
    print(f"\n✅ Deleted {len(existing)} profile_facts row(s) for key '{args.key}'")
    return 0

# ---------------------------------------------------------------------------
# DNA memory (dna_memory table — organic natural-language store)
# ---------------------------------------------------------------------------


def _dna_row(m: Any) -> list[str]:
    return [
        str(m.id)[:8],
        m.memory_type,
        m.source.replace("_", " "),
        str(m.confidence),
        "✓" if m.user_confirmed else "-",
        _short(m.content, 85),
    ]


def cmd_dna_list(args) -> int:
    store = DNAMemoryStore()
    memories = store.list_memories(active_only=not args.all, limit=500)
    if not memories:
        print("(no DNA memories — they form as you chat; run_reflection writes after each turn)")
        return 0
    pending = [m for m in memories if not m.user_confirmed]
    _print_table(
        [_dna_row(m) for m in memories],
        ["ID", "TYPE", "SOURCE", "CONF", "OK?", "CONTENT"],
    )
    print(
        f"\n{len(memories)} memory/ies — {len(pending)} unconfirmed. "
        "Confirm or correct them with: dna confirm <id> / dna correct <id> <text>"
    )
    return 0


def cmd_dna_show(args) -> int:
    record = DNAMemoryStore().get_memory(args.mem_id)
    if record is None:
        print(f"No DNA memory with id '{args.mem_id}'.")
        return 1
    print(record.model_dump_json(indent=2))
    return 0


def cmd_dna_confirm(args) -> int:
    store = DNAMemoryStore()
    try:
        record = store.confirm_memory(args.mem_id, by_user=True)
    except KeyError:
        print(f"No DNA memory with id '{args.mem_id}'.")
        return 1
    print(
        f"✅ Confirmed (unlocked confidence ceiling to {record.confidence_ceiling:.2f}):\n"
        f"   {record.content}"
    )
    return 0


def cmd_dna_correct(args) -> int:
    store = DNAMemoryStore()
    try:
        record = store.revise_memory(
            args.mem_id,
            args.new_text,
            reason=args.reason or "manual correction",
            contradiction=True,
        )
    except KeyError:
        print(f"No DNA memory with id '{args.mem_id}'.")
        return 1
    print(f"✅ Corrected (old memory superseded, audit trail kept):\n   {record.content}")
    return 0


def cmd_dna_delete(args) -> int:
    store = DNAMemoryStore()
    record = store.get_memory(args.mem_id)
    if record is None:
        print(f"No DNA memory with id '{args.mem_id}'.")
        return 1
    print(f"Will deactivate (archived, not erased):\n   {record.content}")
    if not args.yes:
        print("\nAdd --yes to confirm.")
        return 0
    store.deactivate(args.mem_id)
    print(f"✅ Deactivated DNA memory {args.mem_id} (audit trail kept).")
    return 0
# ---------------------------------------------------------------------------
# Job application pipeline (profile_facts career.job_pipeline)
# ---------------------------------------------------------------------------

_STALE_PRONE_STAGES = ("wishlist", "applied", "referral_requested", "screening")


def _pipeline_rows(mm: MemoryManager) -> list[dict[str, Any]]:
    raw = mm.get_profile_fact("career", "job_pipeline") or []
    return [dict(a) for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []


def cmd_pipeline_list(args) -> int:
    apps = _pipeline_rows(args.mm)
    if not apps:
        print("(job pipeline is empty)")
        return 0
    rows = []
    for a in apps:
        updated = str(a.get("last_updated") or "?")[:10]
        rows.append([a.get("stage") or "?", _short(a.get("company"), 24), _short(a.get("title"), 32), updated])
    _print_table(rows, ["STAGE", "COMPANY", "ROLE", "UPDATED"])
    print(f"\n{len(apps)} pipeline row(s).")
    return 0


def cmd_pipeline_prune(args) -> int:
    from datetime import datetime, timedelta, timezone

    apps = _pipeline_rows(args.mm)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    def _age_days(a: dict) -> float:
        raw = a.get("last_updated")
        if not raw:
            return float("inf")
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(str(raw))).days
        except ValueError:
            return float("inf")

    stale = [
        a for a in apps
        if a.get("stage") in _STALE_PRONE_STAGES and _age_days(a) >= args.days
    ]
    if not stale:
        print(f"No stale entries (wishlist/applied/screening unreferenced for ≥{args.days} days).")
        return 0
    for a in stale:
        print(f"  - [{a.get('stage')}] {_short(a.get('company'), 24)} | {_short(a.get('title'), 40)} | updated {str(a.get('last_updated') or '?')[:10]}")
    print(f"\nWould remove {len(stale)} of {len(apps)} pipeline row(s).")
    if not args.apply:
        print("Dry run — add --apply to execute.")
        return 0
    kept = [a for a in apps if a not in stale]
    args.mm.set_profile_fact("career", "job_pipeline", kept, source="manual_prune")
    print(f"✅ Removed {len(stale)} stale pipeline row(s); {len(kept)} remain.")
    return 0


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


def cmd_cleanup_agent_run(args) -> int:
    from sqlalchemy import text

    with args.mm.SessionLocal() as session:
        count = session.execute(text("SELECT count(*) FROM episodic_events WHERE event_type = 'agent_run'")).scalar()
    if not count:
        print("No agent_run events to clean.")
        return 0
    print(f"{count} agent_run meta-events {('would be' if not args.apply else 'will be')} deleted — pure bookkeeping, zero semantic value.")
    if not args.apply:
        print("Dry run — add --apply to execute.")
        return 0
    with args.mm.SessionLocal() as session:
        session.execute(text("DELETE FROM episodic_events WHERE event_type = 'agent_run'"))
        session.commit()
    print(f"✅ Deleted {count} agent_run events.")
    return 0


def cmd_cleanup_empty_keys(args) -> int:
    from sqlalchemy import text

    with args.mm.SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT category, key FROM profile_facts "
                "WHERE jsonb_typeof(value) = 'null' "
                "OR (jsonb_typeof(value) = 'array' AND jsonb_array_length(value) = 0) "
                "OR value = '{}'::jsonb"
            )
        ).fetchall()
    flagged = [(r[0], r[1]) for r in rows]
    if not flagged:
        print("No empty profile rows to clean.")
        return 0
    for c, k in flagged:
        print(f"  - [{c}] {k}")
    print(f"\n{len(flagged)} empty row(s) {('would be' if not args.apply else 'will be')} deleted.")
    if not args.apply:
        print("Dry run — add --apply to execute.")
        return 0
    with args.mm.SessionLocal() as session:
        for c, k in flagged:
            session.execute(text("DELETE FROM profile_facts WHERE category = :c AND key = :k"), {"c": c, "k": k})
        session.commit()
    print(f"✅ Deleted {len(flagged)} empty profile rows.")
    return 0


def cmd_sessions_list(args) -> int:
    sessions = args.mm.get_recent_conversation_sessions(limit=args.limit)
    if not sessions:
        print("(no closed conversation sessions yet)")
        return 0
    rows = []
    for s in sessions:
        rows.append([
            str(s.get("id", ""))[:8],
            str(s.get("started_at") or "?")[:16],
            str(s.get("ended_at") or "?")[:16],
            str(s.get("turn_count", 0)),
            _short(s.get("summary") or "", 40),
        ])
    _print_table(rows, ["ID", "STARTED", "ENDED", "TURNS", "SUMMARY"])
    print(f"\n{len(sessions)} recent session(s).")
    return 0


def cmd_status(args) -> int:
    from sqlalchemy import text

    tables = ("profile_facts", "episodic_events", "dna_memory", "schedule_events", "agent_private_memory", "conversation_sessions")
    rows = []
    with args.mm.SessionLocal() as session:
        for t in tables:
            count = session.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            rows.append([t, str(count)])
    _print_table(rows, ["TABLE", "ROWS"])
    print("\nUse 'cleanup agent-run' to drop the episodic meta-noise.")
    return 0
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Export / import — the whole memory as one JSON file (edit it like a config)
#
#   uv run python scripts/tools/manage_memory.py export > memory.json      # dump all
#   # ... edit memory.json in any text editor ...
#   uv run python scripts/tools/manage_memory.py import memory.json         # push back
#   uv run python scripts/tools/manage_memory.py import memory.json --dry-run  # preview
# ---------------------------------------------------------------------------


def _export_payload(mm: MemoryManager, store: DNAMemoryStore) -> dict[str, Any]:
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
        "profile_facts": [
            {"category": r["category"], "key": r["key"], "value": r["value"], "source": r["source"]}
            for r in _all_profile_rows(mm)
        ],
        "dna_memory": [m.model_dump(mode="json") for m in store.list_memories(active_only=False, limit=1000)],
    }


def cmd_export(args) -> int:
    payload = _export_payload(args.mm, DNAMemoryStore())
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"✅ Exported {len(payload['profile_facts'])} profile facts + "
              f"{len(payload['dna_memory'])} DNA memories to {args.out}")
    else:
        print(text)
    return 0


_PROFILE_MD_LABELS = {
    "full_name": "Name",
    "employment_status": "Status",
    "current_role": "Current role",
    "target_roles": "Target roles",
    "target_locations": "Target locations",
    "short_term_goal": "Short-term goal",
    "long_term_goal": "Long-term goal",
    "learning_streak_days": "Learning streak",
    "energy_level": "Energy",
}


def cmd_export_md(args) -> int:
    """Write a human-readable Memory.md — a generated VIEW over the store.

    The markdown is a projection, NOT a second source of truth: edit the memory
    with `manage_memory.py import memory.json` and regenerate this file.
    """
    mm, store = args.mm, DNAMemoryStore()
    lines = [
        "---",
        "title: Mentor Memory",
        "type: memory_snapshot",
        "tags:",
        "  - memory",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        "---",
        "",
        "# 🧠 Mentor Memory — what I know about Nik",
        "",
        "> This file is a **generated human-readable view** of the memory store. It is not",
        "> the source of truth. Edit memory with `manage_memory.py export` → edit → `import`,",
        "> then regenerate with `manage_memory.py export-md`.",
        "",
    ]

    rows = _all_profile_rows(mm)
    facts = {r["key"]: r["value"] for r in rows}
    lines.append("## 📋 Structured facts")
    lines.append("")
    if rows:
        for key, label in _PROFILE_MD_LABELS.items():
            v = facts.get(key)
            if v not in (None, "", [], {}):
                lines.append(f"- **{label}:** {v}")
        other = [r for r in rows if r["key"] not in _PROFILE_MD_LABELS]
        if other:
            lines.append("")
            lines.append("<details><summary>All stored keys</summary>")
            lines.append("")
            for r in other:
                lines.append(f"- `{r['category']}.{r['key']}` = `{json.dumps(r['value'], ensure_ascii=False)}`")
            lines.append("")
            lines.append("</details>")
    else:
        lines.append("_(none yet — learned in conversation)_")
    lines.append("")

    memories = store.list_memories(active_only=True, limit=500)
    lines.append("## 🧬 DNA memories (organic)")
    lines.append("")
    if not memories:
        lines.append("_(none yet — the mentor writes one after each real conversation)_")
    for m in memories:
        conf = "high" if m.confidence >= 0.8 else ("med" if m.confidence >= 0.5 else "low")
        state = "confirmed" if m.user_confirmed else "unconfirmed"
        src = m.source.replace("_", " ")
        lines.append(f"- [{m.memory_type} · {src} · {conf} · {state}] {m.content}")
    lines.append("")
    lines.append("---")
    lines.append("_Regenerate with: `uv run python scripts/tools/manage_memory.py export-md --out <path>`_")

    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✅ Wrote human-readable memory view to {args.out} (markdown, not source of truth)")
    return 0


def cmd_import(args) -> int:
    from sqlalchemy import text

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    mm, store = args.mm, DNAMemoryStore()

    report: dict[str, list[str]] = {
        "profile_added": [], "profile_updated": [], "profile_deleted": [],
        "dna_created": [], "dna_updated": [], "dna_deactivated": [],
    }

    # --- profile facts: full sync by (category, key) ---
    existing = {(r["category"], r["key"]): r for r in _all_profile_rows(mm)}
    desired = {(r["category"], r["key"]): r for r in data.get("profile_facts", [])}
    for (cat, key) in sorted(set(existing) - set(desired)):
        report["profile_deleted"].append(f"{cat}.{key}")
    for (cat, key), row in desired.items():
        if (cat, key) in existing:
            if existing[(cat, key)]["value"] != row["value"]:
                report["profile_updated"].append(f"{cat}.{key}")
        else:
            report["profile_added"].append(f"{cat}.{key}")

    # --- dna memory: create new, update in place, deactivate missing ---
    existing_dna = {m.id: m for m in store.list_memories(active_only=False, limit=1000)}
    seen_ids: set[str] = set()
    for row in data.get("dna_memory", []):
        mid = row.get("id")
        if mid and mid in existing_dna:
            seen_ids.add(mid)
            rec = existing_dna[mid]
            if (
                rec.content != row.get("content")
                or rec.confidence != row.get("confidence")
                or rec.user_confirmed != row.get("user_confirmed", rec.user_confirmed)
            ):
                report["dna_updated"].append(mid[:8])
        else:
            report["dna_created"].append((row.get("content") or "")[:60])
    for mid, rec in existing_dna.items():
        if rec.active and mid not in seen_ids:
            report["dna_deactivated"].append(mid[:8])

    # --- summary ---
    print("Import plan:")
    for k, v in report.items():
        if v:
            print(f"  {k:<18} {len(v)}")
            for item in v[:8]:
                print(f"      - {item}")
            if len(v) > 8:
                print(f"      ... and {len(v) - 8} more")

    if args.dry_run:
        print("\nDry run — nothing changed. Re-run without --dry-run to apply.")
        return 0

    # --- apply profile facts ---
    for (cat, key) in set(existing) - set(desired):
        with mm.SessionLocal() as session:
            session.execute(text("DELETE FROM profile_facts WHERE category = :c AND key = :k"), {"c": cat, "k": key})
            session.commit()
    for (cat, key), row in desired.items():
        mm.set_profile_fact(cat, key, row["value"], source=row.get("source") or "manual_import")

    # --- apply dna memory ---
    for row in data.get("dna_memory", []):
        mid = row.get("id")
        if mid and mid in existing_dna:
            rec = existing_dna[mid]
            if (
                rec.content != row.get("content")
                or rec.confidence != row.get("confidence")
                or rec.user_confirmed != row.get("user_confirmed", rec.user_confirmed)
            ):
                with store.SessionLocal() as session:
                    session.execute(
                        text(
                            "UPDATE dna_memory SET content = :c, confidence = :conf, "
                            "user_confirmed = :uc, embedding = :emb WHERE id = :id"
                        ),
                        {
                            "c": (row.get("content") or "").strip(),
                            "conf": float(row.get("confidence") or rec.confidence),
                            "uc": bool(row.get("user_confirmed", rec.user_confirmed)),
                            "emb": store._embed(row.get("content") or rec.content),
                            "id": mid,
                        },
                    )
                    session.commit()
        else:
            store.create_memory(
                content=(row.get("content") or "").strip(),
                memory_type=row.get("memory_type") or "fact",
                source=row.get("source") or "user_stated",
                tags=row.get("tags") or [],
                confidence=row.get("confidence"),
                due_at=None,
            )
    for mid, rec in existing_dna.items():
        if rec.active and mid not in seen_ids:
            store.deactivate(mid)

    print("\n✅ Import applied.")
    return 0


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manage_memory", description="Manual control over the AI mentor's memory.")
    sub = parser.add_subparsers(dest="command", required=True)

    # profile
    p_file = sub.add_parser("profile", help="profile_facts (typed structured store)")
    p_sub = p_file.add_subparsers(dest="action", required=True)
    p_sub.add_parser("list", help="list all profile facts")
    show = p_sub.add_parser("show", help="show one fact")
    show.add_argument("key")
    setp = p_sub.add_parser("set", help="set a fact to a JSON value")
    setp.add_argument("key")
    setp.add_argument("json_value")
    setp.add_argument("--category", default=None, help="explicit category (else inferred)")
    setp.add_argument("--yes", action="store_true", help="apply without asking")
    delp = p_sub.add_parser("delete", help="delete a fact by key")
    delp.add_argument("key")
    delp.add_argument("--yes", action="store_true", help="apply without asking")

    # dna
    p_dna = sub.add_parser("dna", help="dna_memory (organic natural-language store)")
    d_sub = p_dna.add_subparsers(dest="action", required=True)
    dl = d_sub.add_parser("list", help="list memories")
    dl.add_argument("--all", action="store_true", help="include inactive/archived")
    dshow = d_sub.add_parser("show", help="show one memory")
    dshow.add_argument("mem_id")
    dconf = d_sub.add_parser("confirm", help="mark a memory as user-confirmed")
    dconf.add_argument("mem_id")
    dcorr = d_sub.add_parser("correct", help="supersede a memory with corrected text")
    dcorr.add_argument("mem_id")
    dcorr.add_argument("new_text")
    dcorr.add_argument("--reason", default=None)
    ddel = d_sub.add_parser("delete", help="deactivate (archive) a memory")
    ddel.add_argument("mem_id")
    ddel.add_argument("--yes", action="store_true", help="apply without asking")

    # pipeline
    p_pipe = sub.add_parser("pipeline", help="job application pipeline")
    pipe_sub = p_pipe.add_subparsers(dest="action", required=True)
    pipe_sub.add_parser("list", help="list pipeline rows")
    prune = pipe_sub.add_parser("prune-stale", help="remove stale wishlist/applied/screening rows")
    prune.add_argument("--days", type=int, default=14, help="age threshold in days")
    prune.add_argument("--apply", action="store_true", help="execute (default is dry-run)")

    # cleanup
    p_clean = sub.add_parser("cleanup", help="housekeeping")
    clean_sub = p_clean.add_subparsers(dest="action", required=True)
    ar = clean_sub.add_parser("agent-run", help="delete episodic agent_run meta-events")
    ar.add_argument("--apply", action="store_true")
    ek = clean_sub.add_parser("empty-keys", help="delete profile_facts rows with empty [] {} null values")
    ek.add_argument("--apply", action="store_true")

    # sessions / status
    p_sess = sub.add_parser("sessions", help="closed conversation sessions")
    s_sub = p_sess.add_subparsers(dest="action", required=True)
    sl = s_sub.add_parser("list", help="list recent sessions")
    sl.add_argument("--limit", type=int, default=5)
    sub.add_parser("status", help="row counts per memory table")

    # export / import — the whole memory as ONE editable JSON file
    exp = sub.add_parser("export", help="dump ALL memory (profile + DNA) to JSON")
    exp.add_argument("--out", default=None, help="write to a file (default: stdout)")

    imp = sub.add_parser("import", help="apply the file back (full sync: adds/updates/deletes)")
    imp.add_argument("file", help="path to the memory.json you edited")
    imp.add_argument("--dry-run", action="store_true", help="preview the diff without changing anything")

    mdl = sub.add_parser("export-md", help="write a human-readable Memory.md view (NOT source of truth)")
    mdl.add_argument("--out", default="data/memory.md", help="output path (default: data/memory.md)")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.mm = MemoryManager()

    try:
        if args.command == "profile":
            return {"list": cmd_profile_list, "show": cmd_profile_show, "set": cmd_profile_set, "delete": cmd_profile_delete}[args.action](args)
        if args.command == "dna":
            return {"list": cmd_dna_list, "show": cmd_dna_show, "confirm": cmd_dna_confirm, "correct": cmd_dna_correct, "delete": cmd_dna_delete}[args.action](args)
        if args.command == "pipeline":
            return {"list": cmd_pipeline_list, "prune-stale": cmd_pipeline_prune}[args.action](args)
        if args.command == "cleanup":
            return {"agent-run": cmd_cleanup_agent_run, "empty-keys": cmd_cleanup_empty_keys}[args.action](args)
        if args.command == "sessions":
            return cmd_sessions_list(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "export":
            return cmd_export(args)
        if args.command == "import":
            return cmd_import(args)
        if args.command == "export-md":
            return cmd_export_md(args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())