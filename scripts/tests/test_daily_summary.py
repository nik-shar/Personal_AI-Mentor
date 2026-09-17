"""
scripts/tests/test_daily_summary.py

Verification suite for orchestrator/memory/daily_summary.py and the
scheduler wiring — the end-of-day narrative memory system.

Checks:
  1. load_day_events + load_day_facts query the memory manager correctly
  2. load_recent_daily_summaries + render_daily_summaries format cleanly
  3. Idempotency: build_daily_summary skips when one exists (per date)
  4. LLM fail-open: build_daily_summary writes deterministic recap when LLM errors
  5. Deterministic fallback formatting
  6. Recent-summaries loading filters by date
  7. LLM narrative path persists the generated summary

Fully mocked — no DB writes, no network. Run:
    uv run python scripts/tests/test_daily_summary.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.memory import daily_summary

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}" + (f" \u2014 {detail}" if detail else ""))


def _fake_memory_manager(**overrides) -> MagicMock:
    """A MemoryManager stub that returns scripted episodic events / facts."""
    mm = MagicMock()
    mm.query_episodic.return_value = []
    mm.load_profile_facts.return_value = {"learning_streak_days": 4}

    def _events_for(event_type=None, since=None, until=None, last_n=None):
        if event_type == "daily_summary":
            return overrides.get("summaries", [])
        if event_type == "learning_session":
            return overrides.get("learning", [])
        return overrides.get("events", [])

    mm.query_episodic.side_effect = _events_for
    return mm


def test_loaders() -> None:
    print("\n=== loaders ===")
    mm = _fake_memory_manager(learning=[
        {"id": "e1", "occurred_at": "2026-09-08T10:00:00+00:00",
         "event_type": "learning_session", "content": "Learned LangGraph State"},
    ])
    events = daily_summary.load_day_events(mm, datetime(2026, 9, 8, tzinfo=timezone.utc))
    check("load_day_events returns the learning event", len(events) == 1, str(events))

    facts = daily_summary.load_day_facts(mm, datetime(2026, 9, 8, tzinfo=timezone.utc))
    check("facts include streak", facts.get("learning_streak_days") == 4)
    check("facts carry date key", facts.get("_date") == "2026-09-08")


def test_render() -> None:
    print("\n=== render_daily_summaries ===")
    events = [
        {"content": "Studied LangGraph, continued 4-day streak.", "payload": {"date": "2026-09-07", "streak_days": 4}},
        {"content": "Built the orchestrator harness.", "payload": {"date": "2026-09-08", "streak_days": 5}},
    ]
    block = daily_summary.render_daily_summaries(events)
    check("section header present", "### 📖 Recent Days" in block)
    check("both days present", "2026-09-07" in block and "2026-09-08" in block)
    check("streak marker rendered", "(streak 5d)" in block)

    check("empty -> empty string", daily_summary.render_daily_summaries([]) == "")


def test_idempotency() -> None:
    print("\n=== idempotency ===")
    existing = {"id": "existing-1", "content": "Already written today.", "payload": {"date": "2026-09-08"}}

    def _existing(*a, **k):
        return [existing]

    mm = _fake_memory_manager(summaries=[])
    mm.query_episodic.side_effect = _existing
    report = daily_summary.build_daily_summary(mm, datetime(2026, 9, 8, tzinfo=timezone.utc))
    check("skipped when exists", report["status"] == "skipped", str(report))
def test_fail_open() -> None:
    print("\n=== LLM fail-open ===")
    mm = _fake_memory_manager(learning=[
        {"id": "e1", "occurred_at": "2026-09-08T10:00:00+00:00",
         "event_type": "learning_session", "content": "Finished State module"},
    ])
    mm.load_profile_facts.return_value = {"learning_streak_days": 5, "learning_log": []}

    class FailingLLM:
        def invoke(self, messages):
            raise RuntimeError("LLM down")

    report = daily_summary.build_daily_summary(
        mm, datetime(2026, 9, 8, tzinfo=timezone.utc),
        force=True, llm=FailingLLM(),
    )
    check("fallback status", report["status"] == "created_fallback", str(report))
    check("fallback includes event content", "Finished State module" in report["summary"], report["summary"])
    check("fallback includes streak", "5" in report["summary"])
    check("episodic event written", mm.add_episodic_event.call_count >= 1)


def test_deterministic_fallback() -> None:
    print("\n=== deterministic fallback ===")
    events = [
        {"id": "1", "occurred_at": "", "event_type": "learning_session", "content": "Learned X"},
        {"id": "2", "occurred_at": "", "event_type": "daily_plan", "content": "Planned 2h study"},
    ]
    facts = {"_date": "2026-09-08", "learning_streak_days": 3}
    out = daily_summary._deterministic_fallback(events, facts)
    check("fallback includes event texts", "Learned X" in out and "Planned 2h study" in out)
    check("fallback includes streak", "Learning streak: 3" in out)


def test_context_injection() -> None:
    print("\n=== recent summaries loading ===")
    fake_summaries = [
        {"content": "Yesterday's recap.", "payload": {"date": "2026-09-07", "streak_days": 3}},
    ]

    def _events_for(event_type=None, since=None, until=None, last_n=None):
        if event_type == "daily_summary":
            return fake_summaries
        return []

    mm = _fake_memory_manager()
    mm.query_episodic.side_effect = _events_for

    recent = daily_summary.load_recent_daily_summaries(
        mm, n=3, before_date=datetime(2026, 9, 9, tzinfo=timezone.utc)
    )
    check("recent summaries returns events", len(recent) >= 1)
    rendered = daily_summary.render_daily_summaries(recent)
    check("rendered block mentions recap", "Yesterday's recap" in rendered)


class FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    def invoke(self, messages):
        class R:
            content = self._text
        return R()


def test_llm_narrative() -> None:
    print("\n=== LLM narrative path ===")
    mm = _fake_memory_manager(learning=[
        {"id": "e1", "occurred_at": "2026-09-08T10:00:00+00:00",
         "event_type": "learning_session", "content": "Finished State module"},
    ])
    mm.load_profile_facts.return_value = {"learning_streak_days": 4}
    report = daily_summary.build_daily_summary(
        mm, datetime(2026, 9, 8, tzinfo=timezone.utc),
        force=True, llm=FakeLLM("Good day: finished the State module, streak is 4."),
    )
    check("created status when LLM works", report["status"] == "created", str(report))
    check("LLM summary persisted in report", "state module" in report["summary"].lower(), report["summary"])
    check("episodic event written", mm.add_episodic_event.call_count >= 1)


def main() -> None:
    test_loaders()
    test_render()
    test_idempotency()
    test_fail_open()
    test_deterministic_fallback()
    test_context_injection()
    test_llm_narrative()
    print(f"\n{'='*50}\nSummary: {PASSED} passed, {FAILED} failed.")
    if FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()