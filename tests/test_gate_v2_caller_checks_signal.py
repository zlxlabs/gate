"""SIGTERM'd quality tests are infrastructure (gate-hub#1696).

Execute the real gate-v2.yml helper + Tests/entry/Record scripts against a
live SIGTERM'd subprocess, then feed the written GITHUB_OUTPUT bytes into
aggregate.py.
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


def _load():
    return yaml.safe_load(WORKFLOW.read_text())


def _step(step_id: str) -> dict:
    return next(s for s in _load()["jobs"]["quality"]["steps"] if s.get("id") == step_id)


def _output_map(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def _bash(script: str, env: dict[str, str], cwd=None, check=False):
    return subprocess.run(
        ["bash", "-c", script], cwd=cwd, env=env, capture_output=True, text=True, check=check,
    )


def _install_helper(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["RUNNER_TEMP"] = str(tmp_path)
    _bash(_step("install-termination-helper")["run"], env, check=True)
    assert (tmp_path / "gate_record_termination.sh").is_file()


def _run_tests(tmp_path: Path, cwd: Path, env_extra=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _install_helper(tmp_path)
    output = tmp_path / "github-output"
    env = os.environ.copy()
    env.update({"GITHUB_OUTPUT": str(output), "RUNNER_TEMP": str(tmp_path)})
    if env_extra:
        env.update(env_extra)
    completed = _bash(_step("run-tests")["run"], env, cwd=cwd)
    return completed, _output_map(output) if output.exists() else {}


def _run_entry(tmp_path: Path, cwd: Path):
    """Run entry via the real helper + the real `./scripts/gate-quality` command."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    _install_helper(tmp_path)
    output = tmp_path / "github-output"
    env = os.environ.copy()
    env.update({"GITHUB_OUTPUT": str(output), "RUNNER_TEMP": str(tmp_path)})
    step = _step("run-quality")
    assert step["run"].strip() == "./scripts/gate-quality"
    script_file = tmp_path / "gha-run.sh"
    script_file.write_text(step["run"])
    cmd = step["shell"].replace("{0}", str(script_file))
    completed = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False, shell=True,
    )
    return completed, _output_map(output) if output.exists() else {}


def _run_record(tmp_path: Path, *, tests_outcome="success", tests_out=None,
                quality_outcome="success", quality_out=None,
                lint_outcome="success", install_outcome="success"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tests_out = tests_out or {}
    quality_out = quality_out or {}
    output = tmp_path / "github-output"
    env = os.environ.copy()
    env.update({
        "GITHUB_OUTPUT": str(output),
        "PREFLIGHT_OUTCOME": "success", "PREFLIGHT_STATUS": "success",
        "RUN_QUALITY_OUTCOME": quality_outcome,
        "RUN_QUALITY_TERMINATION": quality_out.get("termination", ""),
        "RUN_QUALITY_SIGNAL": quality_out.get("signal", ""),
        "RUN_QUALITY_EXIT_CODE": quality_out.get("exit_code", ""),
        "LINT_FORMAT_OUTCOME": lint_outcome,
        "INSTALL_OUTCOME": install_outcome,
        "RUN_TESTS_OUTCOME": tests_outcome,
        "RUN_TESTS_TERMINATION": tests_out.get("termination", ""),
        "RUN_TESTS_SIGNAL": tests_out.get("signal", ""),
        "RUN_TESTS_EXIT_CODE": tests_out.get("exit_code", ""),
    })
    _bash(_step("caller-checks-outcome")["run"], env, check=True)
    return _output_map(output)


def _makefile(cwd: Path, recipe: str) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "Makefile").write_text(".PHONY: test\ntest:\n\t" + recipe + "\n")


def _write_entry(cwd: Path) -> None:
    scripts = cwd / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    entry = scripts / "gate-quality"
    entry.write_text("#!/bin/sh\nexec make test\n")
    entry.chmod(entry.stat().st_mode | stat.S_IEXEC)


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
    raw = _load()
    text = WORKFLOW.read_text()
    helper = _step("install-termination-helper")
    tests, record, quality = _step("run-tests"), _step("caller-checks-outcome"), _step("run-quality")
    assert text.count("SIGNAL_PREDICATE") == 1
    assert "SIGNAL_PREDICATE" in helper["run"] and "CI-SLOT-MEM:" in helper["run"]
    assert "Nested make" in helper["run"]
    assert "gate_run_and_record run_tests" in tests["run"]
    assert "SIGNAL_PREDICATE" not in tests["run"]
    assert quality["run"].strip() == "./scripts/gate-quality"
    assert "gate_record_termination.sh" in quality["shell"]
    assert "gate_run_and_record" in quality["shell"]
    assert record["env"]["RUN_TESTS_TERMINATION"] == "${{ steps.run-tests.outputs.termination }}"
    assert record["env"]["RUN_QUALITY_TERMINATION"] == "${{ steps.run-quality.outputs.termination }}"
    assert "caller_checks=signal_terminated" in record["run"]
    assert "other_failed" in record["run"]
    consumers = [
        s for s in raw["jobs"]["gate"]["steps"]
        if "aggregate.py" in str(s.get("run", "")) and "QUALITY_RESULT" in str(s.get("env", ""))
    ]
    assert len(consumers) == 2
    for step in consumers:
        assert '--caller-signal "$QUALITY_CALLER_SIGNAL"' in step["run"]


def test_make_sigterm_producer_writes_signal_terminated(tmp_path):
    cwd = tmp_path / "src"
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


def test_entry_sigterm_producer_writes_signal_terminated(tmp_path):
    cwd = tmp_path / "src"
    _makefile(cwd, "true; sh -c 'kill -TERM $$$$'")
    _write_entry(cwd)
    completed, outputs = _run_entry(tmp_path / "out", cwd)
    assert completed.returncode != 0
    assert "Error 143" in completed.stdout + completed.stderr
    assert outputs["termination"] == "signal_terminated"
    assert outputs["signal"] == "15"


def test_make_error_1_stays_failed(tmp_path):
    cwd = tmp_path / "src"
    _makefile(cwd, "sh -c 'exit 1'")
    completed, outputs = _run_tests(tmp_path / "out", cwd)
    assert completed.returncode != 0
    assert "Error 1" in completed.stdout + completed.stderr
    assert outputs["termination"] == "failed" and outputs["signal"] == "0"
    record = _run_record(tmp_path / "record", tests_outcome="failure", tests_out=outputs)
    assert record["caller_checks"] == "failed"
    agg, summary, terminal = _aggregate(tmp_path / "agg", record["caller_checks"], "")
    assert agg.returncode == 1
    assert (terminal["classification"], terminal["reason_code"]) == ("ci_failure", "quality_failure")
    assert "quality_failure" in summary


def test_lint_failure_outranks_test_signal(tmp_path):
    cwd = tmp_path / "src"
    _makefile(cwd, "true; sh -c 'kill -TERM $$$$'")
    completed, outputs = _run_tests(tmp_path / "out", cwd)
    assert outputs["termination"] == "signal_terminated"
    record = _run_record(
        tmp_path / "record", tests_outcome="failure", tests_out=outputs, lint_outcome="failure",
    )
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
    record = _run_record(tmp_path / "record", tests_outcome="failure", tests_out=tests_out)
    assert record["caller_checks"] == "signal_terminated" and record["caller_signal"] == "15"
    agg, summary, terminal = _aggregate(
        tmp_path / "agg", record["caller_checks"], record["caller_signal"],
    )
    assert agg.returncode == 1
    assert terminal["gate_result"] == "unavailable"
    assert (terminal["classification"], terminal["reason_code"]) == ("review_unavailable", "quality_infra")
    assert "signal 15" in summary and "not a code problem" in summary and "quality_infra" in summary


def test_e2e_entry_sigterm_reaches_quality_infra(tmp_path):
    cwd = tmp_path / "src"
    _makefile(cwd, "true; sh -c 'kill -TERM $$$$'")
    _write_entry(cwd)
    entry_run, entry_out = _run_entry(tmp_path / "entry", cwd)
    assert entry_run.returncode != 0
    assert entry_out["termination"] == "signal_terminated" and entry_out["signal"] == "15"
    record = _run_record(
        tmp_path / "record", quality_outcome="failure", quality_out=entry_out, tests_outcome="skipped",
    )
    assert record["caller_checks"] == "signal_terminated" and record["caller_signal"] == "15"
    agg, summary, terminal = _aggregate(
        tmp_path / "agg", record["caller_checks"], record["caller_signal"],
    )
    assert agg.returncode == 1
    assert (terminal["classification"], terminal["reason_code"]) == ("review_unavailable", "quality_infra")
    assert "signal 15" in summary and "quality_infra" in summary
