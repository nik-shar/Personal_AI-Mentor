"""
orchestrator/memory/patterns.py

P4 — PATTERNS. "What does it mean?"

Purpose, not table
------------------
The first three purposes report: who he is, what is true now, what happened.
This one *interprets*. It is the only purpose whose content is derived rather than
recorded, and that changes what is safe to say about it — which is why every item
here carries where it came from.

Three sources, and the difference between them matters
------------------------------------------------------
    momentum        computed fresh, every call. Arithmetic, not belief.
                    Streak, completion rates, trend, blocks today (metrics.py).
    observations    STORED in dna_memory. Derived once, and then *earned*: a
                    pattern that keeps recurring gains confidence, one that
                    stops fades. These have provenance and a confidence score.
    detected        computed RIGHT NOW from the recent schedule window, by the
                    same detectors the weekly job uses — but not yet stored, not
                    yet re-observed, and therefore not yet believed.

That third one is the reason this module exists rather than just reading DNA
memory: without it the mentor's sense of his habits is only as fresh as the last
Sunday job. `derive_weekly_observations` writes; `gather_pattern_candidates` is a
pure function of events, so this read can surface a pattern the week it appears.

What it is NOT
--------------
Not the day (that is P2) and not the record (P3). Not drift either: `compute_drift`
needs an intended time allocation per goal, and nothing currently owns that
number — asserting a drift score against an invented intention would be exactly
the fabrication this package exists to avoid.

Every item is labelled with its source, and `detected` items are explicitly marked
as unconfirmed — the mentor must not describe a pattern it noticed once as
something it knows about him.

Fail-open, like the rest of the package: a broken source degrades and names
itself, so a partial picture of his habits is visibly partial.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.config import now_local

# How far back the fresh detectors look, and how many stored items to read.
DEFAULT_WINDOW_DAYS = 14
DEFAULT_LIMIT = 20

# Memory types that already hold derived understanding about him.
DERIVED_TYPES: tuple[str, ...] = ("observation", "insight")


class Momentum(BaseModel):
    """The arithmetic half — computed fresh, never stored, never estimated."""

    streak_days: int = 0
    completion_rate_today: float = 0.0
    completion_rate_7d: float = 0.0
    momentum_trend: str = "unknown"
    blocks_today: int = 0


class Pattern(BaseModel):
    """One behavioural pattern, with the provenance needed to speak about it.

    `source` is load-bearing: `"stored"` items have been re-observed and carry a
    confidence; `"detected-now"` items are a single observation of the current
    window and have none.
    """

    content: str
    source: str = Field(..., description="'stored' | 'detected-now'")
    key: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    memory_type: str | None = None
    confidence: float | None = None
    user_confirmed: bool | None = None
    memory_id: str | None = None

    def provenance(self) -> str:
        """`[observation · conf 0.72 · unconfirmed]` or `[detected now]`."""
        if self.source == "detected-now":
            return "[detected now — not yet re-observed]"
        parts = [self.memory_type or "observation"]
        if self.confidence is not None:
            parts.append(f"conf {self.confidence:.2f}")
        parts.append("user-confirmed" if self.user_confirmed else "unconfirmed")
        return "[" + " · ".join(parts) + "]"


class PatternsView(BaseModel):
    """P4 — the answer to "what does it mean?".

    `degraded` names any source that failed, so a partial reading of his habits
    is visibly partial rather than looking like he has no habits at all.
    """

    momentum: Momentum | None = None
    observations: list[Pattern] = Field(default_factory=list)
    detected: list[Pattern] = Field(default_factory=list)
    window_days: int = DEFAULT_WINDOW_DAYS
    counts: dict[str, int] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.momentum or self.observations or self.detected)


# ---------------------------------------------------------------------------
# Store access — lazy, so importing the facade stays cheap
# ---------------------------------------------------------------------------


def _default_memory_manager() -> Any:
    from orchestrator.memory.store import MemoryManager

    return MemoryManager()


def _default_dna_store() -> Any:
    from orchestrator.memory.dna_store import DNAMemoryStore

    return DNAMemoryStore()


def _stored(record: Any) -> Pattern:
    """Coerce a DNAMemoryRecord into a pattern read-model."""
    return Pattern(
        content=record.content,
        source="stored",
        memory_type=record.memory_type,
        confidence=float(record.confidence),
        user_confirmed=bool(record.user_confirmed),
        memory_id=getattr(record, "id", None),
    )


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


def read_patterns(
    *,
    memory_manager: Any | None = None,
    dna_store: Any | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> PatternsView:
    """Read P4: what his behaviour means, and how fresh each claim is.

    Stores are injectable so the read is testable without a database; `now` and
    `window_days` are injectable so "the last two weeks" means the same thing in a
    test as it does in production.
    """
    moment = now or now_local()
    window = max(1, int(window_days))
    cap = max(1, int(limit))
    view = PatternsView(window_days=window)

    manager: Any | None = None
    try:
        manager = memory_manager if memory_manager is not None else _default_memory_manager()
    except Exception as exc:
        view.degraded.append(f"memory_manager: {exc}")

    # --- The arithmetic half: momentum, recomputed on every call ----------
    if manager is not None:
        try:
            from orchestrator.cognition.metrics import compute_momentum

            raw = compute_momentum(manager) or {}
            view.momentum = Momentum(
                streak_days=int(raw.get("streak_days") or 0),
                completion_rate_today=float(raw.get("completion_rate_today") or 0.0),
                completion_rate_7d=float(raw.get("completion_rate_7d") or 0.0),
                momentum_trend=str(raw.get("momentum_trend") or "unknown"),
                blocks_today=int(raw.get("blocks_today") or 0),
            )
        except Exception as exc:
            view.degraded.append(f"momentum: {exc}")

    # --- Detected now: the SAME detectors the weekly job runs -------------
    # `gather_pattern_candidates` is a pure function of events, so this surfaces
    # a pattern the week it appears rather than the week after the job runs.
    if manager is not None:
        try:
            from orchestrator.cognition.observations import gather_pattern_candidates

            since = moment - timedelta(days=window)
            events = manager.get_schedule_events(start_date=since, end_date=moment) or []
            for candidate in gather_pattern_candidates(events, moment):
                view.detected.append(
                    Pattern(
                        content=candidate.content,
                        source="detected-now",
                        key=candidate.key,
                        evidence=dict(candidate.evidence or {}),
                    )
                )
        except Exception as exc:
            view.degraded.append(f"detected_patterns: {exc}")

    # --- Stored: what has actually been earned ----------------------------
    try:
        store = dna_store if dna_store is not None else _default_dna_store()
        for memory_type in DERIVED_TYPES:
            for record in store.list_memories(memory_type=memory_type, limit=cap):
                view.observations.append(_stored(record))
        view.observations.sort(key=lambda item: (-(item.confidence or 0.0), item.content))
        view.observations = view.observations[:cap]
    except Exception as exc:
        view.degraded.append(f"stored_patterns: {exc}")

    view.counts = {
        "observations": len(view.observations),
        "detected": len(view.detected),
        "has_momentum": int(view.momentum is not None),
    }
    return view


# ---------------------------------------------------------------------------
# Rendering — the compact prompt block
# ---------------------------------------------------------------------------


def render_patterns(view: PatternsView, *, detected_lines: int = 8) -> str:
    """Compact text for a prompt. `""` when there is nothing derived yet."""
    if view.is_empty() and not view.degraded:
        return ""

    lines: list[str] = ["WHAT IT MEANS (derived — read the provenance before claiming it)"]

    if view.momentum:
        m = view.momentum
        lines.append(
            f"- Momentum, computed now: a {m.streak_days}-day streak · "
            f"{int(m.completion_rate_7d * 100)}% completed over 7 days ({m.momentum_trend}) · "
            f"{int(m.completion_rate_today * 100)}% today across {m.blocks_today} block(s)."
        )

    if view.observations:
        lines.append("Seen repeatedly — believed, with confidence:")
        for pattern in view.observations:
            lines.append(f"- {pattern.content} {pattern.provenance()}")

    if view.detected:
        lines.append(
            f"Noticed in the last {view.window_days} days — NOT yet re-observed, so do not "
            f"state these as known habits:"
        )
        for pattern in view.detected[: max(1, detected_lines)]:
            lines.append(f"- {pattern.content} {pattern.provenance()}")

    if view.degraded:
        lines.append("Unreadable right now: " + "; ".join(view.degraded))

    return "\n".join(lines)
