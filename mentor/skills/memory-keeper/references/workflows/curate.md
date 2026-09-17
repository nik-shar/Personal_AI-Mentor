<!-- AUTO-GENERATED from toolkits/memory-keeper/workflows/curate.md -->

# Curate

One run of the memory agent. This is the only workflow that runs unattended.

## Step 1 — requests first

Call `curator_requests`. These are explicit *"remember this"* from the mentor. They
were asked for by name, so they take priority — but the **source is still yours to
attribute**. The mentor knowing something is worth keeping is not the same as him
having stated it.

## Step 2 — read the pending range

Call `curator_pending`. Read **all** of it before writing anything. Writing as you
go produces duplicates and misses corrections that appear later in the range.

While reading, hold three lists in mind:

- **things he stated** — candidates for `user_stated`
- **things that changed or were corrected** — candidates for a supersede
- **things that looked like a pattern** — candidates for `data_derived`, or for an
  attention item if you are not sure

## Step 3 — check what you already hold

Before writing, read `data/memories.jsonl` (you have `read` and `grep`; the file is
small). **Do not write a memory that already exists.** If the new evidence is
stronger, supersede instead:

- he stated it now, the memory was an inference → supersede with `user_stated`
- he contradicted it → supersede with the correction
- it is simply the same → write nothing

## Step 4 — write

For each memory worth keeping, call `memory_write` with:

- `content` — one clear claim about him, specific enough to act on
- `source` — attributed honestly, weaker when unsure
- `quote` — the actual sentence, from the transcript
- `memory_type` — fact / preference / goal / struggle / observation

Use `profile_set` for typed facts that belong in a field (`target_roles`,
`long_term_goal`) rather than in prose.

Use `attention_raise` for signals you believe matter but cannot state as fact —
an unresolved thread, a contradiction, or something about the system's own state.

**When in doubt, write less.** A short run that adds one real thing is a good run.

## Step 5 — advance

Call `curator_advance` **last**, and only if every write succeeded.

If a write failed, do **not** advance. The range will be re-read on the next run,
which is the safe failure: re-reading costs a little, skipping loses memory
permanently.

## Step 6 — stop

Do not summarise. Do not report. Do not explain your reasoning to anyone — nothing
reads your output. The files you changed are the result.

If nothing was worth keeping: read, advance, stop. That is a complete and correct
run, and it will be the common case.
