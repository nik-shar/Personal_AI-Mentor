"""
scripts/seed_profile.py

Reads profile.yaml and upserts all fields into the PostgreSQL profile_facts table.

Usage:
    uv run python scripts/seed_profile.py               # uses profile.yaml in project root
    uv run python scripts/seed_profile.py my_profile.yaml

Safe to re-run after editing profile.yaml — it upserts, never duplicates.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Run:  uv add pyyaml")
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv(override=True)

from orchestrator.memory.store import MemoryManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(value) -> str | None:
    """Coerce a date string or None → ISO timestamp string."""
    if value is None:
        return None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        # Try to parse as date only → add midnight UTC
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue
        return value
    return None


def _safe_str(value, default=None) -> str | None:
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


# ---------------------------------------------------------------------------
# Section writers
# ---------------------------------------------------------------------------

def _write_identity(mm: MemoryManager, data: dict) -> None:
    if not data:
        return
    if name := _safe_str(data.get("full_name")):
        mm.set_profile_fact("identity", "full_name", name, source="profile.yaml")
    if loc := _safe_str(data.get("location")):
        mm.set_profile_fact("identity", "location", loc, source="profile.yaml")


def _write_education(mm: MemoryManager, data: dict) -> None:
    if not data:
        return
    degrees = data.get("degrees") or []
    if degrees:
        mm.set_profile_fact("education", "degrees", degrees, source="profile.yaml")


def _write_career(mm: MemoryManager, data: dict) -> None:
    if not data:
        return
    if status := _safe_str(data.get("employment_status")):
        mm.set_profile_fact("career", "employment_status", status, source="profile.yaml")
    if role := _safe_str(data.get("current_role")):
        mm.set_profile_fact("career", "current_role", role, source="profile.yaml")
    if roles := data.get("target_roles"):
        mm.set_profile_fact("career", "target_roles", list(roles), source="profile.yaml")
    if locs := data.get("target_locations"):
        mm.set_profile_fact("career", "target_locations", list(locs), source="profile.yaml")


def _write_goals(mm: MemoryManager, data: dict) -> None:
    if not data:
        return
    for key in ("long_term_goal", "short_term_goal", "ms_target_term"):
        if val := _safe_str(data.get(key)):
            mm.set_profile_fact("goals", key, val, source="profile.yaml")
    if schools := data.get("ms_target_schools"):
        mm.set_profile_fact("goals", "ms_target_schools", list(schools), source="profile.yaml")


def _write_skills(mm: MemoryManager, items: list) -> None:
    if not items:
        return
    skills = []
    for item in items:
        if not isinstance(item, dict):
            continue
        skill = {
            "name": _safe_str(item.get("name"), "Unknown"),
            "rating_out_of_10": float(item.get("rating_out_of_10", 5.0)),
            "notes": _safe_str(item.get("notes")),
        }
        if ts := _ts(item.get("last_self_assessed")):
            skill["last_self_assessed"] = ts
        skills.append(skill)
    if skills:
        mm.set_profile_fact("skills", "skills", skills, source="profile.yaml")


def _write_projects(mm: MemoryManager, items: list) -> None:
    if not items:
        return
    projects = []
    for item in items:
        if not isinstance(item, dict):
            continue
        proj = {
            "name": _safe_str(item.get("name"), "Unnamed Project"),
            "description": _safe_str(item.get("description"), ""),
            "status": _safe_str(item.get("status"), "planning"),
            "tech_stack": list(item.get("tech_stack") or []),
            "repo_url": _safe_str(item.get("repo_url")),
            "deployed_url": _safe_str(item.get("deployed_url")),
            "extra": item.get("extra") or {},
        }
        if ts := _ts(item.get("last_worked_on")):
            proj["last_worked_on"] = ts
        projects.append(proj)
    if projects:
        mm.set_profile_fact("career", "projects", projects, source="profile.yaml")


def _write_learning_path(mm: MemoryManager, data: dict) -> None:
    if not data:
        return
    entries = []
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        e = {
            "topic": _safe_str(entry.get("topic"), "Unknown"),
            "week": int(entry.get("week", 1)),
            "order": int(entry.get("order", 0)),
            "status": _safe_str(entry.get("status"), "not_started"),
            "source": _safe_str(entry.get("source")),
            "notes": _safe_str(entry.get("notes")),
        }
        if ts := _ts(entry.get("planned_date")):
            e["planned_date"] = ts
        if ts := _ts(entry.get("completed_date")):
            e["completed_date"] = ts
        entries.append(e)

    path = {
        "title": _safe_str(data.get("title"), "Learning Path"),
        "description": _safe_str(data.get("description")),
        "total_weeks": data.get("total_weeks"),
        "weekly_time_budget_hours": data.get("weekly_time_budget_hours"),
        "current_week": int(data.get("current_week", 1)),
        "entries": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    mm.set_profile_fact("learning", "active_learning_path", path, source="profile.yaml")


def _write_preferences(mm: MemoryManager, data: dict) -> None:
    if not data:
        return
    prefs = {
        "content_tone": _safe_str(data.get("content_tone")),
        "preferred_language": _safe_str(data.get("preferred_language"), "en"),
        "linkedin_posting_frequency": _safe_str(data.get("linkedin_posting_frequency")),
        "quiet_hours": _safe_str(data.get("quiet_hours")),
        "do_not_disturb_topics": list(data.get("do_not_disturb_topics") or []),
    }
    mm.set_profile_fact("system", "preferences", prefs, source="profile.yaml")

    # Also write linkedin_posting_frequency as a top-level fact (some agents read it directly)
    if freq := prefs.get("linkedin_posting_frequency"):
        mm.set_profile_fact("system", "linkedin_posting_frequency", freq, source="profile.yaml")


def _write_system(mm: MemoryManager, data: dict) -> None:
    if not data:
        return
    if energy := data.get("energy_level"):
        mm.set_profile_fact("system", "energy_level", int(energy), source="profile.yaml")
    if streak := data.get("learning_streak_days") is not None:
        mm.set_profile_fact("learning", "learning_streak_days", int(data.get("learning_streak_days", 0)), source="profile.yaml")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed(yaml_path: Path) -> None:
    print(f"Loading profile from: {yaml_path}")
    with open(yaml_path, "r") as f:
        profile = yaml.safe_load(f)

    if not isinstance(profile, dict):
        print("ERROR: profile.yaml must be a YAML mapping at the top level.")
        sys.exit(1)

    print("Connecting to database...")
    mm = MemoryManager()
    mm.ensure_schema()

    sections = {
        "identity":             (profile.get("identity") or {},      _write_identity),
        "education":            (profile.get("education") or {},     _write_education),
        "career":               (profile.get("career") or {},        _write_career),
        "goals":                (profile.get("goals") or {},         _write_goals),
        "skills":               (profile.get("skills") or [],        _write_skills),
        "projects":             (profile.get("projects") or [],      _write_projects),
        "active_learning_path": (profile.get("active_learning_path") or {}, _write_learning_path),
        "preferences":          (profile.get("preferences") or {},   _write_preferences),
        "system":               (profile.get("system") or {},        _write_system),
    }

    errors = []
    for section_name, (data, writer) in sections.items():
        try:
            writer(mm, data)
            print(f"  ✅  {section_name}")
        except Exception as exc:
            print(f"  ❌  {section_name}: {exc}")
            errors.append((section_name, exc))

    print()
    if errors:
        print(f"Seeded with {len(errors)} error(s). Check above for details.")
    else:
        print("Profile seeded successfully. Run `uv run python -m orchestrator` to start chatting.")


if __name__ == "__main__":
    yaml_file = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "profile.yaml"
    if not yaml_file.exists():
        print(f"ERROR: {yaml_file} not found.")
        sys.exit(1)
    seed(yaml_file)
