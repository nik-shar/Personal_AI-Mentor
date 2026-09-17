"""
scripts/tools/migrate_toolkits_to_skills.py

Convert the Python `toolkits/` instruction-set overlays into PI Agent Skills.

    toolkits/<name>/{toolkit.md,philosophy.md,behaviors.md,guardrails.md}
        + toolkits/<name>/workflows/*.md
            ↓
    mentor/skills/<name>/SKILL.md
        + mentor/skills/<name>/references/workflows/<wf>.md

Why a converter instead of hand-copying 1150 lines: the toolkit prose IS the
asset and must survive intact. A converter that strips frontmatter and re-orders
sections is reviewable, re-runnable, and cannot silently drop a workflow.

Progressive disclosure (Agent Skills standard):
  - `SKILL.md` holds role + philosophy + behaviors + guardrails (the rules that
    apply while the skill is active) plus a workflow index.
  - `references/workflows/*.md` holds the step-by-step procedures, read by the
    model only when that workflow is actually invoked.

Run:

    uv run python scripts/tools/migrate_toolkits_to_skills.py

The generated files are build output: edit `toolkits/` and re-run, never edit
`mentor/skills/` by hand.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

TOOLKITS_DIR = ROOT_DIR / "toolkits"
SKILLS_DIR = ROOT_DIR / "mentor" / "skills"

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_DESCRIPTION_CHARS = 1024

GENERATED_NOTE = (
    "<!-- AUTO-GENERATED from toolkits/{name}/ by "
    "scripts/tools/migrate_toolkits_to_skills.py — edit the toolkit, not this file. -->"
)


def read_required(path: Path) -> str:
    """Read a file, failing loudly — this is a build script, not runtime."""
    if not path.is_file():
        raise SystemExit(f"[migrate_toolkits_to_skills] missing required file: {path}")
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter dict, body). Fails on bad YAML."""
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}, text
    end = stripped.find("\n---", 3)
    if end == -1:
        raise SystemExit("[migrate_toolkits_to_skills] unterminated frontmatter block")
    import yaml

    meta = yaml.safe_load(stripped[3:end]) or {}
    if not isinstance(meta, dict):
        raise SystemExit("[migrate_toolkits_to_skills] frontmatter is not a mapping")
    return meta, stripped[end + 4 :].lstrip("\n")


def collapse(text: str) -> str:
    """Collapse all whitespace runs to single spaces (for YAML-safe one-liners)."""
    return " ".join((text or "").split())


def strip_leading_heading(body: str) -> str:
    """
    Drop a body's own leading `# Heading` line.

    Each toolkit file repeats the section title as an H1 (e.g. philosophy.md
    starts with `# Teaching Philosophy`). Inlining those under our own `##`
    sections would produce a nested duplicate heading, which reads as noise in a
    file the model is meant to follow.
    """
    lines = (body or "").lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def manifest_descriptions() -> dict[str, str]:
    """
    Tool name -> description, straight from the sidecar's pydantic manifest.

    This is what keeps the generated skills honest. Availability prose used to be
    hand-written, and it went stale the moment Phase 3 added the write tools —
    the mentor then told Nik it had no calendar access while holding the tools to
    read and write it. Generating the list means a skill cannot claim a tool that
    does not exist, or deny one that does.
    """
    from api.tools import build_manifest

    return {tool.name: tool.description for tool in build_manifest().tools}


def validate_skill_tools() -> None:
    """Fail the build if a skill references a tool that does not exist."""
    known = set(manifest_descriptions()) | set(NATIVE_TOOLS)
    for skill, tools in SKILL_TOOLS.items():
        unknown = [tool for tool in tools if tool not in known]
        if unknown:
            raise SystemExit(
                f"[migrate_toolkits_to_skills] skill '{skill}' references unknown tool(s): "
                f"{unknown}. Known: {sorted(known)}"
            )


def render_tools_section(skill: str) -> str:
    """Render the 'Tools available right now' section from the live manifest."""
    descriptions = manifest_descriptions()
    declared = SKILL_TOOLS[skill]
    sidecar = [tool for tool in declared if tool not in NATIVE_TOOLS]
    native = [tool for tool in declared if tool in NATIVE_TOOLS]

    lines: list[str] = []
    if sidecar:
        lines.append("Python sidecar tools (Python owns this data):")
        for tool in sidecar:
            lines.append(f"- `{tool}` — {descriptions[tool]}")

    if native:
        if lines:
            lines.append("")
        lines.append("Native tools (run in TypeScript, no round-trip):")
        for tool in native:
            lines.append(f"- `{tool}` — {NATIVE_TOOLS[tool]}")

    builtins = SKILL_BUILTINS.get(skill) or []
    if builtins:
        if lines:
            lines.append("")
        lines.append("Built-in PI tools available here: " + ", ".join(f"`{t}`" for t in builtins))

    gap = (SKILL_GAPS.get(skill) or "").strip()
    if gap:
        lines.append("")
        lines.append(f"**Still missing:** {gap}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-skill metadata that must be written by a human
#
# The Agent Skills standard uses `description` to decide *when* to load a skill,
# so it has to name the trigger, not just the topic. And every workflow in these
# toolkits references tool names from the Python harness — some of which do not
# exist on the PI side yet. Claiming an action the mentor cannot take is worse
# than having no skill at all, so each skill declares exactly what is possible
# today.
#
# Both maps are checked for full coverage: a new toolkit without an entry fails
# this script loudly instead of silently generating a vague, misleading skill.
# ---------------------------------------------------------------------------

USE_WHEN: dict[str, str] = {
    "learning-companion": (
        "Use whenever Nik is learning, stuck on a concept, practicing, reading unfamiliar "
        "material, debugging, designing, or reviewing — whenever the goal is that he "
        "understands rather than that the answer appears. Also use when he explicitly asks to "
        "be taught, tutored, or quizzed."
    ),
    "code-explorer": (
        "Use whenever the conversation is about Nik's ACTUAL code — his repo, a file, a "
        "traceback, a failing test, a diff, or a feature he is building. Read the real files "
        "before reasoning about them."
    ),
    "calendar-manager": (
        "Use whenever the conversation touches a calendar, schedule, availability, time block, "
        "free window, or a life anchor (sleep, meals, commute, gym)."
    ),
    "tutorial-writer": (
        "Use whenever Nik asks for a written tutorial, deep note, walkthrough, or study "
        "material on a topic — anything meant to be read later rather than discussed now."
    ),
}

# Which real tools each skill leans on. Every name here is checked against the
# sidecar manifest (api/tools.py) at generation time, so a skill can never claim
# a tool that does not exist — and can never claim a tool is missing while it
# actually exists, which is exactly the bug this replaced.
SKILL_TOOLS: dict[str, list[str]] = {
    "learning-companion": [
        "recall_memories",
        "get_profile",
        "available_topic_nodes",
        "log_learning_session",
        "compute_learning_streak",
        "trim_plan_to_fit",
    ],
    "code-explorer": ["get_profile", "recall_memories"],
    "calendar-manager": [
        "get_day_grid",
        "find_available_slots",
        "place_time_block",
        "set_anchor",
        "get_momentum",
    ],
    "tutorial-writer": ["get_profile", "available_topic_nodes", "recall_memories"],
}

# Tools implemented natively in TypeScript (mentor/src/pure/planning.ts). They
# are not in the Python manifest, so they are declared here and labelled
# differently in the generated section.
NATIVE_TOOLS: dict[str, str] = {
    "trim_plan_to_fit": "drop the lowest-priority items until a plan fits the budget (runs locally, no round-trip)",
    "compute_learning_streak": "the streak arithmetic, computed locally rather than fetched",
}

# Built-in PI tools worth naming for a given skill.
SKILL_BUILTINS: dict[str, list[str]] = {
    "learning-companion": ["read"],
    "code-explorer": ["read", "grep", "find", "ls", "bash"],
    "calendar-manager": [],
    "tutorial-writer": ["read", "grep", "find"],
}

# Hand-written, and ONLY for capabilities that genuinely do not exist yet.
# Everything else about tool availability is generated from the manifest, so
# this is the one place that needs a human when a new tool lands.
SKILL_GAPS: dict[str, str] = {
    "learning-companion": (
        "**No learning-log reader yet.** The Python build injected a `RETRIEVAL PRACTICE "
        "MATERIAL` block; here you assemble the material yourself from `recall_memories` + "
        "`available_topic_nodes`. If those return nothing, say you don't have enough history "
        "to quiz him on — **never invent history.**"
    ),
    "code-explorer": "",
    "calendar-manager": "",
    "tutorial-writer": (
        "**No vault writer yet.** The Python build wrote notes into the Obsidian vault "
        "(`Learning/Topics/`). Until that returns, draft the tutorial in the conversation and "
        "say you cannot file it into the vault — never claim a file was written."
    ),
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_skill_md(
    name: str,
    meta: dict[str, Any],
    activate_body: str,
    philosophy: str,
    behaviors: str,
    guardrails: str,
    workflows: list[tuple[str, str]],
) -> str:
    """Render one SKILL.md from the toolkit's four always-on files + index."""
    title = collapse(str(meta.get("title") or name))
    role = str(meta.get("role") or "").strip()
    base_description = collapse(str(meta.get("description") or ""))
    use_when = collapse(USE_WHEN[name])
    description = f"{base_description} {use_when}"

    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SystemExit(
            f"[migrate_toolkits_to_skills] '{name}' description is {len(description)} chars "
            f"(max {MAX_DESCRIPTION_CHARS}) — shorten USE_WHEN"
        )

    index_rows = "\n".join(
        f"| `{wf}` | {collapse(desc)} | `references/workflows/{wf}.md` |"
        for wf, desc in workflows
    )

    return "\n".join(
        [
            "---",
            f"name: {name}",
            "description: >-",
            f"  {description}",
            "---",
            "",
            f"# {title}",
            "",
            GENERATED_NOTE.format(name=name),
            "",
            "## When this applies",
            "",
            strip_leading_heading(activate_body),
            "",
            "## Role",
            "",
            role,
            "",
            "## Philosophy",
            "",
            strip_leading_heading(philosophy),
            "",
            "## Behaviors",
            "",
            strip_leading_heading(behaviors),
            "",
            "## Guardrails",
            "",
            strip_leading_heading(guardrails),
            "",
            "## Tools available right now",
            "",
            render_tools_section(name),
            "",
            "## Workflows",
            "",
            "Load the matching workflow file before doing the work — its path is relative to",
            "this skill's directory. Do not improvise a procedure when a workflow exists.",
            "",
            "| Workflow | Use when | File |",
            "| --- | --- | --- |",
            index_rows,
            "",
        ]
    )


def build_skill(toolkit_dir: Path) -> tuple[str, int, int]:
    """Build one skill directory. Returns (name, workflow_count, skill_md_chars)."""
    name = toolkit_dir.name

    if not SKILL_NAME_RE.match(name):
        raise SystemExit(
            f"[migrate_toolkits_to_skills] '{name}' is not a valid Agent Skills name "
            "(lowercase letters, digits, single hyphens)"
        )
    if name not in USE_WHEN or name not in SKILL_TOOLS:
        raise SystemExit(
            f"[migrate_toolkits_to_skills] toolkit '{name}' has no USE_WHEN/SKILL_TOOLS entry. "
            "Add both before migrating — the description must name the trigger, and the skill "
            "must declare which of its tools it relies on."
        )

    meta, activate_body = parse_frontmatter(read_required(toolkit_dir / "toolkit.md"))
    _, philosophy = parse_frontmatter(read_required(toolkit_dir / "philosophy.md"))
    _, behaviors = parse_frontmatter(read_required(toolkit_dir / "behaviors.md"))
    _, guardrails = parse_frontmatter(read_required(toolkit_dir / "guardrails.md"))

    workflows: list[tuple[str, str]] = []
    for workflow_path in sorted((toolkit_dir / "workflows").glob("*.md")):
        wf_meta, wf_body = parse_frontmatter(read_required(workflow_path))
        workflows.append((workflow_path.stem, str(wf_meta.get("description") or "").strip()))

        target = SKILLS_DIR / name / "references" / "workflows" / workflow_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"<!-- AUTO-GENERATED from toolkits/{name}/workflows/{workflow_path.name} -->\n\n"
            f"{wf_body.strip()}\n",
            encoding="utf-8",
        )

    skill_md = render_skill_md(
        name, meta, activate_body, philosophy, behaviors, guardrails, workflows
    )
    target_dir = SKILLS_DIR / name
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    return name, len(workflows), len(skill_md)


def main() -> int:
    if not TOOLKITS_DIR.is_dir():
        raise SystemExit(f"[migrate_toolkits_to_skills] no toolkits dir at {TOOLKITS_DIR}")

    # Validates against the live sidecar manifest: a skill may not reference a
    # tool that does not exist. Run before anything is written.
    validate_skill_tools()

    toolkit_dirs = sorted(p for p in TOOLKITS_DIR.iterdir() if p.is_dir())
    if not toolkit_dirs:
        raise SystemExit("[migrate_toolkits_to_skills] no toolkits found")

    print(f"[migrate_toolkits_to_skills] {TOOLKITS_DIR} -> {SKILLS_DIR}")

    for toolkit_dir in toolkit_dirs:
        # Regenerate from scratch: stale workflow files from a renamed or deleted
        # workflow must not linger and stay loadable.
        skill_dir = SKILLS_DIR / toolkit_dir.name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)

        name, workflow_count, skill_chars = build_skill(toolkit_dir)
        print(f"  {name}: {workflow_count} workflows, SKILL.md {skill_chars} chars")

    print("[migrate_toolkits_to_skills] done — edit toolkits/, never mentor/skills/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())