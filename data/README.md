# data/ — the mentor's memory

> **This directory is local-only and gitignored.** The repository has a public
> remote, so nothing here is committed. The files *are* the memory: they live on
> your disk, you can read them, edit them, and diff them yourself.

Before this, memory lived in PostgreSQL behind a Python sidecar. The whole store
was **77 rows**. The filesystem is the better database for that size — and it
means you can open your mentor's memory in an editor.

## Layout

| Path | Holds | Written by |
|---|---|---|
| `profile.yaml` | structured facts about you, grouped by category (identity, career, goals, preferences…) | the mentor |
| `memories.jsonl` | what it learned in conversation — one JSON object per line, each with `source`, `confidence`, and `user_confirmed` | the mentor |
| `daylog.jsonl` | what you actually did, in your own words, timestamped. A message that starts something closes the previous interval, so one-liners become durations | you (via chat or Telegram) |
| `episodes/YYYY-MM.jsonl` | append-only log of everything that happened, one file per month | the mentor |
| `schedule/events.jsonl` | every scheduled block | calendar tools |
| `schedule/days/YYYY-MM-DD.yaml` | the 48-slot grid for one date — 48 entries, each with `state`, `status`, `label`, `event_id` | calendar tools |
| `conversations/*.jsonl` | closed session transcripts | session handling |
| `past_linkedin_posts/` | writing samples used to match your voice when drafting | you |

## Conventions

**`memories.jsonl`** — append-only. A memory is never silently edited; a
correction adds a new row and marks the old one `superseded_by`. Confidence has
a lifecycle:

| `source` | starts at | ceiling | user-confirmed |
|---|---|---|---|
| `user_stated` | 0.95 | 1.0 | yes |
| `seeded` | 0.95 | 1.0 | yes |
| `data_derived` | 0.70 | 0.95 | no |
| `mentor_inferred` | 0.40 | 0.60 | no |

Anything at `mentor_inferred` is a guess the mentor should be asking about, not
asserting. Confirming one raises its ceiling to 1.0 — that is you teaching it
what is true.

**`schedule/days/*.yaml`** — the grid is 48 slots of 30 minutes. `state` is one
of `free`, `task`, or an anchor (`sleep`, `meal`, `commute`, `gym`). **Tasks are
never placed over anchors.** Slot labels are wall-clock readings in one
timezone, not UTC.

## Editing by hand

All of this is plain text, so you can correct it directly:

- Fix a wrong fact → edit `profile.yaml`
- Forget something → delete its line from `memories.jsonl`
- Confirm a guess → set `"user_confirmed": true` and raise `confidence`

The mentor reads these fresh, so a hand edit takes effect on the next turn.

**Backups are your call** — nothing here is versioned by git. If you want
history, copy the folder or put it in a private repo of its own.