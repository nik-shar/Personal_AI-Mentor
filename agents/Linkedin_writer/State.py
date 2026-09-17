"""
agents/Linkedin_writer/state.py

LangGraph state for the LinkedIn post writer sub-agent's internal subgraph:

    Input brief -> [Web research, Voice & style RAG] -> Draft writer
                -> Critic/verifier -> (loop: revise draft) -> Human review
                -> Format for output

This state is INTERNAL to this agent's own subgraph — it is not the
orchestrator's graph state. The agent receives one AgentTask (defined in
schemas/agent_io.py) from the orchestrator, unpacks it into this internal
state to run its own multi-step subgraph, then packages the result back
into a single AgentResult before returning control to the orchestrator.
That's what keeps the agent "stateless" from the orchestrator's point of
view: nothing here persists between runs, it only exists for the lifetime
of one subgraph execution.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from schemas import AgentResult, AgentTask, DraftSuggestion


class LinkedInWriterState(TypedDict):
    # --- incoming, set once at graph entry, never mutated after ---
    task: AgentTask
    # The full task from the orchestrator. Contains task.memory_slice
    # (voice/tone preferences, past post topics, project facts) and
    # task.params (e.g. {"topic_hint": "...", "audience": "..."}).
    # Kept as the raw object (not destructured into loose fields) so
    # nothing about the original request is lost as it flows through.

    # --- input brief node output: extracted/normalized from task ---
    topic: str
    goal: str                      # e.g. "build credibility", "share a project update"
    audience: str                  # e.g. "AI/ML recruiters", "general LinkedIn network"

    # --- parallel branch outputs ---
    research_notes: Optional[str]
    # from web research node — current stats/trends/news relevant to topic.
    # Optional because this node could fail or be skipped for topics that
    # don't need current-events grounding (e.g. a pure project update).

    style_notes: Optional[str]
    # from voice & style RAG node — retrieved past posts / tone guide.
    # This is where task.memory_slice's tone preferences and past post
    # history actually get pulled in and turned into concrete guidance
    # for the draft writer (e.g. "concise, no fluff, avoid emoji").

    # --- draft / critic loop ---
    draft: Optional[str]
    critic_feedback: Optional[str]
    # None if the critic approved; populated if it wants a revision.
    # Presence of feedback (not just a bool) is what drives the next
    # draft_writer call — the revision prompt is built from this text.

    revision_count: int
    # Starts at 0, incremented each time the critic sends it back.
    # The graph's conditional edge checks this against a max (e.g. 2-3)
    # to prevent an infinite critic <-> draft_writer loop.

    critic_approved: bool
    # Explicit flag rather than inferring from critic_feedback being None,
    # so the routing logic reads clearly at the conditional edge.

    # --- human review gate ---
    human_approved: Optional[bool]
    # None = not yet reviewed, True/False after Nikhil reviews. If you
    # implement this as a LangGraph interrupt() for human-in-the-loop,
    # this field is what gets set on resume.

    human_edit: Optional[str]
    # If Nikhil edits the draft directly during review rather than just
    # approving/rejecting, the edited text goes here and becomes the
    # final content instead of `draft`.

    # --- final output ---
    final_content: Optional[str]
    # The actual text to hand off — either the approved draft or
    # human_edit if one was made. Set by the "Format for output" node.

    result: Optional[AgentResult]
    # The final packaged AgentResult (wrapping a DraftSuggestion) that
    # this agent returns to the orchestrator. Built at the very last node.


def build_initial_state(task: AgentTask) -> LinkedInWriterState:
    """
    Helper to turn an incoming AgentTask into the subgraph's starting state.
    Keeping this as an explicit function (rather than inlining it at every
    call site) means there's one place to update if AgentTask's shape
    changes later.
    """
    return LinkedInWriterState(
        task=task,
        topic=task.params.get("topic_hint", task.instructions),
        goal=task.params.get("goal", ""),
        audience=task.params.get("audience", ""),
        research_notes=None,
        style_notes=None,
        draft=None,
        critic_feedback=None,
        revision_count=0,
        critic_approved=False,
        human_approved=None,
        human_edit=None,
        final_content=None,
        result=None,
    )


def build_result(state: LinkedInWriterState) -> AgentResult:
    """
    Packages the finished subgraph state into the AgentResult contract the
    orchestrator expects. This is what the final "Format for output" node
    should call before returning.

    NOTE: Uses .get() for all Optional fields because LangGraph only stores
    keys that nodes explicitly returned — fields never set by any node won't
    exist in the state dict even if declared in the TypedDict.

    Status derivation:
      - critic_approved=True                   → "success"  (clean pass)
      - critic_approved=False (max revisions)  → "partial"  (gave up after cap)
      - draft starts with [DRAFT_FAILED]       → "failed"
    """
    content = (
        state.get("human_edit")
        or state.get("final_content")
        or state.get("draft")
        or ""
    )

    critic_approved = state.get("critic_approved", False)
    revision_count  = state.get("revision_count", 0)

    if content.startswith("[DRAFT_FAILED"):
        status = "failed"
    elif critic_approved:
        status = "success"
    else:
        # Revision cap hit without critic approval — best effort, not verified.
        status = "partial"

    return AgentResult(
        task_id=state["task"].task_id,
        agent_name=state["task"].agent_name,
        task_type=state["task"].task_type,
        status=status,
        output=f"Drafted a LinkedIn post about: {state['topic']}",
        memory_delta={
            # e.g. append to a rolling "recent post topics" list so future
            # runs don't repeat themselves — actual merge logic lives in
            # the orchestrator, this is just the proposed diff.
            "last_linkedin_topic": state["topic"],
        },
        draft_suggestions=[
            DraftSuggestion(
                kind="linkedin_post",
                content=content,
                metadata={
                    "char_count": len(content),
                    "revision_count": revision_count,
                    "critic_approved": critic_approved,
                    "human_reviewed": state.get("human_approved") is True,
                },
            )
        ],
    )