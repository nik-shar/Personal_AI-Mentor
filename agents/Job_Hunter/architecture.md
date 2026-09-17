# Job Hunter — Architecture

> **Role:** the job-application specialist. Tailors the master resume to a
> specific JD (with code-enforced honesty), tracks the application pipeline
> over time, and reports on it honestly.

---

## Graph topology

```
[START]
   │
   ▼
input_parser              ← params > non-default task_type > LLM ParsedRequest > keyword heuristic
   │
   ├─ "tailor_resume" ──────────► jd_analyzer          (load master YAML, secure JD, LLM JDAnalysis)
   │                                 │ needs_clarification → pack_result
   │                                 ▼
   │                              resume_tailor        (writer LLM → TailoredResumeDraft)
   │                                 ▼
   │                              quality_critic ◄──┐  (PURE CODE: metric guard, ID/skill
   │                                 │              │   subset, ATS coverage — max 1 revision)
   │                        feedback │──────────────┘
   │                                 ▼
   │                              render_and_write     (view → .tex → tectonic → 1-page check
   │                                 │                   → .md mirror, JD.md, resume.yaml, Fit Notes)
   │                                 ▼
   ├─ "log_application" ───────► application_logger    (dedup, stage coercion, dual-key delta)
   ├─ "update_status" ─────────► application_logger
   ├─ "review" ────────────────► pipeline_analyzer     (funnel, response rate, stale list — no writes)
   │                                 │
   ▼                                 ▼
                          pack_result  →  [END]
```

## Memory contract

| Key | Store | Shape |
|---|---|---|
| `job_pipeline` | `profile_facts` (career) | FULL current list of application dicts, written wholesale |
| `applications` | `episodic_events` (`job_application`) | one event per log/transition — the append-only timeline |
| `application_history` (read) | episodic, via context schema alias | last 20 `job_application` events |

Tailoring writes `job_pipeline` ONLY to link `jd_path`/`tailored_resume_path`
onto an already-tracked application — it never creates entries.

## The three resume layers

1. **`Career/master_resume.yaml`** — human-editable source of truth
   (seeded by `scripts/migrate_resume_to_vault.py` from `Resume_1.pdf`).
2. **`TailoredResumeDraft`** — the only LLM artifact: bullet selections by ID,
   light rephrasing; every number must survive verbatim (code-checked).
3. **`resume_template.tex` + `render.py`** — fixed template, Jinja2 with
   `[[ ]]`/`[% %]` delimiters, deep LaTeX-escaping, tectonic compile,
   pypdf one-page enforcement.

## Files

| File | Purpose |
|---|---|
| `job_hunter.py` | Graph, branch nodes, LLM prompts, `run_job_hunter()` |
| `state.py` | `JobHunterState` + `MasterResume`/`TailoredResumeDraft`/`ParsedRequest`/`JDAnalysis` models |
| `render.py` | View builder, metric guard, keyword coverage, escaping, template render, tectonic, vault IO |
| `resume_template.tex` | The fixed LaTeX template (never edited by the LLM) |
| `skills.md` | Domain skills blueprint + phase-2 seams |

## Verification

`uv run python scripts/test_job_hunter.py` — 40+ checks: guard/escape/trim
units, offline logger/review flows, dispatch routing, and a live end-to-end
tailor (auto-skips without API keys; PDF asserted only when tectonic exists).
