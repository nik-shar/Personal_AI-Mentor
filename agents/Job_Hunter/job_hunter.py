"""
agents/Job_Hunter/job_hunter.py

Specialist LangGraph agent for Nikhil's job hunt. Four branches, one graph
(the stateless sub-agent pattern):

    START → input_parser → route_by_action
      ├─ "tailor_resume"   → jd_analyzer → resume_tailor → quality_critic
      │                        ↺ (critic → tailor, max 1 revision)
      │                      → render_and_write → pack_result
      ├─ "log_application" → application_logger → pack_result
      ├─ "update_status"   → application_logger → pack_result
      └─ "review"          → pipeline_analyzer → pack_result

Design split (same rule as the other agents):
    Code owns:  metric integrity, keyword coverage, stage coercion, dedup,
                stale math, vault IO, LaTeX template, PDF compile.
    LLM owns:   JD signal extraction, bullet selection/rephrasing (by ID only).

Hard rules (mentor_agent_guidelines.md §4 job_hunter):
    - Tailoring NEVER logs an application (side effects stay deliberate).
    - Every number in a tailored bullet must exist verbatim in its source
      bullet — enforced in code in quality_critic, never left to the LLM.
    - Everything user-facing is a draft (DraftSuggestion) — nothing is
      auto-sent anywhere.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import yaml
from langgraph.graph import END, START, StateGraph

from agents.Job_Hunter.render import (
    application_dir,
    build_resume_view,
    compile_pdf,
    count_pdf_pages,
    drop_optional_bullets,
    keyword_coverage,
    load_master_resume,
    metric_violations,
    render_markdown,
    render_tex,
    view_plain_text,
)
from agents.Job_Hunter.state import (
    APPLICATION_STAGES,
    JDAnalysis,
    JobHunterState,
    MasterResume,
    ParsedRequest,
    TailoredResumeDraft,
    build_initial_state,
)
from orchestrator.config import OBSIDIAN_CAREER_FOLDER, OBSIDIAN_VAULT_PATH
from orchestrator.tracing import component, component_span, traced
from schemas import AgentResult, AgentTask, DraftSuggestion, ResultStatus

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TAILOR_REVISIONS = 1        # critic → tailor loop cap (LinkedIn-writer pattern)
STALE_APPLICATION_DAYS = 14     # active application silent this long → follow-up candidate
TERMINAL_STAGES = {"rejected", "withdrawn"}
MAX_JD_CHARS = 6000             # prompt-size guard for pasted job descriptions

# task_type (orchestrator-facing) → internal action_type
_TASK_TYPE_TO_ACTION = {
    "tailor_resume": "tailor_resume",
    "log_application": "log_application",
    "update_application_status": "update_status",
    "job_search_review": "review",
    "assess_fit": "assess_fit",
    "search_jobs": "search_jobs",
}

_STAGE_ALIASES = {
    "bookmarked": "wishlist", "to_apply": "wishlist", "to apply": "wishlist", "saved": "wishlist",
    "submitted": "applied", "just applied": "applied",
    "referral": "referral_requested", "referral requested": "referral_requested",
    "screen": "screening", "oa": "screening", "online assessment": "screening",
    "phone screen": "screening", "recruiter call": "screening", "recruiter screen": "screening",
    "interview": "interviewing", "interviews": "interviewing", "onsite": "interviewing",
    "on site": "interviewing", "on-site": "interviewing", "loop": "interviewing",
    "technical": "interviewing", "final round": "interviewing",
    "offered": "offer",
    "rejection": "rejected", "ghosted": "rejected", "passed": "rejected",
    "withdraw": "withdrawn", "withdrew": "withdrawn",
}


# ---------------------------------------------------------------------------
# Small helpers (pure code)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def _extract_user_text(raw: str) -> str:
    """Unwrap the [USER REQUEST] block the orchestrator wraps instructions in."""
    if "[USER REQUEST]" in raw:
        m = re.search(r'The user requested:\s*"?(.*?)"?\s*\n\s*\[', raw, re.DOTALL)
        if m:
            return m.group(1).strip()
        m = re.search(r'The user requested:\s*"?(.*?)"?\s*$', raw, re.DOTALL)
        if m:
            return m.group(1).strip()
    return raw.strip()


def _extract_instruction_block(raw: str, tag: str) -> str:
    """Pull a [TAG] section out of the orchestrator-built instructions."""
    if tag not in raw:
        return ""
    m = re.search(re.escape(tag) + r"\s*(.+?)\s*(?:\[|$)", raw, re.DOTALL)
    return m.group(1).strip() if m else ""


# Single persistent job board (profile_facts career.job_pipeline). Search
# results are merged into it as wishlist-stage rows (scored_listing_to_pipeline_entry)
# so the Job Hunt tab shows one unified table; the user prunes rows from the UI.
JOB_BOARD_MAX = 200


def _lookup_board_job(
    board: dict[str, Any],
    company: Optional[str],
    role_title: Optional[str],
) -> Optional[dict[str, Any]]:
    """
    Find a previously-found job on the persistent job board by company
    (preferred) or role. Lets "Tailor my resume for the X role at Y" resolve
    its JD from an earlier search — the session-bound #N cache is not enough.
    Expected input: a dict keyed by row id built from the job_pipeline list.
    """
    if not isinstance(board, dict) or not board or not (company or role_title):
        return None
    comp = (company or "").strip().lower()
    role = (role_title or "").strip().lower()
    best: Optional[dict[str, Any]] = None
    for entry in board.values():
        if not isinstance(entry, dict):
            continue
        e_comp = (entry.get("company") or "").strip().lower()
        e_role = (entry.get("title") or "").strip().lower()
        if comp and (comp in e_comp or e_comp in comp):
            if not role or role in e_role or e_role in role:
                return entry
            best = best or entry
        elif not comp and role and (role in e_role or e_role in role):
            best = best or entry
    return best


def _coerce_stage(raw: Any) -> Optional[str]:
    """Tolerant stage coercion — mirrors the codebase's status-coercion style."""
    if not raw:
        return None
    s = str(raw).strip().lower().replace("-", "_")
    if s in APPLICATION_STAGES:
        return s
    if s in _STAGE_ALIASES:
        return _STAGE_ALIASES[s]
    return _STAGE_ALIASES.get(s.replace("_", " "))


def _find_application(
    pipeline: list[dict[str, Any]],
    company: Optional[str],
    role_title: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Fuzzy lookup: exact company (+role if given), then substring company match."""
    if not company:
        return None
    c = company.strip().lower()
    for app in pipeline:
        if (app.get("company") or "").strip().lower() != c:
            continue
        if role_title:
            r = (app.get("role_title") or "").strip().lower()
            want = role_title.strip().lower()
            if r and want not in r and r not in want:
                continue
        return app
    for app in pipeline:
        ac = (app.get("company") or "").strip().lower()
        if ac and (ac in c or c in ac):
            return app
    return None


def _days_since(raw: Any, now: datetime) -> Optional[int]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# LLM calls (all fail-open — a failed LLM call degrades, never crashes a turn)
# ---------------------------------------------------------------------------

_PARSE_PROMPT = """You extract job-hunt intent from a user's message to their AI mentor.

Pick exactly ONE action:
- "tailor_resume": wants a resume adapted to a job description ("tailor my resume for X", "modify my resume for this JD", or pastes a job description)
- "log_application": reports a NEW application submitted ("I applied to Stripe", "just applied for the Rippling role")
- "update_status": reports a status change on an EXISTING application ("Stripe moved me to screening", "got rejected by Meta", "interview at Google next week", "got an offer from X")
- "assess_fit": asks how relevant a role is for them or whether they should apply ("how relevant is the IDFC role for me", "should I apply to this", "is this a good fit", "worth applying?")
- "review": asks about the overall job hunt ("how's my job hunt going", "which applications should I follow up on")
- "search_jobs": asks to find open roles or jobs ("find me AI Engineer jobs", "search for remote ML roles", "show me openings")

Extract what you can: company, role_title, jd_text (the verbatim job description if one was pasted — keep it COMPLETE),
stage (one of: wishlist, applied, referral_requested, screening, interviewing, offer, rejected, withdrawn),
applied_date (ISO if mentioned), referral_contact, job_url, location, notes. Leave fields null when not present — never guess."""


_SEARCH_PARAMS_PROMPT = """You are extracting job search parameters from a user's request to their AI mentor.

The user's message is wrapped in [USER REQUEST]. The mentor's analysis and
guidance is in [MENTOR GUIDANCE / STRATEGY] — the mentor already synthesized
what the user wants and resolved any conflicts with profile defaults.

Your job: extract what to search for.

RULES:
1. ROLE QUERIES — Extract 1-2 specific role titles from the user's words.
   Example: "search AI Engineer jobs in Bengaluru" → ["AI Engineer"]
   Example: "find me software engineer roles" → ["Software Engineer"]
   If the user doesn't name a role, use the mentor's guidance or default.

2. LOCATIONS — If the user named a specific city/country, use ONLY that.
   NEVER add profile defaults like "Tokyo" or "Japan" if the user said
   something else (like "Bengaluru"). The mentor's guidance tells you
   what locations to use and which to ignore.

3. EXPERIENCE — Determine from context. Early career (≤4yr), mid (≤7yr),
   senior (≤10yr). The user or mentor may specify this.

4. EXCLUDE KEYWORDS — Be thoughtful about what to exclude:
   - If early career: exclude "senior", "lead", "principal", "staff"
   - If results require a language the user doesn't speak: exclude that language
   - If mentor's guidance mentions avoiding certain terms: include those"""


@component_span("job_hunter:parse", tags=["component:job_hunter:parse"])
def _llm_parse(user_text: str) -> Optional[ParsedRequest]:
    try:
        from orchestrator.llm import get_reasoning_llm

        structured = get_reasoning_llm(temperature=0.1).with_structured_output(ParsedRequest)
        raw = structured.invoke([("system", _PARSE_PROMPT), ("user", user_text)])
        if isinstance(raw, ParsedRequest):
            return raw
        if isinstance(raw, dict):
            return ParsedRequest(**raw)
    except Exception as exc:
        print(f"[job_hunter] request parse failed: {exc}")
    return None


_JD_PROMPT = """You are a brutally honest technical recruiter reading a job description.

Extract:
- job_title / company: best-effort from the text ("" if absent)
- seniority: one of intern | junior | mid | senior | staff
- required_keywords: 8-15 concrete skills/technologies the JD explicitly requires (short tokens: "LangGraph", "RAG", "Kubernetes")
- preferred_keywords: the nice-to-haves
- domain_focus: 2-4 short phrases describing what this role actually centers on
- summary: 1-2 honest sentences on what the employer really wants — no marketing fluff"""


@component_span("job_hunter:jd_analysis", tags=["component:job_hunter:jd_analysis"])
def _llm_analyze_jd(jd_text: str, company: Optional[str], role_title: Optional[str]) -> Optional[JDAnalysis]:
    try:
        from orchestrator.llm import get_reasoning_llm

        structured = get_reasoning_llm(temperature=0.1).with_structured_output(JDAnalysis)
        raw = structured.invoke([
            ("system", _JD_PROMPT),
            ("user", f"JOB DESCRIPTION:\n{jd_text[:MAX_JD_CHARS]}"),
        ])
        analysis = raw if isinstance(raw, JDAnalysis) else JDAnalysis(**raw)
        if company and not analysis.company:
            analysis.company = company
        if role_title and not analysis.job_title:
            analysis.job_title = role_title
        return analysis
    except Exception as exc:
        print(f"[job_hunter] JD analysis failed: {exc}")
        return None


def _master_prompt_block(master: MasterResume) -> str:
    """Compact, ID-labelled rendering of the master resume for the tailor prompt."""
    lines: list[str] = []
    if master.summary:
        lines.append(f"MASTER SUMMARY (may be adapted into summary_line): {master.summary}")
    lines.append("SKILLS (categories + items — you may reorder, never add):")
    for c in master.skills:
        lines.append(f"  {c.label}: {', '.join(c.items)}")
    lines.append("")
    lines.append("EXPERIENCE & PROJECTS (entry ids + bullet ids):")
    for e in master.experience:
        lines.append(f"[entry: {e.id}] {e.role} @ {e.company} ({e.period})")
        for b in e.bullets:
            lines.append(f"  [{b.id}] {b.text}")
    for p in master.projects:
        tech = f" — {', '.join(p.tech)}" if p.tech else ""
        lines.append(f"[entry: {p.id}] Project: {p.name}{tech}")
        for b in p.bullets:
            lines.append(f"  [{b.id}] {b.text}")
    return "\n".join(lines)


_TAILOR_PROMPT = """You are an expert technical resume writer tailoring Nikhil Sharma's resume for ONE specific job.

HONESTY RULES — NEVER VIOLATE (a code check enforces them and rejects your output):
1. Output only bullets derived from the MASTER RESUME below — every bullet MUST carry the source_id of the master bullet it derives from.
2. Every number/metric in your bullet text must appear VERBATIM in the source bullet. Never invent, round, or inflate metrics.
3. Never add employers, titles, dates, skills, certifications, or credentials absent from the master resume.
4. Skill categories and items must come from the master lists — you may only reorder them. YOU MUST USE THE EXACT CATEGORY LABELS (e.g., "Languages", "AI / ML"). DO NOT RENAME THEM.
5. NEVER mention the target company's name in the summary or anywhere else (unless he actually worked there).

TAILORING RULES:
- Select the strongest 4-7 bullets per included experience and 3-5 per project (aim to fill a one-page resume without leaving it looking empty). Drop weak bullets by omission.
- Rephrase bullets to maximize ATS keyword overlap based on the JD ANALYSIS. Mirror the JD's vocabulary naturally without sounding desperate or keyword-stuffed.
- summary_line: one professional, general sentence positioning him for this role, grounded in his real experience.
- changes_summary: 3-6 short bullets explaining what you changed and why (shown to the user).
"""


def _agent_guidelines_block() -> str:
    """mentor_agent_guidelines.md §4 job_hunter — injected, never copied (fail-open)."""
    try:
        from orchestrator.cognition.guidelines import get_agent_guidelines

        text = get_agent_guidelines("job_hunter")
        return f"\nSTANDING ORDERS (never violate):\n{text}\n" if text else ""
    except Exception:
        return ""


@component_span("job_hunter:tailor", tags=["component:job_hunter:tailor"])
def _llm_tailor(
    master: MasterResume,
    jd_text: str,
    jd_analysis: Optional[JDAnalysis],
    critic_feedback: Optional[str],
    revision_count: int,
    mentor_guidance: str = "",
) -> Optional[TailoredResumeDraft]:
    """Layer 2: structured tailoring draft. Returns None on failure (fail-open)."""
    try:
        from orchestrator.llm import get_reasoning_llm

        analysis_block = ""
        if jd_analysis:
            analysis_block = (
                "JD ANALYSIS:\n"
                f"- Role: {jd_analysis.job_title} @ {jd_analysis.company} ({jd_analysis.seniority})\n"
                f"- Required keywords: {', '.join(jd_analysis.required_keywords)}\n"
                f"- Preferred: {', '.join(jd_analysis.preferred_keywords)}\n"
                f"- Domain focus: {', '.join(jd_analysis.domain_focus)}\n"
                f"- Read: {jd_analysis.summary}\n\n"
            )

        guidance_block = f"MENTOR GUIDANCE:\n{mentor_guidance}\n\n" if mentor_guidance else ""

        revision_block = ""
        if critic_feedback:
            revision_block = (
                f"[REVISION {revision_count} — CODE-CHECK VIOLATIONS YOU MUST FIX]\n"
                f"{critic_feedback}\n"
                "Regenerate the full corrected draft. Do not repeat these violations.\n\n"
            )

        human = (
            f"{revision_block}"
            f"{guidance_block}"
            f"JOB DESCRIPTION:\n{jd_text[:MAX_JD_CHARS]}\n\n"
            f"{analysis_block}"
            f"MASTER RESUME:\n{_master_prompt_block(master)}\n\n"
            "Produce the tailored draft now (structured output)."
        )

        structured = get_reasoning_llm(temperature=0.3).with_structured_output(TailoredResumeDraft)
        raw = structured.invoke([
            ("system", _TAILOR_PROMPT + _agent_guidelines_block()),
            ("user", human),
        ])
        if isinstance(raw, TailoredResumeDraft):
            return raw
        if isinstance(raw, dict):
            return TailoredResumeDraft(**raw)
    except Exception as exc:
        print(f"[job_hunter] tailor LLM failed: {exc}")
    return None


_FIT_PROMPT = """You are a brutally honest senior engineer-mentor assessing job-fit for Nikhil Sharma.

You are given his master resume and a job description. Write a 4-6 sentence honest verdict:
- Where he is genuinely strong for this role (cite his real experience/projects).
- Where the real gaps are — never paper over them, never inflate.
- End with a straight recommendation: apply now / apply with a tailored angle / skill up first (name the skill).

Plain text, no headers, no bullet lists — a mentor's read, not a report."""


@component_span("job_hunter:fit_verdict", tags=["component:job_hunter:fit_verdict"])
def _llm_fit_verdict(master: MasterResume, jd_text: str, jd_analysis: Optional[JDAnalysis]) -> Optional[str]:
    """Short honest fit verdict. Fail-open → None (the deterministic report still renders)."""
    try:
        from orchestrator.llm import get_reasoning_llm

        analysis_hint = ""
        if jd_analysis and jd_analysis.summary:
            analysis_hint = f"\nJD read: {jd_analysis.summary}\n"
        response = get_reasoning_llm(temperature=0.3).invoke([
            ("system", _FIT_PROMPT),
            ("user", f"MASTER RESUME:\n{_master_prompt_block(master)}\n{analysis_hint}\nJOB DESCRIPTION:\n{jd_text[:MAX_JD_CHARS]}"),
        ])
        return (response.content or "").strip() or None
    except Exception as exc:
        print(f"[job_hunter] fit verdict failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Graph nodes — input parsing + JD analysis
# ---------------------------------------------------------------------------

def input_parser(state: JobHunterState) -> dict:
    """
    Decide the action + extract entities. Precedence:
    explicit task.params → orchestrator task_type → LLM parse.
    The LLM is trusted to reason over the user's message. No keyword heuristics.
    """
    raw = state["raw_instructions"]
    task = state["task"]
    params = task.params or {}
    user_text = _extract_user_text(raw)

    parsed = _llm_parse(user_text)

    task_action = _TASK_TYPE_TO_ACTION.get(task.task_type or "")
    action = (
        params.get("action")
        or params.get("action_type")
        or task_action
        or (parsed.action if parsed else None)
    )
    # Normalise orchestrator task-type vocabulary to internal actions.
    action = _TASK_TYPE_TO_ACTION.get(action, action)
    if action not in ("tailor_resume", "log_application", "update_status", "review", "assess_fit", "search_jobs"):
        action = "review"  # safe fallback — the reasoner handles unclear intents via notes_for_agent

    company = params.get("company") or (parsed.company if parsed else None)
    role_title = params.get("role") or params.get("role_title") or (parsed.role_title if parsed else None)
    jd_text = params.get("jd_text") or (parsed.jd_text if parsed else None)
    job_url = params.get("job_url") or (parsed.job_url if parsed else None)
    location = params.get("location") or (parsed.location if parsed else None)

    # Resolve #N references from search cache
    import re
    priv_mem = task.memory_slice.private_memory or {}
    match = re.search(r'#(\d+)', user_text)
    if match and not jd_text:
        rank = match.group(1)
        cache = priv_mem.get("job_hunter_search_cache") or {}
        if rank in cache:
            job = cache[rank]
            company = company or job.get("company")
            role_title = role_title or job.get("title")
            jd_text = jd_text or job.get("description_full")
            job_url = job_url or job.get("apply_url")
            location = location or job.get("location")
            if not jd_text and job_url:
                from integrations.job_search import fetch_full_jd
                fetched = fetch_full_jd(job_url)
                if fetched:
                    jd_text = fetched

    # Persistent job-board lookup — "Tailor my resume for the X role at Y"
    # resolves the JD from an earlier search now stored in the unified board
    # (job_pipeline rows carry description_full). Fail-open: no match just
    # means the tailor asks for the JD.
    if not jd_text and (company or role_title):
        pipeline = task.memory_slice.relevant_profile.get("job_pipeline") or []
        if isinstance(pipeline, list):
            board = {
                str(row.get("id")): row
                for row in pipeline
                if isinstance(row, dict) and row.get("id")
            }
        else:
            board = {}
        board_match = _lookup_board_job(board, company, role_title)
        if board_match:
            company = company or board_match.get("company")
            role_title = role_title or board_match.get("title")
            jd_text = board_match.get("description_full") or jd_text
            job_url = job_url or board_match.get("apply_url")
            location = location or board_match.get("location")
            # Pipeline JD text comes from the search snippet; when it looks
            # truncated, refetch the full posting (parity with the #N cache path).
            if board_match.get("description_truncated") and job_url:
                from integrations.job_search import fetch_full_jd
                fetched = fetch_full_jd(job_url)
                if fetched and len(fetched) > len(jd_text or ""):
                    jd_text = fetched

    return {
        "user_text": user_text,
        "action_type": action,
        "company": company,
        "role_title": role_title,
        "jd_text": jd_text,
        "stage": _coerce_stage(params.get("stage") or (parsed.stage if parsed else None)),
        "applied_date": params.get("applied_date") or (parsed.applied_date if parsed else None),
        "referral_contact": params.get("referral_contact") or (parsed.referral_contact if parsed else None),
        "job_url": job_url,
        "location": location,
        "notes": params.get("notes") or (parsed.notes if parsed else None),
    }

def _load_stored_jd(
    pipeline: list[dict[str, Any]],
    company: Optional[str],
    role_title: Optional[str],
) -> Optional[str]:
    """
    Read the JD stored on a tracked pipeline entry (jd_path → vault JD.md).
    Strips YAML frontmatter and blockquote banner lines. Fail-open → None.
    """
    match = _find_application(pipeline, company, role_title)
    jd_path = (match or {}).get("jd_path")
    if not jd_path:
        return None
    try:
        raw = Path(jd_path).read_text(encoding="utf-8")
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            raw = parts[2] if len(parts) >= 3 else raw
        lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith(">")]
        text = "\n".join(lines).strip()
        return text or None
    except Exception as exc:
        print(f"[job_hunter] stored JD read failed ({jd_path}): {exc}")
        return None


def jd_analyzer(state: JobHunterState) -> dict:
    """
    Load the master resume, secure a JD (user-pasted → stored on the tracked
    application → web search → clarify), and extract structured JD signals.
    Fail-open at every step.
    """
    master = load_master_resume(OBSIDIAN_VAULT_PATH, OBSIDIAN_CAREER_FOLDER)
    if master is None:
        yaml_path = f"{OBSIDIAN_VAULT_PATH}/{OBSIDIAN_CAREER_FOLDER}/master_resume.yaml"
        return {
            "needs_clarification": (
                "I don't have a master resume to tailor from yet. "
                f"Run `uv run python scripts/migrate_resume_to_vault.py` to build `{yaml_path}` "
                "from your Resume_1.pdf (review it, then ask me again)."
            )
        }

    jd_text = (state.get("jd_text") or "").strip() or None
    jd_assumed = False

    # JD resolution order: pasted text → JD stored on the tracked pipeline
    # entry → web search (flagged as assumed) → clarify.
    if not jd_text:
        stored = _load_stored_jd(
            state.get("job_pipeline") or [],
            state.get("company"),
            state.get("role_title"),
        )
        if stored:
            jd_text = stored

    if not jd_text:
        company = state.get("company")
        role = state.get("role_title")
        if company or role:
            try:
                from integrations.search import perform_web_search

                query = f"{company or ''} {role or ''} job description requirements".strip()
                results = perform_web_search(query, max_results=3)
                if results and "yielded no results" not in results:
                    jd_text = results
                    jd_assumed = True
            except Exception as exc:
                print(f"[job_hunter] JD web search failed: {exc}")

    if not jd_text:
        return {
            "master_resume": master,
            "needs_clarification": (
                "Which role should I tailor for? Paste the job description "
                "(or at least the company + role, and I'll search for it)."
            ),
        }

    analysis = _llm_analyze_jd(jd_text, state.get("company"), state.get("role_title"))
    return {
        "master_resume": master,
        "jd_text": jd_text[:MAX_JD_CHARS],
        "jd_assumed": jd_assumed,
        "jd_analysis": analysis,
    }


def resume_tailor(state: JobHunterState) -> dict:
    """Layer 2 LLM call — structured tailoring draft from the master resume."""
    master = state.get("master_resume")
    jd_text = state.get("jd_text") or ""
    revision_count = state.get("revision_count", 0) + (1 if state.get("critic_feedback") else 0)

    mentor_guidance = _extract_instruction_block(state["raw_instructions"], "[MENTOR GUIDANCE / STRATEGY]")

    tailored = _llm_tailor(
        master=master,
        jd_text=jd_text,
        jd_analysis=state.get("jd_analysis"),
        critic_feedback=state.get("critic_feedback"),
        revision_count=revision_count,
        mentor_guidance=mentor_guidance,
    )
    if tailored is None:
        return {
            "tailored": None,
            "revision_count": revision_count,
            "feedback_message": (
                "[DRAFT_FAILED] The tailoring model didn't produce a usable draft. "
                "Your master resume and JD are untouched — try again in a moment."
            ),
        }
    return {"tailored": tailored, "revision_count": revision_count, "critic_feedback": None}


def quality_critic(state: JobHunterState) -> dict:
    """
    Pure-code critic (mentor_agent_guidelines.md §2.5/§2.6): metric integrity,
    unknown IDs, skill-subset checks + ATS keyword coverage. Never an LLM verdict.
    """
    tailored = state.get("tailored")
    master = state.get("master_resume")
    if tailored is None or master is None:
        return {}

    violations = metric_violations(tailored, master)

    jd_analysis = state.get("jd_analysis")
    company_name = (jd_analysis.company if jd_analysis else state.get("company") or "").strip().lower()
    if company_name and len(company_name) > 3 and tailored.summary_line and company_name in tailored.summary_line.lower():
        violations.append(f"Summary contains target company name '{jd_analysis.company if jd_analysis else state.get('company')}' - keep it professional and general.")

    if violations and state.get("revision_count", 0) < MAX_TAILOR_REVISIONS:
        feedback = "CODE CHECK VIOLATIONS:\n" + "\n".join(f"- {v}" for v in violations)
        return {"critic_feedback": feedback}

    view = build_resume_view(master, tailored)
    required = jd_analysis.required_keywords if jd_analysis else []
    coverage = keyword_coverage(required, view_plain_text(view))
    fit_report = {
        "coverage": coverage,
        "unresolved_violations": violations,  # non-empty only if the revision cap was hit
        "jd_assumed": state.get("jd_assumed", False),
    }
    return {"critic_feedback": None, "fit_report": fit_report}


def fit_assessor(state: JobHunterState) -> dict:
    """
    assess_fit branch — "how relevant is this role for me?" / "should I apply?"
    JD analysis + deterministic keyword coverage of the MASTER resume + a short
    honest LLM verdict. Writes no files and no memory — a read-only answer.
    """
    master = state.get("master_resume")
    jd_analysis = state.get("jd_analysis")
    jd_text = state.get("jd_text") or ""
    company = (state.get("company") or (jd_analysis.company if jd_analysis else "") or "this company").strip()
    role = (state.get("role_title") or (jd_analysis.job_title if jd_analysis else "") or "this role").strip()

    required = jd_analysis.required_keywords if jd_analysis else []
    coverage = keyword_coverage(required, _master_prompt_block(master)) if master else {"present": [], "missing": required, "coverage_pct": 0}
    verdict = _llm_fit_verdict(master, jd_text, jd_analysis) if master else None

    lines = [f"🧭 **Fit check: {role} @ {company}**", ""]
    if state.get("jd_assumed"):
        lines += ["⚠️ I pulled this JD via web search — verify it matches the actual posting before trusting the read.", ""]
    if jd_analysis and jd_analysis.summary:
        lines += [f"**What they want:** {jd_analysis.summary}", ""]
    if required:
        lines.append(f"**Your master resume covers {coverage['coverage_pct']}% of their required keywords** ({len(coverage['present'])}/{len(coverage['present']) + len(coverage['missing'])}).")
        if coverage.get("missing"):
            lines.append(f"**Honest gaps:** {', '.join(coverage['missing'][:8])}")
        lines.append("")
    if verdict:
        lines += ["**My read:**", verdict, ""]
    lines.append("Want me to tailor your resume for it? Just ask.")
    return {"feedback_message": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Graph nodes — render + vault write (tailor branch terminus)
# ---------------------------------------------------------------------------

def render_and_write(state: JobHunterState) -> dict:
    """
    Write the per-application vault folder (JD, tailored .tex/.pdf/.md, resume
    overlay YAML, Fit Notes) and compile the PDF. Fail-open: a missing compiler
    or an overflow still leaves all source artifacts behind (status 'partial').
    """
    master: MasterResume = state["master_resume"]
    tailored: TailoredResumeDraft = state["tailored"]
    jd_analysis = state.get("jd_analysis")
    fit = state.get("fit_report") or {}

    company = (state.get("company") or (jd_analysis.company if jd_analysis else "") or "Unknown Company").strip()
    role = (state.get("role_title") or (jd_analysis.job_title if jd_analysis else "") or "Role").strip()

    out_dir = application_dir(OBSIDIAN_VAULT_PATH, OBSIDIAN_CAREER_FOLDER, company, role)
    written: list[str] = []

    # 1. JD.md (with provenance flag when the JD came from web search)
    jd_header = "---\ntype: job_description\n"
    if state.get("jd_assumed"):
        jd_header += "assumed_from_web_search: true\n"
    jd_header += f"company: '{company}'\nrole: '{role}'\n---\n\n"
    jd_note = (
        "> ⚠️ This JD was found via web search, not pasted by you — verify it matches the actual posting.\n\n"
        if state.get("jd_assumed") else ""
    )
    jd_path = out_dir / "JD.md"
    jd_path.write_text(jd_header + jd_note + (state.get("jd_text") or ""), encoding="utf-8")
    written.append(str(jd_path))

    # 2. resume.yaml — the tailored overlay (Layer 2 artifact, auditable)
    overlay_path = out_dir / "resume.yaml"
    overlay = {
        "generated_at": _now_iso(),
        "generated_by": "job_hunter",
        "company": company,
        "role": role,
        "tailored": tailored.model_dump(mode="json"),
    }
    overlay_path.write_text(yaml.safe_dump(overlay, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    written.append(str(overlay_path))

    # 3. View → .tex → (compile → .pdf, one-page enforced)
    view = build_resume_view(master, tailored)
    tex_path = out_dir / "Tailored Resume.tex"
    tex_path.write_text(render_tex(view), encoding="utf-8")
    written.append(str(tex_path))

    pdf_status = "skipped"
    pdf_path = None
    ok, log_tail, pdf_path = compile_pdf(tex_path)
    if ok and pdf_path:
        pages = count_pdf_pages(pdf_path)
        if pages > 1:
            # Deterministic one-page trim: drop optional bullets, re-render, recompile.
            trimmed_view = drop_optional_bullets(view, master)
            tex_path.write_text(render_tex(trimmed_view), encoding="utf-8")
            ok2, log_tail, pdf_path2 = compile_pdf(tex_path)
            if ok2 and pdf_path2:
                pages2 = count_pdf_pages(pdf_path2)
                pdf_status = "compiled" if pages2 <= 1 else "overflow"
                pdf_path = pdf_path2
                if pages2 <= 1:
                    view = trimmed_view
            else:
                pdf_status = "failed"
        else:
            pdf_status = "compiled"
    else:
        pdf_status = "no_compiler" if "no LaTeX compiler" in log_tail else "failed"

    if pdf_path and pdf_path.exists():
        written.append(str(pdf_path))

    # 4. Tailored Resume.md — markdown mirror (Obsidian review + chat surface)
    md = render_markdown(view)
    md_path = out_dir / "Tailored Resume.md"
    md_path.write_text(md, encoding="utf-8")
    written.append(str(md_path))

    # 5. Fit Notes.md — honest analysis record
    fit_path = out_dir / "Fit Notes.md"
    fit_path.write_text(_build_fit_notes(state, company, role, fit, pdf_status), encoding="utf-8")
    written.append(str(fit_path))

    # 6. If this company is already tracked in the pipeline, link the artifacts
    #    (tailoring NEVER creates an application entry — deliberate side effects).
    pipeline = [dict(a) for a in state.get("job_pipeline") or []]
    memory_delta: dict[str, Any] = {}
    match = _find_application(pipeline, company, role)
    if match:
        match["jd_path"] = str(jd_path)
        match["tailored_resume_path"] = str(tex_path)
        match["last_updated"] = _now_iso()
        memory_delta["job_pipeline"] = pipeline

    return {
        "written_files": written,
        "pdf_status": pdf_status,
        "tailored_markdown": md,
        "memory_delta": memory_delta,
        "feedback_message": _tailor_feedback(state, company, role, out_dir, fit, tailored, pdf_status),
    }


def _tailor_feedback(
    state: JobHunterState,
    company: str,
    role: str,
    out_dir,
    fit: dict[str, Any],
    tailored: TailoredResumeDraft,
    pdf_status: str,
) -> str:
    """The user-facing summary message for a finished tailor run."""
    jd_analysis = state.get("jd_analysis")
    coverage = fit.get("coverage") or {}

    msg_lines = [f"📄 **Tailored resume ready: {role} @ {company}**", ""]
    if state.get("jd_assumed"):
        msg_lines += [
            "⚠️ I pulled this JD via web search — double-check it matches the actual posting (`JD.md` in the folder).",
            "",
        ]
    if jd_analysis and jd_analysis.summary:
        msg_lines += [f"**What they want:** {jd_analysis.summary}", ""]
    if coverage:
        present = len(coverage.get("present", []))
        total = present + len(coverage.get("missing", []))
        msg_lines.append(f"**ATS keyword coverage:** {coverage.get('coverage_pct', 0)}% ({present}/{total} required keywords)")
        if coverage.get("missing"):
            msg_lines.append(f"**Missing keywords (honest gaps):** {', '.join(coverage['missing'][:8])}")
        msg_lines.append("")
    if tailored.changes_summary:
        msg_lines.append("**What I changed:**")
        msg_lines += [f"- {c}" for c in tailored.changes_summary[:6]]
        msg_lines.append("")
    if fit.get("unresolved_violations"):
        msg_lines.append("⚠️ **Unresolved honesty-check issues (review before using):**")
        msg_lines += [f"- {v}" for v in fit["unresolved_violations"][:5]]
        msg_lines.append("")
    pdf_line = {
        "compiled": "PDF compiled (one page ✓).",
        "overflow": "PDF compiled but spills past one page even after trimming optional bullets — review manually.",
        "no_compiler": "PDF skipped — no LaTeX compiler found (`sudo pacman -S tectonic` / `brew install tectonic`). The .tex and .md are ready.",
        "failed": "PDF compilation failed — the .tex and .md are in the folder for manual review.",
        "skipped": "PDF compilation skipped.",
    }.get(pdf_status, "")
    msg_lines += [
        pdf_line,
        "",
        f"📁 Folder: `{out_dir}`",
        "",
        "Draft only — review `Tailored Resume.md` before sending anything.",
    ]
    return "\n".join(msg_lines)


def _build_fit_notes(
    state: JobHunterState,
    company: str,
    role: str,
    fit: dict[str, Any],
    pdf_status: str,
) -> str:
    """Fit Notes.md — the honest analysis artifact for the application folder."""
    jd_analysis = state.get("jd_analysis")
    tailored = state.get("tailored")
    coverage = fit.get("coverage") or {}

    lines = [
        "---",
        "type: fit_notes",
        f"company: '{company}'",
        f"role: '{role}'",
        f"generated: '{_now_iso()}'",
        "---",
        "",
        f"# Fit Notes — {role} @ {company}",
        "",
    ]
    if state.get("jd_assumed"):
        lines += ["> ⚠️ JD sourced via web search — verify against the real posting.", ""]
    if jd_analysis:
        lines += [
            "## JD Read",
            f"- Seniority: {jd_analysis.seniority or 'unknown'}",
            f"- Domain focus: {', '.join(jd_analysis.domain_focus) or '—'}",
            f"- Summary: {jd_analysis.summary or '—'}",
            "",
            "## Keywords",
            f"- Required: {', '.join(jd_analysis.required_keywords) or '—'}",
            f"- Preferred: {', '.join(jd_analysis.preferred_keywords) or '—'}",
            "",
        ]
    lines += [
        "## ATS Coverage",
        f"- Coverage: {coverage.get('coverage_pct', 0)}%",
        f"- Present: {', '.join(coverage.get('present', [])) or '—'}",
        f"- Missing: {', '.join(coverage.get('missing', [])) or '—'}",
        "",
    ]
    if tailored and tailored.changes_summary:
        lines.append("## Tailoring Changes")
        lines += [f"- {c}" for c in tailored.changes_summary]
        lines.append("")
    if fit.get("unresolved_violations"):
        lines.append("## ⚠️ Unresolved Honesty-Check Issues")
        lines += [f"- {v}" for v in fit["unresolved_violations"]]
        lines.append("")
    lines += ["## Build", f"- PDF status: {pdf_status}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Graph nodes — application tracking branch
# ---------------------------------------------------------------------------

def application_logger(state: JobHunterState) -> dict:
    """
    Handles log_application + update_status.

    Memory writes (dual-key design):
      - "job_pipeline": the FULL updated list → profile_facts (current state)
      - "applications": [one event payload]  → episodic job_application event
        (the append-only timeline; memory_merger already knows how to summarise it)
    """
    pipeline = [dict(a) for a in state.get("job_pipeline") or []]
    company = (state.get("company") or "").strip()
    role = (state.get("role_title") or "").strip()
    action = state["action_type"]

    if not company:
        return {
            "needs_clarification": (
                "Which company (and role) is this about? "
                "e.g. 'I applied to Stripe for the AI Engineer role' or "
                "'Stripe moved me to screening'."
            )
        }

    match = _find_application(pipeline, company, role or None)
    now_iso = _now_iso()

    if action == "log_application":
        if match:
            # Re-log = refresh the existing entry, never a duplicate.
            if state.get("stage"):
                match["stage"] = state["stage"]
            match["last_updated"] = now_iso
            for k in ("referral_contact", "job_url", "location", "notes"):
                if state.get(k):
                    match[k] = state[k]
            stage = match.get("stage", "applied")
            msg = (
                f"📥 **Already tracking {match.get('role_title', role)} @ {match['company']}** — "
                f"refreshed the entry (stage: {stage}). No duplicate created."
            )
        else:
            stage = state.get("stage") or "applied"
            match = {
                "company": company,
                "role_title": role or "Unknown Role",
                "stage": stage,
                "applied_date": state.get("applied_date") or _today_iso(),
                "last_updated": now_iso,
                "referral_contact": state.get("referral_contact"),
                "job_url": state.get("job_url"),
                "location": state.get("location"),
                "notes": state.get("notes"),
            }
            pipeline.append(match)
            msg = f"📥 **Logged: {match['role_title']} @ {company}** (stage: {stage})."
            if state.get("referral_contact"):
                msg += f" Referral via {state['referral_contact']} — noted."

        event = {
            "company": match["company"],
            "role": match.get("role_title", role),
            "stage": match.get("stage", "applied"),
            "applied_date": match.get("applied_date"),
            "referral_contact": match.get("referral_contact"),
            "job_url": match.get("job_url"),
            "location": match.get("location"),
            "notes": match.get("notes"),
        }
        active = sum(1 for a in pipeline if a.get("stage") not in TERMINAL_STAGES)
        msg += f"\n\nPipeline now: {active} active application(s)."
        return {
            "memory_delta": {"job_pipeline": pipeline, "applications": [event]},
            "feedback_message": msg,
        }

    # ---- update_status ----
    stage = state.get("stage")
    if not stage:
        return {
            "needs_clarification": (
                f"What's the new stage for {company}? "
                "(applied / screening / interviewing / offer / rejected / withdrawn)"
            )
        }

    if match:
        old_stage = match.get("stage", "applied")
        match["stage"] = stage
        match["last_updated"] = now_iso
        if state.get("notes"):
            prior = (match.get("notes") or "").strip()
            match["notes"] = f"{prior}\n[{_today_iso()}] {state['notes']}".strip()
        msg = f"🔄 **{match.get('role_title', role)} @ {match['company']}**: {old_stage} → **{stage}**."
    else:
        # Status update for an untracked application — create it, but flag honestly.
        match = {
            "company": company,
            "role_title": role or "Unknown Role",
            "stage": stage,
            "applied_date": state.get("applied_date"),
            "last_updated": now_iso,
            "referral_contact": state.get("referral_contact"),
            "job_url": state.get("job_url"),
            "location": state.get("location"),
            "notes": f"(created from a status update on {_today_iso()} — never logged the original application)",
        }
        pipeline.append(match)
        msg = (
            f"🔄 **{match['role_title']} @ {company}** → **{stage}**.\n"
            "_(I wasn't tracking this one yet, so I created the entry from your update.)_"
        )

    days = _days_since(match.get("applied_date"), datetime.now(timezone.utc))
    if days is not None:
        msg += f"\n\nApplied {days} day(s) ago."

    event = {
        "company": match["company"],
        "role": match.get("role_title", role),
        "stage": stage,
        "notes": state.get("notes"),
    }
    return {
        "memory_delta": {"job_pipeline": pipeline, "applications": [event]},
        "feedback_message": msg,
    }


# ---------------------------------------------------------------------------
# Graph nodes — pipeline review branch
# ---------------------------------------------------------------------------

def pipeline_analyzer(state: JobHunterState) -> dict:
    """
    job_search_review — honest pipeline report, computed on demand (nothing
    cached; the cognition/metrics.py philosophy). No memory writes.
    """
    pipeline = state.get("job_pipeline") or []
    history = state.get("application_history") or []
    now = datetime.now(timezone.utc)

    if not pipeline:
        return {
            "feedback_message": (
                "📊 **Job hunt review:** nothing tracked yet. "
                "Tell me when you apply somewhere (\"I applied to Stripe for…\") "
                "and I'll keep the pipeline for you."
            )
        }

    active = [a for a in pipeline if a.get("stage") not in TERMINAL_STAGES]
    terminal = [a for a in pipeline if a.get("stage") in TERMINAL_STAGES]

    by_stage: dict[str, int] = {}
    for a in pipeline:
        s = a.get("stage", "unknown")
        by_stage[s] = by_stage.get(s, 0) + 1

    stale = []
    for a in active:
        days = _days_since(a.get("last_updated"), now)
        if days is not None and days >= STALE_APPLICATION_DAYS and a.get("stage") in (
            "wishlist", "applied", "referral_requested", "screening",
        ):
            stale.append((a, days))
    stale.sort(key=lambda t: t[1], reverse=True)

    non_wishlist = [a for a in pipeline if a.get("stage") != "wishlist"]
    responded = [a for a in non_wishlist if a.get("stage") in ("screening", "interviewing", "offer")]
    response_rate = round(100 * len(responded) / max(1, len(non_wishlist)))
    interviews = [a for a in pipeline if a.get("stage") == "interviewing"]
    offers = [a for a in pipeline if a.get("stage") == "offer"]

    lines = ["📊 **Job hunt review**", ""]
    lines.append(f"**Pipeline:** {len(active)} active · {len(terminal)} closed · {response_rate}% response rate")
    funnel = " → ".join(f"{s}:{n}" for s, n in sorted(by_stage.items()))
    lines += [f"**By stage:** {funnel}", ""]

    if interviews or offers:
        lines.append("**🔥 Hot right now:**")
        for a in interviews + offers:
            lines.append(f"- {a.get('role_title', '?')} @ {a.get('company')} ({a.get('stage')})")
        lines.append("")

    if stale:
        lines.append(f"**⏳ Gone quiet (≥{STALE_APPLICATION_DAYS} days — follow-up candidates):**")
        for a, days in stale[:5]:
            lines.append(f"- {a.get('role_title', '?')} @ {a.get('company')} — {a.get('stage')}, silent {days}d")
        lines.append("")

    if history:
        lines.append("**Recent movement:**")
        for evt in history[:3]:
            content = (evt.get("content") or "").strip()
            occurred = (evt.get("occurred_at") or "")[:10]
            if content:
                lines.append(f"- [{occurred}] {content[:120]}")
        lines.append("")

    if not stale and not interviews and not offers and active:
        lines.append("Nothing hot and nothing stale — keep the top of the funnel fed.")

    return {"feedback_message": "\n".join(lines)}


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Graph nodes — job search branch
# ---------------------------------------------------------------------------

@traced("job_searcher", "tool")
def job_searcher_node(state: JobHunterState) -> dict:
    from agents.Job_Hunter.state import JobSearchParams
    from integrations.job_search import search_jobs

    user_text = state.get("user_text") or ""
    raw_instructions = state.get("raw_instructions") or ""
    profile = state["task"].memory_slice.relevant_profile or {}

    # LLM extracts search parameters from the user's request + mentor guidance.
    # The mentor guidance (notes_for_agent from the reasoner) already has the
    # synthesized, conflict-free picture — locations, seniority, exclusions, etc.
    try:
        from pydantic import BaseModel, Field

        class SearchIntent(BaseModel):
            role_queries: list[str] = Field(description="1-2 specific role titles or phrases to search (e.g. 'AI Engineer', 'Machine Learning Engineer'). Prefer the user's own words.")
            locations: list[str] = Field(description="1-2 specific locations. If the user named a city/country, use ONLY that. Never add profile defaults like 'Japan' if the user said something else.")
            max_experience_years: int = Field(default=4, description="Max years of experience. Early career → 4. Mid → 7. Senior → 10+.")
            exclude_keywords: list[str] = Field(default_factory=list, description=(
                "Terms to EXCLUDE from search results. Be comprehensive: "
                "• If user wants early-career roles: include 'senior', 'lead', 'principal', 'staff', '8+ years', '10+ years'"
                "• If job descriptions are in a language the user doesn't speak: include that language (e.g. '日本語', 'japanese', 'japanisch')"
                "• Include any words the user explicitly said to avoid"
                "• List items are AND-ed — broad strokes are fine, the job_filter_node deduplicates"
            ))

        from orchestrator.llm import get_reasoning_llm
        structured = get_reasoning_llm(temperature=0.1).with_structured_output(SearchIntent)

        # Feed both the user's request and the full instructions (which contain
        # the mentor guidance/strategy block from the reasoner).
        combined = f"User request: {user_text}\n\nFull context:\n{raw_instructions[:2000]}"
        with component("job_hunter:search_intent", tags=["component:job_hunter:search_intent"]):
            llm_result = structured.invoke([
                ("system", _SEARCH_PARAMS_PROMPT),
                ("user", combined),
            ])
        intent = llm_result if isinstance(llm_result, SearchIntent) else SearchIntent(**llm_result)
    except Exception as exc:
        print(f"[job_hunter] search params extraction failed: {exc}")
        # Fallback: use user_text as a single role query, search India
        intent = SearchIntent(
            role_queries=[user_text[:100] if user_text else "AI Engineer"],
            locations=["India"],
        )

    params = JobSearchParams(
        role_queries=intent.role_queries[:2],
        locations=intent.locations[:3],
        remote_ok=True,
        max_experience_years=intent.max_experience_years,
        exclude_keywords=intent.exclude_keywords,
        max_results=10,
        include_ats_direct=True,
    )

    raw_listings = search_jobs(
        role_queries=params.role_queries,
        locations=params.locations,
        include_ats_direct=params.include_ats_direct,
        num_per_source=params.max_results
    )

    # Load the master resume so job_scorer_node scores against the REAL profile.
    master = load_master_resume(OBSIDIAN_VAULT_PATH, OBSIDIAN_CAREER_FOLDER)
    if master is None:
        print("[job_hunter] master resume missing — job scores will be placeholders")

    return {
        "search_params": params,
        "raw_job_listings": raw_listings,
        "master_resume": master,
    }


def job_filter_node(state: JobHunterState) -> dict:
    raw = state.get("raw_job_listings") or []
    params = state.get("search_params")
    if not raw or not params:
        return {"filtered_job_listings": []}

    pipeline = state.get("job_pipeline") or []
    rejected_companies = {
        (a.get("company") or "").lower().strip()
        for a in pipeline if a.get("stage") in ("rejected", "withdrawn")
    }

    filtered = []
    seen = set()

    for job in raw:
        # Deduplication by URL
        url = job.get("apply_url") or ""
        if url and url in seen:
            continue

        # Deduplication by company + title
        title = (job.get("title") or "").lower().strip()
        comp = (job.get("company") or "").lower().strip()
        dedup_key = f"{comp}::{title}"
        if dedup_key in seen:
            continue

        # Hard filters
        if comp in rejected_companies:
            continue

        desc = (job.get("description") or "").lower()
        if any(kw.lower() in desc[:500] or kw.lower() in title for kw in params.exclude_keywords):
            continue

        exp = str(job.get("experience_required") or "").lower()
        if "5+" in exp or "8+" in exp or "10+" in exp or "senior" in title.split() or "staff" in title.split():
            continue

        seen.add(url)
        seen.add(dedup_key)
        filtered.append(job)

    print(f"[job_hunter] Filtered {len(raw)} raw jobs down to {len(filtered)}")
    return {"filtered_job_listings": filtered}


_SCORE_PROMPT = """You are a brutal recruiter scoring a batch of job listings against a candidate's profile.

Candidate Profile Summary:
{profile_summary}

You will receive a list of job listings. For EACH job, provide:
- fit_score: 0-100 (be strict: 90+ is a perfect match, 70s is good, <50 is bad)
- match_reasons: 2-3 short bullets why it fits
- honest_gaps: 1-2 short bullets on missing skills/experience
- recommendation: 'apply_now' | 'tailor_first' | 'skip'

Return a JSON array of objects, strictly in the same order as the input jobs.
Each object must have: fit_score (int), match_reasons (list of str), honest_gaps (list of str), recommendation (str)."""

@traced("job_scorer", "llm")
def job_scorer_node(state: JobHunterState) -> dict:
    filtered = state.get("filtered_job_listings") or []
    if not filtered:
        return {"scored_job_listings": [], "feedback_message": "No jobs found matching your criteria."}

    # Take top 15 max to avoid blowing up the context window
    to_score = filtered[:15]
    master = state.get("master_resume")

    # Fallback if no master resume
    if not master:
        # Just return placeholder scores if we can't score (master resume is
        # normally loaded by job_searcher_node — this only fires if the vault
        # YAML is missing). Keep the top-10 cap consistent with the LLM path.
        scored = []
        from agents.Job_Hunter.state import ScoredJobListing
        for i, job in enumerate(to_score[:10]):
            scored.append(ScoredJobListing(
                rank=i+1,
                title=job["title"], company=job["company"], location=job["location"],
                source_platform=job["source_platform"], apply_url=job["apply_url"],
                fit_score=80, match_reasons=["Matches target role"], honest_gaps=["Needs manual review"],
                recommendation="tailor_first",
                description_snippet=job["description"][:300], description_full=job["description"],
                salary_range=job["salary_range"], posted_date=job["posted_date"],
                description_truncated=job["description_truncated"]
            ))
        return {"scored_job_listings": scored}

    # Real profile signal: actual skill ITEMS (not just category labels),
    # experience roles, and project names — the scorer can't judge fit from
    # labels like "Languages" / "AI / ML" alone.
    skills_flat = ", ".join(dict.fromkeys(item for c in master.skills for item in c.items))
    roles_str = ", ".join(dict.fromkeys(e.role for e in master.experience if e.role))
    projects_str = ", ".join(p.name for p in master.projects[:4])
    profile_summary = (
        f"Headline: {master.headline or '—'}\n"
        f"Experience roles: {roles_str or '—'}\n"
        f"Skills: {skills_flat or '—'}\n"
        f"Projects: {projects_str or '—'}"
    )

    jobs_text = ""
    for i, job in enumerate(to_score):
        jobs_text += f"\n[{i}]\nTitle: {job['title']} @ {job['company']}\nLoc: {job['location']}\nDesc: {job['description'][:600]}\n"

    try:
        from pydantic import BaseModel

        from orchestrator.llm import get_reasoning_llm

        class ScoreResult(BaseModel):
            fit_score: int
            match_reasons: list[str]
            honest_gaps: list[str]
            recommendation: str

        class BatchScoreOutput(BaseModel):
            scores: list[ScoreResult]

        llm = get_reasoning_llm(temperature=0.1).with_structured_output(BatchScoreOutput)
        human = f"JOBS TO SCORE:\n{jobs_text}"
        sys_msg = _SCORE_PROMPT.format(profile_summary=profile_summary)

        with component("job_hunter:score", tags=["component:job_hunter:score"]):
            raw = llm.invoke([("system", sys_msg), ("user", human)])
        batch_output = raw if isinstance(raw, BatchScoreOutput) else BatchScoreOutput(**raw)

        from agents.Job_Hunter.state import ScoredJobListing
        scored = []
        for i, job in enumerate(to_score):
            # The LLM occasionally returns fewer score rows than input jobs —
            # pad with a neutral score instead of silently dropping the job.
            if i < len(batch_output.scores):
                score = batch_output.scores[i]
            else:
                score = ScoreResult(
                    fit_score=70,
                    match_reasons=["Matches target role keywords"],
                    honest_gaps=["Not individually scored"],
                    recommendation="tailor_first",
                )
            scored.append(ScoredJobListing(
                rank=0, # will set after sort
                title=job["title"], company=job["company"], location=job["location"],
                source_platform=job["source_platform"], apply_url=job["apply_url"],
                fit_score=score.fit_score, match_reasons=score.match_reasons,
                honest_gaps=score.honest_gaps, recommendation=score.recommendation,
                description_snippet=job["description"][:300], description_full=job["description"],
                salary_range=job["salary_range"], posted_date=job["posted_date"],
                description_truncated=job["description_truncated"]
            ))

        # Sort by score descending
        scored.sort(key=lambda x: x.fit_score, reverse=True)
        for i, s in enumerate(scored):
            s.rank = i + 1

        # Keep top 10
        return {"scored_job_listings": scored[:10]}

    except Exception as exc:
        print(f"[job_hunter] Scoring failed: {exc}")
        return {"scored_job_listings": []}


@traced("job_digest")
def digest_formatter_node(state: JobHunterState) -> dict:
    scored = state.get("scored_job_listings") or []
    if not scored:
        return {"feedback_message": "Could not find or score any matching jobs this time."}

    lines = ["🔍 **Job Search Results**", ""]

    search_cache = {}
    for job in scored:
        search_cache[str(job.rank)] = job.model_dump()

        rec_emoji = "✅" if job.recommendation == "apply_now" else ("✏️" if job.recommendation == "tailor_first" else "⏭️")
        lines.append(f"**#{job.rank} {job.title} @ {job.company}** [{job.fit_score}% fit]")
        lines.append(f"📍 {job.location} | 🌐 {job.source_platform}")
        if job.match_reasons:
            lines.append(f"✅ {job.match_reasons[0]}")
        if job.honest_gaps:
            lines.append(f"⚠️ {job.honest_gaps[0]}")
        lines.append(f"{rec_emoji} {job.recommendation.replace('_', ' ').title()}")
        if job.apply_url:
            # Show the apply URL explicitly (as the link text) so the user can
            # click straight through to apply — visible and copyable regardless
            # of how the chat renders markdown.
            lines.append(f"🔗 **Apply:** [{job.apply_url}]({job.apply_url})")
        else:
            # No direct URL — provide a search fallback so the user can find it.
            search_q = f"{job.company} {job.title} apply"
            lines.append(f"🔗 **Find posting:** [Search Google for \"{search_q}\"](https://www.google.com/search?q={quote(search_q)})")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 **To act on any of these:**")
    lines.append("• `Tailor resume for #2` → tailors + renders PDF")
    lines.append("• `Log #3 as applied` → tracks in your pipeline")

    # Persist the search cache as this agent's private scratch memory so "#N"
    # references ("Tailor resume for #2") resolve on later turns. The outer key
    # MUST be the registered agent name ("job_hunter") — memory_merger writes
    # agent_private_memory items as set_private_memory(<key>, <data>) and the
    # context builder reads get_private_memory("job_hunter"). Writing the cache
    # under "job_hunter_search_cache" used to land it in a row nobody reads.
    #
    # Persist the scored listings into the SINGLE job board
    # (profile_facts career.job_pipeline): search results become wishlist-stage
    # rows via scored_listing_to_pipeline_entry(). A later search refreshes the
    # same row (dedup by dedup_hash) without ever downgrading a row the user has
    # already moved to a real application stage.
    from agents.Job_Hunter.state import _dedup_key, scored_listing_to_pipeline_entry

    pipeline = [dict(p) for p in (state.get("job_pipeline") or []) if isinstance(p, dict)]
    by_row: dict[str, dict[str, Any]] = {}
    for row in pipeline:
        key = row.get("dedup_hash") or _dedup_key(row.get("company"), row.get("title"), row.get("location"))
        by_row[key] = row

    for job in scored:
        key = _dedup_key(job.company, job.title, job.location)
        rec = scored_listing_to_pipeline_entry(job).model_dump()
        existing = by_row.get(key)
        if existing is not None:
            # Refresh the search metadata but preserve the current stage.
            existing.update({k: rec[k] for k in (
                "fit_score", "match_reasons", "honest_gaps",
                "description_snippet", "description_full",
                "salary_range", "posted_date", "apply_url", "location",
            )})
            existing["source"] = "serpapi"
            existing["updated_at"] = _now_iso()
        else:
            by_row[key] = rec

    board_list = list(by_row.values())
    board_list.sort(key=lambda r: r.get("fit_score") or 0, reverse=True)
    board_list = board_list[:JOB_BOARD_MAX]

    delta = state.get("memory_delta") or {}
    delta["agent_private_memory"] = {"job_hunter": {"job_hunter_search_cache": search_cache}}
    delta["job_pipeline"] = board_list

    return {
        "search_digest": "\n".join(lines),
        "feedback_message": "\n".join(lines),
        "memory_delta": delta,
        "search_cache": search_cache,
        "job_pipeline": board_list,
    }


# pack_result + routing
# ---------------------------------------------------------------------------

def _action_to_task_type(action: str) -> str:
    return {
        "tailor_resume": "tailor_resume",
        "log_application": "log_application",
        "update_status": "update_application_status",
        "review": "job_search_review",
        "assess_fit": "assess_fit",
        "search_jobs": "search_jobs",
    }.get(action, "job_search_review")


def pack_result(state: JobHunterState) -> dict:
    """Package the final state into the AgentResult contract."""
    task = state["task"]

    if state.get("needs_clarification"):
        return {
            "result": AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                task_type=task.task_type or "job_search_review",
                status=ResultStatus.NEEDS_CLARIFICATION,
                clarification_needed=state["needs_clarification"],
                output=state["needs_clarification"],
            )
        }

    feedback = state.get("feedback_message") or ""
    if feedback.startswith("[DRAFT_FAILED]"):
        return {
            "result": AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                task_type=task.task_type or "tailor_resume",
                status=ResultStatus.FAILED,
                output=feedback,
                error_message="tailoring LLM produced no usable draft",
            )
        }

    drafts: list[DraftSuggestion] = []
    if state.get("tailored_markdown"):
        fit = state.get("fit_report") or {}
        coverage = fit.get("coverage") or {}
        drafts.append(
            DraftSuggestion(
                kind="tailored_resume",
                content=state["tailored_markdown"],
                suggested_destination=(state.get("written_files") or [None])[0],
                metadata={
                    "pdf_status": state.get("pdf_status", "skipped"),
                    "ats_coverage_pct": coverage.get("coverage_pct"),
                    "missing_keywords": coverage.get("missing", []),
                    "files": state.get("written_files", []),
                    "jd_assumed": state.get("jd_assumed", False),
                },
            )
        )

    pdf_status = state.get("pdf_status", "skipped")
    if state.get("written_files"):
        status = ResultStatus.PARTIAL if pdf_status in ("no_compiler", "failed", "overflow") else ResultStatus.SUCCESS
    else:
        status = ResultStatus.SUCCESS if feedback else ResultStatus.FAILED

    return {
        "result": AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            task_type=task.task_type or _action_to_task_type(state.get("action_type", "review")),
            status=status,
            output=feedback or "job_hunter finished without output.",
            memory_delta=state.get("memory_delta", {}),
            draft_suggestions=drafts,
        )
    }


def route_by_action(state: JobHunterState) -> str:
    action = state.get("action_type", "review")
    if action in ("tailor_resume", "assess_fit"):
        return "jd_analyzer"
    if action in ("log_application", "update_status"):
        return "application_logger"
    if action == "search_jobs":
        return "job_searcher_node"
    return "pipeline_analyzer"


def route_after_jd(state: JobHunterState) -> str:
    if state.get("needs_clarification"):
        return "pack_result"
    if state.get("action_type") == "assess_fit":
        return "fit_assessor"
    return "resume_tailor"


def route_after_critic(state: JobHunterState) -> str:
    if state.get("tailored") is None or state.get("master_resume") is None:
        return "pack_result"
    if state.get("critic_feedback"):
        return "resume_tailor"
    return "render_and_write"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

builder = StateGraph(JobHunterState)

builder.add_node("input_parser", input_parser)
builder.add_node("jd_analyzer", jd_analyzer)
builder.add_node("resume_tailor", resume_tailor)
builder.add_node("quality_critic", quality_critic)
builder.add_node("render_and_write", render_and_write)
builder.add_node("application_logger", application_logger)
builder.add_node("pipeline_analyzer", pipeline_analyzer)
builder.add_node("fit_assessor", fit_assessor)
builder.add_node("pack_result", pack_result)
# Search branch
builder.add_node("job_searcher_node", job_searcher_node)
builder.add_node("job_filter_node", job_filter_node)
builder.add_node("job_scorer_node", job_scorer_node)
builder.add_node("digest_formatter_node", digest_formatter_node)

builder.add_edge(START, "input_parser")
builder.add_conditional_edges(
    "input_parser",
    route_by_action,
    {
        "jd_analyzer": "jd_analyzer",
        "application_logger": "application_logger",
        "pipeline_analyzer": "pipeline_analyzer",
        "job_searcher_node": "job_searcher_node",
    },
)
builder.add_conditional_edges(
    "jd_analyzer",
    route_after_jd,
    {
        "resume_tailor": "resume_tailor",
        "fit_assessor": "fit_assessor",
        "pack_result": "pack_result",
    },
)
builder.add_edge("resume_tailor", "quality_critic")
builder.add_conditional_edges(
    "quality_critic",
    route_after_critic,
    {
        "resume_tailor": "resume_tailor",
        "render_and_write": "render_and_write",
        "pack_result": "pack_result",
    },
)
builder.add_edge("render_and_write", "pack_result")
builder.add_edge("application_logger", "pack_result")
builder.add_edge("pipeline_analyzer", "pack_result")
builder.add_edge("fit_assessor", "pack_result")

# Search branch edges
builder.add_edge("job_searcher_node", "job_filter_node")
builder.add_edge("job_filter_node", "job_scorer_node")
builder.add_edge("job_scorer_node", "digest_formatter_node")
builder.add_edge("digest_formatter_node", "pack_result")

builder.add_edge("pack_result", END)

app = builder.compile()


# ---------------------------------------------------------------------------
# Entry point (registry-facing)
# ---------------------------------------------------------------------------

def run_job_hunter(task: AgentTask) -> AgentResult:
    """Run the Job Hunter subgraph against an incoming AgentTask."""
    with component(
        f"agent:{task.agent_name}",
        tags=[f"agent:{task.agent_name}", f"task:{task.task_type}"],
        metadata={"agent_name": task.agent_name, "task_type": task.task_type},
    ):
        final_state = app.invoke(build_initial_state(task))
    result = final_state.get("result")
    if result is None:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            task_type=task.task_type,
            status=ResultStatus.FAILED,
            output="job_hunter finished without producing a result.",
            error_message="graph finished without a result node",
        )
    return result
