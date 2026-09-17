"""
scripts/tests/test_conversation_thread.py

Verification suite for the ONE continuous conversation thread + hybrid
context-cutoff strategy.

Covers:
  1. Store round-trip: save/load_conversation_thread
  2. rollup_conversation merges the older segment via (fake) LLM and persists
     the trimmed thread; resume_thread restores it
  3. Kill switch: CONVERSATION_ROLLUP_ENABLED=0 degrades to a safe trim
  4. Fail-open: LLM merge raising still trims and keeps the previous summary
  5. dna_context injects the Tier-2 "Earlier Conversation (rolling summary)"
  6. Budget overflow in _append_transcript_turn caps + dispatches rollup

Side effects on the real thread rows are saved/restored around the run.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

import orchestrator.memory.conversation_rollup as cr
from orchestrator.memory.dna_context import build_dna_context
from orchestrator.memory.dna_store import get_dna_store
from orchestrator.memory.store import get_memory_manager

_PASSED = 0
_FAILED = 0
_RESET_ENV = os.environ.get("CONVERSATION_ROLLUP_ENABLED")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if ok:
        _PASSED += 1
        print(f"  ✅ {name}")
    else:
        _FAILED += 1
        print(f"  ❌ {name}  {detail}")


def _snapshot_thread(mm):
    with mm.SessionLocal() as session:
        rows = session.execute(
            text("SELECT category, key, value FROM profile_facts "
                 "WHERE category='system' AND key IN ('conversation_thread','conversation_running_summary')")
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _restore_thread(mm, snap):
    import json
    with mm.SessionLocal() as session:
        session.execute(text(
            "DELETE FROM profile_facts WHERE category='system' "
            "AND key IN ('conversation_thread','conversation_running_summary')"
        ))
        for cat, key, val in snap:
            session.execute(
                text("INSERT INTO profile_facts (category, key, value, source, updated_at) "
                     "VALUES (:c, :k, :v, 'saved', NOW())"),
                {"c": cat, "k": key, "v": json.dumps(val, default=str)},
            )
        session.commit()


def _clear_thread(mm):
    with mm.SessionLocal() as session:
        session.execute(text(
            "DELETE FROM profile_facts WHERE category='system' "
            "AND key IN ('conversation_thread','conversation_running_summary')"
        ))
        session.commit()


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self.content = content

    def invoke(self, messages):
        return _FakeResp(self.content)


def main() -> None:
    mm = get_memory_manager()
    mm.ensure_schema()
    snapshot = _snapshot_thread(mm)

    import orchestrator.llm as orc_llm
    orig_llm = orc_llm.get_reasoning_llm

    try:
        os.environ["CONVERSATION_ROLLUP_ENABLED"] = "1"

        print("\n[1] Store round-trip")
        _clear_thread(mm)
        history = [
            {"role": "user", "content": "a", "timestamp": "2026-09-01T10:00:00"},
            {"role": "mentor", "content": "b", "timestamp": "2026-09-01T10:01:00"},
        ]
        mm.save_conversation_thread(transcript=history, summary="early days", since="2026-08-30T00:00:00")
        t = mm.load_conversation_thread()
        check("transcript round-trips", t["transcript"] == history)
        check("summary round-trips", t["summary"] == "early days")
        check("since preserved", t["since"] == "2026-08-30T00:00:00")

        print("\n[2] rollup merges via LLM + trims thread; resume restores")
        orc_llm.get_reasoning_llm = lambda *a, **k: _FakeLLM(
            "ROLLED: DSA revision, Kotak feedback review, DPO derivation focus."
        )
        big_history = [{"role": "user" if i % 2 == 0 else "mentor",
                        "content": f"turn {i}", "timestamp": f"2026-09-01T1{i:02d}:00"} for i in range(30)]
        report = cr.rollup_conversation(mm, big_history, keep=12)
        check("status rolled", report["status"] == "rolled", report["status"])
        check("evicted 18 turns", report["evicted_count"] == 18, str(report["evicted_count"]))
        check("kept 12 verbatim", len(report["transcript"]) == 12, str(len(report["transcript"])))
        check("summary updated with LLM content", "Kotak" in (report["summary"] or ""), report["summary"])

        resumed = cr.resume_thread(mm)
        check("resume returns kept transcript", len(resumed["transcript"]) == 12 and "turn 29" in resumed["transcript"][-1]["content"])
        check("resume returns merged summary", "ROLLED" in resumed["summary"], resumed["summary"])

        print("\n[3] kill switch")
        os.environ["CONVERSATION_ROLLUP_ENABLED"] = "0"
        before = mm.load_conversation_thread()
        rep2 = cr.rollup_conversation(mm, big_history, keep=12)
        after = mm.load_conversation_thread()
        check("disabled → safe trim reported", rep2["status"] == "disabled", rep2["status"])
        check("disabled → thread untouched", after["transcript"] == before["transcript"] and after["summary"] == before["summary"])
        os.environ["CONVERSATION_ROLLUP_ENABLED"] = "1"

        print("\n[3b] fail-open on LLM failure")
        def _boom(*a, **k):
            raise RuntimeError("llm down")
        orc_llm.get_reasoning_llm = _boom
        rep3 = cr.rollup_conversation(mm, big_history[:18], keep=12)
        check("still trims on failure", len(rep3["transcript"]) == 12, str(len(rep3["transcript"])))
        check("previous summary preserved on failure", (rep3["summary"] or "") == before["summary"])
        check("marker note present", "without summarization" in rep3["note"], rep3["note"])
        orc_llm.get_reasoning_llm = lambda *a, **k: _FakeLLM("recovered summary")

        print("\n[4] budget overflow dispatch (patched rollup_async)")
        import orchestrator.orchestrator as orch
        calls = []
        _save_rollup_async = cr.rollup_async
        cr.rollup_async = lambda mm_, hist, keep=None: calls.append((hist, keep))
        try:
            wm = {"conversation_history": [], "session_started_at": None, "last_turn_at": None}
            # At the exact watermark (2*MAX turns) the oldest MAX are rolled.
            for i in range(orch.MAX_HISTORY_TURNS * 2):
                orch._append_transcript_turn(wm, "user", f"m{i}", memory_manager=object())
            at_watermark_len = len(wm["conversation_history"])
            # A few more turns grow the window again (bounded by the watermark).
            for i in range(orch.MAX_HISTORY_TURNS * 2, orch.MAX_HISTORY_TURNS * 2 + 5):
                orch._append_transcript_turn(wm, "user", f"m{i}", memory_manager=object())
            grown_len = len(wm["conversation_history"])
        finally:
            cr.rollup_async = _save_rollup_async
        check("at watermark the transcript is trimmed to MAX",
              at_watermark_len == orch.MAX_HISTORY_TURNS, str(at_watermark_len))
        check("overflow dispatched ONCE with the oldest MAX turns",
              len(calls) == 1 and len(calls[0][0]) == orch.MAX_HISTORY_TURNS,
              str([(len(c[0]), c[1]) for c in calls]))
        check("transcript stays bounded after further growth",
              grown_len == orch.MAX_HISTORY_TURNS + 5, str(grown_len))

        print("\n[5] dna_context injects Tier-2 rolling summary")
        doc = build_dna_context(mm, get_dna_store(), "hi there",
                                conversation_history=[{"role": "user", "content": "hi", "timestamp": datetime.now(timezone.utc).isoformat()}])
        check("doc has 'Earlier Conversation (rolling summary)' section", "Earlier Conversation (rolling summary)" in doc)
        check("doc still has recent 'Conversation So Far'", "Conversation So Far" in doc)

    finally:
        orc_llm.get_reasoning_llm = orig_llm
        if _RESET_ENV is None:
            os.environ.pop("CONVERSATION_ROLLUP_ENABLED", None)
        else:
            os.environ["CONVERSATION_ROLLUP_ENABLED"] = _RESET_ENV
        _restore_thread(mm, snapshot)
        os.environ["DNA_REFLECTION_ENABLED"] = "0"

    print(f"\n✅ test_conversation_thread: {_PASSED} passed, {_FAILED} failed")
    sys.exit(1 if _FAILED else 0)


if __name__ == "__main__":
    main()