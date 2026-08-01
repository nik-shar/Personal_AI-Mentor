"""
agents/Goal_Decomposer/goal_decomposer.py

Specialist LangGraph agent that decomposes high-level learning goals into
structured, dependency-aware Topic Graphs saved directly as Markdown files in Obsidian.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal
from concurrent.futures import ThreadPoolExecutor, as_completed
from langgraph.graph import END, START, StateGraph

from schemas import AgentResult, AgentTask, ResultStatus
from orchestrator.config import OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER
from orchestrator.llm import get_reasoning_llm
from orchestrator.memory.obsidian_graph import (
    write_topic_node,
    write_roadmap_index,
)
from integrations.search import perform_web_search
from schemas.memory import TopicNode
from agents.Goal_Decomposer.state import (
    ConceptualContent,
    AlgorithmicContent,
    HandsOnCodeContent,
    ReferenceContent,
    GoalDecompositionOutline,
    GoalDecompositionSpec,
    GoalDecomposerState,
    SubTopicOutline,
    SubTopicSpec,
    build_initial_state,
    build_result,
)


def slugify(text: str) -> str:
    """Helper to convert text into URL/file-safe slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "topic"


def validate_tutorial_quality(markdown_text: str) -> tuple[bool, str]:
    """
    Validate that generated tutorial Markdown is complete, meets minimum depth requirements,
    and does not contain lazy placeholders like '...' or 'TODO'.
    """
    if not markdown_text or not isinstance(markdown_text, str):
        return False, "Empty or non-string response."

    cleaned = markdown_text.strip()
    
    # 1. Length check: Must be at least 800 characters
    if len(cleaned) < 800:
        return False, f"Content too brief ({len(cleaned)} chars, minimum 800)."

    # 2. Lazy placeholder check: Match standalone '...' or 'TODO' / 'TBD'
    if re.search(r"(\n\s*\.\.\.\s*\n|\n\s*TODO|\n\s*TBD)", cleaned, re.IGNORECASE) or re.search(r"^#+ [^\n]+\n\s*\.\.\.", cleaned, re.MULTILINE):
        return False, "Contains lazy '...' or 'TODO' placeholders."

    # 3. Minimum structural headings check (at least 2 markdown headings)
    headings = re.findall(r"^#{1,3}\s+.+", cleaned, re.MULTILINE)
    if len(headings) < 2:
        return False, f"Insufficient structural headings ({len(headings)} found)."

    return True, "OK"


# ---------------------------------------------------------------------------
# Content-type registry
#
# Every subtopic is classified by the Architect (Phase 1) into one of these
# buckets, and gets its own structured-output schema, writer prompt, and
# search query template in Phase 2. This replaces the old approach of
# forcing every topic (DSA, theory, tooling, whatever) through one
# "write runnable Python + pitfalls + challenge" template.
# ---------------------------------------------------------------------------

ContentType = Literal["conceptual", "algorithmic", "hands_on_code", "reference"]

CONTENT_MODEL_BY_TYPE: dict[str, type] = {
    "conceptual": ConceptualContent,
    "algorithmic": AlgorithmicContent,
    "hands_on_code": HandsOnCodeContent,
    "reference": ReferenceContent,
}

WRITER_PROMPT_BY_TYPE: dict[str, str] = {
    "conceptual": (
        "You are a principal technical educator explaining a conceptual or theoretical topic "
        "for self-study. This topic is about understanding and judgment, not producing code.\n\n"
        "Rules:\n"
        "1. concept_overview: Start with an intuitive real-world analogy, then a precise formal definition.\n"
        "2. tradeoffs_and_comparisons: Compare this concept against the alternatives or related "
        "approaches a practitioner would actually weigh it against.\n"
        "3. common_misconceptions: 2-3 things learners typically get wrong or oversimplify about this topic.\n"
        "4. reflection_prompt: A specific question or scenario the learner should reason through "
        "themselves (no code required) to check they've internalized the concept.\n"
        "Do NOT invent a code example or coding challenge for this topic."
    ),
    "algorithmic": (
        "You are a principal technical author teaching a Data Structures & Algorithms topic "
        "for self-study, aimed at an engineer preparing for real problem-solving and interviews.\n\n"
        "Rules:\n"
        "1. concept_overview: Intuitive framing of the problem this technique/structure solves, then "
        "a precise technical definition.\n"
        "2. approach_breakdown: Walk through the approach(es) to this problem, from the naive/brute-force "
        "idea to the optimized one, explaining the key insight that improves each step.\n"
        "3. pseudocode_or_code: Prefer clear pseudocode. Only use real, runnable code if it materially "
        "improves clarity over pseudocode for this specific topic. Do not pad length artificially.\n"
        "4. complexity_analysis: Time and space complexity (best/average/worst case where relevant) "
        "for each approach discussed, with a one-line justification for each.\n"
        "5. common_pitfalls: 2-3 specific mistakes learners make implementing or applying this "
        "(off-by-one errors, wrong invariant, misjudging complexity, edge cases, etc).\n"
        "6. practice_problem: One concrete practice problem (with a rough difficulty level) that "
        "exercises this exact topic, not a generic 'implement a demo'."
    ),
    "hands_on_code": (
        "You are a principal technical author writing a practical, code-first tutorial on a specific "
        "library, tool, framework, or API for self-study.\n\n"
        "Rules:\n"
        "1. concept_overview: Intuitive real-world analogy followed by a precise technical definition.\n"
        "2. code_example: A complete, production-ready, runnable code snippet with full imports, "
        "type hints where applicable, inline comments, and error handling. Length should match what's "
        "needed to be correct and complete for this topic — don't pad or artificially truncate.\n"
        "3. common_pitfalls: 2-3 specific production watchouts, performance/memory bugs, or common "
        "developer errors for this exact topic.\n"
        "4. hands_on_challenge: A specific, scoped practice task (roughly 20-40 minutes) with concrete "
        "input/output requirements."
    ),
    "reference": (
        "You are writing a concise technical reference entry for self-study — the learner needs to "
        "look up correct usage and compare options quickly, not read a long narrative.\n\n"
        "Rules:\n"
        "1. concept_overview: One or two sentences on what this is and when you'd reach for it.\n"
        "2. comparison_table: A markdown table comparing the relevant options/variants/parameters "
        "(only include this if there is a genuine comparison to make; otherwise state there isn't one "
        "and instead show canonical usage patterns).\n"
        "3. canonical_usage: The standard, correct way to use this (short code/syntax snippets are fine "
        "here, but keep them minimal — this is a reference, not a full example).\n"
        "4. common_pitfalls: 1-2 things people commonly get wrong when looking this up."
    ),
}


def search_query_for(topic: str, spec_title: str, content_type: str) -> str:
    """Build a content-type-aware search query instead of always assuming 'python documentation'."""
    suffix_by_type = {
        "conceptual": "explained tradeoffs comparison",
        "algorithmic": "algorithm explanation time complexity",
        "hands_on_code": "python documentation tutorial",
        "reference": "reference documentation syntax",
    }
    suffix = suffix_by_type.get(content_type, "tutorial")
    return f"{spec_title} {topic} {suffix} 2026"


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------

def input_parser(state: GoalDecomposerState) -> dict:
    """Extract intent (create, edit, delete, list), target topics/graphs, and timeframe parameters."""
    raw = state["raw_instructions"]
    task = state["task"]
    params = task.params or {}

    raw_lower = raw.lower()
    action_type = params.get("action_type") or "create"

    # Intent detection from raw instructions
    if any(k in raw_lower for k in ("list roadmap", "list vault", "show roadmap", "show vault", "list my graph", "view roadmaps")):
        action_type = "list"
    elif any(k in raw_lower for k in ("delete roadmap", "remove roadmap", "delete graph", "remove graph", "delete topic", "remove topic")):
        action_type = "delete"
    elif any(k in raw_lower for k in ("add topic", "edit roadmap", "update roadmap", "edit topic", "update topic", "change status")):
        action_type = "edit"

    target_days = params.get("days")
    target_hours = params.get("hours_per_day") or 3.0

    days_match = re.search(r"(\d+)\s*days?", raw_lower)
    if days_match:
        try:
            target_days = int(days_match.group(1))
        except ValueError:
            pass

    weeks_match = re.search(r"(\d+)\s*weeks?", raw_lower)
    if weeks_match and not target_days:
        try:
            target_days = int(weeks_match.group(1)) * 7
        except ValueError:
            pass

    hours_match = re.search(r"(\d+(?:\.\d+)?)\s*hours?", raw_lower)
    if hours_match:
        try:
            target_hours = float(hours_match.group(1))
        except ValueError:
            pass

    goal_topic = params.get("topic") or raw
    if "The user requested:" in goal_topic:
        goal_topic = goal_topic.split("The user requested:")[-1].strip().strip('"').strip("'")

    target_graph_id = params.get("graph_id") or params.get("target_graph")
    target_node_id = params.get("node_id") or params.get("target_node")

    if not target_graph_id:
        m_graph = re.search(r"(?:graph|roadmap)\s+['\"]?([a-z0-9_\-\s]+)['\"]?", raw_lower)
        if m_graph:
            target_graph_id = m_graph.group(1).strip()
        else:
            target_graph_id = goal_topic

    return {
        "action_type": action_type,
        "goal_topic": goal_topic,
        "target_days": target_days,
        "target_hours_per_day": target_hours,
        "target_graph_id": target_graph_id,
        "target_node_id": target_node_id,
        "edit_payload": params.get("edit_payload"),
    }


def clarify_timeframe_node(state: GoalDecomposerState) -> dict:
    """Ask user for target timeframe when unconstrained."""
    topic = state.get("goal_topic") or "this learning goal"
    msg = (
        f"I'd love to break down **{topic}** into a structured Obsidian roadmap! "
        "To tailor the schedule to you, what timeframe or timeline do you have in mind? "
        "(e.g. 3-day crash course, 1 week, 2 weeks, or how many hours per day?)"
    )
    return {
        "feedback_message": msg,
        "decomposition": None,
        "written_files": [],
        "index_file_path": "",
        "memory_delta": {},
    }


def list_vault_node(state: GoalDecomposerState) -> dict:
    """List all available roadmaps in the vault with node counts and progress."""
    from orchestrator.memory.obsidian_graph import list_vault_roadmaps
    roadmaps = list_vault_roadmaps(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)

    if not roadmaps:
        msg = f"📂 **Vault is currently empty.** No learning roadmaps found in `{OBSIDIAN_VAULT_PATH}/{OBSIDIAN_TOPIC_FOLDER}`.\n\nSay *'Create a 3-day roadmap for [topic]'* to get started!"
        return {"feedback_message": msg, "written_files": [], "memory_delta": {}}

    lines = [
        f"🗺️ **Obsidian Vault Roadmaps ({len(roadmaps)} found):**",
        f"Location: `{OBSIDIAN_VAULT_PATH}/{OBSIDIAN_TOPIC_FOLDER}`",
        "",
    ]
    for r in roadmaps:
        title = r["title"]
        total = r["total_nodes"]
        done = r["completed"]
        hours = r["total_hours"]
        lines.append(f"- 📌 **{title}** (`{r['topic_id']}`)")
        lines.append(f"  - Progress: {done}/{total} completed | Total time: {hours}h")
        if r["nodes"]:
            lines.append("  - Topics: " + ", ".join(f"`{n['title']}` [{n['status']}]" for n in r["nodes"][:4]))
        lines.append("")

    return {
        "feedback_message": "\n".join(lines),
        "written_files": [],
        "memory_delta": {},
    }


def delete_vault_node(state: GoalDecomposerState) -> dict:
    """Delete a topic graph or single node from the vault."""
    from orchestrator.memory.obsidian_graph import delete_topic_graph, delete_topic_node, load_topic_graphs

    target = state.get("target_graph_id") or state.get("goal_topic") or ""

    ok, count, deleted_files = delete_topic_graph(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, target)
    if ok:
        msg = f"🗑️ **Deleted Roadmap '{target}' from Obsidian Vault.**\nRemoved {count} file(s): {', '.join(deleted_files)}"
        remaining_graphs = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
        memory_delta = {
            "topic_graphs": [g.model_dump(mode="json") for g in remaining_graphs],
            "activity_log": [f"Deleted topic graph '{target}' from Obsidian vault."],
        }
        return {
            "feedback_message": msg,
            "written_files": ["deleted"],
            "memory_delta": memory_delta,
        }

    ok_node, node_msg = delete_topic_node(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, target)
    if ok_node:
        remaining_graphs = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
        memory_delta = {
            "topic_graphs": [g.model_dump(mode="json") for g in remaining_graphs],
            "activity_log": [node_msg],
        }
        return {
            "feedback_message": f"🗑️ **{node_msg}**",
            "written_files": ["deleted"],
            "memory_delta": memory_delta,
        }

    msg = f"⚠️ Could not find roadmap or node matching '{target}' in vault `{OBSIDIAN_VAULT_PATH}/{OBSIDIAN_TOPIC_FOLDER}`."
    return {"feedback_message": msg, "written_files": [], "memory_delta": {}}


def edit_vault_node(state: GoalDecomposerState) -> dict:
    """Edit node properties or add a new subtopic node to an existing roadmap."""
    from orchestrator.memory.obsidian_graph import edit_topic_node, write_topic_node, load_topic_graphs, _regenerate_roadmap_index

    target = state.get("target_graph_id") or state.get("goal_topic") or ""
    payload = state.get("edit_payload") or {}

    if payload and ("node_id" in payload or "node_title" in payload):
        n_target = payload.get("node_id") or payload.get("node_title")
        ok, msg = edit_topic_node(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, n_target, payload)
        if ok:
            remaining_graphs = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
            memory_delta = {
                "topic_graphs": [g.model_dump(mode="json") for g in remaining_graphs],
                "activity_log": [msg],
            }
            return {"feedback_message": f"✏️ **{msg}**", "written_files": ["updated"], "memory_delta": memory_delta}

    raw = state["raw_instructions"]
    node_title = state.get("target_node_id") or raw
    if "The user requested:" in node_title:
        node_title = node_title.split("The user requested:")[-1].strip().strip('"').strip("'")

    graph_id = slugify(target)
    node_id = f"tn_{slugify(node_title)}"

    node = TopicNode(
        id=node_id,
        title=node_title,
        estimated_hours=payload.get("estimated_hours", 1.5),
        status="not_started",
        prerequisites=[],
        notes=payload.get("notes", f"Added to roadmap '{target}'."),
    )

    written_path = write_topic_node(
        vault_path=OBSIDIAN_VAULT_PATH,
        folder=OBSIDIAN_TOPIC_FOLDER,
        graph_id=graph_id,
        graph_title=target,
        node=node,
        day=payload.get("day", 1),
    )
    _regenerate_roadmap_index(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER, graph_id)

    remaining_graphs = load_topic_graphs(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)
    memory_delta = {
        "topic_graphs": [g.model_dump(mode="json") for g in remaining_graphs],
        "activity_log": [f"Added new topic '{node_title}' to roadmap '{target}' in vault."],
    }

    msg = f"✏️ **Added Topic '{node_title}' to Roadmap '{target}' in Obsidian Vault.**\nFile: `{written_path}`"
    return {
        "feedback_message": msg,
        "written_files": [written_path],
        "memory_delta": memory_delta,
    }


def memory_reader(state: GoalDecomposerState) -> dict:
    """Unpack relevant profile facts and existing Obsidian graphs."""
    profile = state["task"].memory_slice.relevant_profile or {}

    return {
        "preferences": profile.get("preferences") or {},
        "existing_graphs": profile.get("topic_graphs") or [],
    }


def web_researcher(state: GoalDecomposerState) -> dict:
    """Perform live web documentation search to fetch exact, current syntax before decomposition."""
    topic = state["goal_topic"] or state["raw_instructions"]
    query = f"{topic} tutorial curriculum outline 2026"
    try:
        summary = perform_web_search(query, max_results=3)
    except Exception as exc:
        print(f"[goal_decomposer] web search error: {exc}")
        summary = ""
    return {"web_research_summary": summary}


def llm_architect(state: GoalDecomposerState) -> dict:
    """Phase 1: High-level Curriculum Architect. Generates DAG topology, subtopic outlines,
    AND classifies each subtopic's content_type so Phase 2 doesn't force one fixed template
    (code-heavy, pitfalls, coding challenge) onto every topic regardless of subject."""
    raw = state["raw_instructions"]
    days = state["target_days"] or 3
    hours = state["target_hours_per_day"] or 3.0
    web_context = state.get("web_research_summary", "").strip()

    system_prompt = (
        "You are a principal AI systems architect and senior technical curriculum developer.\n"
        "Your task is to decompose the user's requested learning goal into a logical, structured "
        "Directed Acyclic Graph (DAG) outline of subtopics for self-study.\n\n"
        "Rules:\n"
        "1. Focus on logical prerequisite mapping, realistic hours per subtopic (use judgment — "
        "typically 0.5h to 4h, but let genuine topic complexity decide), and clear subtopic scope.\n"
        f"2. Organise topics cleanly across {days} day(s), targeting ~{hours}h per day total.\n"
        "3. Define prerequisite relationships accurately using EXACT subtopic titles from your breakdown.\n"
        "   - Entry-level topics on Day 1 MUST have empty prerequisite lists.\n"
        "   - Advanced topics MUST list their prerequisite subtopic titles.\n"
        "4. Provide a clear 'what_to_cover' summary of key concepts to guide deep tutorial creation.\n"
        "5. For EACH subtopic, classify its content_type based on what actually best serves that "
        "specific topic — do not default to the same type for every subtopic:\n"
        "   - 'conceptual': theory, tradeoffs, architecture/design ideas with no natural code artifact "
        "(e.g. CAP theorem, SOLID principles, why indexes speed up queries).\n"
        "   - 'algorithmic': data structures & algorithms topics best taught via approach breakdown, "
        "pseudocode, and complexity analysis rather than a single runnable script "
        "(e.g. binary search, dynamic programming, graph traversal).\n"
        "   - 'hands_on_code': practical usage of a specific library, framework, tool, or API where a "
        "complete runnable code example is the natural teaching artifact (e.g. using pandas groupby, "
        "setting up a FastAPI route).\n"
        "   - 'reference': syntax/API lookups or comparisons best taught as a compact table or cheat "
        "sheet rather than a narrative (e.g. list of string methods, HTTP status codes).\n"
        "Most real curricula mix several of these types — do not force every subtopic into one bucket."
    )

    user_prompt = (
        f"User Request: \"{raw}\"\n"
        f"Goal Topic: {state['goal_topic']}\n"
        f"Time Budget: {days} days at ~{hours} hours per day.\n\n"
        f"Live Web Research Context:\n{web_context if web_context else '(No web search context retrieved)'}"
    )

    try:
        from orchestrator.llm import get_reasoning_llm
        llm = get_reasoning_llm(temperature=0.3)
        structured_llm = llm.with_structured_output(GoalDecompositionOutline)
        outline: GoalDecompositionOutline = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
    except Exception as exc:
        print(f"[goal_decomposer] Phase 1 LLM Architect error: {exc}")
        outline = None

    return {"outline": outline}


def _expand_single_subtopic_node(
    spec: SubTopicOutline,
    goal_topic: str,
    graph_title: str,
    user_profile_context: str,
    llm: Any,
    max_retries: int = 2,
) -> tuple[TopicNode, int, list[str], dict[str, Any], str, SubTopicSpec]:
    """Helper worker to perform web research, LLM invocation, quality validation, and retries for a single subtopic node."""
    spec_title = spec.title.strip()
    node_id = f"tn_{slugify(spec_title)}"
    content_type: str = getattr(spec, "content_type", None) or "hands_on_code"
    if content_type not in CONTENT_MODEL_BY_TYPE:
        content_type = "hands_on_code"

    # Targeted web search per node
    query = search_query_for(goal_topic, spec_title, content_type)
    try:
        node_web_context = perform_web_search(query, max_results=2)
    except Exception as exc:
        print(f"[goal_decomposer] per-node web search error for '{spec_title}': {exc}")
        node_web_context = ""

    type_prompt = WRITER_PROMPT_BY_TYPE.get(content_type, WRITER_PROMPT_BY_TYPE["hands_on_code"])

    system_prompt = (
        f"You are an elite, mentor-grade AI Systems Architect and Principal Staff Engineer.\n"
        f"Write a comprehensive, SOTA, high-density tutorial note in Markdown format.\n\n"
        f"SPECIALIST TEACHING GUIDANCE:\n{type_prompt}\n\n"
        "STRICT QUALITY & COMPLETENESS RULES:\n"
        "1. NEVER use placeholders like '...', 'TODO', 'TBD', or truncated snippets. Write every section out in full.\n"
        "2. Write out EVERY explanation, code block, query, table, or practice problem in complete, runnable detail.\n"
        "3. If learner context includes C++ background or specific interview goals, adapt explanations directly.\n"
        "4. Return clean, beautifully structured Markdown text with clear headings."
    )

    user_prompt = (
        f"Subtopic Title: {spec_title}\n"
        f"Content Classification: {content_type}\n"
        f"Overall Roadmap: {graph_title}\n"
        f"Key Scope to Cover: {spec.what_to_cover}\n"
        f"Prerequisites: {', '.join(spec.prerequisite_titles) if spec.prerequisite_titles else 'None'}\n\n"
        f"{user_profile_context}\n"
        f"Targeted Web Search Context:\n{node_web_context if node_web_context else '(No web context retrieved)'}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    ped_details: dict[str, str] = {}
    suggested_resources: list[str] = []

    for attempt in range(max_retries + 1):
        try:
            res = llm.invoke(messages)
            markdown_text = res.content if hasattr(res, "content") else str(res)

            if markdown_text.startswith("```markdown"):
                markdown_text = markdown_text[11:].strip()
            elif markdown_text.startswith("```"):
                markdown_text = markdown_text[3:].strip()
            if markdown_text.endswith("```"):
                markdown_text = markdown_text[:-3].strip()

            is_valid, reason = validate_tutorial_quality(markdown_text)
            if is_valid:
                ped_details = {"markdown_body": markdown_text}
                break
            else:
                print(f"[goal_decomposer] Quality validation failed for '{spec_title}' (attempt {attempt + 1}/{max_retries + 1}): {reason}")
                if attempt < max_retries:
                    messages.append({"role": "assistant", "content": markdown_text})
                    messages.append({
                        "role": "user",
                        "content": f"CRITICAL QUALITY REJECTION: Your previous output was rejected because: '{reason}'. Re-write the tutorial for '{spec_title}' in full depth without using '...' or stub placeholders."
                    })
                else:
                    print(f"[goal_decomposer] Max retries reached for '{spec_title}'. Accepting latest response.")
                    ped_details = {"markdown_body": markdown_text}
        except Exception as exc:
            print(f"[goal_decomposer] Deep node expansion error for '{spec_title}' (attempt {attempt + 1}): {exc}")
            if attempt == max_retries:
                ped_details = {"markdown_body": f"## Overview\nReview core concepts for {spec_title}."}

    node = TopicNode(
        id=node_id,
        title=spec_title,
        estimated_hours=spec.estimated_hours,
        status="not_started",
        prerequisites=[],
        resources=suggested_resources,
        notes=spec.what_to_cover,
    )

    subtopic_spec = SubTopicSpec(
        title=spec_title,
        estimated_hours=spec.estimated_hours,
        day=spec.day,
        prerequisite_titles=spec.prerequisite_titles,
        what_to_cover=spec.what_to_cover,
        content_type=content_type,
        content_details=ped_details,
        suggested_resources=suggested_resources,
    )

    return (node, spec.day, spec.prerequisite_titles, ped_details, content_type, subtopic_spec)


def deep_node_expander(state: GoalDecomposerState) -> dict:
    """Phase 2: Deep Node Expansion Loop. Uses ThreadPoolExecutor to expand subtopics concurrently,
    validating tutorial quality programmatically per node to prevent stubs and '...' placeholders."""
    outline = state.get("outline")
    if not outline or not outline.subtopics:
        return {"written_files": [], "index_file_path": "", "decomposition": None}

    graph_id = slugify(outline.graph_title or state["goal_topic"])
    graph_title = outline.graph_title or state["goal_topic"]

    from orchestrator.llm import get_deep_reasoning_llm

    llm = get_deep_reasoning_llm(temperature=0.6)

    # Extract learner profile context once for all parallel workers
    task = state.get("task")
    user_profile_context = ""
    if task:
        instructions = task.instructions or ""
        if task.memory_slice:
            prof = task.memory_slice.relevant_profile or {}
            bio = prof.get("bio_summary") or ""
            target_roles = ", ".join(prof.get("target_roles") or [])
            habits = ", ".join(prof.get("working_habits") or [])
            user_profile_context = (
                f"LEARNER BACKGROUND & CONTEXT:\n"
                f"- User Request: {instructions}\n"
                f"- Profile Bio: {bio}\n"
                f"- Target Roles: {target_roles}\n"
                f"- Working Habits: {habits}\n"
            )

    # Step 1: Expand all subtopic nodes in parallel (max_workers=3)
    results_map: dict[str, tuple[TopicNode, int, list[str], dict[str, Any], str, SubTopicSpec]] = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_spec = {
            executor.submit(
                _expand_single_subtopic_node,
                spec,
                state["goal_topic"],
                graph_title,
                user_profile_context,
                llm,
            ): spec
            for spec in outline.subtopics
        }

        for future in as_completed(future_to_spec):
            spec = future_to_spec[future]
            try:
                node, day, prereqs, ped_details, content_type, sub_spec = future.result()
                results_map[spec.title.strip()] = (node, day, prereqs, ped_details, content_type, sub_spec)
            except Exception as exc:
                print(f"[goal_decomposer] Worker exception for '{spec.title}': {exc}")

    # Reassemble results in original outline order
    title_to_node: dict[str, TopicNode] = {}
    subtopic_meta: dict[str, tuple[int, list[str], dict[str, Any], str]] = {}
    converted_subtopic_specs: list[SubTopicSpec] = []
    written_files: list[str] = []
    nodes_by_day: dict[int, list[tuple[TopicNode, list[str]]]] = {}

    for spec in outline.subtopics:
        spec_title = spec.title.strip()
        if spec_title in results_map:
            node, day, prereqs, ped_details, content_type, sub_spec = results_map[spec_title]
            title_to_node[spec_title] = node
            subtopic_meta[node.id] = (day, prereqs, ped_details, content_type)
            converted_subtopic_specs.append(sub_spec)

    # Step 2: Resolve prerequisites into node IDs
    for spec in outline.subtopics:
        spec_title = spec.title.strip()
        if spec_title in title_to_node:
            node = title_to_node[spec_title]
            for prereq_title in spec.prerequisite_titles:
                p_title = prereq_title.strip()
                if p_title in title_to_node:
                    node.prerequisites.append(title_to_node[p_title].id)

    # Step 3: Write markdown files to Obsidian vault
    for spec in outline.subtopics:
        spec_title = spec.title.strip()
        if spec_title in title_to_node:
            node = title_to_node[spec_title]
            day, prereq_titles, ped_details, content_type = subtopic_meta[node.id]

            file_path = write_topic_node(
                vault_path=OBSIDIAN_VAULT_PATH,
                folder=OBSIDIAN_TOPIC_FOLDER,
                graph_id=graph_id,
                graph_title=graph_title,
                node=node,
                day=day,
                prereq_titles=prereq_titles,
                pedagogical_details=ped_details,
            )
            written_files.append(file_path)

            if day not in nodes_by_day:
                nodes_by_day[day] = []
            nodes_by_day[day].append((node, prereq_titles))

    # Step 4: Write Roadmap Index File
    index_file = write_roadmap_index(
        vault_path=OBSIDIAN_VAULT_PATH,
        folder=OBSIDIAN_TOPIC_FOLDER,
        graph_id=graph_id,
        graph_title=graph_title,
        nodes_by_day=nodes_by_day,
    )

    # Step 5: Construct TopicGraph for memory store
    from schemas.memory import TopicGraph
    created_topic_graph = TopicGraph(
        topic_id=graph_id,
        title=graph_title,
        nodes={node.id: node for node in title_to_node.values()},
    )

    decomposition = GoalDecompositionSpec(
        graph_title=graph_title,
        total_days=outline.total_days,
        subtopics=converted_subtopic_specs,
        summary_notes=outline.summary_notes,
    )

    return {
        "written_files": written_files,
        "index_file_path": index_file,
        "created_topic_graph": created_topic_graph,
        "decomposition": decomposition,
    }



def response_formatter(state: GoalDecomposerState) -> dict:
    """Build human-readable summary response for the user."""
    # List, Edit, Delete branches already constructed feedback_message
    if state.get("feedback_message") and state.get("action_type") in ("list", "edit", "delete"):
        return {
            "feedback_message": state["feedback_message"],
            "memory_delta": state.get("memory_delta", {}),
        }

    decomp = state["decomposition"]
    written = state["written_files"]

    if not decomp or not written:
        return {"feedback_message": "I was unable to create the roadmap. Please try rephrasing your goal."}

    graph_title = decomp.graph_title or "Learning Roadmap"
    lines: list[str] = [
        f"🗺️ **Created Detailed Pedagogical Roadmap in Obsidian: {graph_title}**",
        f"Vault location: `{OBSIDIAN_VAULT_PATH}/{OBSIDIAN_TOPIC_FOLDER}`",
        f"Main index file: `{state['index_file_path']}`",
        "",
        f"**Summary:** {decomp.summary_notes}",
        "",
        "### 📋 Breakdown of Created Topics:",
    ]

    # Group subtopics by day for summary print
    nodes_by_day: dict[int, list[Any]] = {}
    for spec in decomp.subtopics:
        nodes_by_day.setdefault(spec.day, []).append(spec)

    type_icon = {
        "conceptual": "💡",
        "algorithmic": "🧮",
        "hands_on_code": "📝",
        "reference": "📖",
    }

    for day in sorted(nodes_by_day.keys()):
        lines.append(f"**Day {day}:**")
        for spec in nodes_by_day[day]:
            prereq_str = f" *(prereq: {', '.join(spec.prerequisite_titles)})*" if spec.prerequisite_titles else " *(ready to start)*"
            icon = type_icon.get(spec.content_type, "📝")
            lines.append(f"  - {icon} `{spec.title}` ({spec.estimated_hours}h){prereq_str}")
        lines.append("")

    lines.append("Open your **Obsidian Graph View** to visualize the dependencies!")
    lines.append("Whenever you are ready, just say: *\"Plan my day\"* and I will pull the unlocked topics from Obsidian into your daily plan.")

    message = "\n".join(lines)

    # Include topic_graphs in memory_delta so profile_facts and context_builder pick it up immediately
    created_graph = state.get("created_topic_graph")
    graph_dict = created_graph.model_dump(mode="json") if created_graph else None

    memory_delta: dict[str, Any] = {
        "activity_log": [f"Created goal decomposition for '{graph_title}' in Obsidian vault ({len(written)} topics)"]
    }
    if graph_dict:
        # Merge with existing graphs if present
        existing_graphs = (state["task"].memory_slice.relevant_profile or {}).get("topic_graphs") or []
        updated_graphs = [g if isinstance(g, dict) else g.model_dump(mode="json") for g in existing_graphs]
        updated_graphs.append(graph_dict)
        memory_delta["topic_graphs"] = updated_graphs

    return {
        "feedback_message": message,
        "memory_delta": memory_delta,
    }


def pack_result(state: GoalDecomposerState) -> dict:
    """Package final state into AgentResult."""
    if state.get("action_type") == "create" and state.get("target_days") is None:
        return {
            "result": AgentResult(
                task_id=state["task"].task_id,
                agent_name="goal_decomposer",
                task_type=state["task"].task_type or "decompose_goal",
                status=ResultStatus.NEEDS_CLARIFICATION,
                clarification_needed=state.get("feedback_message"),
                output=state.get("feedback_message"),
            )
        }

    success = bool(state.get("written_files")) or state.get("action_type") in ("list", "edit", "delete")
    return {
        "result": AgentResult(
            task_id=state["task"].task_id,
            agent_name=state["task"].agent_name,
            task_type=state["task"].task_type or "decompose_goal",
            status=ResultStatus.SUCCESS if success else ResultStatus.FAILED,
            output=state.get("feedback_message", ""),
            memory_delta=state.get("memory_delta", {}),
        )
    }


def route_by_action(state: GoalDecomposerState) -> str:
    action = state.get("action_type", "create")
    if action == "list":
        return "list_vault"
    if action == "delete":
        return "delete_vault"
    if action == "edit":
        return "edit_vault"
    if state.get("target_days") is None:
        return "clarify_timeframe"
    return "memory_reader"


# ---------------------------------------------------------------------------
# Graph Assembly
# ---------------------------------------------------------------------------

builder = StateGraph(GoalDecomposerState)

builder.add_node("input_parser", input_parser)
builder.add_node("clarify_timeframe", clarify_timeframe_node)
builder.add_node("list_vault", list_vault_node)
builder.add_node("delete_vault", delete_vault_node)
builder.add_node("edit_vault", edit_vault_node)
builder.add_node("memory_reader", memory_reader)
builder.add_node("web_researcher", web_researcher)
builder.add_node("llm_architect", llm_architect)
builder.add_node("deep_node_expander", deep_node_expander)
builder.add_node("response_formatter", response_formatter)
builder.add_node("pack_result", pack_result)

builder.add_edge(START, "input_parser")
builder.add_conditional_edges(
    "input_parser",
    route_by_action,
    {
        "clarify_timeframe": "clarify_timeframe",
        "list_vault": "list_vault",
        "delete_vault": "delete_vault",
        "edit_vault": "edit_vault",
        "memory_reader": "memory_reader",
    },
)
builder.add_edge("clarify_timeframe", "pack_result")
builder.add_edge("list_vault", "response_formatter")
builder.add_edge("delete_vault", "response_formatter")
builder.add_edge("edit_vault", "response_formatter")
builder.add_edge("memory_reader", "web_researcher")
builder.add_edge("web_researcher", "llm_architect")
builder.add_edge("llm_architect", "deep_node_expander")
builder.add_edge("deep_node_expander", "response_formatter")
builder.add_edge("response_formatter", "pack_result")
builder.add_edge("pack_result", END)

app = builder.compile()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def run_goal_decomposer(task: AgentTask) -> AgentResult:
    """Run Goal Decomposer subgraph against an incoming AgentTask."""
    initial_state = build_initial_state(task)
    final_state = app.invoke(initial_state)
    return final_state["result"]