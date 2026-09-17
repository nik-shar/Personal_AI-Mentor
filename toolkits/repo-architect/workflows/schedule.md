---
description: Place roadmap nodes onto the real 48-slot calendar — read the grid, respect anchors, propose, then write with consent.
---

# Schedule the Roadmap

A roadmap without dates is a wish. This turns it into blocks on his actual day.

## 1. Read the grid before you say anything about time

Call `get_day_grid` for the date you're planning. **Availability is a fact
computed from the 48 slots — never a guess.**

The grid returns:
- `free_windows` — contiguous runs of free slots, already computed
- `elapsed_minutes` / `remaining_minutes` — what's left of today
- slot states including `sleep` / `meal` / `commute` / `gym` **anchors**

**Never offer a window the grid marks elapsed** — it is in the past.

## 2. Fit the nodes to the real budget

- Take nodes in dependency order (a node cannot be scheduled before its
  prerequisites are done or simultaneously underway).
- Use `find_available_slots` for each block's duration. It already respects
  anchors and existing events.
- **Never place a task over an anchor.** Sleep, meals, commute and gym are hard
  walls. If a request would collide, say so and offer an alternative — never
  silently schedule over them.
- Blocks are **at least 30 minutes** (one slot) and aligned to the half hour.
- Respect energy: a difficulty-5 concept in the last free window of a tired day
  is a block that will be skipped.

## 3. Propose before writing

Writing to the calendar changes his real day. So:

> "Here's how I'd place week 1 — 2h/day, afternoons, avoiding your 8pm dinner:
> **Mon** 16:00–18:00 single-writer boundary (diff 4, needs a clear head) ·
> **Tue** 16:00–17:30 fail-open contracts · ...
> I can lock these in if that shape works. Want me to?"

**One clear proposal, not five options.** Give the best fit with the reason,
then name the single best alternative in one line.

## 4. Write, then confirm

Only after he agrees — or after an explicit standing instruction like "plan my
study days" — call `place_time_block` for each block, with `linked_goal` set to
the node id so progress can be tracked back to the roadmap.

Then **close the loop in one summary**:

```
Locked, 3 blocks:
  Mon 16:00–18:00  The single-writer boundary
  Tue 16:00–17:30  Fail-open tool contracts
  Wed 17:00–18:30  Roadmap store layout
```

## 5. When it doesn't fit

Overflow is normal and must be stated, not hidden:

> "That's 22.5h of roadmap against 14 days at 2h = 28h available, so it fits —
> but three concepts land in week 3. Want to trim to 11 nodes, or raise the
> daily budget?"

Never quietly drop nodes to make it fit. He needs to see the trade.

## 6. Re-planning

When a block is missed, do **not** silently roll everything forward — that is
how a schedule becomes fiction.

1. Read the grid and the actual state first.
2. Ask one question: was the estimate wrong, or the day?
3. If the estimate was wrong, adjust it and say so — that estimate should learn.
4. Re-place only what genuinely still fits, and name what got dropped.

Estimates that never learn from real logged time are decoration. If he
consistently overruns one `content_type`, say it out loud and recalibrate.