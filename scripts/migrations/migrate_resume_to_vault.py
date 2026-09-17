"""
scripts/migrations/migrate_resume_to_vault.py

One-time migration: parse the master resume (Resume_1.pdf, or a .tex source
for higher fidelity) into the structured YAML source of truth the job_hunter
agent tailors from:

    <vault>/Career/master_resume.yaml   ← the source of truth (edit this)
    <vault>/Career/Master Resume.md     ← readable mirror (auto-generated)

The structuring is LLM-assisted but the output is Pydantic-validated before
anything is written, and --dry-run lets you inspect first.

Usage:
    uv run python scripts/migrations/migrate_resume_to_vault.py --dry-run     # preview
    uv run python scripts/migrations/migrate_resume_to_vault.py               # write
    uv run python scripts/migrations/migrate_resume_to_vault.py --source my_resume.tex
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure root workspace directory is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()

from agents.Job_Hunter.render import save_master_resume
from agents.Job_Hunter.state import MasterResume
from orchestrator.config import OBSIDIAN_CAREER_FOLDER, OBSIDIAN_VAULT_PATH

MAX_SOURCE_CHARS = 12000

_PARSE_PROMPT = """You are parsing a resume into structured YAML data for a resume-tailoring system.

Rules:
1. Preserve EVERY number/metric VERBATIM (500+, ~30%, 10,000+, <100ms, ~60%). This is checked in code — never round, drop, or invent numbers.
1b. `name` is the person's full name exactly as printed at the top of the resume (e.g. "Nikhil Sharma") — never a filename or a slug.
2. Stable snake_case IDs: experiences "exp_<company>" (e.g. "exp_turing"), projects "proj_<name>", bullets "<entry>_b<N>" (turing_b1 — no "exp_" prefix on bullets, keep them short).
3. Tag each bullet with 2-4 topical tags (e.g. ["llm", "sft", "data-quality"]) and list its metrics verbatim in `metrics`.
4. Mark `optional: true` on the 1-2 weakest bullets per entry (droppable for one-page trims) — strongest bullets stay required.
5. Keep bullet text as plain text (no LaTeX, no markdown bold). Fix extraction artifacts (broken words, stray spaces) faithfully.
6. contact: extract email/phone/linkedin/github/website as plain strings/URLs.
7. education: include institution, degree, period, and detail lines (GPA, coursework) verbatim.
8. achievements: extract any awards, honors, or achievements as a plain text list."""


def _extract_source_text(source: Path) -> str:
    """Read the resume source: PDF via pypdf, anything else as plain text."""
    if source.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(source))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return source.read_text(encoding="utf-8")


def parse_resume(source_text: str) -> MasterResume:
    """LLM-structure the resume text, then Pydantic-validate. Raises on failure."""
    from orchestrator.llm import get_writer_llm

    structured = get_writer_llm(temperature=0.1).with_structured_output(MasterResume)
    raw = structured.invoke([
        ("system", _PARSE_PROMPT),
        ("user", f"RESUME TEXT:\n{source_text[:MAX_SOURCE_CHARS]}"),
    ])
    master = raw if isinstance(raw, MasterResume) else MasterResume(**raw)
    return master


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Resume_1.pdf → Career/master_resume.yaml")
    parser.add_argument("--source", nargs="+", default=[str(ROOT_DIR / "Resume_1.pdf")], help="Path to one or more .pdf or plain text files")
    parser.add_argument("--vault", default=OBSIDIAN_VAULT_PATH, help="Obsidian vault path")
    parser.add_argument("--folder", default=OBSIDIAN_CAREER_FOLDER, help="Career folder inside the vault")
    parser.add_argument("--dry-run", action="store_true", help="Print the YAML without writing anything")
    args = parser.parse_args()

    source_texts = []
    for s_path in args.source:
        p = Path(s_path)
        if not p.exists():
            print(f"❌ Source not found: {p}")
            sys.exit(1)
        print(f"📄 Extracting text from {p} ...")
        source_texts.append(_extract_source_text(p))

    combined_text = "\n\n=== NEXT SOURCE ===\n\n".join(source_texts)
    print(f"   extracted {len(combined_text)} chars total — structuring with the writer LLM...")

    try:
        master = parse_resume(combined_text)
    except Exception as exc:
        print(f"❌ LLM structuring failed: {exc}")
        sys.exit(1)

    n_exp = len(master.experience)
    n_proj = len(master.projects)
    n_bullets = len(master.bullet_map())
    print(f"   ✓ {master.name}: {n_exp} experience(s), {n_proj} project(s), {n_bullets} bullet(s)")

    import yaml

    yaml_text = yaml.safe_dump(master.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100)

    if args.dry_run:
        print("\n--- DRY RUN (nothing written) ---\n")
        print(yaml_text)
        return

    yaml_path, md_path = save_master_resume(master, args.vault, args.folder)
    print(f"\n✅ Master resume written:\n   YAML:   {yaml_path}\n   Mirror: {md_path}")
    print("\nReview both files — the YAML is the source of truth the job_hunter tailors from.")
    print('Then try: "tailor my resume for <JD>" in chat.')


if __name__ == "__main__":
    main()
