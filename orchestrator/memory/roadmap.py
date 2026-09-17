"""
orchestrator/memory/roadmap.py

The roadmap store — one authoritative artifact per learning roadmap.

Why this module exists
----------------------
The vault used to be "a folder of notes that code reverse-engineers". Topic
graphs lived in *four* disagreeing places (`TopicGraph`'s docstring claimed a
`topic_graph_{id}` key nobody wrote; `PROFILE_KEY_MAP` expected a `topic_graphs`
list that was never populated; a read-time merge in `context_builder` patched the
vault over the DB; and the notes themselves carried the truth in frontmatter),
and every structural read meant re-globbing and YAML-parsing the whole folder.
The live database had no `topic_graphs` row at all, so the PI tool
`available_topic_nodes` answered zero nodes while the vault held five unlocked
ones. That is the class of failure this module removes: **one artifact, one
writer, one reader.**

Layout
------
    <root>/<graph_id>/roadmap.yaml        the manifest — AUTHORITATIVE structure
    <root>/<graph_id>/<Node Title>.md     notes — generated content + zones
    <root>/<graph_id>/<Graph Title> Roadmap.md   index — generated navigation

`root` is `config.MENTOR_CURRICULUM_PATH` (default `<repo>/learning/topics`),
so the curriculum sits INSIDE the repo and therefore inside the PI sandbox: the
mentor can read a note when he asks a doubt mid-study, and append deeper
explanations with its normal file tools. Structure stays Python-written and
validated, which is exactly what makes a hand edit to a note safe — the DAG is
no longer parsed out of the notes.

The three note zones (ownership is the whole point)
--------------------------------------------------
    1. GENERATED body     the specialist's tutorial. Regenerated only when new
                          content is supplied for that node; never blanked.
    2.  Deepened in session
                          appended by the mentor during a study session. NEVER
                          rewritten, NEVER pruned. Before this rule existed a
                          regeneration did a blind `write_text` over the whole
                          file, which would have destroyed weeks of tutoring.
    3. 📝 My Notes        his own writing. Never touched by anything.

Anything else in the folder (a file without our frontmatter) is his, and is
never deleted, moved, or pruned by this module.

Fail-open, like every other store here: reads return None/[] with a printed
warning rather than raising, so a broken vault degrades an answer instead of
crashing a turn. Writes are atomic (`os.replace`) and the manifest is written
LAST — it is the commit point.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from schemas.memory import NodeAnchor, RoadmapSource, TopicGraph, TopicNode


class RoadmapError(RuntimeError):
    """A roadmap could not be read or written (bad manifest, missing folder)."""


# ---------------------------------------------------------------------------
# Constants — the vocabulary the rest of the system depends on
# ---------------------------------------------------------------------------

MANIFEST_NAME = "roadmap.yaml"
SCHEMA_VERSION = 1

# The zone headings. These strings are the contract between the specialist
# (which writes zone 1), the mentor (which appends to zone 2) and him (zone 3).
DEEPENED_HEADING = "## 🏫 Deepened in session"
MY_NOTES_HEADING = "## 📝 My Notes"

# Headings this module generates. Emoji are part of the contract: they are what
# Obsidian renders, and what zone-splitting looks for.
PREREQ_HEADING = "## 🔗 Prerequisites"
COVER_HEADING = "## 🎯 What to Cover"
ANCHOR_HEADING = "## 🔍 In the code"
RESOURCES_HEADING = "## 📚 Resources"

STATUS_VALUES = ("not_started", "in_progress", "done", "skipped")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Make a title safe for a filename. Canonical implementation.

    The retired `obsidian_graph._sanitize_filename` stripped the same illegal
    characters; this adds a trailing-dot strip and a length cap.
    """
    cleaned = re.sub(r'[\\/*?:"<>|]', "", str(name or ""))
    cleaned = cleaned.strip().rstrip(".")          # trailing dot breaks Windows paths
    return cleaned[:120] or "untitled"             # long titles fail to write on ext4 at 255 bytes


def sanitize_filename(name: str) -> str:
    """Public name for the filename sanitiser (the one-shot migration imports it)."""
    return _sanitize_filename(name)


def roadmap_dir(root: str | Path, graph_id: str) -> Path:
    """The roadmap's own folder — named by the STABLE graph_id, never the title.

    Naming it after the title was the old design's trap: retitling a roadmap
    created a second folder and orphaned the first.
    """
    return Path(root) / _sanitize_filename(graph_id)


def manifest_path(root: str | Path, graph_id: str) -> Path:
    return roadmap_dir(root, graph_id) / MANIFEST_NAME


def note_path(root: str | Path, graph_id: str, node: TopicNode) -> Path:
    return roadmap_dir(root, graph_id) / f"{_sanitize_filename(node.title)}.md"


def index_path(root: str | Path, graph_id: str, title: str) -> Path:
    """The generated index. Avoids the old doubled 'X Roadmap Roadmap' title."""
    clean = _sanitize_filename(title)
    if not clean.lower().endswith("roadmap"):
        clean = f"{clean} Roadmap"
    return roadmap_dir(root, graph_id) / f"{clean}.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    Same-directory temp + `os.replace` is atomic on POSIX, so a crash can never
    leave a half-written note or manifest (the old writer used a plain
    `write_text` in a loop, so a failure midway left a partial roadmap).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Frontmatter + note zones
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split `---` YAML frontmatter from the body. Never raises."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        data = yaml.safe_load(parts[1]) or {}
    except Exception as exc:
        print(f"[roadmap] unparseable frontmatter ({exc}) — treating as body only")
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, parts[2].lstrip("\n")


DEFAULT_DEEPENED = (
    f"{DEEPENED_HEADING}\n"
    "*Examples, deeper explanations and follow-ups added during study sessions "
    "land here. Nothing in this section is ever rewritten.*\n"
)

DEFAULT_MY_NOTES = (
    f"{MY_NOTES_HEADING}\n"
    "<!-- Your notes and observations as you study. The mentor never writes here. -->\n"
)


@dataclass
class NoteZones:
    """A note's body split at the ownership boundary.

    `recognized` is the safety flag: when False we found no zone headings, which
    means we cannot tell generated content from anything else — so callers must
    NOT replace the note, only append. Unknown human writing is never destroyed.
    """

    generated: str
    preserved: str
    recognized: bool


def split_note_zones(body: str) -> NoteZones:
    """Split a note body into (generated, everything-from-the-first-zone-heading)."""
    body = body or ""
    idxs = [
        i for i in (
            body.find(DEEPENED_HEADING),
            body.find(MY_NOTES_HEADING),
        ) if i != -1
    ]
    if not idxs:
        return NoteZones(generated=body.strip(), preserved="", recognized=False)
    first = min(idxs)
    return NoteZones(
        generated=body[:first].strip(),
        preserved=body[first:].strip("\n"),
        recognized=True,
    )


def _render_anchors(node: TopicNode) -> str:
    """The 'In the code' block — where this concept actually lives."""
    if not node.anchors:
        return ""
    lines = [ANCHOR_HEADING]
    for a in node.anchors:
        loc = f"`{a.path}`" if a.kind == "file" else f"`{a.path}::{a.symbol or '?'}`"
        if a.line:
            loc += f" (line {a.line})"
        lines.append(f"- {loc}" + (f" — {a.note}" if a.note else ""))
    lines.append("")
    return "\n".join(lines)


def render_note(graph: TopicGraph, node: TopicNode, generated_body: str, preserved: str | None) -> str:
    """Render a full note. `preserved` carries zones 2+3 verbatim from the old file.

    `preserved=None` means "brand-new note" → both zones get their default stubs.
    Passing the parsed tail of an existing note is what makes regeneration
    non-destructive: the mentor's enrichments and his notes survive it.
    """
    prereq_titles = [graph.nodes[p].title for p in node.prerequisites if p in graph.nodes]
    prereq_section = (
        "\n".join(f"- [[{_sanitize_filename(t)}]]" for t in prereq_titles)
        if prereq_titles
        else "*None (Entry point)*"
    )
    resources_section = (
        "\n".join(f"- {r}" for r in node.resources) if node.resources else "*No links attached*"
    )

    frontmatter: dict[str, Any] = {
        "id": node.id,
        "graph_id": graph.topic_id,
        "graph_title": graph.title,
        "title": node.title,
        "status": node.status or "not_started",
        "day": node.day,
        "estimated_hours": node.estimated_hours,
        "content_type": node.content_type,
        "prerequisites": node.prerequisites,
        "resources": node.resources,
    }
    if node.anchors:
        frontmatter["anchors"] = [
            {k: v for k, v in a.model_dump().items() if v is not None} for a in node.anchors
        ]
    frontmatter = {k: v for k, v in frontmatter.items() if v is not None}
    yaml_str = yaml.dump(frontmatter, sort_keys=False, allow_unicode=True).strip()

    anchors_block = _render_anchors(node)
    generated = (generated_body or "").strip()
    if not generated:
        generated = "## 🧭 Overview\n*No tutorial content yet — ask the mentor to develop this node.*"

    tail = preserved if preserved is not None else f"{DEFAULT_DEEPENED}\n{DEFAULT_MY_NOTES}"
    day_line = f" | **Day:** {node.day}" if node.day else ""

    return (
        f"---\n{yaml_str}\n---\n\n"
        f"# {node.title}\n\n"
        f"> **Graph:** {graph.title} | **Est. Time:** {node.estimated_hours}h{day_line}  \n"
        f"> **Status:** `{node.status or 'not_started'}`\n\n"
        f"{PREREQ_HEADING}\n{prereq_section}\n\n"
        f"{COVER_HEADING}\n{node.notes or 'Review the core concepts and complete the practical exercises.'}\n\n"
        f"{generated}\n\n"
        f"{anchors_block}"
        f"{RESOURCES_HEADING}\n{resources_section}\n\n"
        f"{tail.strip()}\n"
    )


def find_note_path(root: str | Path, graph_id: str, node: TopicNode) -> Path | None:
    """Locate a node's note: canonical path first, then a frontmatter-id scan
    (so a note renamed by hand is still found rather than silently duplicated)."""
    canonical = note_path(root, graph_id, node)
    if canonical.is_file():
        return canonical
    directory = roadmap_dir(root, graph_id)
    if not directory.is_dir():
        return None
    for f in sorted(directory.glob("*.md")):
        try:
            data, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if str(data.get("id", "")) == str(node.id):
            return f
    return None


def read_note(root: str | Path, graph_id: str, node: TopicNode) -> tuple[Path, str] | None:
    """Read a node's note verbatim. Fail-open: None when it cannot be read."""
    path = find_note_path(root, graph_id, node)
    if path is None:
        return None
    try:
        return path, path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[roadmap] cannot read note {path}: {exc}")
        return None


def append_deepened(
    root: str | Path,
    graph_id: str,
    node: TopicNode,
    markdown: str,
    *,
    source: str = "mentor",
) -> tuple[bool, str]:
    """Append to zone 2 — the mentor's in-session enrichment.

    This is the mentor's write path during a study session: it explains a doubt
    and records the explanation where he will meet it again. It only ever
    APPENDS and only inside zone 2, so it cannot touch the generated body or his
    own notes.
    """
    found = read_note(root, graph_id, node)
    if found is None:
        return False, f"no note found for '{node.title}' in roadmap '{graph_id}'"
    path, text = found
    data, body = parse_frontmatter(text)
    zones = split_note_zones(body)

    addition = f"\n<!-- {source} — {_now_iso()} -->\n{markdown.strip()}\n"
    if not zones.recognized:
        # No zone headings: this note is not ours to restructure, so append a
        # new zone at the end rather than guessing what is generated.
        new_body = f"{body.rstrip()}\n\n{DEFAULT_DEEPENED}\n{addition}"
    elif DEEPENED_HEADING in zones.preserved and MY_NOTES_HEADING in zones.preserved:
        split_at = zones.preserved.find(MY_NOTES_HEADING)
        deepened, rest = zones.preserved[:split_at], zones.preserved[split_at:]
        new_body = f"{zones.generated}\n\n{deepened.rstrip()}\n{addition}\n{rest}"
    else:
        new_body = f"{zones.generated}\n\n{zones.preserved}\n{addition}"

    yaml_str = yaml.dump(data, sort_keys=False, allow_unicode=True).strip()
    _atomic_write_text(path, f"---\n{yaml_str}\n---\n\n{new_body.strip()}\n")
    return True, f"appended to {path.name}"


# ---------------------------------------------------------------------------
# Manifest <-> model
# ---------------------------------------------------------------------------

def _frontmatter_dict(graph: TopicGraph, node: TopicNode) -> dict[str, Any]:
    """The note frontmatter. Mirrors the manifest so Obsidian shows the same
    facts; the manifest stays authoritative (validate_roadmap reports drift)."""
    fm: dict[str, Any] = {
        "id": node.id,
        "graph_id": graph.topic_id,
        "graph_title": graph.title,
        "title": node.title,
        "status": node.status or "not_started",
        "day": node.day,
        "estimated_hours": node.estimated_hours,
        "content_type": node.content_type,
        "prerequisites": node.prerequisites,
        "resources": node.resources,
    }
    if node.anchors:
        fm["anchors"] = [
            {k: v for k, v in a.model_dump().items() if v is not None} for a in node.anchors
        ]
    return {k: v for k, v in fm.items() if v is not None}


def frontmatter_yaml(graph: TopicGraph, node: TopicNode) -> str:
    return yaml.dump(_frontmatter_dict(graph, node), sort_keys=False, allow_unicode=True).strip()


def _node_to_manifest(node: TopicNode) -> dict[str, Any]:
    """Stable, human-readable manifest entry (field order matters for diffs)."""
    entry: dict[str, Any] = {
        "id": node.id,
        "title": node.title,
        "day": node.day,
        "status": node.status or "not_started",
        "estimated_hours": node.estimated_hours,
        "content_type": node.content_type,
        "prerequisites": node.prerequisites,
        "resources": node.resources,
    }
    if node.notes:
        entry["notes"] = node.notes
    if node.anchors:
        entry["anchors"] = [
            {k: v for k, v in a.model_dump().items() if v is not None} for a in node.anchors
        ]
    if node.checkpoint:
        entry["checkpoint"] = {
            k: v for k, v in node.checkpoint.model_dump().items() if v is not None
        }
    return {k: v for k, v in entry.items() if v is not None}


def _node_from_manifest(data: dict[str, Any]) -> TopicNode:
    anchors = []
    for raw in data.get("anchors") or []:
        try:
            anchors.append(NodeAnchor(**raw) if isinstance(raw, dict) else NodeAnchor(path=str(raw)))
        except Exception as exc:
            print(f"[roadmap] dropping unparseable anchor in node {data.get('id')!r}: {exc}")
    return TopicNode(
        id=str(data.get("id") or ""),
        title=str(data.get("title") or "Untitled"),
        prerequisites=[str(p) for p in (data.get("prerequisites") or [])],
        status=data.get("status") or "not_started",
        estimated_hours=float(data.get("estimated_hours") or 1.0),
        resources=[str(r) for r in (data.get("resources") or [])],
        notes=data.get("notes"),
        day=data.get("day"),
        content_type=data.get("content_type"),
        anchors=anchors,
        checkpoint=data.get("checkpoint") or None,
    )


def _graph_to_manifest(graph: TopicGraph) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "graph_id": graph.topic_id,
        "title": graph.title,
        "version": graph.version,
        "created_at": graph.created_at.isoformat() if graph.created_at else _now_iso(),
        "updated_at": _now_iso(),
        "source": graph.source.model_dump(exclude_none=True),
        "stopping_rule": graph.stopping_rule,
        "target_days": graph.target_days,
        "hours_per_day": graph.hours_per_day,
        "nodes": [_node_to_manifest(n) for n in graph.nodes.values()],
    }


def _graph_from_manifest(data: dict[str, Any], graph_id: str, rel_path: str | None) -> TopicGraph:
    nodes: dict[str, TopicNode] = {}
    for raw in data.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        node = _node_from_manifest(raw)
        if not node.id:
            node = node.model_copy(
                update={"id": _sanitize_filename(node.title).lower().replace(" ", "_")}
            )
        if node.id in nodes:
            print(f"[roadmap] duplicate node id {node.id!r} in {graph_id} — later entry wins")
        nodes[node.id] = node
    source_raw = data.get("source") or {}
    try:
        source = RoadmapSource(**source_raw) if isinstance(source_raw, dict) else RoadmapSource()
    except Exception:
        source = RoadmapSource()
    return TopicGraph(
        topic_id=str(data.get("graph_id") or graph_id),
        title=str(data.get("title") or graph_id),
        nodes=nodes,
        version=int(data.get("version") or 1),
        schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
        source=source,
        rel_path=rel_path,
        stopping_rule=data.get("stopping_rule"),
        target_days=data.get("target_days"),
        hours_per_day=data.get("hours_per_day"),
    )


# ---------------------------------------------------------------------------
# Read — the single reader
# ---------------------------------------------------------------------------

def load_roadmap(root: str | Path, graph_id: str) -> TopicGraph:
    """Read one roadmap manifest. Raises RoadmapError when it cannot be read."""
    path = manifest_path(root, graph_id)
    if not path.is_file():
        raise RoadmapError(f"no manifest at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise RoadmapError(f"unreadable manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RoadmapError(f"manifest {path} is not a mapping")
    return _graph_from_manifest(data, graph_id, _sanitize_filename(graph_id))


def try_load_roadmap(root: str | Path, graph_id: str) -> TopicGraph | None:
    """Fail-open read — None (with a printed reason) instead of an exception."""
    try:
        return load_roadmap(root, graph_id)
    except RoadmapError as exc:
        print(f"[roadmap] {exc}")
        return None


def list_roadmaps(root: str | Path) -> list[TopicGraph]:
    """Every roadmap, from the manifests only — one file each, no rglob of notes."""
    base = Path(root)
    if not base.is_dir():
        return []
    out: list[TopicGraph] = []
    for manifest in sorted(base.glob(f"*/{MANIFEST_NAME}")):
        graph = try_load_roadmap(base, manifest.parent.name)
        if graph is not None:
            out.append(graph)
    return out


def find_roadmap_by_title(root: str | Path, title: str, *, exact: bool = True) -> TopicGraph | None:
    """Resolve a roadmap by title. Exact match only by default.

    The old vault matched titles by SUBSTRING, which is how 'SQL' could hit an
    arbitrary file. Ambiguity here returns None rather than picking one.
    """
    needle = (title or "").strip().lower()
    if not needle:
        return None
    for graph in list_roadmaps(root):
        hay = graph.title.strip().lower()
        if (hay == needle) if exact else (needle in hay):
            return graph
    return None


def find_roadmap_any(root: str | Path, id_or_title: str) -> TopicGraph | None:
    """Resolve by exact graph_id first, then exact title. Never substring.

    This is the replacement for the old `graph_id.lower() in g.topic_id.lower()`
    lookup, which matched prefixes and could return the wrong roadmap. The id
    attempt is deliberately quiet: a title ("Interview Prep") failing to resolve
    as a folder name is the normal path, not a problem worth logging.
    """
    needle = (id_or_title or "").strip()
    if not needle:
        return None
    try:
        return load_roadmap(root, needle)
    except RoadmapError:
        pass
    return find_roadmap_by_title(root, needle)


def roadmap_summaries(root: str | Path) -> list[dict[str, Any]]:
    """Per-roadmap progress summaries in the shape `/api/roadmaps` returns.

    Kept identical to the old `list_vault_roadmaps` output so the dashboard's
    `RoadmapSummary` contract does not change when the source moves from the
    legacy vault scan to the manifests.
    """
    summaries: list[dict[str, Any]] = []
    for graph in list_roadmaps(root):
        nodes = list(graph.nodes.values())
        summaries.append(
            {
                "topic_id": graph.topic_id,
                "title": graph.title,
                "total_nodes": len(nodes),
                "completed": sum(1 for n in nodes if n.status == "done"),
                "in_progress": sum(1 for n in nodes if n.status == "in_progress"),
                "not_started": sum(1 for n in nodes if n.status in ("not_started", None)),
                "total_hours": round(sum(n.estimated_hours for n in nodes), 2),
                "source": graph.source.kind,
                "target_days": graph.target_days,
                "hours_per_day": graph.hours_per_day,
                "nodes": [
                    {
                        "id": n.id,
                        "title": n.title,
                        "status": n.status,
                        "estimated_hours": n.estimated_hours,
                    }
                    for n in nodes
                ],
            }
        )
    return summaries


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

@dataclass
class WriteReport:
    """What a write actually did — returned instead of a bare "it worked"."""

    graph_id: str
    folder: str
    notes_written: list[str] = field(default_factory=list)
    notes_synced: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    manifest: str = ""
    index: str = ""
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.notes_written)} written", f"{len(self.notes_synced)} synced"]
        if self.pruned:
            parts.append(f"{len(self.pruned)} pruned")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")
        return ", ".join(parts)


_LOG_EMOJI = {"done": "✅", "in_progress": "🔄", "skipped": "⏭️", "not_started": "⬜"}


def _render_index(graph: TopicGraph) -> str:
    lines = [
        "---",
        f'graph_id: "{graph.topic_id}"',
        f'title: "{graph.title}"',
        "type: roadmap_index",
        "---",
        "",
        f"# 🗺️ {graph.title}",
        "",
        "Overview of all topics by day. Click any topic to open its study file.",
        "",
    ]
    by_day: dict[Any, list[TopicNode]] = {}
    for n in graph.nodes.values():
        by_day.setdefault(n.day, []).append(n)

    for day in sorted(by_day, key=lambda d: (d is None, d if d is not None else 0)):
        lines.append(f"## 📅 Day {day}" if day is not None else "## 📅 Unscheduled")
        for n in by_day[day]:
            after = ""
            if n.prerequisites:
                titles = [graph.nodes[p].title for p in n.prerequisites if p in graph.nodes]
                if titles:
                    after = " (after " + ", ".join(f"[[{_sanitize_filename(t)}]]" for t in titles) + ")"
            mark = _LOG_EMOJI.get(n.status or "not_started", "⬜")
            lines.append(f"- {mark} [[{_sanitize_filename(n.title)}]] — *{n.estimated_hours}h*{after}")
        lines.append("")
    if graph.source.kind == "repo" and graph.source.path:
        lines += [
            "---",
            "",
            f"*Concepts extracted from `{graph.source.path}`"
            + (f" @ `{graph.source.commit}`" if graph.source.commit else "")
            + (f" — stopping rule: **{graph.stopping_rule}**" if graph.stopping_rule else "")
            + ".*",
            "",
        ]
    return "\n".join(lines)


def write_roadmap(
    root: str | Path,
    graph: TopicGraph,
    *,
    contents: dict[str, str] | None = None,
    tails: dict[str, str] | None = None,
    prune: bool = False,
    write_index: bool = True,
) -> WriteReport:
    """Write a roadmap: notes, index, then the manifest LAST (the commit point).

    `contents` maps node id → generated tutorial markdown:
      - a node present in it gets its GENERATED body replaced, with zones 2 and 3
        carried over verbatim;
      - a node absent from it is only frontmatter-synced — its body, the mentor's
        enrichments and his notes are left exactly as they are;
      - `contents=None` means structure-only: no body is ever touched.

    `tails` maps node id → the zone-2+3 text for a note that does not exist yet
    (used by the migration, so a learner's existing "My Notes" are carried into
    the new format rather than reset to the default stubs). Ignored when the note
    already exists with recognisable zones.

    Pruning only ever removes `.md` files that carry OUR `graph_id` and are no
    longer in the manifest. A file without our frontmatter is his: never deleted,
    never moved, never rewritten.
    """
    gid = graph.topic_id
    directory = roadmap_dir(root, gid)
    directory.mkdir(parents=True, exist_ok=True)
    report = WriteReport(graph_id=gid, folder=str(directory))
    generated = contents or {}
    supplied_tails = tails or {}

    # 1. Notes.
    for node in graph.nodes.values():
        path = find_note_path(root, gid, node)
        existing_text: str | None = None
        if path is not None:
            try:
                existing_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                report.warnings.append(f"cannot read {path.name}: {exc}")
        if path is None:
            path = note_path(root, gid, node)

        if existing_text is None:
            _atomic_write_text(
                path,
                render_note(
                    graph, node, generated.get(node.id, ""), supplied_tails.get(node.id)
                ),
            )
            report.notes_written.append(path.name)
            continue

        _, body = parse_frontmatter(existing_text)
        if node.id in generated:
            zones = split_note_zones(body)
            if zones.recognized:
                _atomic_write_text(path, render_note(graph, node, generated[node.id], zones.preserved))
                report.notes_written.append(path.name)
            else:
                # We cannot tell generated content from his own writing, so we
                # refuse to replace the note and append the new material instead.
                report.warnings.append(
                    f"{path.name}: no zone headings found — note kept, new content appended"
                )
                _atomic_write_text(
                    path,
                    f"{existing_text.rstrip()}\n\n## ♻️ Regenerated {_now_iso()[:10]}\n"
                    f"{generated[node.id].strip()}\n",
                )
                report.notes_written.append(path.name)
        else:
            _atomic_write_text(path, f"---\n{frontmatter_yaml(graph, node)}\n---\n\n{body.strip()}\n")
            report.notes_synced.append(path.name)

    # 2. Prune files the manifest no longer claims (only ones we own).
    if prune and contents is not None:
        keep = {note_path(root, gid, n).name for n in graph.nodes.values()}
        keep.add(MANIFEST_NAME)
        if write_index:
            keep.add(index_path(root, gid, graph.title).name)
        for f in sorted(directory.glob("*.md")):
            if f.name in keep:
                continue
            try:
                data, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
            except OSError:
                continue
            if str(data.get("graph_id", "")) == gid:
                f.unlink()
                report.pruned.append(f.name)

    # 3. Index — generated navigation, never authoritative.
    if write_index:
        idx = index_path(root, gid, graph.title)
        _atomic_write_text(idx, _render_index(graph))
        report.index = str(idx)

    # 4. Manifest — the commit point. Written last, so a crash leaves the previous
    #    consistent state rather than a half-applied one.
    bumped = graph.model_copy(
        update={"version": graph.version + 1, "updated_at": datetime.now(timezone.utc)}
    )
    _atomic_write_text(
        manifest_path(root, gid),
        yaml.dump(_graph_to_manifest(bumped), sort_keys=False, allow_unicode=True),
    )
    report.manifest = str(manifest_path(root, gid))
    return report


# ---------------------------------------------------------------------------
# Mutators — every structural change goes through here, and each one re-validates
# the DAG. The old writer rewrote files directly and never called the
# cycle-checking API in memory/topic_graph.py at all.
# ---------------------------------------------------------------------------

_EDITABLE_NODE_FIELDS = {
    "title",
    "day",
    "status",
    "estimated_hours",
    "content_type",
    "prerequisites",
    "resources",
    "notes",
    "anchors",
    "checkpoint",
}


def _assert_dag(graph: TopicGraph) -> str | None:
    """Return an error string when the graph has a cycle. None when it is sane."""
    from orchestrator.memory.topic_graph import topological_order

    try:
        topological_order(graph)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:  # networkx missing, malformed graph, ...
        print(f"[roadmap] DAG check skipped ({exc})")
    return None


def resolve_node(graph: TopicGraph, node_id_or_title: str) -> TopicNode | None:
    """Resolve a node by EXACT id or EXACT title. Ambiguity resolves to None.

    Deliberately not a substring match: the old vault matched
    `target in file_path.stem`, so deleting "SQL" could hit "SQL Basics" or
    "SQL Practice" depending on filesystem enumeration order.
    """
    needle = (node_id_or_title or "").strip()
    if not needle:
        return None
    if needle in graph.nodes:
        return graph.nodes[needle]
    low = needle.lower()
    matches = [n for n in graph.nodes.values() if n.title.strip().lower() == low]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"[roadmap] ambiguous node reference {needle!r} — {len(matches)} title matches")
    return None


def set_node_status(
    root: str | Path, graph_id: str, node_id_or_title: str, status: str
) -> tuple[bool, str]:
    """THE status mutator — nothing else writes a node's status.

    This is what the PI path was missing entirely: `update_node_status` had a
    single caller, inside the Python graph, so on the default engine no roadmap
    node could ever become `done` and the prerequisite frontier never advanced.
    """
    if status not in STATUS_VALUES:
        return False, f"invalid status '{status}' — valid: {', '.join(STATUS_VALUES)}"
    graph = try_load_roadmap(root, graph_id)
    if graph is None:
        return False, f"roadmap '{graph_id}' not found"
    node = resolve_node(graph, node_id_or_title)
    if node is None:
        return False, f"no node matching '{node_id_or_title}' in '{graph_id}' (exact match only)"

    graph.nodes[node.id] = node.model_copy(update={"status": status})
    # Structure-only write: the note's body, the mentor's enrichments and his own
    # notes are all untouched; only the frontmatter is synced.
    write_roadmap(root, graph)
    return True, f"'{node.title}' marked {status}"


def update_node(
    root: str | Path, graph_id: str, node_id_or_title: str, updates: dict[str, Any]
) -> tuple[bool, str]:
    """Update a node's STRUCTURAL fields, with a DAG check before the write.

    Unknown keys are refused with a readable message rather than ignored — a
    typo that silently does nothing is how a "fixed it" claim becomes a lie.
    """
    graph = try_load_roadmap(root, graph_id)
    if graph is None:
        return False, f"roadmap '{graph_id}' not found"
    node = resolve_node(graph, node_id_or_title)
    if node is None:
        return False, f"no node matching '{node_id_or_title}' in '{graph_id}' (exact match only)"

    unknown = sorted(set(updates) - _EDITABLE_NODE_FIELDS)
    if unknown:
        return False, (
            f"unknown field(s) {', '.join(unknown)} — editable: "
            f"{', '.join(sorted(_EDITABLE_NODE_FIELDS))}"
        )

    payload: dict[str, Any] = dict(updates)
    if "anchors" in payload:
        raw = payload["anchors"] or []
        payload["anchors"] = [
            NodeAnchor(**a) if isinstance(a, dict) else NodeAnchor(path=str(a)) for a in raw
        ]
    if payload.get("prerequisites"):
        wanted = [str(p) for p in payload["prerequisites"]]
        missing = [p for p in wanted if p not in graph.nodes]
        if missing:
            return False, f"unknown prerequisite id(s): {', '.join(missing)}"
        payload["prerequisites"] = wanted
    if "status" in payload and payload["status"] not in STATUS_VALUES:
        return False, f"invalid status '{payload['status']}'"

    candidate = node.model_copy(update=payload)
    graph.nodes[node.id] = candidate
    error = _assert_dag(graph)
    if error:
        graph.nodes[node.id] = node  # roll back the in-memory change
        return False, f"refused — would break the prerequisite DAG: {error}"

    write_roadmap(root, graph)
    return True, f"updated '{candidate.title}' ({', '.join(sorted(updates))})"


def add_node(
    root: str | Path,
    graph_id: str,
    node: TopicNode,
    *,
    prerequisites: list[str] | None = None,
) -> tuple[bool, str]:
    """Insert a node into an existing roadmap.

    This is the operation the old vault had no safe path for at all: a node could
    only be appended by hand-editing files, with no prerequisite rewiring and no
    cycle check anywhere in the write path.
    """
    graph = try_load_roadmap(root, graph_id)
    if graph is None:
        return False, f"roadmap '{graph_id}' not found"

    if not node.id:
        node = node.model_copy(
            update={"id": _sanitize_filename(node.title).lower().replace(" ", "_")}
        )
    if node.id in graph.nodes:
        return False, f"node id '{node.id}' already exists in '{graph_id}'"
    if any(n.title.strip().lower() == node.title.strip().lower() for n in graph.nodes.values()):
        return False, f"a node titled '{node.title}' already exists in '{graph_id}'"

    prereqs = [str(p) for p in (prerequisites if prerequisites is not None else node.prerequisites)]
    missing = [p for p in prereqs if p not in graph.nodes]
    if missing:
        return False, f"unknown prerequisite id(s): {', '.join(missing)}"

    graph.nodes[node.id] = node.model_copy(update={"prerequisites": prereqs})
    error = _assert_dag(graph)
    if error:
        graph.nodes.pop(node.id, None)
        return False, f"refused — would break the prerequisite DAG: {error}"

    report = write_roadmap(root, graph)
    note = f"added '{node.title}' to '{graph_id}'"
    if node.anchors:
        coverage = verify_anchors(graph)
        bad = coverage.unresolved_paths(node.id)
        if bad:
            # Not fatal — the node is still useful — but the unverified claim is
            # reported rather than implied to be true.
            note += f" (WARNING: {len(bad)} anchor(s) do not resolve: {', '.join(bad)})"
    return True, f"{note} [{report.summary()}]"


def delete_node(root: str | Path, graph_id: str, node_id_or_title: str) -> tuple[bool, str]:
    """Remove a node: from the manifest, from other nodes' prereqs, and from disk.

    Exact-match only. The old `delete_topic_node` matched a substring of the
    filename and broke on whatever `rglob` yielded first, which is how a request
    for "SQL" could take out an arbitrary note.
    """
    graph = try_load_roadmap(root, graph_id)
    if graph is None:
        return False, f"roadmap '{graph_id}' not found"
    node = resolve_node(graph, node_id_or_title)
    if node is None:
        return False, f"no node matching '{node_id_or_title}' in '{graph_id}' (exact match only)"

    graph.nodes.pop(node.id, None)
    for other_id, other in list(graph.nodes.items()):
        if node.id in other.prerequisites:
            graph.nodes[other_id] = other.model_copy(
                update={"prerequisites": [p for p in other.prerequisites if p != node.id]}
            )

    # contents={} → structure-only for every remaining node, and prune is allowed,
    # so the deleted node's note is the only file removed (and only because it
    # carries our graph_id).
    report = write_roadmap(root, graph, contents={}, prune=True)
    removed = ", ".join(report.pruned) if report.pruned else "no note file found"
    return True, f"deleted '{node.title}' from '{graph_id}' ({removed})"


def delete_roadmap(
    root: str | Path, graph_id: str, *, trash: bool = True
) -> tuple[bool, str]:
    """Delete a whole roadmap. Default is a recoverable move, not an unlink.

    The old `delete_topic_graph` unlinked files and matched them fuzzily against
    ids, titles AND sanitised filenames, so a short query like "ai" could match
    an unrelated roadmap. Here the graph_id must be exact.
    """
    directory = roadmap_dir(root, graph_id)
    if not directory.is_dir():
        return False, f"no roadmap folder at {directory}"
    if not manifest_path(root, graph_id).is_file() and trash:
        return False, (
            f"refusing to delete {directory} — it has no {MANIFEST_NAME}, so it is not "
            "a roadmap this store owns"
        )

    files = sorted(p.name for p in directory.rglob("*") if p.is_file())
    if trash:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = Path(root) / ".trash" / f"{stamp}-{_sanitize_filename(graph_id)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(directory, dest) if directory.is_dir() else None
        return True, f"moved '{graph_id}' to {dest} ({len(files)} files, recoverable)"
    import shutil

    shutil.rmtree(directory)
    return True, f"deleted '{graph_id}' permanently ({len(files)} files)"


# ---------------------------------------------------------------------------
# Anchor verification — turning "this node is about X" into something checkable
# ---------------------------------------------------------------------------

@dataclass
class AnchorResult:
    node_id: str
    node_title: str
    anchor: NodeAnchor
    resolved: bool
    reason: str = ""

    def label(self) -> str:
        a = self.anchor
        loc = a.path if a.kind == "file" else f"{a.path}::{a.symbol or '?'}"
        return loc


@dataclass
class AnchorCoverage:
    """How many of a roadmap's anchors actually point at something real.

    This is the falsifiable number the Repo-to-Curriculum plan asks for: if an
    extractor cannot resolve the files and symbols it cites, that shows up here
    instead of being smoothed over by confident prose.
    """

    results: list[AnchorResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def resolved(self) -> int:
        return sum(1 for r in self.results if r.resolved)

    def ratio(self) -> float:
        return (self.resolved / self.total) if self.total else 0.0

    def unresolved_paths(self, node_id: str) -> list[str]:
        return [r.label() for r in self.results if r.node_id == node_id and not r.resolved]

    def unresolved(self) -> list[AnchorResult]:
        return [r for r in self.results if not r.resolved]

    def summary(self) -> str:
        if not self.total:
            return "no anchors to verify"
        pct = round(self.ratio() * 100)
        return f"{self.resolved}/{self.total} anchors resolved ({pct}%)"


def _workspace_roots(roots: Iterable[str] | None = None) -> list[Path]:
    """The sandbox roots anchors are resolved against (fail-open to the repo)."""
    if roots is not None:
        return [Path(r) for r in roots]
    try:
        from orchestrator.config import WORKSPACE_ROOTS

        return [Path(r) for r in WORKSPACE_ROOTS if r]
    except Exception as exc:
        print(f"[roadmap] workspace roots unavailable ({exc}) — using cwd")
        return [Path.cwd()]


def _resolve_anchor_path(raw: str, roots: list[Path]) -> Path | None:
    """Resolve an anchor path: absolute as given, relative against every root."""
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    checks = [candidate] if candidate.is_absolute() else [r / candidate for r in roots]
    for c in checks:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def verify_anchors(graph: TopicGraph, roots: Iterable[str] | None = None) -> AnchorCoverage:
    """Resolve every anchor in a roadmap against the workspace sandbox.

    existence check for a file anchor; for a symbol anchor the symbol must also
    appear in that file. Never raises — an unreadable file counts as unresolved.
    """
    resolved_roots = _workspace_roots(roots)
    coverage = AnchorCoverage()
    for node in graph.nodes.values():
        for anchor in node.anchors:
            path = _resolve_anchor_path(anchor.path, resolved_roots)
            if path is None:
                coverage.results.append(
                    AnchorResult(node.id, node.title, anchor, False, f"path not found: {anchor.path}")
                )
                continue
            if anchor.kind == "symbol" and anchor.symbol:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    coverage.results.append(
                        AnchorResult(node.id, node.title, anchor, False, f"unreadable: {exc}")
                    )
                    continue
                if not re.search(rf"\b{re.escape(anchor.symbol)}\b", text):
                    coverage.results.append(
                        AnchorResult(
                            node.id, node.title, anchor, False,
                            f"symbol '{anchor.symbol}' not present in {anchor.path}",
                        )
                    )
                    continue
            coverage.results.append(AnchorResult(node.id, node.title, anchor, True))
    return coverage


# ---------------------------------------------------------------------------
# The time plan — the ONE owner of `day` assignment (blueprint G3 / Q2)
# ---------------------------------------------------------------------------

def total_hours(graph: TopicGraph) -> float:
    return round(sum(n.estimated_hours for n in graph.nodes.values()), 2)


def budget_report(graph: TopicGraph) -> dict[str, Any]:
    """Compare the declared budget with what the nodes actually add up to.

    Pure arithmetic — the LLM proposes hours per node, code decides whether the
    plan fits. This mirrors `harness.trim_plan_to_fit`, which enforces the 8h/day
    cap for the SCHEDULE; the curriculum's day assignment had no equivalent, so a
    "4-day at 3h/day" request could produce a 22-hour roadmap unchallenged.
    """
    hours = total_hours(graph)
    days = graph.target_days
    per_day = graph.hours_per_day
    report: dict[str, Any] = {
        "total_hours": hours,
        "target_days": days,
        "hours_per_day": per_day,
        "fits": True,
        "overflow_hours": 0.0,
        "hours_per_day_actual": None,
        "capacity_hours": None,
    }
    if days and days > 0:
        report["hours_per_day_actual"] = round(hours / days, 2)
    if days and per_day:
        capacity = days * per_day
        report["capacity_hours"] = round(capacity, 2)
        if hours > capacity:
            report["fits"] = False
            report["overflow_hours"] = round(hours - capacity, 2)
    return report


def allocate_days(
    graph: TopicGraph,
    *,
    target_days: int | None = None,
    hours_per_day: float | None = None,
) -> tuple[TopicGraph, dict[str, Any]]:
    """Assign each node a day, respecting prerequisites and the daily budget.

    Deterministic: topological order decides sequencing (the DAG is the real
    timeline — `day` presents it), then nodes pack into days up to the per-day
    capacity. Overflow extends the plan instead of silently overloading a day,
    and the report says by how much.
    """
    from orchestrator.memory.topic_graph import topological_order

    days = target_days if target_days is not None else graph.target_days
    per_day = hours_per_day if hours_per_day is not None else graph.hours_per_day
    graph = graph.model_copy(update={"target_days": days, "hours_per_day": per_day})
    if not graph.nodes:
        return graph, budget_report(graph)

    try:
        order = topological_order(graph)
    except Exception as exc:  # cyclic or unreadable graph → keep existing days
        print(f"[roadmap] allocate_days: cannot topologically sort ({exc}) — days unchanged")
        return graph, budget_report(graph)

    capacity = per_day if per_day and per_day > 0 else None
    if capacity is None:
        # No time budget → no day assignment. `day` is a PRESENTATION of the DAG
        # under a budget, not a property of the graph: without a budget the honest
        # answer is "days unset" rather than packing every node into a 26-hour
        # "day 1", which is what the first live extraction reported.
        return graph, budget_report(graph)

    current_day, used = 1, 0.0
    assigned: dict[str, int] = {}
    for node_id in order:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        hours = node.estimated_hours or 0.0
        if capacity is not None and used > 0 and (used + hours) > capacity:
            current_day += 1
            used = 0.0
        assigned[node_id] = current_day
        used += hours

    nodes = {
        nid: n.model_copy(update={"day": assigned.get(nid, n.day or 1)})
        for nid, n in graph.nodes.items()
    }
    graph = graph.model_copy(update={"nodes": nodes})
    report = budget_report(graph)
    report["days_used"] = max(assigned.values()) if assigned else 0
    if days and report["days_used"] > days:
        report["fits"] = False
        report["overflow_hours"] = round(total_hours(graph) - (days * (per_day or 0)), 2)
    return graph, report


# ---------------------------------------------------------------------------
# Validation — the gate every write should be checked against
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    severity: str      # "error" | "warning"
    code: str
    graph_id: str
    message: str
    node_id: str = ""

    def __str__(self) -> str:
        where = self.graph_id + (f"/{self.node_id}" if self.node_id else "")
        return f"[{self.severity}] {where} {self.code}: {self.message}"


def validate_roadmap(
    root: str | Path, graph_id: str | None = None, *, verify: bool = True
) -> list[Issue]:
    """Check every invariant this store promises. Returns issues, never raises.

    Codes: manifest_missing / manifest_mismatch / schema_unsupported / no_nodes /
    duplicate_title / orphan_prereq / cycle / missing_note / frontmatter_drift /
    bad_anchor / budget_overflow / unowned_file
    """
    issues: list[Issue] = []
    base = Path(root)
    gids = [graph_id] if graph_id else [m.parent.name for m in base.glob(f"*/{MANIFEST_NAME}")]

    for gid in gids:
        graph = try_load_roadmap(root, gid)
        if graph is None:
            issues.append(Issue("error", "manifest_missing", gid, "no readable manifest"))
            continue
        if graph.topic_id != gid:
            issues.append(Issue("error", "manifest_mismatch", gid,
                                f"folder '{gid}' holds graph_id '{graph.topic_id}'"))
        if graph.schema_version > SCHEMA_VERSION:
            issues.append(Issue("warning", "schema_unsupported", gid,
                                f"schema_version {graph.schema_version} > {SCHEMA_VERSION}"))
        if not graph.nodes:
            issues.append(Issue("warning", "no_nodes", gid, "roadmap has no nodes"))

        seen_titles: dict[str, str] = {}
        for node in graph.nodes.values():
            low = node.title.strip().lower()
            if low in seen_titles:
                issues.append(Issue("error", "duplicate_title", gid,
                                    f"'{node.title}' shared by {seen_titles[low]} and {node.id}",
                                    node.id))
            seen_titles[low] = node.id
            for prereq in node.prerequisites:
                if prereq not in graph.nodes:
                    issues.append(Issue("error", "orphan_prereq", gid,
                                        f"prerequisite '{prereq}' does not exist", node.id))

        dag_error = _assert_dag(graph)
        if dag_error:
            issues.append(Issue("error", "cycle", gid, dag_error))

        for node in graph.nodes.values():
            found = read_note(root, gid, node)
            if found is None:
                issues.append(Issue("warning", "missing_note", gid, "no note file", node.id))
                continue
            data, _ = parse_frontmatter(found[1])
            if not data:
                continue
            note_status = str(data.get("status", ""))
            note_prereqs = [str(p) for p in (data.get("prerequisites") or [])]
            if note_status and note_status != str(node.status or "not_started"):
                issues.append(Issue("warning", "frontmatter_drift", gid,
                                    f"note status '{note_status}' != manifest '{node.status}'",
                                    node.id))
            elif note_prereqs != node.prerequisites:
                issues.append(Issue("warning", "frontmatter_drift", gid,
                                    "note prerequisites disagree with the manifest", node.id))

        if verify:
            for bad in verify_anchors(graph).unresolved():
                issues.append(Issue("warning", "bad_anchor", gid,
                                    f"{bad.label()} — {bad.reason}", bad.node_id))

        budget = budget_report(graph)
        if budget["capacity_hours"] and not budget["fits"]:
            issues.append(Issue("warning", "budget_overflow", gid,
                                f"{budget['total_hours']}h of nodes vs {budget['capacity_hours']}h "
                                f"budget ({budget['target_days']}d x {budget['hours_per_day']}h) — "
                                f"over by {budget['overflow_hours']}h"))

        directory = roadmap_dir(root, gid)
        owned = {note_path(root, gid, n).name for n in graph.nodes.values()}
        owned.update({MANIFEST_NAME, index_path(root, gid, graph.title).name})
        for f in sorted(directory.glob("*.md")):
            if f.name in owned:
                continue
            data, _ = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
            if str(data.get("graph_id", "")) != gid:
                issues.append(Issue("warning", "unowned_file", gid,
                                    f"{f.name} has no graph_id — treated as the learner's"))
    return issues


def summarize_issues(issues: list[Issue]) -> str:
    """One-line health string, for a tool result or a log line."""
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = len(issues) - errors
    if not issues:
        return "no issues"
    return f"{errors} error(s), {warnings} warning(s)"


# ---------------------------------------------------------------------------
# Pedagogical rendering + cross-roadmap node lookup
#
# Both are live: `render_pedagogical_body` builds a node's generated body for
# goal_decomposer, and `find_node_anywhere` serves the API and the legacy graph.
# They used to be labelled a "legacy bridge" for the retired `obsidian_graph`
# shims — that module no longer exists, so this is simply the canonical home.
# ---------------------------------------------------------------------------
# Heading titles for the Phase-2 structured content buckets.
_PEDAGOGICAL_HEADINGS: dict[str, str] = {
    "concept_overview": "1. Concept Overview",
    "tradeoffs_and_comparisons": "2. Trade-Offs & Comparisons",
    "common_misconceptions": "3. Common Misconceptions",
    "reflection_prompt": "4. Reflection & Self-Check Prompt",
    "approach_breakdown": "2. Approach Breakdown (Naive to Optimal)",
    "pseudocode_or_code": "3. Pseudocode & Implementation",
    "complexity_analysis": "4. Time & Space Complexity Analysis",
    "common_pitfalls": "Common Pitfalls & Watchouts",
    "practice_problem": "Practice Problem",
    "code_example": "2. Code Example",
    "hands_on_challenge": "4. Hands-On Challenge",
    "comparison_table": "2. Comparison Table",
    "canonical_usage": "3. Canonical Usage",
}

_CODE_FIELD_KEYS = ("code_example", "canonical_usage", "pseudocode_or_code")


def render_pedagogical_body(details: dict[str, Any] | None) -> str:
    """Compose a node's GENERATED body from Phase-2 content fields.

    A `markdown_body` key wins outright (the deep expander already renders full
    markdown). Otherwise the structured fields are emitted in a fixed order under
    their canonical headings, with code fields fenced.
    """
    details = details or {}
    body = str(details.get("markdown_body") or "").strip()
    if body:
        return body
    if not details:
        return ""

    blocks: list[str] = []
    index = 1
    for key, value in details.items():
        if not value or key in ("suggested_resources", "content_type", "markdown_body"):
            continue
        text = str(value).strip()
        heading = _PEDAGOGICAL_HEADINGS.get(key, f"{index}. {key.replace('_', ' ').title()}")
        if key in _CODE_FIELD_KEYS and not text.startswith("```") and "\n" in text:
            text = f"```python\n{text}\n```"
        blocks.append(f"## {heading}\n{text}")
        index += 1
    return "\n\n".join(blocks)


def find_node_anywhere(
    root: str | Path, node_id_or_title: str
) -> tuple[str, TopicNode] | None:
    """Locate a node across every roadmap.

    Node references arrive without a graph_id — a title from chat, an id from the
    API — so resolution is exact-match and ambiguity-refusing by design. Returns
    (graph_id, node) — and None when the reference is ambiguous or absent, which
    is what stops a short query from silently picking one of several matches.
    """
    needle = (node_id_or_title or "").strip().lower()
    if not needle:
        return None
    hits: list[tuple[str, TopicNode]] = []
    for graph in list_roadmaps(root):
        for node in graph.nodes.values():
            if node.id.lower() == needle or node.title.strip().lower() == needle:
                hits.append((graph.topic_id, node))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"[roadmap] '{node_id_or_title}' is ambiguous across {len(hits)} roadmaps")
    return None