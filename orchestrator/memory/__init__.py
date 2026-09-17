"""
orchestrator/memory/__init__.py

The memory facade — the public surface of the memory system.

Purposes, not tables
--------------------
The stores are an implementation detail. What memory is *for* is five things,
and each purpose gets one reader here:

    P1  read_identity()  who is he, what does he want      ← built
    P2  read_state()     what is true right now            ← built
    P3  read_history()   what happened                     ← built
    P4  read_patterns()  what it means                     ← built
    P5  read_governance() why trust it, and how to fix it  (pending)

Naming convention: the reader is `read_<purpose>()`, never the bare purpose
name. `identity()` would shadow the `identity` *module* in this package (a
function and a submodule cannot share the attribute), which is exactly the kind
of ambiguity this redesign exists to remove.

Callers — the sidecar tools included — ask for a *purpose* and get a shaped
answer; they never reach into a store and assemble rows themselves. That is what
makes "the agent can reach every memory" a property of the design rather than a
pile of per-store plumbing.

Migration note (strangler)
--------------------------
`MemoryManager` is still exported while the purposes are built out behind this
facade. Each purpose migrates its store's callers, and these transitional
exports shrink as it does — they are not the interface, they are the scaffolding.
"""

from orchestrator.memory.history import (
    DailyRecap,
    DayLogEntry,
    HistoryView,
    PastSession,
    read_history,
    render_history,
)
from orchestrator.memory.identity import (
    IdentityFact,
    IdentitySnapshot,
    read_identity,
    render_identity,
)
from orchestrator.memory.patterns import (
    Momentum,
    Pattern,
    PatternsView,
    read_patterns,
    render_patterns,
)
from orchestrator.memory.state import (
    CommittedBlock,
    FreeWindow,
    PresentState,
    read_state,
    render_state,
)
from orchestrator.memory.store import MemoryManager

__all__ = [
    # P1 — identity: who he is, and what he wants.
    "read_identity",
    "render_identity",
    "IdentityFact",
    "IdentitySnapshot",
    # P2 — present state: what is true right now.
    "read_state",
    "render_state",
    "PresentState",
    "CommittedBlock",
    "FreeWindow",
    # P3 — history: what happened.
    "read_history",
    "render_history",
    "HistoryView",
    "PastSession",
    "DailyRecap",
    "DayLogEntry",
    # P4 — patterns: what it means.
    "read_patterns",
    "render_patterns",
    "PatternsView",
    "Pattern",
    "Momentum",
    # Transitional — still reached directly by callers not yet migrated.
    "MemoryManager",
]
