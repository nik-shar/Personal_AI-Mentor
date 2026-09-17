"""
scripts/tests/test_obsidian_daily.py

Verification test suite for Milestone 2:
Obsidian Daily Notes Integration.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from integrations.obsidian_daily import (
    append_mentor_log,
    ensure_daily_note_exists,
    read_daily_note,
)


def test_daily_note_creation():
    print("=== Testing Obsidian Daily Note Creation & Reading ===")
    today = datetime.now(timezone.utc)
    note_path = ensure_daily_note_exists(date=today)
    print(f"✅ Daily Note Path: {note_path}")
    assert note_path.exists(), "Daily note file was not created!"

    res = read_daily_note(date=today)
    assert res["exists"] is True
    print(f"✅ Daily Note Content Read ({len(res['content'])} bytes)")
    assert "Daily Note" in res["content"]


def test_daily_note_appending():
    print("\n=== Testing Appending Mentor Proactive Log Entries ===")
    today = datetime.now(timezone.utc)
    log_content = (
        "Scheduled today's 4-hour focus block:\n"
        "- 60m: LangGraph Conditional Edges\n"
        "- 60m: Chroma Vector Store\n"
        "- 60m: AI Engineer Applications"
    )
    note_path = append_mentor_log(
        content=log_content,
        title="Proactive Morning Planning",
        date=today,
    )
    res = read_daily_note(date=today)
    assert "Proactive Morning Planning" in res["content"]
    assert "LangGraph Conditional Edges" in res["content"]
    print("✅ Appended mentor log entry successfully into Obsidian Daily Note!")


if __name__ == "__main__":
    test_daily_note_creation()
    test_daily_note_appending()
    print("\n🎉 OBSIDIAN DAILY NOTES TESTS PASSED SUCCESSFULLY!")
