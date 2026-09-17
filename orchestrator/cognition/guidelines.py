"""
orchestrator/cognition/guidelines.py

Loader for `mentor_agent_guidelines.md` — the mentor's operating constitution.

The markdown file is the SINGLE SOURCE OF TRUTH for:
  - §1 anchor identity facts about Nik (deliberately minimal, user-authored)
  - §2 core principles / §3 router responsibilities / §6 guardrails (behavior)
  - §4 per-agent operating guidelines (injected into each sub-agent's task)

Design rule: the constitution is INJECTED into prompts/context documents,
never copied into memory stores — copies drift from the file; injection does
not. Everything the mentor learns *beyond* the §1 anchor comes from
conversation and lives in DNA memory (see dna_store.py).

Fail-open everywhere: a missing/unreadable file degrades to empty strings,
never to a crashed turn.
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.config import MENTOR_GUIDELINES_PATH

# ---------------------------------------------------------------------------
# Cached file loading (re-reads automatically when the file changes on disk)
# ---------------------------------------------------------------------------

_CACHE: dict[str, object] = {"path": None, "mtime": None, "text": ""}


def _load_text() -> str:
    path = Path(MENTOR_GUIDELINES_PATH)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    if _CACHE["text"] and _CACHE["path"] == str(path) and _CACHE["mtime"] == mtime:
        return _CACHE["text"]  # type: ignore[return-value]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[guidelines] failed to read {path}: {exc}")
        return ""
    _CACHE.update({"path": str(path), "mtime": mtime, "text": text})
    return text


def reset_cache() -> None:
    """Force a fresh read on next access (useful in tests)."""
    _CACHE.update({"path": None, "mtime": None, "text": ""})


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

def _sections() -> dict[str, str]:
    """Split the document into its numbered sections: {"1": body, "2": body, ...}."""
    text = _load_text()
    if not text:
        return {}
    parts = re.split(r"^## (\d+)\. [^\n]*$", text, flags=re.MULTILINE)
    # re.split keeps the capture groups: [pre, "1", body1, "2", body2, ...]
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i]] = parts[i + 1].strip()
    return sections


def get_core_identity() -> str:
    """§1 — Who This Agent Serves. The user-authored anchor facts."""
    return _sections().get("1", "")


def get_core_principles() -> str:
    """§2 — Core Principles (apply to every sub-agent)."""
    return _sections().get("2", "")


def get_router_responsibilities() -> str:
    """§3 — Orchestrator / Router Responsibilities."""
    return _sections().get("3", "")


def get_guardrails() -> str:
    """§6 — Guardrails."""
    return _sections().get("6", "")


def get_agent_guidelines(agent_name: str) -> str:
    """
    §4 subsection for one sub-agent, e.g. get_agent_guidelines("job_hunter")
    returns the bullets under '### `job_hunter`'. Empty string if absent.
    """
    agent_section = _sections().get("4", "")
    if not agent_section or not agent_name:
        return ""
    subsections = re.split(r"^### `([^`]+)`\s*$", agent_section, flags=re.MULTILINE)
    for i in range(1, len(subsections) - 1, 2):
        if subsections[i].strip() == agent_name:
            return subsections[i + 1].strip()
    return ""


# ---------------------------------------------------------------------------
# Prompt building blocks
# ---------------------------------------------------------------------------

def build_constitution_block() -> str:
    """
    Combined standing-orders block for the reasoner's context document:
    core principles + guardrails. Empty string when the file is unavailable.
    """
    principles = get_core_principles()
    guardrails = get_guardrails()
    if not principles and not guardrails:
        return ""
    parts = ["### 📜 Operating Constitution (standing orders — never violate)"]
    if principles:
        parts.append(principles)
    if guardrails:
        parts.append("\n**Guardrails:**")
        parts.append(guardrails)
    return "\n".join(parts)


def build_identity_block() -> str:
    """
    §1 anchor facts for the context document. These are the ONLY built-in
    facts about Nik — everything else is learned through conversation.
    """
    identity = get_core_identity()
    if not identity:
        return ""
    return (
        "### 🧭 Core Identity (anchor facts — user-authored, always trust)\n"
        + identity
    )
