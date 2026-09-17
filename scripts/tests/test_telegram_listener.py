"""
scripts/tests/test_telegram_listener.py

Verification suite for the Telegram day-log listener — transport only, and
deliberately offline: a stub session stands in for `requests`, so this suite
never touches Telegram, the sidecar, or a database.

Covers:
  1. Config: token cleanup, allowlist parsing + fallback, service url
  2. Trust: an unknown chat is refused; an empty allowlist refuses everyone
  3. Message extraction: only real text messages pass; edits/stickers do not
  4. Session identity: one per chat, per day
  5. Truncation: a long reply is clipped rather than lost
  6. The ack cursor: round-trip, atomic write, corrupt file -> 0
  7. Polling is fail-open: ok / 409-webhook / 500 / no-JSON / ok:false
  8. The mentor call: success, HTTP error, network failure, empty reply
  9. Sending: plain text (no parse_mode), truncated, failure reported
 10. handle_update: a refused sender is never answered, /start costs no turn, and
     a normal message reaches /api/chat with the right session id then replied to
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations import telegram_listener as tl

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


# ---------------------------------------------------------------------------
# Stubs — a `requests`-shaped session that records what was asked of it
# ---------------------------------------------------------------------------


class StubResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class StubSession:
    """Routes GET (getUpdates) and POST (chat / sendMessage) through responders."""

    def __init__(self, get_response=None, post_response=None) -> None:
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []
        self._get_response = get_response
        self._post_response = post_response

    def get(self, url, params=None, timeout=None):
        self.get_calls.append({"url": url, "params": params, "timeout": timeout})
        if isinstance(self._get_response, Exception):
            raise self._get_response
        if self._get_response is not None:
            return self._get_response
        return StubResponse(200, {"ok": True, "result": []})

    def post(self, url, json=None, timeout=None):
        self.post_calls.append({"url": url, "json": json, "timeout": timeout})
        if isinstance(self._post_response, Exception):
            raise self._post_response
        if callable(self._post_response):
            return self._post_response(url, json)
        if self._post_response is not None:
            return self._post_response
        return StubResponse(200, {"ok": True})

    def posts_to(self, needle: str) -> list[dict]:
        return [call for call in self.post_calls if needle in call["url"]]


def _env(**values) -> None:
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


_SAVED_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_OFFSET_PATH",
    "MENTOR_SERVICE_URL",
)


def main() -> int:
    saved = {key: os.environ.get(key) for key in _SAVED_KEYS}

    try:
        print("\n[1] Config")
        _env(TELEGRAM_BOT_TOKEN="  '123:ABC'  ")
        check("token has quotes and space stripped", tl.bot_token() == "123:ABC", tl.bot_token())

        _env(TELEGRAM_ALLOWED_CHAT_IDS="111, 222", TELEGRAM_CHAT_ID="999")
        check(
            "allowlist is comma-split and trimmed",
            tl.allowed_chat_ids() == {"111", "222"},
            str(tl.allowed_chat_ids()),
        )

        _env(TELEGRAM_ALLOWED_CHAT_IDS=None, TELEGRAM_CHAT_ID="'999'")
        check("falls back to TELEGRAM_CHAT_ID", tl.allowed_chat_ids() == {"999"}, str(tl.allowed_chat_ids()))

        _env(TELEGRAM_ALLOWED_CHAT_IDS=None, TELEGRAM_CHAT_ID=None)
        check("no allowlist configured -> empty set", tl.allowed_chat_ids() == set(), str(tl.allowed_chat_ids()))

        _env(MENTOR_SERVICE_URL="http://127.0.0.1:8000/")
        check("service url loses its trailing slash", tl.service_url() == "http://127.0.0.1:8000", tl.service_url())

        print("\n[2] Trust — the allowlist is the only way in")
        check("a listed chat is allowed", tl.is_allowed("111", {"111", "222"}))
        check("an unlisted chat is refused", not tl.is_allowed("333", {"111"}))
        check("int and str chat ids both match", tl.is_allowed(111, {"111"}))
        check("an EMPTY allowlist refuses everyone", not tl.is_allowed("111", set()))

        print("\n[3] Message extraction")
        good = {"update_id": 1, "message": {"chat": {"id": 42}, "text": "  waking up  "}}
        check(
            "a text message yields (chat_id, text)",
            tl.extract_message(good) == ("42", "waking up"),
            str(tl.extract_message(good)),
        )
        check(
            "an edit is ignored",
            tl.extract_message({"edited_message": {"chat": {"id": 42}, "text": "x"}}) is None,
        )
        check("a sticker (no text) is ignored", tl.extract_message({"message": {"chat": {"id": 42}, "sticker": {}}}) is None)
        check("blank text is ignored", tl.extract_message({"message": {"chat": {"id": 42}, "text": "   "}}) is None)
        check("a missing chat is ignored", tl.extract_message({"message": {"text": "hi"}}) is None)
        check("a non-message update is ignored", tl.extract_message({"update_id": 9}) is None)

        print("\n[4] Session identity — one per chat, per day")
        check(
            "shape",
            tl.session_id_for("42", "2031-05-10") == "telegram-42-2031-05-10",
            tl.session_id_for("42", "2031-05-10"),
        )
        check("two chats never share a session", tl.session_id_for("42", "d") != tl.session_id_for("43", "d"))
        check("a negative group id still yields an id", tl.session_id_for("-100123", "d") == "telegram--100123-d", tl.session_id_for("-100123", "d"))
        check("two days never share a session", tl.session_id_for("42", "d1") != tl.session_id_for("42", "d2"))
        check("a stamp is derived by default", tl.session_id_for("42").startswith("telegram-42-"), tl.session_id_for("42"))

        print("\n[4b] The session id must be PI-legal (the live 502)")
        import re as _re

        pi_ok = _re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
        for shape in (tl.session_id_for("42"), tl.session_id_for("-100123", "2031-05-10")):
            check(f"{shape} is a legal PI session id", bool(pi_ok.match(f"mentor-{shape}")), shape)
        check("the id contains no colon", ":" not in tl.session_id_for("42"), tl.session_id_for("42"))

        print("\n[5] Truncation")
        check("a short reply is untouched", tl.truncate_for_telegram("hello") == "hello")
        check("an empty reply is made explicit", tl.truncate_for_telegram("") == "(no reply)")
        clipped = tl.truncate_for_telegram("x" * (tl.TELEGRAM_MAX_MESSAGE_CHARS + 500))
        check(
            "a long reply is clipped to Telegram's limit",
            len(clipped) <= tl.TELEGRAM_MAX_MESSAGE_CHARS,
            str(len(clipped)),
        )
        check("the clipping is visible", "truncated" in clipped)

        print("\n[6] The ack cursor")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "offset.json"
            check("a missing file reads as 0", tl.load_offset(path) == 0)
            tl.save_offset(path, 4242)
            check("round-trips", tl.load_offset(path) == 4242, str(tl.load_offset(path)))
            check("no temp file is left behind", not path.with_suffix(path.suffix + ".tmp").exists())
            path.write_text("{not json", encoding="utf-8")
            check("a corrupt file reads as 0 rather than raising", tl.load_offset(path) == 0)
            _env(TELEGRAM_OFFSET_PATH=str(Path(tmp) / "custom.json"))
            check("the path is configurable", tl.offset_path() == Path(tmp) / "custom.json", str(tl.offset_path()))
        _env(TELEGRAM_OFFSET_PATH=saved["TELEGRAM_OFFSET_PATH"])

        print("\n[7] Polling is fail-open")
        token = "tok"

        s = StubSession(get_response=StubResponse(200, {"ok": True, "result": [{"update_id": 7}]}))
        updates = tl.poll_updates(s, token, 0)
        check("a good poll returns the updates", len(updates) == 1 and updates[0]["update_id"] == 7, str(updates))
        check(
            "the request carries the offset and a long-poll timeout",
            s.get_calls[0]["params"]["offset"] == 0 and "timeout" in s.get_calls[0]["params"],
            str(s.get_calls[0]["params"]),
        )
        check(
            "the HTTP timeout exceeds the poll timeout (an idle poll is not a failure)",
            s.get_calls[0]["timeout"] > tl.DEFAULT_POLL_TIMEOUT,
            str(s.get_calls[0]["timeout"]),
        )

        check(
            "409 (a webhook is still registered) -> []",
            tl.poll_updates(StubSession(get_response=StubResponse(409, {"ok": False})), token, 0) == [],
        )
        check(
            "500 -> []",
            tl.poll_updates(StubSession(get_response=StubResponse(500, None, text="boom")), token, 0) == [],
        )
        check(
            "ok:false -> []",
            tl.poll_updates(StubSession(get_response=StubResponse(200, {"ok": False, "description": "nope"})), token, 0) == [],
        )
        check(
            "unparseable JSON -> []",
            tl.poll_updates(StubSession(get_response=StubResponse(200, None, text="<html>")), token, 0) == [],
        )
        check(
            "a network exception -> []",
            tl.poll_updates(StubSession(get_response=RuntimeError("dns")), token, 0) == [],
        )
        check(
            "a non-list result -> []",
            tl.poll_updates(StubSession(get_response=StubResponse(200, {"ok": True, "result": {"nope": 1}})), token, 0) == [],
        )

        print("\n[8] The mentor call")
        _env(MENTOR_SERVICE_URL="http://sidecar:8000")
        s = StubSession(post_response=lambda url, payload: StubResponse(200, {"response_text": "  Logged 09:00.  "}))
        ok, reply = tl.ask_mentor(s, "waking up", "telegram-42-2031-05-10")
        check("a good turn returns the reply, trimmed", ok and reply == "Logged 09:00.", reply)
        check("it posts to /api/chat", bool(s.posts_to("/api/chat")), str([c["url"] for c in s.post_calls]))
        check(
            "the message and session id are passed through",
            s.posts_to("/api/chat")[0]["json"] == {"message": "waking up", "session_id": "telegram-42-2031-05-10"},
            str(s.posts_to("/api/chat")[0]["json"]),
        )

        s = StubSession(post_response=StubResponse(502, {"detail": "the mentor core did not complete the turn"}))
        ok, reply = tl.ask_mentor(s, "x", "sid")
        check("an HTTP error is a failure, not an answer", ok is False, reply)
        check("and it says plainly that nothing was recorded", "Nothing was recorded" in reply, reply)

        s = StubSession(post_response=RuntimeError("connection refused"))
        ok, reply = tl.ask_mentor(s, "x", "sid")
        check("a network failure is returned, never raised", ok is False and "couldn't reach" in reply, reply)

        s = StubSession(post_response=lambda url, payload: StubResponse(200, {"response_text": "   "}))
        ok, reply = tl.ask_mentor(s, "x", "sid")
        check("an empty reply is a failure, never a silent success", ok is False and "empty" in reply, reply)

        print("\n[9] Sending")
        s = StubSession()
        check("a successful send returns True", tl.send_message(s, "tok", 42, "hi") is True)
        sent = s.posts_to("/sendMessage")
        check("it posts to sendMessage", bool(sent), str([c["url"] for c in s.post_calls]))
        check(
            "plain text — no parse_mode to break on unescaped Markdown",
            "parse_mode" not in (sent[0]["json"] or {}),
            str(sent[0]["json"]),
        )
        check("the chat id is passed through untouched", sent[0]["json"]["chat_id"] == 42)

        s = StubSession()
        tl.send_message(s, "tok", 42, "x" * 9000)
        check(
            "an oversized reply is truncated before sending",
            len(s.posts_to("/sendMessage")[0]["json"]["text"]) <= tl.TELEGRAM_MAX_MESSAGE_CHARS,
        )
        check(
            "a rejected send returns False",
            tl.send_message(StubSession(post_response=StubResponse(400, None, text="bad")), "tok", 42, "hi") is False,
        )
        check(
            "a network failure on send returns False",
            tl.send_message(StubSession(post_response=RuntimeError("down")), "tok", 42, "hi") is False,
        )

        print("\n[10] handle_update")

        def _chat_reply(text: str):
            """Answers /api/chat with `text`; accepts Telegram sends."""

            def responder(url: str, payload):
                if url.endswith("/api/chat"):
                    return StubResponse(200, {"session_id": "s", "response_text": text})
                return StubResponse(200, {"ok": True})

            return responder

        allow = {"42"}

        def msg(text: str) -> dict:
            return {"update_id": 5, "message": {"chat": {"id": 42}, "text": text}}

        s = StubSession(post_response=_chat_reply("should not happen"))
        handled = tl.handle_update(
            s, {"update_id": 5, "message": {"chat": {"id": 999}, "text": "hi"}}, "tok", allow
        )
        check("a refused sender is acked", handled is True)
        check("a refused sender gets no reply", not s.post_calls, str([c["url"] for c in s.post_calls]))
        check("a refused sender never reaches the mentor", not s.posts_to("/api/chat"))

        s = StubSession(post_response=_chat_reply("mentor reply"))
        tl.handle_update(s, msg("/start"), "tok", allow)
        check("/start is answered", bool(s.posts_to("/sendMessage")))
        check("/start costs no mentor turn", not s.posts_to("/api/chat"), str([c["url"] for c in s.post_calls]))
        check(
            "the orientation is the local message",
            s.posts_to("/sendMessage")[0]["json"]["text"] == tl.START_MESSAGE,
        )

        s = StubSession(post_response=_chat_reply("Logged 09:12 wake."))
        handled = tl.handle_update(s, msg("waking up"), "tok", allow)
        check("a normal message is acked", handled is True)
        chat = s.posts_to("/api/chat")
        check("it reaches the mentor", bool(chat))
        check(
            "with today's session for this chat",
            bool(chat) and chat[0]["json"]["session_id"].startswith("telegram-42-"),
            str(chat[0]["json"]["session_id"]) if chat else "",
        )
        check(
            "the text is passed through verbatim",
            bool(chat) and chat[0]["json"]["message"] == "waking up",
            str(chat[0]["json"]["message"]) if chat else "",
        )
        replies = s.posts_to("/sendMessage")
        check(
            "the mentor's reply is sent back",
            bool(replies) and replies[0]["json"]["text"] == "Logged 09:12 wake.",
            str(replies[0]["json"]["text"]) if replies else "",
        )

        s = StubSession(post_response=StubResponse(503, {"detail": "bridge down"}))
        handled = tl.handle_update(s, msg("starting learning"), "tok", allow)
        check("a failed turn is acked, so the queue never wedges", handled is True)
        replies = s.posts_to("/sendMessage")
        check(
            "the failure is told plainly, never swallowed",
            bool(replies) and "Nothing was recorded" in replies[0]["json"]["text"],
            str(replies[0]["json"]["text"]) if replies else "",
        )

        s = StubSession(post_response=_chat_reply("x"))
        check("a non-text update is acked", tl.handle_update(s, {"update_id": 6}, "tok", allow) is True)
        check("...and nothing is sent", not s.post_calls, str(s.post_calls))

        print("=" * 60)
        print(f"RESULT: {_passed} passed, {_failed} failed")
        print("=" * 60)
        return 0 if _failed == 0 else 1
    finally:
        for key, value in saved.items():
            _env(**{key: value})

if __name__ == "__main__":
    raise SystemExit(main())
