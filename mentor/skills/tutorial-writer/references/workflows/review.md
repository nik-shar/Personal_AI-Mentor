<!-- AUTO-GENERATED from toolkits/tutorial-writer/workflows/review.md -->

# Review

Every draft must pass this pass before it is written to the vault. Failures
are visible and fixed, never waived.

## Flow

1. **Mechanical gate (code-runs this too — a failure returns a rejection
   observation):**
   - length ≥ `min_length`
   - at least `min_headings` headings
   - none of `banned_placeholders` (`...`, `TODO`, `TBD`, `lorem`)
2. **Re-read as the learner.** Does the hard step the mentor flagged read
   clearly this time? Would you attempt the practice task from this alone?
3. **Honesty check:** any number, API, or example not grounded in the
   research step? Flag or remove it.
4. **Structure check:** headings actually match the content-type blueprint;
   examples are runnable; prerequisites link to real notes.

## On failure

- Return the requirement that failed verbatim — the revision loop feeds the
  violation back to the writer, which rewrites in full (never a patch with
  `...`).
- If revision cap is reached, the note is still saved but the response
  reports the remaining violations honestly.
