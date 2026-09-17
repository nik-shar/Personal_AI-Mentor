"""
orchestrator/nodes/reasoner.py

The mentor's reasoning core. Reads a compact state summary and decides
whether to route to a sub-agent, ask a clarifying question, or proactively
nudge Nikhil before any routing happens.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from orchestrator.llm import get_reasoning_llm
from orchestrator.tracing import component_span, traced


class ReasoningDecision(BaseModel):
    action: Literal["route", "ask_clarifying_question", "proactive_nudge", "direct_response"] = Field(
        ...,
        description="The high-level strategy for this turn.",
    )
    coaching_mode: str | None = Field(
        default=None,
        description="Selected coaching mode for this turn: 'encourager', 'accountability', 'strategic_advisor', or 'calm_presence'.",
    )
    disclosure_type: str | None = Field(
        default=None,
        description=(
            "Emotional disclosure class for Nik's message (STEP 0): 'venting', "
            "'seeking_guidance', 'burnout', 'context_sharing', 'crisis', or null "
            "for ordinary task talk."
        ),
    )
    reasoning: str = Field(
        ...,
        description="Short explanation of why this action was chosen.",
    )
    agent_name: str | None = Field(
        default=None,
        description="Target agent if action is 'route'.",
    )
    agent_pipeline: list[str] = Field(
        default_factory=list,
        description="Ordered list of agent names to execute sequentially in a multi-agent pipeline (e.g. ['goal_decomposer', 'job_hunter']). If non-empty, agent_name should match the first item in the list.",
    )
    task_type: str | None = Field(
        default=None,
        description=(
            "Specific task type to send to the agent if action is 'route'. MUST be one of the "
            "VALID task_type VALUES listed in the routing instructions (e.g. job_hunter: "
            "'search_jobs' for find/search/show-jobs requests, 'tailor_resume', "
            "'update_application_status'). Never invent a task type — use null when unsure."
        ),
    )
    notes_for_agent: str | None = Field(
        default=None,
        description=(
            "PRIMARY context channel for the sub-agent. The ONLY rich-context "
            "block the sub-agent receives — no DNA memories, no procedural rules, "
            "no profile facts are sent separately. Synthesize a clean, conflict-free "
            "picture here: relevant user facts, what the user wants, what to prioritize, "
            "and what to ignore. If the user overrides their defaults (e.g. says "
            "'search Bengaluru jobs' when defaults are Japan/target_roles), drop the "
            "defaults from your notes — only include what's relevant to THIS task."
        ),
    )
    target_questions_now: list[str] = Field(
        default_factory=list,
        description="Max 1-2 highest-value questions to ask the user immediately if score == 1 (e.g. gut-check on active blocker). Hard capped at 2 questions.",
    )
    queued_questions: list[str] = Field(
        default_factory=list,
        description="Non-urgent questions noticed during reasoning to save in open_questions memory tier for opportunistic later use.",
    )
    clarifying_question: str | None = Field(
        default=None,
        description="Concise 1-2 question message to present if action is 'ask_clarifying_question'.",
    )
    proactive_message: str | None = Field(
        default=None,
        description="Mentor message to share if action is 'proactive_nudge'.",
    )
    needs_long_term_context: bool = Field(
        default=False,
        description=(
            "Set to True ONLY when the user's message requires historical context "
            "beyond the last 7 days — e.g. 'how have I been doing overall?', "
            "'am I making progress?', 'remind me what I learned last month', "
            "'I feel stuck'. Set False for routine tasks like planning today, "
            "logging learning, or writing a LinkedIn post."
        ),
    )
    next_wake_up_minutes: int | None = Field(
        default=None,
        description="Minutes from now when the mentor should autonomously wake up next to check in or audit progress (e.g. 180 for 3 hours from now, 720 for tomorrow morning). Set null if no autonomous wake-up is needed.",
    )
    next_wake_up_reason: str | None = Field(
        default=None,
        description="Brief explanation of why the mentor scheduled the next wake-up run (e.g. 'Post-study block progress audit & evening check-in').",
    )
    proposed_actions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Proposed memory, schedule, or vault edits requiring user consent before execution (e.g. [{'action_type': 'update_schedule', 'title': 'Move study block to 4pm'}]).",
    )
    personality_updates: dict[str, Any] | None = Field(
        default=None,
        description="Updates to the mentor's personality configuration if the user EXPLICITLY requests a change in tone or style (e.g. {'tone': 'direct'}, {'accountability_level': 8}, {'humor': True}). DO NOT set this unless the user makes an explicit request to change how you talk to them.",
    )
    toolkit_mode: str | None = Field(
        default=None,
        description=(
            "Active instruction-set toolkit for this turn. Toolkits are specialized "
            "role overlays for learning/teaching interactions. Currently only "
            "'learning-companion'. Set ONLY when Nik is learning, stuck, practicing, "
            "debugging, reading unfamiliar material, designing, or asks to be taught. "
            "Null for normal mentor talk, task requests, venting, or agent routing."
        ),
    )
    workflow: str | None = Field(
        default=None,
        description=(
            "The specific workflow within toolkit_mode to follow: 'hint', 'debug', "
            "'explain', 'test', 'read', 'review', 'explore', 'api', 'arch', 'autopsy', "
            "'retrieve', or 'learn'; for calendar-manager: 'schedule', "
            "'availability', or 'anchors'. Only meaningful when toolkit_mode is set."
        ),
    )
    reasoning_depth: str = Field(
        default="standard",
        description=(
            "How deeply to reason this turn: 'quick' (greeting/casual — no tool loop), "
            "'standard' (default mentor chat with the tool loop), or 'deep' (complex "
            "diagnosis / design / code questions: private reasoning pass over tools, "
            "verify, then answer)."
        ),
    )
    writing_split: str | None = Field(
        default=None,
        description=(
            "The teach-while-building lane allocation for this turn: 'mentor_writes', "
            "'joint', or 'learner_writes'. Set ONLY for code-building turns inside the "
            "code-explorer / scaffold workflow. Null for non-code turns."
        ),
    )


_REASONING_SYSTEM_PROMPT = """You are the cognitive core of Nikhil's personal AI mentor-companion.

You will receive a CONTEXT DOCUMENT containing Nik's compressed organic memories (narrative sentence snippets capturing who he is, his dynamic habits, energy patterns, learning preferences, and recent observations), today's schedule, momentum metrics, baseline identity, and personality configuration. Treat this document as your *living memory of Nik*.

The document also carries two user-authored standing sections:
- 🧭 CORE IDENTITY — anchor facts about Nik, written by him. Always ground truth.
- 📜 OPERATING CONSTITUTION — your standing orders (core principles + guardrails).
  They override every default behavior below. Never violate them — in particular:
  never fabricate plans, facts, or urgency; never silently upgrade a low Context
  Sufficiency Score to avoid asking a question; never let a failure pass silently.

=============================================================================
COMPRESSED SENTENCE MEMORIES & CONVERSATIONAL FIRST PRINCIPLE
=============================================================================
- Nik's profile is NOT a set of rigid static variables. His habits, preferences, and focus areas evolve dynamically and are stored as compressed natural sentences (e.g. "Learns algorithmic concepts faster by writing visual code in VS Code", "Experiences peak focus late at night around 11 PM").
- Use these compressed memories as your organic background awareness — just like a human mentor who remembers past conversations.
- NEVER act like a robotic schedule solver or CLI ticket dispatcher. If Nik is casually chatting, thinking out loud, sharing thoughts, or asking open-ended questions, DO NOT route to scheduling or goal decomposition agents. Respond directly as a natural, empathetic, insightful mentor (`action = "direct_response"`).

=============================================================================
STEP 0: EMOTIONAL CHECKPOINT (before ANY assessment or routing — never skip)
=============================================================================

First classify Nik's message and set disclosure_type:

  - venting          — "today was garbage, nothing went right" → mirror + validate. Do NOT problem-solve.
  - seeking_guidance — "should I focus on DSA or system design?" → professional mode: analyze, suggest.
  - burnout          — "I'm exhausted and nothing excites me" → acknowledge, then REDUCE intensity: lighter plans, recovery emphasis.
  - context_sharing  — "I'm between jobs right now" → a fact about his situation; remember it and adapt.
  - crisis           — hopelessness, "no point to this", self-harm signals → see the boundary below.
  - null             — ordinary talk or task request.

If disclosure_type is venting, burnout, or crisis: your FIRST response must
acknowledge the emotion like a human would — validate, reflect, at most one
gentle follow-up question. Do NOT route to an agent. Do NOT offer a plan or
schedule. Only transition to task/agent execution AFTER he explicitly signals
readiness ("okay, let's plan", "what should I do", or an explicit request).

PROFESSIONAL BOUNDARY (absolute):
You are a learning/productivity mentor who is emotionally aware — never a
therapist. Acknowledge the emotion, adapt the plan, but NEVER diagnose
("sounds like depression"), never treat, never play therapist.
For crisis: action MUST be "direct_response". Acknowledge seriously and
warmly, offer no plans, and gently encourage him to reach out to someone he
trusts or a mental-health professional. A code-level guardrail enforces this
routing — your job is honest classification and a humane message.

=============================================================================
COGNITIVE LOOP — THINK THROUGH THESE STEPS IN ORDER
=============================================================================

STEP 1: ASSESS (What stands out?)
- Read the organic sentence memories. What do you remember about Nik's current focus, energy, and preferences?
- Read the schedule state. Any missed blocks? Overdue events? Upcoming deadlines?
- Read recent conversations. What was the user's last interaction about?
- Consider the time context. Is this morning planning time? Evening review? Mid-day check?

STEP 2: DECIDE (What's the single most important thing?)
- Decide if Nik is asking for an explicit action (e.g. "plan today", "create roadmap") vs engaging in organic conversation or reflection.
- If context is sparse (cold start), lean into discovery: greet warmly, acknowledge what little you know, and ask 1 organic, friendly question to build rapport.
- If Nik EXPLICITLY asks you to change how you talk to him (e.g., "be more direct", "stop being so strict", "use humor"), set `personality_updates` accordingly. DO NOT set it for normal conversation context, ONLY for explicit commands directed at your personality.

STEP 3: COACHING MODE (How should you say it?)
- Choose ONE mode based on the situation and personality config:
  - "encourager": User on a streak, just completed something, or needs a boost
  - "accountability": User drifting, missed blocks, avoiding important work
  - "strategic_advisor": User is active but could optimize allocation/approach
  - "calm_presence": Late night, user stressed, just completed a long day
- The accountability_level in personality config controls intensity:
  - Level 1-3: Warm, gentle, friendly. Avoid unsolicited schedule pushes.
  - Level 4-6: Balanced mentor. Note gaps supportively when relevant.
  - Level 7-10: Direct accountability. Call out drift directly when asked to review progress.

STEP 3.5: TOOLKIT SELECTION (learning/teaching interactions only)
- A toolkit is a specialized role overlay for teaching. It changes HOW you help
  for this turn — never WHAT is true, never routing. The base mentor identity,
  constitution, and guardrails always stay on top.
- Detect when Nik is LEARNING: trying to understand a concept, stuck on a
  problem, practicing, debugging, reading unfamiliar code/material, designing,
  reviewing, or explicitly asks to be taught. For those turns:
    → set `toolkit_mode = "learning-companion"`
    → set `workflow` to the matching one:
        "hint"      — stuck on a concrete problem; wants guided help
        "debug"     — code/issue not working; diagnose via hypotheses
        "explain"   — wants a concept explained clearly
        "test"      — designing tests before implementing
        "read"      — wants to understand unfamiliar code or a paper
        "review"    — wants educational code/DSA review
        "explore"   — weighing approaches, designs, or decisions
        "api"       — learning a new library/framework/API
        "arch"      — designing a feature/system
        "autopsy"   — reflecting on a fixed bug to learn from it
        "retrieve"  — wants spaced review / practice of past material
        "learn"     — starting a new learning session, mode unclear
    → action stays "direct_response". Do NOT route a learning turn to an agent.
- Detect scheduling/time turns: calendars, availability ("when am I free?"),
  booking/placing blocks, anchors ("I sleep midnight to 8", dinner time), or
  "plan my day". For those turns:
    → set `toolkit_mode = "calendar-manager"`
    → set `workflow` to the matching one:
        "schedule"      — placing work on the grid (read grid → find slots →
                          justify → consent → place → confirm)
        "availability"  — answering "when do I have time" from the grid
        "anchors"       — learning/setting sleep, meal, commute, gym anchors
    → action stays "direct_response". The mentor owns the calendar with
      code-validated tools (get_day_grid / find_available_slots /
      place_time_block / set_anchor) — never a sub-agent, never guessed time.
- NEVER set a toolkit on: venting/burnout/crisis (STEP 0 wins), casual chat,
  or explicit task requests (logging, job apps, LinkedIn, roadmaps).
- If Nik says "ship this", "just give me the code", "stop teaching me", or
  otherwise asks for a direct answer — leave toolkit_mode null (a code-level
  guardrail also enforces this). The teacher knows when the lesson is over.

STEP 3.6: REASONING DEPTH + CODE GROUNDING
- Set `reasoning_depth` for every turn:
  → "quick" for pure greetings/casual chat (no tools needed, single pass).
  → "standard" for normal mentor conversation.
  → "deep" when the turn needs investigation: code diagnosis/errors, design
    analysis on real code, unfamiliar-code understanding, or high-stakes
    decisions where guessing is unacceptable. Deep means the mentor reads
    actual files (read_file/grep_search/git) BEFORE answering — never reasons
    about code it hasn't looked at.
- For code turns, the mentor is CODE-GROUNDED: use read_file / grep_search /
  list_directory / git_log / git_diff on the actual codebase the user names,
  and only then answer. Never fabricate what code does — read it.
- `writing_split` (teach-while-building lanes): ONLY inside building/debugging
  turns, choose who writes the code:
    "mentor_writes"   — learner at the edge of their ability, or plumbing/low
                        learning value; ALWAYS follow with a transfer task.
    "joint"           — mentor skeleton, learner fills bodies (scaffold).
    "learner_writes"  — the core conceptual chunk: learner writes, mentor
                        reviews and finalizes integration only.
  For non-code turns leave it null. Ship mode requests ("just fix it") let the
  mentor propose the full change (propose_edit), never silently apply it.

STEP 4: ACT (What should you do?)
- GREETING or CASUAL CHAT ("hi", "hey", "hello", "hi there", "what's up", "good morning")
  → action = "direct_response".
  DO NOT route to any agent. DO NOT give unsolicited advice, career tips, or plans.
  Respond like a real mentor: greet him back warmly, ask what's on his mind.
- DISCOVERY / OPEN CONVERSATION (Sharing thoughts, asking advice, exploring ideas)
  → action = "direct_response". Generate a natural, conversational response using your memory of Nik.
- STATE CHANGE REPORTS — status/progress facts about tracked domains (job applications, plans,
  streaks): "TestCorp moved me to interviewing", "got an offer from X", "rejected by Meta".
  These are NOT open conversation — they are logging requests. action = "route" to the owning
  agent (`job_hunter` for applications). Continuity is non-negotiable (guidelines §5): a state
  change must never evaporate into a chat-only reply. The agent confirms; you stay warm in the
  delivery — but the state gets recorded.
- ROUTE TO AGENT ONLY IF user made an EXPLICIT ACTIONABLE request for tool execution:
  * "plan my day" / "schedule today" → action = "direct_response". Plan making is now a skill
    the orchestrator handles directly (no sub-agent). Put available time, energy level, and
    priorities into notes_for_agent.
  * "create a roadmap for X" → `goal_decomposer` (`decompose_goal`)
  * "write a tutorial on X" / "deep note on X" / "explain X in a note" →
    `goal_decomposer` (`write_tutorial`). Put the weakness/subtopics to focus
    into notes_for_agent — the tutorial-writer skill is built on that emphasis.
  * "log my study session" / "i studied X" / "i finished X" → action = "direct_response".
    Learning logging is also an orchestrator skill (no sub-agent). Put what was studied into
    notes_for_agent.
  * "write a LinkedIn post" → `linkedin_writer`
  * "tailor my resume" / "I applied to X" / "X moved me to screening" / "interview at X" / "rejected by X" / "offer from X" / "job hunt status" → `job_hunter`
  * "should I apply to X" / "how relevant is the X role for me" / "is X a good fit" → `job_hunter` (assess_fit)
  * "find me jobs" / "search for X roles" / "show me openings" / "latest job listings" / "any new roles for me" → `job_hunter` (search_jobs)
    The job_hunter has LIVE internet job search (Google Jobs + company boards) scored against Nik's resume.
    A request to find/search/show jobs or openings is ALWAYS actionable — route it. NEVER answer with a
    clarifying question about which roles/companies: his target roles and locations are already in context.
    NOTE: a message reporting a job-application status change IS an actionable logging request — route it to `job_hunter`. Never let a state change evaporate into a chat-only reply (guidelines §5 continuity).

VALID task_type VALUES (pick ONLY from this list — never invent task types; when unsure, leave task_type null):
  - linkedin_writer:  write_linkedin_post
  - goal_decomposer:  decompose_goal | write_tutorial
      → "write a tutorial on X" / "deep note on X" / "explain X to me as a note":
        use write_tutorial. The mentor's guidance (weaknesses, subtopics to
        focus) goes into notes_for_agent — the goal_decomposer's tutorial-writer
        skill is built around that emphasis.
  - job_hunter:       tailor_resume | log_application | update_application_status | job_search_review | assess_fit | search_jobs

STEP 5: SCHEDULE (When to think next?)
- Set next_wake_up_minutes based on situation (e.g. morning planning -> 180-240 min, evening -> 600-720 min, casual turn -> null).

=============================================================================
ROUTING (when action = "route")
=============================================================================

CONTEXT SUFFICIENCY RUBRIC (1 - 3)
Score 1 (INSUFFICIENT CONTEXT — ASK ORGANICALLY):
- Trigger: User asks for open direction ("What should I do next?") AND context lacks active goals.
- Action: "ask_clarifying_question", ask max 1-2 natural questions.

Score 3 (SUFFICIENT CONTEXT — ROUTE & ACT):
- Route ONLY when explicit actionable intent is present.

=== NOTES FOR AGENT — THE PRIMARY CONTEXT CHANNEL ===

When you route to a sub-agent, your `notes_for_agent` field is the ONLY
rich-context block the agent receives. The agent does NOT get raw DNA
memories, profile facts, procedural rules, or mindset calibration — your
notes ARE their context. Therefore:

1. BE COMPREHENSIVE: Include everything the agent needs to do a good job.
   Relevant facts: degree, skills, experience level, project history, etc.
   Strategic guidance: what to prioritize, tone to use, pitfalls to avoid.

2. BE CONFLICT-FREE: If the user overrides their defaults (e.g. says
   "search software engineer roles in Bengaluru" when defaults are
   "AI Engineer" / "Japan"), drop the defaults from your notes. The
   agent should only see what's relevant to THIS task.

3. BE SPECIFIC: For job searches — include exact role, location, and
   relevant experience/skills. For daily plans — include energy level,
   available time, and task priorities. For LinkedIn posts — include
   topic, tone, and target audience.

4. DO NOT include conflicting signals. One coherent picture.

Example for "search for software engineer roles in Bengaluru":
  "Nik wants software engineer roles specifically in Bengaluru (not his
  usual AI Engineer/Japan targets). He has 3mo intern + 10mo Data
  Scientist experience, IIT Roorkee B.Tech, skills: Python, PyTorch,
  LangGraph, SQL. He's actively job-hunting and needs Bengaluru-based
  roles matching his experience level."

=============================================================================
PERSONALITY CONFIGURATION
=============================================================================

Be concise, warm, calibrated, and mentor-grade."""


@traced("reasoner_llm", "llm")
@component_span("reasoner", tags=["component:reasoner"])
def run_reasoner(summary_text: str) -> ReasoningDecision:
    llm = get_reasoning_llm(temperature=0.2)
    # method="function_calling": OpenAI's strict json_schema mode rejects
    # list[dict] and dict fields with a 400. function_calling mode tolerates
    # free-form nested dicts (proposed_actions, personality_updates).
    structured_llm = llm.with_structured_output(ReasoningDecision, method="function_calling")
    raw = structured_llm.invoke(
        [
            {"role": "system", "content": _REASONING_SYSTEM_PROMPT},
            {"role": "user", "content": summary_text},
        ]
    )
    if isinstance(raw, ReasoningDecision):
        return raw
    if isinstance(raw, dict):
        return ReasoningDecision(**raw)
    raise TypeError(f"Unexpected reasoner output type: {type(raw)}")


def decision_to_dict(decision: ReasoningDecision) -> dict[str, Any]:
    return decision.model_dump()


# ---------------------------------------------------------------------------
# Continuity guardrail (mentor_agent_guidelines.md §5)
# ---------------------------------------------------------------------------

# Strong stage-transition signals. Deliberately conservative — a false positive
# here routes a chat message to the logger, so vague words stay out.
_JOB_STAGE_SIGNALS = (
    "moved me", "moving me", "screening", "interview", "offer", "rejected",
    "rejection", "ghosted", "withdrew", "withdrawn", "online assessment",
    "recruiter call", "phone screen", "next round", "assessment",
)


def apply_job_status_guardrail(
    decision: ReasoningDecision,
    user_input: str,
    tracked_companies: list[str],
) -> ReasoningDecision:
    """
    A status-change report about a TRACKED job application must route to
    job_hunter — the router LLM keeps treating "X moved me to interviewing"
    as open conversation, losing the state change. Enforced deterministically:
    requires BOTH a stage signal AND a tracked company name in the message.
    """
    if decision.action == "route":
        return decision
    lowered = (user_input or "").lower()
    if not lowered or not any(sig in lowered for sig in _JOB_STAGE_SIGNALS):
        return decision
    for company in tracked_companies:
        if company and company.strip().lower() in lowered:
            return decision.model_copy(update={
                "action": "route",
                "agent_name": "job_hunter",
                "task_type": "update_application_status",
                "agent_pipeline": ["job_hunter"],
                "reasoning": (decision.reasoning or "")
                + " [continuity guardrail: tracked application status change → job_hunter]",
            })
    return decision


# ---------------------------------------------------------------------------
# Toolkit guardrail (code-enforced, same philosophy as the crisis guardrail)
# ---------------------------------------------------------------------------

# Phrases that mean "stop teaching, just ship it". When present, any toolkit
# selection is suppressed — the teacher steps aside for direct engineering help.
TOOLKIT_SHIP_SIGNALS = (
    "ship this", "ship it", "just give me the code", "give me the answer",
    "show me the solution", "show me the code", "just tell me", "do it for me",
    "implement it", "implement it for me", "stop teaching", "stop quizzing",
    "no more hints", "give me the code", "just do it", "direct answer",
    "enough with the questions", "stop asking questions",
)

# Explicit "put the teacher hat on" backstop. Code-enforced so a teaching
# intent (e.g. "teach me X") is never dropped by the routing LLM.
TOOLKIT_TEACHER_SWITCH_SIGNALS = (
    "act as my teacher", "be my teacher", "switch to teacher", "teacher mode",
    "tutor me", "teach me", "tutorial mode", "help me learn", "learning mode",
    "teach me like", "explain it like", "guide me through",
)

# Explicit code-grounding backstop: turns that reference Nik's ACTUAL code
# must run with the code-explorer overlay + deep reasoning so the mentor reads
# files before reasoning about them.
TOOLKIT_CODE_SWITCH_SIGNALS = (
    "debug this", "debug my", "debugging my", "look at my code", "look at this file",
    "look at this code", "understand this code", "why is my code", "why does my code",
    "read this file", "read my code", "error in my", "traceback", "trace this",
    "stepping through", "review my code", "review this code", "explain this code",
    "in my repo", "in my project", "my codebase", "this function", "this class",
    "this module", "build this feature", "implement this feature",
    "my code", "this bug", "this error", "that error", "the traceback",
    "failing test", "test fails", "test failing", "it's broken", "it is broken",
    "what's wrong with my", "what is wrong with my", "doesn't work", "not working",
    "is failing", "keeps failing", "erroring", "crashing", "stack trace",
    "segfault", "exception in", "broken in", "failed at line",
)

# Explicit calendar-manager backstop: turns about calendars, availability,
# time-blocks, or anchors run under the calendar-manager overlay so the mentor
# uses the day-grid tools (get_day_grid / find_available_slots /
# place_time_block / set_anchor) instead of guessing free time — and instead
# of misrouting to a sub-agent.
TOOLKIT_CALENDAR_SWITCH_SIGNALS = (
    "my calendar", "the calendar", "on my calendar", "calendar",
    "schedule today", "schedule my", "to schedule", "want to schedule",
    "schedule it", "reschedule", "scheduled",
    "book a block", "book a slot", "place a block", "block my time",
    "time block", "time-block", "blocking", "protected time",
    "find a slot", "find slots", "find time for", "fit in",
    "available time", "when am i free", "when am i available",
    "when are you free", "free window", "free time", "i have free time",
    "plan my day", "plan today", "plan the day", "planning my day",
    "add to my schedule", "put it in the calendar", "put it on the calendar",
    "set an anchor", "as an anchor", "my anchor", "the anchor",
    "my sleep", "sleep schedule", "i sleep", "sleep midnight", "sleep at",
    "sleep from", "usual sleep", "dinner time", "dinner at", "dinner is",
    "meal time", "lunch at", "lunch is", "breakfast at",
    "revision hours", "study block", "study hours", "blocked tonight",
    "when can i do", "is that time", "does that time work",
)


def apply_toolkit_guardrail(
    decision: ReasoningDecision,
    user_input: str,
) -> ReasoningDecision:
    """
    Code-enforced rules for the instruction-set toolkit layer:

    1. Ship-mode override — if the user asks for a direct answer/solution,
       any toolkit selection is removed (teacher steps aside for shipping).
    2. Emotional invalidation — toolkits never ride on venting/burnout/crisis
       turns; STEP 0 owns those.
    3. Direct-response-only — a toolkit only makes sense as a spoken overlay;
       if the turn routes to an agent, toolkit fields are cleared.
    4. Explicit-teacher backstop — "teach me X" / "act as my teacher" forces
       toolkit_mode=learning-companion (default workflow 'learn') so the
       reasoner can't silently drop the teaching intent.
    5. Code-grounding backstop — turns about Nik's actual code force the
       code-explorer overlay + deep reasoning (read before you reason).
    6. Calendar-manager backstop — scheduling/availability/calendar/anchor
       turns force toolkit_mode=calendar-manager (default workflow
       'schedule') so the mentor uses the day-grid tools instead of guessing
       free time or misrouting to a sub-agent.
    """
    toolkit_mode = getattr(decision, "toolkit_mode", None)
    workflow = getattr(decision, "workflow", None)
    lowered = (user_input or "").lower()
    is_code_turn = any(sig in lowered for sig in TOOLKIT_CODE_SWITCH_SIGNALS)

    # 1. Ship-mode override — explicit user demand for a direct answer.
    if toolkit_mode and any(sig in lowered for sig in TOOLKIT_SHIP_SIGNALS):
        return decision.model_copy(update={
            "toolkit_mode": None,
            "workflow": None,
            "reasoning": (getattr(decision, "reasoning", "") or "")
            + " [toolkit guardrail: user asked to ship — teacher mode stepped aside]",
        })

    # 2. Emotional turns — toolkits never apply to venting/burnout/crisis.
    if toolkit_mode and getattr(decision, "disclosure_type", None) in (
        "venting", "burnout", "crisis",
    ):
        return decision.model_copy(update={"toolkit_mode": None, "workflow": None})

    # 3. Direct-response-only — agent routing never carries a toolkit.
    if toolkit_mode and getattr(decision, "action", None) != "direct_response":
        return decision.model_copy(update={"toolkit_mode": None, "workflow": None})

    # 5. Code-grounding backstop — a code turn with an active (non-routing)
    #     toolkit still needs deep reasoning so code tools are bound in the
    #     direct-response node.
    if toolkit_mode and is_code_turn:
        if getattr(decision, "reasoning_depth", None) != "deep":
            return decision.model_copy(update={
                "reasoning_depth": "deep",
                "reasoning": (getattr(decision, "reasoning", "") or "")
                + " [toolkit guardrail: code turn → deep reasoning to bind code tools]",
            })

    # 4. Explicit-teacher backstop — user verbatim asks for teaching.
    #    Only engages when the router isn't already sending an agent route:
    #    deliberate task routing (jobs, roadmaps, LinkedIn) outranks the
    #    teaching backstop.
    if (
        not toolkit_mode
        and getattr(decision, "action", None) != "route"
        and any(sig in lowered for sig in TOOLKIT_TEACHER_SWITCH_SIGNALS)
    ):
        return decision.model_copy(update={
            "action": "direct_response",
            "agent_name": None,
            "agent_pipeline": [],
            "toolkit_mode": "learning-companion",
            "workflow": workflow or "learn",
            "reasoning": (getattr(decision, "reasoning", "") or "")
            + " [toolkit guardrail: explicit teaching request → learning-companion]",
        })

    # Code-grounding backstop — a code turn with no toolkit at all forces the
    # code-explorer overlay (read before you reason), unless it's agent-routed.
    if (
        not toolkit_mode
        and getattr(decision, "action", None) != "route"
        and is_code_turn
    ):
        return decision.model_copy(update={
            "action": "direct_response",
            "agent_name": None,
            "agent_pipeline": [],
            "toolkit_mode": "code-explorer",
            "workflow": workflow or "read-repo",
            "reasoning_depth": "deep",
            "reasoning": (getattr(decision, "reasoning", "") or "")
            + " [toolkit guardrail: code question → code-explorer + deep reasoning]",
        })

    # Calendar-manager backstop — turns about calendars, availability,
    # time-blocks, or anchors run under the calendar-manager overlay so the
    # mentor uses the day-grid tools instead of guessing free time (and never
    # misroutes a scheduling turn to a sub-agent). Runs after the code/teacher
    # backstops so a teaching/code turn that merely mentions a time still keeps
    # its stronger overlay. Ship-mode, crisis, and agent-routed turns always
    # win over this backstop.
    is_calendar_turn = any(sig in lowered for sig in TOOLKIT_CALENDAR_SWITCH_SIGNALS)
    if (
        not toolkit_mode
        and getattr(decision, "action", None) != "route"
        and getattr(decision, "disclosure_type", None) not in ("venting", "burnout", "crisis")
        and is_calendar_turn
    ):
        return decision.model_copy(update={
            "action": "direct_response",
            "agent_name": None,
            "agent_pipeline": [],
            "toolkit_mode": "calendar-manager",
            "workflow": workflow or "schedule",
            "reasoning": (getattr(decision, "reasoning", "") or "")
            + " [toolkit guardrail: scheduling/calendar turn → calendar-manager overlay]",
        })

    return decision
