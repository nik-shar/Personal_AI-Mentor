#!/usr/bin/env bash
#
# Run the PI agent core as the mentor, from this repo.
#
#   scripts/run_pi_mentor.sh -p "hey"          # one-shot, prints the reply
#   scripts/run_pi_mentor.sh                   # interactive TUI
#   scripts/run_pi_mentor.sh --list-models     # prove extensions + provider loaded
#
# What it does:
#   - loads .env (provider keys reach whichever extension needs them)
#   - sources scripts/_guard.sh: a wall-clock cap, a duplicate-run lock, and a
#     preflight that names the provider before anything is spent
#   - passes -a (approve project resources) so .pi/settings.json and the
#     mentor/ extensions load in non-interactive modes
#
# There is no sidecar to start. The mentor reads `data/` directly.
#
# Budgeting: MENTOR_RUN_TIMEOUT_SECONDS (default 600) and MENTOR_MAX_TOOL_CALLS
# (default 40). See scripts/_guard.sh for why both exist.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/_guard.sh"

PI_ENTRY="${PI_ENTRY:-$ROOT_DIR/pi/packages/coding-agent/dist/bundle/cli.js}"
if [[ ! -f "$PI_ENTRY" ]]; then
  echo "pi entry not found at $PI_ENTRY" >&2
  echo "build it: (cd pi && npm run build)" >&2
  exit 1
fi

# --list-models spends nothing, so it skips the lock and the cap entirely.
if [[ "${1:-}" == "--list-models" ]]; then
  exec node "$PI_ENTRY" -a "$@"
fi

GUARD_PROMPT_LABEL="${*:-interactive}"
guard_acquire

PROVIDER="${MENTOR_PROVIDER:-$(node -e "console.log(require('$ROOT_DIR/.pi/settings.json').defaultProvider)" 2>/dev/null || echo '?')}"
MODEL="${MENTOR_MODEL:-$(node -e "console.log(require('$ROOT_DIR/.pi/settings.json').defaultModel)" 2>/dev/null || echo '?')}"
guard_preflight "$PROVIDER" "$MODEL" "mentor run"

# Give the budget clause to the agent when this is a one-shot (-p). The
# interactive TUI has a human at the keyboard, so it needs no stop condition.
ARGS=("$@")
for i in "${!ARGS[@]}"; do
  if [[ "${ARGS[$i]}" == "-p" || "${ARGS[$i]}" == "--print" ]] && [[ -n "${ARGS[$((i + 1))]:-}" ]]; then
    ARGS[$((i + 1))]="${ARGS[$((i + 1))]}$(guard_budget_clause)"
    break
  fi
done

guard_run node "$PI_ENTRY" -a "${ARGS[@]}"