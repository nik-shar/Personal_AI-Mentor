"""
orchestrator/cognition/onboarding.py

Discovery mode (Notes.md Layer B + mentor_agent_guidelines.md §1).

The mentor starts knowing only Nik's name and the §1 anchor facts from
mentor_agent_guidelines.md. Everything else is learned through conversation.
Discovery mode turns that philosophy into a directed process:

  - DISCOVERY_FACETS — the fixed curiosity agenda: the ~10 things the mentor
    actively wants to learn about Nik's present life.
  - Coverage tracking — the reflection node reports which facets each turn
    meaningfully advanced (MemoryReflection.discovered_facets); covered facets
    persist in profile_facts ("system", "discovery_coverage"). That row is
    system infrastructure metadata, not user profile data.
  - Dynamic guidance — build_onboarding_guidance() renders what is still
    unknown into the reasoner's context document until coverage is sufficient.
  - First contact — before any session or memory exists, a special opener
    block: the mentor introduces itself and sets the relationship frame.

Exit criteria are coverage-based, not a raw count: discovery ends when every
facet is covered, OR >= FULL_COVERAGE_FRACTION of facets are covered AND at
least MIN_USER_STATED_MEMORIES conversational memories exist.

Detection is deliberately fail-open: any store error reads as "not
onboarding" (normal mode), never the other way around.
"""

from __future__ import annotations

from math import ceil
from typing import Any

from orchestrator.memory.dna_store import DNAMemoryStore
from orchestrator.memory.store import MemoryManager

# ---------------------------------------------------------------------------
# The curiosity agenda — what the mentor wants to learn about Nik's present
# ---------------------------------------------------------------------------

DISCOVERY_FACETS: dict[str, str] = {
    "present_situation":   "what his life looks like right now — work, the job hunt, study mix",
    "current_projects":    "what he's actively building or learning this month",
    "daily_routine":       "the shape of a normal day — when he wakes, works, rests",
    "energy_patterns":     "his best deep-work hours vs. when he crashes",
    "short_term_goals":    "what he wants to achieve in the next 1–3 months",
    "job_search_state":    "where the job hunt stands — applications, interviews, blockers",
    "learning_style":      "how he actually learns best (building first, reading, courses…)",
    "coaching_preference": "how he likes to be pushed — gentle accountability vs. hard challenge",
    "emotional_baseline":  "what's been weighing on him or energizing him lately",
    "wins_and_struggles":  "recent wins worth celebrating, recurring struggles worth watching",
}

COVERAGE_CATEGORY = "system"
COVERAGE_KEY = "discovery_coverage"

# Discovery ends when: every facet is covered, OR >= 70% of facets are covered
# AND at least this many conversational (user_stated) memories exist.
FULL_COVERAGE_FRACTION = 0.7
MIN_USER_STATED_MEMORIES = 12


# ---------------------------------------------------------------------------
# Coverage store (system metadata row in profile_facts)
# ---------------------------------------------------------------------------

def get_covered_facets(mm: MemoryManager) -> set[str]:
    """The set of discovery-facet ids covered so far (unknown ids ignored)."""
    raw = mm.get_profile_fact(COVERAGE_CATEGORY, COVERAGE_KEY) or []
    if not isinstance(raw, list):
        return set()
    return {f for f in raw if f in DISCOVERY_FACETS}


def mark_facets_covered(mm: MemoryManager, facets: list[str]) -> set[str]:
    """Union newly discovered facets into the coverage store. Returns the set."""
    valid = {f for f in facets if f in DISCOVERY_FACETS}
    if not valid:
        return get_covered_facets(mm)
    covered = get_covered_facets(mm) | valid
    mm.set_profile_fact(
        COVERAGE_CATEGORY, COVERAGE_KEY, sorted(covered), source="reflection",
    )
    return covered


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_onboarding_status(dna_store: DNAMemoryStore, mm: MemoryManager) -> dict[str, Any]:
    """
    Discovery-mode status snapshot. Fail-open: any error → active=False
    (normal mode), so a store outage never traps the mentor in onboarding.
    """
    try:
        user_stated_count = len(dna_store.list_memories(source="user_stated", limit=300))
        covered = get_covered_facets(mm)
        remaining = [f for f in DISCOVERY_FACETS if f not in covered]
        enough_facets = len(covered) >= ceil(FULL_COVERAGE_FRACTION * len(DISCOVERY_FACETS))
        done = (not remaining) or (
            enough_facets and user_stated_count >= MIN_USER_STATED_MEMORIES
        )
        try:
            has_past_session = mm.get_last_conversation_session() is not None
            session_count = mm.get_conversation_session_count()
        except Exception:
            has_past_session = True   # uncertain → don't claim first contact
            session_count = 1

        is_first_contact = user_stated_count == 0 and not has_past_session
        
        # Calculate relationship phase
        if is_first_contact:
            phase = "first_contact"
        elif user_stated_count < 12:
            phase = "getting_to_know"
        elif user_stated_count < 30 or not done:
            phase = "building_trust"
        elif user_stated_count < 50 or session_count < 20:
            phase = "established"
        else:
            phase = "deep_rapport"

        return {
            "active": not done,
            "first_contact": is_first_contact,
            "relationship_phase": phase,
            "covered": sorted(covered),
            "remaining": remaining,
            "user_stated_count": user_stated_count,
            "session_count": session_count,
        }
    except Exception as exc:
        print(f"[onboarding] status check failed ({exc}) — assuming normal mode.")
        return {
            "active": False, "first_contact": False, "relationship_phase": "established",
            "covered": [], "remaining": [], "user_stated_count": -1, "session_count": -1
        }


# ---------------------------------------------------------------------------
# Guidance blocks rendered into the reasoner's context document
# ---------------------------------------------------------------------------

FIRST_CONTACT_GUIDANCE = """\
### 🌱 First Contact — your very first conversation with Nik

All you know is his name and the anchor facts he wrote for you. Everything
else, you learn by talking. This first impression sets the whole relationship:
- If you haven't yet in this conversation, introduce yourself briefly: his
  mentor-companion who learns who he is through conversation — and who will
  sometimes check "did I get that right?" rather than assume.
- Be warm, genuinely curious, unhurried. No plans, no schedules, no
  assessments, no advice he didn't ask for.
- Ask ONE natural opener about his present situation — what his days look
  like right now, what's on his plate, what's on his mind.
- Then listen. One question per turn at most, always tied to what he just
  said. Discovery happens across many conversations, never in one interrogation.\
"""


def build_onboarding_guidance(status: dict[str, Any]) -> str:
    """
    The discovery-mode block for the context document. Shows the reasoner
    exactly which facets are still unknown so curiosity has direction, and
    includes the relationship phase.
    """
    phase = status.get("relationship_phase", "established")
    phase_descriptions = {
        "first_contact": "First Contact — your very first conversation with Nik. Introduce yourself, be warm, pure curiosity.",
        "getting_to_know": "Getting to Know — early stages. Use discovery questions, direct responses, gentle observations.",
        "building_trust": "Building Trust — intermediate stages. You can agent-route, schedule, and give opinions when asked.",
        "established": "Established — you know him well. You can give proactive nudges, unsolicited observations, and accountability.",
        "deep_rapport": "Deep Rapport — long-term mentor. Use blunt challenges, inside references, and shorthand.",
    }
    
    phase_line = f"### 📊 Relationship Phase: {phase_descriptions.get(phase, phase)}\n"

    if status.get("first_contact"):
        return phase_line + FIRST_CONTACT_GUIDANCE

    lines = [
        phase_line,
        "### 🌱 Discovery Mode — you're still getting to know Nik",
        "",
        "You know his anchor facts, but not his present life yet. Prioritize",
        "understanding over prescribing — bias every reply toward curiosity:",
        "- Weave in at most ONE discovery question per turn, naturally tied to",
        "  what he just said. Never stack questions. Never interrogate.",
        "- If he asks for a plan or help, deliver it — discovery biases your",
        "  curiosity, it never blocks him.",
        "- Emotional disclosures → acknowledge first (STEP 0), discover later.",
        "- Never push schedules or roadmaps unprompted.",
    ]
    
    # If not active anymore, we just return the phase line
    if not status.get("active"):
        return phase_line

    remaining = status.get("remaining") or []
    if remaining:
        lines += [
            "",
            "Still curious about (pick whichever fits the moment, not in order):",
        ]
        lines += [f"  - {DISCOVERY_FACETS[f]}" for f in remaining]
    return "\n".join(lines)

