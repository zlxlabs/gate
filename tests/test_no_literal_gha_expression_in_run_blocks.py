"""Repo-wide regression guards for `run:` block text-hygiene bugs found across two
canary incidents (2026-07-26) — both are cases where a `run:` block's literal STRING
CONTENT (comments included: a `#`-prefixed line inside a `run: |` block scalar is not a
real YAML comment, just text bash happens to treat as one) misbehaves in a way that has
nothing to do with normal YAML/bash syntax errors:

1. `test_no_workflow_run_block_contains_a_literal_empty_gha_expression_token` — a
   literal, EMPTY double-brace expression-interpolation token (quoted in a `run:` block
   comment to illustrate a design decision) compiles to an invalid GitHub Actions
   expression and fails the ENTIRE workflow file at creation time — GitHub Actions'
   expression templating scans every `run:` block's full string content for that syntax
   and substitutes every match before bash ever parses any of it.
2. `test_no_workflow_run_block_contains_a_bare_gate_hub_dir_reference` — a `GATE_HUB_DIR`
   reference missing its `$`/`${...}` sigil resolves to the literal string
   "GATE_HUB_DIR" instead of the resolved directory path (canary probe #2: `git -C
   GATE_HUB_DIR` -> `fatal: cannot change to 'GATE_HUB_DIR': No such file or
   directory`). Unlike guard 1, a genuine bash `#`-comment IS safe from this specific
   bug (bash really does ignore it), so comment lines are excluded from this scan —
   see `_gha_lint.find_bare_gate_hub_dir_offenders_in_run_blocks`'s own docstring for
   why the two guards deliberately treat comments differently.

Deliberately NOT scoped to the one file each bug actually occurred in — both scan every
workflow-shaped YAML file the repo currently has, discovered by glob rather than a
hardcoded list, so a FUTURE workflow file automatically gets the same guards without
anyone remembering to add a new test for it. Composite actions
(.github/actions/*/action.yml) are intentionally out of scope for both: they use a
different schema (`runs: {using: composite, steps: [...]}`, no top-level `jobs:`) that
neither `_gha_lint` function parses — a real bug class, but explicitly not what either
task asked for.
"""
from pathlib import Path

from _gha_lint import (
    find_bare_gate_hub_dir_offenders_in_run_blocks,
    find_empty_or_malformed_gha_expression_offenders_in_run_blocks,
)

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


def test_no_workflow_run_block_contains_a_bare_gate_hub_dir_reference():
    all_offenders: dict[str, list[str]] = {}
    for path in WORKFLOW_FILES + TEMPLATE_FILES:
        offenders = find_bare_gate_hub_dir_offenders_in_run_blocks(path)
        if offenders:
            all_offenders[str(path.relative_to(REPO_ROOT))] = offenders
    assert not all_offenders, (
        "found run: block(s) with a GATE_HUB_DIR reference missing its $/${...} sigil "
        f"(this is the exact class of bug canary probe #2 surfaced as `git -C "
        f"GATE_HUB_DIR` -> 'fatal: cannot change to GATE_HUB_DIR' — see this module's "
        f"own docstring): {all_offenders!r}"
    )
