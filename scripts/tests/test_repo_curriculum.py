"""
scripts/tests/test_repo_curriculum.py

Verification suite for repo → concept inventory (G1 of the Repo-to-Curriculum plan).

The model is FAKE here on purpose: this suite tests the half that must be
trustworthy — what code does to the model's answer — so it is deterministic, free,
and runs in milliseconds. The live extraction (the falsifiable §5 test) is a
separate, deliberate run: `scripts/tools/extract_repo_curriculum.py --live`.

Covers:
 1. The scan: skips vendor/ignored trees and NESTED REPOS (a vendored `pi/`)
 2. The briefing: docs capped, concepts come from code, bounded size
 3. Anchor verification: an invented `path::symbol` is dropped and reported
 4. Node budget: concepts beyond `max_nodes` are dropped and reported
 5. Prerequisites: wired by exact concept name; unknown names dropped and reported
 6. Cycles: broken in code, reported, and never persisted
 7. Days and hours: assigned by code from difficulty, not by the model
 8. Persistence: a real roadmap, clean validation, and a study frontier
 9. The ground-truth checker is not vacuous (empty inventory → nothing found)
10. Fail-open: a path outside the sandbox, and an empty repo, are readable errors

Isolation: everything runs inside a temporary repo and a temporary curriculum root,
with the workspace sandbox overridden via `harness._set_ws_roots`.
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.cognition.repo_curriculum import (  # noqa: E402
    AnchorDraft,
    RepoConcept,
    RepoInventory,
    check_ground_truth,
    extract,
    render_briefing,
    resolve_repo,
    scan_repo,
)
from orchestrator.harness import _set_ws_roots  # noqa: E402
from orchestrator.memory.roadmap import load_roadmap, read_note, validate_roadmap  # noqa: E402
from orchestrator.memory.topic_graph import get_available_nodes  # noqa: E402

_passed = 0
_failed = 0
_failed_names: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    """`check(condition, label)` — same shape as the other curriculum suites."""
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1
        _failed_names.append(name)


class _FakeStructured:
    def __init__(self, payload: object, explode: bool = False) -> None:
        self.payload, self.explode = payload, explode

    def invoke(self, _messages):  # noqa: ANN001
        if self.explode:
            raise RuntimeError("model unavailable")
        return self.payload


class FakeLLM:
    """Stands in for the chat model. Returns a fixed inventory, or raises."""

    def __init__(self, payload: object, explode: bool = False) -> None:
        self.payload, self.explode = payload, explode

    def with_structured_output(self, _schema):  # noqa: ANN001
        return _FakeStructured(self.payload, self.explode)


def _seed_repo(base: Path) -> Path:
    """A tiny repo with real symbols, plus trees that must be skipped."""
    repo = base / "sample_repo"
    (repo / "svc").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / "node_modules" / "leftover").mkdir(parents=True)
    nested = repo / "pi_vendored"
    (nested / ".git").mkdir(parents=True)

    (repo / "README.md").write_text("# Sample\nA tiny service.\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
    (repo / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    (repo / "svc" / "engine.py").write_text(
        "def fuse_scores(vector_hits, keyword_hits, k=60):\n"
        "    return {d: 1 / (k + i) for i, d in enumerate(vector_hits + keyword_hits)}\n\n\n"
        "class Registry:\n"
        "    def lookup(self, name):\n"
        "        return name\n",
        encoding="utf-8",
    )
    (repo / "svc" / "api.py").write_text(
        "from svc.engine import fuse_scores\n\n\ndef search(q):\n    return fuse_scores([q], [])\n",
        encoding="utf-8",
    )
    (repo / "docs" / "design.md").write_text("# Design\nfusion matters\n", encoding="utf-8")
    (repo / "node_modules" / "leftover" / "junk.js").write_text("x\n", encoding="utf-8")
    (nested / "vendored.py").write_text("y = 1\n", encoding="utf-8")
    return repo


def _inventory(*, extra: int = 0) -> RepoInventory:
    concepts = [
        RepoConcept(
            concept="Reciprocal rank fusion",
            what_to_cover="How two ranked lists are combined without score normalisation.",
            difficulty=4,
            content_type="algorithmic",
            appears_in=[
                AnchorDraft(path="svc/engine.py", symbol="fuse_scores", note="the fusion itself"),
                AnchorDraft(path="svc/ghost.py", symbol="invented_symbol", note="does not exist"),
            ],
        ),
        RepoConcept(
            concept="Registry lookup",
            what_to_cover="Name to handler resolution.",
            difficulty=2,
            appears_in=[AnchorDraft(path="svc/engine.py", symbol="Registry")],
            prerequisites=["Reciprocal rank fusion"],
        ),
        RepoConcept(
            concept="Alpha doctrine",
            what_to_cover="Cycle participant A.",
            difficulty=2,
            prerequisites=["Beta doctrine"],
        ),
        RepoConcept(
            concept="Beta doctrine",
            what_to_cover="Cycle participant B.",
            difficulty=2,
            prerequisites=["Alpha doctrine"],
        ),
    ]
    concepts[1].prerequisites.append("A concept that does not exist")
    for i in range(extra):
        concepts.append(
            RepoConcept(concept=f"Filler concept {i}", what_to_cover="padding", difficulty=1)
        )
    return RepoInventory(title="Understanding Sample", summary="A tiny service.", concepts=concepts)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="mentor_repo_curriculum_test_"))
    atexit.register(lambda: shutil.rmtree(tmp, ignore_errors=True))
    repo = _seed_repo(tmp)
    curriculum = tmp / "curriculum"

    # The sandbox: resolve_repo reuses harness._resolve_ws_path, so point the
    # workspace at the fixture root for this run.
    _set_ws_roots([str(tmp)])

    print("\n=== 1. The scan ===")
    scan = scan_repo(repo)
    paths = {f.path for f in scan.files}
    check(scan.total_files > 0, f"the fixture repo scans ({scan.total_files} files)")
    check("svc/engine.py" in paths, "real code is included")
    check(not any(p.startswith("node_modules") for p in paths), "node_modules is skipped")
    check(
        not any(p.startswith("pi_vendored") for p in paths),
        "a NESTED REPO (its own .git) is skipped entirely",
    )
    check(scan.by_ext().get(".py") == 2, f"two .py files counted, the vendored one skipped ({scan.by_ext().get('.py')})")

    print("\n=== 2. The briefing ===")
    briefing = render_briefing(scan)
    check("REPOSITORY: sample_repo" in briefing, "the briefing names the repo")
    check("svc/engine.py" in briefing, "code is quoted")
    check("checked against the filesystem" in briefing, "the citation rule is stated to the model")
    check(len(briefing) < 60_000, f"the briefing is bounded ({len(briefing)} chars)")
    candidates = scan.briefing_candidates()
    check(sum(1 for c in candidates if c.endswith((".md", ".rst"))) <= 2, "docs are capped")
    check(
        sum(1 for c in candidates if c.endswith((".py", ".ts", ".tsx", ".js"))) >= 1,
        "code gets the budget",
    )
    check(not any(c.endswith("tsconfig.json") for c in candidates[:3]), "configs do not crowd out code")

    print("\n=== 3. Anchors are verified, not trusted ===")
    report = extract("sample_repo", graph_id="repo_sample", llm=FakeLLM(_inventory()), max_nodes=8)
    check(report["ok"] is True, f"the extraction succeeded ({report.get('error')})")
    dropped = " ".join(report["dropped_anchors"])
    check("svc/ghost.py::invented_symbol" in dropped, f"the invented anchor was dropped ({dropped})")
    check("svc/ghost.py" not in str(report["concepts"]), "and it is not in the inventory")
    check(
        any(a["symbol"] == "fuse_scores" for c in report["concepts"] for a in c["anchors"]),
        "the real anchor survived",
    )
    check("2/3" in (report["anchor_coverage"] or ""), f"coverage is reported ({report['anchor_coverage']})")

    print("\n=== 4. The node budget ===")
    capped = extract("sample_repo", graph_id="repo_sample", llm=FakeLLM(_inventory(extra=4)), max_nodes=4)
    check(capped["concept_count"] == 4, f"the cap holds ({capped['concept_count']} nodes)")
    check(len(capped["dropped_concepts"]) == 4, f"the drop is reported ({capped['dropped_concepts']})")
    check(
        capped["dropped_concepts"] == [f"Filler concept {i}" for i in range(4)],
        "the tail of the model's learning order is what goes",
    )

    print("\n=== 5. Prerequisites ===")
    reg = next(c for c in report["concepts"] if c["concept"] == "Registry lookup")
    check(reg["prerequisites"] == ["Reciprocal rank fusion"], f"real prereqs wired ({reg['prerequisites']})")
    check(
        any("does not exist" in d for d in report["dropped_prerequisites"]),
        f"an unknown prerequisite name is dropped and reported ({report['dropped_prerequisites']})",
    )

    print("\n=== 6. Cycles ===")
    check(bool(report["broken_cycles"]), f"the cycle was broken ({report['broken_cycles']})")
    check(
        not any("Alpha doctrine" in c["prerequisites"] and "Beta doctrine" in
                next((x["prerequisites"] for x in report["concepts"] if x["concept"] == "Beta doctrine"), [])
                for c in report["concepts"]),
        "no mutual prerequisite pair remains",
    )

    print("\n=== 7. Hours and days come from code, not the model ===")
    fusion = next(c for c in report["concepts"] if c["concept"] == "Reciprocal rank fusion")
    check(fusion["estimated_hours"] == 3.0, f"difficulty 4 → 3.0h ({fusion['estimated_hours']})")
    check(
        all(c["day"] is None for c in report["concepts"]),
        f"no budget given → days stay UNSET, not 'all on day 1' "
        f"({[c['day'] for c in report['concepts']]})",
    )
    check(report["total_hours"] > 0, f"total hours reported ({report['total_hours']}h)")

    print("\n=== 8. Persistence makes a real roadmap ===")
    persisted = extract(
        "sample_repo",
        graph_id="repo_sample_persisted",
        llm=FakeLLM(_inventory()),
        max_nodes=8,
        persist=True,
        curriculum_root=str(curriculum),
        target_days=4,
        hours_per_day=2.0,
    )
    check(persisted["persisted"] is True, f"a roadmap was written ({persisted.get('error')})")
    graph = load_roadmap(curriculum, "repo_sample_persisted")
    check(len(graph.nodes) == 4, f"4 nodes in the manifest ({len(graph.nodes)})")
    check(graph.source.kind == "repo", f"provenance says repo ({graph.source.kind})")
    check(graph.source.path.endswith("sample_repo"), f"provenance names the repo ({graph.source.path})")
    check(graph.stopping_rule == "comprehension", "the stopping rule is recorded")
    check(
        not [i for i in validate_roadmap(curriculum, "repo_sample_persisted", verify=False) if i.severity == "error"],
        "the persisted roadmap validates with no errors",
    )
    node = graph.nodes["tn_reciprocal_rank_fusion"]
    note = read_note(curriculum, "repo_sample_persisted", node)[1]
    check("## 🔍 In the code" in note, "the note carries the anchor block")
    check("svc/engine.py::fuse_scores" in note, "the anchor is in the note the mentor reads")
    check("None (Entry point)" in note, "a root node renders as an entry point")
    frontier = [n.title for n in get_available_nodes(graph)]
    check(
        "Reciprocal rank fusion" in frontier,
        f"the extracted curriculum has a study frontier immediately ({frontier})",
    )
    check(
        "Registry lookup" not in frontier,
        "and a dependent node is still locked — the DAG is real",
    )

    print("\n=== 9. The ground-truth checker is not vacuous ===")
    empty = check_ground_truth(RepoInventory(title="x", summary="y", concepts=[]))
    check(len(empty) == 8, f"all eight §5 expectations are listed ({len(empty)})")
    check(not any(r["found"] for r in empty), "nothing is 'found' in an empty inventory")
    matches = check_ground_truth(
        RepoInventory(
            title="x",
            summary="y",
            concepts=[
                RepoConcept(
                    concept="The single-writer boundary between runtimes",
                    what_to_cover="One writer per store; fail-open contracts degrade rather than crash.",
                ),
            ],
        )
    )
    found = [r["expected"] for r in matches if r["found"]]
    check("single-writer boundary" in found, f"a real match is detected ({found})")
    check(len(found) < 8, "and it does not match everything")

    print("\n=== 10. Fail-open ===")
    outside = extract("/etc", llm=FakeLLM(_inventory()))
    check(outside["ok"] is False and "workspace" in (outside.get("error") or ""),
          f"a path outside the sandbox is refused ({outside.get('error')})")
    try:
        resolve_repo("/etc")
        check(False, "resolve_repo should have raised")
    except Exception as exc:
        check("workspace" in str(exc), f"resolve_repo raises a readable error ({exc})")
    empty_repo = tmp / "empty_repo"
    empty_repo.mkdir(exist_ok=True)
    nothing = extract("empty_repo", llm=FakeLLM(_inventory()))
    check(nothing["ok"] is False and "nothing readable" in (nothing.get("error") or ""),
          f"an empty repo is a readable error ({nothing.get('error')})")
    broken = extract("sample_repo", llm=FakeLLM(_inventory(), explode=True))
    check(broken["ok"] is False and "model failed" in (broken.get("error") or ""),
          f"an LLM failure is a readable error ({broken.get('error')})")
    no_concepts = extract(
        "sample_repo", llm=FakeLLM(RepoInventory(title="t", summary="s", concepts=[]))
    )
    check(no_concepts["ok"] is False and "no concepts" in (no_concepts.get("error") or ""),
          f"an empty inventory is a readable error ({no_concepts.get('error')})")

    print("\n" + "=" * 60)
    if _failed:
        print(f"RESULT: {_passed} passed, {_failed} FAILED")
        for name in _failed_names:
            print(f"   ❌ {name}")
        sys.exit(1)
    print(f"RESULT: {_passed} passed, 0 failed")


if __name__ == "__main__":
    main()