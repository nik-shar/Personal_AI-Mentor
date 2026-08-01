"""
orchestrator/memory/topic_graph.py

Pure-logic module for Topic Graph operations (§5.5 of architecture).

Design rules:
- No database access — every function takes a TopicGraph and returns a new one.
- All graph mutation functions enforce the DAG constraint using networkx.
- "available" and "locked" are computed on read, never stored.
- Raises ValueError on any edit that would introduce a cycle.

Usage:
    from orchestrator.memory.topic_graph import (
        get_available_nodes,
        mark_node_done,
        add_node,
        ...
    )
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import networkx as nx

from schemas.memory import TopicGraph, TopicNode

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Read-only queries
# ---------------------------------------------------------------------------

def _build_nx_graph(graph: TopicGraph) -> nx.DiGraph:
    """Convert a TopicGraph into a networkx DiGraph for analysis."""
    G = nx.DiGraph()
    for node_id in graph.nodes:
        G.add_node(node_id)
    for node_id, node in graph.nodes.items():
        for prereq_id in node.prerequisites:
            # Edge direction: prereq → node (prereq must come before node)
            G.add_edge(prereq_id, node_id)
    return G


def _is_node_available(node: TopicNode, graph: TopicGraph) -> bool:
    """
    A node is available when:
    - It has not been started, skipped, or done (status is None or "not_started")
    - All existing prerequisites in the graph are marked "done"
    """
    if node.status not in (None, "not_started"):
        return False
    for prereq_id in node.prerequisites:
        prereq = graph.nodes.get(prereq_id)
        if prereq is not None and prereq.status != "done":
            return False
    return True


def get_available_nodes(graph: TopicGraph) -> list[TopicNode]:
    """
    Return nodes that are ready to be studied right now:
    - status is not_started or None
    - all prerequisites are done

    These are surfaced by the Daily Planner as candidate plan items.
    """
    return [n for n in graph.nodes.values() if _is_node_available(n, graph)]


def get_locked_nodes(graph: TopicGraph) -> list[TopicNode]:
    """Return nodes that cannot be started yet (unfinished prerequisites)."""
    return [
        n for n in graph.nodes.values()
        if n.status in (None, "not_started") and not _is_node_available(n, graph)
    ]



def get_in_progress_nodes(graph: TopicGraph) -> list[TopicNode]:
    """Return nodes currently being studied."""
    return [n for n in graph.nodes.values() if n.status == "in_progress"]


def get_done_nodes(graph: TopicGraph) -> list[TopicNode]:
    """Return completed nodes."""
    return [n for n in graph.nodes.values() if n.status == "done"]


def topological_order(graph: TopicGraph) -> list[str]:
    """
    Return node IDs in a valid study order (prerequisites before dependents).
    Raises ValueError if the graph contains a cycle (should never happen if
    add_node / add_dependency are used correctly).
    """
    G = _build_nx_graph(graph)
    if not nx.is_directed_acyclic_graph(G):
        raise ValueError("Topic graph contains a cycle — data is inconsistent.")
    return list(nx.topological_sort(G))


def critical_path(graph: TopicGraph) -> list[str]:
    """
    Return the minimum sequence of not-yet-done nodes (by estimated_hours)
    between the current frontier and the final goal nodes (nodes with no
    outgoing edges).

    Uses longest path by estimated_hours to identify the bottleneck chain.
    Returns node IDs in order.
    """
    G = _build_nx_graph(graph)
    if not nx.is_directed_acyclic_graph(G):
        raise ValueError("Topic graph contains a cycle.")

    # Only consider non-done nodes.
    pending = {
        nid for nid, n in graph.nodes.items()
        if n.status not in ("done", "skipped")
    }

    # Subgraph of pending nodes.
    sub = G.subgraph(pending).copy()

    # Add weights.
    for nid in sub.nodes:
        sub.nodes[nid]["weight"] = graph.nodes[nid].estimated_hours

    # Longest path by weight = critical path.
    try:
        path = nx.dag_longest_path(sub, weight="weight")
    except Exception:
        path = list(nx.topological_sort(sub))

    return path


def completion_percentage(graph: TopicGraph) -> float:
    """Return what fraction (0.0–1.0) of nodes are done."""
    total = len(graph.nodes)
    if total == 0:
        return 0.0
    done = sum(1 for n in graph.nodes.values() if n.status == "done")
    return done / total


# ---------------------------------------------------------------------------
# Graph mutation — always return a NEW TopicGraph (immutable-style)
# ---------------------------------------------------------------------------

def _bump_version(graph: TopicGraph) -> TopicGraph:
    """Return a copy with version + 1 and updated_at."""
    return graph.model_copy(update={
        "version": graph.version + 1,
        "updated_at": datetime.now(timezone.utc),
    })


def _assert_dag(graph: TopicGraph) -> None:
    """Raise ValueError if the graph is not a DAG."""
    G = _build_nx_graph(graph)
    if not nx.is_directed_acyclic_graph(G):
        cycle = nx.find_cycle(G)
        raise ValueError(f"Operation would create a cycle: {cycle}")


def mark_node_done(graph: TopicGraph, node_id: str) -> TopicGraph:
    """
    Mark a node as done. Automatically unlocks downstream nodes.
    Raises KeyError if node_id is not in the graph.
    """
    if node_id not in graph.nodes:
        raise KeyError(f"Node '{node_id}' not found in graph.")
    updated_nodes = dict(graph.nodes)
    updated_nodes[node_id] = updated_nodes[node_id].model_copy(
        update={"status": "done"}
    )
    new_graph = graph.model_copy(update={"nodes": updated_nodes})
    return _bump_version(new_graph)


def mark_node_in_progress(graph: TopicGraph, node_id: str) -> TopicGraph:
    """Mark a node as in_progress."""
    if node_id not in graph.nodes:
        raise KeyError(f"Node '{node_id}' not found in graph.")
    updated_nodes = dict(graph.nodes)
    updated_nodes[node_id] = updated_nodes[node_id].model_copy(
        update={"status": "in_progress"}
    )
    new_graph = graph.model_copy(update={"nodes": updated_nodes})
    return _bump_version(new_graph)


def mark_node_skipped(graph: TopicGraph, node_id: str) -> TopicGraph:
    """Mark a node as skipped."""
    if node_id not in graph.nodes:
        raise KeyError(f"Node '{node_id}' not found in graph.")
    updated_nodes = dict(graph.nodes)
    updated_nodes[node_id] = updated_nodes[node_id].model_copy(
        update={"status": "skipped"}
    )
    new_graph = graph.model_copy(update={"nodes": updated_nodes})
    return _bump_version(new_graph)



