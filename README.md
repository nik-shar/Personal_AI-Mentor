# 🧠 Personal AI Mentor

A local-first personal mentor built on the [PI agent harness](pi/README.md).
It remembers you, teaches you, and plans your time — across sessions.

**TypeScript only.** The conversation, the tools, the skills and the guardrails
are all PI extensions. The memory is plain files you can open in an editor.

---

## The idea

Most "AI companions" are a prompt and a chat log. This one is built around three
honest constraints:

1. **Nothing is fabricated.** Every claim about your calendar, streak, roadmap or
   memory comes from a tool call that read a real file. When the data is missing,
   the mentor says so rather than inventing it.
2. **Code owns the arithmetic.** Streaks, free windows, prerequisite unlocking,
   budget trimming — computed in code, never estimated by a model. The model
   reasons *with* those numbers; it does not calculate them.
3. **The pen stays yours.** Calendar writes are proposed before they happen. Code
   changes go through `propose_edit`. The one place the mentor writes directly is
   `learning/` — your curriculum.

---

## Layout

```
mentor/                 the mentor — PI extensions, skills, identity wiring
├── extensions/         identity overlay, guardrails, permission gate, provider
├── skills/             GENERATED from toolkits/ — do not edit
├── src/                identity assembly, disclosure classifier, pure math
└── test/               node --test

toolkits/               the instruction sets the skills are generated from
├── repo-architect/     read a repo → concepts → gaps → roadmap → schedule
├── learning-companion/ teach: predict before explain, smallest intervention
├── code-explorer/      read the real code before reasoning about it
├── calendar-manager/   the 48-slot grid, anchors, availability
└── tutorial-writer/    deep notes that actually teach

data/                   THE MEMORY — local-only, gitignored (see data/README.md)
learning/               your curriculum: topics/<name>/roadmap.yaml + notes
docs/                   design record and reasoning

mentor_persona.md            voice — read fresh every turn
mentor_agent_guidelines.md   the constitution — read fresh every turn
AGENTS.md                    project context PI loads at session start
```

---

## Run it

No sidecar to start — there is nothing else to run. One command:

```bash
npm run mentor                                  # interactive
npm run mentor -- -p "what should I study?"     # one-shot
```

`scripts/run_pi_mentor.sh` sources `.env` (for `NEBIUS_API_KEY`) and runs PI with
the mentor's extensions and skills, loaded from `.pi/settings.json`.

---

## Regenerate the skills

The skills are build output. Edit the toolkit, never the generated skill:

```bash
npm run skills      # toolkits/ -> mentor/skills/
```

The converter validates every tool a skill names against a registry, so a skill
cannot claim a capability that does not exist.

---

## Tests

```bash
npm test            # node --test mentor/test/*.test.ts
```

Covers the identity assembly (constitution present, not duplicated, fails open),
the crisis classifier's taxonomy, the pure planning math, and the specialist
status contract.

---

## Where the mentor's identity comes from

Two runtime-neutral markdown files at the repo root, read **fresh on every turn**
by `mentor/src/identity.ts`:

| File | Holds |
|---|---|
| `mentor_persona.md` | voice rules, hard rules, few-shot examples |
| `mentor_agent_guidelines.md` | the constitution — anchor facts, standing orders, guardrails |

Editing either takes effect on the next turn. Nothing is cached and nothing needs
regenerating. These two are *character*; the skills are *capability*. Do not
confuse them.

---

## Honest limitations

- **Tools are mid-migration.** The 16 tools the extensions register were served
  by a Python sidecar that has been deleted; they are being ported to read `data/`
  directly. Until that lands, the memory and calendar tools report unreachable
  rather than answering. `repo-architect` does not depend on them — it reads the
  repo with PI's own `read` / `grep` / `find`.
- **Estimates do not self-calibrate.** Hour figures in a roadmap are judgement on
  the day they were written; nothing reads back the time you actually logged to
  adjust them.
- **No proactive scheduling yet.** There is no daemon. Nightly summaries and weekly
  consolidation would be a cron entry running PI in `-p` mode — not built.
- **`docs/` is partly historical.** It documents the retired Python architecture,
  including the reasoning that produced the current design. Read it as a design
  journal, not a description of today's code.