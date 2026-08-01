import os
import unicodedata
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_tavily import TavilySearch
from dotenv import load_dotenv
from agents.Linkedin_writer.State import LinkedInWriterState, build_result, build_initial_state
from agents.Linkedin_writer.RAG import _load_or_build_vectorstore
from schemas import AgentResult, AgentTask, ResultStatus, TaskSource
from schemas.memory import MemorySlice

load_dotenv()

search_tool = TavilySearch(max_results=3)
tools = [search_tool]

from orchestrator.llm import get_writer_llm

llm = get_writer_llm(temperature=0.4)
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)

def input_brief(state: LinkedInWriterState) -> dict:
    """
    Unpacks the incoming AgentTask into flat topic/goal/audience fields
    that all downstream nodes expect. Acts as the single entry point for
    normalizing both structured params and free-text instructions.
    """
    task = state["task"]
    
    # If the caller already gave us structured params, use those directly.
    # Otherwise fall back to the raw instructions string as the topic.
    topic    = task.params.get("topic") or task.instructions.strip()
    goal     = task.params.get("goal", "")
    audience = task.params.get("audience", "")

    # If goal/audience weren't in params, try to infer them with the LLM
    # (optional — only needed for free-text instructions like your example)
    if not goal or not audience:
        EXTRACT_PROMPT = (
            "Extract the following from this LinkedIn post request. "
            "Respond in this exact format (no other text):\n"
            "TOPIC: <topic>\nGOAL: <goal>\nAUDIENCE: <audience>\n\n"
            f"Request: {task.instructions}"
        )
        try:
            response = llm.invoke([("human", EXTRACT_PROMPT)])
            lines = {
                line.split(":")[0].strip(): ":".join(line.split(":")[1:]).strip()
                for line in response.content.strip().splitlines()
                if ":" in line
            }
            topic    = lines.get("TOPIC", topic)
            goal     = lines.get("GOAL", goal)
            audience = lines.get("AUDIENCE", audience)
        except Exception as e:
            print(f"[input_brief] extraction failed: {e}")

    return {"topic": topic, "goal": goal, "audience": audience}


from integrations.search import perform_web_search

def web_search_node(state: LinkedInWriterState) -> dict:
    """
    Searches the web for AI trends/facts using perform_web_search and fetches
    high-importance wins (importance >= 4) from memory.
    """
    topic    = state["topic"]
    goal     = state["goal"]
    audience = state["audience"]
    task     = state["task"]

    # 1. Fetch high-importance wins from memory slice
    recent_activity = task.memory_slice.recent_activity or []
    profile_activity = (task.memory_slice.relevant_profile or {}).get("recent_activity", [])
    all_activities = list(recent_activity) + list(profile_activity)

    win_notes: list[str] = []
    for item in all_activities:
        imp = getattr(item, "importance", None) or (item.get("importance") if isinstance(item, dict) else 0)
        if imp >= 4:
            content = getattr(item, "summary", None) or (item.get("summary") if isinstance(item, dict) else str(item))
            win_notes.append(f"- Verified Win: {content}")

    # 2. Live web search for AI trends
    query = f"{topic} AI engineering trends best practices 2026"
    search_results = ""
    try:
        search_results = perform_web_search(query, max_results=3)
    except Exception as e:
        print(f"[web_search_node] web search failed: {e}")

    # Merge win notes and search results into research_notes
    sections = []
    if win_notes:
        sections.append("High-Importance Project Wins & Achievements:\n" + "\n".join(win_notes))
    if search_results and "yielded no results" not in search_results:
        sections.append(search_results)

    research_notes = "\n\n".join(sections)
    return {"research_notes": research_notes}
    
    

# Built once at import time — not inside the node function.
_vectorstore = _load_or_build_vectorstore()
def voice_style_node(state: LinkedInWriterState) -> dict:
    """
    Retrieves past posts closest in topic/goal to the current brief, so the
    draft writer matches the user's actual voice instead of defaulting to
    generic LLM tone. Runs independently of web_search_node — both branch
    off the input brief and feed into the draft writer.

    NOTE (Bug 5 — not a bug, expected behaviour):
    task.memory_slice.relevant_profile and task.memory_slice.recent_activity
    are empty dicts/lists at this stage because the orchestrator / memory store
    is not wired up yet.  Voice consistency right now comes entirely from the
    hardcoded STYLE_GUIDE + the RAG-retrieved past posts.  Do not mistake this
    for the memory system working end-to-end — it is not.

    Args:
        state: The LinkedIn writer subgraph state

    Returns:
        dict: style_notes — merged style guide + retrieved voice examples
    """
    # Static brand-voice rules that ALWAYS apply, regardless of topic similarity.
    # RAG retrieval below finds topically-relevant examples; this covers the
    # constant stuff RAG can't reliably surface (formatting rules, banned phrases).
    STYLE_GUIDE = """
        Voice: direct, first-person, no corporate jargon, occasional dry humor.
        Formatting: short paragraphs (1-2 sentences), line breaks between ideas.
        Never use emojis or hashtags.
        Never end with a generic "Thoughts?" — ask something specific.
    """.strip()
    query = f"{state['topic']} {state['goal']}"

    try:
        results = _vectorstore.similarity_search(query, k=3)
        style_examples = [doc.page_content for doc in results]
    except Exception as e:
        print(f"[voice_style_node] retrieval failed: {e}")
        style_examples = []

    # Merge guide + retrieved examples into one string — matches State.py's
    # single `style_notes` field instead of two separate fields.
    formatted_examples = (
        "\n\n".join(f"Example {i+1}:\n{ex}" for i, ex in enumerate(style_examples))
        if style_examples
        else "(No past posts available — rely on the style guide only.)"
    )
    style_notes = f"Style guide:\n{STYLE_GUIDE}\n\nVoice examples (past posts):\n{formatted_examples}"

    return {"style_notes": style_notes}


def draft_writer_node(state: LinkedInWriterState) -> dict:
    """
    Writes (or revises) the LinkedIn post draft, grounded in distilled research
    notes and matched to the user's voice via retrieved style examples.

    Bug 1 fix: on revision passes, critic_feedback is injected as a *dedicated
    second system message* (not buried in the human turn) so the model cannot
    ignore it.  A visible [REVISION N] header is prepended to the human prompt
    so both the model and logs make it clear this is a retry.

    Bug 2 fix: max_length constraint is injected into the system prompt so the
    model is aware of the hard limit from the first token.

    Bug 3 fix: draft_writer is explicitly forbidden from citing external
    surveys/reports/studies — it may only use facts from the distilled research
    notes or the user's own project details.

    Args:
        state: The LinkedIn writer subgraph state

    Returns:
        dict: draft (the post text) and incremented revision_count
    """
    topic          = state["topic"]
    goal           = state["goal"]
    audience       = state["audience"]
    research_notes = state.get("research_notes", "")
    style_notes    = state.get("style_notes", "")
    critic_feedback = state.get("critic_feedback")
    previous_draft  = state.get("draft")
    revision_count  = state.get("revision_count", 0)

    # --- Bug 2: pull max_length from constraints, if present ---
    constraints = {}
    try:
        constraints = state["task"].memory_slice.constraints or {}
    except (AttributeError, KeyError):
        pass
    max_length = constraints.get("max_length")
    length_rule = (
        f"HARD LIMIT: the final post must be {max_length} characters or fewer "
        "(not words — characters). Count carefully before finishing. "
        "If over the limit, cut ruthlessly."
        if max_length
        else "Target roughly 150-200 words."
    )

    # --- Bug 3: system prompt forbids unverifiable citations ---
    WRITER_SYSTEM_PROMPT = (
        "You are an expert LinkedIn content writer. Write engaging, professional "
        "LinkedIn posts. Match the voice and formatting shown in the example "
        "posts as closely as possible — same sentence rhythm, same level of "
        "directness, same paragraph length.\n"
        f"{length_rule}\n"
        "Rules:\n"
        "  - Strong hook in the first line.\n"
        "  - One clear takeaway.\n"
        "  - Short, skimmable paragraphs (1-2 sentences max).\n"
        "  - End with a specific question or call-to-action.\n"
        "  - No emojis, no hashtags.\n"
        "  - NEVER cite external surveys, studies, or reports. Only use facts "
        "    from the distilled research notes below or the user's own project "
        "    details. If no research note supports a claim, do not make it."
    )

    context = (
        f"Topic: {topic}\n"
        f"Goal: {goal}\n"
        f"Audience: {audience}\n\n"
        f"{style_notes}\n\n"
        f"Distilled research notes (cite ONLY facts listed here):\n"
        f"{research_notes or '(none — write from personal project knowledge only)'}"
    )

    # --- Bug 1: on revision, feedback goes in its own system message ---
    if critic_feedback and previous_draft:
        human_prompt = (
            f"[REVISION {revision_count + 1}]\n\n"
            f"{context}\n\n"
            f"Previous draft (do NOT copy this verbatim):\n{previous_draft}\n\n"
            f"Write a fully revised draft that fixes every issue above."
        )
        messages = [
            ("system", WRITER_SYSTEM_PROMPT),
            # Second system message — critic feedback is impossible to miss
            ("system",
             f"CRITIC FEEDBACK — you MUST address ALL of the following before writing:\n"
             f"{critic_feedback}"),
            ("human", human_prompt),
        ]
    else:
        human_prompt = f"{context}\n\nWrite the LinkedIn post."
        messages = [("system", WRITER_SYSTEM_PROMPT), ("human", human_prompt)]

    try:
        response = llm.invoke(messages)
        draft = response.content
    except Exception as e:
        print(f"[draft_writer_node] generation failed: {e}")
        if previous_draft:
            # Keep the last good draft but don't increment — the revision
            # that failed shouldn't count as a valid attempt.
            draft = previous_draft
        else:
            # No prior draft exists; tag explicitly so the AgentResult
            # is never silently empty. Force the revision cap so the
            # critic loop exits immediately rather than retrying a broken state.
            draft = f"[DRAFT_FAILED: LLM call failed on attempt {revision_count + 1} — {e}]"
            return {
                "draft": draft,
                "revision_count": MAX_REVISIONS,  # force loop exit
            }

    return {
        "draft": draft,
        "revision_count": revision_count + 1,
    }


MAX_REVISIONS = 3


def critic_node(state: LinkedInWriterState) -> dict:
    """
    Reviews the current draft against the original brief and style rules.
    Returns critic_feedback (a string of actionable issues) when the draft
    needs work, or sets critic_approved=True to let it move forward.

    Bug 2 fix: after the LLM review, constraints from task.memory_slice are
    checked *programmatically* (not left to the LLM).  Any violations are
    appended as a [CONSTRAINT VIOLATIONS] block that draft_writer_node is
    required to fix.  This ensures max_length and avoid_topics are enforced
    even when the LLM would otherwise approve the draft.

    The LLM is prompted to act as a strict editor — it must either approve
    the draft outright or list *specific, numbered* issues. Vague praise or
    generic complaints are not accepted as valid feedback. This forces the
    revision loop to always make measurable progress.

    Args:
        state: The LinkedIn writer subgraph state

    Returns:
        dict: critic_approved (bool) and critic_feedback (str | None)
    """
    # --- Pull constraints early: needed to build the critic prompt ---
    constraints = {}
    try:
        constraints = state["task"].memory_slice.constraints or {}
    except (AttributeError, KeyError):
        pass

    avoid_topics = constraints.get("avoid_topics", [])
    # Humanize snake_case identifiers so the LLM understands them as natural
    # language topics (e.g. 'traditional_ml' → 'traditional ml').
    avoid_phrases = [kw.replace("_", " ").strip() for kw in avoid_topics]

    # Build the avoid-topics clause for the critic prompt.
    # Topic avoidance is *semantic*, not lexical — an LLM check is the
    # only reliable way to catch paraphrases like "classical ML approaches"
    # or "conventional machine learning" for the token 'traditional_ml'.
    avoid_clause = (
        "\nAlso check: does the draft discuss any of the following forbidden "
        "topics (even indirectly or by paraphrase)? If yes, flag it as a "
        f"numbered issue: {', '.join(repr(p) for p in avoid_phrases)}."
        if avoid_phrases else ""
    )

    CRITIC_SYSTEM_PROMPT = (
        "You are a ruthlessly honest LinkedIn content editor. "
        "Review the draft post against the brief and style notes below. "
        "If the post is good enough to publish, respond with exactly: APPROVED\n"
        "If it needs work, respond with a numbered list of specific, actionable "
        "issues — no praise, no preamble, just the problems. "
        "Check for: weak or generic hook, missing/vague CTA, invented statistics "
        "(any claim citing a survey/study/report not in the research notes), "
        f"emojis or hashtags, corporate jargon, paragraphs longer than 2 sentences."
        f"{avoid_clause}"
    )

    topic       = state["topic"]
    goal        = state["goal"]
    audience    = state["audience"]
    draft       = state.get("draft", "")
    style_notes = state.get("style_notes", "")

    human_prompt = (
        f"Brief:\nTopic: {topic}\nGoal: {goal}\nAudience: {audience}\n\n"
        f"{style_notes}\n\n"
        f"Draft to review:\n{draft}"
    )
    messages = [("system", CRITIC_SYSTEM_PROMPT), ("human", human_prompt)]

    try:
        response = llm.invoke(messages)
        raw = response.content.strip()
        llm_approved = raw.upper().startswith("APPROVED")
        llm_feedback = None if llm_approved else raw
    except Exception as e:
        print(f"[critic_node] critique failed: {e}")
        # Approve to avoid blocking the pipeline, but tag the feedback so
        # this is distinguishable from a genuine APPROVED in the AgentResult.
        return {
            "critic_approved": True,
            "critic_feedback": f"[AUTO-APPROVED: critic LLM call failed — draft was not reviewed. Error: {e}]",
        }

    # --- Programmatic constraint enforcement ---
    # max_length: checked here in code (LLMs are unreliable at counting chars).
    # avoid_topics: handled semantically by the critic LLM prompt above;
    #   lexical fallback below catches any literal occurrences the LLM missed
    #   (e.g. if the model outputs the raw snake_case token or the humanized
    #   phrase verbatim). Both layers together give full coverage.
    # Violations override an LLM APPROVED verdict.

    violations: list[str] = []

    max_len = constraints.get("max_length")
    if max_len is not None and len(draft) > max_len:
        violations.append(
            f"LENGTH VIOLATION: draft is {len(draft)} chars but max_length="
            f"{max_len}. Cut aggressively until the post is {max_len} chars or fewer."
        )

    # Lexical fallback: check both the raw token and the humanized phrase.
    # The LLM semantic check in the critic prompt is the primary defence;
    # this catches any literal substring leakage the LLM already flagged
    # (or that it missed on a weak model run).
    draft_lower = draft.lower()
    for raw_kw, human_phrase in zip(avoid_topics, avoid_phrases):
        if raw_kw.lower() in draft_lower or human_phrase in draft_lower:
            violations.append(
                f"TOPIC VIOLATION: draft contains '{human_phrase}' "
                f"(constraint: avoid_topics includes '{raw_kw}'). "
                "Remove all references, including paraphrases."
            )

    if violations:
        violation_block = "\n".join(f"- {v}" for v in violations)
        combined_feedback = (
            (llm_feedback + "\n\n" if llm_feedback else "")
            + "[CONSTRAINT VIOLATIONS — MUST FIX BEFORE APPROVAL]\n"
            + violation_block
        )
        return {"critic_approved": False, "critic_feedback": combined_feedback}

    if llm_approved:
        return {"critic_approved": True, "critic_feedback": None}
    return {"critic_approved": False, "critic_feedback": llm_feedback}


def should_revise(state: LinkedInWriterState) -> str:
    """
    Conditional edge after critic_node.
    Routes back to draft_writer_node for another revision, or forward to
    human_review once the critic is satisfied or the revision cap is hit.

    Returns:
        "revise" → back to draft_writer_node
        "done"   → forward to human_review / format_output_node
    """
    if not state.get("critic_approved") and state.get("revision_count", 0) < MAX_REVISIONS:
        return "revise"
    return "done"


def format_output_node(state: LinkedInWriterState) -> dict:
    """
    Final node. Picks the authoritative content (human edit > approved draft)
    and packages everything into the AgentResult contract the orchestrator
    expects, using the shared build_result() helper from State.py.

    Bug 4 fix: applies unicodedata NFKC normalisation and strips leading/
    trailing whitespace before setting final_content.  NFKC collapses unicode
    lookalikes (e.g. \u202f NARROW NO-BREAK SPACE → regular space) that the
    LLM occasionally emits.  .strip() removes the leading \n that Nemotron
    tends to prepend to its completions.

    Args:
        state: The LinkedIn writer subgraph state (after human review)

    Returns:
        dict: final_content (str) and result (AgentResult)
    """
    # Prefer the human's direct edit; fall back to the AI-approved draft.
    raw_content = state.get("human_edit") or state.get("draft") or ""

    # --- Bug 4: sanitise unicode quirks and strip surrounding whitespace ---
    final_content = unicodedata.normalize("NFKC", raw_content).strip()

    result = build_result({
        **state,
        "final_content": final_content,   # ensure build_result sees the resolved value
    })

    return {
        "final_content": final_content,
        "result": result,
    }

builder = StateGraph(LinkedInWriterState)

builder.add_node("input_brief",input_brief)
builder.add_node("voice_style_node",voice_style_node)
builder.add_node("web_search_node",web_search_node)
builder.add_node("draft_writer_node",draft_writer_node)
builder.add_node("critic_node",critic_node)
builder.add_node("format_output_node",format_output_node)

builder.add_edge(START,"input_brief")
builder.add_edge("input_brief","web_search_node")
builder.add_edge("input_brief","voice_style_node")
builder.add_edge("web_search_node","draft_writer_node")
builder.add_edge("voice_style_node","draft_writer_node")
builder.add_edge("draft_writer_node","critic_node")
builder.add_conditional_edges(
    "critic_node",
    should_revise,
    {
        "revise":"draft_writer_node",
        "done":"format_output_node"
    }
)
builder.add_edge("format_output_node",END)

app = builder.compile()


def run_linkedin_writer(task: AgentTask) -> AgentResult:
    """
    Uniform entry point used by the orchestrator.

    Takes the orchestrator's AgentTask, seeds the internal LangGraph state,
    invokes the compiled graph, and returns the packaged AgentResult.
    """
    initial_state = build_initial_state(task)
    final_state = app.invoke(initial_state)
    result = final_state.get("result")
    if result is None:
        # Defensive fallback: the graph should always populate result, but
        # if something fails upstream we still return a failed envelope.
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            task_type=task.task_type,
            status=ResultStatus.FAILED,
            output="LinkedIn writer did not produce a result.",
            error_message="Graph finished without a result node.",
        )
    return result


example = """
    write a linkedin post on topic as i have created a vectorless RAG project that is a part of my project
    so the goal is to show the recruiter as I am a person who can develop and deploy RAG and production level AI projects
    and this is just one of my projects
"""

if __name__ == "__main__":
    task = AgentTask(
        task_id="1",
        agent_name="linkedin_writer",
        task_type="linkedin_post",
        instructions=example,
        source=TaskSource.CHAT,             # ✅ required
        memory_slice=MemorySlice(
            agent_name = "linkedin_writer",
            task_type = "linkedin_post",
            constraints={
                "max_length" : 200,
                "avoid_topics" : ["traditional_ml"]
            }
        ),      # ✅ proper object
        params={
            "topic": "vectorless RAG project",
            "goal": "show recruiters I can build production AI",
            "audience": "AI/ML recruiters",
        },
    )
    result = run_linkedin_writer(task)
    print(result.model_dump_json(indent=2)) 