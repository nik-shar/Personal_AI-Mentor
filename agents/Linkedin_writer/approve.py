"""
agents/Linkedin_writer/approve.py

CLI tool and utility to review and approve drafted LinkedIn posts stored in MemoryManager.
"""

from __future__ import annotations

import sys
from typing import Any

from orchestrator.memory.store import MemoryManager


def list_pending_drafts(memory_manager: MemoryManager) -> list[dict[str, Any]]:
    """Fetch all LinkedIn drafts from profile_facts."""
    drafts = memory_manager.get_profile_fact("linkedin", "linkedin_drafts") or []
    if isinstance(drafts, list):
        return drafts
    return []


def approve_draft(memory_manager: MemoryManager, draft_index: int) -> bool:
    """Mark a pending draft as approved."""
    drafts = list_pending_drafts(memory_manager)
    if not (0 <= draft_index < len(drafts)):
        print(f"Invalid draft index: {draft_index}")
        return False

    drafts[draft_index]["status"] = "approved"
    memory_manager.set_profile_fact("linkedin", "linkedin_drafts", drafts, source="cli_approve")
    print(f"Draft #{draft_index + 1} successfully marked as APPROVED! 🎉")
    return True


def main() -> None:
    print("=== LinkedIn Draft Review & Approval ===")
    mm = MemoryManager()
    mm.ensure_schema()

    drafts = list_pending_drafts(mm)
    if not drafts:
        print("No pending LinkedIn post drafts found in memory.")
        return

    for idx, d in enumerate(drafts, start=1):
        status = d.get("status", "pending")
        topic = d.get("topic", "General")
        content = d.get("content", "")
        print(f"\n[{idx}] Status: {status.upper()} | Topic: {topic}")
        print("-" * 50)
        print(content)
        print("-" * 50)

    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        target_idx = int(sys.argv[1]) - 1
        approve_draft(mm, target_idx)
    else:
        try:
            choice = input("\nEnter draft number to approve (or press Enter to exit): ").strip()
            if choice.isdigit():
                approve_draft(mm, int(choice) - 1)
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
