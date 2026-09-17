"""
orchestrator/toolkits.py

The toolkit host layer — a generic instruction-set loader for the main agent.

Vision (from the capability audit → overlay + role-frame model):

  The orchestrator is a universal host. It does NOT contain a teacher,
  psychologist, friend, etc. Instead it loads "toolkits" — folder-per-role
  markdown instruction sets — and activates one per turn as an *overlay*
  over the persistent mentor identity.

      Identity = relationship core (memory, phase, personality — ALWAYS ON)
               + role frame   (toolkit manifest — swapped)
               + behaviors    (toolkit philosophy/workflows/skills — swapped)

  Nothing in the orchestrator core knows the internals of any toolkit;
  `render_toolkit_block()` is the only surface it calls.

Toolkit layout under TOOLKITS_DIR (default <repo>/toolkits):

    toolkits/<name>/
    ├── toolkit.md            # manifest: YAML frontmatter {name, title, role, description}
    ├── philosophy.md         # core doctrine (applied while active)
    ├── behaviors.md          # always-on behavior rules while active
    ├── guardrails.md         # toolkit-specific boundaries
    └── workflows/*.md        # one file per invocable workflow (hint, debug, ...)

The loader is a generalization of orchestrator/cognition/guidelines.py:
cached, mtime-invalidated, never raises, degrades to empty strings. Fail-open.

Design invariants (toolkits modulate HOW the mentor speaks, never WHAT is true):
  - Base persona / identity / constitution always stay; a toolkit only layers on top.
  - Crisis, continuity, and professional-boundary guardrails stay code-enforced
    and are immune to any toolkit content.
  - Toolkits apply only to direct-response turns, never to agent routing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from orchestrator.config import TOOLKITS_DIR

# Labels used to surface teaching-relevant memories for the "retrieve" workflow.
RETRIEVAL_TAGS = ("concept", "mistake", "question", "retrieval", "learning", "owned", "watched")


# ---------------------------------------------------------------------------
# Toolkit data model
# ---------------------------------------------------------------------------

@dataclass
class Toolkit:
    """One parsed instruction-set toolkit."""

    name: str
    path: Path
    title: str = ""                       # display title
    role: str = ""                        # "role frame" — identity while active
    description: str = ""
    philosophy: str = ""                  # core doctrine
    behaviors: str = ""                   # always-on rules while active
    guardrails: str = ""                  # toolkit-specific boundaries
    workflows: dict[str, str] = field(default_factory=dict)              # name -> body
    workflow_descriptions: dict[str, str] = field(default_factory=dict)  # name -> description


_CACHE: dict[str, tuple[float, Toolkit]] = {}  # toolkit dir str -> (mtime, Toolkit)


def _toolkits_root() -> Path:
    """The toolkit root dir, honoring a runtime env override (testable)."""
    return Path(os.getenv("TOOLKITS_DIR", TOOLKITS_DIR))


# ---------------------------------------------------------------------------
# Low-level parsing (mirrors cognition/guidelines.py, but YAML-frontmatter aware)
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> str:
    """Read a file; missing/unreadable files return '' (fail-open)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[toolkits] read failed for {path}: {exc}")
        return ""


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    Split a markdown file into (frontmatter-dict, body).
    Frontmatter is a leading `---\n yaml \n---` block; anything else is body.
    Never raises — malformed YAML falls back to empty dict + full text body.
    """
    body = text
    meta: dict[str, Any] = {}
    stripped = text.lstrip("\ufeff")
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        if end != -1:
            yaml_block = stripped[3:end].strip()
            body = stripped[end + 4 :].lstrip("\n")
            try:
                import yaml

                parsed = yaml.safe_load(yaml_block) or {}
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception as exc:
                print(f"[toolkits] frontmatter parse failed: {exc}")
    if not isinstance(meta, dict):
        meta = {}
    return meta, body


# ---------------------------------------------------------------------------
# Public API — discovery & loading
# ---------------------------------------------------------------------------

def reset_cache() -> None:
    """Force fresh reads on the next access (used by tests)."""
    global _CACHE
    _CACHE = {}


def _dir_mtime(path: Path) -> float:
    """Max mtime across the toolkit dir (so edits to any file invalidate cache)."""
    try:
        return max((p.stat().st_mtime for p in path.rglob("*") if p.is_file()), default=0.0)
    except OSError:
        return 0.0


def list_toolkits() -> list[str]:
    """Names of every valid toolkit folder (one containing a toolkit.md manifest)."""
    root = _toolkits_root()
    try:
        return sorted(
            d.name
            for d in root.iterdir()
            if d.is_dir() and (d / "toolkit.md").is_file()
        )
    except OSError as exc:
        print(f"[toolkits] directory scan failed ({root}): {exc}")
        return []


def load_toolkit(name: str) -> Optional[Toolkit]:
    """
    Load and cache a toolkit by name. Returns None when missing/malformed.
    Cache is invalidated whenever any file under the toolkit dir changes.
    """
    if not name:
        return None
    root = _toolkits_root()
    tk_dir = root / name
    if not tk_dir.is_dir() or not (tk_dir / "toolkit.md").is_file():
        return None

    cache_key = str(tk_dir)
    mtime = _dir_mtime(tk_dir)
    cached = _CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    metad, _ = _parse_frontmatter(_read_file(tk_dir / "toolkit.md"))
    toolkit = Toolkit(
        name=str(metad.get("name") or name),
        path=tk_dir,
        title=str(metad.get("title") or name),
        role=str(metad.get("role") or ""),
        description=str(metad.get("description") or ""),
        philosophy=_read_file(tk_dir / "philosophy.md").strip(),
        behaviors=_read_file(tk_dir / "behaviors.md").strip(),
        guardrails=_read_file(tk_dir / "guardrails.md").strip(),
    )

    workflows_dir = tk_dir / "workflows"
    if workflows_dir.is_dir():
        try:
            wf_paths = sorted(p for p in workflows_dir.glob("*.md"))
        except OSError as exc:
            print(f"[toolkits] workflows scan failed: {exc}")
            wf_paths = []
        for wf in wf_paths:
            text = _read_file(wf)
            wmeta, body = _parse_frontmatter(text)
            label = wf.stem
            toolkit.workflows[label] = body.strip()
            if wmeta.get("description"):
                toolkit.workflow_descriptions[label] = str(wmeta["description"]).strip()

    _CACHE[cache_key] = (mtime, toolkit)
    return toolkit


def get_workflow(name: str, workflow: str) -> str:
    """Return the body of one workflow block ('' when absent)."""
    tk = load_toolkit(name)
    if tk is None:
        return ""
    return tk.workflows.get(workflow) or ""


def _workflow_index(tk: Toolkit) -> str:
    """Compact list of available workflows with their descriptions."""
    lines = ["AVAILABLE WORKFLOWS (pick the one the moment calls for):"]
    if not tk.workflow_descriptions:
        lines.append("- (no structured workflow descriptions found)")
        return "\n".join(lines)
    for wname, wdesc in sorted(tk.workflow_descriptions.items()):
        lines.append(f"- {wname}: {wdesc}")
    return "\n".join(lines)


def render_toolkit_block(
    name: str,
    workflow: Optional[str] = None,
) -> str:
    """
    The instruction-set overlay for the direct-response system prompt.

    Includes the role frame + philosophy + behaviors; then the selected
    workflow body when given, otherwise the workflow index; then guardrails.
    Returns '' when the toolkit can't be loaded. Never raises.
    """
    tk = load_toolkit(name)
    if tk is None:
        return ""

    parts = [f"[TOOLKIT ACTIVE: {tk.title}]"]
    if tk.role:
        parts.append(f"ROLE FRAME: {tk.role}")
    parts.append("")
    if tk.philosophy:
        parts.append(tk.philosophy)
        parts.append("")
    if tk.behaviors:
        parts.append(tk.behaviors)
        parts.append("")

    if workflow and workflow in tk.workflows:
        parts.append(f"WORKFLOW — {workflow}:")
        parts.append(tk.workflows[workflow])
        parts.append("")
        if tk.workflow_descriptions.get(workflow):
            parts.append(f"(workflow intent: {tk.workflow_descriptions[workflow]})")
            parts.append("")
    elif tk.workflows:
        parts.append(_workflow_index(tk))
        parts.append("")

    if tk.guardrails:
        parts.append("TOOLKIT GUARDRAILS:")
        parts.append(tk.guardrails)

    return "\n".join(parts).strip()


def is_toolkit_active(name: str) -> bool:
    """True if the toolkit loads and exposes any behavior/workflow content."""
    tk = load_toolkit(name)
    return tk is not None and bool(tk.philosophy or tk.behaviors or tk.workflows)


def load_guardrail_rules(toolkit_name: str) -> dict[str, Any]:
    """
    Parse the mechanical `rules:` block from a toolkit's guardrails.md.

    This is the single source of the mechanically-enforced quality standard:
    code reads the YAML frontmatter `rules:` (min_length, min_headings,
    banned_placeholders, ...) and enforces it deterministically, while the
    guardrails prose below the frontmatter guides the LLM consciously.
    Fail-open: returns {} when the toolkit or rules block is absent.
    """
    try:
        tk_dir = _toolkits_root() / toolkit_name
        text = _read_file(tk_dir / "guardrails.md")
        if not text:
            return {}
        metad, _body = _parse_frontmatter(text)
        rules = metad.get("rules") if isinstance(metad, dict) else None
        return dict(rules) if isinstance(rules, dict) else {}
    except Exception as exc:
        print(f"[toolkits] guardrail rules load failed for '{toolkit_name}': {exc}")
        return {}


# ---------------------------------------------------------------------------
# Memory-backed material for the "retrieve" (spaced practice) workflow
# ---------------------------------------------------------------------------

def assemble_retrieval_material(
    memory_manager: Any,
    dna_store: Any = None,
    limit: int = 8,
) -> str:
    """
    Real learning history for spaced-retrieval practice:

      - recent learning_log entries (profile facts)
      - DNA memories tagged for teaching (concept/mistake/question/...)
      - the last daily narrative summary

    Returns a compact block (or '' when nothing exists). Fail-open everywhere.
    """
    sections: list[str] = []

    # 1. Recent learning log
    try:
        log = (memory_manager.load_profile_facts(["learning_log"]) or {}).get("learning_log") or []
        if isinstance(log, list) and log:
            lines = []
            for entry in log[-7:]:
                if isinstance(entry, dict):
                    d = str(entry.get("date") or "?")
                    topics = entry.get("topics") or entry.get("topic") or []
                    if isinstance(topics, list):
                        topics = ", ".join(str(t) for t in topics)
                    if topics:
                        lines.append(f"- {d}: {topics}")
            if lines:
                sections.append("RECENT LEARNING LOG (last 7):\n" + "\n".join(lines))
    except Exception as exc:
        print(f"[toolkits] retrieval learning_log failed: {exc}")

    # 2. Tagged DNA memories
    try:
        if dna_store is None:
            from orchestrator.memory.dna_store import get_dna_store
            dna_store = get_dna_store()
        memories = dna_store.list_memories(active_only=True, limit=200)
        tagged = [
            m for m in memories
            if any(t.lower() in (str(tag).lower() for tag in (m.tags or [])) for t in RETRIEVAL_TAGS)
        ]
        tagged.sort(key=lambda m: m.confidence or 0.0, reverse=True)
        if tagged:
            lines = [f"- {m.content}" for m in tagged[:limit]]
            sections.append("DNA MEMORIES WORTH REVIEWING:\n" + "\n".join(lines))
    except Exception as exc:
        print(f"[toolkits] retrieval dna memories failed: {exc}")

    # 3. Recent daily narrative summaries (day-over-day continuity)
    try:
        from orchestrator.memory.daily_summary import (
            load_recent_daily_summaries,
            render_daily_summaries,
        )
        block = render_daily_summaries(
            load_recent_daily_summaries(memory_manager, n=2, before_date=datetime.now(timezone.utc))
        )
        if block:
            sections.append(block)
    except Exception as exc:
        print(f"[toolkits] retrieval daily summaries failed: {exc}")

    if not sections:
        return ""
    return (
        "=== RETRIEVAL PRACTICE MATERIAL (real history — build questions from this) ===\n"
        + "\n\n".join(sections)
    )


# Canonical toolkit names (imported by the reasoner guardrail / tests).
LEARNING_COMPANION = "learning-companion"
CODE_EXPLORER = "code-explorer"