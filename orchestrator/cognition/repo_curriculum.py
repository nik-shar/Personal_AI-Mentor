"""
orchestrator/cognition/repo_curriculum.py

Repo → concept inventory: the missing link (G1 of the Repo-to-Curriculum plan).

What this answers
-----------------
"Teach me to understand this repository." The mentor can *read* a repo
(code-explorer) and it can *plan a topic* (goal_decomposer), but nothing bridges
them: the curriculum came from the model's general knowledge of a topic string,
so it could never know that *this* repo's hard part is embedding drift or the
single-writer boundary, could not say which node maps to which file, and could
never assess comprehension against the artifact because it never saw it.

This module produces the missing artifact: a concept inventory where every claim
points at real code.

    concept     : 384-dim embedding / vector-dimension coupling
    why needed  : comprehension
    appears in  : orchestrator/memory/store.py::_get_embed_model
    prereq of   : [hybrid retrieval, recall/latency tradeoff]
    difficulty  : 2/5

Design decisions, and why
-------------------------
**Where the judgment lives.** *Which* concepts matter, why, and how hard they are
is judgement — that is the model's job. Three things are NOT judgement and stay in
code, because a wrong value there fails silently:

1. **Anchor validity** — every `path::symbol` is resolved against the workspace
   sandbox before it is admitted. An unresolved anchor is dropped and reported; a
   curriculum citing files that do not exist is worse than one with no citations,
   because it looks checkable. The resolved ratio (`9/11 anchors resolved`) is the
   falsifiable signal the blueprint asks for.
2. **The budget** — the graph is capped by `max_nodes`. Every concept has infinite
   prerequisites (ask for vector search and you can justify linear algebra, then
   calculus, then floating point), so without a cap the roadmap becomes 200 nodes
   and the learner drowns. The cap is the stopping rule's teeth.
3. **The DAG** — cycles are broken in code and reported, and a cyclic graph is
   never persisted. `memory/topic_graph.py` owns that check; this module routes
   through it rather than re-implementing it.

**Why a narrow intent and not a new agent.** The blueprint's §7.4 leans towards a
specialist, and the boundary reasoning is right — analysis has no single-writer
need but it does need the code sandbox. A whole LangGraph agent is not required to
get that outcome, though: this is a bounded, single-shot analysis whose only
persistence is a roadmap, and the store already owns that. So it ships as one typed
intent (`learn_repo`) that a specialist could later wrap without changing a line
here.

**Where `mode` matters.** `inventory_only` writes nothing and answers with the
inventory — the falsifiable first test (run it against *this* repo, whose ground
truth is known). `persist` turns it into a real roadmap via `memory/roadmap.py`, so
the mentor can teach each node in place with the anchors in front of it.

**Honest limitation.** v1 is briefing-driven: one bounded pass over the tree, entry
points, manifests and the heads of the most informative files. It is not an
agentic walk that greps per concept. The upgrade path is a second pass that
searches for each proposed concept before admitting it — `verify_anchors` is the hook.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# Directories that never carry concepts, and would flood the briefing.
_SKIPPED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".obsidian",
    ".ruff_cache", ".pytest_cache", ".mypy_cache", ".next", "dist", "build",
    "chroma_store", "data", ".pgdata", ".idea", ".vscode", "coverage", "htmlcov",
    # vendor / generated trees that are not this project's own code
    "vendor", "third_party", "site-packages", ".tox", ".eggs", ".cache", "target",
}

# Extensions worth reading. Anything else is counted, never read.
_CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".rb",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".kt", ".swift", ".php", ".scala", ".sh",
    ".sql", ".tf", ".proto", ".graphql",
}
_CONFIG_EXTS = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"}
_DOC_EXTS = {".md", ".rst", ".txt"}
_READ_EXTS = _CODE_EXTS | _CONFIG_EXTS | _DOC_EXTS

# Files that reveal what a repo IS, before any deep reading.
_ENTRY_HINTS = (
    "main.py", "app.py", "server.py", "cli.py", "manage.py", "wsgi.py", "asgi.py",
    "index.ts", "index.js", "main.ts", "server.ts", "app.ts", "cli.ts",
    "route", "handler", "controller", "schema", "model", "settings", "config",
)
_MANIFEST_NAMES = (
    "pyproject.toml", "requirements.txt", "package.json", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "Gemfile", "composer.json", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "Makefile",
)
_DOC_NAMES = ("readme.md", "readme.rst", "readme.txt", "architecture.md", "design.md")

_MAX_FILE_BYTES = 200_000        # never read a file larger than this
_HEAD_LINES = 60                 # lines quoted per file in the briefing


class RepoCurriculumError(RuntimeError):
    """The repo could not be scanned or resolved (path outside sandbox, missing)."""


# ---------------------------------------------------------------------------
# The scan — deterministic, no LLM, fully testable
# ---------------------------------------------------------------------------

@dataclass
class RepoFile:
    path: str          # repo-relative, POSIX separators
    size: int
    kind: str          # "code" | "config" | "doc" | "other"


@dataclass
class RepoScan:
    """What a repo looks like before any interpretation happens."""

    root: Path
    commit: str | None = None
    files: list[RepoFile] = field(default_factory=list)
    truncated: bool = False           # the walk hit max_files
    unreadable: list[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.files)

    def by_ext(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.files:
            ext = Path(f.path).suffix.lower() or "(none)"
            counts[ext] = counts.get(ext, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def entry_points(self) -> list[str]:
        """Files that most often reveal a repo's shape, best guesses first."""
        hits: list[tuple[int, str]] = []
        for f in self.files:
            name = Path(f.path).name.lower()
            score = 0
            if name in _DOC_NAMES:
                score = 4
            elif name in _MANIFEST_NAMES:
                score = 3
            elif name in _ENTRY_HINTS or any(h in name for h in _ENTRY_HINTS):
                score = 2
            if score:
                # Prefer shallower files: depth is a decent proxy for importance.
                hits.append((score * 10 - f.path.count("/"), f.path))
        hits.sort(reverse=True)
        return [path for _score, path in hits]

    def manifests(self) -> list[str]:
        return [f.path for f in self.files if Path(f.path).name in _MANIFEST_NAMES]

    def briefing_candidates(self, *, max_docs: int = 2, max_manifests: int = 3, max_code: int = 7) -> list[str]:
        """Orientation from docs and manifests, concepts from CODE — mostly code.

        A briefing dominated by READMEs produces concepts anchored to prose, and
        prose cannot carry a `path::symbol` anchor. A briefing dominated by
        `pyproject.toml` / `tsconfig.json` produces concepts about tooling. So the
        budget is split, and the code slots go to the files with the most substance
        (see `_code_score`) rather than the first alphabetically.
        """
        docs: list[tuple[int, str]] = []
        manifests: list[tuple[int, str]] = []
        code: list[tuple[int, str]] = []
        for f in self.files:
            name = Path(f.path).name.lower()
            if name in _DOC_NAMES:
                docs.append((10 - f.path.count("/"), f.path))
            elif name in _MANIFEST_NAMES:
                manifests.append((10 - f.path.count("/"), f.path))
            elif f.kind == "code":
                code.append((self._code_score(f), f.path))
        docs.sort(reverse=True)
        manifests.sort(reverse=True)
        code.sort(reverse=True)
        return (
            [p for _s, p in docs[:max_docs]]
            + [p for _s, p in manifests[:max_manifests]]
            + [p for _s, p in code[:max_code]]
        )

    def _code_score(self, f: RepoFile) -> int:
        """How likely a code file is to carry the concepts worth teaching.

        Deliberately crude and deterministic: named entry points first, then
        domain-sounding modules, then raw size as a proxy for substance. Config
        files are not code and never reach here (`_kind_of` keeps them separate).
        """
        name = Path(f.path).name.lower()
        score = 0
        if name in ("main.py", "app.py", "cli.py", "server.py", "orchestrator.py"):
            score += 30
        if name in _ENTRY_HINTS or any(h in name for h in _ENTRY_HINTS):
            score += 8
        if name == "__main__.py":
            score -= 15          # almost always a three-line CLI stub, not a concept
        score += min(f.size // 2000, 20)     # substance
        score -= f.path.count("/")           # shallower is usually more central
        return score


def _kind_of(ext: str) -> str:
    if ext in _CODE_EXTS:
        return "code"
    if ext in _CONFIG_EXTS:
        return "config"
    if ext in _DOC_EXTS:
        return "doc"
    return "other"


def resolve_repo(path: str) -> Path:
    """Resolve a repo path inside the workspace sandbox.

    Reuses `harness._resolve_ws_path` deliberately: the sandbox rule must live in
    exactly one place (boundary anti-pattern: two implementations of one
    invariant). Failure is a readable error, never a traceback.
    """
    raw = (path or "").strip()
    if not raw:
        raise RepoCurriculumError("no repo path given")
    try:
        from orchestrator.harness import _resolve_ws_path

        resolved = _resolve_ws_path(raw)
    except Exception as exc:
        raise RepoCurriculumError(f"cannot resolve '{raw}' inside the workspace: {exc}") from exc
    if not resolved.is_dir():
        raise RepoCurriculumError(f"not a directory: {resolved}")
    return resolved


def _git_commit(root: Path) -> str | None:
    """The commit the inventory was taken at, so the curriculum is re-derivable."""
    try:
        import subprocess

        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def scan_repo(root: Path, *, max_files: int = 400, skip_nested_repos: bool = True) -> RepoScan:
    """Walk the tree and record what is there. Never raises, never reads contents.

    A directory containing its own `.git` is skipped entirely: that is a *vendored
    separate repository*, not part of this project's concepts. Without this rule
    the walk of this repo filled 145 of its 400 slots with the upstream PI harness
    under `pi/` — gitignored vendor code the curriculum must not teach as if it
    were ours.
    """
    import os

    scan = RepoScan(root=root, commit=_git_commit(root))
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        keep: list[str] = []
        for name in dirnames:
            if name in _SKIPPED_DIRS:
                continue
            child = here / name
            if skip_nested_repos and (child / ".git").exists():
                continue
            keep.append(name)
        dirnames[:] = sorted(keep)

        for filename in sorted(filenames):
            if len(scan.files) >= max_files:
                scan.truncated = True
                return scan
            path = here / filename
            try:
                size = path.stat().st_size
            except OSError:
                scan.unreadable.append(str(path))
                continue
            scan.files.append(
                RepoFile(
                    path=path.relative_to(root).as_posix(),
                    size=size,
                    kind=_kind_of(path.suffix.lower()),
                )
            )
    return scan


def read_head(root: Path, rel_path: str, *, lines: int = _HEAD_LINES) -> str:
    """The first `lines` lines of a file, bounded. Empty string on any failure."""
    target = root / rel_path
    try:
        if target.stat().st_size > _MAX_FILE_BYTES:
            return ""
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:lines])


# Definition patterns, per language family. Crude on purpose: this is an index the
# model reasons over, not a parser, and a false positive costs one line of noise.
_SYMBOL_PATTERNS = {
    ".py": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
    ".ts": re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z_$][\w$]*)"
        r"|^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=",
        re.MULTILINE,
    ),
}
_SYMBOL_PATTERNS[".tsx"] = _SYMBOL_PATTERNS[".ts"]
_SYMBOL_PATTERNS[".js"] = _SYMBOL_PATTERNS[".ts"]

_INDEX_MAX_LINES = 1200      # of a file, when scanning for definitions


def render_symbol_index(
    scan: RepoScan, *, max_files: int = 30, max_per_file: int = 14
) -> str:
    """Every notable definition in the repo's own code, as `path: sym, sym, …`.

    Why this exists: quoting 60-line heads of the largest files shows imports and
    module docstrings, not the vocabulary of the codebase. In the first live run
    that produced a generic inventory ("FastAPI for Web Services") and missed every
    concept the repo is actually about, because the files carrying those decisions
    — `pi_bridge.py`, `memory/store.py`, the schema generator — were never shown.
    An index of definitions is the cheapest way to put the real names in front of
    the model, and it is what a `path::symbol` anchor needs to be plausible at all.
    """
    code = [f for f in scan.files if f.kind == "code" and Path(f.path).suffix.lower() in _SYMBOL_PATTERNS]
    code.sort(key=lambda f: -scan._code_score(f))

    lines = ["", "DEFINITIONS (path: notable functions/classes — cite these by name):"]
    shown = 0
    for f in code[:max_files]:
        pattern = _SYMBOL_PATTERNS[Path(f.path).suffix.lower()]
        try:
            if (scan.root / f.path).stat().st_size > _MAX_FILE_BYTES:
                continue
            text = "\n".join(
                (scan.root / f.path).read_text(encoding="utf-8", errors="replace").splitlines()[
                    :_INDEX_MAX_LINES
                ]
            )
        except OSError:
            continue
        names: list[str] = []
        for match in pattern.finditer(text):
            name = match.group(1) or (match.group(2) if match.lastindex and match.lastindex >= 2 else None)
            if name and name not in names:
                names.append(name)
        if not names:
            continue
        lines.append(f"  {f.path}: {', '.join(names[:max_per_file])}")
        shown += 1
    if shown == 0:
        return ""
    return "\n".join(lines)


_SYMBOL_CLEAN_RE = re.compile(r"^[^A-Za-z_$]*([A-Za-z_$][\w$]*)")


def clean_symbol(raw: str | None) -> str | None:
    """Salvage a symbol name from the model's output.

    The first live run returned symbols like `run_orchestrator_turn()},{` and
    `MemoryManager},{` — array-boundary junk glued onto a correct name. Rejecting
    those threw away good anchors and understated coverage, so the leading
    identifier is extracted instead (and `()`, generics, and trailing punctuation
    fall away with it). A symbol that is genuinely invented still fails to resolve
    afterwards, which is where that judgement belongs.
    """
    if not raw:
        return None
    match = _SYMBOL_CLEAN_RE.match(str(raw).strip())
    return match.group(1) if match else None


def render_briefing(scan: RepoScan, *, max_files_quoted: int = 12) -> str:
    """The bounded, deterministic brief the model reasons over.

    Deliberately not the whole repo: a briefing that fits in a prompt is one the
    model can weigh, and the anchors it proposes are verified afterwards rather
    than trusted. Composition is docs-then-code with a code-weighted budget, so
    concepts can be anchored to symbols instead of to prose.
    """
    lines: list[str] = [
        f"REPOSITORY: {scan.root.name}",
        f"Files scanned: {scan.total_files}" + (" (TRUNCATED at the cap)" if scan.truncated else ""),
        f"Commit: {scan.commit or 'unknown (not a git repo)'}",
        "",
        "FILE TYPES:",
    ]
    for ext, count in list(scan.by_ext().items())[:12]:
        lines.append(f"  - {ext}: {count}")

    tree = sorted({f.path for f in scan.files})
    lines += ["", f"TREE (up to 60 of {scan.total_files}):"]
    lines += [f"  {p}" for p in tree[:60]]
    if len(tree) > 60:
        lines.append(f"  … and {len(tree) - 60} more")

    quoted = 0
    for path in scan.briefing_candidates():
        if quoted >= max_files_quoted:
            break
        head = read_head(scan.root, path)
        if not head.strip():
            continue
        lines += ["", f"--- {path} (first {_HEAD_LINES} lines) ---", head]
        quoted += 1

    lines.append(render_symbol_index(scan))

    lines += [
        "",
        "REMINDER: cite only paths and symbols that appear above. `appears_in` entries",
        "are checked against the filesystem; invented ones are dropped and reported.",
        "Prefer the concepts THIS code embodies over general computer science.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The inventory — the model's judgement, in a shape code can check
# ---------------------------------------------------------------------------

class AnchorDraft(BaseModel):
    """One place a concept lives. Verified in code before it is admitted."""

    path: str = Field(description="Repository-relative file path, exactly as shown in the briefing.")
    symbol: str | None = Field(
        default=None,
        description="A function/class/constant name in that file, or omit if the whole file is the anchor.",
    )
    note: str | None = Field(default=None, description="One clause on why this location matters.")


class RepoConcept(BaseModel):
    """One thing a reader must understand to read this repo."""

    concept: str = Field(description="The concept, named as a learner would search for it.")
    what_to_cover: str = Field(description="One or two sentences on what understanding this requires.")
    why_needed: Literal["comprehension", "authorship"] = Field(
        default="comprehension",
        description=(
            "'comprehension' — needed to READ this repo. 'authorship' — needed to rebuild it. "
            "Honour the stopping rule you were given."
        ),
    )
    difficulty: int = Field(default=2, ge=1, le=5, description="1 (gentle) to 5 (demanding).")
    appears_in: list[AnchorDraft] = Field(
        default_factory=list,
        description="Where this concept actually lives. Only cite files you were shown.",
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Exact names of other concepts in THIS list that must be understood first.",
    )
    content_type: Literal["conceptual", "algorithmic", "hands_on_code", "reference"] = Field(
        default="conceptual",
        description="How this is best taught.",
    )


class RepoInventory(BaseModel):
    """The extractor's whole answer for one repo."""

    title: str = Field(description="A short title for the curriculum, e.g. 'Understanding Mini-RAG'.")
    summary: str = Field(description="Two or three sentences on what this repo is and what its hard parts are.")
    concepts: list[RepoConcept] = Field(
        default_factory=list, description="Ordered as a learning path: foundations first."
    )


_SYSTEM_PROMPT = """You are a principal engineer reading a repository to build a curriculum.

Your job is NOT to summarise the code. It is to identify the CONCEPTS a reader must
understand to read THIS repository, and for each one to say exactly where it lives.

Rules:
1. Cite only files you were actually shown, using the exact paths from the briefing.
   If you cannot point at real code for a concept, leave `appears_in` empty rather
   than guessing — the citations are verified afterwards and invented ones are
   dropped and reported as errors.
2. Honour the stopping rule:
   - `comprehension` — what a reader needs in order to READ this code.
   - `authorship` — what a person needs in order to REBUILD it from scratch.
   Both are legitimate; they produce very different lists. Do not exceed the rule.
3. Respect the node budget: at most {max_nodes} concepts. If there is more worth
   teaching, choose the ones with the highest learning leverage — the decisions that
   make the rest of the code make sense.
4. Prefer the repo's *own* hard parts over generic computer science. "Why the
   embedding dimension is fixed and shared" beats "what is a vector". A concept
   that would appear in any repo of this language and framework is usually the
   wrong answer: if "FastAPI basics" or "CORS" could be said of a thousand
   projects, it is not an insight about THIS one. Look for the decisions,
   invariants, and traps that make this codebase what it is.
5. Difficulty is about THIS repo's code, not the topic in the abstract.
6. Prerequisites must be exact concept names from your own list, and must not form a
   cycle. Foundations first in the list order.
7. Use the DEFINITIONS index: cite real `path::symbol` pairs from it. A concept with
   a verified citation is worth more than three with invented ones.
"""


def build_inventory(
    briefing: str,
    *,
    llm: Any | None = None,
    stopping_rule: str = "comprehension",
    max_nodes: int = 12,
    temperature: float = 0.2,
) -> RepoInventory:
    """Ask the model for the inventory. Raises on LLM failure — the caller reports it.

    `llm` is injectable so the whole pipeline is testable without a model, which is
    also how the suite stays cheap and deterministic.
    """
    if llm is None:
        from orchestrator.llm import get_reasoning_llm

        llm = get_reasoning_llm(temperature=temperature)

    system = _SYSTEM_PROMPT.format(max_nodes=max_nodes)
    user = (
        f"STOPPING RULE: {stopping_rule}\n"
        f"NODE BUDGET: {max_nodes} concepts maximum.\n\n"
        f"{briefing}"
    )
    structured = llm.with_structured_output(RepoInventory)
    inventory = structured.invoke(
        [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )
    if isinstance(inventory, dict):  # some wrappers hand back a plain dict
        inventory = RepoInventory.model_validate(inventory)
    return inventory


# ---------------------------------------------------------------------------
# Materialization — everything the model must NOT be trusted with
# ---------------------------------------------------------------------------

# Difficulty → hours. Code owns the numbers, the model owns the judgement: the
# mapping is fixed so estimates are comparable across repos and sessions, and
# `roadmap.allocate_days` can turn it into a real schedule.
_HOURS_BY_DIFFICULTY = {1: 0.75, 2: 1.25, 3: 2.0, 4: 3.0, 5: 4.0}


@dataclass
class MaterializeResult:
    """The graph plus everything that had to be corrected to build it."""

    graph: Any                                    # TopicGraph
    dropped_anchors: list[str] = field(default_factory=list)
    dropped_concepts: list[str] = field(default_factory=list)
    dropped_prerequisites: list[str] = field(default_factory=list)
    broken_cycles: list[str] = field(default_factory=list)
    coverage: Any | None = None                   # AnchorCoverage
    issues: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "concept"


def _break_cycles(graph: Any, result: MaterializeResult, *, max_rounds: int = 12) -> Any:
    """Drop prerequisite edges until the graph is acyclic. Never persist a cycle.

    A cycle is a model error (two concepts each declared a prerequisite of the
    other), and the DAG is the structure the whole feature rests on: a cyclic
    prerequisite graph cannot be topologically ordered, so "what can I study next"
    would have no answer at all.
    """
    import networkx as nx

    from orchestrator.memory.topic_graph import _build_nx_graph, topological_order

    for _round in range(max_rounds):
        try:
            topological_order(graph)
            return graph
        except ValueError:
            pass  # cyclic — that is what we are here to fix
        except Exception as exc:
            print(f"[repo_curriculum] cycle check unavailable ({exc}) — leaving the graph as-is")
            return graph

        try:
            cycle = list(nx.find_cycle(_build_nx_graph(graph)))
        except Exception as exc:
            # Loud, not silent: a swallowed error here would hand back a cyclic
            # graph that `allocate_days` then quietly refuses to schedule.
            print(f"[repo_curriculum] could not inspect the graph for cycles: {exc}")
            return graph
        if not cycle:
            return graph

        src, dst = cycle[-1][0], cycle[-1][1]
        node = graph.nodes.get(dst)
        if node is None:
            return graph
        src_title = graph.nodes[src].title if src in graph.nodes else str(src)
        graph.nodes[dst] = node.model_copy(
            update={"prerequisites": [p for p in node.prerequisites if p != src]}
        )
        result.broken_cycles.append(f"{node.title} no longer requires {src_title} (cycle broken)")
    print(f"[repo_curriculum] gave up breaking cycles after {max_rounds} rounds")
    return graph


def materialize(
    scan: RepoScan,
    inventory: RepoInventory,
    *,
    graph_id: str,
    title: str | None = None,
    stopping_rule: str = "comprehension",
    max_nodes: int = 12,
    target_days: int | None = None,
    hours_per_day: float | None = None,
) -> MaterializeResult:
    """Turn an inventory into a validated TopicGraph, correcting as it goes.

    Corrections, all reported rather than silent:
      - concepts beyond `max_nodes` are dropped (the model's order IS the learning
        path, so the tail is what goes);
      - anchors that do not resolve against the sandbox are dropped;
      - prerequisites naming a concept that is absent or dropped are dropped;
      - cycles are broken.
    """
    from schemas.memory import NodeAnchor, RoadmapSource, TopicGraph, TopicNode

    result = MaterializeResult(graph=None)

    concepts = list(inventory.concepts)
    if len(concepts) > max_nodes:
        result.dropped_concepts = [c.concept for c in concepts[max_nodes:]]
        concepts = concepts[:max_nodes]

    graph_title = title or inventory.title or f"Understanding {scan.root.name}"

    # Pass 1 — nodes, with anchors attached so they can be verified together.
    nodes: dict[str, TopicNode] = {}
    title_to_id: dict[str, str] = {}
    for concept in concepts:
        node_id = f"tn_{_slug(concept.concept)}"
        if node_id in nodes:  # two concepts slugged to the same id
            suffix = 2
            while f"{node_id}_{suffix}" in nodes:
                suffix += 1
            node_id = f"{node_id}_{suffix}"
        nodes[node_id] = TopicNode(
            id=node_id,
            title=concept.concept,
            estimated_hours=_HOURS_BY_DIFFICULTY.get(concept.difficulty, 1.25),
            content_type=concept.content_type,
            anchors=[
                NodeAnchor(
                    kind="symbol" if clean_symbol(a.symbol) else "file",
                    path=a.path,
                    symbol=clean_symbol(a.symbol),
                    note=a.note,
                )
                for a in concept.appears_in
            ],
            notes=concept.what_to_cover,
        )
        title_to_id[concept.concept.strip().lower()] = node_id

    # Pass 2 — anchors, verified in code. An unresolvable citation is dropped:
    # a curriculum pointing at files that do not exist looks checkable but is not.
    coverage = None
    try:
        from orchestrator.memory.roadmap import verify_anchors

        coverage = verify_anchors(
            TopicGraph(topic_id=graph_id, title=graph_title, nodes=nodes), roots=[str(scan.root)]
        )
        for bad in coverage.unresolved():
            result.dropped_anchors.append(f"{bad.node_title}: {bad.label()} — {bad.reason}")
        for node_id, node in list(nodes.items()):
            bad_labels = {r.label() for r in coverage.unresolved() if r.node_id == node_id}
            keep = [
                a
                for a in node.anchors
                if (a.path if a.kind == "file" else f"{a.path}::{a.symbol or '?'}") not in bad_labels
            ]
            if len(keep) != len(node.anchors):
                nodes[node_id] = node.model_copy(update={"anchors": keep})
    except Exception as exc:
        result.issues.append(f"anchor verification unavailable: {exc}")

    # Pass 3 — prerequisite edges, resolved by exact concept name.
    for concept in concepts:
        node_id = title_to_id.get(concept.concept.strip().lower())
        if not node_id:
            continue
        prereq_ids: list[str] = []
        for name in concept.prerequisites:
            target = title_to_id.get((name or "").strip().lower())
            if target is None or target == node_id:
                result.dropped_prerequisites.append(
                    f"{concept.concept} → '{name}' (not a concept in this inventory)"
                )
                continue
            if target not in prereq_ids:
                prereq_ids.append(target)
        if prereq_ids:
            nodes[node_id] = nodes[node_id].model_copy(update={"prerequisites": prereq_ids})

    graph = TopicGraph(
        topic_id=graph_id,
        title=graph_title,
        nodes=nodes,
        source=RoadmapSource(
            kind="repo", path=str(scan.root), commit=scan.commit, stopping_rule=stopping_rule
        ),
        stopping_rule=stopping_rule,
        target_days=target_days,
        hours_per_day=hours_per_day,
    )
    graph = _break_cycles(graph, result)

    # Days are assigned by code, never by the model (roadmap.allocate_days).
    try:
        from orchestrator.memory.roadmap import allocate_days

        graph, _budget = allocate_days(graph)
    except Exception as exc:
        result.issues.append(f"day allocation skipped: {exc}")

    result.graph = graph
    result.coverage = coverage
    return result


# ---------------------------------------------------------------------------
# The falsifiable test — §5 of the blueprint, made mechanical
# ---------------------------------------------------------------------------

# A defensible inventory of THIS repo must include these, at minimum. Each entry
# is (label, keyword groups); a concept counts as present when every group has a
# hit in the concept text, so "embedding" alone does not satisfy "embedding
# dimension coupling". Quoted from the blueprint so the expectation is not
# quietly reshaped to whatever the extractor happens to produce.
GROUND_TRUTH_CONCEPTS: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    ("single-writer boundary", (("single", "one"), ("writer", "write"))),
    ("LF-only JSONL framing over a subprocess", (("jsonl", "framing", "rpc"), ("line", "lf", "newline"))),
    ("deterministic session-id derivation", (("session",), ("deriv", "deterministic"))),
    ("384-dim embeddings and silent recall degradation", (("embedding", "vector"), ("384", "dimension"))),
    ("topological unlock over a prerequisite DAG", (("topolog", "prerequisit", "dag"),)),
    ("fail-open tool contracts", (("fail-open", "fail open", "degrade"),)),
    # Deliberately strict: the first version accepted "generat", so "Markdown
    # Content Generation" counted as a hit for "generated, not hand-mirrored".
    # A matcher that flatters the extractor is worse than no matcher.
    ("contracts generated, not hand-mirrored", (("mirror", "codegen", "generated contract", "generated from"),)),
    ("idempotence owned by the store", (("idempot", "upsert", "atomic"),)),
)


def check_ground_truth(inventory: RepoInventory) -> list[dict[str, Any]]:
    """Which §5 concepts this inventory actually contains.

    Not a score to game — a listing, so a miss is visible and can be argued with.
    """
    results: list[dict[str, Any]] = []
    for label, groups in GROUND_TRUTH_CONCEPTS:
        blob = " ".join(
            f"{c.concept} {c.what_to_cover} {' '.join(a.symbol or '' for a in c.appears_in)}"
            for c in inventory.concepts
        ).lower()
        matched = all(any(k in blob for k in group) for group in groups)
        witness = next(
            (
                c.concept
                for c in inventory.concepts
                if any(k in f"{c.concept} {c.what_to_cover}".lower() for k in groups[0])
            ),
            "",
        )
        results.append({"expected": label, "found": matched, "as": witness})
    return results


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def extract(
    repo_path: str,
    *,
    title: str | None = None,
    graph_id: str | None = None,
    stopping_rule: str = "comprehension",
    max_nodes: int = 12,
    persist: bool = False,
    target_days: int | None = None,
    hours_per_day: float | None = None,
    llm: Any | None = None,
    curriculum_root: str | None = None,
) -> dict[str, Any]:
    """Repo → validated concept inventory, and optionally a real roadmap.

    Returns a report dict and never raises: `ok`, the inventory, the corrections
    code had to make, the anchor-coverage ratio, and — when `persist` — where the
    roadmap landed. `inventory_only` (the default) writes nothing.
    """
    report: dict[str, Any] = {
        "ok": False,
        "error": None,
        "repo": "",
        "commit": None,
        "files_scanned": 0,
        "briefing_chars": 0,
        "stopping_rule": stopping_rule,
        "max_nodes": max_nodes,
        "graph_id": "",
        "title": "",
        "concept_count": 0,
        "concepts": [],
        "anchor_coverage": None,
        "dropped_anchors": [],
        "dropped_concepts": [],
        "dropped_prerequisites": [],
        "broken_cycles": [],
        "issues": [],
        "total_hours": 0.0,
        "days": 0,
        "budget": None,
        "persisted": False,
        "roadmap_path": None,
        "ground_truth": [],
        "summary": "",
    }

    try:
        root = resolve_repo(repo_path)
    except RepoCurriculumError as exc:
        report["error"] = str(exc)
        return report

    scan = scan_repo(root)
    briefing = render_briefing(scan)
    report.update(
        repo=str(root),
        commit=scan.commit,
        files_scanned=scan.total_files,
        briefing_chars=len(briefing),
    )
    if not scan.total_files:
        report["error"] = f"nothing readable under {root} (every file skipped or empty)"
        return report

    try:
        inventory = build_inventory(
            briefing, llm=llm, stopping_rule=stopping_rule, max_nodes=max_nodes
        )
    except Exception as exc:
        report["error"] = f"the inventory model failed: {exc}"
        return report

    if not inventory.concepts:
        report["error"] = (
            f"the model returned no concepts — the briefing may be too thin "
            f"({scan.total_files} files scanned)"
        )
        return report

    gid = graph_id or f"repo_{_slug(root.name)}"
    result = materialize(
        scan,
        inventory,
        graph_id=gid,
        title=title,
        stopping_rule=stopping_rule,
        max_nodes=max_nodes,
        target_days=target_days,
        hours_per_day=hours_per_day,
    )
    graph = result.graph
    by_name = {c.concept.strip().lower(): c for c in inventory.concepts}

    report.update(
        ok=True,
        graph_id=gid,
        title=graph.title,
        concept_count=len(graph.nodes),
        summary=inventory.summary,
        dropped_anchors=result.dropped_anchors,
        dropped_concepts=result.dropped_concepts,
        dropped_prerequisites=result.dropped_prerequisites,
        broken_cycles=result.broken_cycles,
        issues=result.issues,
        anchor_coverage=result.coverage.summary() if result.coverage else None,
        ground_truth=check_ground_truth(inventory),
    )
    report["concepts"] = [
        {
            "id": n.id,
            "concept": n.title,
            "why_needed": getattr(by_name.get(n.title.strip().lower()), "why_needed", "comprehension"),
            "difficulty": getattr(by_name.get(n.title.strip().lower()), "difficulty", None),
            "estimated_hours": n.estimated_hours,
            "content_type": n.content_type,
            "day": n.day,
            "anchors": [{"path": a.path, "symbol": a.symbol, "note": a.note} for a in n.anchors],
            "prerequisites": [
                graph.nodes[p].title for p in n.prerequisites if p in graph.nodes
            ],
            "what_to_cover": n.notes,
        }
        for n in graph.nodes.values()
    ]

    try:
        from orchestrator.memory.roadmap import budget_report, total_hours

        report["total_hours"] = total_hours(graph)
        report["days"] = max((n.day or 0) for n in graph.nodes.values()) if graph.nodes else 0
        report["budget"] = budget_report(graph)
    except Exception as exc:
        report["issues"].append(f"budget report unavailable: {exc}")

    if persist:
        try:
            from orchestrator.config import MENTOR_CURRICULUM_PATH
            from orchestrator.memory.roadmap import validate_roadmap, write_roadmap

            root_curriculum = curriculum_root or MENTOR_CURRICULUM_PATH
            write = write_roadmap(root_curriculum, graph)
            report["persisted"] = True
            report["roadmap_path"] = write.folder
            # The write happened even if the graph is bad, so report both loudly.
            report["issues"].extend(
                str(i)
                for i in validate_roadmap(root_curriculum, gid, verify=False)
                if i.severity == "error"
            )
        except Exception as exc:
            report["error"] = f"persisting the roadmap failed: {exc}"

    return report