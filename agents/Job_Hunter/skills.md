# Job Hunter Agent — Skills Blueprint (`agents/Job_Hunter/skills.md`)

This document defines the specialized domain skills, tool capabilities, implementation strategies, and guardrails for the **Job Hunter Agent** — Nikhil's job-application specialist: JD-tailored resume drafting + application pipeline tracking.

---

## 1. Core Skills & Capabilities

### 📄 JD-Tailored Resume Drafting (`tailor_resume`)
- **Skill:** Adapts the master resume to one specific job description — selecting/reordering/rephrasing bullets by ID, never free-writing.
- **Target Outcome:** A per-application folder in the vault with `JD.md`, `resume.yaml` (auditable overlay), `Tailored Resume.tex/.pdf/.md`, and `Fit Notes.md` (ATS coverage + honest gaps + change log).

### 🧱 Three-Layer Resume Architecture
- **Layer 1 (source of truth):** `Career/master_resume.yaml` — every bullet has a stable ID, tags, verbatim metrics, and an `optional` flag for one-page trims. Human-editable in Obsidian.
- **Layer 2 (LLM judgment):** `TailoredResumeDraft` structured output — bullets reference master IDs; the LLM never touches LaTeX.
- **Layer 3 (code-owned typesetting):** fixed Jinja2 LaTeX template + tectonic compile + pypdf one-page check.

### 📥 Application Tracking (`log_application` / `update_application_status`)
- **Skill:** Captures applications and stage transitions (`wishlist → applied → … → offer/rejected/withdrawn`) with fuzzy dedup by company+role.
- **Target Outcome:** current state in `profile_facts.job_pipeline`; append-only timeline as `job_application` episodic events.

### 📊 Pipeline Review (`job_search_review`)
- **Skill:** On-demand honest funnel report — stage counts, response rate, hot applications, stale ones (≥14 days silent → follow-up candidates). Nothing cached.

---

## 2. How to Achieve It (Architecture & Tools)

### A. Metric-Integrity Guard (code, `render.py`)
Every number in a tailored bullet must appear verbatim in its source bullet; unknown bullet/entry IDs and non-master skills are violations. One LLM revision pass on violation, then unresolved issues are flagged in Fit Notes — never silently approved.

### B. ATS Keyword Coverage (code)
Word-boundary matching of JD `required_keywords` against the rendered resume → coverage % + missing-keyword list in Fit Notes.

### C. One-Page Enforcement (code)
`tectonic` compile → pypdf page count → if >1 page: drop `optional` bullets → re-render → recompile. No compiler on PATH → degrade to `.tex`/`.md` artifacts with `partial` status.

### D. JD Sourcing
User-pasted JD preferred; otherwise web search (`integrations/search.py`) with an explicit `jd_assumed` flag surfaced in output (never silently assumed).

---

## 3. What to be Careful About (Guardrails & Watchouts)

> [!WARNING]
> **Never fabricate:** no invented employers, titles, dates, skills, or metrics. The Turing reframe ("LLM pipeline engineering, not data labeling") is the validated boundary — don't extend it without Nikhil's confirmation.

> [!CAUTION]
> **Tailoring ≠ logging.** Drafting a tailored resume NEVER creates/updates an application entry. Applications are logged only on explicit user reports.

> [!IMPORTANT]
> **Draft-and-confirm only.** Nothing is ever auto-sent to a recruiter/portal. Fit analysis stays honest: a truthful 70% match beats a fabricated 95%.

---

## 4. Implementation Roadmap

- [x] **Phase 1:** Three-layer tailor pipeline (YAML → structured draft → fixed template + tectonic) with metric guard + ATS coverage.
- [x] **Phase 2:** Application logging/stage updates (profile current-state + episodic timeline) + pipeline review with stale detection.
- [ ] **Phase 3:** Outreach drafting (referral/recruiter messages) sharing the honesty critic.
- [ ] **Phase 4:** Interview-prep chaining — `job_hunter` → `goal_decomposer` pipeline via the reasoner's `agent_pipeline`.
- [ ] **Phase 5:** `/api/applications` + kanban panel in the web UI; scheduler stale-application nudges.
