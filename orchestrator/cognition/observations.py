"""
orchestrator/cognition/observations.py

Weekly data-derived observation job (dna_memory_redesign_v2.md §13.5, §12).

Once a week (piggybacking the Sunday consolidation job), mine the trailing
14 days of `schedule_events` for behavioral *patterns* and write them as DNA
memories with source="data_derived":

  - Patterns only, never point-in-time metrics (§15). "Completes morning
    blocks at 85%" is a pattern; "today's completion is 40%" is a point
    metric — those stay computed on demand in cognition/metrics.py.
  - All statistics are computed in code (deterministic guardrails). The only
    LLM use is the §6 compare call inside upsert_with_checks, and only when
    a near-duplicate pattern already exists in the store.
  - Refresh semantics (§4.2/§6): a stable pattern re-upserts as SAME →
    confirm (+0.05 toward the 0.95 data_derived ceiling, fresh
    last_confirmed); shifted numbers come back REFINES → the old row is
    superseded with the new figures; a pattern that stops being true simply
    stops being refreshed, and the weekly decay pass fades it (§4.4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager

# ---------------------------------------------------------------------------
# Guardrail constants
# ---------------------------------------------------------------------------

WINDOW_DAYS = 14          # trailing observation window (weekly job → 2 weeks)
MIN_EVENTS = 8            # cold-start guard: below this, derive nothing
MIN_BUCKET_EVENTS = 3     # a bucket/category needs this many events to be judged
SIGNIFICANCE_GAP = 0.25   # min completion-rate gap that counts as a pattern
AFFINITY_GAP = 0.15       # min lead over the overall rate to be a "strong area"
AVOIDANCE_MAX_RATE = 0.40 # at/below this completion, a lagging category = avoidance
TREND_DELTA = 0.15        # min week-over-week movement to call a trend
POSTPONE_SHARE = 0.5      # cancelled+postponed share that signals over-planning
MAX_CATEGORY_PATTERNS = 2 # keep memory lean — only the strongest signals

# ---------------------------------------------------------------------------
# Pattern candidates
# ---------------------------------------------------------------------------


@dataclass
class PatternCandidate:
    """One detected behavioral pattern, ready to upsert as a DNA memory."""

    key: str            # stable-ish identity, e.g. "category_avoidance:system-design"
    content: str        # natural-language pattern statement (templated, 3rd person)
    evidence: dict[str, Any] = field(default_factory=dict)  # the numbers behind it


def _rate(events: list[dict[str, Any]]) -> float:
    """Completion rate: completed / total (consistent with cognition/metrics.py)."""
    if not events:
        return 0.0
    return sum(1 for e in events if e.get("status") == "completed") / len(events)


def _pct(rate: float) -> str:
    return f"{round(rate * 100)}%"


def _parse_start(event: dict[str, Any]) -> datetime | None:
    raw = event.get("start_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _time_bucket(start: datetime) -> str:
    hour = start.hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _by_category(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        groups.setdefault(e.get("category") or "other", []).append(e)
    return groups


# ---------------------------------------------------------------------------
# Detectors — pure functions, no I/O, no LLM
# ---------------------------------------------------------------------------


def detect_time_of_day(events: list[dict[str, Any]]) -> list[PatternCandidate]:
    """Strong-vs-weak time-of-day window (e.g. mornings 100% vs evenings 20%)."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        start = _parse_start(e)
        if start:
            buckets.setdefault(_time_bucket(start), []).append(e)

    qualified = {b: evs for b, evs in buckets.items() if len(evs) >= MIN_BUCKET_EVENTS}
    if len(qualified) < 2:
        return []

    rates = {b: _rate(evs) for b, evs in qualified.items()}
    best = max(rates, key=lambda b: rates[b])
    worst = min(rates, key=lambda b: rates[b])
    gap = rates[best] - rates[worst]
    if gap < SIGNIFICANCE_GAP:
        return []

    content = (
        f"Nik completes about {_pct(rates[best])} of {best} blocks versus "
        f"{_pct(rates[worst])} of {worst} blocks over the past two weeks — "
        f"{best}s are his strongest window."
    )
    return [PatternCandidate(
        key=f"time_of_day:{best}-strong",
        content=content,
        evidence={"rates": {b: round(r, 3) for b, r in rates.items()},
                  "counts": {b: len(evs) for b, evs in qualified.items()}},
    )]


def detect_category_patterns(events: list[dict[str, Any]]) -> list[PatternCandidate]:
    """Category-level avoidance (far below overall) or strength (far above)."""
    overall = _rate(events)
    candidates: list[tuple[float, PatternCandidate]] = []

    for category, cat_events in _by_category(events).items():
        if len(cat_events) < MIN_BUCKET_EVENTS:
            continue
        rate = _rate(cat_events)
        done = sum(1 for e in cat_events if e.get("status") == "completed")
        total = len(cat_events)

        if rate <= AVOIDANCE_MAX_RATE and overall - rate >= SIGNIFICANCE_GAP:
            candidates.append((overall - rate, PatternCandidate(
                key=f"category_avoidance:{category}",
                content=(
                    f"Nik has completed only {_pct(rate)} of his {category} blocks "
                    f"over the past two weeks ({done} of {total}), well below his "
                    f"{_pct(overall)} overall rate — possible avoidance of {category}."
                ),
                evidence={"category": category, "rate": round(rate, 3),
                          "overall": round(overall, 3), "done": done, "total": total},
            )))
        elif rate >= 0.85 and rate - overall >= AFFINITY_GAP:
            candidates.append((rate - overall, PatternCandidate(
                key=f"category_strength:{category}",
                content=(
                    f"Nik completes {_pct(rate)} of his {category} blocks over the "
                    f"past two weeks ({done} of {total}) — a reliably strong area."
                ),
                evidence={"category": category, "rate": round(rate, 3),
                          "overall": round(overall, 3), "done": done, "total": total},
            )))

    # Strongest signals first, capped — the rest is noise for the reasoner.
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [cand for _, cand in candidates[:MAX_CATEGORY_PATTERNS]]


def detect_block_length(events: list[dict[str, Any]]) -> list[PatternCandidate]:
    """Short (≤45m) vs long (≥60m) block completion contrast."""
    short = [e for e in events if (e.get("duration_min") or 30) <= 45]
    long_ = [e for e in events if (e.get("duration_min") or 30) >= 60]
    if len(short) < MIN_BUCKET_EVENTS or len(long_) < MIN_BUCKET_EVENTS:
        return []

    rate_short, rate_long = _rate(short), _rate(long_)
    if abs(rate_short - rate_long) < SIGNIFICANCE_GAP:
        return []

    if rate_short > rate_long:
        better, worse = ("short (≤45-minute)", _pct(rate_short)), ("long (60+ minute)", _pct(rate_long))
        key = "block_length:short-strong"
    else:
        better, worse = ("long (60+ minute)", _pct(rate_long)), ("short (≤45-minute)", _pct(rate_short))
        key = "block_length:long-strong"

    content = (
        f"Nik finishes about {better[1]} of {better[0]} blocks versus {worse[1]} "
        f"of {worse[0]} blocks over the past two weeks — {better[0]} blocks suit "
        f"him better."
    )
    return [PatternCandidate(
        key=key,
        content=content,
        evidence={"short_rate": round(rate_short, 3), "long_rate": round(rate_long, 3),
                  "short_count": len(short), "long_count": len(long_)},
    )]


def detect_weekly_trend(events: list[dict[str, Any]], now: datetime) -> list[PatternCandidate]:
    """Week-over-week completion movement within the window."""
    split = now - timedelta(days=7)
    prev, curr = [], []
    for e in events:
        start = _parse_start(e)
        if not start:
            continue
        (curr if start >= split else prev).append(e)

    if len(prev) < MIN_BUCKET_EVENTS or len(curr) < MIN_BUCKET_EVENTS:
        return []

    rate_prev, rate_curr = _rate(prev), _rate(curr)
    delta = rate_curr - rate_prev
    if abs(delta) < TREND_DELTA:
        return []

    direction = "rising" if delta > 0 else "declining"
    content = (
        f"Nik's completion is trending {direction}: {_pct(rate_curr)} over the "
        f"past week versus {_pct(rate_prev)} the week before."
    )
    return [PatternCandidate(
        key="weekly_trend",
        content=content,
        evidence={"prev_rate": round(rate_prev, 3), "curr_rate": round(rate_curr, 3),
                  "prev_count": len(prev), "curr_count": len(curr)},
    )]


def detect_postponement(
    events: list[dict[str, Any]],
    already_flagged: set[str],
) -> list[PatternCandidate]:
    """Categories where most blocks get cancelled/postponed (over-planning).

    Categories already flagged for avoidance are skipped — a cancel-heavy
    category is the same signal, and memory must stay lean.
    """
    candidates = []
    for category, cat_events in _by_category(events).items():
        if category in already_flagged or len(cat_events) < MIN_BUCKET_EVENTS:
            continue
        postponed = [e for e in cat_events if e.get("status") in ("cancelled", "postponed")]
        if len(postponed) >= 2 and len(postponed) / len(cat_events) >= POSTPONE_SHARE:
            candidates.append(PatternCandidate(
                key=f"postponement:{category}",
                content=(
                    f"Nik postponed or cancelled {len(postponed)} of {len(cat_events)} "
                    f"{category} blocks over the past two weeks — the plan may be "
                    f"over-ambitious for {category}."
                ),
                evidence={"category": category, "postponed": len(postponed),
                          "total": len(cat_events)},
            ))
    return candidates


# ---------------------------------------------------------------------------
# Candidate gathering (cold-start guard lives here)
# ---------------------------------------------------------------------------


def gather_pattern_candidates(
    events: list[dict[str, Any]],
    now: datetime,
) -> list[PatternCandidate]:
    """Run every detector over the window. The cold-start guard keeps thin
    weeks from producing noise."""
    if len(events) < MIN_EVENTS:
        return []

    candidates: list[PatternCandidate] = []
    candidates.extend(detect_time_of_day(events))

    category_candidates = detect_category_patterns(events)
    candidates.extend(category_candidates)
    flagged = {
        cand.key.split(":", 1)[1]
        for cand in category_candidates
        if cand.key.startswith("category_avoidance:")
    }

    candidates.extend(detect_postponement(events, flagged))
    candidates.extend(detect_block_length(events))
    candidates.extend(detect_weekly_trend(events, now))
    return candidates


# ---------------------------------------------------------------------------
# The weekly job entry point
# ---------------------------------------------------------------------------


def derive_weekly_observations(
    memory_manager: MemoryManager,
    dna_store: DNAMemoryStore,
    now: datetime | None = None,
    compare_fn: Callable[..., str] | None = None,
    merge_fn: Callable[..., str] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Mine the trailing window for patterns and upsert them as data_derived
    DNA memories. Called weekly from scheduler/consolidation_job.py.

    compare_fn / merge_fn / events are injectable for tests; in production
    compare/merge default to the reasoning-tier LLM helpers inside the
    store (§6) and events are queried from schedule_events.

    Returns a JSON-able report for the consolidation job log.
    """
    from sqlalchemy import text

    now = now or datetime.now(UTC)
    window_start = now - timedelta(days=WINDOW_DAYS)
    if events is None:
        events = memory_manager.get_schedule_events(start_date=window_start, end_date=now)

    report: dict[str, Any] = {
        "window_start": window_start.isoformat(),
        "window_days": WINDOW_DAYS,
        "events": len(events),
        "candidates": [],
        "created": [],
        "confirmed": [],
        "revised": [],
        "errors": [],
    }

    candidates = gather_pattern_candidates(events, now)
    report["candidates"] = [
        {"key": c.key, "content": c.content, "evidence": c.evidence} for c in candidates
    ]
    if not candidates:
        if len(events) < MIN_EVENTS:
            report["note"] = f"cold start: {len(events)} events < {MIN_EVENTS} — nothing derived"
        return report

    # Classify each upsert's outcome by diffing store state around the calls.
    # revision_targets must be read AFTER the loop — revisions made by this
    # run don't exist in a pre-run snapshot.
    before_ids = {m.id for m in dna_store.list_memories(active_only=False, limit=1000)}
    results: list[tuple[str, str]] = []  # (pattern key, returned memory id)

    for cand in candidates:
        try:
            record = dna_store.upsert_with_checks(
                cand.content,
                compare_fn=compare_fn,
                merge_fn=merge_fn,
                memory_type="observation",
                source="data_derived",
                tags=["data_derived", cand.key],
            )
            results.append((cand.key, record.id))
        except Exception as exc:
            report["errors"].append(f"{cand.key}: {exc}")

    with dna_store.SessionLocal() as session:
        revision_targets = {
            row[0]
            for row in session.execute(
                text("SELECT superseded_by FROM dna_memory WHERE superseded_by IS NOT NULL")
            )
        }

    for key, record_id in results:
        if record_id in before_ids:
            report["confirmed"].append(key)   # SAME → confirm (§4.2)
        elif record_id in revision_targets:
            report["revised"].append(key)     # REFINES/CONTRADICTS → supersede
        else:
            report["created"].append(key)     # fresh pattern

    return report

