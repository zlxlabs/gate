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

import yaml

# Matches an arithmetic operator used AS AN OPERATOR (whitespace on both sides) so it
# doesn't false-positive on legitimate tight-hyphen identifiers GitHub Actions
# expressions use all over both workflow files, e.g. step ids (`resolve-job-id`), runner
# labels inside quoted string literals (`'self-hosted'`), or `control_runner`'s
# `'self-hosted-control'` value — none of those have whitespace around the `-`.
_ARITHMETIC_OPERATOR_WITH_SPACES = re.compile(r"\s[-*/%]\s")
_GHA_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
# A syntactically plausible GHA expression body starts with an identifier/keyword
# character (a context name like `github`, a function call like `fromJSON(...)`, a
# literal like `'x'` or `true`, etc.) or an opening paren/bracket for a grouped/negated
# sub-expression. Empty/whitespace-only, or anything else (e.g. literal `...` prose),
# can never be a real expression — see find_empty_or_malformed_gha_expression_offenders.
_PLAUSIBLE_EXPRESSION_START = re.compile(r"^\s*[A-Za-z_!('\"0-9(]")


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


def find_empty_or_malformed_gha_expression_offenders_in_run_blocks(workflow_path: Path) -> list[str]:
    """P0 regression guard (2026-07-26): a literal, EMPTY double-brace expression-
    interpolation token, typed out inside a `run:` block's shell comment to explain some
    design decision in prose, compiles to an invalid GitHub Actions expression and fails
    the ENTIRE workflow file at creation time (confirmed: gate-shadow-v2.yml's canary
    first run failed immediately, empty `referenced_workflows`, no job ever dispatched —
    root-caused to exactly this).

    Unlike `find_arithmetic_gha_expression_offenders` above (which scans the WHOLE raw
    file text with a line-based `#`-comment strip), this function scans ONLY `run:`
    block STRING VALUES, via a real YAML parse — a `${{ ... }}`-shaped substring inside
    an ordinary top-level `#` YAML comment is genuinely inert (YAML strips those before
    GitHub Actions ever sees the parsed document at all), but a `#`-prefixed line INSIDE
    a `run: |` block scalar is not a YAML comment at all — it is literal STRING CONTENT
    (meaningful only to bash, not to YAML or to GitHub Actions' own expression
    templating), so anything shaped like `${{ ... }}` inside it is scanned and
    template-expanded exactly the same as real code in that same script. actionlint
    itself does not catch this class of bug either (see ci.yml's own `actionlint` job
    comment): it only flags a malformed/unknown REFERENCE inside an otherwise
    well-formed expression, never an EMPTY one.

    Returns a list of `"<job_id>::<step name or id>: <body!r>"` diagnostic strings; an
    empty list means the file is clean.
    """
    doc = yaml.safe_load(workflow_path.read_text())
    if not doc or "jobs" not in doc:
        return []
    offenders: list[str] = []
    for job_id, job in doc["jobs"].items():
        for step in job.get("steps", []) or []:
            run = step.get("run")
            if not isinstance(run, str):
                continue
            for match in _GHA_EXPRESSION.finditer(run):
                body = match.group(1)
                if not _PLAUSIBLE_EXPRESSION_START.match(body):
                    label = step.get("name") or step.get("id") or "<unnamed step>"
                    offenders.append(f"{job_id}::{label}: {body!r}")
    return offenders
