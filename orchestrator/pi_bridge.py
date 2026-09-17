"""
orchestrator/pi_bridge.py

Drive the PI agent core over its **RPC mode** so the web UI talks to the mentor
(PI) instead of the Python LangGraph pipeline. The boundary register's chosen
path: Python stays the only writer of every store; PI owns the conversation.

    Browser ── /api/chat ──> FastAPI ──stdin/stdout JSONL──> pi --mode rpc
                                │                                 │
                                │                            tools: /tools/*
                                └──── memory writes <─────────────┘

Design notes (each one is a real failure mode, not theory):

1. **Framing is LF-only.** PI's RPC doc is explicit: strict JSONL, ``\\n`` is the
   only record delimiter, and clients must not use readers that treat U+2028 /
   U+2029 as newlines (both are legal *inside* JSON strings; Node's ``readline``
   splits on them and corrupts records). So we read raw **bytes** and split on
   ``b"\\n"`` — never ``str.splitlines()``, never ``readline()``.

2. **``agent_end`` is not "the turn is done".** It can be followed by an automatic
   retry, a compaction retry, or a queued continuation. ``agent_settled`` is the
   only event that means nothing further will happen on its own — so that is what
   a turn waits for, and what triggers the Python-side memory work.

3. **``message_end.message`` is authoritative text.** ``message_update.text_delta``
   is display-only: a ``message_end`` mutation (the crisis footer is one) never
   appears in the deltas, so accumulating deltas would silently drop it.

4. **A dead bridge must not lose the conversation.** PI persists every session as
   JSONL, and the PI session id is derived deterministically from the web session
   id, so a crash costs the in-flight turn — not the thread.

Fail-open, like every other tool here: a bridge failure returns a structured
error, and one bad turn never takes the API down.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parent.parent

PI_ENTRY = Path(
    os.getenv("PI_ENTRY", str(ROOT_DIR / "pi" / "packages" / "coding-agent" / "dist" / "bundle" / "cli.js"))
)

# A turn can legitimately take minutes (goal decomposition); don't cut it short.
PI_TURN_TIMEOUT = float(os.getenv("PI_TURN_TIMEOUT_SECONDS", "420"))
PI_COMMAND_TIMEOUT = float(os.getenv("PI_COMMAND_TIMEOUT_SECONDS", "30"))
# Idle web sessions should not hold a Node process forever.
PI_SESSION_IDLE_TTL = float(os.getenv("PI_SESSION_IDLE_TTL_SECONDS", "1800"))
# Cap how much text we mirror into a trace event payload.
_TRACE_TEXT_LIMIT = 4000
_READ_CHUNK = 65536


class PiBridgeError(RuntimeError):
    """Bridge-level failure (process dead, protocol timeout, missing entry)."""


@dataclass
class PiTurnResult:
    """One completed mentor turn, in the shape ``api/main.py`` needs."""

    ok: bool = False
    text: str = ""
    error: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    pipeline: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    session_file: str | None = None
    duration_ms: int = 0
    turns: int = 0


# ---------------------------------------------------------------------------
# Pure helpers — unit-tested without spawning PI
# (scripts/tests/test_pi_bridge.py)
# ---------------------------------------------------------------------------


# PI validates its session id strictly — alphanumerics plus '-', '_' and '.',
# starting and ending alphanumeric — because the id is both a CLI argument and
# part of the session JSONL filename (`{timestamp}_{id}.jsonl`).
_PI_ID_ILLEGAL = re.compile(r"[^A-Za-z0-9._-]")
_PI_ID_EDGE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")


def derive_pi_session_id(web_session_id: str) -> str:
    """A PI-legal session id, whatever alphabet the caller handed us.

    The web path passes UUIDs and lands untouched. A caller that used ':' as a
    separator (the Telegram listener did) produced
    ``mentor-telegram:42:2026-09-16``, which PI rejected outright — Node exited
    and the turn came back as a 502 whose stderr read "Session id must be
    non-empty". So the mapping is enforced at this choke point rather than
    trusting every caller to know PI's alphabet (the same rule the grid applies
    to slot math: the guard lives where the mapping happens, not at each caller).

    Deterministic by construction: one web id always yields one PI id, so a
    respawn still resumes the same session and no mapping table is needed. An id
    that sanitises away entirely falls back to a stable hash rather than "".
    """
    cleaned = _PI_ID_EDGE.sub("", _PI_ID_ILLEGAL.sub("-", f"mentor-{web_session_id}"))
    if not cleaned:
        digest = hashlib.sha1(str(web_session_id).encode("utf-8")).hexdigest()[:16]
        cleaned = f"mentor-{digest}"
    return cleaned


def assistant_text(message: Any) -> str:
    """Text blocks of an assistant ``AgentMessage``; "" for anything else.

    ``message.content`` is normally a list of typed blocks, but a plain string is
    tolerated because that shape has moved between PI versions.
    """
    if not isinstance(message, dict):
        return ""
    if message.get("role") != "assistant":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def block_text(result: Any) -> str:
    """Flatten a tool result (``{content: [{type, text}, ...]}``) into a string."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
    return ""


def events_to_trace(
    events: list[tuple[float, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Map PI's event stream to the web UI's trace contract.

    Returns ``(trace, pipeline, tool_calls)``: trace entries are ``TraceEvent``
    dicts (``kind: "node" | "tool"``), ``pipeline`` is the ordered list of tools the
    mentor actually used, and ``tool_calls`` carries the full args/result records.

    Tool ``ms`` is a real duration (start → end). Node ``ms`` is the elapsed time
    since the previous trace event — PI has no per-node concept, so that is an
    approximation stated as one rather than an invented number.
    """
    trace: list[dict[str, Any]] = []
    pipeline: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    pending: dict[str, tuple[float, dict[str, Any]]] = {}
    state: dict[str, float | None] = {"last_at": None}

    def _node(name: str, at: float, **extra: Any) -> None:
        entry: dict[str, Any] = {"kind": "node", "node": name}
        last = state["last_at"]
        if last is not None:
            entry["ms"] = int(max(0.0, at - last) * 1000)
        entry.update(extra)
        trace.append(entry)
        state["last_at"] = at

    for at, event in events:
        etype = event.get("type")

        if etype == "agent_start":
            _node("agent_start", at, status="started")
        elif etype == "turn_start":
            _node("turn", at, status="started")
        elif etype == "tool_execution_start":
            pending[str(event.get("toolCallId"))] = (at, event.get("args") or {})
        elif etype == "tool_execution_end":
            call_id = str(event.get("toolCallId"))
            started_at, args = pending.pop(call_id, (at, {}))
            name = str(event.get("toolName") or "tool")
            is_error = bool(event.get("isError"))
            result_text = block_text(event.get("result"))
            duration_ms = int(max(0.0, at - started_at) * 1000)
            tool_calls.append(
                {
                    "tool": name,
                    "args": args,
                    "ok": not is_error,
                    "ms": duration_ms,
                    "result": result_text[:_TRACE_TEXT_LIMIT],
                }
            )
            if name not in pipeline:
                pipeline.append(name)
            trace.append(
                {
                    "kind": "tool",
                    "tool": name,
                    "ms": duration_ms,
                    "ok": not is_error,
                    "status": "error" if is_error else "ok",
                    "args": args,
                    "result": result_text[:_TRACE_TEXT_LIMIT] or None,
                    "error": result_text[:500] if is_error else None,
                }
            )
            state["last_at"] = at
        elif etype == "message_end":
            message = event.get("message")
            role = message.get("role") if isinstance(message, dict) else None
            if role == "assistant":
                _node(
                    "assistant_message",
                    at,
                    io={"out": {"text": assistant_text(message)[:_TRACE_TEXT_LIMIT]}},
                )
            elif role == "user":
                _node("user_message", at)
        elif etype == "compaction_start":
            _node("compaction", at, status="started")
        elif etype == "compaction_end":
            _node("compaction", at, status="finished")
        elif etype == "auto_retry_start":
            _node("auto_retry", at, status="started")
        elif etype == "extension_error":
            _node("extension_error", at, status="error", error=str(event.get("error"))[:500])
        elif etype == "agent_settled":
            _node("agent_settled", at, status="settled")

    return trace, pipeline, tool_calls


# ---------------------------------------------------------------------------
# One live PI process per active web session
# ---------------------------------------------------------------------------


def _child_env() -> dict[str, str]:
    """Environment for the PI child: parent env with **.env taking precedence**.

    Parity with ``scripts/run_pi_mentor.sh`` (``set -a; source .env``), which lets
    .env *override* an already-exported variable. Getting this backwards is a silent
    failure, not a cosmetic one: a stale ``NEBIUS_API_KEY`` exported in the shell
    shadows the real one in .env, the provider authenticates with the stale
    credential, and every turn returns ``401 status code (no body)`` — a whole
    session of empty replies with nothing pointing at the environment.
    """
    env = dict(os.environ)
    try:
        from dotenv import dotenv_values

        for key, value in dotenv_values(ROOT_DIR / ".env").items():
            if value is not None:
                env[key] = value  # .env wins — same as `source .env` in bash
    except Exception as exc:  # fail-open: the parent env is usually enough
        print(f"[pi_bridge] .env merge skipped: {exc}")
    env.setdefault("MENTOR_SERVICE_URL", "http://127.0.0.1:8000")
    return env


def _now_iso() -> str:
    """Timestamp in the same format the Python path writes into transcripts."""
    return datetime.now().astimezone().isoformat()


class PiSession:
    """A single PI RPC subprocess bound to one web session id."""

    def __init__(self, web_session_id: str) -> None:
        self.web_session_id = web_session_id
        # Deterministic, so a respawn resumes the same PI session — no mapping
        # table. Sanitised, because PI's alphabet excludes ':' and the id also
        # becomes a filename (see derive_pi_session_id).
        self.pi_session_id = derive_pi_session_id(web_session_id)
        self.session_file: str | None = None
        self.turns: list[dict[str, Any]] = []
        self.last_used: float = time.time()

        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._events: list[tuple[float, dict[str, Any]]] = []
        self._responses: dict[str, dict[str, Any]] = {}
        self._stderr: list[str] = []
        self._closed = False
        self._settled = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Spawn PI in RPC mode. No-op when it is already running."""
        if self.alive:
            return
        if not PI_ENTRY.exists():
            raise PiBridgeError(f"PI entry not found at {PI_ENTRY}. Build it: (cd pi && npm run build)")

        cmd = [
            "node",
            str(PI_ENTRY),
            "-a",  # trust project resources (.pi/settings.json, mentor/ extensions)
            "--mode",
            "rpc",
            "--session-id",
            self.pi_session_id,
        ]
        # Auth is the provider extension's job (`apiKey: "$NEBIUS_API_KEY"`, the
        # documented `$ENV_VAR` interpolation). The bridge only guarantees the
        # environment the interpolation reads from — see `_child_env()`, where the
        # .env-over-shell precedence is what keeps the credential current.
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT_DIR),
                env=_child_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise PiBridgeError(f"failed to spawn PI: {exc}") from exc

        with self._cond:
            self._events = []
            self._responses = {}
            self._closed = False
            self._settled = False
        threading.Thread(target=self._read_stdout, args=(self._proc,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self._proc,), daemon=True).start()

    def _read_stdout(self, proc: subprocess.Popen[bytes]) -> None:
        """Read raw bytes and split records on b"\\n" only (module docstring #1)."""
        stream = proc.stdout
        if stream is None:
            return
        fd = stream.fileno()
        buffer = bytearray()
        while True:
            try:
                chunk = os.read(fd, _READ_CHUNK)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                index = buffer.find(b"\n")
                if index < 0:
                    break
                line = bytes(buffer[:index])
                del buffer[: index + 1]
                if line.endswith(b"\r"):
                    line = line[:-1]
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    print(f"[pi_bridge] unparseable record ({exc}): {line[:200]!r}")
                    continue
                if isinstance(payload, dict):
                    self._on_payload(payload)
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def _read_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        """Keep a bounded stderr tail — PI's extension failures land here."""
        stream = proc.stderr
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr.append(text)
                del self._stderr[:-40]

    # -- protocol ----------------------------------------------------------

    def _on_payload(self, payload: dict[str, Any]) -> None:
        at = time.perf_counter()
        with self._cond:
            self._events.append((at, payload))
            if payload.get("type") == "response":
                request_id = payload.get("id")
                if request_id:
                    self._responses[str(request_id)] = payload
            elif payload.get("type") == "agent_settled":
                self._settled = True
            self._cond.notify_all()

    def _write(self, command: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or not self.alive:
            raise PiBridgeError(self.diagnostics())
        try:
            proc.stdin.write((json.dumps(command) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise PiBridgeError(f"PI stdin write failed: {exc}") from exc

    def diagnostics(self) -> str:
        """Human-readable reason the bridge could not talk to PI."""
        tail = " | ".join(self._stderr[-5:]) or "no stderr"
        code = self._proc.returncode if self._proc is not None else None
        return f"PI process not running (exit={code}, stderr={tail})"

    def request(self, command: dict[str, Any], timeout: float = PI_COMMAND_TIMEOUT) -> dict[str, Any]:
        """Send a command and wait for its correlated response."""
        request_id = str(command.setdefault("id", f"cmd-{uuid4().hex[:12]}"))
        self.start()
        self._write(command)
        deadline = time.time() + timeout
        with self._cond:
            while True:
                response = self._responses.get(request_id)
                if response is not None:
                    return response
                if self._closed:
                    raise PiBridgeError(self.diagnostics())
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise PiBridgeError(f"PI did not answer {command.get('type')} within {timeout}s")
                self._cond.wait(min(remaining, 0.5))

    # -- turns -------------------------------------------------------------

    def prompt(self, text: str, timeout: float | None = None) -> PiTurnResult:
        """Run one mentor turn and wait for ``agent_settled`` (docstring #2).

        Never raises: a protocol/process failure comes back as ``ok=False`` with a
        reason, so one bad turn cannot take the API down.
        """
        if not text or not text.strip():
            return PiTurnResult(ok=False, error="empty prompt")

        started = time.perf_counter()
        self.last_used = started
        try:
            self.start()
        except PiBridgeError as exc:
            return PiTurnResult(ok=False, error=str(exc))

        with self._lock:  # one in-flight prompt per session
            with self._cond:
                self._settled = False
                start_index = len(self._events)
            request_id = f"turn-{uuid4().hex[:12]}"
            try:
                self._write({"id": request_id, "type": "prompt", "message": text})
            except PiBridgeError as exc:
                return PiTurnResult(ok=False, error=str(exc))

            limit = timeout or PI_TURN_TIMEOUT
            deadline = time.time() + limit
            turn_error: str | None = None
            settled = False
            with self._cond:
                while True:
                    if self._settled:
                        settled = True
                        break
                    response = self._responses.get(request_id)
                    if response is not None and not response.get("success", True):
                        turn_error = str(response.get("error") or "PI rejected the prompt")
                        break
                    if self._closed:
                        turn_error = self.diagnostics()
                        break
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        turn_error = f"PI turn exceeded {int(limit)}s and was aborted"
                        break
                    self._cond.wait(min(remaining, 0.5))
                events = list(self._events[start_index:])

        if turn_error and "aborted" in turn_error:
            self._try_abort()

        trace, pipeline, tool_calls = events_to_trace(events)

        # Final text and provider failure in one pass. PI reports an auth/model
        # failure as an assistant message with `stopReason: "error"` and empty
        # content — reporting that as a successful turn would hand the UI a blank
        # reply with no explanation, which is the silent failure this codebase
        # forbids. If PI retried, the *last* assistant message is the one that counts.
        final_text = ""
        provider_error: str | None = None
        for _at, event in events:
            if event.get("type") != "message_end":
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            if message.get("stopReason") == "error":
                provider_error = str(message.get("errorMessage") or "the model call failed")
                final_text = ""
            else:
                candidate = assistant_text(message)
                if candidate:
                    final_text = candidate
        if settled and provider_error:
            turn_error = provider_error
        elif settled and not final_text:
            # Settled with nothing to say is a defect worth surfacing, not a blank bubble.
            turn_error = "the mentor completed the turn without returning any text"

        duration_ms = int((time.perf_counter() - started) * 1000)
        if settled and not turn_error:
            self._refresh_session_file()
            self._record_turn(text, final_text)
            trace.append(
                {
                    "kind": "node",
                    "node": "memory_writeback",
                    "status": "dispatched",
                    "detail": {
                        "engine": "pi_bridge",
                        "what": "episodic turn log + Obsidian daily note + DNA reflection",
                    },
                }
            )

        return PiTurnResult(
            ok=settled and not turn_error,
            text=final_text,
            error=turn_error,
            trace=trace,
            pipeline=pipeline,
            tool_calls=tool_calls,
            session_file=self.session_file,
            duration_ms=duration_ms,
            turns=len(self.turns),
        )

    def _try_abort(self) -> None:
        """Best-effort stop, so a timed-out turn does not keep burning tokens."""
        try:
            self._write({"type": "abort"})
        except PiBridgeError as exc:
            print(f"[pi_bridge] abort failed: {exc}")

    def _refresh_session_file(self) -> None:
        """Learn the PI session JSONL path once (fail-open)."""
        if self.session_file:
            return
        try:
            data = (self.request({"type": "get_state"}, timeout=10).get("data") or {})
            self.session_file = data.get("sessionFile")
        except PiBridgeError as exc:
            print(f"[pi_bridge] get_state failed: {exc}")

    # -- memory parity -----------------------------------------------------

    def _record_turn(self, user_text: str, mentor_text: str) -> None:
        """Python-side writes for one PI turn, mirroring the orchestrator path.

        ``orchestrator/_log_turn`` + the ``reflect_on_turn_async`` dispatch inside
        ``format_output_node`` do three things per turn; the PI path must do the
        same three or the mentor stops remembering turns:
          1. episodic turn log (``log_conversation_turn``)
          2. Obsidian daily-note append
          3. DNA reflection (async, fire-and-forget)
        Fail-open throughout: memory work must never break a turn.
        """
        self.turns.append({"role": "user", "content": user_text, "timestamp": _now_iso()})
        self.turns.append({"role": "mentor", "content": mentor_text, "timestamp": _now_iso()})
        if not mentor_text:
            return

        def _bg() -> None:
            try:
                from orchestrator.memory.store import get_memory_manager

                get_memory_manager().log_conversation_turn(
                    user_input=user_text,
                    response_text=mentor_text,
                    agent_invoked=None,
                    session_id=self.web_session_id,
                )
            except Exception as exc:
                print(f"[pi_bridge] episodic turn log failed: {exc}")
            try:
                from integrations.obsidian_daily import append_mentor_log

                append_mentor_log(
                    content=f"**User:** {user_text[:200]}\n**Mentor:** {mentor_text[:400]}",
                    title="Mentor Turn (pi)",
                )
            except Exception as exc:
                print(f"[pi_bridge] obsidian daily note failed: {exc}")
            try:
                from orchestrator.memory.dna_reflection import reflect_on_turn_async

                reflect_on_turn_async(user_text, mentor_text)
            except Exception as exc:
                print(f"[pi_bridge] dna reflection dispatch failed: {exc}")

        threading.Thread(target=_bg, daemon=True).start()

    # -- session end -------------------------------------------------------

    def persist_transcript(self) -> int:
        """Archive the transcript through the Python path (Python = sole writer).

        Reuses the **public** ``close_active_session`` hook instead of re-implementing
        ``_persist_transcript``, so the PI path produces the same ConversationSession
        row, the same async session summary, and the same boundary rollup into the
        continuous thread as the Python path.

        Phase 4 note: that hook lives in the module the decommission deletes — it
        should move into ``orchestrator/memory/`` first. It is imported lazily here so
        the bridge never becomes an import-time dependency of it.
        """
        transcript = list(self.turns)
        if not transcript:
            return 0
        try:
            from orchestrator.memory.store import get_memory_manager
            from orchestrator.orchestrator import close_active_session

            working_memory = {
                "session_id": self.web_session_id,
                "conversation_history": transcript,
                "session_started_at": transcript[0].get("timestamp"),
                "last_turn_at": transcript[-1].get("timestamp"),
            }
            saved = close_active_session({"working_memory": working_memory}, get_memory_manager())
            self.turns = []
            return int(saved or 0)
        except Exception as exc:  # fail-open: never block a session close
            print(f"[pi_bridge] transcript persistence failed: {exc}")
            return 0

    def close(self) -> None:
        """Stop the PI process. The session JSONL stays on disk — PI owns it."""
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._cond:
            self._closed = True
            self._cond.notify_all()


# ---------------------------------------------------------------------------
# Bridge manager — one live session per web session id, reaped when idle
# ---------------------------------------------------------------------------


class PiBridge:
    """Owns the live PI sessions for one API process."""

    def __init__(self) -> None:
        self._sessions: dict[str, PiSession] = {}
        self._lock = threading.Lock()

    def session(self, web_session_id: str) -> PiSession:
        """The session for this web id, spawning PI lazily on first use."""
        with self._lock:
            session = self._sessions.get(web_session_id)
            if session is None:
                session = PiSession(web_session_id)
                self._sessions[web_session_id] = session
            session.last_used = time.time()
            self._reap_locked()
            return session

    def close_session(self, web_session_id: str) -> int | None:
        """Persist and close a session. ``None`` when that id has no live session."""
        with self._lock:
            session = self._sessions.pop(web_session_id, None)
        if session is None:
            return None
        turns = session.persist_transcript()
        session.close()
        return turns

    def shutdown(self) -> None:
        """Persist and stop every live session (API shutdown)."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions = {}
        for session in sessions:
            session.persist_transcript()
            session.close()

    def _reap_locked(self) -> None:
        """Retire sessions idle past the TTL. Caller holds ``self._lock``."""
        cutoff = time.time() - PI_SESSION_IDLE_TTL
        for session_id in [key for key, value in self._sessions.items() if value.last_used < cutoff]:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session.persist_transcript()
                session.close()
                print(f"[pi_bridge] reaped idle session {session_id}")


_BRIDGE: PiBridge | None = None
_BRIDGE_LOCK = threading.Lock()


def get_pi_bridge() -> PiBridge:
    """Process-wide bridge, one API process."""
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = PiBridge()
        return _BRIDGE