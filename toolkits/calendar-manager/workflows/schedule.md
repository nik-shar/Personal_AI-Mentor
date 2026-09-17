---
description: The standard operating procedure for placing work on the calendar.
---

# Schedule

The mentor's core workflow: place a task (or a whole day) on the grid with
discipline.

## Flow

1. **Read the grid.** Call `get_day_grid(today)` — free windows and anchors
   are computed by code. Never plan against a grid you haven't read.
2. **Gather the workload.** What does Nik want done? How long each? Are there
   hard deadlnes (interviews, due dates)? What's his energy level right now?
3. **Find honest windows.** Call `find_available_slots(date, duration_min,
   energy_level)` and use ONLY the returned candidates. For multiple blocks,
   iterate per block.
4. **Justify the allocation.** Arrange blocks by priority and energy: the
   hardest/deepest work goes in his strongest hours; lighter review work in
   the tail; short gaps stay as buffers. Say the reasoning.
5. **Propose, don't write.** Present the placement with times + reasons. Ask
   for the go-ahead. (When he pre-authorized — "go ahead and schedule my
   day" — this step is a summary, not a question.)
6. **Place on consent.** Call `place_time_block(...)` per block. If a call
   returns an anchor-conflict or overlap error, pick the next candidate and
   retry (the error is the grid's honest word).
7. **Confirm and close.** Report what was locked where — and offer to move
   anything if it clashes with reality later.

## Rules

- Round everything to 30 minutes; alignment is the code's job, not a
  negotiation.
- If total requested time exceeds available windows, say so openly and help
  him trim by priority — never silently drop or stretch a block.
- End every placement turn with a crisp confirmation so Nik can correct it.