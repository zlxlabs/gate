"""SIGTERM'd quality tests are infrastructure (gate-hub#1696).

Execute the real gate-v2.yml Tests/Record scripts against a live SIGTERM'd
subprocess, then feed the written GITHUB_OUTPUT bytes into aggregate.py.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2.yml"
AGGREGATOR = REPO_ROOT / ".github/actions/gate-aggregator/aggregate.py"


def _step(step_id: str) -> dict:
    raw = yaml.safe_load(WORKFLOW.read_text())
    return next(s for s in raw["jobs"]["quality"]["steps"] if s.get("id") == step_id)


def _output_map(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def _bash(script: str, env: dict[str, str], cwd=None, check=False):
    return subprocess.run(
        ["bash", "-c", script], cwd=cwd, env=env, capture_output=True, text=True, check=check,
    )


def _run_tests(tmp_path: Path, cwd: Path, env_extra=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "github-output"
    env = os.environ.copy()
    env.update({"GITHUB_OUTPUT": str(output), "RUNNER_TEMP": str(tmp_path)})
    if env_extra:
        env.update(env_extra)
    completed = _bash(_step("run-tests")["run"], env, cwd=cwd)
    return completed, _output_map(output) if output.exists() else {}


def _run_record(tmp_path: Path, tests_outcome: str, tests_out: dict[str, str]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "github-output"
    env = os.environ.copy()
    env.update({
        "GITHUB_OUTPUT": str(output),
        "PREFLIGHT_OUTCOME": "success", "PREFLIGHT_STATUS": "success",
        "RUN_QUALITY_OUTCOME": "success", "LINT_FORMAT_OUTCOME": "success",
        "INSTALL_OUTCOME": "success", "RUN_TESTS_OUTCOME": tests_outcome,
        "RUN_TESTS_TERMINATION": tests_out.get("termination", ""),
        "RUN_TESTS_SIGNAL": tests_out.get("signal", ""),
        "RUN_TESTS_EXIT_CODE": tests_out.get("exit_code", ""),
    })
    _bash(_step("caller-checks-outcome")["run"], env, check=True)
    return _output_map(output)


def _makefile(cwd: Path, recipe: str) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "Makefile").write_text(".PHONY: test\ntest:\n\t" + recipe + "\n")


def _aggregate(tmp_path: Path, caller_checks: str, caller_signal: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps({
        "kind": "primary_review", "schema_version": 1, "repository_id": 123,
        "head_sha": "a" * 40, "run_id": 999, "run_attempt": 1, "pr": 42,
        "verdict": "pass", "reviewer": "test",
    }))
    summary, terminal = tmp_path / "summary.md", tmp_path / "gate-terminal.json"
    completed = subprocess.run(
        [sys.executable, str(AGGREGATOR),
         "--quality-result", "failure", "--caller-checks", caller_checks,
         "--caller-signal", caller_signal, "--preflight-result", "success",
         "--primary-result", "success", "--runner", "self",
         "--is-draft", "false", "--is-fork", "false",
         "--classify-review-expected", "true", "--review-expected", "true",
         "--pr-author-json", json.dumps("test-human"),
         "--repository-id", "123", "--repository", "zlxlabs/gate",
         "--head-sha", "a" * 40, "--run-id", "999", "--run-attempt", "1",
         "--pr-number", "42", "--audit-source-attempt", "1",
         "--audit-artifact-name", "primary-audit-v2-1",
         "--audit-dir", str(audit_dir), "--summary-path", str(summary),
         "--terminal-path", str(terminal)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    return completed, summary.read_text(), json.loads(terminal.read_text())


def test_workflow_wires_signal_terminated_outputs():
    raw = yaml.safe_load(WORKFLOW.read_text())
    tests, record = _step("run-tests"), _step("caller-checks-outcome")
    assert "SIGNAL_PREDICATE" in tests["run"] and "CI-SLOT-MEM:" in tests["run"]
    assert record["env"]["RUN_TESTS_TERMINATION"] == "${{ steps.run-tests.outputs.termination }}"
    assert "caller_checks=signal_terminated" in record["run"]
    assert raw["jobs"]["quality"]["outputs"]["caller_signal"] == (
        "${{ steps.caller-checks-outcome.outputs.caller_signal }}"
    )
    consumers = [
        s for s in raw["jobs"]["gate"]["steps"]
        if "aggregate.py" in str(s.get("run", "")) and "QUALITY_RESULT" in str(s.get("env", ""))
    ]
    assert len(consumers) == 2
    for step in consumers:
        assert step["env"]["QUALITY_CALLER_SIGNAL"] == "${{ needs.quality.outputs.caller_signal }}"
        assert '--caller-signal "$QUALITY_CALLER_SIGNAL"' in step["run"]


def test_make_sigterm_producer_writes_signal_terminated(tmp_path):
    cwd = tmp_path / "src"
    # Real SIGTERM; `true;` keeps dash from exec'ing, so make records Error 143.
    _makefile(cwd, "true; sh -c 'kill -TERM $$$$'")
    completed, outputs = _run_tests(tmp_path / "out", cwd)
    assert completed.returncode != 0
    assert "Error 143" in completed.stdout + completed.stderr
    assert outputs["termination"] == "signal_terminated"
    assert outputs["signal"] == "15"


def test_direct_sigterm_producer_writes_signal_terminated(tmp_path):
    cwd = tmp_path / "src"
    cwd.mkdir()
    (cwd / "pyproject.toml").write_text("[project]\nname = \"sigterm-fixture\"\nversion = \"0\"\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pytest_bin = fake_bin / "pytest"
    pytest_bin.write_text("#!/bin/sh\nkill -TERM $$\n")
    pytest_bin.chmod(pytest_bin.stat().st_mode | stat.S_IEXEC)
    completed, outputs = _run_tests(tmp_path / "out", cwd, {"PATH": f"{fake_bin}:/usr/bin:/bin"})
    assert completed.returncode == 143
    assert outputs["termination"] == "signal_terminated"
    assert outputs["signal"] == "15"


def test_make_error_1_stays_failed(tmp_path):
    cwd = tmp_path / "src"
    _makefile(cwd, "sh -c 'exit 1'")
    completed, outputs = _run_tests(tmp_path / "out", cwd)
    assert completed.returncode != 0
    assert "Error 1" in completed.stdout + completed.stderr
    assert outputs["termination"] == "failed" and outputs["signal"] == "0"
    record = _run_record(tmp_path / "record", "failure", outputs)
    assert record["caller_checks"] == "failed"
    agg, summary, terminal = _aggregate(tmp_path / "agg", record["caller_checks"], "")
    assert agg.returncode == 1
    assert (terminal["classification"], terminal["reason_code"]) == ("ci_failure", "quality_failure")
    assert "quality_failure" in summary


def test_e2e_sigterm_make_reaches_quality_infra(tmp_path):
    cwd = tmp_path / "src"
    _makefile(cwd, "true; sh -c 'kill -TERM $$$$'")
    tests_run, tests_out = _run_tests(tmp_path / "tests", cwd)
    assert tests_run.returncode != 0
    assert tests_out["termination"] == "signal_terminated" and tests_out["signal"] == "15"
    record = _run_record(tmp_path / "record", "failure", tests_out)
    assert record["caller_checks"] == "signal_terminated" and record["caller_signal"] == "15"
    agg, summary, terminal = _aggregate(tmp_path / "agg", record["caller_checks"], record["caller_signal"])
    assert agg.returncode == 1
    assert terminal["gate_result"] == "unavailable"
    assert (terminal["classification"], terminal["reason_code"]) == ("review_unavailable", "quality_infra")
    assert "signal 15" in summary and "not a code problem" in summary and "quality_infra" in summary
