"""
scripts/tests/test_mark_topic_done.py

Verification suite for `api/tools.py::mark_topic_done` — the status mutator the
PI path was missing entirely.

Before this existed, `update_node_status` had one caller (inside the Python
graph), so on the default engine nothing could mark a curriculum node done: the
prerequisite frontier never advanced and all 22 live nodes sat at `not_started`.

Covers:
 1. Manifest: declared as a write intent, with its params and result
 2. Marking a node done persists the status
 3. The unlock report is code-computed: finishing a prerequisite unlocks its child
 4. `roadmap` disambiguates a title that exists in two roadmaps
 5. An ambiguous title with no `roadmap` is a structured error, never a guess
 6. An unknown node / unknown roadmap is a structured error
 7. An invalid status is refused
 8. `note` is appended to the topic's session log (zone 2), not to his notes
 9. `remaining` counts what is still not done in that roadmap

Isolation: runs entirely against a temporary curriculum root, set before
`orchestrator.config` is imported. The live curriculum is never touched.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP_CURRICULUM = tempfile.mkdtemp(prefix="mentor_mtd_test_")
os.environ["MENTOR_CURRICULUM_PATH"] = _TMP_CURRICULUM
atexit.register(lambda: shutil.rmtree(_TMP_CURRICULUM, ignore_errors=True))

from api.tools import MarkTopicDoneRequest, build_manifest, mark_topic_done  # noqa: E402
from orchestrator.memory.roadmap import (  # noqa: E402
    DEEPENED_HEADING,
    MY_NOTES_HEADING,
    load_roadmap,
    read_note,
    write_roadmap,
)
from schemas.memory import TopicGraph, TopicNode  # noqa: E402

_passed = 0
_failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    """`check(condition, label)` — matching scripts/tests/test_roadmap_store.py."""
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def _seed(root: str) -> None:
    """Two roadmaps. 'SQL Basics' exists in BOTH, so it is a genuine ambiguity."""
    write_roadmap(
        root,
        TopicGraph(
            topic_id="interview_prep",
            title="Interview Prep",
            nodes={
                "tn_sql_basics": TopicNode(id="tn_sql_basics", title="SQL Basics", day=1),
                "tn_sql_joins": TopicNode(
                    id="tn_sql_joins", title="SQL Joins", day=2, prerequisites=["tn_sql_basics"]
                ),
                "tn_indexes": TopicNode(
                    id="tn_indexes", title="Indexes", day=3, prerequisites=["tn_sql_joins"]
                ),
            },
        ),
        contents={
            "tn_sql_basics": "## 1. Concept Overview\nSELECT.",
            "tn_sql_joins": "## 1. Concept Overview\nJOIN.",
        },
    )
    write_roadmap(
        root,
        TopicGraph(
            topic_id="data_basics",
            title="Data Basics",
            nodes={"tn_sql_basics": TopicNode(id="tn_sql_basics", title="SQL Basics", day=1)},
        ),
        contents={"tn_sql_basics": "## 1. Concept Overview\nother copy"},
    )


def main() -> None:
    root = _TMP_CURRICULUM
    _seed(root)

    print("\n=== 1. Manifest declaration ===")
    descriptor = next((t for t in build_manifest().tools if t.name == "mark_topic_done"), None)
    check(descriptor is not None, "mark_topic_done is declared in the manifest")
    if descriptor:
        check(descriptor.kind == "write", f"declared as a write intent (got {descriptor.kind})")
        check(descriptor.params.get("required") == ["node"],
              f"only `node` is required (got {descriptor.params.get('required')})")
        props = set(descriptor.params.get("properties", {}))
        check({"node", "roadmap", "status", "note", "session_id"} <= props,
              f"all five params are exposed ({sorted(props)})")
        check("unlocked" in descriptor.result.get("properties", {}),
              "the result declares the code-computed `unlocked` list")

    print("\n=== 2. Marking a topic done ===")
    res = mark_topic_done(MarkTopicDoneRequest(node="SQL Joins", roadmap="Interview Prep"))
    check(res["updated"] is True, f"the update succeeded ({res.get('error')})")
    check(res["status"] == "done", f"status is done (got {res['status']})")
    check(res["node"] == "tn_sql_joins", f"resolved to the right node (got {res['node']})")
    graph = load_roadmap(root, "interview_prep")
    check(graph.nodes["tn_sql_joins"].status == "done", "the manifest holds the new status")

    print("\n=== 3. The unlock report is code-computed ===")
    check("Indexes" in res["unlocked"], f"finishing a prerequisite unlocked the child ({res['unlocked']})")
    res2 = mark_topic_done(MarkTopicDoneRequest(node="SQL Basics", roadmap="Interview Prep"))
    check(res2["updated"] is True, f"marking the root topic works ({res2.get('error')})")
    check(res2["unlocked"] == [], f"a second pass unlocks nothing new ({res2['unlocked']})")

    print("\n=== 4. `roadmap` disambiguates a shared title ===")
    check(res2["roadmap"] == "interview_prep", f"it hit the named roadmap (got {res2['roadmap']})")
    other = mark_topic_done(MarkTopicDoneRequest(node="SQL Basics", roadmap="Data Basics"))
    check(other["roadmap"] == "data_basics", f"the other copy is addressable too ({other['roadmap']})")
    check(load_roadmap(root, "data_basics").nodes["tn_sql_basics"].status == "done",
          "only the named roadmap's copy changed")

    print("\n=== 5. Ambiguity is refused, not guessed ===")
    mark_topic_done(MarkTopicDoneRequest(node="SQL Basics", roadmap="Data Basics", status="not_started"))
    ambiguous = mark_topic_done(MarkTopicDoneRequest(node="SQL Basics"))
    check(ambiguous["updated"] is False, "an ambiguous title without `roadmap` is refused")
    check("roadmap" in (ambiguous.get("error") or ""),
          f"the error explains how to fix it ({ambiguous['error']})")

    print("\n=== 6. Unknown node / roadmap / near-match ===")
    missing = mark_topic_done(MarkTopicDoneRequest(node="Quantum Field Theory", roadmap="Interview Prep"))
    check(missing["updated"] is False and "no topic exactly matching" in (missing.get("error") or ""),
          f"an unknown node is a structured error ({missing.get('error')})")
    bad_graph = mark_topic_done(MarkTopicDoneRequest(node="Indexes", roadmap="Nonexistent"))
    check(bad_graph["updated"] is False and "no roadmap" in (bad_graph.get("error") or ""),
          f"an unknown roadmap is a structured error ({bad_graph.get('error')})")
    near = mark_topic_done(MarkTopicDoneRequest(node="SQL", roadmap="Interview Prep"))
    check(near["updated"] is False, "a near-match ('SQL') is refused rather than guessed")

    print("\n=== 7. Invalid status ===")
    bad_status = mark_topic_done(MarkTopicDoneRequest(node="Indexes", roadmap="Interview Prep", status="banana"))
    check(bad_status["updated"] is False and "invalid status" in (bad_status.get("error") or ""),
          f"an invalid status is refused ({bad_status.get('error')})")

    print("\n=== 8. `note` lands in the session log, not his notes ===")
    with_note = mark_topic_done(
        MarkTopicDoneRequest(
            node="Indexes",
            roadmap="Interview Prep",
            status="in_progress",
            note="Covered B-tree vs hash indexes; still shaky on partial indexes.",
        )
    )
    check(with_note["updated"] is True and with_note["note_appended"] is True, "the note was appended")
    node = load_roadmap(root, "interview_prep").nodes["tn_indexes"]
    found = read_note(root, "interview_prep", node)
    text = found[1] if found else ""
    check("still shaky on partial indexes" in text, "the note text is in the file")
    if DEEPENED_HEADING in text and MY_NOTES_HEADING in text:
        check(text.index("still shaky") < text.index(MY_NOTES_HEADING),
              "it landed in zone 2, above his own notes")
    else:
        check(False, "the note carries both zone headings")

    print("\n=== 9. `remaining` counts what is still not done ===")
    check(with_note["remaining"] == 1, f"only the in_progress topic is left (got {with_note['remaining']})")

    print("\n" + "=" * 60)
    if _failed:
        print(f"RESULT: {_passed} passed, {_failed} FAILED")
        sys.exit(1)
    print(f"RESULT: {_passed} passed, 0 failed")


if __name__ == "__main__":
    main()