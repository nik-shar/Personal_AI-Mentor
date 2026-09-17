"""
scripts/migrations/migrate_vault_to_folders.py

One-time migration to the per-roadmap folder layout.

Before (old layout — everything mixed together):
    <vault>/Learning/Topics/          <- all node notes from ALL roadmaps, flat
    <vault>/<Graph Title> Roadmap.md  <- roadmap indexes at the vault ROOT

After (new layout — one folder per roadmap):
    <vault>/Learning/Topics/<Graph Title>/
        <Graph Title> Roadmap.md      (index)
        <Node Title>.md               (one file per topic node)

Dry-run by default; pass --apply to actually move files.

Usage:
    uv run python scripts/migrations/migrate_vault_to_folders.py           # preview moves
    uv run python scripts/migrations/migrate_vault_to_folders.py --apply   # execute moves
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Ensure root workspace directory is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.config import OBSIDIAN_TOPIC_FOLDER, OBSIDIAN_VAULT_PATH
from orchestrator.memory.roadmap import parse_frontmatter, sanitize_filename


def _plan_moves(vault_path: str, folder: str) -> tuple[list[tuple[Path, Path]], list[str]]:
    """Build the list of (source, destination) moves for legacy flat files.

    Returns (moves, warnings). Only touches:
      - .md files directly inside the topics folder with graph frontmatter
      - "* Roadmap.md" index files at the vault root
    Anything already inside a subfolder is left alone (idempotent).
    """
    target_dir = Path(vault_path) / folder
    vault_dir = Path(vault_path)
    moves: list[tuple[Path, Path]] = []
    warnings: list[str] = []

    if not target_dir.exists():
        return moves, [f"Topics folder does not exist: {target_dir}"]

    # 1. Flat node notes directly inside the topics folder
    for file_path in sorted(target_dir.glob("*.md")):
        try:
            data, _ = parse_frontmatter(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"Could not parse {file_path.name}: {exc} — left in place")
            continue

        graph_title = str(data.get("graph_title") or "").strip()
        if not graph_title:
            warnings.append(f"{file_path.name}: no 'graph_title' frontmatter — left in place")
            continue

        dest = target_dir / sanitize_filename(graph_title) / file_path.name
        moves.append((file_path, dest))

    # 2. Legacy roadmap index files at the vault root
    for index_path in sorted(vault_dir.glob("* Roadmap.md")):
        folder_name: str | None = None
        try:
            data, _ = parse_frontmatter(index_path.read_text(encoding="utf-8"))
            idx_graph_id = str(data.get("graph_id") or "").strip()
        except Exception:
            idx_graph_id = ""

        # Prefer the graph_title from a matching node note (source of truth)
        if idx_graph_id:
            for node_path in sorted(target_dir.rglob("*.md")):
                try:
                    ndata, _ = parse_frontmatter(node_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(ndata.get("graph_id") or "").strip() == idx_graph_id and ndata.get("graph_title"):
                    folder_name = sanitize_filename(str(ndata["graph_title"]))
                    break

        # Fallback: derive from the index filename itself ("<Title> Roadmap.md")
        if not folder_name:
            folder_name = sanitize_filename(index_path.stem.replace(" Roadmap", "").strip())

        if not folder_name:
            warnings.append(f"{index_path.name}: could not determine roadmap folder — left in place")
            continue

        dest = target_dir / folder_name / index_path.name
        moves.append((index_path, dest))

    return moves, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate flat Obsidian topic notes into per-roadmap folders.")
    parser.add_argument("--apply", action="store_true", help="Actually move files (default is dry-run preview).")
    args = parser.parse_args()

    moves, warnings = _plan_moves(OBSIDIAN_VAULT_PATH, OBSIDIAN_TOPIC_FOLDER)

    print(f"Vault:   {OBSIDIAN_VAULT_PATH}")
    print(f"Topics:  {OBSIDIAN_VAULT_PATH}/{OBSIDIAN_TOPIC_FOLDER}")
    print(f"Mode:    {'APPLY (files will be moved)' if args.apply else 'DRY-RUN (preview only)'}")
    print("-" * 70)

    if not moves:
        print("Nothing to migrate — vault is already organized into per-roadmap folders.")
    for src, dst in moves:
        print(f"  MOVE  {src}\n    ->  {dst}")

    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  ⚠️  {w}")

    if not args.apply or not moves:
        if moves and not args.apply:
            print("\nDry-run only. Re-run with --apply to perform these moves.")
        return

    print("\nApplying moves...")
    moved = 0
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                print(f"  ⚠️  Skipped {src.name}: destination already exists ({dst})")
                continue
            shutil.move(str(src), str(dst))
            moved += 1
        except Exception as exc:
            print(f"  ❌ Failed to move {src}: {exc}")

    print(f"\nDone. Moved {moved}/{len(moves)} file(s) into per-roadmap folders.")


if __name__ == "__main__":
    main()
