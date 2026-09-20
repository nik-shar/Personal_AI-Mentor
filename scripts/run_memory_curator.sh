#!/usr/bin/env bash
#
# Run the memory agent: read the conversation forward from the cursor, promote
# what mattered into data/, and stop.
#
#   scripts/run_memory_curator.sh                 # curate whatever is pending
#   scripts/run_memory_curator.sh --dry-run       # show what is pending, write nothing
#
# What this is
# ------------
# A SECOND PI process, isolated from the mentor on six axes (MEMORY.md §3):
#
#   model         --provider/--model, the cheap fast tier — extraction is not
#                 reasoning-heavy work, and this runs frequently
#   context       --session-dir + --no-session: it never sees the mentor's window
#   instructions  --no-skills --skill <memory-keeper>: its own procedure only
#   tools         --exclude-tools bash,write,edit: no shell, no raw file writes
#   project ctx   --no-context-files: it does not load AGENTS.md; it is not the mentor
#   write rights  --no-extensions -e mentor/curator/extension.ts: its gate allows data/ ONLY
#
# That last one is the load-bearing bit. The mentor may write learning/ only; the
# memory agent may write data/ only. Neither can reach the other's domain, so
# there is exactly one writer per file (MEMORY.md §4).
#
# The curator's extension deliberately lives OUTSIDE `mentor/extensions/`: PI loads
# every file in that directory (`.pi/settings.json` points at it), so leaving it
# there would load the curator's gate into the mentor — and two gates with
# different allowlists would block each other, leaving the mentor unable to write
# anything at all.
#
# It is safe to run late, twice, or after a crash: the curator reads a CURSOR and
# only moves it forward after a successful run. Nothing is skipped by a missed
# invocation.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PI_ENTRY="${PI_ENTRY:-$ROOT_DIR/pi/packages/coding-agent/dist/bundle/cli.js}"
if [[ ! -f "$PI_ENTRY" ]]; then
  echo "pi entry not found at $PI_ENTRY" >&2
  echo "build it: (cd pi && npm run build)" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/_guard.sh"

# Curation is extraction, not reasoning, so the cheapest capable tier is right —
# it is also the tier that runs most often.
#
# Default is OpenAI's gpt-5-nano: $0.05/M in, $0.40/M out, $0.005/M cache-read.
# That cache-read price matters — it is 10x cheaper than gpt-4.1-nano's, and a
# curator re-reads a growing transcript as it walks forward.
#
# Measured on this machine, 2026-09-17:
#   openai/gpt-5-nano     cheapest capable option; 400K context
#   groq                  works, but 8k tokens/minute — one long range rate-limits
#   nebius / nebius-classifier   HTTP 402, no credit
CURATOR_PROVIDER="${CURATOR_PROVIDER:-openai}"
CURATOR_MODEL="${CURATOR_MODEL:-gpt-5-nano}"
# MEMORY_SKILL / PROVIDER_EXTENSION
#
# The provider is registered by an extension, so it must be loaded explicitly —
# `--no-extensions` would otherwise leave the curator with no provider at all
# ("Unknown provider") and it would fail before reading a single message.
PROVIDER_EXTENSION="$ROOT_DIR/mentor/extensions/provider-nebius.ts"

MEMORY_SKILL="$ROOT_DIR/mentor/skills/memory-keeper"
if [[ ! -f "$MEMORY_SKILL/SKILL.md" ]]; then
  echo "memory-keeper skill not generated at $MEMORY_SKILL" >&2
  echo "run: npm run skills" >&2
  exit 1
fi

PROMPT="Curate the pending conversation into memory. Follow the memory-keeper skill:
call curator_requests first, then curator_pending, then write what is worth
keeping, then curator_advance last.

Write nothing you cannot quote, attribute every source honestly (a guess is
mentor_inferred, never user_stated), and write less than you are tempted to.
Advancing the cursor is the commit point — only do it if your writes succeeded."

# --dry-run: hand the curator a read-only instruction set instead of the real one,
# so it reports what it would write without touching any file.
if [[ "${1:-}" == "--dry-run" ]]; then
  PROMPT="Report what is pending — do NOT write anything and do NOT advance the cursor.
Call curator_pending and curator_requests, then list what you would remember if
asked, with the source you would attribute and the quote you would cite."
fi

# --init: park the cursor at the end of the newest session and write no memory.
#
# The right move on an existing install. The cursor starts empty, so a first run
# would try to curate every session you have ever had — a huge context and a
# flood of memories, most of which are no longer true. This says "start from now"
# instead. Nothing is lost: the transcripts stay on disk, and you can curate
# backwards deliberately later by resetting the cursor.
if [[ "${1:-}" == "--init" ]]; then
  node --input-type=module -e "
    const mod = await import('$ROOT_DIR/mentor/src/memory/transcript.ts');
    const { next, lines } = mod.pendingTranscript('$ROOT_DIR');
    if (!next.file) {
      console.log('No sessions found for this repo — nothing to park. Curation starts from the next conversation.');
    } else {
      mod.writeCursor('$ROOT_DIR', next);
      console.log('Cursor parked at ' + next.file.split('/').pop() + ' line ' + next.line + '.');
      console.log('Skipped ' + lines.length + ' existing message(s) — curation starts from now.');
      console.log('Transcripts are untouched, so you can curate backwards later by resetting data/.curator_cursor.json');
    }
  "
  exit 0
fi

GUARD_PROMPT_LABEL="memory curator"
guard_acquire
guard_preflight "$CURATOR_PROVIDER" "$CURATOR_MODEL" "memory curation"

# Curation is bounded by design — it reads a range and stops. A short cap is
# enough, and the lock stops a duplicate run from doubling the spend.
MENTOR_RUN_TIMEOUT_SECONDS="${MENTOR_RUN_TIMEOUT_SECONDS:-300}"

guard_run node "$PI_ENTRY" \
  --mode text \
  --no-extensions \
  -e "$PROVIDER_EXTENSION" \
  -e "$ROOT_DIR/mentor/curator/extension.ts" \
  --no-skills --skill "$MEMORY_SKILL" \
  --no-context-files \
  --provider "$CURATOR_PROVIDER" \
  --model "$CURATOR_MODEL" \
  --session-dir "$ROOT_DIR/.pi/memory-sessions" \
  --no-session \
  --exclude-tools bash,write,edit \
  -a \
  -p "$PROMPT$(guard_budget_clause)"