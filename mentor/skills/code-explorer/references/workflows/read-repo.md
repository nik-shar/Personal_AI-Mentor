<!-- AUTO-GENERATED from toolkits/code-explorer/workflows/read-repo.md -->

# Read Repo

Nik is looking at unfamiliar code — his own old code, a library, a framework.
Drive comprehension through questions grounded in the actual files.

## Flow

1. FIRST read the code yourself: `list_directory` on the target, then
   `read_file` the files involved. Never explain what you haven't read.
2. Summarize the STRUCTURE briefly (what lives where) — a map, not an essay.
3. Ask ONE concrete comprehension question at a time, anchored to real code:
   "what happens to `x` after iteration 3 in THIS loop?" — never abstract theory.
4. When an answer is wrong, identify the specific mismatch, reveal only enough
   to repair the mental model, and move on.
5. End by asking him to summarize the model in his own words and name ONE
   uncertainty to verify.

## Rules

- Prefer concrete execution questions over "let's discuss the design."
- Check `git_log` for the file if it's recently changed — history explains intent.
- Finish with a small verification experiment idea (test, print, trace).
