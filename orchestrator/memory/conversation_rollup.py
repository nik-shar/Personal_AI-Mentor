"""
orchestrator/memory/conversation_rollup.py

Rolling-summary layer for the mentor's ONE continuous conversation thread.

Design (single continuous conversation + hybrid context cutoff):
  Tier 1 — verbatim recent window   (last KEEP_RECENT_TURNS turns, rendered by
           dna_context as the "Conversation So Far" section).
  Tier 2 — rolling conversation summary ("Since <checkpoint>: …"), kept in
           profile_facts (system/conversation_running_summary) and injected
           DETERMINISTICALLY into every context document — no retrieval luck.
  Tier 3 — DNA memory (unchanged long-term spine).

Cutoff strategy:
  Boundary rollup  — at natural rest stops (inactivity gap >= 45m, day close,
                     explicit exit / api session end) the portion older than the
                     recent verbatim window is LLM-merged into the running
                     summary; the recent window stays verbatim so the next turn
                     "picks up where it left off".
  Budget rollup    — when the live transcript exceeds MAX_HISTORY_TURNS the
                     overflow is rolled up IMMEDIATELY (never silently dropped).

  Fail-open everywhere: if the LLM merge fails, older turns are still archived
  out of the live window and a marker notes they were not summarized. Nothing
  here can crash a mentor turn.
"""

from __future__ import annotations

import os
import threading
from typing import Any

KEEP_RECENT_TURNS = 12  # turns kept verbatim after a boundary rollup


def _format_transcript(history: list[dict[str, Any]]) -> str:
    return "\n".join(f"{t.get('role', 'unknown')}: {t.get('content', '')}" for t in history or [])


def _merge_summary_llm(
    prev_summary: str,
    segment: list[dict[str, Any]],
    since: str | None = None,
) -> str:
    """
    Fold a conversation segment into the running summary.

    Raises on LLM failure — callers are responsible for the fail-open policy.
    """
    from orchestrator.llm import get_reasoning_llm
    from orchestrator.tracing import component

    segment_text = _format_transcript(segment)
    since_line = f" (summaries began {since})" if since else ""
    prompt = (
        "You maintain a rolling summary of ONE continuous mentor conversation that has "
        "been happening over days/weeks. It is never a general chatbot; every turn is part "
        "of the same relationship and often continues earlier threads.\n"
        "Previous rolling summary:\n"
        f"{prev_summary or '(none yet)'}\n\n"
        f"New conversation segment to fold in{since_line}:\n"
        f"{segment_text}\n\n"
        "Write the UPDATED rolling summary (2-5 sentences, third person, past tense): what "
        "Nik is working on, achievements, anxieties, decisions, concrete gaps, and the most "
        "recent open next step. Keep specific names (topics, companies, technologies) that "
        "matter for continuity. Fold the new segment into the old summary — never just "
        "repeat it; never drift into vagueness."
    )
    with component("conversation_rollup", tags=["component:conversation_rollup"]):
        llm = get_reasoning_llm(temperature=0.1)
        response = llm.invoke([{"role": "user", "content": prompt}])
    return (response.content or "").strip()
def rollup_conversation(
    memory_manager: Any,
    history: list[dict[str, Any]],
    keep: int = KEEP_RECENT_TURNS,
    force: bool = False,
) -> dict[str, Any]:
    """
    Merge the older portion of a transcript into the running summary and
    persist the trimmed thread. Never raises.

    Kill switch: CONVERSATION_ROLLUP_ENABLED=0 degrades to a safe trim
    (recent window kept, thread untouched) — used by tests.

    Returns a report dict:
      {status: idle|rolled|disabled, transcript (kept), summary, note,
       evicted_count}
    """
    if os.getenv("CONVERSATION_ROLLUP_ENABLED", "1").strip().lower() in (
        "0", "false", "no", "off",
    ):
        history = list(history or [])
        if not force and len(history) <= max(0, int(keep)):
            # Within the recent window: keep everything verbatim.
            return {
                "status": "disabled",
                "transcript": history,
                "summary": "",
                "note": "rollup disabled (CONVERSATION_ROLLUP_ENABLED=0)",
                "evicted_count": 0,
            }
        keep_n = 0 if force else max(0, min(int(keep), len(history)))
        return {
            "status": "disabled",
            "transcript": history[keep_n:] if keep_n else [],
            "summary": "",
            "note": "rollup disabled (CONVERSATION_ROLLUP_ENABLED=0)",
            "evicted_count": len(history) - keep_n,
        }

    try:
        thread = dict(memory_manager.load_conversation_thread() or {})
    except Exception as exc:
        print(f"[conversation_rollup] thread load failed: {exc}")
        thread = {}
    prev_summary = thread.get("summary") or ""
    since = thread.get("since")

    history = list(history or [])
    if not force and len(history) <= max(0, int(keep)):
        return {
            "status": "idle",
            "transcript": history,
            "summary": prev_summary,
            "note": "history within recent window",
            "evicted_count": 0,
        }

    keep_n = 0 if force else max(0, min(int(keep), len(history)))
    evicted = history[: len(history) - keep_n]
    stay = history[len(history) - keep_n:] if keep_n else []

    if not evicted:
        return {
            "status": "idle",
            "transcript": stay,
            "summary": prev_summary,
            "note": "nothing to roll up",
            "evicted_count": 0,
        }

    merged = prev_summary
    note = ""
    try:
        merged = _merge_summary_llm(prev_summary, evicted, since)
        if not merged:
            merged = prev_summary
            note = " (merge returned empty — summary unchanged)"
    except Exception as exc:
        print(f"[conversation_rollup] LLM merge failed ({exc}) — older turns archived without summary")
        note = " (older turns archived without summarization)"

    try:
        memory_manager.save_conversation_thread(transcript=stay, summary=merged, since=since)
    except Exception as exc:
        print(f"[conversation_rollup] thread save failed: {exc}")

    return {
        "status": "rolled",
        "transcript": stay,
        "summary": merged,
        "note": note,
        "evicted_count": len(evicted),
    }


def rollup_async(
    memory_manager: Any,
    history: list[dict[str, Any]],
    keep: int = KEEP_RECENT_TURNS,
) -> None:
    """Fire-and-forget budget rollup (daemon thread). Never raises to caller."""

    def _bg() -> None:
        try:
            rollup_conversation(memory_manager, history, keep=keep)
        except Exception as exc:
            print(f"[conversation_rollup] async rollup failed: {exc}")

    threading.Thread(target=_bg, daemon=True).start()


def resume_thread(memory_manager: Any) -> dict[str, Any]:
    """
    Load the persistent conversation thread (transcript + rolling summary).

    Returns {transcript, summary, since, updated_at} with safe empties.
    """
    try:
        return dict(memory_manager.load_conversation_thread() or {})
    except Exception as exc:
        print(f"[conversation_rollup] resume failed: {exc}")
        return {"transcript": [], "summary": "", "since": None, "updated_at": None}