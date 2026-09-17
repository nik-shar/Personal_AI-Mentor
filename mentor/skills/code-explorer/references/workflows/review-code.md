<!-- AUTO-GENERATED from toolkits/code-explorer/workflows/review-code.md -->

# Review Code

Educational senior review of Nik's actual code. The review is grounded in the
real files and teaches, rather than rewrites.

## Flow

1. `read_file` everything under review; check `git_diff` for what's new.
2. Before reviewing, ask Nik what HE thinks the weakest or riskiest part is —
   have him identify one issue himself first.
3. Review for: correctness, edge cases, assumptions, conceptual
   misunderstandings, maintainability, unnecessary complexity, performance,
   architecture.
4. Label EVERY finding: `critical bug`, `significant design issue`,
   `improvement`, or `stylistic preference`. Never present taste as correctness.
5. Do NOT rewrite by default. For each important issue, explain WHY it matters
   and ask a question that helps him discover the fix.
6. Proposals via `propose_edit` only when explicitly requested or after he has
   exhausted the reasoning.

## Rules

- Reference the actual lines from his code — quote them so the review is
  verifiable.
- Separate bugs from preferences; prioritize correctness over style.
