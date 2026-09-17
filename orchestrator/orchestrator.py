"""
orchestrator/orchestrator.py

Entry point and LangGraph graph for the Orchestrator — the mentor-style
reasoning loop that sits above all stateless sub-agents.

The orchestrator is stateless from the *session* point of view: memory lives
in the Postgres-backed MemoryManager, not in this graph state.  Each call to
`run_orchestrator_turn()` runs one full turn of the mentor loop and returns
the updated state.

Turn flow:
    intake_node
        → summarize_node
        → reason_node
          ├─ clarify_node      → format_output_node → END
          ├─ nudge_node        → format_output_node → END
          └─ dispatch_node
               → context_builder_node
               → agent_executor_node
               → memory_merger_node
               → format_output_node
                    → END

All heavy-lifting helpers (state summariser, LLM reasoner, memory merger, etc.)
live in their own focused modules under orchestrator/nodes/ and orchestrator/memory/.
This file does only two things: wraps those helpers as graph nodes and wires
the LangGraph StateGraph.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

# Node helpers — pure functions imported from their focused modules.
from orchestrator.cognition.persona import build_persona_block, get_user_name
from orchestrator.config import DEFAULT_TASK_TYPES, ROUTING_HINTS, VALID_TASK_TYPES
from orchestrator.memory.retriever import MemoryRetriever
from orchestrator.memory.store import MemoryManager, get_memory_manager
from orchestrator.nodes.context_builder import build_task as build_agent_task
from orchestrator.nodes.memory_merger import apply_memory_delta
from orchestrator.nodes.reasoner import (
    ReasoningDecision,
    apply_job_status_guardrail,
    apply_toolkit_guardrail,
    decision_to_dict,
    run_reasoner,
)
from orchestrator.nodes.synthesizer import synthesize_response
from orchestrator.registry import get_agent_spec
from orchestrator.state import OrchestratorState, build_initial_state
from orchestrator.tracing import component, traced
from schemas import AgentResult, AgentTask, ResultStatus

# Crisis handling guidance injected as reasoner notes when the crisis guardrail
# fires (Notes.md Layer D + professional boundary). direct_response_node reads
# this via "Reasoner notes" in its prompt.
CRISIS_RESPONSE_GUIDANCE = (
    "CRISIS HANDLING — Nik may be in genuine distress. Respond as a caring human, "
    "not a coach: acknowledge what he said seriously and warmly, in his own terms. "
    "Do NOT offer plans, schedules, or productivity advice. Do NOT diagnose or act "
    "as a therapist. Gently encourage him to reach out to someone he trusts or a "
    "mental-health professional, and remind him you're here whenever he wants to talk."
)

# Appended to a crisis response when the model itself didn't include a support
# pointer — the boundary behavior is code-enforced, never left to LLM judgment.
CRISIS_SUPPORT_LINE = (
    "One thing from me, mentor to friend: if what you're carrying ever feels heavier "
    "than a bad week, please reach out to someone you trust or a mental-health "
    "professional. You don't have to carry it alone — and I'm here whenever you "
    "want to talk."
)

# ---------------------------------------------------------------------------
# Conversation transcript (Phase 1 — live session transcript)
# ---------------------------------------------------------------------------

SESSION_GAP_MINUTES = 45   # inactivity gap that auto-closes a session
MAX_HISTORY_TURNS = 40     # cap on the live transcript kept in working memory


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _append_transcript_turn(
    wm: dict,
    role: str,
    content: str,
    memory_manager=None,
) -> None:
    """
    Append one turn to the live transcript inside a working-memory dict.

    Budget cutoff: when the transcript exceeds MAX_HISTORY_TURNS the oldest
    overflow is rolled into the continuous thread's running summary (async) —
    never silently dropped.
    """
    if not content:
        return
    history = list(wm.get("conversation_history") or [])
    history.append({
        "role": role,
        "content": content,
        "timestamp": _now_local().isoformat(),
    })
    if len(history) >= MAX_HISTORY_TURNS * 2:
        # Budget rollup fires at the watermark (not every single overflow
        # turn): the oldest MAX turns are merged into the running summary
        # (async) and the newest MAX stay verbatim. Nothing is ever silently
        # dropped — whatever leaves the window first went through the rollup.
        overflow = history[: len(history) - MAX_HISTORY_TURNS]
        wm["conversation_history"] = history[-MAX_HISTORY_TURNS:]
        if memory_manager is not None:
            try:
                from orchestrator.memory.conversation_rollup import rollup_async
                rollup_async(memory_manager, overflow, keep=0)
            except Exception as exc:
                print(f"[orchestrator] budget rollup dispatch failed: {exc}")
    else:
        wm["conversation_history"] = history
    wm["last_turn_at"] = history[-1]["timestamp"]


import threading


def _bg_summarize_session(history: list[dict], session_id: str) -> None:
    if not history:
        return
    try:
        from orchestrator.llm import get_reasoning_llm
        from orchestrator.memory.dna_store import get_dna_store

        transcript_text = "\n".join([f"{t.get('role', 'unknown')}: {t.get('content', '')}" for t in history])
        
        prompt = (
            "Summarize this conversation session in 1-2 sentences. "
            "Focus on the narrative thread: what was Nik working on, what did he achieve, "
            "what was he anxious or excited about, and what is the next step? "
            "Write in third person past tense ('Nik spent the session...').\n\n"
            f"Transcript:\n{transcript_text}"
        )
        
        llm = get_reasoning_llm(temperature=0.1)
        with component(
            "session_summary",
            tags=["component:session_summary"],
            metadata={"session_id": session_id},
        ):
            response = llm.invoke([("user", prompt)])
        summary = response.content.strip()
        
        store = get_dna_store()
        store.create_memory(
            content=summary,
            memory_type="context",
            source="data_derived",
            tags=[f"session:{session_id}"],
        )
        print("[orchestrator] session summarized and saved to DNA memory.")
    except Exception as exc:
        print(f"[orchestrator] background session summarization failed: {exc}")

def _persist_transcript(wm: dict, memory_manager) -> int:
    """
    Archive the live transcript as a ConversationSession row, then BOUNDARY
    rollup into the continuous thread: the portion older than the recent
    verbatim window is folded into the rolling summary and dropped from the
    live window; the recent window stays in working memory so the conversation
    continues seamlessly. Returns the number of turns archived.
    Never raises.
    """
    history = list(wm.get("conversation_history") or [])
    if not history:
        return 0
    try:
        session_id = wm.get("session_id") or "unknown"
        started_raw = wm.get("session_started_at") or history[0].get("timestamp")
        ended_raw = wm.get("last_turn_at") or history[-1].get("timestamp")
        memory_manager.save_conversation_session(
            session_id=session_id,
            started_at=datetime.fromisoformat(started_raw),
            ended_at=datetime.fromisoformat(ended_raw),
            transcript=history,
        )
        # Background session summary (archival DNA context memory).
        threading.Thread(target=_bg_summarize_session, args=(history, session_id), daemon=True).start()
    except Exception as exc:
        print(f"[orchestrator] session persistence failed: {exc}")

    # Boundary rollup — single continuous conversation: fold the older turns
    # into the rolling summary, keep the recent verbatim window for continuity.
    try:
        from orchestrator.memory.conversation_rollup import (
            KEEP_RECENT_TURNS,
            rollup_conversation,
        )
        report = rollup_conversation(memory_manager, history, keep=KEEP_RECENT_TURNS)
        wm["conversation_history"] = report.get("transcript") or []
    except Exception as exc:
        print(f"[orchestrator] boundary rollup failed: {exc}")
        wm["conversation_history"] = history[-MAX_HISTORY_TURNS:]
    wm["session_started_at"] = None
    return len(history)


def close_active_session(state: OrchestratorState, memory_manager=None) -> int:
    """
    Public session-close hook: persist the live transcript with timestamps and
    start fresh. Called on CLI exit and via POST /api/session/end. The third
    close path — an inactivity gap — lives in intake_node. Returns turns saved.
    """
    wm = state.get("working_memory") or {}
    mm = memory_manager or state.get("memory_manager") or get_memory_manager()
    return _persist_transcript(wm, mm)


def _build_direct_system_prompt(
    coaching_mode: str | None = None,
    toolkit_mode: str | None = None,
    workflow: str | None = None,
) -> str:
    """System prompt for direct_response_node — persona voice + examples from
    cognition/persona.py (single source of truth), plus this node's
    read-the-room and grounding rules.
    When toolkit_mode is set, the matching instruction-set toolkit block
    (role frame + philosophy + behaviors + selected workflow + guardrails)
    is injected as the behavior overlay on top of the base persona.
    """
    from orchestrator.cognition.persona import get_user_name
    name = get_user_name()
    
    mode_guidance = ""
    if coaching_mode:
        mode_rules = {
            "encourager": "Celebrate specifics, hand momentum back ('what's next?'). Keep it light and positive.",
            "accountability": "Name the gap directly, offer one concrete challenge. Don't be mean, but don't let excuses slide.",
            "calm_presence": "Short, warm, no plans unless asked. Give permission to rest.",
            "strategic_advisor": "Analyze trade-offs, form opinions, recommend clearly based on his goals.",
        }
        rule = mode_rules.get(coaching_mode, "")
        if rule:
            mode_guidance = f"\nCOACHING MODE FOR THIS TURN ({coaching_mode}):\n- {rule}\n"

    base_prompt = (
        f"You are {name}'s personal AI mentor-companion. You know him through the "
        "context document you are given — his memories, schedule, vault roadmaps, "
        "goals, and conversation history.\n\n"
        + build_persona_block(include_examples=True)
        + f"""
{mode_guidance}
EMOTIONAL INTELLIGENCE:
- READ THE ROOM. Match your response to the *type* of message {name} sent:
  • Greeting ('hi', 'hey', 'hello') → Greet back warmly, 1-2 sentences.
    Maybe one brief observation from context. Wait for HIM to set direction.
  • Emotional disclosure ('I feel stuck', 'I'm burned out') → Acknowledge
    the feeling FIRST. Mirror it. Do NOT jump to solutions or plans.
  • Venting ('today was garbage') → Validate. Listen. One gentle question at most.
  • Seeking guidance → Analyze using data, then suggest.
  • Specific request → Execute via the data you have.
- If "Reasoner notes" contain crisis guidance, follow it exactly — warmth,
  no plans, no diagnosis, gentle pointer to human support.
- NEVER give unsolicited career advice, numbered action lists, or productivity
  tips unless {name} explicitly asks for them.
- When in doubt about what he needs, ASK rather than assume.


ANTI-GENERIC RULES (critical — failure here makes the mentor useless):
- NEVER suggest something the user has already mentioned. Read the conversation history.
  If he said he's applying on Naukri, do NOT say "try applying on Naukri". If he mentioned
  LinkedIn, do NOT say "try LinkedIn". The conversation is in your context — use it.
- NEVER give advice a random Google article would give. That means no lists like
  "try Glassdoor, update your resume, network more" unless he explicitly asked for that.
- React to the SPECIFICS of what he shared. If he told you his ATS score is 93,
  his referrals aren't converting, and LinkedIn only worked via a recruiter comment —
  those details are the entire substance. Your response must engage with those specifics.
- If a detail he shared deserves a pointed follow-up, ask one sharp question about it
  rather than adding generic tips. One focused question > five generic suggestions.
- When you have no relevant memory or context, say so honestly and ask — don't fill the
  gap with Wikipedia-level advice.

GROUNDING RULES:
- Ground your response in the CONVERSATION HISTORY first, then the DNA memory data.
- Memory provenance labels tell you how much to trust each memory: an
  'unconfirmed' mentor inference is a hypothesis — speak about it as one.
- If he asks what to do, check the Schedule, Momentum, and goals sections."""
    )

    # Toolkit overlay (instruction-set layer): when a toolkit is active, its
    # role frame + philosophy + behaviors + selected workflow + guardrails are
    # layered on top of the base persona. Fail-open — a broken toolkit never
    # crashes the turn, it just gets skipped.
    toolkit_block = ""
    if toolkit_mode:
        try:
            from orchestrator.toolkits import render_toolkit_block
            toolkit_block = render_toolkit_block(toolkit_mode, workflow=workflow)
        except Exception as exc:
            print(f"[direct_system_prompt] toolkit render failed ({toolkit_mode}): {exc}")
        if toolkit_block:
            base_prompt += (
                "\n\n=====================================================================\n"
                "TOOLKIT OVERLAY (combined with the persona above — do not drop persona rules)\n"
                "=====================================================================\n"
                + toolkit_block
            )

    return base_prompt

# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def intake_node(state: OrchestratorState) -> dict:
    """
    Capture incoming user message, bump turn counter, and process user consent approvals for proposed actions.
    """
    wm = dict(state["working_memory"])
    wm["turn_count"] = wm.get("turn_count", 0) + 1

    user_text = (wm.get("user_input") or "").strip().lower()
    pending_actions = wm.get("pending_proposed_actions") or []

    executed_feedback = None
    mm = state.get("memory_manager") or get_memory_manager()

    if pending_actions and any(kw in user_text for kw in ("yes", "approve", "confirm", "ok", "apply", "sure", "do it", "accept")):

        results_summary = []
        for action in pending_actions:
            act_type = action.get("action_type") or action.get("action")
            if act_type in ("update_schedule", "edit_event") and action.get("event_id"):
                res = mm.update_schedule_event(action["event_id"], action.get("updates", {}))
                if res:
                    results_summary.append(f"Updated Schedule Event '{res['title']}' [Status: {res['status']}]")
            elif act_type == "create_schedule" and action.get("event"):
                res = mm.create_schedule_event(action["event"])
                results_summary.append(f"Created Schedule Event '{res['title']}'")
            elif act_type in ("mark_node_done", "update_obsidian"):
                from orchestrator.config import MENTOR_CURRICULUM_PATH
                from orchestrator.memory.roadmap import (
                    find_node_anywhere,
                    set_node_status,
                )
                node_id = action.get("node_id") or action.get("target")
                new_status = action.get("new_status", "done")
                hit = find_node_anywhere(MENTOR_CURRICULUM_PATH, str(node_id)) if node_id else None
                if hit:
                    graph_id, _node = hit
                    ok, msg = set_node_status(
                        MENTOR_CURRICULUM_PATH, graph_id, str(node_id), new_status
                    )
                    results_summary.append(
                        msg if ok else f"Could not update node '{node_id}': {msg}"
                    )
                else:
                    results_summary.append(
                        f"No node exactly matching '{node_id}' — nothing was updated"
                    )

        wm["pending_proposed_actions"] = []
        if results_summary:
            executed_feedback = "✅ Confirmed and executed updates:\n" + "\n".join(f"  • {r}" for r in results_summary)

    # ------------------------------------------------------------------
    # Conversation transcript (Phase 1): inactivity-gap auto-close, then
    # append this user turn. The mentor turn is appended in format_output_node.
    # ------------------------------------------------------------------
    now = _now_local()
    last_turn_raw = wm.get("last_turn_at")
    if wm.get("conversation_history") and last_turn_raw:
        try:
            gap_minutes = (now - datetime.fromisoformat(last_turn_raw)).total_seconds() / 60
        except ValueError:
            gap_minutes = 0
        if gap_minutes > SESSION_GAP_MINUTES:
            saved = _persist_transcript(wm, mm)
            print(f"[intake] inactivity gap {int(gap_minutes)}m — previous session closed ({saved} turns saved).")

    if not wm.get("session_started_at"):
        wm["session_started_at"] = now.isoformat()
    raw_user_input = (wm.get("user_input") or "").strip()
    if raw_user_input:
        _append_transcript_turn(wm, "user", raw_user_input, mm)

    if executed_feedback:
        wm["system_feedback"] = executed_feedback

    return {
        "working_memory": wm,
        "status": "idle",
        # Reset per-turn output fields so stale values never leak.
        "summary_text": None,
        "reasoning_decision": None,
        "current_task": None,
        "response_text": None,
    }


def summarize_node(state: OrchestratorState) -> dict:
    """
    Build the reasoner's context for this turn.

    Phase 4 (dna_memory_redesign_v2 §12): the two-layer DNA context document
    is the ONLY context path — the situation report and its 23-column user
    state table are gone. If DNA context construction fails catastrophically,
    a minimal inline fallback (profile snapshot + message) keeps the turn
    alive — the reasoner must never get an empty context.
    """
    user_input = state["working_memory"].get("user_input") or ""
    mm = state.get("memory_manager") or get_memory_manager()
    trigger = state["working_memory"].get("trigger", "user_message")

    wm = dict(state["working_memory"])
    try:
        from orchestrator.memory.dna_context import build_dna_context
        from orchestrator.memory.dna_store import get_dna_store

        summary = build_dna_context(
            mm,
            get_dna_store(),
            user_input,
            trigger=trigger,
            conversation_history=state["working_memory"].get("conversation_history"),
        )
        wm["context_source"] = "dna"
        return {"summary_text": summary, "working_memory": wm}
    except Exception as exc:
        print(f"[summarize_node] DNA context failed ({exc}) — using minimal inline fallback.")
        wm["context_source"] = "minimal_fallback"
        return {"summary_text": _minimal_context_fallback(mm, user_input, trigger), "working_memory": wm}


def _minimal_context_fallback(mm, user_input: str, trigger: str) -> str:
    """
    Emergency context when DNA context building fails entirely. Deliberately
    tiny: a few structured profile facts + the message. Not a report.
    """
    try:
        profile = mm.load_profile_facts([
            "employment_status", "target_roles", "short_term_goal", "energy_level",
        ])
    except Exception:
        profile = {}
    lines = ["## Context (minimal fallback)", ""]
    for key, value in profile.items():
        if value not in (None, "", []):
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines += ["", f"Trigger: {trigger}", "", "### Nik's Message", user_input or "(autonomous wake-up)"]
    return "\n".join(lines)


def reason_node(state: OrchestratorState) -> dict:
    """
    Run the LLM reasoning core. Reads summary_text, returns a structured
    ReasoningDecision stored as a plain dict in reasoning_decision.
    """
    summary = state.get("summary_text") or ""
    try:
        decision = run_reasoner(summary)
    except Exception as exc:
        decision = ReasoningDecision(
            action="route",
            reasoning=f"Reasoner failed ({exc}); using keyword fallback.",
        )

    # ------------------------------------------------------------------
    # Crisis guardrail (Notes.md Layer D + professional boundary). A crisis
    # disclosure may NEVER be routed to an agent or answered with a plan.
    # Enforced in code — not left to LLM judgment alone (same philosophy as
    # the programmatic constraint override in the LinkedIn critic).
    # ------------------------------------------------------------------
    if getattr(decision, "disclosure_type", None) == "crisis":
        decision = decision.model_copy(update={
            "action": "direct_response",
            "agent_name": None,
            "agent_pipeline": [],
            "needs_long_term_context": False,
            "proposed_actions": [],
            "notes_for_agent": CRISIS_RESPONSE_GUIDANCE,
        })

    # ------------------------------------------------------------------
    # Daily_Coach skill migration: daily_planner and learning_monitor are
    # no longer sub-agents. Their capabilities are absorbed into the
    # orchestrator's direct_response_node with tool access.
    # Code-enforced: any route to these agents is redirected to direct_response
    # with a skill instruction injected into notes_for_agent.
    # ------------------------------------------------------------------
    SKILL_OVERRIDE_NOTES = {
        "daily_planner": (
            "PLANNING SKILL — The user asked for a daily plan. "
            "Use save_daily_plan to persist it, trim_plan_to_fit for budget, "
            "get_available_topic_nodes for unlocked topics. "
            "Produce a plan, trim it to fit, save it, and explain the result."
        ),
        "learning_monitor": (
            "LEARNING LOG SKILL — The user reported learning progress. "
            "Use log_learning_session to persist it, compute_learning_streak "
            "for streak math. Classify what they did, log it, save it, and "
            "report the result including the new streak."
        ),
    }
    agent = getattr(decision, "agent_name", None)
    # Resolve legacy aliases the LLM might emit before the skill lookup so a
    # "planner" / "learning" route still lands on the orchestrator skill.
    _SKILL_ALIASES = {"planner": "daily_planner", "learning": "learning_monitor"}
    skill_key = _SKILL_ALIASES.get(agent, agent)
    skill_note = SKILL_OVERRIDE_NOTES.get(skill_key)
    if skill_note and getattr(decision, "disclosure_type", None) != "crisis":
        existing_notes = getattr(decision, "notes_for_agent", "") or ""
        combined = f"{skill_note}\n\n{existing_notes}".strip()
        decision = decision.model_copy(update={
            "action": "direct_response",
            "agent_name": None,
            "agent_pipeline": [],
            "needs_long_term_context": False,
            "proposed_actions": [],
            "notes_for_agent": combined,
        })

    # ------------------------------------------------------------------
    # Continuity guardrail (guidelines §5): a status-change report about a
    # TRACKED job application must route to job_hunter — the router model
    # keeps treating these as open conversation, losing the state change.
    # Code-enforced, same philosophy as the crisis guardrail above.
    # ------------------------------------------------------------------
    if decision.action != "route" and getattr(decision, "disclosure_type", None) != "crisis":
        try:
            mm = state.get("memory_manager") or get_memory_manager()
            pipeline_facts = mm.get_profile_fact("career", "job_pipeline") or []
            companies = [a.get("company") for a in pipeline_facts if isinstance(a, dict)]
            decision = apply_job_status_guardrail(
                decision,
                state["working_memory"].get("user_input") or "",
                companies,
            )
        except Exception as exc:
            print(f"[reason_node] job-status guardrail warning: {exc}")

    # ------------------------------------------------------------------
    # Toolkit guardrail (instruction-set overlay layer): ship-mode override,
    # explicit teacher-mode backstop, and direct-response-only enforcement.
    # Code-enforced — the teaching overlay can never ride on agent routing
    # or an emotional/crisis turn, and "just ship it" always exits teaching.
    # ------------------------------------------------------------------
    try:
        decision = apply_toolkit_guardrail(
            decision,
            state["working_memory"].get("user_input") or "",
        )
    except Exception as exc:
        print(f"[reason_node] toolkit guardrail warning: {exc}")

    decision_dict = decision_to_dict(decision)
    pipeline = decision.agent_pipeline or ([decision.agent_name] if decision.agent_name else [])
    wm = dict(state["working_memory"])

    # ------------------------------------------------------------------
    # Apply personality updates if LLM semantically triggered them
    # ------------------------------------------------------------------
    if getattr(decision, "personality_updates", None):
        try:
            from orchestrator.cognition.personality import _set_personality, get_personality
            mm = state.get("memory_manager") or get_memory_manager()
            pers = get_personality(mm)
            for k, v in decision.personality_updates.items():
                pers[k] = v
            _set_personality(mm, pers)
            
            # Format a nice feedback string
            updates_str = ", ".join(f"{k}={v}" for k, v in decision.personality_updates.items())
            sys_fb = wm.get("system_feedback", "")
            new_fb = f"⚙️ Updated mentor personality: {updates_str}"
            wm["system_feedback"] = f"{sys_fb}\n{new_fb}" if sys_fb else new_fb
        except Exception as exc:
            print(f"[reason_node] failed to apply personality updates: {exc}")

    # Dynamic AI Self-Scheduling Trigger Registration
    if getattr(decision, "next_wake_up_minutes", None):
        try:
            from scheduler.engine import get_scheduler_engine
            scheduler = get_scheduler_engine()
            scheduler.schedule_wake_up(
                minutes_from_now=decision.next_wake_up_minutes,
                reason=decision.next_wake_up_reason or "Proactive mentor check-in",
            )
        except Exception as exc:
            print(f"[reason_node] Self-scheduling warning: {exc}")

    # Store proposed actions awaiting consent
    if getattr(decision, "proposed_actions", None):
        wm = dict(state["working_memory"])
        wm["pending_proposed_actions"] = decision.proposed_actions

    return {
        "reasoning_decision": decision_dict,
        "agent_pipeline": pipeline,
        "pipeline_step": 0,
        "working_memory": wm,
    }


# ---------------------------------------------------------------------------
# Routing function (conditional edge after reason_node)
# ---------------------------------------------------------------------------

def route_after_reason(state: OrchestratorState) -> str:
    """
    Inspect reasoning_decision and return the name of the next node.

    Returns:
        "direct_response_node"   — answer directly using full situation context
        "long_term_recall_node" — needs historical context before dispatching
        "clarify_node"           — ask the user a clarifying question
        "nudge_node"             — proactively send an encouragement message
        "dispatch_node"          — route to a sub-agent (no deep recall needed)
    """
    decision = state.get("reasoning_decision") or {}
    action = decision.get("action", "route")
    if action == "direct_response":
        return "direct_response_node"
    if action == "ask_clarifying_question":
        return "clarify_node"
    if action == "proactive_nudge":
        return "nudge_node"
    # Route through long-term recall only when explicitly flagged.
    if decision.get("needs_long_term_context", False):
        return "long_term_recall_node"
    return "dispatch_node"


# ---------------------------------------------------------------------------
# Terminal-branch nodes (clarify / nudge)
# ---------------------------------------------------------------------------

def clarify_node(state: OrchestratorState) -> dict:
    """
    Build a clarifying-question response from the reasoner decision and route
    directly to format_output_node. Hard caps questions at max 2 per turn.
    """
    decision = state.get("reasoning_decision") or {}

    # Enforce strict 1-2 question cap in Python
    target_q = decision.get("target_questions_now") or []
    if target_q:
        target_q = target_q[:2]  # Hard cap at max 2 questions
        question = " ".join(target_q)
    else:
        question = (
            decision.get("clarifying_question")
            or "Can you tell me a bit more about what's going on right now?"
        )

    # Persist any queued non-urgent questions for opportunistic later use
    queued = decision.get("queued_questions") or []
    if queued:
        try:
            mm = state.get("memory_manager") or get_memory_manager()
            existing = mm.get_profile_fact("system", "open_questions") or []
            if isinstance(existing, list):
                updated = list(set(existing + queued))
                mm.set_profile_fact("system", "open_questions", updated, source="reasoner_queue")
        except Exception as exc:
            print(f"[clarify_node] Failed to queue questions: {exc}")

    return {
        "response_text": question,
        "status": "needs_clarification",
    }


def nudge_node(state: OrchestratorState) -> dict:
    """
    Build a proactive mentor message and route directly to format_output_node.
    """
    decision = state.get("reasoning_decision") or {}
    message = (
        decision.get("proactive_message")
        or "Just checking in — how are things going?"
    )
    return {
        "response_text": message,
        "status": "done",
    }


def direct_response_node(state: OrchestratorState) -> dict:
    """
    Generate a high-quality, context-aware response using the full DNA context
    document. This replaces the old fallback path — the heavy conversational model
    (Qwen3-235B) speaks directly to the user with full awareness of memories, vault,
    schedule, goals, etc.
    """
    from orchestrator.harness import (
        CODE_TOOLS,
        TOOLS,
        assemble_situation_facts,
        make_calendar_tools,
        make_memory_tools,
        run_tool_loop,
    )
    from orchestrator.llm import get_conversational_llm
    from orchestrator.memory.store import get_memory_manager

    user_input = state["working_memory"].get("user_input") or ""
    summary = state.get("summary_text") or ""
    decision = state.get("reasoning_decision") or {}
    notes = decision.get("notes_for_agent") or ""
    coaching_mode = decision.get("coaching_mode")
    toolkit_mode = decision.get("toolkit_mode")
    toolkit_workflow = decision.get("workflow")
    reasoning_depth = decision.get("reasoning_depth") or "standard"

    system_prompt = _build_direct_system_prompt(coaching_mode, toolkit_mode, toolkit_workflow)

    # Build situation facts from the memory manager (the manager's "harness").
    mm = state.get("memory_manager") or get_memory_manager()
    situation_facts = ""
    try:
        situation_facts = assemble_situation_facts(mm)
    except Exception as exc:
        print(f"[direct_response_node] situation facts failed: {exc}")

    # Bind memory-write tools alongside pure computation tools.
    memory_tools = []
    try:
        memory_tools = make_memory_tools(mm)
    except Exception as exc:
        print(f"[direct_response_node] memory tools failed: {exc}")

    # Calendar grid tools (read availability + write validated blocks).
    calendar_tools = []
    try:
        calendar_tools = make_calendar_tools(mm)
    except Exception as exc:
        print(f"[direct_response_node] calendar tools failed: {exc}")

    human_prompt = f"""CONTEXT DOCUMENT (your full context):
{summary}

---
Reasoner notes: {notes}
---

{situation_facts}

{get_user_name()} says: "{user_input}"

Respond as his mentor using the context document data. Be specific and grounded.
OUTPUT CONSTRAINT: Write EXACTLY ONE single final response. Do NOT provide multiple options or variations.

COMPUTATION TOOLS (available via function-calling):
  Pure computation (no side effects):
    - compute_learning_streak: exact streak arithmetic
    - trim_plan_to_fit: drop lowest-priority plan items until budget fits
    - get_available_topic_nodes: prerequisite-unlocked study topics from DAG
    - format_duration: humanize minutes

  Memory persistence (side effects — writes are permanent):
    - save_daily_plan: persist a plan with items, available_minutes, date
    - log_learning_session: persist topics studied, compute streak

  Calendar grid (code-validated slot math — never guess availability):
    - get_day_grid: read the 48 half-hour slots for a date (free/busy/anchors)
    - find_available_slots: candidate free windows for a task duration
    - place_time_block: book a block (overlap + sleep/meal-anchor guarded)
    - set_anchor: reserve a life anchor (sleep/meal/commute/gym)

Use computation tools for exact arithmetic or graph facts. Use memory tools
to persist results. Use calendar tools for scheduling. Do NOT call them for
ordinary conversation."""

    # Code-explorer surface is exposed ONLY for code-grounded turns (deep
    # reasoning or the code-explorer toolkit). The mentor reads real code
    # before reasoning about it — it never fabricates what code does.
    use_code_tools = (
        reasoning_depth == "deep" or toolkit_mode == "code-explorer"
    )
    if use_code_tools:
        human_prompt += (
            "\n\nCODE-EXPLORER TOOLS (your eyes on the actual codebase):\n"
            "  - read_file(path, start?, end?): read real files\n"
            "  - grep_search(pattern, path?): find usages/definitions\n"
            "  - list_directory(path?): repo structure\n"
            "  - git_status / git_log / git_diff: what changed and why\n"
            "  - run_command(cmd): allowlisted tests/lint/build only\n"
            "  - propose_edit(path, search_text, replace_text): propose a code\n"
            "    change WITHOUT writing it — the user owns the pen in v1\n"
            "READ THE ACTUAL CODE BEFORE ANSWERING. Never guess what a function\n"
            "does — open it. Proposals are diffs for the user to review, never\n"
            "silent writes."
        )

    # Retrieval-practice material: when the learning-companion toolkit is in
    # "retrieve" mode, inject Nik's real learning history so the mentor builds
    # spaced-practice questions from actual facts (never invented history).
    # Also injected for the code-explorer "scaffold" workflow so lane choice
    # is memory-aware ("last time you watched X — today you try it first").
    if toolkit_mode and toolkit_workflow in ("retrieve", "scaffold"):
        try:
            from orchestrator.toolkits import assemble_retrieval_material
            material = assemble_retrieval_material(mm)
            if material:
                if toolkit_workflow == "retrieve":
                    human_prompt += f"\n\n{material}"
                else:
                    human_prompt += (
                        "\n\nOWNERSHIP + LEARNING HISTORY (for choosing writing_split "
                        "lanes — who wrote vs. watched what, and what's been studied):\n"
                        + material
                    )
        except Exception as exc:
            print(f"[direct_response_node] retrieval material failed: {exc}")

    is_crisis = decision.get("disclosure_type") == "crisis"
    try:
        llm = get_conversational_llm(temperature=0.45)
        all_tools = TOOLS + memory_tools + calendar_tools + (CODE_TOOLS if use_code_tools else [])
        if is_crisis:
            response = llm.invoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": human_prompt},
            ])
            output = response.content.strip()
        elif reasoning_depth == "deep":
            # Two-phase deep loop (Cline-grade reasoning):
            #   Phase 1 — investigation: reasoning over the codebase, forming
            #             and VERIFYING hypotheses with tools. Output is a
            #             private analysis block, not the user-facing answer.
            #   Phase 2 — synthesis: the mentor-voiced final answer grounded in
            #             what was actually read.
            analysis_prompt = (
                human_prompt
                + "\n\nINVESTIGATION PHASE (private thinking, not the final answer):\n"
                "Read the relevant code, form a hypothesis, VERIFY it with tools,"
                " then write your working analysis. This is the phase where you"
                " reason — do not address the user directly yet."
            )
            analysis = run_tool_loop(
                llm,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": analysis_prompt},
                ],
                tools=all_tools,
                max_rounds=6,
            )
            final_prompt = (
                f"INVESTIGATION ANALYSIS (your own work — use it as the ground "
                f"truth for the answer):\n{analysis}\n\n"
                + human_prompt
                + "\n\nFINAL ANSWER PHASE: write the mentor response for "
                f"{get_user_name()} now, grounded in the analysis. Be concise, "
                "specific, and honest about what you verified vs. could not."
            )
            output = run_tool_loop(
                llm,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": final_prompt},
                ],
                tools=all_tools,
                max_rounds=4,
            )
            output = output.strip()
        else:
            # standard / quick — single loop (quick still works through the
            # loop; the model just won't call tools for casual turns).
            output = run_tool_loop(
                llm,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": human_prompt},
                ],
                tools=all_tools,
            )
            output = output.strip()
    except Exception as exc:
        print(f"[direct_response_node] LLM call failed ({exc})")
        output = "I'm having trouble right now — could you try that again?"

    # Crisis footer (code-enforced, Notes.md professional boundary): the support
    # pointer must be present in every crisis response, even if the model omits it.
    if decision.get("disclosure_type") == "crisis" and not any(
        k in output.lower()
        for k in ("professional", "therapist", "counselor", "counsellor", "helpline")
    ):
        output += "\n\n" + CRISIS_SUPPORT_LINE

    return {
        "response_text": output,
        "status": "done",
    }


def long_term_recall_node(state: OrchestratorState) -> dict:
    """
    Semantic search over warm/cold memory tiers (Tier 2/3).

    Only reached when reason_node sets needs_long_term_context=True.
    Appends a 'RELEVANT OLDER MEMORIES' block to summary_text so that
    dispatch_node and context_builder_node have richer context.
    Falls through silently if retrieval fails or finds nothing.
    """
    user_input = state["working_memory"].get("user_input") or ""
    mm = state.get("memory_manager") or get_memory_manager()
    retriever = MemoryRetriever(mm)

    recall_block = retriever.recall(user_input)
    if not recall_block:
        # Nothing found — proceed with existing summary unchanged.
        return {}

    existing_summary = state.get("summary_text") or ""
    enriched_summary = f"{existing_summary}\n\n{recall_block}"
    return {"summary_text": enriched_summary}


# ---------------------------------------------------------------------------
# Main dispatch path nodes
# ---------------------------------------------------------------------------

def dispatch_node(state: OrchestratorState) -> dict:
    """
    Convert the reasoner's decision (or fall back to keyword hints) into the
    concrete agent_name + task_type, and store them in working_memory.extra
    for the next node to pick up.
    """
    decision = state.get("reasoning_decision") or {}
    user_input = state["working_memory"].get("user_input") or ""
    pipeline = state.get("agent_pipeline") or []
    step = state.get("pipeline_step") or 0

    if pipeline and step < len(pipeline):
        agent_name = pipeline[step]
    else:
        agent_name = decision.get("agent_name") or ""
    task_type = (decision.get("task_type") or "").strip()

    ALIAS_MAP = {
        "content_generator": "linkedin_writer",
        "linkedin": "linkedin_writer",
        "decomposer": "goal_decomposer",
        "resume": "job_hunter",
        "career": "job_hunter",
        "job_tracker": "job_hunter",
        "job_applications": "job_hunter",
    }
    if agent_name in ALIAS_MAP:
        agent_name = ALIAS_MAP[agent_name]

    # Drop hallucinated task types (observed: "find_jobs", "search_openings",
    # "job_listings"). An invalid value is worse than none — it can hijack a
    # multi-action agent's branch routing away from the user's actual intent.
    if task_type and agent_name in VALID_TASK_TYPES and task_type not in VALID_TASK_TYPES[agent_name]:
        print(f"[dispatch_node] dropping invalid task_type '{task_type}' for agent '{agent_name}'")
        task_type = ""

    if agent_name:
        # Empty default for multi-action agents (job_hunter): the agent's own
        # input_parser resolves intent from the raw message instead.
        task_type = task_type or DEFAULT_TASK_TYPES.get(agent_name, "")
    else:
        # Fallback: cheap keyword matching.
        lowered = user_input.lower()
        for name, hints in ROUTING_HINTS.items():
            if any(hint in lowered for hint in hints):
                agent_name = name
                task_type = DEFAULT_TASK_TYPES.get(name, "")
                break
        else:
            agent_name = "fallback"
            task_type = DEFAULT_TASK_TYPES["fallback"]

    wm = dict(state["working_memory"])
    wm["extra"] = {
        **wm.get("extra", {}),
        "agent_name": agent_name,
        "task_type": task_type,
    }
    # Keep agent_pipeline truthful for turns resolved here by keyword fallback —
    # reason_node only seeds it from the LLM decision (line 402), so a
    # fallback-resolved route would otherwise leave it empty (the API and UI
    # read this field to know which agent ran).
    updates = {"working_memory": wm}
    if not pipeline and agent_name != "fallback":
        updates["agent_pipeline"] = [agent_name]
    return updates


def context_builder_node(state: OrchestratorState) -> dict:
    """
    Pull the right chunks from each memory tier and build the full AgentTask
    that the chosen sub-agent will receive.
    """
    wm = state["working_memory"]
    extra = wm.get("extra", {})
    agent_name: str = extra.get("agent_name", "fallback")
    task_type: str = extra.get("task_type", "direct_response")
    user_input: str = wm.get("user_input") or ""
    session_id: str = wm.get("session_id", "")

    # Reconstruct decision object for notes_for_agent forwarding.
    raw_decision = state.get("reasoning_decision") or {}
    try:
        decision = ReasoningDecision(**raw_decision)
    except Exception:
        decision = None

    mm = state.get("memory_manager") or get_memory_manager()
    task = build_agent_task(
        memory_manager=mm,
        session_id=session_id,
        user_input=user_input,
        agent_name=agent_name,
        task_type=task_type,
        decision=decision,
    )
    return {"current_task": task}


@traced("agent_executor")
def _run_agent(spec, task: AgentTask) -> AgentResult:
    """Run the chosen sub-agent through the registry (traced)."""
    return spec.run(task)


def agent_executor_node(state: OrchestratorState) -> dict:
    """
    Run the chosen sub-agent via the registry and append its AgentResult to
    the results list.
    """
    task: AgentTask | None = state.get("current_task")
    if task is None:
        # Should not happen — guard against bad state.
        dummy = AgentResult(
            task_id=str(uuid4()),
            agent_name="unknown",
            task_type="unknown",
            status=ResultStatus.FAILED,
            output="No task was built for this turn.",
            error_message="current_task was None in agent_executor_node",
        )
        return {"results": [*(state.get("results") or []), dummy]}

    try:
        spec = get_agent_spec(task.agent_name)
        
        # Print input payload to console
        print(f"\n[Agent Invocation] Dispatched to: {task.agent_name} (Task ID: {task.task_id})")
        print(f"  ├─ Instructions: {task.instructions}")
        print(f"  ├─ Memory Slice profile keys: {list(task.memory_slice.relevant_profile.keys())}")
        if task.memory_slice.constraints:
            print(f"  ├─ Constraints: {task.memory_slice.constraints}")
        if task.params:
            print(f"  └─ Params: {task.params}")
        
        result = _run_agent(spec, task)

        # Print output payload to console
        print(f"[Agent Response] Received from: {result.agent_name}")
        print(f"  ├─ Status: {result.status.value}")
        if result.memory_delta:
            print(f"  ├─ Memory Delta: {result.memory_delta}")
        if result.draft_suggestions:
            print(f"  ├─ Draft Suggestions: {len(result.draft_suggestions)} suggestion(s)")
        output_preview = (result.output or "").replace("\n", " ")
        if len(output_preview) > 150:
            output_preview = output_preview[:147] + "..."
        print(f"  └─ Output preview: {output_preview}")
        print("-" * 60 + "\n")
    except Exception as exc:
        result = AgentResult(
            task_id=task.task_id or str(uuid4()),
            agent_name=task.agent_name,
            task_type=task.task_type,
            status=ResultStatus.FAILED,
            output=f"{task.agent_name} failed: {exc}",
            error_message=str(exc),
        )

    return {"results": [result]}


def memory_merger_node(state: OrchestratorState) -> dict:
    """
    Persist the last AgentResult's memory_delta back into the memory store.
    Only runs when the result status is success or partial.
    """
    results = state.get("results") or []
    if not results:
        return {}

    result = results[-1]
    if result.status.value in ("success", "partial"):
        mm = state.get("memory_manager") or get_memory_manager()
        apply_memory_delta(mm, result)

    return {}


def route_after_merger(state: OrchestratorState) -> str:
    """
    Check if there are remaining sub-agents in the pipeline chain.
    If yes, route to next_pipeline_agent_node; else proceed to format_output_node.
    """
    pipeline = state.get("agent_pipeline") or []
    step = state.get("pipeline_step") or 0
    if step + 1 < len(pipeline):
        return "next_pipeline_agent_node"
    return "format_output_node"


def next_pipeline_agent_node(state: OrchestratorState) -> dict:
    """Increment pipeline_step for the next sub-agent in the pipeline."""
    step = state.get("pipeline_step", 0) + 1
    return {"pipeline_step": step}


def format_output_node(state: OrchestratorState) -> dict:
    """
    Turn AgentResults into a final user-facing response, synthesized through
    the mentor's voice layer.

    For clarify/nudge short-circuit paths, response_text is already set —
    pass it through the synthesizer so even those feel human.
    For agent-dispatch paths, synthesize each result then combine.
    """
    user_input = state["working_memory"].get("user_input") or ""
    mm = state.get("memory_manager") or get_memory_manager()
    wm = dict(state["working_memory"])
    sys_feedback = wm.pop("system_feedback", None)

    # --- Short-circuit path (clarify / nudge / direct_response already set response_text) ---
    if state.get("response_text") is not None:
        final_text = state["response_text"]
        if sys_feedback:
            final_text = f"{sys_feedback}\n\n{final_text}"
            
        wm["last_assistant_message"] = final_text
        _append_transcript_turn(wm, "mentor", final_text, mm)
        _log_turn(state, final_text)
        try:
            from orchestrator.memory.dna_reflection import reflect_on_turn_async
            reflect_on_turn_async(user_input, final_text)
        except Exception as exc:
            print(f"[format_output_node] dna reflection dispatch warning: {exc}")
        return {"working_memory": wm, "status": state.get("status", "done"), "response_text": final_text}

    results = state.get("results") or []
    if not results:
        final_text = "Something went wrong on my end — could you try that again?"
        if sys_feedback:
            final_text = f"{sys_feedback}\n\n{final_text}"
        return {
            "response_text": final_text,
            "status": "failed",
            "working_memory": wm,
        }

    # Load a compact profile snapshot for the synthesizer.
    try:
        profile_context = mm.load_profile_facts([
            "employment_status", "learning_streak_days",
            "short_term_goal", "long_term_goal",
            "energy_level", "active_learning_path",
        ])
    except Exception:
        profile_context = {}

    synthesized_parts: list[str] = []
    has_failed = False
    has_clarification = False

    for res in results:
        if res.status == ResultStatus.FAILED:
            raw = res.error_message or f"{res.agent_name} step failed."
            synthesized_parts.append(
                synthesize_response(
                    agent_output=raw,
                    agent_name=res.agent_name,
                    task_type=res.task_type,
                    user_input=user_input,
                    profile_context=profile_context,
                    status="failed",
                )
            )
            has_failed = True
        elif res.status == ResultStatus.NEEDS_CLARIFICATION:
            synthesized_parts.append(
                res.clarification_needed or "I need a bit more context — what's the most important thing here?"
            )
            has_clarification = True
        else:
            raw = res.output or f"{res.agent_name} completed."
            if res.draft_suggestions:
                drafts = "\n\n".join(
                    f"{s.content}" for s in res.draft_suggestions
                )
                raw = f"{raw}\n\n{drafts}"

            PREFORMATTED_AGENTS = {"goal_decomposer", "linkedin_writer", "job_hunter"}
            if res.agent_name in PREFORMATTED_AGENTS or raw.startswith(("📅", "✅", "🗺️", "➕", "✏️", "🗑️", "📋", "#")):
                synthesized_parts.append(raw)
            else:
                status_label = res.status.value if hasattr(res.status, "value") else str(res.status)
                synthesized_parts.append(
                    synthesize_response(
                        agent_output=raw,
                        agent_name=res.agent_name,
                        task_type=res.task_type,
                        user_input=user_input,
                        profile_context=profile_context,
                        status=status_label,
                    )
                )

    response_text = "\n\n---\n\n".join(synthesized_parts) if len(synthesized_parts) > 1 else (synthesized_parts[0] if synthesized_parts else "")
    if sys_feedback:
        response_text = f"{sys_feedback}\n\n{response_text}"
        
    status = "failed" if has_failed else ("needs_clarification" if has_clarification else "done")

    last_result = results[-1]
    wm["last_assistant_message"] = response_text
    wm["pending_draft_suggestions"] = last_result.draft_suggestions or []
    _append_transcript_turn(wm, "mentor", response_text, mm)

    _log_turn(state, response_text, agent_invoked=last_result.agent_name)
    try:
        from orchestrator.memory.dna_reflection import reflect_on_turn_async
        reflect_on_turn_async(user_input, response_text)
    except Exception as exc:
        print(f"[format_output_node] dna reflection dispatch warning: {exc}")

    return {
        "response_text": response_text,
        "status": status,
        "working_memory": wm,
    }




def _log_turn(
    state: OrchestratorState,
    response_text: str,
    agent_invoked: str | None = None,
) -> None:
    """Write user ↔ assistant exchange to episodic_events asynchronously. Never raises."""
    def _bg_log():
        try:
            user_input  = state["working_memory"].get("user_input") or ""
            session_id  = state["working_memory"].get("session_id", "")
            mm = state.get("memory_manager") or get_memory_manager()
            mm.log_conversation_turn(
                user_input   = user_input,
                response_text= response_text,
                agent_invoked= agent_invoked,
                session_id   = session_id,
            )

            # Sync turn log into Obsidian Daily Note
            try:
                from integrations.obsidian_daily import append_mentor_log
                title = f"Mentor Turn ({agent_invoked or 'direct'})"
                log_body = f"**User:** {user_input[:200]}\n**Mentor:** {response_text[:400]}"
                append_mentor_log(content=log_body, title=title)
            except Exception as obs_exc:
                print(f"[_log_turn] obsidian daily note warning: {obs_exc}")
        except Exception as exc:
            print(f"[_log_turn] background logging warning: {exc}")

    threading.Thread(target=_bg_log, daemon=True).start()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Graph assembly & Checkpointer (Working Memory Checkpointing §3.1)
# ---------------------------------------------------------------------------

import sqlite3

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

os.makedirs("data", exist_ok=True)
_checkpoint_conn = sqlite3.connect("data/checkpoints.sqlite", check_same_thread=False)
serde = JsonPlusSerializer(allowed_msgpack_modules=True)
checkpointer = SqliteSaver(_checkpoint_conn, serde=serde)

# ---------------------------------------------------------------------------
# Execution-trace wrapper (architecture visualizer)
#
# Every graph node is wrapped so it (a) records its own activation + latency and
# (b) drains any tool calls it triggered into the turn's `execution_trace`.
# Plain dicts only → safe for the sqlite checkpointer / JSON response.
# ---------------------------------------------------------------------------

def _node_detail(name: str, state: OrchestratorState, out: dict) -> dict:
    """Compact, display-facing detail for one node (never raises)."""
    try:
        if name == "reason_node":
            d = out.get("reasoning_decision") or state.get("reasoning_decision") or {}
            if isinstance(d, dict):
                return {
                    k: d.get(k)
                    for k in (
                        "action",
                        "agent_name",
                        "agent_pipeline",
                        "task_type",
                        "toolkit_mode",
                        "workflow",
                        "reasoning_depth",
                        "disclosure_type",
                    )
                    if d.get(k)
                }
        if name in ("dispatch_node", "context_builder_node", "agent_executor_node"):
            extra = (state.get("working_memory") or {}).get("extra") or {}
            detail = {k: extra.get(k) for k in ("agent_name", "task_type") if extra.get(k)}
            results = out.get("results") or state.get("results") or []
            if results:
                last = results[-1]
                detail.setdefault("agent", getattr(last, "agent_name", None))
                detail.setdefault("task_type", getattr(last, "task_type", None))
                status = getattr(last, "status", None)
                detail["status"] = getattr(status, "value", status)
            pipeline = state.get("agent_pipeline") or []
            if pipeline:
                detail["pipeline"] = pipeline
            return detail
        if name == "format_output_node":
            text = out.get("response_text") or state.get("response_text") or ""
            return {"chars": len(text)}
        if name == "long_term_recall_node":
            return {"recall": "appended" if out.get("summary_text") else "none"}
    except Exception:
        pass
    return {}


def _clip(value, n: int = 400):
    """Compact single-line preview of a payload value (for the flow view)."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (dict, list)):
            s = json.dumps(value, default=str, ensure_ascii=False)
        else:
            s = str(value)
    except Exception:
        s = repr(value)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _node_io(name: str, state: OrchestratorState, out: dict) -> dict:
    """
    The raw DATA payloads flowing in and out of one node, for the visualizer.

    Deliberately excludes prompt templates / system prompts — only the message
    and structured data each step consumes and produces.
    """
    wm = state.get("working_memory") or {}
    decision = out.get("reasoning_decision") or state.get("reasoning_decision") or {}
    if hasattr(decision, "model_dump"):
        decision = decision.model_dump()
    if not isinstance(decision, dict):
        decision = {}
    extra = wm.get("extra") or {}
    din: dict = {}
    dout: dict = {}

    try:
        if name == "intake_node":
            din = {"user_input": _clip(wm.get("user_input"), 300), "session_id": wm.get("session_id")}
            dout = {"status": "idle", "cleared": ["summary_text", "reasoning_decision", "current_task", "response_text"]}
        elif name == "summarize_node":
            din = {"user_input": _clip(wm.get("user_input"), 200)}
            ctx = out.get("summary_text") or state.get("summary_text") or ""
            dout = {"context_doc": _clip(ctx, 600), "chars": len(ctx)}
        elif name == "reason_node":
            ctx = state.get("summary_text") or ""
            din = {"user_input": _clip(wm.get("user_input"), 200), "context_doc_chars": len(ctx)}
            dout = {
                k: decision.get(k)
                for k in ("action", "agent_name", "agent_pipeline", "task_type",
                          "needs_long_term_context", "toolkit_mode", "workflow",
                          "reasoning_depth", "disclosure_type")
                if decision.get(k) not in (None, "", [])
            }
            if decision.get("notes_for_agent"):
                dout["notes_for_agent"] = _clip(decision["notes_for_agent"], 400)
        elif name == "long_term_recall_node":
            din = {"query": _clip(wm.get("user_input"), 200)}
            dout = {"recall": "appended to context" if out.get("summary_text") else "(no older memories matched)"}
        elif name == "clarify_node":
            dout = {"question": _clip(out.get("response_text"), 400)}
        elif name == "nudge_node":
            dout = {"message": _clip(out.get("response_text"), 400)}
        elif name == "direct_response_node":
            din = {
                "user_input": _clip(wm.get("user_input"), 300),
                "toolkit_mode": decision.get("toolkit_mode"),
                "reasoning_depth": decision.get("reasoning_depth"),
            }
            dout = {"response": _clip(out.get("response_text"), 600)}
        elif name == "dispatch_node":
            din = {"action": decision.get("action"), "agent_name": decision.get("agent_name"),
                   "task_type": decision.get("task_type")}
            dout = {"agent_name": extra.get("agent_name"), "task_type": extra.get("task_type"),
                    "pipeline": out.get("agent_pipeline") or state.get("agent_pipeline")}
        elif name == "context_builder_node":
            din = {"agent_name": extra.get("agent_name"), "task_type": extra.get("task_type")}
            task = out.get("current_task") or state.get("current_task")
            if task is not None:
                ms = getattr(task, "memory_slice", None)
                dout = {
                    "instructions": _clip(getattr(task, "instructions", ""), 500),
                    "memory_slice_keys": sorted((getattr(ms, "relevant_profile", {}) or {}).keys()) if ms else [],
                    "params": getattr(task, "params", {}) or {},
                }
        elif name == "agent_executor_node":
            task = state.get("current_task")
            din = {"agent_name": getattr(task, "agent_name", None), "task_type": getattr(task, "task_type", None)}
            results = out.get("results") or state.get("results") or []
            if results:
                last = results[-1]
                st = getattr(last, "status", None)
                delta = getattr(last, "memory_delta", {}) or {}
                dout = {
                    "status": getattr(st, "value", st),
                    "output": _clip(getattr(last, "output", ""), 600),
                    "memory_delta_keys": sorted(delta.keys()),
                    "drafts": len(getattr(last, "draft_suggestions", []) or []),
                }
        elif name == "memory_merger_node":
            results = state.get("results") or []
            delta = (getattr(results[-1], "memory_delta", {}) if results else {}) or {}
            din = {"memory_delta_keys": sorted(delta.keys())}
            dout = {"applied": bool(delta)}
        elif name == "next_pipeline_agent_node":
            dout = {"pipeline_step": out.get("pipeline_step"), "pipeline": state.get("agent_pipeline")}
        elif name == "format_output_node":
            text = out.get("response_text") or state.get("response_text") or ""
            din = {"chars": len(text)}
            dout = {"response": _clip(text, 600), "status": out.get("status")}
    except Exception:
        pass

    io: dict = {}
    if din:
        io["in"] = din
    if dout:
        io["out"] = dout
    return io


def _traced_node(name: str, fn):
    """Wrap a graph node: time it, record it, attach its tool calls + I/O."""
    def wrapped(state: OrchestratorState) -> dict:
        started = time.time()
        out = fn(state) or {}
        ms = int((time.time() - started) * 1000)
        trace = list(state.get("execution_trace") or [])
        event: dict = {"kind": "node", "node": name, "ms": ms, "status": "ok"}
        detail = _node_detail(name, state, out)
        if detail:
            event["detail"] = detail
        io = _node_io(name, state, out)
        if io:
            event["io"] = io
        trace.append(event)
        try:
            # Lazy import avoids any module-load cycle with harness.
            from orchestrator.harness import drain_tool_calls
            for call in drain_tool_calls():
                trace.append({"kind": "tool", **call})
        except Exception:
            pass
        return {**out, "execution_trace": trace}

    wrapped.__name__ = f"traced_{name}"
    return wrapped


builder = StateGraph(OrchestratorState)

builder.add_node("intake_node",           _traced_node("intake_node", intake_node))
builder.add_node("summarize_node",         _traced_node("summarize_node", summarize_node))
builder.add_node("reason_node",            _traced_node("reason_node", reason_node))
builder.add_node("direct_response_node",   _traced_node("direct_response_node", direct_response_node))
builder.add_node("long_term_recall_node",  _traced_node("long_term_recall_node", long_term_recall_node))
builder.add_node("clarify_node",           _traced_node("clarify_node", clarify_node))
builder.add_node("nudge_node",             _traced_node("nudge_node", nudge_node))
builder.add_node("dispatch_node",          _traced_node("dispatch_node", dispatch_node))
builder.add_node("context_builder_node",   _traced_node("context_builder_node", context_builder_node))
builder.add_node("agent_executor_node",    _traced_node("agent_executor_node", agent_executor_node))
builder.add_node("memory_merger_node",     _traced_node("memory_merger_node", memory_merger_node))
builder.add_node("format_output_node",     _traced_node("format_output_node", format_output_node))

# Linear prefix.
builder.add_edge(START, "intake_node")
builder.add_edge("intake_node",   "summarize_node")
builder.add_edge("summarize_node", "reason_node")

# Branch after reasoning — four possible paths.
builder.add_conditional_edges(
    "reason_node",
    route_after_reason,
    {
        "direct_response_node":  "direct_response_node",
        "long_term_recall_node": "long_term_recall_node",
        "clarify_node":          "clarify_node",
        "nudge_node":            "nudge_node",
        "dispatch_node":         "dispatch_node",
    },
)

# Long-term recall merges back into the dispatch path.
builder.add_edge("long_term_recall_node", "dispatch_node")

# Short-circuit paths (clarify / nudge / direct_response).
builder.add_edge("clarify_node",          "format_output_node")
builder.add_edge("nudge_node",            "format_output_node")
builder.add_edge("direct_response_node",  "format_output_node")

builder.add_node("next_pipeline_agent_node", _traced_node("next_pipeline_agent_node", next_pipeline_agent_node))

# Main dispatch path.
builder.add_edge("dispatch_node",          "context_builder_node")
builder.add_edge("context_builder_node",   "agent_executor_node")
builder.add_edge("agent_executor_node",    "memory_merger_node")
builder.add_conditional_edges(
    "memory_merger_node",
    route_after_merger,
    {
        "next_pipeline_agent_node": "next_pipeline_agent_node",
        "format_output_node":       "format_output_node",
    },
)
builder.add_edge("next_pipeline_agent_node", "dispatch_node")

builder.add_edge("format_output_node", END)

app = builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Main orchestrator entry point
# ---------------------------------------------------------------------------

def run_orchestrator_turn(
    state: OrchestratorState,
    user_input: str,
    thread_id: str | None = None,
) -> OrchestratorState:
    """
    Run one full turn of the mentor loop.

    Args:
        state:      The OrchestratorState from the previous turn (or freshly
                    created with build_initial_state()).
        user_input: The raw text the user just typed.
        thread_id:  Optional thread identifier for LangGraph checkpointer persistence.
                    Defaults to working_memory.session_id.

    Returns:
        The updated OrchestratorState after the full graph has run.
        Read state["response_text"] for the assistant's reply.
    """
    # Inject user_input before the graph starts so intake_node can read it.
    state["working_memory"]["user_input"] = user_input
    session_id = thread_id or state["working_memory"].get("session_id") or "default_session"
    config = {"configurable": {"thread_id": session_id}}

    # Reset the per-turn execution trace and open a tool-call capture so the
    # architecture visualizer sees exactly what THIS turn invoked.
    state["execution_trace"] = []
    try:
        from orchestrator.harness import start_tool_capture
        start_tool_capture()
    except Exception:
        pass

    # Observability: open one tracing turn, run the graph, then close it.
    # The local report (per-step latency + failure point) prints when
    # MENTOR_DEBUG=1; a LangSmith run is posted too when credentials exist.
    from orchestrator.tracing import begin_turn, end_turn, measure
    _debug = os.getenv("MENTOR_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")
    begin_turn("mentor_turn", {"user_input": user_input, "session_id": session_id})
    try:
        with measure("graph_invoke"), component(
            "mentor_turn",
            tags=["mentor_turn", f"session:{session_id}"],
            metadata={"session_id": session_id, "user_input": user_input},
        ):
            _out = app.invoke(state, config=config)
        _report = end_turn("ok")
    except Exception as exc:
        _report = end_turn("error", str(exc))
        if _debug and _report:
            print(_report)
        raise
    if _debug and _report:
        print(_report)
    return _out


# ---------------------------------------------------------------------------
# __main__ — interactive CLI demo  (mirrors agents' __main__ blocks)
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    from dotenv import load_dotenv

    load_dotenv()

    print("Loading memory and ensuring schema...")
    memory_manager = MemoryManager()
    memory_manager.ensure_schema()

    session_id = str(uuid4())
    state = build_initial_state(memory_manager, session_id)

    print(f"Mentor session started: {session_id}")
    print("Type your message (empty line to exit).")
    print("-" * 60)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            print("Goodbye.")
            break

        try:
            state = run_orchestrator_turn(state, user_input)
        except Exception as exc:
            print(f"\nMentor (error): {exc}")
            continue

        print(f"\nMentor:\n{state['response_text']}")
        print("-" * 60)

    # Persist the session transcript (with timestamps) before exiting.
    saved = close_active_session(state, memory_manager)
    if saved:
        print(f"Session transcript saved ({saved} turns).")
