"""
scripts/tests/test_job_hunter.py

Verification suite for the job_hunter agent. Runs fully offline except the
final end-to-end tailor test, which needs an LLM key (auto-skips otherwise).
Never touches the real Obsidian vault — live test uses a temp vault.

Covers:
1. Metric-integrity guard (fabricated numbers rejected, verbatim numbers pass)
2. LaTeX escaping + fixed-template rendering (no compiler needed)
3. Deterministic one-page trim (drop_optional_bullets)
4. Application logging: log → dedup → stage update → auto-create on unknown
5. Pipeline review: stale detection + funnel + response rate
6. Orchestrator dispatch routing → job_hunter
7. LIVE end-to-end tailor (env-gated): JD paste → vault artifacts written

Usage:
    uv run python scripts/tests/test_job_hunter.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure root workspace directory is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()

import agents.Job_Hunter.job_hunter as jh
from agents.Job_Hunter.render import (
    build_resume_view,
    drop_optional_bullets,
    find_compiler,
    metric_violations,
    render_markdown,
    render_tex,
    save_master_resume,
)
from agents.Job_Hunter.state import (
    Bullet,
    Contact,
    Experience,
    MasterResume,
    Project,
    SkillCategory,
    TailoredBullet,
    TailoredEntry,
    TailoredResumeDraft,
)
from schemas import AgentTask, MemorySlice, TaskSource

PASS = "✅"
FAIL = "❌"
_failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {PASS if condition else FAIL} {label}")
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_master() -> MasterResume:
    return MasterResume(
        name="Nikhil Sharma",
        headline="AI Engineer — LLM systems, agents, RAG",
        contact=Contact(email="nik@example.com", phone="+91 0000000000", github="github.com/nik-shar"),
        skills=[
            SkillCategory(label="Languages", items=["Python", "C++", "SQL"]),
            SkillCategory(label="AI / ML", items=["LLMs", "RAG", "LangChain", "LangGraph"]),
        ],
        experience=[
            Experience(
                id="exp_turing",
                company="Turing",
                role="Data Scientist – LLM Training & Evaluation",
                location="Remote",
                period="Jun 2025 – Apr 2026",
                bullets=[
                    Bullet(id="turing_b1", text="Engineered schema-driven validation systems for structured LLM outputs, cutting data errors by ~30%.", tags=["llm", "validation"], metrics=["~30%"]),
                    Bullet(id="turing_b2", text="Authored 500+ SFT prompt-response pairs for code generation and reasoning tasks.", tags=["sft"], metrics=["500+"], optional=True),
                ],
            )
        ],
        projects=[
            Project(
                id="proj_taskchain",
                name="Taskchain Agent Orchestrator",
                tech=["Python", "FastAPI", "LangGraph"],
                bullets=[
                    Bullet(id="taskchain_b1", text="Architected a multi-agent workspace cutting onboarding time by ~60%.", tags=["agents"], metrics=["~60%"]),
                ],
            )
        ],
    )


def _make_task(
    instructions: str,
    task_type: str = "job_search_review",
    profile: dict | None = None,
    params: dict | None = None,
) -> AgentTask:
    return AgentTask(
        task_id="test-1",
        agent_name="job_hunter",
        task_type=task_type,
        instructions=instructions,
        source=TaskSource.CHAT,
        memory_slice=MemorySlice(
            agent_name="job_hunter",
            task_type=task_type,
            relevant_profile=profile or {},
        ),
        params=params or {},
    )


# ---------------------------------------------------------------------------
# 1. Metric-integrity guard
# ---------------------------------------------------------------------------

def test_metric_guard() -> None:
    print("\n=== 1. Metric-integrity guard ===")
    master = _sample_master()

    clean = TailoredResumeDraft(
        experience=[TailoredEntry(source_id="exp_turing", bullets=[
            TailoredBullet(source_id="turing_b1", text="Built schema-driven validation for LLM outputs, cutting data errors by ~30%."),
        ])],
        projects=[TailoredEntry(source_id="proj_taskchain", bullets=[
            TailoredBullet(source_id="taskchain_b1", text="Architected a multi-agent workspace cutting onboarding time by ~60%."),
        ])],
        skills=[SkillCategory(label="Languages", items=["Python", "SQL"])],
    )
    violations = metric_violations(clean, master)
    check(violations == [], f"clean draft passes the guard (got {violations})")

    fabricated = TailoredResumeDraft(
        experience=[TailoredEntry(source_id="exp_turing", bullets=[
            TailoredBullet(source_id="turing_b1", text="Achieved 98.7% accuracy on FinanceBench benchmarks."),
        ])],
    )
    violations = metric_violations(fabricated, master)
    check(any("98.7" in v for v in violations), "fabricated metric '98.7' is caught")

    unknown = TailoredResumeDraft(
        experience=[TailoredEntry(source_id="exp_ghost", bullets=[
            TailoredBullet(source_id="ghost_b1", text="Invented bullet."),
        ])],
    )
    violations = metric_violations(unknown, master)
    check(any("unknown entry" in v for v in violations), "unknown entry id is caught")
    check(any("unknown bullet" in v for v in violations), "unknown bullet id is caught")

    bad_skill = TailoredResumeDraft(
        skills=[SkillCategory(label="Languages", items=["Python", "Rust"])],
    )
    violations = metric_violations(bad_skill, master)
    check(any("Rust" in v for v in violations), "skill not in master is caught")


# ---------------------------------------------------------------------------
# 2. LaTeX escaping + template rendering (no compiler needed)
# ---------------------------------------------------------------------------

def test_template_render() -> None:
    print("\n=== 2. LaTeX escaping + fixed-template rendering ===")
    master = _sample_master()
    master.experience[0].bullets[0].text = "Engineered R&D validation systems, cutting data errors by ~30% & improving consistency_2."

    draft = TailoredResumeDraft(
        summary_line="AI engineer focused on LLM systems.",
        experience=[TailoredEntry(source_id="exp_turing", bullets=[
            TailoredBullet(source_id="turing_b1", text="Engineered R&D validation systems, cutting data errors by ~30% & improving consistency_2."),
        ])],
        projects=[TailoredEntry(source_id="proj_taskchain", bullets=[
            TailoredBullet(source_id="taskchain_b1", text="Architected a multi-agent workspace cutting onboarding time by ~60%."),
        ])],
    )
    view = build_resume_view(master, draft)
    tex = render_tex(view)

    check("\\&" in tex, "ampersand is escaped (R\\&D)")
    check("\\%" in tex, "percent is escaped (30\\%)")
    check("consistency\\_2" in tex, "underscore is escaped")
    check("[[" not in tex and "]]" not in tex, "no Jinja delimiters left in output")
    check("\\section{Experience}" in tex and "\\section{Projects}" in tex, "sections render")
    check("exp_turing" not in tex, "internal IDs never leak into the .tex")

    md = render_markdown(view)
    check("Turing" in md and "30%" in md, "markdown mirror carries raw readable text")


# ---------------------------------------------------------------------------
# 3. Deterministic one-page trim
# ---------------------------------------------------------------------------

def test_optional_trim() -> None:
    print("\n=== 3. One-page trim (drop_optional_bullets) ===")
    master = _sample_master()
    draft = TailoredResumeDraft(
        experience=[TailoredEntry(source_id="exp_turing", bullets=[
            TailoredBullet(source_id="turing_b1", text="Built schema-driven validation, cutting data errors by ~30%."),
            TailoredBullet(source_id="turing_b2", text="Authored 500+ SFT prompt-response pairs for code generation and reasoning tasks."),
        ])],
    )
    view = build_resume_view(master, draft)
    trimmed = drop_optional_bullets(view, master)

    exp_bullets = view["experience"][0]["bullets"]
    trimmed_bullets = trimmed["experience"][0]["bullets"]
    check(len(exp_bullets) == 2, f"view starts with 2 bullets (got {len(exp_bullets)})")
    check(len(trimmed_bullets) == 1, "optional bullet dropped from trimmed view")
    check("~30%" in trimmed_bullets[0], "required bullet survives the trim")
    check(len(view["experience"][0]["bullets"]) == 2, "original view is not mutated")


# ---------------------------------------------------------------------------
# 4. Application logging: log → dedup → stage update → auto-create
# ---------------------------------------------------------------------------

def test_application_logger() -> None:
    print("\n=== 4. Application logging flow (offline, LLM parse stubbed) ===")
    original_parse = jh._llm_parse
    jh._llm_parse = lambda _text: None  # deterministic: force keyword heuristics
    try:
        # -- log a new application --
        task = _make_task(
            "I applied to Stripe for the AI Engineer role",
            task_type="log_application",
            params={"action": "log_application", "company": "Stripe", "role": "AI Engineer"},
        )
        result = jh.run_job_hunter(task)
        pipeline = result.memory_delta.get("job_pipeline", [])
        events = result.memory_delta.get("applications", [])
        check(result.status.value == "success", f"log_application succeeds (got {result.status.value})")
        check(len(pipeline) == 1 and pipeline[0]["company"] == "Stripe", "pipeline gains the Stripe entry")
        check(pipeline[0]["stage"] == "applied", "default stage is 'applied'")
        check(len(events) == 1 and events[0].get("role") == "AI Engineer",
              "one episodic job_application event with merger-compatible keys (company/role/stage)")

        # -- log the same application again → dedup --
        task = _make_task(
            "I applied to Stripe for the AI Engineer role",
            task_type="log_application",
            profile={"job_pipeline": pipeline},
            params={"action": "log_application", "company": "Stripe", "role": "AI Engineer"},
        )
        result = jh.run_job_hunter(task)
        pipeline2 = result.memory_delta.get("job_pipeline", [])
        check(len(pipeline2) == 1, "re-logging dedups — no duplicate entry")
        check("Already tracking" in result.output, "dedup is surfaced honestly in the output")

        # -- update the stage --
        task = _make_task(
            "Stripe moved me to interviewing",
            task_type="update_application_status",
            profile={"job_pipeline": pipeline2},
            params={"action": "update_status", "company": "Stripe", "stage": "interviewing"},
        )
        result = jh.run_job_hunter(task)
        pipeline3 = result.memory_delta.get("job_pipeline", [])
        check(pipeline3[0]["stage"] == "interviewing", "stage transitions applied → interviewing")
        check("🔄" in result.output and "applied → **interviewing**" in result.output,
              "transition is reported with the old stage")

        # -- update an untracked company → auto-create + honest flag --
        task = _make_task(
            "Got rejected by NewCorp",
            task_type="update_application_status",
            profile={"job_pipeline": pipeline3},
            params={"action": "update_status", "company": "NewCorp", "stage": "rejected"},
        )
        result = jh.run_job_hunter(task)
        pipeline4 = result.memory_delta.get("job_pipeline", [])
        newcorp = next((a for a in pipeline4 if a["company"] == "NewCorp"), None)
        check(len(pipeline4) == 2 and newcorp is not None, "unknown company auto-creates the entry")
        check(newcorp and "never logged" in (newcorp.get("notes") or ""),
              "auto-created entry carries the honest provenance note")

        # -- missing company → clarification, no writes --
        task = _make_task("log an application", task_type="log_application",
                          params={"action": "log_application"})
        result = jh.run_job_hunter(task)
        check(result.status.value == "needs_clarification", "missing company asks for clarification")
        check(not result.memory_delta, "clarification performs no memory writes")
    finally:
        jh._llm_parse = original_parse


# ---------------------------------------------------------------------------
# 5. Pipeline review: stale detection + funnel
# ---------------------------------------------------------------------------

def test_pipeline_review() -> None:
    print("\n=== 5. Pipeline review (stale detection + funnel) ===")
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=20)).isoformat()
    fresh = (now - timedelta(days=2)).isoformat()
    pipeline = [
        {"company": "Stripe", "role_title": "AI Engineer", "stage": "applied",
         "applied_date": old[:10], "last_updated": old},
        {"company": "Rippling", "role_title": "ML Engineer", "stage": "interviewing",
         "applied_date": fresh[:10], "last_updated": fresh},
        {"company": "Meta", "role_title": "DS", "stage": "rejected",
         "applied_date": old[:10], "last_updated": old},
    ]
    task = _make_task("how is my job hunt going", profile={"job_pipeline": pipeline})
    result = jh.run_job_hunter(task)
    out = result.output

    check("2 active" in out and "1 closed" in out, "funnel counts active vs closed")
    check("Stripe" in out and "silent 20d" in out, "stale application flagged with silence duration")
    check("Rippling" in out and "interviewing" in out, "hot application surfaced")
    check("Meta — applied" not in out, "terminal-stage application never flagged as stale")
    check(not result.memory_delta, "review performs no memory writes")


# ---------------------------------------------------------------------------
# 6. Orchestrator dispatch routing → job_hunter
# ---------------------------------------------------------------------------

def test_dispatch_routing() -> None:
    print("\n=== 6. Dispatch routing (keyword fallback) ===")
    from orchestrator.orchestrator import dispatch_node
    from orchestrator.state import build_initial_state

    cases = [
        ("tailor my resume for this JD", "job_hunter"),
        ("I applied to Stripe yesterday", "job_hunter"),
        ("how is my job hunt going", "job_hunter"),
        # Daily_Coach was absorbed as an orchestrator skill — the keyword
        # fallback for planning/logging now lands on `fallback` (which carries
        # the harness tools), not on a removed sub-agent.
        ("plan my day", "fallback"),
        ("write a linkedin post about agents", "linkedin_writer"),
    ]
    for text, expected in cases:
        state = build_initial_state()
        state["working_memory"]["user_input"] = text
        state["reasoning_decision"] = {"action": "route"}
        out = dispatch_node(state)
        agent = out["working_memory"]["extra"]["agent_name"]
        check(agent == expected, f"'{text}' → {agent}")


# ---------------------------------------------------------------------------
# 6b. Continuity guardrail (reasoner-level, code-enforced)
# ---------------------------------------------------------------------------

def test_continuity_guardrail() -> None:
    print("\n=== 6b. Continuity guardrail (offline, no LLM) ===")
    from orchestrator.nodes.reasoner import (
        ReasoningDecision,
        apply_job_status_guardrail,
    )

    tracked = ["KuKu FM", "TestCorp"]

    # A chat-routed status update about a tracked company → forced to job_hunter
    chatty = ReasoningDecision(action="direct_response", reasoning="chat", disclosure_type="context_sharing")
    out = apply_job_status_guardrail(chatty, "TestCorp moved me to interviewing next week", tracked)
    check(out.action == "route" and out.agent_name == "job_hunter",
          "tracked company + stage signal forces route → job_hunter")
    check(out.task_type == "update_application_status", "task type set to update_application_status")
    check(out.agent_pipeline == ["job_hunter"], "agent_pipeline backfilled")

    # No stage signal → untouched
    out = apply_job_status_guardrail(chatty, "TestCorp is a company I admire", tracked)
    check(out.action == "direct_response", "no stage signal → decision untouched")

    # Untracked company → untouched (conservative: no false routes)
    out = apply_job_status_guardrail(chatty, "Google moved me to interviewing", tracked)
    check(out.action == "direct_response", "untracked company → decision untouched")

    # Already-routed decisions are never overridden
    routed = ReasoningDecision(action="route", reasoning="r", agent_name="goal_decomposer")
    out = apply_job_status_guardrail(routed, "TestCorp moved me to interviewing", tracked)
    check(out.agent_name == "goal_decomposer", "explicit LLM route is never overridden")


# ---------------------------------------------------------------------------
# 7. Board CRUD API (FastAPI TestClient against the real DB, with cleanup)
# ---------------------------------------------------------------------------

def test_board_crud_api() -> None:
    print("\n=== 7. Board CRUD API (TestClient, throwaway entry, cleaned up) ===")
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from api.main import app as api_app

    client = TestClient(api_app)
    company = "__BoardTest__"
    today = datetime.now(timezone.utc).date().isoformat()

    def _cleanup() -> None:
        try:
            import shutil

            from sqlalchemy import text

            from agents.Job_Hunter.render import application_folder_path
            from orchestrator.config import OBSIDIAN_CAREER_FOLDER, OBSIDIAN_VAULT_PATH
            from orchestrator.memory.store import MemoryManager

            mm = MemoryManager()
            pipeline = mm.get_profile_fact("career", "job_pipeline") or []
            mm.set_profile_fact(
                "career", "job_pipeline",
                [a for a in pipeline if (a.get("company") or "") != company],
                source="test_cleanup",
            )
            with mm._session() as s:
                s.execute(text(
                    "DELETE FROM episodic_events WHERE event_type='job_application' "
                    "AND payload->>'company' = :c"
                ), {"c": company})
            # remove the throwaway vault folder (JD.md written by create/attach)
            shutil.rmtree(
                application_folder_path(OBSIDIAN_VAULT_PATH, OBSIDIAN_CAREER_FOLDER, company, "Data Scientist"),
                ignore_errors=True,
            )
        except Exception as exc:
            print(f"  ⚠️ cleanup warning: {exc}")

    _cleanup()  # pre-clean in case a previous run left residue
    try:
        # -- create --
        res = client.post("/api/applications", json={
            "company": company, "role_title": "Data Scientist", "stage": "applied",
        })
        check(res.status_code == 200 and res.json()["status"] == "created", "create → 200 'created'")
        check(res.json()["application"]["stage"] == "applied", "created entry carries the stage")

        # -- dedup refresh (default stage must NOT downgrade a later stage) --
        client.post("/api/applications/stage", json={"company": company, "stage": "screening"})
        res = client.post("/api/applications", json={
            "company": company, "role_title": "Data Scientist", "stage": "applied",
            "location": "Hyderabad",
        })
        body = res.json()
        check(body["status"] == "refreshed", "re-adding an existing entry refreshes (no duplicate)")
        check(body["application"]["stage"] == "screening", "default stage never downgrades screening → applied")
        check(body["application"]["location"] == "Hyderabad", "refresh merges new fields (location)")

        # -- validation --
        res = client.post("/api/applications", json={"company": company, "role_title": "DS", "stage": "space"})
        check(res.status_code == 400, "invalid stage → 400")
        res = client.post("/api/applications", json={"company": company})
        check(res.status_code == 422, "missing role_title → 422")

        # -- quick note --
        res = client.post("/api/applications/note", json={"company": company, "note": "recruiter said OA coming"})
        noted = res.json()["application"]
        check(res.status_code == 200 and "recruiter said OA coming" in (noted.get("notes") or ""),
              "note appended to the entry")
        check(f"[{today}]" in (noted.get("notes") or ""), "note carries a timestamp")
        res = client.post("/api/applications/note", json={"company": "__Nobody__", "note": "hi"})
        check(res.status_code == 404, "note on untracked company → 404")

        # -- stage endpoint validation --
        res = client.post("/api/applications/stage", json={"company": company, "stage": "nonsense"})
        check(res.status_code == 400, "stage endpoint rejects garbage stage")
        res = client.post("/api/applications/stage", json={"company": "__Nobody__", "stage": "offer"})
        check(res.status_code == 404, "stage endpoint 404s unknown company")

        # -- board data + history shape --
        res = client.get("/api/applications")
        data = res.json()
        check(res.status_code == 200 and "stats" in data and "applications" in data, "board payload shape")
        entry = next((a for a in data["applications"] if a["company"] == company), None)
        check(entry is not None and entry["stage"] == "screening", "board reflects the current stage")
        res = client.get("/api/applications/history?limit=10")
        events = res.json().get("events", [])
        check(any(company in (e.get("content") or "") for e in events), "timeline captured the board activity")

        # -- delete --
        res = client.delete(f"/api/applications/{company}")
        check(res.status_code == 200 and res.json()["status"] == "deleted", "delete → 200")
        res = client.delete(f"/api/applications/{company}")
        check(res.status_code == 404, "second delete → 404 (idempotent-safe)")
        res = client.get("/api/applications")
        check(all(a["company"] != company for a in res.json()["applications"]), "entry gone from the board")
    finally:
        _cleanup()


# ---------------------------------------------------------------------------
# 8. LIVE end-to-end tailor (env-gated)
# ---------------------------------------------------------------------------

_SAMPLE_JD = """AI Engineer — Agentic Systems (Acme Corp, Remote)

We're looking for an AI engineer to build production agentic workflows.
Requirements: Python, LangGraph, RAG pipelines, FastAPI, PostgreSQL, Docker.
Experience with LLM fine-tuning (SFT/RLHF) and evaluation is a strong plus.
You will ship multi-agent systems used by real customers."""


def test_live_tailor() -> None:
    print("\n=== 8. LIVE end-to-end tailor (env-gated) ===")
    if not (os.getenv("NEBIUS_API_KEY") or os.getenv("OPENAI_API_KEY")):
        print("  ⏭️  no LLM API key set — skipping live tailor test")
        return

    with tempfile.TemporaryDirectory() as tmp:
        save_master_resume(_sample_master(), tmp, "Career")

        original_vault = jh.OBSIDIAN_VAULT_PATH
        jh.OBSIDIAN_VAULT_PATH = tmp  # redirect the agent at the temp vault
        try:
            task = _make_task(
                f"Tailor my resume for this job:\n\n{_SAMPLE_JD}",
                task_type="tailor_resume",
            )
            result = jh.run_job_hunter(task)
        finally:
            jh.OBSIDIAN_VAULT_PATH = original_vault

        print(f"  status: {result.status.value} | pdf: {result.draft_suggestions[0].metadata.get('pdf_status') if result.draft_suggestions else '—'}")
        check(result.status.value in ("success", "partial"), f"tailor completes (got {result.status.value})")
        check(bool(result.draft_suggestions), "a DraftSuggestion is produced (never auto-anything)")

        app_dirs = list((Path(tmp) / "Career" / "Applications").glob("*"))
        check(len(app_dirs) == 1, f"one application folder created (got {len(app_dirs)})")
        if app_dirs:
            folder = app_dirs[0]
            check((folder / "JD.md").is_file(), "JD.md written")
            check((folder / "resume.yaml").is_file(), "resume.yaml overlay written")
            check((folder / "Tailored Resume.tex").is_file(), "Tailored Resume.tex written")
            check((folder / "Tailored Resume.md").is_file(), "Tailored Resume.md written")
            check((folder / "Fit Notes.md").is_file(), "Fit Notes.md written")

            md = (folder / "Tailored Resume.md").read_text(encoding="utf-8")
            check("Nikhil Sharma" in md and "Turing" in md, "tailored resume carries master content")

            if find_compiler():
                check((folder / "Tailored Resume.pdf").is_file(), "PDF compiled (tectonic found)")
            else:
                print("  ℹ️  no LaTeX compiler on PATH — PDF step correctly degraded to .tex/.md")

        # Tailoring must never log an application (deliberate side effects rule)
        check(not result.memory_delta.get("applications"), "tailoring performs NO application logging")
        if result.draft_suggestions:
            meta = result.draft_suggestions[0].metadata
            check(meta.get("ats_coverage_pct") is not None, "ATS coverage report attached to the draft")


def main() -> None:
    test_metric_guard()
    test_template_render()
    test_optional_trim()
    test_application_logger()
    test_pipeline_review()
    test_dispatch_routing()
    test_continuity_guardrail()
    test_board_crud_api()
    test_live_tailor()

    print("\n" + "=" * 60)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED — job_hunter works end to end.")


if __name__ == "__main__":
    main()
