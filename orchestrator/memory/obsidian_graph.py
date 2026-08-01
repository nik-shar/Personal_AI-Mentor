"""
orchestrator/memory/obsidian_graph.py

Obsidian Markdown storage for Topic Graphs.
Reads and writes topic graph nodes as .md files with YAML frontmatter in your Obsidian vault.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
import yaml

from schemas.memory import TopicGraph, TopicNode


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter delimited by --- at start of markdown string."""
    if not content.startswith("---"):
        return {}, content
    
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    
    try:
        data = yaml.safe_load(parts[1]) or {}
        body = parts[2].lstrip()
        return data, body
    except Exception as exc:
        print(f"[obsidian_graph] Failed to parse YAML frontmatter: {exc}")
        return {}, content


def _sanitize_filename(name: str) -> str:
    """Sanitize string to be safe for filenames."""
    # Replace illegal filename characters with space or dash
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    return cleaned.strip()


STATUS_ALIAS_MAP = {
    "completed": "done",
    "complete": "done",
    "finished": "done",
    "finish": "done",
}


def _normalize_status(val: str | None) -> str:
    if not val:
        return "not_started"
    v = str(val).lower().strip()
    return STATUS_ALIAS_MAP.get(v, v)


def load_topic_graphs(vault_path: str, folder: str = "Learning/Topics") -> list[TopicGraph]:
    """
    Scan target Obsidian directory for markdown files containing topic graph frontmatter.
    Group nodes by graph_id and return list of TopicGraph objects.
    """
    target_dir = Path(vault_path) / folder
    if not target_dir.exists():
        return []

    nodes_by_graph: dict[str, dict[str, Any]] = {}

    for file_path in target_dir.rglob("*.md"):
        try:
            content = file_path.read_text(encoding="utf-8")
            data, _ = _parse_frontmatter(content)
            
            node_id = data.get("id")
            graph_id = data.get("graph_id")
            if not node_id or not graph_id:
                continue

            title = data.get("title", file_path.stem)
            status = _normalize_status(data.get("status"))
            estimated_hours = float(data.get("estimated_hours", 1.0))
            prerequisites = data.get("prerequisites") or []
            if isinstance(prerequisites, str):
                prerequisites = [prerequisites]
            resources = data.get("resources") or []
            notes = data.get("notes") or ""

            node = TopicNode(
                id=str(node_id),
                title=str(title),
                status=str(status),
                estimated_hours=estimated_hours,
                prerequisites=[str(p) for p in prerequisites],
                resources=[str(r) for r in resources],
                notes=str(notes),
            )

            if graph_id not in nodes_by_graph:
                nodes_by_graph[graph_id] = {
                    "title": data.get("graph_title", "Learning Roadmap"),
                    "nodes": {},
                }
            nodes_by_graph[graph_id]["nodes"][node.id] = node

        except Exception as exc:
            print(f"[obsidian_graph] Error reading {file_path}: {exc}")
            continue

    topic_graphs: list[TopicGraph] = []
    for g_id, g_data in nodes_by_graph.items():
        topic_graphs.append(
            TopicGraph(
                topic_id=g_id,
                title=g_data["title"],
                nodes=g_data["nodes"],
            )
        )
    return topic_graphs


def write_topic_node(
    vault_path: str,
    folder: str,
    graph_id: str,
    graph_title: str,
    node: TopicNode,
    day: int = 1,
    prereq_titles: list[str] | None = None,
    pedagogical_details: dict[str, str] | None = None,
) -> str:
    """
    Write a single TopicNode as a markdown file with YAML frontmatter to Obsidian.
    Includes [[wiki-links]] to prerequisites for Obsidian's Graph View.
    Supports 4-part pedagogical tutorial sections when provided.
    """
    target_dir = Path(vault_path) / folder
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{_sanitize_filename(node.title)}.md"
    file_path = target_dir / filename

    frontmatter = {
        "id": node.id,
        "graph_id": graph_id,
        "graph_title": graph_title,
        "title": node.title,
        "status": node.status or "not_started",
        "estimated_hours": node.estimated_hours,
        "prerequisites": node.prerequisites,
        "day": day,
        "resources": node.resources,
    }

    yaml_str = yaml.dump(frontmatter, sort_keys=False, allow_unicode=True).strip()

    prereq_links = []
    if prereq_titles:
        for pt in prereq_titles:
            prereq_links.append(f"- [[{_sanitize_filename(pt)}]]")
    
    prereq_section = "\n".join(prereq_links) if prereq_links else "*None (Entry point)*"

    resources_list = []
    for r in node.resources:
        resources_list.append(f"- {r}")
    resources_section = "\n".join(resources_list) if resources_list else "*No links attached*"

    pedagogical = pedagogical_details or {}
    markdown_body = pedagogical.get("markdown_body", "").strip()

    pedagogical_blocks = []
    if markdown_body:
        pedagogical_blocks.append(markdown_body)
    elif pedagogical:
        heading_titles = {
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
        idx = 1
        for k, v in pedagogical.items():
            if not v or k in ("suggested_resources", "content_type"):
                continue
            val_str = str(v).strip()
            heading = heading_titles.get(k, f"{idx}. {k.replace('_', ' ').title()}")
            if k in ("code_example", "canonical_usage", "pseudocode_or_code") and not val_str.startswith("```") and "\n" in val_str:
                val_str = f"```python\n{val_str}\n```"
            pedagogical_blocks.append(f"## {heading}\n{val_str}")
            idx += 1

    pedagogical_section = "\n\n".join(pedagogical_blocks) if pedagogical_blocks else ""
    if pedagogical_section:
        pedagogical_section = f"\n\n{pedagogical_section}"

    content = f"""---
{yaml_str}
---

# {node.title}

> **Graph:** {graph_title} | **Est. Time:** {node.estimated_hours}h | **Day:** {day}  
> **Status:** `{node.status or 'not_started'}`

## 🔗 Prerequisites
{prereq_section}

## 🎯 What to Cover
{node.notes or 'Review core concepts and complete practical exercises.'}{pedagogical_section}

## 📚 Resources
{resources_section}

## 📝 My Notes
<!-- Write your notes and observations here as you study! -->

"""
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


def write_roadmap_index(
    vault_path: str,
    folder: str,
    graph_id: str,
    graph_title: str,
    nodes_by_day: dict[int, list[tuple[TopicNode, list[str]]]],
) -> str:
    """
    Write a main index roadmap Markdown file listing all days and wiki links to subtopics.
    """
    vault_dir = Path(vault_path)
    vault_dir.mkdir(parents=True, exist_ok=True)

    index_filename = f"{_sanitize_filename(graph_title)} Roadmap.md"
    file_path = vault_dir / index_filename

    lines = [
        "---",
        f"graph_id: {graph_id}",
        f"title: \"{graph_title} Roadmap\"",
        "type: roadmap_index",
        "---",
        "",
        f"# 🗺️ {graph_title} Roadmap",
        "",
        "Overview of all topics organized by day. Click any topic to open its study file.",
        "",
    ]

    for day in sorted(nodes_by_day.keys()):
        lines.append(f"## 📅 Day {day}")
        items = nodes_by_day[day]
        for node, prereq_titles in items:
            clean_title = _sanitize_filename(node.title)
            prereq_str = f" (after [[{']], [['.join(_sanitize_filename(p) for p in prereq_titles)}]])" if prereq_titles else ""
            lines.append(f"- [[{clean_title}]] — *{node.estimated_hours}h*{prereq_str}")
        lines.append("")

    content = "\n".join(lines)
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


def update_node_status(vault_path: str, folder: str, node_id: str, new_status: str) -> bool:
    """
    Find the file with frontmatter matching `id: node_id` and update status.
    """
    target_dir = Path(vault_path) / folder
    if not target_dir.exists():
        return False

    for file_path in target_dir.rglob("*.md"):
        try:
            content = file_path.read_text(encoding="utf-8")
            data, body = _parse_frontmatter(content)
            if str(data.get("id")) == str(node_id):
                data["status"] = new_status
                new_yaml = yaml.dump(data, sort_keys=False, allow_unicode=True).strip()
                new_content = f"---\n{new_yaml}\n---\n\n{body}"
                file_path.write_text(new_content, encoding="utf-8")
                return True
        except Exception as exc:
            print(f"[obsidian_graph] Failed to update status in {file_path}: {exc}")
            continue
    return False


def delete_topic_graph(vault_path: str, folder: str, graph_id_or_title: str) -> tuple[bool, int, list[str]]:
    """
    Delete an entire topic graph and all its node files + roadmap index file from disk.
    Returns (success, deleted_count, deleted_filenames).
    """
    target_dir = Path(vault_path) / folder
    vault_dir = Path(vault_path)
    if not target_dir.exists():
        return False, 0, []

    target_clean = graph_id_or_title.lower().strip()
    deleted_files = []

    # 1. Find all node files matching graph_id or graph_title
    for file_path in list(target_dir.rglob("*.md")):
        try:
            content = file_path.read_text(encoding="utf-8")
            data, _ = _parse_frontmatter(content)
            g_id = str(data.get("graph_id", "")).lower().strip()
            g_title = str(data.get("graph_title", "")).lower().strip()

            if target_clean in (g_id, g_title) or target_clean in _sanitize_filename(g_title).lower():
                file_path.unlink()
                deleted_files.append(file_path.name)
        except Exception as exc:
            print(f"[obsidian_graph] Error deleting {file_path}: {exc}")

    # 2. Find matching roadmap index file in vault root
    for index_path in list(vault_dir.glob("* Roadmap.md")):
        index_name_clean = index_path.stem.replace(" Roadmap", "").lower().strip()
        if target_clean in index_name_clean or target_clean in _sanitize_filename(index_name_clean):
            try:
                index_path.unlink()
                deleted_files.append(index_path.name)
            except Exception as exc:
                print(f"[obsidian_graph] Error deleting index {index_path}: {exc}")

    return len(deleted_files) > 0, len(deleted_files), deleted_files


def delete_topic_node(vault_path: str, folder: str, node_id_or_title: str) -> tuple[bool, str]:
    """
    Delete a single topic node markdown file by node_id or title.
    Cleans up prerequisite references in remaining nodes and updates the roadmap index file.
    """
    target_dir = Path(vault_path) / folder
    if not target_dir.exists():
        return False, "Vault directory does not exist."

    target_clean = node_id_or_title.lower().strip()
    matched_file = None
    matched_node_id = None
    target_graph_id = None

    for file_path in target_dir.rglob("*.md"):
        try:
            content = file_path.read_text(encoding="utf-8")
            data, _ = _parse_frontmatter(content)
            n_id = str(data.get("id", "")).lower().strip()
            n_title = str(data.get("title", "")).lower().strip()

            if target_clean in (n_id, n_title) or target_clean in file_path.stem.lower():
                matched_file = file_path
                matched_node_id = data.get("id")
                target_graph_id = data.get("graph_id")
                break
        except Exception:
            continue

    if not matched_file:
        return False, f"Node '{node_id_or_title}' not found in vault."

    deleted_title = matched_file.stem
    matched_file.unlink()

    if matched_node_id:
        for file_path in target_dir.rglob("*.md"):
            try:
                content = file_path.read_text(encoding="utf-8")
                data, body = _parse_frontmatter(content)
                prereqs = data.get("prerequisites") or []
                if matched_node_id in prereqs:
                    data["prerequisites"] = [p for p in prereqs if str(p) != str(matched_node_id)]
                    new_yaml = yaml.dump(data, sort_keys=False, allow_unicode=True).strip()
                    new_content = f"---\n{new_yaml}\n---\n\n{body}"
                    file_path.write_text(new_content, encoding="utf-8")
            except Exception:
                continue

    if target_graph_id:
        _regenerate_roadmap_index(vault_path, folder, target_graph_id)

    return True, f"Deleted node '{deleted_title}' from vault."


def edit_topic_node(
    vault_path: str,
    folder: str,
    node_id_or_title: str,
    updates: dict[str, Any],
) -> tuple[bool, str]:
    """
    Update YAML frontmatter or content body of a topic node markdown file.
    Supported updates keys: 'title', 'status', 'estimated_hours', 'prerequisites', 'notes', 'day', etc.
    """
    target_dir = Path(vault_path) / folder
    if not target_dir.exists():
        return False, "Vault directory does not exist."

    target_clean = node_id_or_title.lower().strip()
    matched_file = None

    for file_path in target_dir.rglob("*.md"):
        try:
            content = file_path.read_text(encoding="utf-8")
            data, _ = _parse_frontmatter(content)
            n_id = str(data.get("id", "")).lower().strip()
            n_title = str(data.get("title", "")).lower().strip()

            if target_clean in (n_id, n_title) or target_clean in file_path.stem.lower():
                matched_file = file_path
                break
        except Exception:
            continue

    if not matched_file:
        return False, f"Node '{node_id_or_title}' not found."

    content = matched_file.read_text(encoding="utf-8")
    data, body = _parse_frontmatter(content)

    for k in ("title", "estimated_hours", "prerequisites", "day", "resources"):
        if k in updates:
            data[k] = updates[k]

    if "status" in updates:
        data["status"] = _normalize_status(updates["status"])

    if "notes" in updates:
        data["notes"] = updates["notes"]

    graph_id = data.get("graph_id")
    old_file_path = matched_file
    new_title = data.get("title", matched_file.stem)
    new_file_path = old_file_path.parent / f"{_sanitize_filename(new_title)}.md"

    new_yaml = yaml.dump(data, sort_keys=False, allow_unicode=True).strip()
    new_content = f"---\n{new_yaml}\n---\n\n{body}"

    if old_file_path != new_file_path and old_file_path.exists():
        old_file_path.unlink()

    new_file_path.write_text(new_content, encoding="utf-8")

    if graph_id:
        _regenerate_roadmap_index(vault_path, folder, graph_id)

    return True, f"Updated node '{new_title}'."


def list_vault_roadmaps(vault_path: str, folder: str = "Learning/Topics") -> list[dict[str, Any]]:
    """
    Summarize all roadmaps stored in the vault with node counts, status breakdown, and total hours.
    """
    graphs = load_topic_graphs(vault_path, folder)
    summary_list = []

    for g in graphs:
        nodes = list(g.nodes.values())
        total_nodes = len(nodes)
        completed = sum(1 for n in nodes if n.status in ("done", "completed"))
        in_progress = sum(1 for n in nodes if n.status == "in_progress")
        not_started = sum(1 for n in nodes if n.status in ("not_started", None))
        total_hours = sum(n.estimated_hours for n in nodes)

        summary_list.append({
            "topic_id": g.topic_id,
            "title": g.title,
            "total_nodes": total_nodes,
            "completed": completed,
            "in_progress": in_progress,
            "not_started": not_started,
            "total_hours": total_hours,
            "nodes": [{"id": n.id, "title": n.title, "status": n.status, "estimated_hours": n.estimated_hours} for n in nodes],
        })

    return summary_list


def _regenerate_roadmap_index(vault_path: str, folder: str, graph_id: str) -> None:
    """Helper to rebuild the roadmap index file for a graph after edits/deletions."""
    graphs = load_topic_graphs(vault_path, folder)
    target_graph = next((g for g in graphs if g.topic_id == graph_id), None)
    if not target_graph:
        return

    nodes_by_day: dict[int, list[tuple[TopicNode, list[str]]]] = {}
    node_id_to_title = {n.id: n.title for n in target_graph.nodes.values()}

    target_dir = Path(vault_path) / folder
    for file_path in target_dir.rglob("*.md"):
        try:
            content = file_path.read_text(encoding="utf-8")
            data, _ = _parse_frontmatter(content)
            if data.get("graph_id") == graph_id:
                n_id = data.get("id")
                node = target_graph.nodes.get(n_id)
                if node:
                    day = int(data.get("day", 1))
                    prereqs = data.get("prerequisites") or []
                    prereq_titles = [node_id_to_title.get(p, p) for p in prereqs]
                    nodes_by_day.setdefault(day, []).append((node, prereq_titles))
        except Exception:
            continue

    if nodes_by_day:
        write_roadmap_index(
            vault_path=vault_path,
            folder=folder,
            graph_id=graph_id,
            graph_title=target_graph.title,
            nodes_by_day=nodes_by_day,
        )
