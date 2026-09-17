"""
orchestrator/memory/identity.py

P1 — IDENTITY. "Who is he, and what does he want?"

Purpose, not table
------------------
Identity is the memory that is true about the person regardless of when it is
asked: who he is, what he can do, what he is aiming at, how he wants to be
spoken to. It moves slowly, it is high-confidence, and — critically — it must
never depend on retrieval luck. A mentor that forgets your name because a cosine
distance landed on the wrong side of a threshold is not a mentor.

Two sources, one view
---------------------
    profile_facts   structured, typed, written by code paths
    dna_memory      organic, natural language, written by reflection

Both are P1; they are not alternatives. That is the two-consumer boundary
(docs/Key Design Decisions.md #3): typed values feed code, free text feeds
voice. This module reads BOTH and keeps provenance attached, so the caller can
always tell a user-stated fact from a mentor inference (0.4, capped at 0.6).

Deterministic by construction
-----------------------------
Selected by memory_type and confidence, never by similarity — the §7.1 Layer 1
rule from dna_memory_redesign_v2.md. `get_deterministic()` already owns the
due / core / pending-validation split; this module composes it with the profile
snapshot into the shape an identity question actually needs.

Scope note (why the key set differs from the sidecar's)
-------------------------------------------------------
`api/tools.py::DEFAULT_PROFILE_KEYS` mixes two purposes: `todays_plan` and
`learning_streak_days` are *present state* (P2), not identity. They are
deliberately excluded here and belong to `state.py`. Splitting them is the point
of a purpose-shaped read: one question, one answer.

Fail-open, like the rest of the package: a broken store degrades to a partial
view and names what failed, rather than raising — a mentor should still be able
to say what it knows.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# Structured profile keys that are IDENTITY. Mirrors the identity half of
# api/tools.py::DEFAULT_PROFILE_KEYS — the P2 half lives in state.py.
IDENTITY_PROFILE_KEYS: tuple[str, ...] = (
    "full_name",
    "current_role",
    "employment_status",
    "target_roles",
    "target_locations",
    "long_term_goal",
    "short_term_goal",
    "active_learning_path",
    "relationship_phase",
)

# Memory types that constitute identity: stable claims about the person.
# `observation`/`insight`/`reflection`/`context` are patterns and history (P3/P4).
IDENTITY_TYPES: tuple[str, ...] = ("fact", "goal", "preference")


class IdentityFact(BaseModel):
    """One thing believed about him, with the provenance needed to trust it.

    `source` + `confidence` + `user_confirmed` are not decoration: they are the
    difference between "he told me" and "I guessed", and the mentor is expected
    to speak differently about each.
    """

    content: str
    memory_type: str
    source: str
    confidence: float
    user_confirmed: bool
    memory_id: str | None = None
    due_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)

    def provenance(self) -> str:
        """`[fact · user-stated · conf 0.95 · user-confirmed]`.

        Mirrors `dna_context._provenance_label` deliberately rather than
        importing it — this module must not depend on a Gen-2 module that is
        slated for retirement. When `dna_context.py` is dissolved this becomes
        the canonical implementation.
        """
        confirmed = "user-confirmed" if self.user_confirmed else "unconfirmed"
        return (
            f"[{self.memory_type} · {self.source.replace('_', '-')} "
            f"· conf {self.confidence:.2f} · {confirmed}]"
        )


class IdentitySnapshot(BaseModel):
    """P1 — the answer to "who is he?".

    Every list is filled deterministically. `degraded` names any source that
    failed, so a partial answer is visibly partial instead of quietly thin.
    """

    profile: dict[str, Any] = Field(default_factory=dict)
    who: list[IdentityFact] = Field(default_factory=list)
    due: list[IdentityFact] = Field(default_factory=list)
    pending_validation: list[IdentityFact] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.profile or self.who or self.due or self.pending_validation)


# ---------------------------------------------------------------------------
# Store access — lazy, so importing the facade stays cheap
# ---------------------------------------------------------------------------


def _default_memory_manager() -> Any:
    from orchestrator.memory.store import MemoryManager

    return MemoryManager()


def _default_dna_store() -> Any:
    from orchestrator.memory.dna_store import DNAMemoryStore

    return DNAMemoryStore()


def _fact(record: Any) -> IdentityFact:
    """Coerce a DNAMemoryRecord into the identity read-model."""
    return IdentityFact(
        content=record.content,
        memory_type=record.memory_type,
        source=record.source,
        confidence=float(record.confidence),
        user_confirmed=bool(record.user_confirmed),
        memory_id=getattr(record, "id", None),
        due_at=getattr(record, "due_at", None),
        tags=list(getattr(record, "tags", None) or []),
    )


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


def read_identity(
    *,
    memory_manager: Any | None = None,
    dna_store: Any | None = None,
    limit: int = 40,
    include_profile: bool = True,
) -> IdentitySnapshot:
    """Read P1: who he is, what he wants, and what is time-sensitive.

    Stores are injectable so the read is testable without a database and so a
    caller can pass a session-scoped instance.

    Ordering is deterministic: most-trusted first (confidence desc, then type,
    then content), so two identical calls produce an identical block — no
    retrieval luck, no ordering jitter.
    """
    snapshot = IdentitySnapshot()
    cap = max(1, int(limit))

    # --- Structured half: profile_facts (typed values feed code) ------------
    if include_profile:
        try:
            mm = memory_manager if memory_manager is not None else _default_memory_manager()
            facts = mm.load_profile_facts(list(IDENTITY_PROFILE_KEYS))
            if not facts:
                # A fresh install stores no identity key yet. Answer with what
                # exists rather than reporting an empty identity.
                facts = mm.load_profile_facts()
            snapshot.profile = {k: v for k, v in dict(facts or {}).items() if v not in (None, "", [])}
        except Exception as exc:
            snapshot.degraded.append(f"profile_facts: {exc}")

    # --- Organic half: dna_memory (natural language feeds voice) -----------
    store: Any | None = None
    try:
        store = dna_store if dna_store is not None else _default_dna_store()
        for memory_type in IDENTITY_TYPES:
            for record in store.list_memories(memory_type=memory_type, limit=cap):
                snapshot.who.append(_fact(record))
        snapshot.who.sort(key=lambda f: (-f.confidence, f.memory_type, f.content))
        snapshot.who = snapshot.who[:cap]
    except Exception as exc:
        snapshot.degraded.append(f"dna_memory: {exc}")
        store = None

    # --- Time-sensitive, never retrieval-gated (§7.1 Layer 1) -------------
    if store is not None:
        try:
            det = store.get_deterministic() or {}
            snapshot.due = [_fact(r) for r in det.get("due") or []]
            snapshot.pending_validation = [_fact(r) for r in det.get("pending_validation") or []]
        except Exception as exc:
            snapshot.degraded.append(f"dna_deterministic: {exc}")

    snapshot.counts = {
        "profile_facts": len(snapshot.profile),
        "who": len(snapshot.who),
        "due": len(snapshot.due),
        "pending_validation": len(snapshot.pending_validation),
    }
    return snapshot


# ---------------------------------------------------------------------------
# Rendering — the compact prompt block
# ---------------------------------------------------------------------------


def render_identity(snapshot: IdentitySnapshot) -> str:
    """Compact text for a prompt. `""` when nothing is known.

    Kept small on purpose: this is the *identity* block, not a memory dump.
    Patterns and history belong to their own purposes.
    """
    if snapshot.is_empty():
        return ""

    lines = ["WHO HE IS (from memory — never from assumption)"]

    for key, value in snapshot.profile.items():
        if key == "active_learning_path" and isinstance(value, dict):
            value = value.get("title")
            if value in (None, ""):
                continue
        rendered = value if isinstance(value, str) else json.dumps(value, default=str)
        lines.append(f"- {key.replace('_', ' ')}: {rendered}")

    if snapshot.who:
        lines.append("Known about him:")
        for fact in snapshot.who:
            lines.append(f"- {fact.content} {fact.provenance()}")

    if snapshot.due:
        lines.append("Time-sensitive (do not let these lapse):")
        for fact in snapshot.due:
            due = fact.due_at.strftime("%Y-%m-%d") if fact.due_at else "unknown"
            lines.append(f"- {fact.content} (due {due}) {fact.provenance()}")

    if snapshot.pending_validation:
        lines.append("Awaiting his word — do NOT state these as fact:")
        for fact in snapshot.pending_validation:
            lines.append(f"- {fact.content} {fact.provenance()}")

    if snapshot.degraded:
        lines.append("Unreadable right now: " + "; ".join(snapshot.degraded))

    return "\n".join(lines)
