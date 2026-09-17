---
description: Learn, confirm, and adjust life anchors (sleep / meal / commute / gym).
---

# Anchors

Anchors are the stable skeleton of the week; the grid treats them as walls.

## Flow

1. **Capture what Nik says.** "I sleep midnight to 8", "dinner's around 8",
   "gym at 7 in the morning" → these are anchor candidates.
2. **Offer to set them, don't just set them.** `set_anchor` writes his real
   day — propose it ("want me to block that in as your sleep anchor?") unless
   he already told you to handle it.
3. **Set on consent.** Use `set_anchor(date, start_time, duration_min,
   state, label)` with the accurate state (`sleep`, `meal`, `commute`, `gym`)
   and a clear label.
4. **Notify the consequence.** After an anchor is set, name what it protects:
   "now no plan will ever put work in your 00:00–08:00 window."
5. **When plans collide with anchors**, surface the collision and offer the
   nearest safe alternative — Nik decides, never the plan.

## Rules

- **Anchors are per-date, and do NOT recur yet.** Setting sleep for tonight covers
  tonight and the small hours of tomorrow morning — nothing else. Never tell Nik a
  future day is protected. If he says "I generally sleep 10 to 9", say plainly that
  you can set it for a given day and that daily recurrence is not built yet.
- A bedtime usually spans midnight. Set it in ONE call — start 22:00, duration 660
  for 10pm–9am — and name both windows back to him (tonight 22:00–24:00 AND tomorrow
  00:00–09:00). Never report only the part before midnight.
- Never delete or shift an existing anchor without asking.