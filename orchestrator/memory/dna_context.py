"""
orchestrator/memory/dna_context.py

DNA Context Builder (dna_memory_redesign_v2.md §7).

Assembles the reasoner's context document from two layers:

  Layer 1 — deterministic injection (never semantically retrieved):
    upcoming due_at memories, today's schedule, core identity facts/goals,
    and at most one pending validation candidate.

  Layer 2 — semantic retrieval (composite-scored, §7.1):
    8-10 memories relevant to the user's message.

Plus compact structured sections that remain the reasoner's contract with
the rest of the system: profile snapshot and personality configuration
(§2.1 — structured data stays structured).

Phase 4 wiring (§12): this is the reasoner's ONLY context path — the
650-line situation report, the mentor_user_state table, and the meta-pattern
engine are gone. Agents are untouched (§7.3): their slices still come from
structured stores.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

from orchestrator.config import USER_NAME
from orchestrator.memory.dna_store import DNAMemoryRecord, DNAMemoryStore
from orchestrator.memory.store import MemoryManager


def classify_time_context(now: datetime) -> str:
    """Bucket local time into a human time-of-day context label."""
    hour = now.hour
    suffix = "_weekend" if now.weekday() >= 5 else "_weekday"
    if 5 <= hour < 8:     return f"early_morning{suffix}"
    elif 8 <= hour < 12:  return f"morning{suffix}"
    elif 12 <= hour < 14: return f"early_afternoon{suffix}"
    elif 14 <= hour < 17: return f"mid_afternoon{suffix}"
    elif 17 <= hour < 21: return f"evening{suffix}"
    elif 21 <= hour < 24: return f"night{suffix}"
    else:                 return f"late_night{suffix}"


# How many semantically-retrieved memories to merge into the themed groups.
SEMANTIC_TOP_K = 10

# How many live-transcript turns to render into the context document.
TRANSCRIPT_RENDER_TURNS = 10

# Per-turn character cap inside the rendered transcript (token control).
TRANSCRIPT_TURN_MAX_CHARS = 600

# memory_type → themed section (§7.2)
TYPE_TO_GROUP = {
    "fact": "who",
    "goal": "who",
    "preference": "how",
    "observation": "now",
    "insight": "now",
    "reflection": "now",
    "context": "now",
}

# Structured profile keys the reasoner still gets verbatim (§2.1 boundary).
# Kept deliberately compact (roughly one line per key in the context document);
# widening this makes the mentor *use* what's stored instead of guessing.
PROFILE_SNAPSHOT_KEYS = [
    "employment_status",
    "current_role",
    "target_roles",
    "target_locations",
    "short_term_goal",
    "long_term_goal",
    "learning_streak_days",
    "energy_level",
    "active_learning_path",
    "linkedin_posting_frequency",
    "last_linkedin_topic",
]

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _provenance_label(m: DNAMemoryRecord) -> str:
    """Inline provenance tag (§7.2) — the reasoner can calibrate trust."""
    confirmed = "user-confirmed" if m.user_confirmed else "unconfirmed"
    return f"[{m.memory_type} · {m.source.replace('_', '-')} · conf {m.confidence:.2f} · {confirmed}]"


def _render_memory_line(m: DNAMemoryRecord) -> str:
    due = f" (due {m.due_at.strftime('%Y-%m-%d')})" if m.due_at else ""
    return f"- {m.content} {_provenance_label(m)}{due}"


def _render_transcript(history: list[dict[str, Any]]) -> list[str]:
    """Render the live session transcript as compact timestamped lines."""
    lines = []
    for turn in history:
        speaker = USER_NAME if turn.get("role") == "user" else "Mentor"
        try:
            hhmm = datetime.fromisoformat(str(turn.get("timestamp", ""))).strftime("%H:%M")
        except ValueError:
            hhmm = "--:--"
        content = (turn.get("content") or "").replace("\n", " ")
        if len(content) > TRANSCRIPT_TURN_MAX_CHARS:
            content = content[: TRANSCRIPT_TURN_MAX_CHARS - 3] + "..."
        lines.append(f"- [{hhmm}] {speaker}: {content}")
    return lines


def _human_delta(iso_ts: str | None, now: datetime) -> str:
    """'25m ago' / '6h ago' / '3 days ago' from an ISO timestamp."""
    if not iso_ts:
        return ""
    try:
        then = datetime.fromisoformat(str(iso_ts))
    except ValueError:
        return ""
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400} day(s) ago"


def _human_minutes(minutes: int) -> str:
    minutes = max(0, int(minutes))
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h{m:02d}m" if m else f"{h}h"
    return f"{minutes}m"


def _render_day_grid(memory_manager: Any, now: datetime) -> list[str]:
    """
    Compact availability summary rendered from the 48-slot day grid.

    Shows free windows (code-computed) and life anchors so the reasoner can
    reason about placement *before* calling a tool — and validate placement
    decisions after. Fail-open to an empty block.
    """
    try:
        from orchestrator.harness import ANCHOR_STATES, build_day_grid
        grid = build_day_grid(memory_manager, now)
    except Exception as exc:
        print(f"[dna_context] day grid failed: {exc}")
        return []

    lines = ["### 📅 Today's Free Windows (30-min slots, computed by code)"]
    free = grid.get("free_windows") or []
    if not free:
        lines.append("- (no free windows found)")
    else:
        for win in free[:6]:
            duration = win.get("duration_min", 0)
            lines.append(f"- {win['start_clock']}–{win['end_clock']} ({_human_minutes(duration)})")

    anchors: list[str] = []
    task_count = 0
    for slot in grid.get("slots") or []:
        state = slot.get("state")
        if state in ANCHOR_STATES:
            clock = slot.get("clock") or ""
            anchors.append(f"{clock} {state}" + (f" ({slot.get('label')})" if slot.get("label") else ""))
        elif state == "task":
            task_count += 1
    if anchors:
        lines.append(
            "Life anchors (never schedule over these): " + " · ".join(sorted(set(anchors)))
        )
    if task_count:
        lines.append(f"Placed blocks today: {task_count} slot(s) booked.")
    return lines


def _render_schedule(events: list[dict[str, Any]]) -> list[str]:
    if not events:
        return ["- (nothing scheduled today)"]
    lines = []
    for e in events:
        start = e.get("start_time")
        time_str = ""
        if start:
            try:
                time_str = datetime.fromisoformat(str(start)).strftime("%H:%M")
            except ValueError:
                time_str = ""
        status = e.get("status", "scheduled")
        marker = {"completed": "✅", "in_progress": "🔄", "cancelled": "❌"}.get(status, "⏳")
        duration = e.get("duration_min", 30)
        lines.append(f"- {time_str} {marker} {e.get('title', 'Untitled')} ({duration}m)")
    return lines


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_dna_context(
    memory_manager: MemoryManager,
    dna_store: DNAMemoryStore,
    user_input: str,
    trigger: str = "user_message",
    conversation_history: list[dict[str, Any]] | None = None,
) -> str:
    """
    Build the reasoner's context document (§7.2). This is the only context
    path since Phase 4 removed the situation report. Never raises on missing
    data — every section degrades gracefully.
    """
    now_local = datetime.now().astimezone()
    now_utc = datetime.now(UTC)
    time_context = classify_time_context(now_local)

    # --- Layer 1: deterministic injection ----------------------------------
    det = {"due": [], "core": [], "pending_validation": []}
    semantic: list[DNAMemoryRecord] = []
    try:
        det = dna_store.get_deterministic()
    except Exception as exc:
        print(f"[dna_context] deterministic layer failed: {exc}")

    # --- Layer 2: semantic retrieval ---------------------------------------
    try:
        query_context = f"User says: '{user_input}' | Time: {now_local.strftime('%A %I:%M %p')}"
        semantic = dna_store.retrieve(query_context, top_k=SEMANTIC_TOP_K)
    except Exception as exc:
        print(f"[dna_context] semantic retrieval failed: {exc}")

    # --- Merge + dedup by id, group by theme --------------------------------
    seen: set[str] = set()
    groups: dict[str, list[DNAMemoryRecord]] = {"who": [], "how": [], "now": []}

    def _add(m: DNAMemoryRecord) -> None:
        if m.id in seen:
            return
        seen.add(m.id)
        groups.setdefault(TYPE_TO_GROUP.get(m.memory_type, "now"), []).append(m)

    for m in det["core"]:
        _add(m)
    for m in semantic:
        _add(m)

    # --- Today's schedule (structured — always included) --------------------
    today_start = datetime.combine(now_utc.date(), time.min, tzinfo=UTC)
    today_end = datetime.combine(now_utc.date(), time.max, tzinfo=UTC)
    try:
        schedule_today = memory_manager.get_schedule_events(start_date=today_start, end_date=today_end)
    except Exception as exc:
        print(f"[dna_context] schedule read failed: {exc}")
        schedule_today = []

    # --- Structured profile snapshot (§2.1 — stays structured) --------------
    try:
        profile = memory_manager.load_profile_facts(PROFILE_SNAPSHOT_KEYS)
    except Exception as exc:
        print(f"[dna_context] profile read failed: {exc}")
        profile = {}

    # --- Personality configuration (structured; reasoner prompt expects it) --
    try:
        from orchestrator.cognition.personality import get_personality
        personality = get_personality(memory_manager)
    except Exception as exc:
        print(f"[dna_context] personality read failed: {exc}")
        personality = {}

    # --- Momentum (computed on demand — Phase 4 keeps the math, not the table)
    try:
        from orchestrator.cognition.metrics import compute_momentum
        momentum = compute_momentum(memory_manager)
    except Exception as exc:
        print(f"[dna_context] momentum computation failed: {exc}")
        momentum = {}


    # --- Discovery mode (Notes.md Layer B) — coverage-based guided discovery ---
    onboarding_block = ""
    try:
        from orchestrator.cognition.onboarding import (
            build_onboarding_guidance,
            get_onboarding_status,
        )
        onboarding_status = get_onboarding_status(dna_store, memory_manager)
        onboarding_block = build_onboarding_guidance(onboarding_status)
    except Exception as exc:
        print(f"[dna_context] onboarding check failed: {exc}")

    # --- Operating constitution + identity anchor (mentor_agent_guidelines.md)
    # Deterministic by design: these are user-authored standing orders, never
    # semantically retrieved and never copied into memory stores.
    identity_block = ""
    constitution_block = ""
    try:
        from orchestrator.cognition.guidelines import (
            build_constitution_block,
            build_identity_block,
        )
        identity_block = build_identity_block()
        constitution_block = build_constitution_block()
    except Exception as exc:
        print(f"[dna_context] guidelines load failed: {exc}")

    # --- Last closed conversation session (temporal continuity) -------------
    last_session_line = ""
    try:
        last_session = memory_manager.get_last_conversation_session()
    except Exception as exc:
        print(f"[dna_context] last-session read failed: {exc}")
        last_session = None
    if last_session and last_session.get("ended_at"):
        delta = _human_delta(last_session["ended_at"], now_local)
        try:
            ended_fmt = datetime.fromisoformat(last_session["ended_at"]).strftime("%b %d, %I:%M %p")
        except ValueError:
            ended_fmt = "unknown time"
        last_session_line = (
            f"Last conversation: {ended_fmt} ({delta}) — "
            f"{last_session.get('turn_count', 0)} turns"
        )

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    lines: list[str] = [
        f"## Context — {now_local.strftime('%b %d, %Y, %I:%M %p')} ({time_context})",
        f"Trigger: {trigger}",
    ]
    if last_session_line:
        lines.append(last_session_line)
    lines.append("")

    if onboarding_block:
        lines.append(onboarding_block)
        lines.append("")

    if identity_block:
        lines.append(identity_block)
        lines.append("")

    if constitution_block:
        lines.append(constitution_block)
        lines.append("")

    if det["due"]:
        lines.append("### ⏰ Deadlines & Time-Sensitive")
        lines.extend(_render_memory_line(m) for m in det["due"])
        lines.append("")

    lines.append(f"### 🧬 Who {USER_NAME} Is")
    if groups["who"]:
        lines.extend(_render_memory_line(m) for m in groups["who"])
    else:
        lines.append("- (no identity memories yet)")
    lines.append("")

    lines.append(f"### 🛠 How {USER_NAME} Works")
    if groups["how"]:
        lines.extend(_render_memory_line(m) for m in groups["how"])
    else:
        lines.append("- (no working-style memories yet)")
    lines.append("")

    lines.append("### 📈 What's Happening Now")
    if groups["now"]:
        lines.extend(_render_memory_line(m) for m in groups["now"])
    else:
        lines.append("- (no current observations yet)")
    lines.append("")

    # --- Recent daily narrative summaries (day-over-day continuity) --------
    daily_summaries_block = ""
    try:
        from orchestrator.memory.daily_summary import load_recent_daily_summaries, render_daily_summaries
        daily_summaries_block = render_daily_summaries(
            load_recent_daily_summaries(memory_manager, n=3, before_date=now_utc)
        )
    except Exception as exc:
        print(f"[dna_context] daily summaries read failed: {exc}")
    if daily_summaries_block:
        lines.append(daily_summaries_block)
        lines.append("")

    if det["pending_validation"]:
        candidate = det["pending_validation"][0]
        lines.append("### ❓ Worth Validating (ask naturally, max 1)")
        lines.append(
            f"- Unconfirmed inference: \"{candidate.content}\" — if it fits the "
            "conversation, ask Nik whether this rings true."
        )
        lines.append("")

    lines.append("### 📋 Today's Schedule")
    lines.extend(_render_schedule(schedule_today))
    lines.append("")

    lines.extend(_render_day_grid(memory_manager, now_utc))
    lines.append("")

    if momentum:
        lines.append("### 📶 Momentum (computed on demand)")
        lines.append(
            f"- Streak: {momentum.get('streak_days', 0)} day(s) ≥60% completion · "
            f"7-day completion: {int(momentum.get('completion_rate_7d', 0) * 100)}% "
            f"({momentum.get('momentum_trend', 'stable')}) · "
            f"today: {int(momentum.get('completion_rate_today', 0) * 100)}% "
            f"of {momentum.get('blocks_today', 0)} block(s)"
        )
        lines.append("")

    # --- Render Profile Snapshot ONLY if NOT in onboarding / fresh cold-start mode ---
    if not onboarding_block:
        lines.append("### 📊 Profile Snapshot (structured)")
        snapshot_lines = []
        for key in PROFILE_SNAPSHOT_KEYS:
            value = profile.get(key)
            if value in (None, "", []):
                continue
            if key == "active_learning_path" and isinstance(value, dict):
                value = value.get("title")
            snapshot_lines.append(f"- {key.replace('_', ' ').title()}: {value}")
        lines.extend(snapshot_lines or ["- (no pre-stored profile facts)"])
        lines.append("")
    else:
        lines.append("### 🍃 Pure Conversational Discovery (No static profile facts active)")
        lines.append("- Static profile assumptions are hidden. Start curious and learn purely through natural conversation.")
        lines.append("")

    lines.append("### 🎭 Personality Configuration")
    lines.append(f"- Accountability level: {personality.get('accountability_level', 3)}/10")
    lines.append(f"- Tone: {personality.get('tone', 'warm')}")
    lines.append(f"- Emphasis: {personality.get('coaching_emphasis', 'consistency')}")
    if personality.get("custom_instructions"):
        lines.append(f"- Custom instructions: \"{personality['custom_instructions']}\"")
    lines.append("")

    # --- Rolling summary of earlier conversation (Tier 2 continuity) --------
    # Deterministic injection: the consolidated "since <checkpoint>" narrative
    # is always present regardless of semantic-retrieval luck. Fail-open.
    try:
        thread = memory_manager.load_conversation_thread()
        rollup_summary = thread.get("summary") or ""
        if rollup_summary:
            lines.append("### 📜 Earlier Conversation (rolling summary)")
            since_raw = thread.get("since")
            if since_raw:
                try:
                    since_fmt = datetime.fromisoformat(str(since_raw)).strftime("%b %d")
                except ValueError:
                    since_fmt = str(since_raw)[:10]
                lines.append(f"- Since {since_fmt}: {rollup_summary}")
            else:
                lines.append(f"- {rollup_summary}")
            lines.append("")
    except Exception as exc:
        print(f"[dna_context] rolling summary read failed: {exc}")

    lines.append("### 💬 Conversation So Far (this session)")
    history = (conversation_history or [])[-TRANSCRIPT_RENDER_TURNS:]
    if history:
        lines.extend(_render_transcript(history))
    else:
        lines.append("- (session just started — no prior turns)")
    lines.append("")

    lines.append(f"### 💬 {USER_NAME}'s Message")
    lines.append(user_input or "(autonomous wake-up — no user message)")

    return "\n".join(lines)

