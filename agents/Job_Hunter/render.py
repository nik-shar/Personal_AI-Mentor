"""
agents/Job_Hunter/render.py

Layer 3 of the resume pipeline — everything here is code, no LLM judgment:

  build_resume_view(master, tailored)  → plain-text renderable view
  metric_violations(tailored, master)  → the honesty guard (exact number match)
  keyword_coverage(required, text)     → ATS coverage report
  render_tex(view)                     → fixed Jinja2 LaTeX template → .tex source
  render_markdown(view)                → Obsidian/chat-readable mirror
  compile_pdf(tex_path)                → tectonic wrapper (fail-open)
  drop_optional_bullets(view)          → deterministic one-page trim

Vault IO:
  load_master_resume / save_master_resume  (Career/master_resume.yaml + .md mirror)
  application_dir                          (Career/Applications/<Company> — <Role>/)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Optional

import yaml
from jinja2 import Environment, FileSystemLoader

from agents.Job_Hunter.state import MasterResume, TailoredResumeDraft

_TEMPLATE_DIR = Path(__file__).resolve().parent
_TEMPLATE_NAME = "resume_template.tex"

# ---------------------------------------------------------------------------
# LaTeX escaping — every injected string passes through here (template stays clean)
# ---------------------------------------------------------------------------

_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "<": r"\textless{}",
    ">": r"\textgreater{}",
}
_LATEX_SPECIALS_RE = re.compile("|".join(re.escape(k) for k in _LATEX_SPECIALS))


def latex_escape(text: Any) -> str:
    """Escape LaTeX special characters in an arbitrary string (NFKC-normalised first)."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    return _LATEX_SPECIALS_RE.sub(lambda m: _LATEX_SPECIALS[m.group(0)], s)


def _escape_view(value: Any) -> Any:
    """Deep-escape every string in a nested dict/list structure."""
    if isinstance(value, str):
        return latex_escape(value)
    if isinstance(value, dict):
        return {k: _escape_view(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_escape_view(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# The honesty guard — every number in a tailored bullet must exist in its source
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    """Numeric tokens in text, comma-normalised ('10,000' → '10000')."""
    return set(_NUMBER_RE.findall((text or "").replace(",", "")))


def metric_violations(
    tailored: TailoredResumeDraft,
    master: MasterResume,
) -> list[str]:
    """
    Return one string per honesty violation:
      - tailored bullet referencing an unknown master bullet id
      - a number in the tailored text that does not appear in the source bullet
      - a skill label/item that does not exist in the master resume
    Empty list = clean. This is the programmatic anti-fabrication guard —
    it overrides any LLM confidence (mentor_agent_guidelines.md §2.6).
    """
    violations: list[str] = []
    bullet_map = master.bullet_map()
    entry_map = master.entry_map()

    for section in (*tailored.experience, *tailored.projects):
        if section.source_id not in entry_map:
            violations.append(f"unknown entry id '{section.source_id}'")
        for tb in section.bullets:
            src = bullet_map.get(tb.source_id)
            if src is None:
                violations.append(f"unknown bullet id '{tb.source_id}'")
                continue
            src_numbers = _numbers(src.text)
            for n in _numbers(tb.text) - src_numbers:
                violations.append(
                    f"bullet '{tb.source_id}': metric '{n}' not present in source bullet"
                )

    master_labels = master.skill_labels()
    for cat in tailored.skills:
        if cat.label not in master_labels:
            violations.append(f"unknown skill category '{cat.label}'")
            continue
        master_items = next(c.items for c in master.skills if c.label == cat.label)
        master_items_lower = {i.lower() for i in master_items}
        for item in cat.items:
            if item.lower() not in master_items_lower:
                violations.append(f"skill '{item}' not present in master category '{cat.label}'")

    return violations


# ---------------------------------------------------------------------------
# ATS keyword coverage — deterministic, word-boundary aware
# ---------------------------------------------------------------------------


def _keyword_present(keyword: str, text_lower: str) -> bool:
    k = (keyword or "").strip().lower()
    if not k:
        return False
    # Word-boundary-ish match that still allows C++ / C# / .NET style tokens.
    pattern = rf"(?<![\w+#.]){re.escape(k)}(?![\w+#])"
    return re.search(pattern, text_lower) is not None


def keyword_coverage(required: list[str], text: str) -> dict[str, Any]:
    """How many JD required keywords actually appear in the tailored resume."""
    text_lower = (text or "").lower()
    present = [k for k in required if _keyword_present(k, text_lower)]
    missing = [k for k in required if not _keyword_present(k, text_lower)]
    pct = round(100 * len(present) / max(1, len(required)))
    return {"present": present, "missing": missing, "coverage_pct": pct}


# ---------------------------------------------------------------------------
# View building — merge master structure + tailored selection into a renderable dict
# ---------------------------------------------------------------------------


def _resolve_entry(t_entry, entry_map, bullet_map) -> Optional[dict[str, Any]]:
    """Build one renderable experience/project entry from a tailored selection.

    The draft's selection is authoritative: unmentioned master bullets stay
    dropped (that's what tailoring means — the master YAML is untouched, and
    changes_summary documents the drops). Empty-section guard: an entry with
    zero selected bullets falls back to its full master bullet list.
    """
    src = entry_map.get(t_entry.source_id)
    if src is None:
        return None
    bullets: list[str] = []
    for tb in t_entry.bullets:
        if tb.source_id in bullet_map and tb.text.strip():
            bullets.append(tb.text.strip())
    if not bullets:
        bullets = [b.text for b in src.bullets]
    view: dict[str, Any] = {"_source_id": src.id, "bullets": bullets}
    if hasattr(src, "role"):  # Experience
        view.update({
            "role": src.role,
            "company": src.company,
            "location": src.location,
            "period": src.period,
        })
    else:  # Project
        view.update({
            "name": src.name,
            "tech": list(src.tech),
            "link": src.link,
            "link_label": "link",
        })
    return view


def _verbatim_entry(entry) -> dict[str, Any]:
    """Renderable entry copied straight from the master (untailored fallback)."""
    view: dict[str, Any] = {"_source_id": entry.id, "bullets": [b.text for b in entry.bullets]}
    if hasattr(entry, "role"):
        view.update({
            "role": entry.role,
            "company": entry.company,
            "location": entry.location,
            "period": entry.period,
        })
    else:
        view.update({
            "name": entry.name,
            "tech": list(entry.tech),
            "link": entry.link,
            "link_label": "link",
        })
    return view


def build_resume_view(
    master: MasterResume,
    tailored: TailoredResumeDraft,
) -> dict[str, Any]:
    """
    Merge the master's structure with the tailored selection/rephrasing into
    the plain-text view consumed by render_tex / render_markdown.

    Tailored entries come first in the draft's relevance order; master entries
    the draft never mentioned are appended verbatim (nothing silently dropped).
    Unknown ids are skipped defensively (the critic reports them separately).
    """
    bullet_map = master.bullet_map()
    entry_map = master.entry_map()

    # Bucket by the source entry's REAL type, not by which list the LLM used —
    # models occasionally file a project under "experience" and vice versa.
    # Unreferenced entries stay dropped (the draft's selection is authoritative);
    # total-omission of a whole section falls back to the master verbatim.
    experience: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    seen_entry_ids: set[str] = set()
    for t_entry in (*tailored.experience, *tailored.projects):
        v = _resolve_entry(t_entry, entry_map, bullet_map)
        if not v or v["_source_id"] in seen_entry_ids:
            continue
        seen_entry_ids.add(v["_source_id"])
        if "role" in v:
            experience.append(v)
        else:
            projects.append(v)

    if not experience and not tailored.experience:
        # Draft selected no experience entries at all → total-failure fallback.
        experience = [_verbatim_entry(exp) for exp in master.experience]
    if not projects and not tailored.projects and not tailored.experience:
        # Draft selected nothing at all → full master projects verbatim too.
        projects = [_verbatim_entry(proj) for proj in master.projects]

    skills = (
        [{"label": c.label, "items": list(c.items)} for c in tailored.skills]
        if tailored.skills
        else [{"label": c.label, "items": list(c.items)} for c in master.skills]
    )

    contact_bits = [
        master.contact.email,
        master.contact.phone,
        master.contact.linkedin,
        master.contact.github,
        master.contact.website,
    ]

    return {
        "name": master.name,
        "headline": tailored.summary_line or master.headline,
        "contact_line": " | ".join(b for b in contact_bits if b),
        "summary": tailored.summary_line or master.summary,
        "skills": skills,
        "experience": experience,
        "projects": projects,
        "education": [
            {
                "institution": e.institution,
                "degree": e.degree,
                "period": e.period,
                "details": list(e.details),
            }
            for e in master.education
        ],
        "achievements": list(master.achievements),
    }


def drop_optional_bullets(view: dict[str, Any], master: MasterResume) -> dict[str, Any]:
    """
    Deterministic one-page trim: remove bullets whose master source is tagged
    optional=true (matched by exact source text). Returns a NEW view.
    """
    optional_texts = {b.text.strip() for b in master.bullet_map().values() if b.optional}
    if not optional_texts:
        return {**view}

    def _trim(section: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**entry, "bullets": [b for b in entry["bullets"] if b.strip() not in optional_texts]}
            for entry in section
        ]

    new_view = {**view}
    new_view["experience"] = _trim(view.get("experience", []))
    new_view["projects"] = _trim(view.get("projects", []))
    return new_view


# ---------------------------------------------------------------------------
# Rendering — fixed template + escaped content
# ---------------------------------------------------------------------------


def render_tex(view: dict[str, Any]) -> str:
    """Render the fixed LaTeX template with the (deep-escaped) resume view."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        variable_start_string="[[",
        variable_end_string="]]",
        block_start_string="[%",
        block_end_string="%]",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,  # LaTeX, not HTML — escaping handled by _escape_view
    )
    template = env.get_template(_TEMPLATE_NAME)
    return template.render(**_escape_view(view))


def render_markdown(view: dict[str, Any]) -> str:
    """Obsidian/chat-readable mirror of the tailored resume."""
    lines = [f"# {view['name']} — Tailored Resume", ""]
    if view.get("headline"):
        lines += [f"**{view['headline']}**", ""]
    if view.get("contact_line"):
        lines += [view["contact_line"], ""]

    if view.get("summary"):
        lines += ["## Summary", view["summary"], ""]

    if view.get("skills"):
        lines.append("## Technical Skills")
        for cat in view["skills"]:
            lines.append(f"- **{cat['label']}:** {', '.join(cat['items'])}")
        lines.append("")

    if view.get("experience"):
        lines.append("## Experience")
        for exp in view["experience"]:
            header = f"### {exp['role']} — {exp['company']}"
            if exp.get("period"):
                header += f"  ({exp['period']})"
            lines.append(header)
            lines += [f"- {b}" for b in exp["bullets"]]
            lines.append("")

    if view.get("projects"):
        lines.append("## Projects")
        for p in view["projects"]:
            header = f"### {p['name']}"
            if p.get("tech"):
                header += f"  |  {', '.join(p['tech'])}"
            lines.append(header)
            lines += [f"- {b}" for b in p["bullets"]]
            lines.append("")

    if view.get("education"):
        lines.append("## Education")
        for e in view["education"]:
            lines.append(f"- **{e['institution']}** — {e['degree']}  ({e.get('period', '')})")
            lines += [f"  - {d}" for d in e.get("details", [])]
        lines.append("")

    if view.get("achievements"):
        lines.append("## Achievements and Awards")
        for a in view["achievements"]:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def view_plain_text(view: dict[str, Any]) -> str:
    """Flat text of the whole resume — used for ATS keyword coverage."""
    return render_markdown(view)


# ---------------------------------------------------------------------------
# PDF compilation — tectonic wrapper, fail-open everywhere
# ---------------------------------------------------------------------------


def find_compiler() -> Optional[str]:
    """Path to a usable LaTeX compiler, or None (tectonic preferred)."""
    return shutil.which("tectonic") or shutil.which("pdflatex")


def compile_pdf(tex_path: Path, out_dir: Optional[Path] = None) -> tuple[bool, str, Optional[Path]]:
    """
    Compile a .tex file to PDF. Returns (ok, log_tail, pdf_path).
    Never raises — a missing compiler or failed build is a soft failure the
    agent reports as 'partial' with the .tex/.md artifacts still written.
    """
    compiler = find_compiler()
    if not compiler:
        return False, "no LaTeX compiler found (install: tectonic)", None

    tex_path = Path(tex_path)
    out_dir = out_dir or tex_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if Path(compiler).name == "tectonic":
        cmd = [compiler, "--outdir", str(out_dir), str(tex_path)]
    else:  # pdflatex
        cmd = [compiler, "-interaction=nonstopmode", "-halt-on-error",
               "-output-directory", str(out_dir), str(tex_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        log_tail = (proc.stdout + "\n" + proc.stderr)[-2000:]
    except Exception as exc:
        return False, f"compiler invocation failed: {exc}", None

    pdf_path = out_dir / (tex_path.stem + ".pdf")
    if proc.returncode == 0 and pdf_path.exists():
        return True, log_tail, pdf_path
    return False, log_tail, None


def count_pdf_pages(pdf_path: Path) -> int:
    """Page count of a compiled PDF (0 on failure)."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Vault IO — master resume + per-application folders
# ---------------------------------------------------------------------------

MASTER_RESUME_YAML = "master_resume.yaml"
MASTER_RESUME_MD = "Master Resume.md"


def _sanitize_filename(name: str) -> str:
    """Filename-safe string (same illegal-character set as roadmap.sanitize_filename)."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    return cleaned.strip() or "application"


def career_dir(vault_path: str, career_folder: str) -> Path:
    """<vault>/<career_folder>/ — created on demand (fail-open)."""
    d = Path(vault_path) / career_folder
    d.mkdir(parents=True, exist_ok=True)
    return d


def master_resume_paths(vault_path: str, career_folder: str) -> tuple[Path, Path]:
    """(yaml_path, markdown_mirror_path) for the master resume."""
    d = career_dir(vault_path, career_folder)
    return d / MASTER_RESUME_YAML, d / MASTER_RESUME_MD


def application_folder_path(
    vault_path: str,
    career_folder: str,
    company: str,
    role_title: str,
) -> Path:
    """Read-only path resolution for an application folder (never creates dirs)."""
    folder_name = _sanitize_filename(f"{company} — {role_title}".strip(" —") or "application")
    return Path(vault_path) / career_folder / "Applications" / folder_name


def application_dir(
    vault_path: str,
    career_folder: str,
    company: str,
    role_title: str,
) -> Path:
    """
    <vault>/<career_folder>/Applications/<Company> — <Role>/ — one folder per
    application, mirroring the per-roadmap folder layout for topic graphs.
    Creates the folder on demand (write path).
    """
    d = application_folder_path(vault_path, career_folder, company, role_title)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_master_resume(vault_path: str, career_folder: str) -> Optional[MasterResume]:
    """Load + validate the master resume YAML. Returns None if missing/invalid."""
    yaml_path, _ = master_resume_paths(vault_path, career_folder)
    if not yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        return MasterResume.model_validate(data)
    except Exception as exc:
        print(f"[job_hunter] failed to load master resume {yaml_path}: {exc}")
        return None


def master_resume_markdown(master: MasterResume) -> str:
    """Readable Obsidian mirror of the master resume YAML."""
    lines = [
        "---",
        "type: master_resume",
        "tags:",
        "  - career",
        "  - resume",
        "---",
        "",
        f"# {master.name} — Master Resume",
        "",
        "> Source of truth: `master_resume.yaml` (same folder). Edit either;",
        "> the job_hunter reads the YAML. Regenerate this mirror with",
        "> `uv run python scripts/migrate_resume_to_vault.py`.",
        "",
    ]
    if master.headline:
        lines += [f"**{master.headline}**", ""]
    contact_bits = [
        master.contact.email,
        master.contact.phone,
        master.contact.linkedin,
        master.contact.github,
        master.contact.website,
    ]
    contact_line = " | ".join(b for b in contact_bits if b)
    if contact_line:
        lines += [contact_line, ""]
    if master.summary:
        lines += ["## Summary", master.summary, ""]

    if master.skills:
        lines.append("## Technical Skills")
        for cat in master.skills:
            lines.append(f"- **{cat.label}:** {', '.join(cat.items)}")
        lines.append("")

    for exp in master.experience:
        lines.append(f"## {exp.role} — {exp.company}  ({exp.period})  `[{exp.id}]`")
        for b in exp.bullets:
            flags = []
            if b.optional:
                flags.append("optional")
            if b.tags:
                flags.append("tags: " + ", ".join(b.tags))
            suffix = f"  *({' · '.join(flags)})*" if flags else ""
            lines.append(f"- `[{b.id}]` {b.text}{suffix}")
        lines.append("")

    if master.projects:
        lines.append("## Projects")
        for p in master.projects:
            tech = f"  |  {', '.join(p.tech)}" if p.tech else ""
            lines.append(f"### {p.name}{tech}  `[{p.id}]`")
            for b in p.bullets:
                flags = "  *(optional)*" if b.optional else ""
                lines.append(f"- `[{b.id}]` {b.text}{flags}")
            lines.append("")

    if master.education:
        lines.append("## Education")
        for e in master.education:
            lines.append(f"- **{e.institution}** — {e.degree}  ({e.period})")
            lines += [f"  - {d}" for d in e.details]
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def save_master_resume(
    master: MasterResume,
    vault_path: str,
    career_folder: str,
) -> tuple[Path, Path]:
    """Write master_resume.yaml + the 'Master Resume.md' mirror. Returns both paths."""
    yaml_path, md_path = master_resume_paths(vault_path, career_folder)
    yaml_text = yaml.safe_dump(
        master.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    yaml_path.write_text(yaml_text, encoding="utf-8")
    md_path.write_text(master_resume_markdown(master), encoding="utf-8")
    return yaml_path, md_path
