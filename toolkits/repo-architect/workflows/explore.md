---
description: Read a repository properly with your own tools — orient, then dig into the files that carry decisions.
---

# Explore

Read the repo yourself. You are a coding agent with `grep`, `read`, `find`, `ls`
and `bash`; there is no reason to reason about a codebase you could open.

## 1. Orient (5 minutes of reading, not 50)

- `ls` the root. Note the top level shape.
- Read the manifest: `package.json`, `pyproject.toml`, `requirements.txt`,
  `go.mod`, `Cargo.toml`. Dependencies tell you the domains in play.
- Read `README.md` — but treat it as a *claim*, not a finding. You will verify
  anything you take from it.
- `find` the entry points: `main.py`, `__main__.py`, `index.ts`, `app/`, `cmd/`.
- Check for `docs/`, `CHANGELOG.md`, `CONTRIBUTING.md`, and any `*.md` outside
  the root. **Design docs and changelogs are where the hard decisions are
  written down.** A changelog entry about a subtle bug is a concept with a
  receipt.

## 2. Grep for the invariants (the highest-yield step)

The concepts you want are the rules the code must not break. Find them by
searching for how people write them down:

```bash
grep -rn "never\|must not\|only\|always\|invariant\|single writer\|atomic" \
  --include=*.py --include=*.ts --include=*.go <root> | head -40
grep -rn "NOTE\|IMPORTANT\|WARNING\|HACK\|TODO\|FIXME\|XXX" <root> | head -40
grep -rn "choke point\|guard\|fail-open\|idempotent\|race\|deadlock" <root> | head -30
```

Then read the files those hits landed in. A comment explaining *why* is worth
more than a hundred lines of plumbing.

## 3. Find the load-bearing files

Run something like:

```bash
# files that are large AND imported a lot are usually where the design lives
find <root> -name '*.py' -o -name '*.ts' | xargs wc -l 2>/dev/null | sort -rn | head -20
grep -rn "class \|def " <root> --include=*.py | wc -l    # size of the surface
```

Read the largest and the most-imported ones — but read them *for the decisions*,
not line by line. You are looking for: what does this module refuse to do, and
why?

## 4. Follow one real request end to end

Pick a user-visible capability and trace it. `README` → entry point → handler →
the thing that actually does the work. This is where you learn the shape of the
system: where the boundaries are, what is duplicated, what is generated.

## 5. Know when to stop

You cannot read everything, and you should not try. Stop when you can answer,
for the part of the repo that matters:

- What are the 3-5 non-obvious rules here?
- Where is each one enforced?
- What would break if someone got it wrong?

**Record your coverage honestly.** Note how many files you read and which
directories you skipped — that number is what you report in `build-roadmap`, and
it is the difference between analysis and a guess.

## Report back

Do not dump the exploration into chat. Produce a short structural map
(what lives where, in 5-10 lines) and a list of candidate concepts with their
citations. Then go to `extract-concepts`.