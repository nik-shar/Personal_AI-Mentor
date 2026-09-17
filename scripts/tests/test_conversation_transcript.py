"""
scripts/tests/test_conversation_transcript.py

Phase 1 verification suite — live conversation transcript:

  1. Initial state carries empty transcript fields
  2. intake_node appends the user turn with a timestamp + session start
  3. format_output_node appends the mentor turn (short-circuit path)
  4. Transcript is capped at MAX_HISTORY_TURNS
  5. Inactivity gap (> SESSION_GAP_MINUTES) auto-closes the previous
     session: persisted to conversation_sessions, transcript reset
  6. MemoryManager conversation-session round-trip (save / last / recent)
  7. build_dna_context renders the transcript + "Last conversation" line

Cleans up its own conversation_sessions test rows (session_id prefix
"test-transcript-"). Run from project root:
    uv run python scripts/tests/test_conversation_transcript.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reflection fires a real LLM call otherwise — disable for these tests.
os.environ["DNA_REFLECTION_ENABLED"] = "0"
# Rolling-summary merges would fire real LLM calls + touch the continuous
# thread — disable for these tests too (boundary rollup then safely trims).
os.environ["CONVERSATION_ROLLUP_ENABLED"] = "0"

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

import orchestrator.orchestrator as orch
from orchestrator.memory.dna_context import build_dna_context
from orchestrator.memory.dna_store import get_dna_store
from orchestrator.memory.store import MemoryManager, get_memory_manager
from orchestrator.state import build_initial_state

PASSED = 0
FAILED = 0

TEST_PREFIX = "test-transcript-"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def _make_state(session_id: str, user_input: str = "hello there"):
    state = build_initial_state(None, session_id)
    state["working_memory"]["user_input"] = user_input
    return state


def cleanup(mm: MemoryManager) -> None:
    with mm.SessionLocal() as session:
        session.execute(
            text("DELETE FROM conversation_sessions WHERE session_id LIKE :p"),
            {"p": f"{TEST_PREFIX}%"},
        )
        session.commit()


def main() -> None:
    mm = get_memory_manager()
    mm.ensure_schema()
    cleanup(mm)

    # Silence side-effectful background logging during direct node calls.
    orch._log_turn = lambda *a, **k: None

    # ------------------------------------------------------------------
    print("\n[1] Initial state carries empty transcript fields")
    state = build_initial_state(None, f"{TEST_PREFIX}init")
    wm = state["working_memory"]
    check("conversation_history starts empty", wm["conversation_history"] == [])
    check("session_started_at starts None", wm["session_started_at"] is None)
    check("last_turn_at starts None", wm["last_turn_at"] is None)

    # ------------------------------------------------------------------
    print("\n[2] intake_node appends the user turn with timestamps")
    state = _make_state(f"{TEST_PREFIX}intake")
    out = orch.intake_node(state)
    wm = out["working_memory"]
    hist = wm["conversation_history"]
    check("one turn appended", len(hist) == 1, f"got {len(hist)}")
    check("turn is the raw user message",
          hist[0]["role"] == "user" and hist[0]["content"] == "hello there")
    try:
        datetime.fromisoformat(hist[0]["timestamp"])
        ts_ok = True
    except ValueError:
        ts_ok = False
    check("turn has a parseable ISO timestamp", ts_ok)
    check("session_started_at set", wm["session_started_at"] is not None)
    check("last_turn_at set", wm["last_turn_at"] is not None)

    # ------------------------------------------------------------------
    print("\n[3] format_output_node appends the mentor turn (short-circuit path)")
    state = _make_state(f"{TEST_PREFIX}format")
    out = orch.intake_node(state)
    state["working_memory"] = out["working_memory"]
    state["response_text"] = "Hey — good to see you. What's on your mind?"
    out2 = orch.format_output_node(state)
    hist = out2["working_memory"]["conversation_history"]
    check("user + mentor turns present",
          len(hist) == 2 and hist[1]["role"] == "mentor",
          f"got {[h['role'] for h in hist]}")
    check("mentor content matches response",
          hist[1]["content"] == "Hey — good to see you. What's on your mind?")

    # ------------------------------------------------------------------
    print("\n[4] Transcript bounded by watermark; rollup trim (not silent drop)")
    wm = {"conversation_history": [], "session_started_at": None, "last_turn_at": None}
    for i in range(orch.MAX_HISTORY_TURNS * 2):
        orch._append_transcript_turn(wm, "user", f"message {i}")
    check("at the watermark the transcript is trimmed to MAX",
          len(wm["conversation_history"]) == orch.MAX_HISTORY_TURNS,
          f"len={len(wm['conversation_history'])}")
    check("newest turn preserved across the trim",
          wm["conversation_history"][-1]["content"] == f"message {orch.MAX_HISTORY_TURNS * 2 - 1}")
    for i in range(orch.MAX_HISTORY_TURNS * 2, orch.MAX_HISTORY_TURNS * 2 + 5):
        orch._append_transcript_turn(wm, "user", f"message {i}")
    check("transcript stays under the watermark",
          len(wm["conversation_history"]) <= orch.MAX_HISTORY_TURNS * 2 - 1,
          f"len={len(wm['conversation_history'])}")


    # ------------------------------------------------------------------
    print("\n[5] Inactivity gap auto-closes + persists the previous session")
    session_id = f"{TEST_PREFIX}gap"
    state = _make_state(session_id, "i'm back")
    two_hours_ago = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
    state["working_memory"]["conversation_history"] = [
        {"role": "user", "content": "earlier question", "timestamp": two_hours_ago},
        {"role": "mentor", "content": "earlier answer", "timestamp": two_hours_ago},
    ]
    state["working_memory"]["session_started_at"] = two_hours_ago
    state["working_memory"]["last_turn_at"] = two_hours_ago
    out = orch.intake_node(state)
    wm = out["working_memory"]
    check("gap checkpoints the thread: recent turns retained, new turn appended",
          len(wm["conversation_history"]) == 3
          and wm["conversation_history"][-1]["content"] == "i'm back"
          and wm["conversation_history"][0]["content"] == "earlier question",
          f"got {wm['conversation_history']}")
    check("session_started_at reset to now",
          wm["session_started_at"] != two_hours_ago)
    with mm.SessionLocal() as session:
        row = session.execute(
            text("SELECT turn_count, transcript FROM conversation_sessions WHERE session_id = :s"),
            {"s": session_id},
        ).first()
    check("previous session persisted with 2 turns + full transcript",
          row is not None and row[0] == 2 and len(row[1]) == 2,
          f"row={row}")

    # ------------------------------------------------------------------
    print("\n[6] MemoryManager conversation-session round-trip")
    now = datetime.now().astimezone()
    row_id = mm.save_conversation_session(
        session_id=f"{TEST_PREFIX}roundtrip",
        started_at=now - timedelta(minutes=20),
        ended_at=now,
        transcript=[{"role": "user", "content": "hi", "timestamp": now.isoformat()}],
    )
    check("save returns row id", row_id is not None)
    last = mm.get_last_conversation_session()
    check("get_last returns the roundtrip session",
          last is not None and last["session_id"] == f"{TEST_PREFIX}roundtrip",
          f"got {last and last['session_id']}")
    recent = mm.get_recent_conversation_sessions(limit=5)
    check("get_recent includes it with turn_count + transcript",
          any(r["session_id"] == f"{TEST_PREFIX}roundtrip"
              and r["turn_count"] == 1 and len(r["transcript"]) == 1 for r in recent))

    # ------------------------------------------------------------------
    print("\n[7] build_dna_context renders transcript + temporal continuity")
    history = [
        {"role": "user", "content": "hey, rough day", "timestamp": now.isoformat()},
        {"role": "mentor", "content": "sorry to hear — what happened?", "timestamp": now.isoformat()},
    ]
    doc = build_dna_context(
        mm, get_dna_store(), "let's talk about it",
        conversation_history=history,
    )
    check("doc has 'Conversation So Far' section", "Conversation So Far" in doc)
    check("doc renders timestamped speaker lines",
          "] Nik: hey, rough day" in doc and "] Mentor: sorry to hear" in doc,
          detail=doc[doc.find("Conversation So Far"): doc.find("Conversation So Far") + 300])
    check("doc has 'Last conversation' continuity line", "Last conversation:" in doc)
    check("transcript appears before the current message section",
          doc.find("Conversation So Far") < doc.find("### 💬 Nik's Message"))

    cleanup(mm)

    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
