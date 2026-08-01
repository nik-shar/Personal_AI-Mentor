"""
scripts/view_history.py

View, search, and manage your AI Mentor conversation history & memory store.

Commands:
    uv run python scripts/view_history.py                   # View recent conversation turns
    uv run python scripts/view_history.py search <query>    # Semantic RAG search over past chats
    uv run python scripts/view_history.py stats             # Show DB memory stats & open queued questions
    uv run python scripts/view_history.py clear             # Clear conversation history (with confirmation)
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(override=True)

from orchestrator.memory.store import MemoryManager


def print_turn(idx: int, t: dict) -> None:
    dt = t.get("occurred_at") or "Unknown Date"
    if isinstance(dt, str) and "T" in dt:
        try:
            dt_obj = datetime.fromisoformat(dt)
            dt = dt_obj.strftime("%a, %d %b %Y %H:%M:%S")
        except Exception:
            pass

    payload = t.get("payload") or {}
    agent = payload.get("agent_invoked") or "mentor"
    user = payload.get("user_input") or t.get("content") or ""
    response = payload.get("response_text") or ""

    print(f"[{idx}] 📅 {dt}  |  Agent: {agent}")
    print(f"  👤 You:  {user.strip()}")
    if response:
        # Indent response lines for clean CLI reading
        resp_preview = "\n".join(f"  🤖 {line}" for line in response.strip().split("\n")[:6])
        if len(response.strip().split("\n")) > 6:
            resp_preview += "\n  🤖 ..."
        print(f"{resp_preview}")
    print("-" * 75)


def list_history(mm: MemoryManager, limit: int = 10) -> None:
    turns = mm.query_episodic(event_type="conversation_turn", last_n=limit)
    if not turns:
        print("No conversation history found in PostgreSQL DB.")
        return

    print("=" * 75)
    print(f"      RECENT CONVERSATION HISTORY (Last {len(turns)} turns)")
    print("=" * 75)
    # Reverse so oldest appears at top, newest at bottom
    for idx, turn in enumerate(reversed(turns), 1):
        print_turn(idx, turn)


def search_history(mm: MemoryManager, query: str) -> None:
    print(f"🔍 Searching conversation memory for: '{query}'...")
    try:
        from sentence_transformers import SentenceTransformer
        embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        vec = embed_model.encode(query, normalize_embeddings=True).tolist()
        results = mm.semantic_search(vec, hot_threshold_days=0, limit=5)
    except Exception as exc:
        print(f"Vector search failed ({exc}), falling back to keyword search...")
        results = []

    if not results:
        # Fallback keyword match over episodic events
        all_events = mm.query_episodic(last_n=50)
        results = [
            e for e in all_events
            if query.lower() in (e.get("content") or "").lower()
            or query.lower() in str(e.get("payload") or {}).lower()
        ][:5]

    if not results:
        print("No matching memories found.")
        return

    print("=" * 75)
    print(f"      SEMANTIC SEARCH RESULTS FOR '{query}'")
    print("=" * 75)
    for idx, r in enumerate(results, 1):
        dt = r.get("occurred_at") or "Unknown"
        print(f"[{idx}] 📅 {dt}  |  Event: {r.get('event_type')}")
        print(f"  Content: {r.get('content')}\n")


def show_stats(mm: MemoryManager) -> None:
    facts = mm.load_profile_facts()
    turns = mm.query_episodic(event_type="conversation_turn")
    all_events = mm.query_episodic(last_n=100)

    print("=" * 75)
    print("      AI MENTOR DATABASE MEMORY STATS")
    print("=" * 75)
    print(f"  • Total Profile Facts Keys: {len(facts)}")
    print(f"  • Total Conversation Turns Logged: {len(turns)}")
    print(f"  • Total Episodic Events: {len(all_events)}")
    print()
    print("  --- Profile Facts Summary ---")
    for key in ("full_name", "location", "employment_status", "long_term_goal", "energy_level"):
        if key in facts:
            print(f"    - {key}: {facts[key]}")

    open_q = facts.get("open_questions") or []
    if open_q:
        print(f"\n  --- Queued Open Questions ({len(open_q)}) ---")
        for q in open_q:
            print(f"    - {q}")
    print("=" * 75)


def clear_history(mm: MemoryManager) -> None:
    confirm = input("⚠️ Are you sure you want to clear all conversation turns from PostgreSQL DB? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    with mm._session() as session:
        from sqlalchemy import text
        session.execute(text("DELETE FROM episodic_events WHERE event_type = 'conversation_turn';"))
        session.commit()
    print("✅ All conversation turns cleared.")


def main():
    mm = MemoryManager()
    mm.ensure_schema()

    if len(sys.argv) == 1:
        list_history(mm)
    elif sys.argv[1] == "search" and len(sys.argv) > 2:
        search_history(mm, " ".join(sys.argv[2:]))
    elif sys.argv[1] == "stats":
        show_stats(mm)
    elif sys.argv[1] == "clear":
        clear_history(mm)
    else:
        print("Usage:")
        print("  uv run python scripts/view_history.py           (list history)")
        print("  uv run python scripts/view_history.py search X  (semantic search)")
        print("  uv run python scripts/view_history.py stats     (memory stats)")
        print("  uv run python scripts/view_history.py clear     (clear history)")


if __name__ == "__main__":
    main()
