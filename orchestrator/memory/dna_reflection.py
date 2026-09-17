"""
orchestrator/memory/dna_reflection.py

Memory Reflection Node (dna_memory_redesign_v2.md §5).

After every conversation turn, a lightweight async LLM call reviews the
exchange and proposes DNA memory updates: CREATE / CONFIRM / REVISE.
Runs in the background after the response is sent — the user never waits.

Wiring (Phase 4): this is the only writer of new DNA memories; the reasoner
reads them via dna_context.build_dna_context. Kill switch: DNA_REFLECTION_ENABLED=0.

The confirmation discipline (§4.3) is enforced structurally:
  - Confirmations must declare an evidence class (user_affirmed | user_action)
  - Only user_affirmed unlocks the confidence ceiling (by_user=True)
  - user_action is behavioral evidence → normal capped growth
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from orchestrator.memory.dna_store import (
    DNAMemoryRecord,
    DNAMemoryStore,
    get_dna_store,
)
from orchestrator.tracing import component_span

# ---------------------------------------------------------------------------
# Reflection output schema (§5.1)
# ---------------------------------------------------------------------------

MemoryType = Literal[
    "fact", "observation", "insight", "preference",
    "goal", "reflection", "context",
]


class NewMemory(BaseModel):
    content: str
    memory_type: MemoryType
    confidence: float = 0.4
    # "Distinguish between what the user SAID vs what you INFERRED" (§5.2):
    source: Literal["user_stated", "mentor_inferred"] = "mentor_inferred"
    tags: list[str] = Field(default_factory=list)
    due_at: Optional[str] = None   # ISO datetime for time-sensitive memories


class Confirmation(BaseModel):
    memory_id: str
    evidence_class: Literal["user_affirmed", "user_action"]
    # The §4.3 discipline in the schema: the LLM must declare WHICH kind of
    # evidence it saw. "Conversation was consistent with it" is not legal.


class MemoryRevision(BaseModel):
    memory_id: str
    new_content: str
    new_confidence: Optional[float] = None
    reason: str
    contradicts_prior: bool = False


class MemoryReflection(BaseModel):
    new_memories: list[NewMemory] = Field(default_factory=list)
    confirmations: list[Confirmation] = Field(default_factory=list)
    revisions: list[MemoryRevision] = Field(default_factory=list)
    validation_candidates: list[str] = Field(default_factory=list)
    discovered_facets: list[str] = Field(
        default_factory=list,
        description=(
            "Ids from the DISCOVERY FACETS list that this turn meaningfully "
            "advanced — only when something real about that facet was learned."
        ),
    )
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Reflection prompt (v1 §4.2 base + v2 §5.2 confirmation discipline
# + discovery-facet tracking + emotionally-weighted sentence style)
# ---------------------------------------------------------------------------

def _discovery_facet_block() -> str:
    """DISCOVERY FACETS prompt section, generated from the single source of
    truth in orchestrator/cognition/onboarding.py (never hand-duplicated)."""
    from orchestrator.cognition.onboarding import DISCOVERY_FACETS

    lines = [
        "== DISCOVERY FACETS (early-relationship tracking) ==",
        "The mentor is still learning Nik's present life through conversation.",
        "If this turn meaningfully revealed information about any facet below,",
        "list its id in discovered_facets. Only when something real was learned —",
        "small talk alone advances nothing.",
    ]
    lines += [f"  - {fid}: {desc}" for fid, desc in DISCOVERY_FACETS.items()]
    return "\n".join(lines)

MEMORY_REFLECTION_PROMPT = """You are the memory system for a personal AI mentor.
A conversation just happened. Your job is to decide what, if anything, is worth
remembering for future conversations.

== CURRENT MEMORIES THAT WERE RETRIEVED FOR THIS TURN ==
{retrieved_memories}

== THE CONVERSATION ==
User: {user_input}
Mentor: {mentor_response}
Time: {timestamp}

== INSTRUCTIONS ==

Review the conversation and output memory updates. You can:

1. CREATE new memories — things worth remembering for future conversations.
   - New facts the user shared (goals, deadlines, preferences)
   - Behavioral observations (patterns you noticed in how they talk/act)
   - Emotional signals (frustration, excitement, avoidance)
   - Insights connecting multiple past observations
   - The user's own realizations or reflections
   - Set source="user_stated" for things the user SAID directly,
     source="mentor_inferred" for anything you inferred.
   - If a memory is time-sensitive (interview, deadline, event), set due_at
     to its ISO datetime.

2. CONFIRM existing memories — only with real evidence (see discipline below).
   Reference the memory IDs from the retrieved list.

3. REVISE existing memories — evidence changed or the user corrected something.
   Provide the memory ID, new content, and why it changed.
   Set contradicts_prior=true when the user corrects an existing belief.

== HOW TO WRITE A MEMORY (style — strict) ==
Every memory is 1-2 natural, emotionally-honest sentences — the way a caring
mentor would recount it. Capture stance and feeling, not just the bare fact.
  GOOD: "Nik has been mass-applying for 3 months with zero callbacks, and it's
        wearing him down — he said starting to upskill 'feels pointless'."
  GOOD: "Nik lights up when he talks about robotics; he lost the whole evening
        to his orchestrator project without noticing the time."
  BAD:  "Job search duration: 3 months."   (a database field, not a memory)
  BAD:  "User is demotivated."             (clinical — loses what matters)
- Write in third person ("Nik ..."), never "the user".
- Keep his own charged words in quotes when they carry the emotion.
- Never manufacture drama: a flat, routine turn earns a plain factual sentence
  or no memory at all. Emotional weight must come from what HE expressed.

== CONFIRMATION DISCIPLINE (STRICT) ==
- CONFIRM a memory only when the USER explicitly affirmed it
  (evidence_class="user_affirmed"), or their stated action unambiguously
  demonstrates it (evidence_class="user_action").
- NEVER confirm because the conversation is merely "consistent with" a
  memory. Consistency with your own prior inference is not evidence.
- If the user corrects or refines an existing memory, use REVISE with
  contradicts_prior=true — do not CREATE a new memory and do not CONFIRM
  the old one.
- If you created or strengthened an inference this turn, add its ID to
  validation_candidates so the mentor can ask the user about it later.

{discovery_facets}

== RULES ==
- Be SELECTIVE. Most turns yield 0-1 new memories. Don't create noise.
- A memory should change how you'd respond in a FUTURE conversation.
  If it wouldn't, don't store it.
- Never store: passwords, medical details, exact financial figures.
- For observations, note the evidence: "Nik skipped System Design for the
  4th time this week" not just "Nik avoids System Design".
- Routine interactions ("Nik asked for today's schedule") → no memory.
- Temporary one-off emotional states → no memory.
- Raw schedule data → no memory (it lives in structured tables).

== TEACHING-OWNERSHIP MEMORIES (teach-while-building) ==
When this turn was a BUILDING/DEBUGGING/CODE session (mentor responded through
the code-explorer / scaffold flow), record WHO owned each significant piece:
- If Nik WROTE code (learner_writes / transfer task), create a memory tagged
  "owned" — e.g. "Owner writing success: Nik wired the retry logic on his own
  after a scaffold session." That memory drives the next session's lane choice.
- If the mentor WROTE it (mentor_writes / worked example), create a memory
  tagged "watched" — e.g. "Nik watched a worked example of a vector-store
  layer; hasn't built one solo yet."
- Welcome STYLE tags: "owned" / "watched" alongside the memory_type. These tags
  are what the code-explorer scaffold reads to choose the writing_split next
  time ("last time you watched the rate-limiter — today you try it first").
- Keep it to one ownership memory per meaningful piece/session, never one per
  line — noise defeats the purpose.
- Never fabricate ownership. If the session had no real building, no ownership
  memory.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_retrieved(memories: list[DNAMemoryRecord]) -> str:
    """Render retrieved memories with IDs so the LLM can reference them."""
    if not memories:
        return "(no memories stored yet — this is a fresh memory store)"
    lines = []
    for m in memories:
        confirmed = "user-confirmed" if m.user_confirmed else "unconfirmed"
        lines.append(
            f"- [id={m.id}] ({m.memory_type}, conf {m.confidence:.2f}, {confirmed}) {m.content}"
        )
    return "\n".join(lines)


def _parse_due_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _gather_context_memories(store: DNAMemoryStore, user_input: str, top_k: int = 8) -> list[DNAMemoryRecord]:
    """Retrieve the memories the reflection LLM reasons over (semantic + deterministic)."""
    seen: set[str] = set()
    gathered: list[DNAMemoryRecord] = []
    try:
        det = store.get_deterministic()
        for m in det["due"] + det["core"]:
            if m.id not in seen:
                seen.add(m.id)
                gathered.append(m)
        for m in store.retrieve(user_input, top_k=top_k):
            if m.id not in seen:
                seen.add(m.id)
                gathered.append(m)
    except Exception as exc:
        print(f"[dna_reflection] context retrieval failed: {exc}")
    return gathered


# ---------------------------------------------------------------------------
# Apply a reflection to the store (§5 — per-op isolation, never raises)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Grounding guard (anti-hallucination, code-enforced)
# ---------------------------------------------------------------------------

# Words that actually demonstrate the user affirmed a belief. Used to verify a
# reflection's Confirmation(evidence_class="user_affirmed") against the user's
# actual words this turn — the LLM must not be able to fabricate consent.
_AFFIRMATION_TOKENS = (
    "yes", "yeah", "yep", "yup", "correct", "exactly", "right", "true",
    "agree", "agreed", "confirmed", "accurate", "spot on", "indeed",
    "makes sense", "that's right", "that is right", "you're right",
    "you are right", "i agree",
)

# Proper-noun artifacts of the mentor/user identity that are always allowed in
# memory content regardless of the user's exact words.
_KNOWN_IDENTITY_NOUNS = {"nik", "nikhil", "nik's", "mentor", "i", "ai"}

_STOP_WORDS = {
    "the", "and", "for", "not", "was", "are", "his", "her", "that", "this",
    "with", "from", "have", "been", "were", "what", "when", "where", "which",
    "there", "their", "about", "would", "could", "should", "your", "you're",
    "they", "them", "then", "than", "more", "some", "just", "well", "very",
    "has", "had", "but", "its", "it's", "our", "being", "because", "into",
    "doesn't", "don't", "can't", "feel", "feels", "felt", "going", "gonna",
    "want", "wants", "will", "shall", "also", "really",
}


def _proper_nouns(text: str) -> list[str]:
    """Capitalized words (len>=3) that look like proper nouns / entities."""
    return re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text or "")


def _significant_tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z][a-z0-9_]{2,}", (text or "").lower())
        if w not in _STOP_WORDS
    }


def _is_grounded_in(content: str, user_input: str) -> bool:
    """
    Deterministic check that a memory claiming to be USER-STATED is actually
    grounded in what the user said this turn.

    Downgrade triggers:
      1. The content introduces a proper noun / entity (e.g. "Google") that
         never appears in the user's message — the classic reflection
         fabrication (invented company/name/date).
      2. The content shares ZERO significant tokens with the user's message —
         no basis to call it "user stated".
    """
    user_lower = (user_input or "").lower()
    for w in _proper_nouns(content):
        wl = w.lower()
        if wl in _KNOWN_IDENTITY_NOUNS:
            continue
        # A capitalized proper noun that the user never said → unsupported.
        if wl not in user_lower:
            return False
    mem_tokens = _significant_tokens(content)
    if not mem_tokens:
        return True  # nothing meaningful to check
    return bool(mem_tokens & _significant_tokens(user_input))


def _has_affirmation(user_input: str) -> bool:
    low = (user_input or "").lower()
    return any(tok in low for tok in _AFFIRMATION_TOKENS)


def _sanitize_reflection(
    reflection: MemoryReflection,
    user_input: str,
) -> MemoryReflection:
    """
    Code-level grounding guard for reflection output.

    The reflection LLM decides source/evidence-class; this guard makes sure it
    cannot silently upgrade a hallucination into a trusted fact:
      - A `user_stated` memory with no grounding in the user's actual words is
        downgraded to `mentor_inferred` (0.4 confidence, unconfirmed, and its
        due_at cleared so a fabricated deadline never becomes a deterministic
        context injection).
      - A `user_affirmed` confirmation with no affirmation in the user's words
        is downgraded to `user_action` (capped growth, no ceiling unlock).
    Revisions are never downgraded (they carry the user's spoken content).
    """
    if not (reflection.new_memories or reflection.confirmations):
        return reflection

    new_memories: list[NewMemory] = []
    for mem in reflection.new_memories or []:
        if mem.source == "user_stated" and not _is_grounded_in(mem.content, user_input):
            print(
                f"[dna_reflection] grounding guard: 'user_stated' memory not grounded "
                f"in user words → downgraded to mentor_inferred: {mem.content[:100]!r}"
            )
            mem = mem.model_copy(update={
                "source": "mentor_inferred",
                "confidence": min(float(mem.confidence or 1.0), 0.4),
                "due_at": None,
                # Marker the apply layer must honor: never let this statement
                # merge into / inherit the trust of a pre-existing memory.
                "tags": list(mem.tags or []) + ["grounding:downgraded"],
            })
        new_memories.append(mem)

    confirmations: list[Confirmation] = []
    for conf in reflection.confirmations or []:
        if conf.evidence_class == "user_affirmed" and not _has_affirmation(user_input):
            print(
                f"[dna_reflection] grounding guard: 'user_affirmed' confirm without "
                f"user affirmation → downgraded to user_action: {conf.memory_id}"
            )
            conf = conf.model_copy(update={"evidence_class": "user_action"})
        confirmations.append(conf)

    return reflection.model_copy(update={
        "new_memories": new_memories,
        "confirmations": confirmations,
    })


def apply_reflection(store: DNAMemoryStore, reflection: MemoryReflection) -> dict[str, Any]:
    """
    Persist a MemoryReflection. Each operation is isolated: one bad ID or
    payload must not sink the rest of the batch.
    """
    report: dict[str, Any] = {"created": [], "confirmed": [], "revised": [], "errors": []}

    for mem in reflection.new_memories:
        try:
            downgraded = "grounding:downgraded" in (mem.tags or [])
            tags = [t for t in (mem.tags or []) if t != "grounding:downgraded"]
            record = store.upsert_with_checks(
                mem.content,
                memory_type=mem.memory_type,
                source=mem.source,
                tags=tags,
                confidence=mem.confidence,
                due_at=_parse_due_at(mem.due_at),
                force_distinct=downgraded,
            )
            report["created"].append(record.id)
        except Exception as exc:
            report["errors"].append(f"create failed: {exc}")

    for conf in reflection.confirmations:
        try:
            store.confirm_memory(
                conf.memory_id,
                by_user=(conf.evidence_class == "user_affirmed"),
            )
            report["confirmed"].append(conf.memory_id)
        except Exception as exc:
            report["errors"].append(f"confirm {conf.memory_id} failed: {exc}")

    for rev in reflection.revisions:
        try:
            record = store.revise_memory(
                rev.memory_id,
                rev.new_content,
                reason=rev.reason,
                contradiction=rev.contradicts_prior,
                new_confidence=rev.new_confidence,
            )
            report["revised"].append(record.id)
        except Exception as exc:
            report["errors"].append(f"revise {rev.memory_id} failed: {exc}")

    return report


# ---------------------------------------------------------------------------
# Run one reflection cycle
# ---------------------------------------------------------------------------

@component_span("dna_reflection", tags=["component:dna_reflection"])
def run_reflection(
    user_input: str,
    mentor_response: str,
    store: Optional[DNAMemoryStore] = None,
    llm: Any = None,
    memory_manager: Any = None,
) -> dict[str, Any]:
    """
    Reflect on one conversation turn and persist the resulting memory ops.

    Args:
        user_input:      what Nik said this turn
        mentor_response: what the mentor replied
        store:           DNAMemoryStore (created if not given)
        llm:             injectable for tests; defaults to the reasoning tier
        memory_manager:  injectable for tests; used to advance discovery-facet
                         coverage when the reflection reports discovered_facets
    Returns:
        The apply_reflection report dict.
    """
    store = store or get_dna_store()

    retrieved = _gather_context_memories(store, user_input)
    prompt = MEMORY_REFLECTION_PROMPT.format(
        retrieved_memories=_format_retrieved(retrieved),
        user_input=user_input,
        mentor_response=mentor_response,
        timestamp=datetime.now(timezone.utc).isoformat(),
        discovery_facets=_discovery_facet_block(),
    )

    if llm is None:
        from orchestrator.llm import get_reflection_llm
        llm = get_reflection_llm(temperature=0.1)

    try:
        structured = llm.with_structured_output(MemoryReflection)
        raw = structured.invoke([("system", prompt)])
        if isinstance(raw, MemoryReflection):
            reflection = raw
        elif isinstance(raw, dict):
            reflection = MemoryReflection(**raw)
        else:
            raise TypeError(f"Unexpected reflection output type: {type(raw)}")
    except Exception as exc:
        print(f"[dna_reflection] LLM reflection failed: {exc}")
        return {"created": [], "confirmed": [], "revised": [], "errors": [str(exc)]}

    report = apply_reflection(store, _sanitize_reflection(reflection, user_input))

    # Discovery-facet coverage (onboarding): advance the curiosity agenda.
    if reflection.discovered_facets:
        try:
            from orchestrator.cognition.onboarding import mark_facets_covered
            from orchestrator.memory.store import get_memory_manager

            mm = memory_manager or get_memory_manager()
            covered = mark_facets_covered(mm, reflection.discovered_facets)
            report["facets_covered"] = sorted(covered)
            print(f"[dna_reflection] discovery facets advanced: {reflection.discovered_facets}")
        except Exception as exc:
            print(f"[dna_reflection] facet coverage update failed: {exc}")

    if reflection.validation_candidates:
        print(f"[dna_reflection] validation candidates queued: {reflection.validation_candidates}")
    print(
        f"[dna_reflection] turn reflected — "
        f"{len(report['created'])} created, {len(report['confirmed'])} confirmed, "
        f"{len(report['revised'])} revised, {len(report['errors'])} errors"
    )
    return report


# ---------------------------------------------------------------------------
# Async entry point (§5.1 — step 5 runs in the background)
# ---------------------------------------------------------------------------

def reflection_enabled() -> bool:
    return os.getenv("DNA_REFLECTION_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


def reflect_on_turn_async(user_input: str, response_text: str) -> None:
    """
    Fire-and-forget reflection for one conversation turn.
    Daemon thread, never raises — a reflection failure must never surface
    to the user-facing turn.
    """
    if not reflection_enabled():
        return
    if not user_input or not response_text:
        return

    def _bg() -> None:
        try:
            run_reflection(user_input, response_text)
        except Exception as exc:
            print(f"[dna_reflection] background reflection failed: {exc}")

    threading.Thread(target=_bg, daemon=True).start()

