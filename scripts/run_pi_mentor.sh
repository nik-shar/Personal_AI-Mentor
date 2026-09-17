#!/usr/bin/env bash
#
# Run the PI agent core as the mentor, from this repo.
#
#   scripts/run_pi_mentor.sh -p "hey"          # one-shot, prints the reply
#   scripts/run_pi_mentor.sh                   # interactive TUI
#   scripts/run_pi_mentor.sh --list-models     # prove extensions + provider loaded
#
# What it does:
#   - loads .env so NEBIUS_API_KEY reaches the provider extension ($NEBIUS_API_KEY)
#   - exports MENTOR_SERVICE_URL for the mentor bridge tools
#   - passes -a (approve project resources) so .pi/settings.json and the
#     mentor/ extensions load in non-interactive modes
#
# The sidecar (FastAPI) must be running for the memory tools to answer:
#   uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

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

PI_ENTRY="${PI_ENTRY:-$ROOT_DIR/pi/packages/coding-agent/dist/bundle/cli.js}"
if [[ ! -f "$PI_ENTRY" ]]; then
  echo "pi entry not found at $PI_ENTRY" >&2
  echo "build it: (cd pi && npm run build)" >&2
  exit 1
fi

exec node "$PI_ENTRY" -a "$@"
