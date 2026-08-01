"""
orchestrator/memory/retriever.py

MemoryRetriever — semantic search over the warm and cold memory tiers.

Called exclusively by the orchestrator's long_term_recall_node when
reason_node sets needs_long_term_context=True. Never called on every turn.

Design:
  - Embeds the user query using the same sentence-transformers model used
    during event storage (all-MiniLM-L6-v2, 384-dim).
  - Calls memory_manager.semantic_search() which runs a pgvector cosine-
    distance query restricted to events older than hot_threshold_days and
    not yet archived.
  - Returns a human-readable block of text (not raw dicts) that the
    long_term_recall_node can append directly to the state's summary_text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from orchestrator.memory.store import MemoryManager, _get_embed_model


# How many older memories to surface per RAG call.
_RECALL_LIMIT = 5

# Only search events older than this many days (hot-tier events are already
# in the summarize_node output — no point double-surfacing them).
_HOT_THRESHOLD_DAYS = 14


class MemoryRetriever:
    """
    Semantic search over warm/cold episodic memories.

    Usage:
        retriever = MemoryRetriever(memory_manager)
        block = retriever.recall(user_input="how's my learning been going?")
        # block is a formatted string ready to append to summary_text
    """

    def __init__(self, memory_manager: MemoryManager) -> None:
        self._mm = memory_manager

    def recall(
        self,
        user_input: str,
        limit: int = _RECALL_LIMIT,
        hot_threshold_days: int = _HOT_THRESHOLD_DAYS,
    ) -> Optional[str]:
        """
        Embed user_input, search for the closest episodic memories from the
        warm/cold tiers, and return a formatted text block.

        Returns None if no relevant memories were found (distance too high)
        or if the embedding step fails — callers must handle None gracefully.
        """
        # 1. Embed the query.
        try:
            model = _get_embed_model()
            query_vec = model.encode(user_input, normalize_embeddings=True).tolist()
        except Exception as exc:
            print(f"[MemoryRetriever.recall] embedding failed: {exc}")
            return None

        # 2. Semantic search.
        try:
            results = self._mm.semantic_search(
                query_embedding=query_vec,
                hot_threshold_days=hot_threshold_days,
                limit=limit,
            )
        except Exception as exc:
            print(f"[MemoryRetriever.recall] semantic_search failed: {exc}")
            return None

        if not results:
            return None

        # 3. Filter by distance threshold — cosine distance of 0.5 means
        #    the memory is more different than similar, not worth surfacing.
        relevant = [r for r in results if r["distance"] < 0.5]
        if not relevant:
            return None

        # 4. Format into a clear, labelled text block for the reasoning LLM.
        lines = ["=== RELEVANT OLDER MEMORIES (semantic recall) ==="]
        for mem in relevant:
            occurred = mem.get("occurred_at", "")
            if occurred:
                try:
                    dt = datetime.fromisoformat(occurred)
                    occurred = dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
            event_type = mem.get("event_type", "event")
            content = (mem.get("content") or "").replace("\n", " ")
            if len(content) > 200:
                content = content[:197] + "..."
            lines.append(f"- [{occurred}] ({event_type}): {content}")

        return "\n".join(lines)
