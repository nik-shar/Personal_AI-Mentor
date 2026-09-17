"""
scripts/tests/test_pi_bridge.py

Verification suite for the PI RPC bridge (`orchestrator/pi_bridge.py`) — the pure
protocol layer, with **no PI process and no LLM call**.

Covers the documented failure modes the bridge exists to prevent:

  1. Framing        — records split across arbitrary chunk boundaries, CRLF stripped,
                      and U+2028/U+2029 (legal *inside* JSON strings) not treated as
                      newlines. Regression guard for the readline/splitlines trap.
  2. Settled-ness   — a turn is complete only at `agent_settled`; `agent_end` alone
                      (which can be followed by retry/compaction/continuation) must
                      not be mistaken for it.
  3. Authoritative text — final text comes from `message_end.message`, NOT from
                      accumulated `text_delta`s (a `message_end` mutation, e.g. the
                      crisis footer, never appears in the deltas).

Run from the project root:
    uv run python scripts/tests/test_pi_bridge.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.pi_bridge import (
    PiSession,
    assistant_text,
    block_text,
    events_to_trace,
)

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


class _FakeProc:
    """Just enough process for the reader: a pipe fd, no stderr."""

    def __init__(self, fd: int) -> None:
        self.stdout = self
        self.stderr = None
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


def _feed_reader(session: PiSession, chunks: list[bytes]) -> list[dict]:
    """Drive the real byte reader from a pipe fed in the given chunks."""
    read_fd, write_fd = os.pipe()
    try:
        for chunk in chunks:
            os.write(write_fd, chunk)
        os.close(write_fd)
        session._read_stdout(_FakeProc(read_fd))  # type: ignore[arg-type]
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
    return [payload for _at, payload in session._events]


def main() -> None:
    print("\n[1] Framing: chunk boundaries, CRLF, U+2028 / U+2029 safety")
    session = PiSession("framing-session")
    tricky = "line\u2028sep\u2029line"  # legal inside a JSON string
    record_a = json.dumps({"type": "turn_start"}) + "\n"
    record_b = json.dumps({"type": "message_end", "message": {"role": "assistant", "content": tricky}}) + "\n"
    record_c = json.dumps({"type": "agent_settled"}) + "\r\n"

    payloads = _feed_reader(
        session,
        [record_a.encode()[:5], record_a.encode()[5:], record_b.encode(), record_c.encode()],
    )
    check("all three records parsed", len(payloads) == 3, f"got {len(payloads)}")
    check("record split across chunks reassembled", payloads[0].get("type") == "turn_start", str(payloads[:1]))
    check(
        "U+2028 / U+2029 survive inside a JSON string",
        payloads[1].get("message", {}).get("content") == tricky,
        repr(payloads[1].get("message", {}).get("content")),
    )
    check("trailing CR stripped (CRLF accepted)", payloads[2].get("type") == "agent_settled")

    print("\n[2] Settled-ness: agent_settled is the completion signal")
    session = PiSession("settled-session")
    _feed_reader(
        session,
        [
            (json.dumps({"type": "agent_start"}) + "\n").encode(),
            (json.dumps({"type": "agent_end", "messages": [], "willRetry": False}) + "\n").encode(),
        ],
    )
    check("agent_end alone does NOT mark the turn settled", session._settled is False)
    _feed_reader(session, [(json.dumps({"type": "agent_settled"}) + "\n").encode()])
    check("agent_settled marks the turn settled", session._settled is True)

    print("\n[3] Authoritative text comes from message_end, not deltas")
    check(
        "text blocks joined from message_end (tool call block skipped)",
        assistant_text(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "toolCall", "name": "get_profile"},
                    {"type": "text", "text": "world"},
                ],
            }
        )
        == "Hello world",
    )
    check(
        "a crisis footer added at message_end is preserved",
        assistant_text({"role": "assistant", "content": [{"type": "text", "text": "reach out: 988"}]})
        == "reach out: 988",
    )
    check("user messages yield no assistant text", assistant_text({"role": "user", "content": "hi"}) == "")
    check("plain-string content tolerated", assistant_text({"role": "assistant", "content": "plain"}) == "plain")
    check("non-dict input tolerated", assistant_text(None) == "")

    print("\n[4] Tool results flatten to text")
    check(
        "content blocks flattened",
        block_text({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "ab",
    )
    check("string result passes through", block_text("done") == "done")
    check("None result is empty", block_text(None) == "")

    print("\n[5] Event stream → web trace contract")
    events = [
        (0.0, {"type": "agent_start"}),
        (0.1, {"type": "turn_start"}),
        (0.2, {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "get_day_grid", "args": {"date": None}}),
        (
            0.5,
            {
                "type": "tool_execution_end",
                "toolCallId": "c1",
                "toolName": "get_day_grid",
                "result": {"content": [{"type": "text", "text": "free 16:00"}]},
                "isError": False,
            },
        ),
        (
            0.7,
            {
                "type": "message_end",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "You are free at 4pm."}]},
            },
        ),
        (0.8, {"type": "agent_settled"}),
    ]
    trace, pipeline, tool_calls = events_to_trace(events)
    check("every trace entry has kind node|tool", all(e.get("kind") in ("node", "tool") for e in trace))
    tool_entries = [e for e in trace if e.get("kind") == "tool"]
    check("tool event carries the real duration", bool(tool_entries) and tool_entries[0].get("ms") == 300, str(tool_entries))
    check("tool event carries args", tool_entries[0].get("args") == {"date": None}, str(tool_entries[0].get("args")))
    check("tool event carries the result text", tool_entries[0].get("result") == "free 16:00")
    check("successful tool marked ok", tool_entries[0].get("ok") is True and tool_entries[0].get("status") == "ok")
    check("pipeline lists the tool used", pipeline == ["get_day_grid"], str(pipeline))
    check("tool_calls record captured", len(tool_calls) == 1 and tool_calls[0]["tool"] == "get_day_grid")
    check("settled node present", any(e.get("node") == "agent_settled" for e in trace))
    check(
        "assistant text exposed as node io.out",
        any((e.get("io") or {}).get("out", {}).get("text") == "You are free at 4pm." for e in trace),
    )
    check(
        "duplicate tool names are not repeated in the pipeline",
        events_to_trace(
            [
                (0.0, {"type": "tool_execution_start", "toolCallId": "a", "toolName": "get_profile", "args": {}}),
                (0.1, {"type": "tool_execution_end", "toolCallId": "a", "toolName": "get_profile", "result": None, "isError": False}),
                (0.2, {"type": "tool_execution_start", "toolCallId": "b", "toolName": "get_profile", "args": {}}),
                (0.3, {"type": "tool_execution_end", "toolCallId": "b", "toolName": "get_profile", "result": None, "isError": False}),
            ]
        )[1]
        == ["get_profile"],
    )

    print("\n[6] Failing tools are visible, never silently ok")
    trace, _pipeline, tool_calls = events_to_trace(
        [
            (0.0, {"type": "tool_execution_start", "toolCallId": "c2", "toolName": "get_profile", "args": {}}),
            (
                0.1,
                {
                    "type": "tool_execution_end",
                    "toolCallId": "c2",
                    "toolName": "get_profile",
                    "result": {"content": [{"type": "text", "text": "sidecar unreachable"}]},
                    "isError": True,
                },
            ),
        ]
    )
    failing = [e for e in trace if e.get("kind") == "tool"][0]
    check("failed tool marked not-ok", failing.get("ok") is False and failing.get("status") == "error")
    check("failure reason surfaced in error field", "sidecar unreachable" in (failing.get("error") or ""))
    check("tool_calls record keeps ok=False", tool_calls[0]["ok"] is False)

    print("\n[7] Compaction / retry / extension errors are visible in the trace")
    trace, _p, _t = events_to_trace(
        [
            (0.0, {"type": "compaction_start"}),
            (1.0, {"type": "compaction_end"}),
            (1.1, {"type": "auto_retry_start"}),
            (1.2, {"type": "extension_error", "error": "boom"}),
        ]
    )
    names = [e.get("node") for e in trace]
    check("compaction shown", names.count("compaction") == 2, str(names))
    check("auto_retry shown", "auto_retry" in names)
    check(
        "extension_error shown as an error node",
        any(e.get("node") == "extension_error" and e.get("status") == "error" for e in trace),
    )

    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()