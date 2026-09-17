"""
orchestrator/tracing.py

Observability for the mentor pipeline: debug latency, context formation, and
exactly where a turn fails.

Two complementary outputs:

  1. LangSmith (cloud, when credentials are present).
     - setup_tracing() enables LangChain's built-in LangSmith tracer BEFORE any
       chat model is built, so every LLM .invoke() (reasoner, synthesizer, agent
       scoring, request parse, DNA reflection, ...) is auto-traced with prompts,
       outputs and latency.
     - Each manual orchestration span is also mirrored as a LangSmith run
       (best-effort, guarded) so the whole pipeline tree lands in one project.

  2. Local spans (always available, no network).
     - Every orchestration seam records a span with wall-clock time and an
       ok/error status; errors carry the exception string so you can see which
       step of the pipeline failed.
     - MENTOR_DEBUG=1 prints a compact per-turn trace to the terminal.

Everything is fail-open: if LangSmith isn't configured or a post fails, tracing
silently degrades — it must never break a mentor turn.
"""

from __future__ import annotations

import functools
import os
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, Optional

_LOCAL = threading.local()
_CONFIGURED: bool = False


# ---------------------------------------------------------------------------
# Setup — enable LangChain's LangSmith tracer (auto-logs every LLM call)
# ---------------------------------------------------------------------------

def setup_tracing() -> bool:
    """
    Enable LangSmith-backed tracing if credentials are present. Idempotent.

    - Reads LANGSMITH_API_KEY (or LANGCHAIN_API_KEY).
    - Forces LANGCHAIN_TRACING_V2=true so LangChain's built-in tracer captures
      EVERY LLM call (full prompts, outputs, token usage, latency, model) and
      nests it under the manual component runs opened by ``component()`` /
      ``component_span()`` — giving a proper run tree in the LangSmith UI.
    - Set MENTOR_TRACING=off to explicitly disable even when a key is present.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return bool(os.getenv("LANGCHAIN_TRACING_V2"))
    _CONFIGURED = True

    if os.getenv("MENTOR_TRACING", "on").strip().lower() in ("off", "0", "false", "no"):
        return False

    known_key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    if not known_key:
        if os.getenv("MENTOR_DEBUG"):
            print("[tracing] no LangSmith credentials — using local latency traces.")
        return False

    # Bridge whichever key the user set so both LangChain's tracer and the
    # langsmith Client read it.
    os.environ["LANGSMITH_API_KEY"] = known_key
    os.environ["LANGCHAIN_API_KEY"] = known_key
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ.setdefault(
        "LANGCHAIN_PROJECT",
        os.getenv("LANGCHAIN_TRACING_PROJECT") or "ai-mentor",
    )
    print(
        f"[tracing] LangSmith enabled → project '{os.environ['LANGCHAIN_PROJECT']}'. "
        "Every LLM call is auto-traced in detail."
    )
    return True


def tracing_enabled() -> bool:
    return os.getenv("LANGCHAIN_TRACING_V2", "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Component runs — the run-tree mechanism
#
# ``component()`` / ``component_span()`` open a named LangSmith run. Because
# langsmith's ``trace`` context sets the parent-run-tree contextvar, any
# LangChain LLM call made *inside* the block is auto-captured as a CHILD run
# (with full prompt/output/tokens/latency). The result in the LangSmith UI is
# a tree like:
#
#     mentor_turn  (session metadata)
#     ├── reasoner            └── ChatOpenAI
#     ├── direct_response     └── tool_loop → ChatOpenAI
#     ├── agent:job_hunter    └── ChatOpenAI (parse/tailor/score...)
#     ├── synthesizer         └── ChatOpenAI
#     └── dna_reflection      └── ChatOpenAI
#
# Both are fail-open: when tracing is disabled/unavailable they are transparent
# no-ops and never raise into a mentor turn.
# ---------------------------------------------------------------------------


def _open_trace(name: str, run_type: str, tags: Optional[list], metadata: Optional[dict]):
    """
    Enter a langsmith ``trace`` run context (low-level, fail-open).

    Returns ``(ctx, run)`` after successfully entering the run — or
    ``(None, None)`` when tracing is off, langsmith is unavailable, or the
    run cannot be opened. Never raises into the caller.
    """
    if not tracing_enabled():
        return None, None
    try:
        from langsmith import trace as _ls_trace
    except Exception:
        return None, None
    try:
        ctx = _ls_trace(
            name,
            run_type=run_type,
            tags=tags or [],
            metadata=metadata or {},
        )
        run = ctx.__enter__()
        return ctx, run
    except Exception as exc:
        if os.getenv("MENTOR_DEBUG"):
            print(f"[tracing] failed to open run '{name}': {exc}")
        return None, None


def _close_trace(ctx, name: str) -> None:
    """Exit a trace context without ever raising (fail-open)."""
    if ctx is None:
        return
    try:
        ctx.__exit__(*sys.exc_info())
    except Exception as exc:
        if os.getenv("MENTOR_DEBUG"):
            print(f"[tracing] failed to close run '{name}': {exc}")


@contextmanager
def component(name: str, run_type: str = "chain", tags: Optional[list] = None, metadata: Optional[dict] = None):
    """
    Open a LangSmith run as a context so every LLM call made inside it is
    captured in detail as a CHILD run of this component (a proper run tree).

        with component("reasoner", tags=["component:reasoner"]):
            llm.invoke(...)   # auto-traced, nested under 'reasoner'

    Fail-open: when tracing is disabled/unavailable this is a transparent no-op.
    """
    ctx, rt = _open_trace(name, run_type, tags, metadata)
    if ctx is None:
        yield None
        return
    try:
        yield rt
    finally:
        _close_trace(ctx, name)


def component_span(name: str, run_type: str = "chain", tags: Optional[list] = None, metadata: Optional[dict] = None):
    """
    Decorator form of component(): runs the whole function inside a named
    LangSmith run so its LLM calls nest under it.

        @component_span("reasoner", tags=["component:reasoner"])
        def run_reasoner(summary_text): ...

    Fail-open: when tracing is disabled/unavailable the function runs normally.
    """
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx, _ = _open_trace(name, run_type, tags, metadata)
            if ctx is None:
                return fn(*args, **kwargs)
            try:
                return fn(*args, **kwargs)
            finally:
                _close_trace(ctx, name)
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Per-turn span recorder
# ---------------------------------------------------------------------------

class TurnTracer:
    """Collects the manual orchestration spans for one mentor turn."""

    def __init__(self, name: str = "mentor_turn", inputs: Optional[dict] = None) -> None:
        self.name = name
        self.inputs = inputs or {}
        self.started = time.time()
        self.finished: Optional[float] = None
        self.status = "ok"
        self.error: Optional[str] = None
        self.spans: list[dict[str, Any]] = []

    def record(
        self,
        step: str,
        run_type: str,
        started: float,
        status: str,
        error: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> None:
        self.spans.append({
            "step": step,
            "run_type": run_type,
            "secs": round(time.time() - started, 4),
            "status": status,
            "error": error,
            "meta": meta or {},
        })

    def finish(self, status: str = "ok", error: Optional[str] = None) -> None:
        self.finished = time.time()
        self.status = status
        self.error = error
        if self.error:
            self.spans.append({
                "step": "~turn~",
                "run_type": "chain",
                "secs": round(self.finished - self.started, 4),
                "status": status,
                "error": error,
                "meta": {},
            })

    def total_secs(self) -> float:
        end = self.finished or time.time()
        return round(end - self.started, 4)

    def report(self) -> str:
        lines = [f"⏱️  mentor_turn  {self.total_secs()}s  [{self.status}]"]
        if self.error:
            lines.append(f"   ✗ {self.error}")
        for sp in self.spans:
            flag = "✓" if sp["status"] == "ok" else "✗"
            err = f"  {sp['error']}" if sp.get("error") else ""
            lines.append(f"   {flag} {sp['step']:<22} {sp['secs']:>7}s{err}")
        return "\n".join(lines)

    def post(self) -> None:
        """Push this turn as one LangSmith run. Never raises."""
        if not tracing_enabled():
            return
        try:
            from langsmith import Client
            Client().create_run(
                name=self.name,
                run_type="chain",
                inputs=self.inputs,
                outputs={"status": self.status, "spans": self.spans},
                start_time=self.started,
                end_time=self.finished or time.time(),
                error=self.error,
                tags=["mentor_turn"],
                extra={"data": {"mentor_turn_spans": self.spans}},
            )
        except Exception as exc:  # fail-open
            if os.getenv("MENTOR_DEBUG"):
                print(f"[tracing] LangSmith post failed (ignored): {exc}")


# ---------------------------------------------------------------------------
# Thread-bound "current turn" + decorator for orchestration seams
# ---------------------------------------------------------------------------

_ENABLE_LOCAL_REPORT = os.getenv("MENTOR_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")


def begin_turn(name: str = "mentor_turn", inputs: Optional[dict] = None) -> None:
    """Push a TurnTracer onto this thread."""
    _LOCAL.current = TurnTracer(name, inputs)
    _LOCAL.keep_after_turn = False


def current_tracer() -> Optional[TurnTracer]:
    return getattr(_LOCAL, "current", None)


def end_turn(status: str = "ok", error: Optional[str] = None) -> Optional[str]:
    """Finalize the current turn's tracer; returns its local report (or None).

    The LangSmith side of a turn is handled by the ``component("mentor_turn")``
    wrapper in ``run_orchestrator_turn`` — it opens the run BEFORE the graph
    runs so every LLM call nests under it. This function only finalizes the
    local latency report (MENTOR_DEBUG)."""
    t: Optional[TurnTracer] = getattr(_LOCAL, "current", None)
    if t is None:
        return None
    t.finish(status, error)
    report = t.report()
    del _LOCAL.current
    return report


def traced(step_name: str, run_type: str = "chain"):
    """Decorator: record a step's latency + ok/error into the current turn."""
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t: Optional[TurnTracer] = getattr(_LOCAL, "current", None)
            if t is None:
                return fn(*args, **kwargs)
            started = time.time()
            try:
                out = fn(*args, **kwargs)
                t.record(step_name, run_type, started, "ok")
                return out
            except Exception as exc:
                t.record(step_name, run_type, started, "error", str(exc))
                raise
        return wrapper
    return deco


@contextmanager
def measure(step_name: str, run_type: str = "chain"):
    """Context form of traced(): time a block and record ok/error."""
    t: Optional[TurnTracer] = getattr(_LOCAL, "current", None)
    started = time.time()
    try:
        yield t
    except Exception as exc:
        if t is not None:
            t.record(step_name, run_type, started, "error", str(exc))
        raise
    else:
        if t is not None:
            t.record(step_name, run_type, started, "ok")