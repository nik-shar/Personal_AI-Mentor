"""
orchestrator/architecture_graph.py

Static descriptor of the mentor's architecture for the visualizer (`/flow` page).

This is the *shape* of the system (what nodes and tools exist); the *per-turn
truth* (what actually ran) comes from the live `execution_trace` returned by
POST /api/chat. Keeping the shape in one Python place — served via
GET /api/architecture — means the diagram can't silently drift from the code.

Keep the edge lists in sync with the `builder.add_node` / `add_edge` calls in
`orchestrator/orchestrator.py` and each agent's module.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Orchestrator pipeline (orchestrator/orchestrator.py::builder)
# ---------------------------------------------------------------------------

ORCHESTRATOR_NODES: list[dict[str, str]] = [
    {"id": "intake_node", "label": "Intake", "kind": "node",
     "desc": "Write user_input into working memory, bump turn count"},
    {"id": "summarize_node", "label": "Summarize", "kind": "node",
     "desc": "Build the DNA context document (profile, memories, transcript)"},
    {"id": "reason_node", "label": "Reasoner", "kind": "llm",
     "desc": "LLM decides: route | direct_response | clarify | nudge"},
    {"id": "direct_response_node", "label": "Direct Response", "kind": "llm",
     "desc": "Mentor voice answer with the tool loop (harness + calendar + code)"},
    {"id": "long_term_recall_node", "label": "Long-term Recall", "kind": "tool",
     "desc": "pgvector semantic search over warm/cold memory tiers"},
    {"id": "clarify_node", "label": "Clarify", "kind": "node",
     "desc": "Ask the clarifying question the reasoner chose"},
    {"id": "nudge_node", "label": "Nudge", "kind": "node",
     "desc": "Proactive check-in message"},
    {"id": "dispatch_node", "label": "Dispatch", "kind": "node",
     "desc": "Resolve agent_name + task_type (validated against the registry)"},
    {"id": "context_builder_node", "label": "Context Builder", "kind": "node",
     "desc": "Assemble the AgentTask + trimmed MemorySlice for the agent"},
    {"id": "agent_executor_node", "label": "Agent Executor", "kind": "node",
     "desc": "Look up the agent in the registry and run its subgraph"},
    {"id": "memory_merger_node", "label": "Memory Merger", "kind": "node",
     "desc": "Persist the agent's memory_delta into the right tier"},
    {"id": "next_pipeline_agent_node", "label": "Pipeline Step", "kind": "node",
     "desc": "Advance multi-agent pipeline chaining"},
    {"id": "format_output_node", "label": "Format Output", "kind": "llm",
     "desc": "Synthesize the final mentor-voiced reply + log the turn"},
]

ORCHESTRATOR_EDGES: list[dict[str, str]] = [
    {"from": "START", "to": "intake_node"},
    {"from": "intake_node", "to": "summarize_node"},
    {"from": "summarize_node", "to": "reason_node"},
    {"from": "reason_node", "to": "direct_response_node"},
    {"from": "reason_node", "to": "clarify_node"},
    {"from": "reason_node", "to": "nudge_node"},
    {"from": "reason_node", "to": "long_term_recall_node"},
    {"from": "long_term_recall_node", "to": "dispatch_node"},
    {"from": "dispatch_node", "to": "context_builder_node"},
    {"from": "context_builder_node", "to": "agent_executor_node"},
    {"from": "agent_executor_node", "to": "memory_merger_node"},
    {"from": "memory_merger_node", "to": "next_pipeline_agent_node"},
    {"from": "memory_merger_node", "to": "format_output_node"},
    {"from": "next_pipeline_agent_node", "to": "dispatch_node"},
    {"from": "direct_response_node", "to": "format_output_node"},
    {"from": "clarify_node", "to": "format_output_node"},
    {"from": "nudge_node", "to": "format_output_node"},
    {"from": "format_output_node", "to": "END"},
]

# ---------------------------------------------------------------------------
# Specialist agent subgraphs (one container node on the main canvas)
# ---------------------------------------------------------------------------

AGENTS: dict[str, dict[str, Any]] = {
    "linkedin_writer": {
        "label": "LinkedIn Writer",
        "nodes": ["input_brief", "voice_style_node", "web_search_node",
                  "draft_writer_node", "critic_node", "format_output_node"],
    },
    "goal_decomposer": {
        "label": "Goal Decomposer",
        "nodes": ["input_parser", "clarify_timeframe", "list_vault", "delete_vault",
                  "edit_vault", "memory_reader", "web_researcher", "llm_architect",
                  "tutorial_architect", "deep_node_expander", "response_formatter",
                  "pack_result"],
    },
    "job_hunter": {
        "label": "Job Hunter",
        "nodes": ["input_parser", "jd_analyzer", "resume_tailor", "quality_critic",
                  "render_and_write", "application_logger", "pipeline_analyzer",
                  "fit_assessor", "job_searcher_node", "job_filter_node",
                  "job_scorer_node", "digest_formatter_node", "pack_result"],
    },
    "fallback": {
        "label": "Fallback",
        "nodes": ["web_search", "situation_facts", "tool_loop"],
    },
}

# ---------------------------------------------------------------------------
# Deterministic tool suite (orchestrator/harness.py)
# ---------------------------------------------------------------------------

TOOL_GROUPS: list[dict[str, Any]] = [
    {"name": "Computation", "tools": [
        {"name": "format_duration", "desc": "Format minutes as '2h 30m'"},
        {"name": "compute_learning_streak", "desc": "Deterministic streak math"},
        {"name": "trim_plan_to_fit", "desc": "Drop lowest-priority items to fit a budget"},
        {"name": "get_available_topic_nodes", "desc": "DAG traversal → unlocked topics"},
    ]},
    {"name": "Memory", "tools": [
        {"name": "save_daily_plan", "desc": "Persist a daily plan (+ calendar mirror)"},
        {"name": "log_learning_session", "desc": "Log a session, compute the streak"},
    ]},
    {"name": "Calendar", "tools": [
        {"name": "get_day_grid", "desc": "48-slot day grid with states"},
        {"name": "find_available_slots", "desc": "Locate free windows"},
        {"name": "place_time_block", "desc": "Place a block (anchor-guarded)"},
        {"name": "set_anchor", "desc": "Reserve recurring life anchors"},
    ]},
    {"name": "Code Explorer", "tools": [
        {"name": "read_file", "desc": "Read a file in the workspace sandbox"},
        {"name": "grep_search", "desc": "Regex search across the repo"},
        {"name": "list_directory", "desc": "List a directory"},
        {"name": "git_status", "desc": "Git status"},
        {"name": "git_log", "desc": "Recent commits"},
        {"name": "git_diff", "desc": "Working-tree diff"},
        {"name": "run_command", "desc": "Run an allowlisted test/lint command"},
        {"name": "propose_edit", "desc": "Propose a file edit (draft only)"},
    ]},
]


def get_architecture() -> dict[str, Any]:
    """The full static descriptor served to the visualizer."""
    return {
        "orchestrator": {
            "nodes": ORCHESTRATOR_NODES,
            "edges": ORCHESTRATOR_EDGES,
        },
        "agents": AGENTS,
        "tool_groups": TOOL_GROUPS,
    }
