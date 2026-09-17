"""
orchestrator/cognition/persona.py

The mentor's persona — the single source of truth for HOW the mentor speaks.

The text itself lives in `mentor_persona.md` at the repo root, because it is
CONTENT, not code: the TypeScript mentor core (`mentor/src/identity.ts`) reads
the very same file, so the voice cannot drift between the two runtimes. Before
this, the voice was written twice — here as f-strings, and again hand-condensed
in `mentor/extensions/mentor-identity.ts` — and the copies diverged.

This module is the Python *reader*: it loads the file fresh (mtime-cached),
substitutes `{name}`, and exposes the same public API as before, so nothing
downstream changes. (`direct_response_node` and `synthesizer` are the callers.)

Two layers:
  1. Voice rules + hard rules — always applied.
  2. Few-shot example exchanges — models imitate examples far better than they
     obey adjectives; this is what makes the mentor feel like a person rather
     than a task dispatcher.

Grounding lives here too: what the mentor knows about the user comes from the
context document (DNA memories + the mentor_agent_guidelines.md identity
anchor), never from a hardcoded bio. Familiarity is earned through
conversation, never faked.

Fail-open, like every other cognition loader: a missing/unreadable file
degrades to empty strings — loudly, never by crashing a turn.
"""

from __future__ import annotations

import re
from pathlib import Path

import orchestrator.config as _config
from orchestrator.config import MENTOR_PERSONA_PATH


def get_user_name() -> str:
    """Read the configured user name dynamically (patchable in tests)."""
    return _config.USER_NAME


# ---------------------------------------------------------------------------
# Cached file loading (re-reads automatically when the file changes on disk)
# ---------------------------------------------------------------------------

_CACHE: dict[str, object] = {"path": None, "mtime": None, "text": ""}


def _load_text() -> str:
    path = Path(MENTOR_PERSONA_PATH)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        print(
            f"[persona] {path} not found — the mentor runs WITHOUT its voice layer. "
            "Restore mentor_persona.md at the repo root."
        )
        return ""
    if _CACHE["text"] and _CACHE["path"] == str(path) and _CACHE["mtime"] == mtime:
        return _CACHE["text"]  # type: ignore[return-value]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[persona] failed to read {path}: {exc}")
        return ""
    _CACHE.update({"path": str(path), "mtime": mtime, "text": text})
    return text


def reset_cache() -> None:
    """Force a fresh read on next access (useful in tests)."""
    _CACHE.update({"path": None, "mtime": None, "text": ""})


# ---------------------------------------------------------------------------
# Section parsing — the same `## N. Title` convention as mentor_agent_guidelines.md
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


def _render(body: str) -> str:
    """Substitute the configured user name for the `{name}` placeholder."""
    return body.replace("{name}", get_user_name())


# ---------------------------------------------------------------------------
# Layer 1 — voice rules + hard rules
# ---------------------------------------------------------------------------

def voice_rules() -> str:
    return _render(_sections().get("1", ""))


def hard_rules() -> str:
    return _render(_sections().get("2", ""))


# ---------------------------------------------------------------------------
# Layer 2 — few-shot examples (the voice, demonstrated)
# ---------------------------------------------------------------------------

def few_shot_examples() -> str:
    return _render(_sections().get("3", ""))


# ---------------------------------------------------------------------------
# Assembled blocks for prompts
# ---------------------------------------------------------------------------

def build_persona_block(include_examples: bool = True) -> str:
    """The full persona block: voice + hard rules (+ examples)."""
    examples = few_shot_examples() if include_examples else ""
    parts = [voice_rules(), "", hard_rules()]
    # Only wrap when there is something to wrap: with the content file missing
    # an unconditional wrapper would emit an empty `<examples></examples>` pair
    # and dress a broken read up as a real one. Identical output when the file
    # is present (the normal path), cleaner when it is not.
    if examples:
        parts += ["", "<examples>", examples, "</examples>"]
    return "\n".join(parts)
