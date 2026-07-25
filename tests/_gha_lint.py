"""Shared GitHub-Actions-workflow lint helpers, reused across the gate-v2 (D1) and
gate-shadow-v2 (D2) contract test suites.

Not a test module itself — a plain helper `import`ed BY test_gate_v2_contract.py and
test_gate_shadow_v2_contract.py (pytest's default collection only picks up
`test_*.py`/`*_test.py`, so this filename is deliberately outside that pattern and is
never collected/run directly).

Extracted (2026-07-26, D2 task) from test_gate_v2_contract.py's own original P1
regression guard (see that test's own history: an earlier draft of gate-v2.yml used
`${{ (inputs.primary_timeout_minutes - 5) * 60 }}`, which fails GitHub Actions workflow
parsing outright — GHA expression syntax has NO arithmetic operators, only
`() [] . ! < <= > >= == != && ||` per the official Operators reference) — the same guard
now covers gate-shadow-v2.yml too, since that file was hand-written against the same
constraint and deserves the same regression lock, without duplicating the regex/scan
logic a second time.
"""
from __future__ import annotations

import re
from pathlib import Path

# Matches an arithmetic operator used AS AN OPERATOR (whitespace on both sides) so it
# doesn't false-positive on legitimate tight-hyphen identifiers GitHub Actions
# expressions use all over both workflow files, e.g. step ids (`resolve-job-id`), runner
# labels inside quoted string literals (`'self-hosted'`), or `control_runner`'s
# `'self-hosted-control'` value — none of those have whitespace around the `-`.
_ARITHMETIC_OPERATOR_WITH_SPACES = re.compile(r"\s[-*/%]\s")
_GHA_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def find_arithmetic_gha_expression_offenders(workflow_path: Path) -> list[str]:
    """Every `${{ ... }}` span in `workflow_path` that contains what looks like an
    arithmetic operator used as an operator. Comment-only lines are dropped first, so a
    file's own prose explaining this exact bug (which may quote a broken example
    verbatim) never trips the guard against itself. An empty list means the file is
    clean; callers should `assert not offenders`.
    """
    text = "\n".join(
        ln for ln in workflow_path.read_text().splitlines() if not ln.lstrip().startswith("#")
    )
    offenders: list[str] = []
    for match in _GHA_EXPRESSION.finditer(text):
        body = match.group(1)
        # Strip single-quoted string literals (GHA expression string syntax) before
        # scanning, so quoted content like 'self-hosted' can't trip the heuristic even
        # if it somehow contained a spaced hyphen.
        stripped = re.sub(r"'[^']*'", "", body)
        if _ARITHMETIC_OPERATOR_WITH_SPACES.search(stripped):
            offenders.append(body.strip())
    return offenders
