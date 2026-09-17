"""
scripts/tools/extract_repo_curriculum.py

Repo → concept inventory, from the command line. This is the falsifiable first
test of the Repo-to-Curriculum plan (§5): run it against a repo whose ground truth
is known and read what it produces.

    # 1. No model, no tokens — see exactly what the model would be shown:
    uv run python scripts/tools/extract_repo_curriculum.py --briefing

    # 2. The live extraction (this is the §5 test):
    uv run python scripts/tools/extract_repo_curriculum.py --live

    # 3. And keep it, as a real roadmap you can study:
    uv run python scripts/tools/extract_repo_curriculum.py --live --persist

Default repo is this repository itself, because its ground truth is known: the
extractor is expected to find the single-writer boundary, the LF-only JSONL
framing, deterministic session ids, the 384-dim embedding coupling, the
prerequisite DAG, fail-open contracts, generated-not-mirrored contracts, and
idempotence owned by the store. `--live` prints a table saying which of those it
actually found, so a miss is visible and can be argued with rather than smoothed
over. If it cannot produce a defensible list for this repo, it will not work on an
unfamiliar one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.cognition.repo_curriculum import render_briefing, resolve_repo, scan_repo  # noqa: E402


def _print_scan(repo: str) -> Path:
    root = resolve_repo(repo)
    scan = scan_repo(root)
    print("=" * 78)
    print(f"REPO SCAN — {root}")
    print("=" * 78)
    print(f"  files scanned : {scan.total_files}" + ("  (TRUNCATED)" if scan.truncated else ""))
    print(f"  commit        : {scan.commit or 'not a git repo'}")
    print(f"  file types    : {', '.join(f'{k}×{v}' for k, v in list(scan.by_ext().items())[:8])}")
    if scan.unreadable:
        print(f"  unreadable    : {len(scan.unreadable)}")
    quoted = scan.briefing_candidates()
    print(f"  quoted to the model ({len(quoted)}):")
    for path in quoted:
        print(f"      {path}")
    return root


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", nargs="?", default=".", help="repo path (default: this repo)")
    parser.add_argument("--briefing", action="store_true", help="print the briefing and exit (no model call)")
    parser.add_argument("--live", action="store_true", help="run the real extraction (uses the model)")
    parser.add_argument("--persist", action="store_true", help="write the inventory as a roadmap")
    parser.add_argument("--stopping-rule", default="comprehension", choices=["comprehension", "authorship"])
    parser.add_argument("--max-nodes", type=int, default=12)
    parser.add_argument("--days", type=int, default=None, help="optional budget for day allocation")
    parser.add_argument("--hours-per-day", type=float, default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--briefing-file", default=None, help="write the briefing to this path")
    args = parser.parse_args()

    root = _print_scan(args.repo)

    if args.briefing or not args.live:
        briefing = render_briefing(scan_repo(root))
        if args.briefing_file:
            Path(args.briefing_file).write_text(briefing, encoding="utf-8")
            print(f"\nBriefing written to {args.briefing_file} ({len(briefing)} chars)")
        print("\n" + "=" * 78)
        print(f"BRIEFING ({len(briefing)} chars) — everything the model sees")
        print("=" * 78)
        print(briefing)
        if not args.live:
            print("\n(no model call: pass --live to run the extraction)")
        return 0

    # The live run. Imported here so --briefing never needs the LLM stack.
    from orchestrator.cognition.repo_curriculum import extract

    print("\n" + "=" * 78)
    print(f"LIVE EXTRACTION — stopping rule: {args.stopping_rule}, max {args.max_nodes} nodes")
    print("=" * 78)
    report = extract(
        args.repo,
        title=args.title,
        stopping_rule=args.stopping_rule,
        max_nodes=args.max_nodes,
        persist=args.persist,
        target_days=args.days,
        hours_per_day=args.hours_per_day,
    )

    if not report["ok"]:
        print(f"\nEXTRACTION FAILED: {report['error']}")
        return 1

    print(f"\n{report['title']} — {report['concept_count']} concepts")
    if report["summary"]:
        print(f"  {report['summary']}")
    print(f"  anchors: {report['anchor_coverage']}  |  {report['total_hours']}h over {report['days']} day(s)")

    print("\nCONCEPT INVENTORY")
    for c in report["concepts"]:
        anchors = (
            ", ".join(f"`{a['path']}{'::' + a['symbol'] if a['symbol'] else ''}`" for a in c["anchors"])
            or "(no anchors)"
        )
        prereq = f"  after: {', '.join(c['prerequisites'])}" if c["prerequisites"] else ""
        print(f"  • {c['concept']}  [{c['difficulty']}/5, {c['estimated_hours']}h, {c['content_type']}]")
        print(f"      {anchors}{prereq}")

    for label, items in (
        ("DROPPED ANCHORS (did not resolve)", report["dropped_anchors"]),
        ("DROPPED CONCEPTS (over budget)", report["dropped_concepts"]),
        ("DROPPED PREREQUISITES (unknown)", report["dropped_prerequisites"]),
        ("CYCLES BROKEN", report["broken_cycles"]),
        ("ISSUES", report["issues"]),
    ):
        if items:
            print(f"\n{label}:")
            for item in items:
                print(f"  ! {item}")

    print("\n§5 GROUND TRUTH (a defensible inventory of THIS repo must contain these)")
    found = sum(1 for r in report["ground_truth"] if r["found"])
    for r in report["ground_truth"]:
        mark = "✅" if r["found"] else ""
        hint = f'   → "{r["as"]}"' if r["found"] and r["as"] else ""
        print(f"  {mark} {r['expected']}{hint}")
    print(f"  {found}/{len(report['ground_truth'])} expected concepts present")

    if report["persisted"]:
        print(f"\nROADMAP WRITTEN: {report['roadmap_path']}")
        print("  Study it with available_topic_nodes; mark topics done with mark_topic_done.")
    else:
        print("\n(inventory only — pass --persist to keep it as a roadmap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())