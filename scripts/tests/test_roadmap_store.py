"""
scripts/tests/test_roadmap_store.py

Verification suite for the roadmap store (Phase 0 of the roadmap redesign).

Runs against a temporary throwaway root — never touches the real curriculum.

Covers the invariants the redesign promises:
 1. Manifest round-trip (nodes, prerequisites, day, hours, anchors, source, budget)
 2. One folder per graph_id, index inside it, no doubled "Roadmap Roadmap" title
 3. Zone preservation — a regeneration NEVER destroys the mentor's "Deepened in
    session" or his own "My Notes"
 4. An unrecognised note layout is never replaced (appended to, and warned about)
 5. Idempotence — same content written twice, no duplicates, stable ids
 6. Prune removes only files we own; a foreign file survives untouched
 7. Exact-match resolution — "SQL" must NOT match "SQL Basics"/"SQL Practice"
 8. One status mutator, and it syncs frontmatter without touching the body
 9. Structural edits are refused when they would break the DAG
10. add_node refuses duplicates and dangling prerequisites
11. verify_anchors resolves real file::symbol and flags the fakes
12. allocate_days respects prerequisites and reports budget overflow
13. validate_roadmap finds orphan prerequisites and budget overflow
14. append_deepened writes only into zone 2
15. delete_roadmap is recoverable and refuses folders it does not own

Usage:
    uv run python scripts/tests/test_roadmap_store.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()

from orchestrator.memory.roadmap import (
    DEEPENED_HEADING,
    MANIFEST_NAME,
    MY_NOTES_HEADING,
    add_node,
    allocate_days,
    append_deepened,
    delete_node,
    delete_roadmap,
    list_roadmaps,
    load_roadmap,
    parse_frontmatter,
    set_node_status,
    update_node,
    validate_roadmap,
    verify_anchors,
    write_roadmap,
)
from schemas.memory import NodeAnchor, RoadmapSource, TopicGraph, TopicNode

PASS = "✅"
FAIL = "❌"
_failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {PASS if condition else FAIL} {label}")
    if not condition:
        _failures.append(label)


def _fixture_repo(base: Path) -> Path:
    """A tiny fake repo for anchor resolution."""
    repo = base / "repo"
    (repo / "pkg").mkdir(parents=True, exist_ok=True)
    (repo / "pkg" / "mod.py").write_text(
        "def embed_model():\n    return 'all-MiniLM-L6-v2'\n\n\nclass Store:\n    pass\n",
        encoding="utf-8",
    )
    return repo


def _graph(gid: str = "sql_roadmap", title: str = "SQL Roadmap") -> TopicGraph:
    return TopicGraph(
        topic_id=gid,
        title=title,
        target_days=4,
        hours_per_day=3.0,
        source=RoadmapSource(kind="topic"),
        nodes={
            "sql_basics": TopicNode(id="sql_basics", title="SQL Basics", estimated_hours=2.0, day=1),
            "sql_practice": TopicNode(
                id="sql_practice",
                title="SQL Practice",
                estimated_hours=2.0,
                day=2,
                prerequisites=["sql_basics"],
            ),
        },
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "learning"
        _fixture_repo(base)

        # ------------------------------------------------------------------
        print("\n=== 1. Manifest round-trip ===")
        graph = _graph()
        graph.nodes["sql_basics"] = graph.nodes["sql_basics"].model_copy(
            update={
                "anchors": [
                    NodeAnchor(
                        kind="symbol", path="pkg/mod.py", symbol="embed_model", note="384-dim model"
                    )
                ]
            }
        )
        report = write_roadmap(root, graph, contents={"sql_basics": "## 1. Concept Overview\nSQL."})
        check(Path(report.manifest).name == MANIFEST_NAME, "manifest written as roadmap.yaml")
        check(Path(report.manifest).parent.name == "sql_roadmap", "folder named by the stable graph_id")
        loaded = load_roadmap(root, "sql_roadmap")
        check(len(loaded.nodes) == 2, f"2 nodes round-tripped (got {len(loaded.nodes)})")
        check(loaded.nodes["sql_practice"].prerequisites == ["sql_basics"], "prerequisites preserved")
        check(loaded.nodes["sql_basics"].day == 1, "day preserved")
        check(loaded.nodes["sql_basics"].anchors[0].symbol == "embed_model", "anchors preserved")
        check(loaded.target_days == 4 and loaded.hours_per_day == 3.0, "budget preserved")
        check(loaded.version == 2, f"version bumped to 2 (got {loaded.version})")
        check(len(list_roadmaps(root)) == 1, "list_roadmaps finds it from manifests alone")

        # ------------------------------------------------------------------
        print("\n=== 2. Layout ===")
        folder = root / "sql_roadmap"
        check((folder / "SQL Roadmap.md").is_file(), "index sits inside the graph_id folder")
        check(not list(root.glob("*.md")), "no stray .md at the root")
        check(not (folder / "SQL Roadmap Roadmap.md").exists(), "title is NOT doubled")
        check((folder / "SQL Basics.md").is_file(), "note named from the node title")

        # ------------------------------------------------------------------
        print("\n=== 3. Zone preservation across regeneration ===")
        basics = load_roadmap(root, "sql_roadmap").nodes["sql_basics"]
        append_deepened(root, "sql_roadmap", basics, "**Q:** why two embeddings?\n**A:** recall drift.")
        note = (folder / "SQL Basics.md").read_text(encoding="utf-8")
        check("why two embeddings?" in note, "mentor enrichment landed in the note")

        note = note.replace(
            "<!-- Your notes and observations as you study. The mentor never writes here. -->",
            "MY OWN NOTE: JOINs clicked on day 2.",
        )
        (folder / "SQL Basics.md").write_text(note, encoding="utf-8")

        graph = load_roadmap(root, "sql_roadmap")
        write_roadmap(root, graph, contents={"sql_basics": "## 1. Concept Overview\nREWRITTEN BODY."})
        note = (folder / "SQL Basics.md").read_text(encoding="utf-8")
        check("REWRITTEN BODY." in note, "generated body was replaced")
        check("why two embeddings?" in note, "mentor's enrichment SURVIVED regeneration")
        check("MY OWN NOTE" in note, "his own notes SURVIVED regeneration")
        check(note.index(DEEPENED_HEADING) < note.index(MY_NOTES_HEADING), "zone order preserved")

        # ------------------------------------------------------------------
        print("\n=== 4. An unrecognised note layout is never replaced ===")
        hand = folder / "SQL Basics.md"
        hand.write_text("---\nid: sql_basics\ngraph_id: sql_roadmap\n---\n\nJust my raw thoughts.\n",
                        encoding="utf-8")
        graph = load_roadmap(root, "sql_roadmap")
        rep = write_roadmap(root, graph, contents={"sql_basics": "## NEW GENERATED\nmore"})
        text = hand.read_text(encoding="utf-8")
        check("Just my raw thoughts." in text, "hand-written content survived")
        check("NEW GENERATED" in text, "new content was appended instead")
        check(any("no zone headings" in w for w in rep.warnings), f"a warning was raised ({rep.warnings})")

        # ------------------------------------------------------------------
        print("\n=== 5. Idempotence ===")
        # Start from a note that carries our zone layout: a note WITHOUT zones is
        # deliberately append-only (test 4), so idempotence is asserted on a
        # note we own and can regenerate.
        (folder / "SQL Basics.md").unlink()
        graph = load_roadmap(root, "sql_roadmap")
        write_roadmap(root, graph, contents={"sql_basics": "## 1. Concept Overview\nSTABLE BODY."})
        first = (folder / "SQL Basics.md").read_text(encoding="utf-8")
        ids_first = sorted(load_roadmap(root, "sql_roadmap").nodes)
        check(DEEPENED_HEADING in first, "a fresh note carries the zone headings")
        graph = load_roadmap(root, "sql_roadmap")
        write_roadmap(root, graph, contents={"sql_basics": "## 1. Concept Overview\nSTABLE BODY."})
        second = (folder / "SQL Basics.md").read_text(encoding="utf-8")
        check(first == second, "writing the same content twice yields byte-identical notes")
        check(sorted(load_roadmap(root, "sql_roadmap").nodes) == ids_first, "node ids are stable")
        check(len(list(folder.glob("*.md"))) == 3, f"no duplicate files ({len(list(folder.glob('*.md')))} md)")

        # ------------------------------------------------------------------
        print("\n=== 6. Prune only removes files we own ===")
        foreign = folder / "his-scratch-note.md"
        foreign.write_text("# random thoughts\nno frontmatter here\n", encoding="utf-8")
        graph = load_roadmap(root, "sql_roadmap")
        graph.nodes.pop("sql_practice", None)          # drop a node from the manifest
        rep = write_roadmap(root, graph, contents={"sql_basics": "## 1. Concept Overview\nSTABLE BODY."},
                            prune=True)
        check("SQL Practice.md" in rep.pruned, f"the dropped node's note was pruned ({rep.pruned})")
        check(not (folder / "SQL Practice.md").exists(), "pruned note is gone")
        check(foreign.is_file(), "a file we do not own survived the prune")

        # ------------------------------------------------------------------
        print("\n=== 7. Exact-match resolution (the 'SQL' hazard) ===")
        graph = load_roadmap(root, "sql_roadmap")
        graph.nodes["sql_practice"] = TopicNode(
            id="sql_practice", title="SQL Practice", estimated_hours=2.0, prerequisites=["sql_basics"]
        )
        write_roadmap(root, graph, contents={"sql_practice": "## 1. Concept Overview\npractice"})
        ok, msg = delete_node(root, "sql_roadmap", "SQL")
        check(not ok, f"deleting by the ambiguous substring 'SQL' is refused ({msg})")
        check((folder / "SQL Basics.md").is_file() and (folder / "SQL Practice.md").is_file(),
              "neither note was touched by the refused delete")

        # ------------------------------------------------------------------
        print("\n=== 8. The single status mutator ===")
        append_deepened(root, "sql_roadmap", load_roadmap(root, "sql_roadmap").nodes["sql_basics"],
                        "KEEP ME: enriched during study")
        ok, msg = set_node_status(root, "sql_roadmap", "SQL Basics", "done")
        check(ok, f"status mutator accepted a valid change ({msg})")
        data, body = parse_frontmatter((folder / "SQL Basics.md").read_text(encoding="utf-8"))
        check(data.get("status") == "done", "note frontmatter was synced")
        check("KEEP ME" in body, "the note body was NOT touched by a structure-only write")
        check(load_roadmap(root, "sql_roadmap").nodes["sql_basics"].status == "done",
              "manifest holds the authoritative status")
        ok, _ = set_node_status(root, "sql_roadmap", "SQL Basics", "banana")
        check(not ok, "an invalid status is refused")

        # ------------------------------------------------------------------
        print("\n=== 9. Structural edits respect the DAG ===")
        ok, msg = update_node(root, "sql_roadmap", "SQL Basics", {"nonsense_field": 1})
        check(not ok and "unknown field" in msg, f"unknown fields are refused ({msg})")
        ok, msg = update_node(root, "sql_roadmap", "SQL Basics", {"prerequisites": ["sql_practice"]})
        check(not ok and "DAG" in msg, f"a prerequisite cycle is refused ({msg})")
        ok, msg = update_node(root, "sql_roadmap", "SQL Practice", {"day": 3, "estimated_hours": 1.5})
        check(ok, f"a legitimate structural update works ({msg})")
        check(load_roadmap(root, "sql_roadmap").nodes["sql_practice"].day == 3, "the change persisted")

        # ------------------------------------------------------------------
        print("\n=== 10. add_node ===")
        ok, msg = add_node(root, "sql_roadmap", TopicNode(id="", title="SQL Basics"))
        check(not ok and "already exists" in msg, f"a duplicate title is refused ({msg})")
        ok, msg = add_node(
            root, "sql_roadmap", TopicNode(id="", title="Window Functions"), prerequisites=["ghost"]
        )
        check(not ok and "unknown prerequisite" in msg, f"a dangling prerequisite is refused ({msg})")
        ok, msg = add_node(
            root,
            "sql_roadmap",
            TopicNode(id="", title="Window Functions", estimated_hours=1.5, day=4),
            prerequisites=["sql_practice"],
        )
        check(ok, f"a valid insert works ({msg})")
        check("window_functions" in load_roadmap(root, "sql_roadmap").nodes, "its id was derived")
        check((folder / "Window Functions.md").is_file(), "its note was written")

        # ------------------------------------------------------------------
        print("\n=== 11. verify_anchors ===")
        graph = load_roadmap(root, "sql_roadmap")
        graph.nodes["sql_basics"] = graph.nodes["sql_basics"].model_copy(
            update={
                "anchors": [
                    NodeAnchor(kind="symbol", path="pkg/mod.py", symbol="embed_model"),
                    NodeAnchor(kind="symbol", path="pkg/mod.py", symbol="not_a_real_symbol"),
                    NodeAnchor(kind="file", path="pkg/missing.py"),
                ]
            }
        )
        coverage = verify_anchors(graph, roots=[str(base / "repo")])
        check(coverage.total == 3, f"3 anchors examined (got {coverage.total})")
        check(coverage.resolved == 1, f"exactly the real one resolved (got {coverage.resolved})")
        check("pkg/mod.py::not_a_real_symbol" in coverage.unresolved_paths("sql_basics"),
              "a fake symbol is reported")
        check("pkg/missing.py" in coverage.unresolved_paths("sql_basics"), "a fake path is reported")
        check("1/3" in coverage.summary(), f"the summary states the ratio ({coverage.summary()})")

        # ------------------------------------------------------------------
        print("\n=== 12. allocate_days — the time-plan owner ===")
        pack = TopicGraph(
            topic_id="pack",
            title="Pack",
            nodes={
                "a": TopicNode(id="a", title="A", estimated_hours=2.0),
                "b": TopicNode(id="b", title="B", estimated_hours=2.0, prerequisites=["a"]),
                "c": TopicNode(id="c", title="C", estimated_hours=2.0, prerequisites=["b"]),
                "d": TopicNode(id="d", title="D", estimated_hours=2.0, prerequisites=["c"]),
            },
        )
        packed, rep = allocate_days(pack, target_days=4, hours_per_day=3.0)
        days = [packed.nodes[k].day for k in ("a", "b", "c", "d")]
        check(days == [1, 2, 3, 4], f"2h nodes never double up in a 3h day ({days})")
        check(rep["fits"] and rep["days_used"] == 4, f"8h over 4x3h fits ({rep['days_used']} days)")
        _, tight = allocate_days(pack, target_days=2, hours_per_day=1.0)
        check(not tight["fits"] and tight["overflow_hours"] == 6.0,
              f"an impossible 8h/2-day budget reports overflow ({tight['overflow_hours']}h)")

        # ------------------------------------------------------------------
        print("\n=== 13. validate_roadmap ===")
        write_roadmap(root, load_roadmap(root, "sql_roadmap"))
        issues = validate_roadmap(root, "sql_roadmap", verify=False)
        check(not [i for i in issues if i.severity == "error"],
              f"a healthy roadmap has no errors ({[str(i) for i in issues]})")
        broken = load_roadmap(root, "sql_roadmap")
        broken.nodes["sql_basics"] = broken.nodes["sql_basics"].model_copy(
            update={"prerequisites": ["ghost_node"]}
        )
        broken.target_days, broken.hours_per_day = 1, 1.0
        write_roadmap(root, broken)      # writes are not gated; validation IS the gate
        codes = {i.code for i in validate_roadmap(root, "sql_roadmap", verify=False)}
        check("orphan_prereq" in codes, f"an orphan prerequisite is an error ({sorted(codes)})")
        check("budget_overflow" in codes, f"a blown budget is reported ({sorted(codes)})")

        # ------------------------------------------------------------------
        print("\n=== 14. append_deepened touches only zone 2 ===")
        graph = load_roadmap(root, "sql_roadmap")
        before = parse_frontmatter((folder / "SQL Basics.md").read_text(encoding="utf-8"))[1]
        generated_before = before.split(DEEPENED_HEADING)[0]
        ok, msg = append_deepened(root, "sql_roadmap", graph.nodes["sql_basics"], "ANOTHER DEEP DIVE")
        after = parse_frontmatter((folder / "SQL Basics.md").read_text(encoding="utf-8"))[1]
        check(ok, f"append reported success ({msg})")
        check(generated_before == after.split(DEEPENED_HEADING)[0], "the generated body is byte-identical")
        check("ANOTHER DEEP DIVE" in after, "the new material is present")
        check(after.index("ANOTHER DEEP DIVE") < after.index(MY_NOTES_HEADING),
              "it landed inside zone 2, above his notes")

        # ------------------------------------------------------------------
        print("\n=== 15. delete_roadmap is recoverable and refuses strangers ===")
        stranger = root / "not_ours"
        stranger.mkdir(parents=True, exist_ok=True)
        (stranger / "random.md").write_text("mine\n", encoding="utf-8")
        ok, msg = delete_roadmap(root, "not_ours")
        check(not ok and "not a roadmap" in msg, f"a folder without a manifest is refused ({msg})")
        check(stranger.is_dir(), "and it was left alone")

        ok, msg = delete_roadmap(root, "sql_roadmap")
        check(ok and "recoverable" in msg, f"deletion is a recoverable move ({msg})")
        check(not (root / "sql_roadmap").exists(), "the roadmap folder is gone from its place")
        trashed = list((root / ".trash").glob("*sql_roadmap"))
        check(len(trashed) == 1 and (trashed[0] / MANIFEST_NAME).is_file(),
              "it sits in .trash with its manifest intact")

        # ------------------------------------------------------------------
        print("\n=== 16. The retired vault writer stays retired ===")
        # `obsidian_graph` was a SECOND writer of the same roadmap notes — exactly
        # the duplication this redesign removed. It is now deleted, and its two
        # still-live helpers (frontmatter parsing, filename sanitising) moved into
        # `roadmap.py`. This guard fails the suite if a reference creeps back in,
        # or if the module is resurrected.
        offenders: list[str] = []
        for folder in (ROOT_DIR / "agents", ROOT_DIR / "api", ROOT_DIR / "orchestrator"):
            for py in folder.rglob("*.py"):
                text = py.read_text(encoding="utf-8", errors="replace")
                if "orchestrator.memory.obsidian_graph" in text:
                    offenders.append(str(py.relative_to(ROOT_DIR)))
        check(not offenders, f"nothing imports the retired vault writer ({offenders})")
        check(
            not (ROOT_DIR / "orchestrator/memory/obsidian_graph.py").exists(),
            "the retired vault writer is deleted, not merely unreferenced",
        )

        decomposer = (ROOT_DIR / "agents/Goal_Decomposer/goal_decomposer.py").read_text(encoding="utf-8")
        check("MENTOR_CURRICULUM_PATH" in decomposer, "the decomposer writes to the curriculum root")
        check("OBSIDIAN_VAULT_PATH" not in decomposer,
              "the decomposer no longer targets the personal vault")

    # ----------------------------------------------------------------------
    print("\n" + "=" * 60)
    if _failures:
        print(f"RESULT: {len(_failures)} FAILED")
        for f in _failures:
            print(f"  {FAIL} {f}")
        sys.exit(1)
    print("RESULT: all roadmap-store checks passed")


if __name__ == "__main__":
    main()