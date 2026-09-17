"""
scripts/migrations/migrate_roadmaps_to_manifests.py

Move the learning roadmaps from the personal Obsidian vault into the in-repo
curriculum store, converting each one to the manifest format.

Before (legacy — title-named folder, structure only in note frontmatter):
    <vault>/Learning/Topics/<Graph Title>/
        <Graph Title> Roadmap.md      index
        <Node Title>.md               one note per node

After (the roadmap store — stable id folder, authoritative manifest):
    <repo>/learning/topics/<graph_id>/
        roadmap.yaml                  identity + structure (the manifest)
        <Node Title>.md               notes with the three ownership zones
        <Graph Title>.md              regenerated index

What this preserves:
  - node ids unchanged (`tn_sql_basics` stays `tn_sql_basics`), so prerequisite
    edges keep resolving and nothing has to be re-learned
  - prerequisites, status, day, estimated_hours, resources
  - the "What to Cover" text, recovered from the note BODY. The old reader took
    `TopicNode.notes` from the frontmatter, where the writer never put it, so
    every reload silently returned an empty string. This is where that data is
    recovered rather than lost.
  - the learner's own "📝 My Notes" section, carried into the new note as zone 3

What it does NOT do:
  - it never deletes or edits the legacy vault. It copies and converts, so the
    old folder is still there as a fallback.
  - it does not invent a time budget. The legacy format never stored
    `target_days`/`hours_per_day`, so `hours_per_day` is left unset and the
    actual hours-per-day is REPORTED instead (the live "4-Day" roadmap holds
    22 hours of nodes — visible in the dry run, not silently "fixed").

Dry-run by default.

Usage:
    uv run python scripts/migrations/migrate_roadmaps_to_manifests.py
    uv run python scripts/migrations/migrate_roadmaps_to_manifests.py --apply
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.config import (
    MENTOR_CURRICULUM_PATH,
    OBSIDIAN_TOPIC_FOLDER,
    OBSIDIAN_VAULT_PATH,
)
from orchestrator.memory.roadmap import (
    MANIFEST_NAME,
    parse_frontmatter,
    roadmap_dir,
    verify_anchors,
    write_roadmap,
)
from schemas.memory import RoadmapSource, TopicGraph, TopicNode

H_COVER = "## 🎯 What to Cover"
H_RESOURCES = "## 📚 Resources"
H_MY_NOTES = "## 📝 My Notes"
H_DEEPENED = "## 🏫 Deepened in session"


def _section_bounds(body: str, heading: str) -> tuple[int, int] | None:
    """(content_start, content_end) for a level-2 section, or None."""
    start = body.find(heading)
    if start == -1:
        return None
    content_start = start + len(heading)
    nxt = body.find("\n## ", content_start)
    return content_start, (nxt if nxt != -1 else len(body))


def split_legacy_body(body: str) -> tuple[str, str, str]:
    """Split a legacy note body into (what_to_cover, generated, learner_notes).

    The legacy writer placed the short "What to Cover" summary directly above the
    tutorial it had generated, and that tutorial usually opens with its own H1 and
    a metadata blockquote. Splitting the cover at the next `## ` heading is
    therefore wrong — it swallows the entire tutorial into the summary, which is
    exactly what the first migration run did. The cover ends at the first heading
    of ANY level; everything from there on is generated content.
    """
    cover_bounds = _section_bounds(body, H_COVER)
    if cover_bounds:
        raw_cover = body[cover_bounds[0]:cover_bounds[1]]
        match = re.search(r"^#{1,6}\s", raw_cover, flags=re.MULTILINE)
        cover = (raw_cover[:match.start()] if match else raw_cover).strip()
        generated_start = cover_bounds[0] + (match.start() if match else len(raw_cover))
    else:
        cover = ""
        generated_start = 0
    resources_bounds = _section_bounds(body, H_RESOURCES)
    notes_bounds = _section_bounds(body, H_MY_NOTES)

    if resources_bounds:
        generated = body[generated_start:resources_bounds[0] - len("\n## 📚 Resources")]
    elif notes_bounds:
        generated = body[generated_start:notes_bounds[0] - len("\n## 📝 My Notes")]
    else:
        generated = body[generated_start:]

    learner_notes = body[notes_bounds[0]:notes_bounds[1]].strip() if notes_bounds else ""
    # Drop the scaffolding placeholder comment — it is the writer's, not his.
    if learner_notes.startswith("<!--") and learner_notes.count("<!--") == 1:
        learner_notes = ""

    return cover, generated.strip(), learner_notes


def read_legacy(source_root: Path) -> tuple[list[TopicGraph], dict[str, dict[str, str]], dict[str, dict[str, str]], list[str]]:
    """Read the legacy vault into (graphs, contents, tails, warnings).

    `contents` maps graph_id → {node_id: generated markdown}; `tails` maps
    graph_id → {node_id: zone-2+3 text} so a learner's own notes survive the
    conversion instead of being reset to the default stubs.
    """
    graphs: list[TopicGraph] = []
    contents: dict[str, dict[str, str]] = {}
    tails: dict[str, dict[str, str]] = {}
    warnings: list[str] = []

    if not source_root.is_dir():
        return graphs, contents, tails, [f"source folder does not exist: {source_root}"]

    by_graph: dict[str, dict[str, Any]] = {}
    for note in sorted(source_root.rglob("*.md")):
        try:
            data, body = parse_frontmatter(note.read_text(encoding="utf-8"))
        except OSError as exc:
            warnings.append(f"unreadable {note.name}: {exc}")
            continue
        node_id = data.get("id")
        graph_id = data.get("graph_id")
        if not node_id or not graph_id:
            continue  # the index file has no `id` — not a node
        gid = str(graph_id)
        entry = by_graph.setdefault(
            gid, {"title": str(data.get("graph_title") or note.parent.name), "nodes": {}, "raw": {}}
        )
        cover, generated, learner_notes = split_legacy_body(body)
        # The legacy tutorial often opens by repeating its own title as an H1;
        # `render_note` emits "# <title>" itself, so drop the duplicate line
        # (keeping the metadata blockquote below it, which is real content).
        generated = re.sub(r"^#\s+.*\n+", "", generated, count=1).strip() if generated.startswith("# ") else generated
        node = TopicNode(
            id=str(node_id),
            title=str(data.get("title") or note.stem),
            prerequisites=[str(p) for p in (data.get("prerequisites") or [])],
            status=str(data.get("status") or "not_started"),
            estimated_hours=float(data.get("estimated_hours") or 1.0),
            resources=[str(r) for r in (data.get("resources") or [])],
            notes=cover or None,
            day=int(data["day"]) if str(data.get("day", "")).isdigit() else None,
        )
        entry["nodes"][node.id] = node
        entry["raw"][node.id] = (generated, learner_notes)

    for gid, entry in sorted(by_graph.items()):
        nodes: dict[str, TopicNode] = entry["nodes"]
        if not nodes:
            continue
        # Prerequisite edges that point at a node which no longer exists would
        # make the graph a lie, so they are dropped and reported.
        for node in nodes.values():
            dangling = [p for p in node.prerequisites if p not in nodes]
            if dangling:
                warnings.append(f"{gid}/{node.id}: dropping dangling prereq {dangling}")
                nodes[node.id] = node.model_copy(
                    update={"prerequisites": [p for p in node.prerequisites if p in nodes]}
                )
        days = [n.day for n in nodes.values() if n.day]
        graphs.append(
            TopicGraph(
                topic_id=gid,
                title=entry["title"],
                nodes=nodes,
                source=RoadmapSource(kind="topic"),
                # The legacy format never stored a budget. We record the observed
                # span as target_days and leave hours_per_day unset rather than
                # inventing a number — the report shows the real hours/day.
                target_days=max(days) if days else None,
            )
        )
        contents[gid] = {nid: raw[0] for nid, raw in entry["raw"].items()}
        tails[gid] = {nid: raw[1] for nid, raw in entry["raw"].items()}

    return graphs, contents, tails, warnings


def _tail_for(learner_notes: str) -> str | None:
    """Zone-2+3 text for a converted note, or None to use the default stubs."""
    from orchestrator.memory.roadmap import DEFAULT_DEEPENED, MY_NOTES_HEADING

    text = (learner_notes or "").strip()
    if not text:
        return None
    return f"{DEFAULT_DEEPENED}\n{MY_NOTES_HEADING}\n{text}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    parser.add_argument(
        "--source",
        default=str(Path(OBSIDIAN_VAULT_PATH) / OBSIDIAN_TOPIC_FOLDER),
        help="legacy vault folder to read",
    )
    parser.add_argument("--dest", default=MENTOR_CURRICULUM_PATH, help="curriculum root to write")
    parser.add_argument("--force", action="store_true", help="also re-migrate roadmaps that already have a manifest")
    parser.add_argument("--no-backup", action="store_true", help="skip the legacy-file backup")
    args = parser.parse_args()

    source_root = Path(args.source)
    dest_root = Path(args.dest)

    print("=" * 72)
    print("Roadmap migration → manifest store")
    print(f"  source: {source_root}")
    print(f"  dest:   {dest_root}")
    print(f"  mode:   {'APPLY' if args.apply else 'DRY RUN'}")
    print("=" * 72)

    graphs, contents, tails, warnings = read_legacy(source_root)
    for w in warnings:
        print(f"  ! {w}")
    if not graphs:
        print("\nNothing to migrate — no legacy notes with graph frontmatter were found.")
        return

    todo = []
    for graph in graphs:
        dest_manifest = roadmap_dir(dest_root, graph.topic_id) / MANIFEST_NAME
        if dest_manifest.is_file() and not args.force:
            print(f"\n  skip  {graph.topic_id} — already has {MANIFEST_NAME} (use --force to redo)")
            continue
        todo.append(graph)

    print(f"\n{len(todo)} roadmap(s) to convert:\n")
    for graph in todo:
        nodes = list(graph.nodes.values())
        hours = round(sum(n.estimated_hours for n in nodes), 2)
        days = [n.day for n in nodes if n.day]
        edges = sum(len(n.prerequisites) for n in nodes)
        recovered_cover = sum(1 for n in nodes if n.notes)
        his_notes = sum(1 for n in nodes if tails.get(graph.topic_id, {}).get(n.id, "").strip())
        span = max(days) if days else 0
        per_day = f"{round(hours / span, 2)}h/day" if span else "n/a"
        print(f"  • {graph.title}  ({graph.topic_id})")
        print(f"      {len(nodes)} nodes, {edges} prerequisite edges, {hours}h total, {span} days → {per_day}")
        print(f"      'What to Cover' recovered for {recovered_cover}/{len(nodes)} nodes")
        print(f"      learner's own notes carried over for {his_notes}/{len(nodes)} nodes")
        print(f"      → {roadmap_dir(dest_root, graph.topic_id)}/")

    if not args.apply:
        print("\nDRY RUN — nothing was written. Re-run with --apply to migrate.")
        return

    if not args.no_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_dir = ROOT_DIR / "data" / "memory_backups" / f"roadmaps_legacy_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for note in source_root.rglob("*.md"):
            target = backup_dir / note.parent.name / note.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(note, target)
            copied += 1
        print(f"\nBacked up {copied} legacy note(s) → {backup_dir}")

    print()
    for graph in todo:
        gid = graph.topic_id
        report = write_roadmap(
            dest_root,
            graph,
            contents=contents.get(gid, {}),
            tails={nid: t for nid, t in (
                (nid, _tail_for(tails.get(gid, {}).get(nid, ""))) for nid in graph.nodes
            ) if t},
            prune=True,
        )
        coverage = verify_anchors(graph)
        print(f"  ✓ {gid}: {report.summary()}")
        print(f"      manifest: {report.manifest}")
        if report.pruned:
            print(f"      pruned:   {', '.join(report.pruned)}")
        if coverage.total:
            print(f"      anchors:  {coverage.summary()}")
        for w in report.warnings:
            print(f"      ! {w}")

    print("\nDone. The legacy vault was not modified — it remains as a fallback.")
    print("Verify with: uv run python scripts/tests/test_roadmap_store.py")


if __name__ == "__main__":
    main()