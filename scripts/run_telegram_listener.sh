#!/usr/bin/env bash
#
# Run the day-log Telegram listener: inbound messages -> the mentor -> replies.
#
#   scripts/run_telegram_listener.sh
#
# What it needs:
#   - the sidecar running, because every message is POSTed to /api/chat:
#       uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
#   - .env with TELEGRAM_BOT_TOKEN plus an allowlist (TELEGRAM_ALLOWED_CHAT_IDS,
#     or TELEGRAM_CHAT_ID as the fallback). Without an allowlist the listener
#     refuses to start — anyone who finds the bot could otherwise make the mentor
#     write to the calendar and memory.
#
# It LONG-POLLS Telegram, so it opens no inbound port and needs no public URL.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export MENTOR_SERVICE_URL="${MENTOR_SERVICE_URL:-http://127.0.0.1:8000}"

exec uv run python -m integrations.telegram_listener "$@"