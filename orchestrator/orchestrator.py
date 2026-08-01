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

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from schemas import AgentResult, AgentTask, ResultStatus, TaskSource
from orchestrator.config import DEFAULT_TASK_TYPES, ROUTING_HINTS
from orchestrator.llm import get_reasoning_llm
from orchestrator.memory.store import MemoryManager, get_memory_manager
from orchestrator.registry import get_agent_spec
from orchestrator.state import OrchestratorState, build_initial_state

# Node helpers — pure functions imported from their focused modules.
from orchestrator.nodes.state_summarizer import build_summary
from orchestrator.nodes.reasoner import (
    ReasoningDecision,
    run_reasoner,
    decision_to_dict,
)
from orchestrator.nodes.context_builder import build_task as build_agent_task
from orchestrator.nodes.memory_merger import apply_memory_delta
from orchestrator.memory.retriever import MemoryRetriever


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def intake_node(state: OrchestratorState) -> dict:
    """
    Capture the incoming user message into working_memory and bump the
    turn counter.  The raw user_input must already be injected into
    state["working_memory"]["user_input"] before the graph is invoked
    (see run_orchestrator_turn() below).
    """
    wm = dict(state["working_memory"])
    wm["turn_count"] = wm.get("turn_count", 0) + 1
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
    Condense the three memory tiers into a compact text block that the
    reasoner can consume without blowing its context window.
    """
    user_input = state["working_memory"].get("user_input") or ""
    mm = state.get("memory_manager") or get_memory_manager()
    summary = build_summary(mm, user_input)
    return {"summary_text": summary}


def reason_node(state: OrchestratorState) -> dict:
    """
    Run the LLM reasoning core.  Reads summary_text, returns a structured
    ReasoningDecision stored as a plain dict in reasoning_decision.
    Falls back to a bare 'route' decision if the LLM call fails.
    """
    summary = state.get("summary_text") or ""
    try:
        decision = run_reasoner(summary)
    except Exception as exc:
        decision = ReasoningDecision(
            action="route",
            reasoning=f"Reasoner failed ({exc}); using keyword fallback.",
        )
    decision_dict = decision_to_dict(decision)
    pipeline = decision.agent_pipeline or ([decision.agent_name] if decision.agent_name else [])
    return {
        "reasoning_decision": decision_dict,
        "agent_pipeline": pipeline,
        "pipeline_step": 0,
    }


# ---------------------------------------------------------------------------
# Routing function (conditional edge after reason_node)
# ---------------------------------------------------------------------------

def route_after_reason(state: OrchestratorState) -> str:
    """
    Inspect reasoning_decision and return the name of the next node.

    Returns:
        "long_term_recall_node" — needs historical context before dispatching
        "clarify_node"           — ask the user a clarifying question
        "nudge_node"             — proactively send an encouragement message
        "dispatch_node"          — route to a sub-agent (no deep recall needed)
    """
    decision = state.get("reasoning_decision") or {}
    action = decision.get("action", "route")
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
    task_type = decision.get("task_type") or ""

    ALIAS_MAP = {
        "content_generator": "linkedin_writer",
        "linkedin": "linkedin_writer",
        "planner": "daily_planner",
        "learning": "learning_monitor",
        "decomposer": "goal_decomposer",
    }
    if agent_name in ALIAS_MAP:
        agent_name = ALIAS_MAP[agent_name]

    if agent_name:
        task_type = task_type or DEFAULT_TASK_TYPES.get(agent_name, "direct_response")
    else:
        # Fallback: cheap keyword matching.
        lowered = user_input.lower()
        for name, hints in ROUTING_HINTS.items():
            if any(hint in lowered for hint in hints):
                agent_name = name
                task_type = DEFAULT_TASK_TYPES[name]
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
    return {"working_memory": wm}


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
        
        result = spec.run(task)

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
    Turn AgentResults into final user-facing response. If multiple sub-agents
    executed in a pipeline (e.g. goal_decomposer -> daily_planner), combines
    their outputs into a cohesive response.
    """
    if state.get("response_text") is not None:
        wm = dict(state["working_memory"])
        wm["last_assistant_message"] = state["response_text"]
        _log_turn(state, state["response_text"])
        return {"working_memory": wm, "status": state.get("status", "done")}

    results = state.get("results") or []
    if not results:
        return {
            "response_text": "I couldn't process that — please try again.",
            "status": "failed",
        }

    # Format output (combine if multiple results in pipeline)
    formatted_outputs = []
    has_failed = False
    has_clarification = False

    for res in results:
        if res.status == ResultStatus.FAILED:
            formatted_outputs.append(f"⚠️ **{res.agent_name} Notice:** {res.error_message or 'Step failed'}")
            has_failed = True
        elif res.status == ResultStatus.NEEDS_CLARIFICATION:
            formatted_outputs.append(res.clarification_needed or "Context clarification needed.")
            has_clarification = True
        else:
            header = f"[ Helping Agent: {res.agent_name} | Task: {res.task_type} ]\n\n"
            text = res.output or f"{res.agent_name} completed."
            if res.draft_suggestions:
                suggestions_text = "\n\n".join(
                    f"--- Draft suggestion ({s.kind}) ---\n{s.content}"
                    for s in res.draft_suggestions
                )
                text = f"{text}\n\n{suggestions_text}\n\n[ drafts are not actioned automatically ]"
            formatted_outputs.append(header + text)

    response_text = ("\n\n" + ("=" * 60) + "\n\n").join(formatted_outputs) if len(results) > 1 else formatted_outputs[-1]
    status = "failed" if has_failed else ("needs_clarification" if has_clarification else "done")

    last_result = results[-1]
    wm = dict(state["working_memory"])
    wm["last_assistant_message"] = response_text
    wm["pending_draft_suggestions"] = last_result.draft_suggestions or []

    _log_turn(state, response_text, agent_invoked=last_result.agent_name)

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
    """Write user ↔ assistant exchange to episodic_events. Never raises."""
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
    except Exception as exc:
        print(f"[_log_turn] failed (non-fatal): {exc}")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Graph assembly & Checkpointer (Working Memory Checkpointing §3.1)
# ---------------------------------------------------------------------------

import sqlite3
import os
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

os.makedirs("data", exist_ok=True)
_checkpoint_conn = sqlite3.connect("data/checkpoints.sqlite", check_same_thread=False)
serde = JsonPlusSerializer(allowed_msgpack_modules=True)
checkpointer = SqliteSaver(_checkpoint_conn, serde=serde)

builder = StateGraph(OrchestratorState)

builder.add_node("intake_node",           intake_node)
builder.add_node("summarize_node",         summarize_node)
builder.add_node("reason_node",            reason_node)
builder.add_node("long_term_recall_node",  long_term_recall_node)
builder.add_node("clarify_node",           clarify_node)
builder.add_node("nudge_node",             nudge_node)
builder.add_node("dispatch_node",          dispatch_node)
builder.add_node("context_builder_node",   context_builder_node)
builder.add_node("agent_executor_node",    agent_executor_node)
builder.add_node("memory_merger_node",     memory_merger_node)
builder.add_node("format_output_node",     format_output_node)

# Linear prefix.
builder.add_edge(START, "intake_node")
builder.add_edge("intake_node",   "summarize_node")
builder.add_edge("summarize_node", "reason_node")

# Branch after reasoning — three possible paths.
builder.add_conditional_edges(
    "reason_node",
    route_after_reason,
    {
        "long_term_recall_node": "long_term_recall_node",
        "clarify_node":          "clarify_node",
        "nudge_node":            "nudge_node",
        "dispatch_node":         "dispatch_node",
    },
)

# Long-term recall merges back into the dispatch path.
builder.add_edge("long_term_recall_node", "dispatch_node")

# Clarify / nudge short-circuit paths.
builder.add_edge("clarify_node", "format_output_node")
builder.add_edge("nudge_node",   "format_output_node")

builder.add_node("next_pipeline_agent_node", next_pipeline_agent_node)

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
# Convenience entry point  (mirrors run_daily_planner / run_learning_monitor)
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
    return app.invoke(state, config=config)


# ---------------------------------------------------------------------------
# __main__ — interactive CLI demo  (mirrors agents' __main__ blocks)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
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
