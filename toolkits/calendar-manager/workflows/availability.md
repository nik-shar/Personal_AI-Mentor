---
description: Answer "am I free / when do I have time / is this a good time?" from the grid.
---

# Availability

Pure read workflow — no writes, no proposals needed unless asked.

## Flow

1. **Read the grid.** Call `get_day_grid(date)`.
2. **Answer from the tool.** Quote the free windows and anchors the code
   computed. "You've got 09:00 – 13:30 free; you're anchored at dinner
   20:00 – 20:30 and sleep starts 23:00."
3. **Nuance honestly.** If the user names a specific time (e.g. "am I free at
   16:30?"), check that exact slot's state and say yes/no with what's there.
4. **One sharp next step.** If they're hunting for time, mention the smallest
   window that fits their likely task — don't dump the whole day.

## Rules

- Never answer a time question without a grid read in this turn (or an
  explicit reference to one just made).
- Free ≠ guaranteed: if adjacent free windows are tiny, say so ("you have
  30-min gaps, not a real 2-hour stretch").