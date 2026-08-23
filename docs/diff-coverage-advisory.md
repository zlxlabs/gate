# Diff coverage advisory (gate-v2 Phase C / 1a)

Gate v2 posts a **non-blocking** one-line PR comment that summarizes how well
the pull request's **changed executable lines** are covered by tests. The step
never affects the required gate verdict; failures degrade to a missing note.

## LCOV contract

Target repositories should write LCOV at:

```text
coverage/lcov.info
```

relative to the repository workspace root after their test step (for example
`coverage lcov -o coverage/lcov.info` from coverage.py, or the equivalent from
vitest/c8). Gate reads this path only; it does not generate coverage itself.

## Three-state semantics

| Situation | PR comment |
|---|---|
| `coverage/lcov.info` exists and diff-cover can measure changed code lines | `diff-coverage: <pct>% (<covered>/<total> changed lines)` |
| Code changed but `coverage/lcov.info` is absent | `diff-coverage: no coverage data` |
| All changed paths are non-code (docs-only PR) | *(no advisory line)* |

Notes:

- **Never show 0% when data is missing.** Absent LCOV is `no coverage data`, not a
  percentage.
- **Changed-line scope only.** diff-cover compares `base..head`; untouched debt
  outside the diff is excluded (invariant I-1a).
- **Base fetch.** The advisory action fetches only the PR base and head SHAs when
  missing, matching `.github/actions/pr-size-preflight` — not a full-history clone.

## Machine-readable prefix

Every emitted note line starts with `diff-coverage: ` so downstream aggregators can
parse PR comments without scraping free-form markdown.
