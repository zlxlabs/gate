"""Behavioural lock for the classify step's HARD time boundaries (gate-hub#1547).

The step's only two external calls (`gh api`, then the classifier) used to carry
a soft upper limit only. GNU `timeout` sends SIGTERM at the limit and then waits
for the child for as long as it takes — forever, if the child ignores SIGTERM.
That is how `classify_pr_paths` once burned its whole 5-minute job budget: the
job was killed by GitHub, `review_expected` never reached the outputs, and the
aggregator read the empty output as an unexpected primary skip. `-k` turns that
into a bounded wait, and the bracketing `::notice::` lines make the next
occurrence locatable in the job log.

These tests execute the step's `run:` body EXTRACTED VERBATIM from the workflow
files, so the boundary under test is the one that ships — not a copy of it. The
fakes below model the failure by ignoring SIGTERM outright; `exec sleep` keeps
the stub a single process so a SIGKILLed one leaves no orphan holding the
probe's pipes.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = (
    REPO_ROOT / ".github" / "workflows" / "gate-v2.yml",
    REPO_ROOT / ".github" / "workflows" / "gate-shadow-v2.yml",
)
CLASSIFIER_REL = "_gate-classify-src/scripts/classify_pr_reviewable_paths.py"
REAL_CLASSIFIER = REPO_ROOT / "scripts" / "classify_pr_reviewable_paths.py"
JOB_BUDGET_SECONDS = 5 * 60  # the job's own `timeout-minutes: 5`
# Slack on top of the limit+grace the step declares, covering process startup
# and CI noise. Comfortably below the job budget the boundary exists to protect.
SLACK_SECONDS = 15
# Extra headroom the probe itself allows before it gives up and fails: a
# regression (boundary removed) must turn the assertion red, not hang CI.
CAP_MARGIN_SECONDS = 30

# The shipped boundary, as a contract. The behaviour tests below hold THIS
# expectation (not whatever the step's text happens to parse to), so deleting
# `-k` from the workflow turns the timing assertion red instead of quietly
# re-deriving a looser bound from the broken text; `test_both_external_calls_
# declare_a_hard_boundary` separately pins the workflow literal to it.
EXPECTED_BOUND = {"gh": (5, 30), "python3": (5, 20)}  # command -> (SIGKILL grace, limit)

_BOUND = re.compile(r"timeout -k (\d+)s --foreground (\d+)s (gh|python3)\b")

# gate-hub#1547's hang: answers nothing, not even SIGTERM.
_STUBBORN_GH = """#!/bin/bash
trap '' TERM
exec sleep 600
"""

_STUBBORN_CLASSIFIER = """#!/usr/bin/env python3
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(600)
"""

# One changed path, in the fleet's default exempt set
# (scripts/classify_pr_reviewable_paths.py's FLEET_DEFAULT_EXEMPT).
_LEDGER_ONLY_LISTING = (
    '{"files": [{"filename": "retro/acceptance-log.jsonl"}], "truncated": false}\n'
)
_QUICK_GH = f"""#!/bin/bash
cat <<'JSON'
{_LEDGER_ONLY_LISTING}JSON
"""


@dataclass
class _Probe:
    elapsed: float
    finished: bool
    returncode: int | None
    stdout: str
    stderr: str
    github_output: str


def _classify_run(workflow: Path) -> str:
    raw = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    step = next(
        s for s in raw["jobs"]["classify_pr_paths"]["steps"] if s.get("id") == "classify"
    )
    return step["run"]


def _bounds(run: str) -> dict[str, tuple[int, int]]:
    """The (grace, limit) each call's own `timeout -k` declares, per command."""
    return {cmd: (int(grace), int(limit)) for grace, limit, cmd in _BOUND.findall(run)}


def _write_exec(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _probe_step(
    tmp_path: Path,
    run: str,
    *,
    gh_body: str,
    classifier_body: str,
    hard_cap: float,
    base_sha: str = "0" * 40,
    head_sha: str = "a" * 40,
) -> _Probe:
    """Run the extracted step body under `cwd=tmp_path`, with stubbed gh/classifier."""
    bin_dir = tmp_path / "bin"
    _write_exec(bin_dir / "gh", gh_body)
    _write_exec(tmp_path / CLASSIFIER_REL, classifier_body)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GH_TOKEN": "x",
        "REPOSITORY": "zlxlabs/gate",
        "BASE_SHA": base_sha,
        "HEAD_SHA": head_sha,
        "REVIEW_EXEMPT_PATHS": "",
    }
    started = time.monotonic()
    proc = subprocess.Popen(
        ["bash", "-c", run],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Own session so the whole group (bash → timeout → stub) can be killed
        # together when the cap trips; a wedged grandchild must not outlive it.
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=hard_cap)
        finished = True
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        finished = False
    return _Probe(
        elapsed=time.monotonic() - started,
        finished=finished,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        github_output=output.read_text(encoding="utf-8"),
    )


def _bound_for(command: str) -> tuple[int, int]:
    assert command in EXPECTED_BOUND, f"no declared boundary for {command}"
    return EXPECTED_BOUND[command]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_both_external_calls_declare_a_hard_boundary(workflow):
    bounds = _bounds(_classify_run(workflow))
    assert bounds == EXPECTED_BOUND, f"{workflow.name}: boundary drifted from the contract"
    for command, (grace, limit) in bounds.items():
        assert grace > 0, f"{workflow.name}: {command} declares no SIGKILL grace (-k {grace}s)"
    worst_case = sum(limit + grace for grace, limit in bounds.values())
    # 60s slack for checkout + job setup: the whole step must end far inside the
    # job budget, or the boundary has not actually bought the 5 minutes back.
    assert worst_case + 60 <= JOB_BUDGET_SECONDS, (
        f"{workflow.name}: boundary budget {worst_case}s + 60s slack exceeds "
        f"{JOB_BUDGET_SECONDS}s"
    )


def test_stubborn_gh_is_killed_and_the_step_stays_fail_open(tmp_path):
    run = _classify_run(WORKFLOWS[0])
    grace, limit = _bound_for("gh")
    probe = _probe_step(
        tmp_path,
        run,
        gh_body=_STUBBORN_GH,
        classifier_body=REAL_CLASSIFIER.read_text(encoding="utf-8"),
        hard_cap=limit + grace + CAP_MARGIN_SECONDS,
    )
    assert probe.finished, (
        f"step never finished within {limit + grace + CAP_MARGIN_SECONDS}s: the `gh` "
        "boundary does not hold a SIGTERM-ignoring child"
    )
    assert probe.elapsed <= limit + grace + SLACK_SECONDS
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "review_expected=true" in probe.github_output
    assert "review_expected=false" not in probe.github_output
    assert "listing PR files failed" in probe.stdout
    assert "step-start" in probe.stdout
    assert "step-end" in probe.stdout


def test_stubborn_classifier_is_killed_and_the_step_stays_fail_open(tmp_path):
    run = _classify_run(WORKFLOWS[0])
    grace, limit = _bound_for("python3")
    probe = _probe_step(
        tmp_path,
        run,
        gh_body=_QUICK_GH,
        classifier_body=_STUBBORN_CLASSIFIER,
        hard_cap=limit + grace + CAP_MARGIN_SECONDS,
    )
    assert probe.finished, (
        f"step never finished within {limit + grace + CAP_MARGIN_SECONDS}s: the "
        "classifier boundary does not hold a SIGTERM-ignoring child"
    )
    assert probe.elapsed <= limit + grace + SLACK_SECONDS
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "review_expected=true" in probe.github_output
    assert "review_expected=false" not in probe.github_output
    assert "classifier failed" in probe.stdout
    assert "step-start" in probe.stdout
    assert "step-end" in probe.stdout


def test_healthy_listing_still_classifies_to_false(tmp_path):
    """Control: with both stubs well-behaved the step's real output is unchanged."""
    run = _classify_run(WORKFLOWS[0])
    probe = _probe_step(
        tmp_path,
        run,
        gh_body=_QUICK_GH,
        classifier_body=REAL_CLASSIFIER.read_text(encoding="utf-8"),
        hard_cap=30,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.github_output.splitlines() == ["review_expected=true", "review_expected=false"]
    assert "listing PR files failed" not in probe.stdout
    assert "classifier failed" not in probe.stdout
    assert "step-start" in probe.stdout
    assert "step-end" in probe.stdout
