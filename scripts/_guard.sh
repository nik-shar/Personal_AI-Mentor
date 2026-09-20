#!/usr/bin/env bash
#
# scripts/_guard.sh — the run guard. Sourced by every launcher.
#
# Why this exists
# ---------------
# On 2026-09-17 a repo-analysis prompt was launched four times while debugging a
# tool timeout. Each was `setsid`-detached, so the shell that launched them died
# and the caller's `timeout` never applied. All four ran for 3h17m, made 2,093
# model calls, and sent ~310M input tokens — a $63 bill from one afternoon.
#
# Three protections, each aimed at a specific part of that failure:
#
#   1. WALL-CLOCK CAP, applied INSIDE this process. An outer `timeout` cannot kill
#      a detached child, so the cap has to live where the child does.
#   2. A LOCK. Refuses to start while another run for this repo is alive. This is
#      the one that would have prevented the duplicates.
#   3. A PREFLIGHT that prints the provider, model, and rate limit before spending
#      anything — so a wrong default is visible instead of discovered on a bill.
#
# Plus a TOOL-CALL BUDGET passed to the agent, because PI has no step limit of its
# own (checked: no --max-steps, no iteration cap in the settings schema). An
# agentic loop is bounded by the prompt or it is not bounded at all.

GUARD_DIR="${ROOT_DIR:-$(pwd)}/.pi"
GUARD_LOCK="$GUARD_DIR/run.lock"

# Default 10 minutes. A normal turn is seconds; a repo analysis is a few minutes.
# The runaway was 197 minutes. Override per-run with MENTOR_RUN_TIMEOUT_SECONDS.
MENTOR_RUN_TIMEOUT_SECONDS="${MENTOR_RUN_TIMEOUT_SECONDS:-600}"

# Tool-call ceiling handed to the agent in its prompt. PI has no flag for this,
# so the prompt is the only bound — and an unbounded agentic loop is what made
# this expensive, not the model.
MENTOR_MAX_TOOL_CALLS="${MENTOR_MAX_TOOL_CALLS:-40}"

# ---------------------------------------------------------------------------
# 1. The lock
# ---------------------------------------------------------------------------

guard_acquire() {
  mkdir -p "$GUARD_DIR"

  if [[ -d "$GUARD_LOCK" ]]; then
    local other
    other="$(cat "$GUARD_LOCK/pid" 2>/dev/null || echo "")"
    if [[ -n "$other" ]] && kill -0 "$other" 2>/dev/null; then
      echo "" >&2
      echo "REFUSING TO START — another mentor run is already active." >&2
      echo "" >&2
      echo "  pid:      $other" >&2
      echo "  started:  $(cat "$GUARD_LOCK/started" 2>/dev/null || echo '?')" >&2
      echo "  prompt:   $(head -c 120 "$GUARD_LOCK/prompt" 2>/dev/null || echo '?')..." >&2
      echo "" >&2
      echo "This guard exists because four duplicated runs cost \$63 in one" >&2
      echo "afternoon. Wait for it, or stop it:" >&2
      echo "" >&2
      echo "  kill $other" >&2
      echo "  # if it is truly stuck: rm -rf $GUARD_LOCK" >&2
      echo "" >&2
      exit 3
    fi
    # Dead pid: the run crashed without cleaning up. Reclaim the lock.
    echo "[guard] reclaiming a stale lock (pid $other is gone)" >&2
    rm -rf "$GUARD_LOCK"
  fi

  mkdir -p "$GUARD_LOCK"
  echo $$ > "$GUARD_LOCK/pid"
  date -Is > "$GUARD_LOCK/started"
  printf '%s' "${GUARD_PROMPT_LABEL:-$*}" > "$GUARD_LOCK/prompt"

  # Clean up on any exit — including a killed run, as long as we are not SIGKILLed.
  trap 'rm -rf "$GUARD_LOCK"' EXIT INT TERM
}

# ---------------------------------------------------------------------------
# 2. The preflight
# ---------------------------------------------------------------------------

guard_preflight() {
  local provider="$1" model="$2" label="$3"

  echo "[guard] $label" >&2
  echo "[guard] provider=$provider model=$model" >&2
  echo "[guard] wall-clock cap: ${MENTOR_RUN_TIMEOUT_SECONDS}s | tool-call budget: $MENTOR_MAX_TOOL_CALLS" >&2

  case "$provider" in
    nebius*)
      # Measured 2026-09-17: this tier returned HTTP 402 on every call.
      echo "[guard] WARNING: '$provider' returned 402 (no credit) when last tested." >&2
      ;;
    groq)
      # Measured: 8,000 tokens/minute. A single 200K-token mentor turn cannot fit.
      echo "[guard] WARNING: groq allows 8K tokens/minute — one long turn will rate-limit." >&2
      ;;
  esac

  if [[ "${GUARD_DRY_RUN:-}" == "1" ]]; then
    echo "[guard] dry run — nothing was spent." >&2
    exit 0
  fi
}

# ---------------------------------------------------------------------------
# 3. The bounded run
# ---------------------------------------------------------------------------

# Runs the PI entry under the wall-clock cap, as a CHILD (not exec), so the lock
# trap still fires and the exit code is preserved.
guard_run() {
  local rc=0
  # --kill-after: SIGKILL 10s after SIGTERM, for a node process ignoring SIGTERM.
  timeout --kill-after=10 "$MENTOR_RUN_TIMEOUT_SECONDS" "$@" || rc=$?

  if [[ $rc -eq 124 || $rc -eq 137 ]]; then
    echo "" >&2
    echo "[guard] STOPPED after ${MENTOR_RUN_TIMEOUT_SECONDS}s — the wall-clock cap." >&2
    echo "[guard] This is the guard working, not a bug. If the run legitimately" >&2
    echo "[guard] needs longer, raise it: MENTOR_RUN_TIMEOUT_SECONDS=1800 ..." >&2
    echo "" >&2
  fi
  return $rc
}

# The budget sentence appended to every prompt. PI has no step limit, so this is
# the only thing standing between an agent and an unbounded loop.
guard_budget_clause() {
  cat <<EOF

STOP CONDITION (mandatory, not a suggestion): you have a budget of ${MENTOR_MAX_TOOL_CALLS} tool calls.
Count them. When you reach it, STOP and report what you have found so far,
including what you did not get to. An incomplete answer within budget is the
correct outcome. A complete answer past budget is a failure.
EOF
}