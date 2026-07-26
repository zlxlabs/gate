"""Repo-wide regression guard for the 2026-07-26 P0 canary failure: a literal, EMPTY
double-brace expression-interpolation token (quoted in a `run:` block's shell comment to
illustrate a design decision) compiles to an invalid GitHub Actions expression and fails
the ENTIRE workflow file at creation time — GitHub Actions' expression templating scans
every `run:` block's full string content (comments included, since a `#`-prefixed line
inside a `run: |` block scalar is literal STRING CONTENT to YAML/GitHub Actions, not a
real YAML comment) for that syntax and substitutes every match before bash ever parses
any of it.

Deliberately NOT scoped to gate-shadow-v2.yml alone (where the bug actually occurred) —
this scans every workflow-shaped YAML file the repo currently has, discovered by glob
rather than a hardcoded list, so a FUTURE workflow file automatically gets the same
guard without anyone remembering to add a new test for it. Composite actions
(.github/actions/*/action.yml) are intentionally out of scope: they use a different
schema (`runs: {using: composite, steps: [...]}`, no top-level `jobs:`) that
`_gha_lint.find_empty_or_malformed_gha_expression_offenders_in_run_blocks` does not
parse — a real bug class, but explicitly not what this task asked for.
"""
from pathlib import Path

from _gha_lint import find_empty_or_malformed_gha_expression_offenders_in_run_blocks

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILES = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
TEMPLATE_FILES = sorted((REPO_ROOT / "templates").glob("*.yml"))


def test_workflow_files_glob_finds_the_known_files():
    # Sanity check on the glob itself: if a future rename/move silently emptied this
    # list, every test below would vacuously "pass" without checking anything at all.
    names = {p.name for p in WORKFLOW_FILES}
    assert {"ci.yml", "gate.yml", "gate-v2.yml", "gate-shadow-v2.yml"} <= names
    assert TEMPLATE_FILES  # non-empty


def test_no_workflow_run_block_contains_a_literal_empty_gha_expression_token():
    all_offenders: dict[str, list[str]] = {}
    for path in WORKFLOW_FILES + TEMPLATE_FILES:
        offenders = find_empty_or_malformed_gha_expression_offenders_in_run_blocks(path)
        if offenders:
            all_offenders[str(path.relative_to(REPO_ROOT))] = offenders
    assert not all_offenders, (
        "found run: block(s) containing an empty/malformed GHA expression token "
        f"(this is the exact class of bug that failed gate-shadow-v2.yml's canary "
        f"first run — see this module's own docstring): {all_offenders!r}"
    )
