"""
integrations/obsidian_daily.py

Obsidian Daily Note Integration (Milestone 2)

Manages reading, creating, and appending proactive mentor logs, daily plans,
and evening reflections into the Obsidian Vault Daily Notes folder:
`/home/nik/Documents/AI-Mentor/Daily Notes/YYYY-MM-DD.md`
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from orchestrator.config import OBSIDIAN_DAILY_NOTES_FOLDER, OBSIDIAN_VAULT_PATH


def get_daily_note_path(
    date: Optional[datetime] = None,
    vault_path: str = OBSIDIAN_VAULT_PATH,
    folder: str = OBSIDIAN_DAILY_NOTES_FOLDER,
) -> Path:
    """Return Path object for the target date's daily note file."""
    d = date or datetime.now(timezone.utc)
    date_str = d.strftime("%Y-%m-%d")
    target_dir = Path(vault_path) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{date_str}.md"


def ensure_daily_note_exists(
    date: Optional[datetime] = None,
    vault_path: str = OBSIDIAN_VAULT_PATH,
    folder: str = OBSIDIAN_DAILY_NOTES_FOLDER,
) -> Path:
    """Ensure the daily note file exists with initial frontmatter and headers."""
    note_path = get_daily_note_path(date=date, vault_path=vault_path, folder=folder)
    if note_path.exists():
        return note_path

    d = date or datetime.now(timezone.utc)
    date_str = d.strftime("%Y-%m-%d")
    display_date = d.strftime("%A, %d %B %Y")

    initial_content = (
        "---\n"
        f"date: '{date_str}'\n"
        "type: daily_note\n"
        "tags:\n"
        "  - daily\n"
        "  - ai-mentor\n"
        "mentor_synced: true\n"
        "---\n\n"
        f"# 📅 Daily Note — {display_date}\n\n"
        "## 🧠 Mentor Proactive Logs\n"
    )
    note_path.write_text(initial_content, encoding="utf-8")
    return note_path


def append_mentor_log(
    content: str,
    title: str = "Proactive Check-In",
    date: Optional[datetime] = None,
    vault_path: str = OBSIDIAN_VAULT_PATH,
    folder: str = OBSIDIAN_DAILY_NOTES_FOLDER,
) -> Path:
    """
    Append a structured timestamped mentor log entry into the daily note.
    """
    note_path = ensure_daily_note_exists(date=date, vault_path=vault_path, folder=folder)
    d = datetime.now(timezone.utc)
    time_str = d.strftime("%H:%M UTC")

    formatted_entry = (
        f"\n### ⏱️ [{time_str}] {title}\n"
        f"{content.strip()}\n"
    )

    existing = note_path.read_text(encoding="utf-8")
    note_path.write_text(existing + formatted_entry, encoding="utf-8")
    return note_path


def read_daily_note(
    date: Optional[datetime] = None,
    vault_path: str = OBSIDIAN_VAULT_PATH,
    folder: str = OBSIDIAN_DAILY_NOTES_FOLDER,
) -> dict[str, Any]:
    """Read and return content of daily note for target date if it exists."""
    note_path = get_daily_note_path(date=date, vault_path=vault_path, folder=folder)
    if not note_path.exists():
        return {"exists": False, "content": ""}

    try:
        content = note_path.read_text(encoding="utf-8")
        return {"exists": True, "filepath": str(note_path), "content": content}
    except Exception as exc:
        return {"exists": False, "error": str(exc), "content": ""}
