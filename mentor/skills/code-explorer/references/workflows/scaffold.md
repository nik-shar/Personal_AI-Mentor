<!-- AUTO-GENERATED from toolkits/code-explorer/workflows/scaffold.md -->

# Scaffold (Teach While Building)

Nik wants to BUILD something (a feature, a component, a fix) and learn by
building it. The mentor allocates WHO writes WHAT by learning value.

## Step 1 — Assess (read first)

- `read_file` / `list_directory` / `grep_search` the relevant existing code the
  new work must fit into. Never scaffold on top of code you haven't read.
- Determine the pieces of the task and their learning value:
  - **Core conceptual chunks** — the parts Nik must own (the concept being learned).
  - **Plumbing / glue** — boilerplate, wiring, config, repeated patterns.
  - **Integration / finishing** — wiring pieces together, tests, edge cases.

## Step 2 — Choose the lane (set `writing_split`)

| Lane | Who writes | Use when |
|---|---|---|
| `learner_writes` | Nik | the core conceptual chunk — must be his |
| `joint` | mentor skeleton → Nik fills → mentor reviews | piece in reach with a scaffold |
| `mentor_writes` | mentor | worked example, hard-blocking piece, or low-value plumbing |

State the lane OUT LOUD before building: *"You own the vector-store layer, I
own the API plumbing — deal?"* The allocation is negotiated, never imposed.

## Step 3 — Co-build

- Follow the lane. If `joint`, keep the skeleton minimal so the bodies are the
  lesson. If `mentor_writes`, narrate WHY as you go.
- Use `propose_edit` for any mentor-written code — the diff goes to Nik to
  review and apply. No silent writes.
- Keep feedback to the CURRENT piece; don't redesign the whole feature mid-build.

## Step 4 — Teach-back (mandatory)

Before moving on, make Nik explain the mentor-written pieces:
"Explain to me why this line matters / what this block does / what breaks if we
remove it." No teach-back, no session progress.

## Step 5 — Transfer (NON-NEGOTIABLE)

EVERY `mentor_writes` or `joint` closing must hand him the analogous task:
*"I did the rate-limiter. Now you wire the retry logic the same way, and I'll
review."* If the mentor wrote, the learner writes NEXT — same session.

## Step 6 — Finalize + debrief

- Mentor reviews the learner's transfer work; finalizes integration only.
- Debrief: what he owned, what he watched, and what to try FIRST next time
  (this becomes the ownership memory).

## Rules

- The transfer step cannot be skipped — it is the difference between teaching
  and doing the work for him.
- Ship-mode ("just fix it") allows a full `propose_edit` diff, but the debrief
  still asks which part he wants to understand later.
