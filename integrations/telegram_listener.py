"""
integrations/telegram_listener.py

Inbound Telegram → the mentor, and the reply back out again.

Transport only. This module holds no mentor logic, opens no database, and writes
nothing. It long-polls Telegram, POSTs each message to the local ``/api/chat``, and
sends the answer back. Everything that produces the answer — tools, memory,
guardrails — happens inside that turn, exactly as it does for the web dashboard.
That is the point: one chat path, one response contract, one place the engine
switch lives. ``MENTOR_CHAT_ENGINE=python`` still serves Telegram.

Why long polling, not a webhook
-------------------------------
A webhook needs Telegram to reach a PUBLIC https endpoint, which means a tunnel
and an internet-reachable service that can write to his calendar and memory. Long
polling pulls instead: outbound HTTPS only, no inbound port, no public surface —
the only transport consistent with "local-first, privacy by default". It also
needs no SDK: one GET, and ``requests`` is already a dependency of this repo.

The messages themselves still transit Telegram's cloud. The *data* stays local
(Postgres); the transport does not. That is a real tradeoff, and it is why this is
built for transition logs — "starting my learning" — rather than a diary.

Why a phone at all
------------------
The day log is only worth anything if it is cheap to write at the moment it
matters, and that moment is never at a terminal. A one-line message between two
activities is the lowest-friction record that still carries a timestamp — and the
timestamp is what turns a stream of one-liners into durations.

Idempotence
-----------
Telegram redelivers an update until it is acked by advancing ``offset``, so a crash
between recording and acking can deliver the same message twice. The offset is
therefore persisted AFTER a successful turn — at-least-once, so nothing is ever
silently dropped — and the duplicate is absorbed by the writer: ``log_day_event``
reports a repeat instead of logging it. Whoever owns the store owns the invariant;
this module must not try to be clever about it.

Fail-open, like everything else here: a network error, a malformed update, or a
dead sidecar must never kill the loop. It backs off, keeps listening, and says so
on stdout — because silence that looks like health is the failure mode this exists
to avoid.

Run:
    uv run python -m integrations.telegram_listener
    scripts/run_telegram_listener.sh

Requires (in .env): TELEGRAM_BOT_TOKEN, and TELEGRAM_ALLOWED_CHAT_IDS (or
TELEGRAM_CHAT_ID). The allowlist is mandatory — with none configured the listener
refuses every message and says so at startup.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

TELEGRAM_API = "https://api.telegram.org"

# Telegram's own hard limit for a bot message.
TELEGRAM_MAX_MESSAGE_CHARS = 4096

# getUpdates long-poll: how long Telegram should hold the connection when there is
# nothing to deliver. The HTTP timeout must exceed it, or every idle poll looks
# like a network failure.
DEFAULT_POLL_TIMEOUT = 30.0

# A mentor turn can legitimately take minutes (goal decomposition), so the chat
# call is patient by default — the same reasoning as PI_TURN_TIMEOUT_SECONDS.
DEFAULT_CHAT_TIMEOUT = 420.0

# Backoff ceiling for repeated network failures. Never give up: a listener that
# exits is a listener that silently loses logs.
BACKOFF_MAX_SECONDS = 60.0

START_MESSAGE = (
    "Day log is live. Tell me what you're doing as you do it — "
    '"waking up", "starting my learning", "off to lunch", "going to sleep" — '
    "and I'll keep the record."
)


# ---------------------------------------------------------------------------
# Configuration — read at call time, never cached at import (tests mutate it)
# ---------------------------------------------------------------------------


def bot_token() -> str:
    """The bot token, with the quotes people paste from config files stripped."""
    raw = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    return raw.strip("'").strip('"')


def allowed_chat_ids() -> set[str]:
    """
    The chat ids allowed to talk to the mentor.

    TELEGRAM_ALLOWED_CHAT_IDS wins (comma-separated, so a second device can be
    added); TELEGRAM_CHAT_ID is the fallback because it is the one already set.
    Deliberately no default: an empty allowlist trusts nobody, because a stranger
    who finds the bot could make the mentor write to his calendar and memory.
    """
    raw = (os.getenv("TELEGRAM_ALLOWED_CHAT_IDS") or "").strip()
    if not raw:
        raw = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    return {part.strip().strip("'").strip('"') for part in raw.split(",") if part.strip()}


def service_url() -> str:
    """The local sidecar. The same variable the PI tool bridge uses."""
    return (os.getenv("MENTOR_SERVICE_URL") or "http://127.0.0.1:8000").rstrip("/")


def poll_timeout() -> float:
    try:
        return max(1.0, float(os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "")))
    except (TypeError, ValueError):
        return DEFAULT_POLL_TIMEOUT


def chat_timeout() -> float:
    try:
        return max(5.0, float(os.getenv("TELEGRAM_CHAT_TIMEOUT_SECONDS", "")))
    except (TypeError, ValueError):
        return DEFAULT_CHAT_TIMEOUT


def offset_path() -> Path:
    """Where the ack cursor lives. Defaults into the gitignored runtime folder."""
    raw = (os.getenv("TELEGRAM_OFFSET_PATH") or "").strip()
    return Path(raw) if raw else ROOT_DIR / "data" / "telegram_offset.json"


# ---------------------------------------------------------------------------
# The ack cursor
# ---------------------------------------------------------------------------


def load_offset(path: Path) -> int:
    """The update_id to ask from next — i.e. last handled + 1. 0 when unset."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(data.get("offset") or 0))
    except Exception:
        return 0


def save_offset(path: Path, offset: int) -> None:
    """
    Persist the cursor atomically.

    Written to a temp file and moved into place: a crash mid-write must not leave
    a truncated file that reads as offset 0, because that would re-deliver — and
    therefore re-log — everything Telegram still has queued.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"offset": int(offset)}), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        print(f"[telegram] could not persist offset (a restart may re-deliver): {exc}")


# ---------------------------------------------------------------------------
# Pure helpers — the parts worth testing without a network
# ---------------------------------------------------------------------------


def is_allowed(chat_id: Any, allowlist: set[str]) -> bool:
    """Exact match against the allowlist. Empty allowlist → nobody."""
    if not allowlist:
        return False
    return str(chat_id).strip() in allowlist


def extract_message(update: dict[str, Any]) -> Optional[tuple[str, str]]:
    """
    (chat_id, text) for a usable text message, else None.

    Only plain ``message`` updates count. Edits are ignored on purpose: a day log
    is a record of what happened when he sent it, and silently rewriting history
    from an edit would make the record argue with itself. Anything else Telegram
    sends (joins, stickers, button taps) is dropped rather than guessed at.
    """
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return str(chat["id"]), text.strip()


def session_id_for(chat_id: Any, stamp: Optional[str] = None) -> str:
    """
    The mentor session for one chat, per day.

    Per-day rather than one endless thread, deliberately: this conversation exists
    to describe a single daycycle, and a fresh session at the date boundary gives
    the mentor a clean "today" to reason about — and stops a year of logs from
    growing into one enormous context. The chat id keeps two devices apart.

    Separators are '-' rather than ':'. This id is handed to the PI bridge, which
    prefixes it and passes it to PI as --session-id; PI accepts only
    alphanumerics plus '-', '_' and '.', and the id also becomes part of the
    session filename. A colon failed both — the first live message came back as a
    502 because of it.
    """
    if not stamp:
        try:
            from orchestrator.config import today_local

            stamp = today_local().isoformat()
        except Exception:
            stamp = time.strftime("%Y-%m-%d")
    return f"telegram-{chat_id}-{stamp}"


def truncate_for_telegram(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> str:
    """
    Trim a reply to what Telegram will accept.

    The mentor is asked to keep log replies to one line, so this should never
    fire in practice — it exists so that a long answer degrades into a clipped
    one instead of failing to send at all, which would look like the mentor went
    silent.
    """
    body = (text or "").strip()
    if not body:
        return "(no reply)"
    if len(body) <= limit:
        return body
    marker = "\n… (truncated — ask me to continue)"
    return body[: max(0, limit - len(marker))] + marker


# ---------------------------------------------------------------------------
# Telegram + sidecar calls (a `session` is injected so tests need no network)
# ---------------------------------------------------------------------------


def _api_url(token: str, method: str) -> str:
    return f"{TELEGRAM_API}/bot{token}/{method}"


def poll_updates(
    session: Any,
    token: str,
    offset: int,
    timeout: float = DEFAULT_POLL_TIMEOUT,
) -> list[dict[str, Any]]:
    """
    Long-poll for new updates. Returns [] on any failure (never raises).

    A 409 means a webhook is still registered — Telegram refuses getUpdates while
    one is set — so it is called out explicitly rather than buried in a generic
    error, because the fix is one command and the symptom is otherwise silent.
    """
    url = _api_url(token, "getUpdates")
    params = {"timeout": int(timeout), "offset": int(offset), "allowed_updates": '["message"]'}
    try:
        response = session.get(url, params=params, timeout=timeout + 10.0)
    except Exception as exc:
        print(f"[telegram] poll failed: {exc}")
        return []

    if response.status_code == 409:
        print(
            "[telegram] Telegram refused getUpdates (409): a webhook is still "
            "registered for this bot. Remove it with:\n"
            f"    curl -s \"{_api_url('<' + 'token' + '>', 'deleteWebhook')}\""
        )
        return []
    if response.status_code != 200:
        print(f"[telegram] poll returned HTTP {response.status_code}: {response.text[:200]}")
        return []

    try:
        payload = response.json()
    except Exception as exc:
        print(f"[telegram] poll returned unparseable JSON: {exc}")
        return []

    if not payload.get("ok"):
        print(f"[telegram] poll not ok: {str(payload.get('description'))[:200]}")
        return []

    result = payload.get("result")
    return result if isinstance(result, list) else []


def ask_mentor(
    session: Any,
    text: str,
    session_id: str,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> tuple[bool, str]:
    """
    One mentor turn through the local ``/api/chat``.

    Returns ``(ok, reply)``. A failure is returned, never raised and never dressed
    up as an answer: if the mentor cannot be reached he is told exactly that,
    rather than being left to assume his log landed.
    """
    url = f"{(base_url or service_url())}/api/chat"
    payload = {"message": text, "session_id": session_id}
    try:
        response = session.post(url, json=payload, timeout=timeout or chat_timeout())
    except Exception as exc:
        return False, f"I couldn't reach the mentor ({exc}). Nothing was recorded — send it again."

    if response.status_code != 200:
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("detail") or "")[:200]
        except Exception:
            detail = (response.text or "")[:200]
        return False, (
            f"The mentor returned HTTP {response.status_code}"
            + (f" ({detail})" if detail else "")
            + ". Nothing was recorded — send it again."
        )

    try:
        body = response.json()
    except Exception as exc:
        return False, f"I couldn't read the mentor's reply ({exc})."

    reply = str(body.get("response_text") or "").strip()
    if not reply:
        return False, "The mentor returned an empty reply — nothing was recorded. Send it again."
    return True, reply


def send_message(session: Any, token: str, chat_id: Any, text: str) -> bool:
    """
    Send one message back to his phone.

    Deliberately plain text, no ``parse_mode``: Markdown in a model's reply is
    unescaped more often than not, and Telegram rejects the whole message on a bad
    entity — which would look exactly like the mentor ignoring him. ``messenger.py``
    needs its Markdown fallback for this reason; plain text just avoids it.
    """
    try:
        response = session.post(
            _api_url(token, "sendMessage"),
            json={"chat_id": chat_id, "text": truncate_for_telegram(text)},
            timeout=15.0,
        )
    except Exception as exc:
        print(f"[telegram] send failed: {exc}")
        return False

    if response.status_code != 200:
        print(f"[telegram] send returned HTTP {response.status_code}: {response.text[:200]}")
        return False
    return True


def handle_update(
    session: Any,
    update: dict[str, Any],
    token: str,
    allowlist: set[str],
) -> bool:
    """
    Deal with one update. Returns True when it should be acked.

    Everything here is acked except nothing at all: even a refused sender and a
    failed mentor turn advance the cursor, because a stalled cursor blocks every
    message behind it — one unreachable turn would silently swallow the rest of
    the day. The cost of acking is that a failed log is lost, so the loss is made
    loud instead: he is told plainly that nothing was recorded and to send it
    again. A silent gap is far worse than a visible one, because a gap is
    indistinguishable from "he didn't log anything".
    """
    update_id = update.get("update_id")
    extracted = extract_message(update)
    if extracted is None:
        print(f"[telegram] update {update_id}: ignored (not a text message)")
        return True

    chat_id, text = extracted
    if not is_allowed(chat_id, allowlist):
        # Never reply to an unknown sender: a reply confirms the bot is live and
        # can write to his calendar.
        print(f"[telegram] update {update_id}: refused — chat {chat_id} is not in the allowlist")
        return True

    first_word = text.split()[0].lower()
    if first_word in ("/start", "/help"):
        # Answered locally: an orientation is not worth a mentor turn.
        send_message(session, token, chat_id, START_MESSAGE)
        return True

    session_id = session_id_for(chat_id)
    print(f"[telegram] update {update_id}: {len(text)} chars → {session_id}")

    ok, reply = ask_mentor(session, text, session_id)
    if not ok:
        # One immediate retry: most failures here are a sidecar that is mid-restart,
        # which recovers in seconds. More than one would just delay the queue.
        print(f"[telegram] update {update_id}: mentor turn failed ({reply}) — retrying once")
        time.sleep(2.0)
        ok, reply = ask_mentor(session, text, session_id)
        if not ok:
            print(f"[telegram] update {update_id}: mentor turn failed twice ({reply}) — acking and telling him")

    send_message(session, token, chat_id, reply)
    return True


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def main() -> int:
    # Line-buffer stdout so every diagnostic appears the moment it happens, even
    # when the output is redirected to a file or a process manager. A daemon whose
    # whole purpose is to be visible must not sit on its logs: buffered output is
    # lost outright on SIGTERM, which makes a dead listener look like a quiet one.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    token = bot_token()
    allowlist = allowed_chat_ids()

    if not token:
        print("[telegram] TELEGRAM_BOT_TOKEN is not set — there is nothing to listen with.")
        return 1
    if not allowlist:
        print(
            "[telegram] No allowlist configured (set TELEGRAM_ALLOWED_CHAT_IDS, or\n"
            "           TELEGRAM_CHAT_ID as a fallback). Every message would be refused,\n"
            "           so the listener is stopping rather than running uselessly."
        )
        return 1

    path = offset_path()
    offset = load_offset(path)
    known = sorted(allowlist)

    print("[telegram] day-log listener started.")
    print(f"[telegram]   allowed chats : {known}")
    print(f"[telegram]   mentor service: {service_url()}  (POST /api/chat)")
    print(f"[telegram]   session shape : {session_id_for(known[0])}  (one per day)")
    print(f"[telegram]   cursor        : {path} (offset {offset})")
    print(f"[telegram]   poll timeout  : {poll_timeout():.0f}s · chat timeout {chat_timeout():.0f}s")
    print("[telegram] Ctrl+C to stop.")

    session = requests.Session()
    backoff = 1.0
    failures = 0

    while True:
        started = time.monotonic()
        try:
            updates = poll_updates(session, token, offset)
        except KeyboardInterrupt:
            print("\n[telegram] stopped.")
            return 0
        except Exception as exc:
            print(f"[telegram] poll raised: {exc}")
            updates = []
        elapsed = time.monotonic() - started

        if not updates:
            # A healthy idle long-poll cannot come back quickly — it waits for the
            # timeout. So an empty result that returned instantly is a failed
            # request, not a quiet moment, and it must back off rather than spin.
            if elapsed < 2.0:
                failures += 1
                if failures in (1, 5, 20) or failures % 50 == 0:
                    print(
                        f"[telegram] poll returned empty in {elapsed:.2f}s "
                        f"({failures} consecutive) — retrying in {backoff:.0f}s"
                    )
                time.sleep(backoff)
                backoff = min(BACKOFF_MAX_SECONDS, backoff * 2)
            else:
                backoff, failures = 1.0, 0
            continue

        backoff, failures = 1.0, 0

        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = int(update.get("update_id") or 0)
            try:
                handle_update(session, update, token, allowlist)
            except KeyboardInterrupt:
                print("\n[telegram] stopped.")
                return 0
            except Exception as exc:
                # A crash in one handler must not take the listener with it, and it
                # must not wedge the queue either.
                print(f"[telegram] update {update_id} raised in handler (acked): {exc}")

            # Advance and persist only once the update is dealt with, so a crash
            # before this point re-delivers it rather than losing it.
            if update_id >= offset:
                offset = update_id + 1
                save_offset(path, offset)


if __name__ == "__main__":
    raise SystemExit(main())