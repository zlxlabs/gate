"""Producer: quality 'Emit tested evidence' prints one GATE-TESTED-EVIDENCE-V1 line.

Extracts the real `run:` text from gate-v2.yml and executes it the way Actions
does (`bash --noprofile --norc -eo pipefail <file>`). Junit fixtures are
written by real pytest `--junitxml` (root `<testsuites>`). YAML substring
checks alone are not enough.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"

HEAD = "a" * 40
MERGE = "b" * 40
EVIDENCE_RE = re.compile(
    r"^GATE-TESTED-EVIDENCE-V1 head_sha=[0-9a-f]{40} merge_sha=[0-9a-f]{40} mode=(entry|pytest)$"
)
MARKER = "GATE-TESTED-EVIDENCE-V1"
WARN = "GATE-TESTED-EVIDENCE-EMIT-FAILED"
LEGACY_JUNIT_DIR = "${{ runner.temp }}/gate-tested-evidence-junit"
CONTRADICTION_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="99" time="0">
    <testcase classname="t" name="test_ok" time="0.001" />
  </testsuite>
</testsuites>
"""
NESTED_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="outer" errors="0" failures="0" skipped="0" tests="2" time="0">
    <testsuite name="inner-a" errors="0" failures="0" skipped="0" tests="1" time="0">
      <testcase classname="t" name="test_a" time="0.001" />
    </testsuite>
    <testsuite name="inner-b" errors="0" failures="0" skipped="0" tests="1" time="0">
      <testcase classname="t" name="test_b" time="0.001" />
    </testsuite>
  </testsuite>
</testsuites>
"""
ERROR_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1" time="0">
    <testcase classname="t" name="test_e" time="0.001">
      <error message="boom">boom</error>
    </testcase>
  </testsuite>
</testsuites>
"""


def _quality_steps():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["quality"]["steps"]


def _step_by_name(name):
    matches = [step for step in _quality_steps() if step.get("name") == name]
    assert len(matches) == 1, f"{name}: found {len(matches)}"
    return matches[0]


def _step_by_id(step_id):
    matches = [step for step in _quality_steps() if step.get("id") == step_id]
    assert len(matches) == 1, f"{step_id}: found {len(matches)}"
    return matches[0]


def _evidence_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if EVIDENCE_RE.fullmatch(line)]


def _result_json(**overrides) -> bytes:
    payload = {
        "schema": "gate-quality-result/v1",
        "mode": "full",
        "stage": "tests",
        "outcome": "passed",
        "failed_nodeids": [],
        "evidence": "junit",
        "flake_signal": "undetermined",
    }
    payload.update(overrides)
    return (json.dumps(payload) + "\n").encode()


def _junit_xml(tmp_path: Path, body: str, label: str) -> str:
    work = tmp_path / f"jw-{label}"
    work.mkdir(parents=True)
    (work / "mod.py").write_text(body, encoding="utf-8")
    xml = work / "out.xml"
    subprocess.run(
        [
            sys.executable, "-m", "pytest", "mod.py",
            f"--junitxml={xml}", "-q", "-p", "no:cacheprovider",
        ],
        cwd=work, check=False, capture_output=True, text=True,
    )
    assert xml.is_file(), f"pytest did not write junit for {label}"
    text = xml.read_text(encoding="utf-8")
    assert "<testsuites" in text
    return text


def _passing_xml(tmp_path: Path) -> str:
    text = _junit_xml(tmp_path, "def test_ok():\n    assert True\n", "pass")
    assert "<testcase" in text
    return text


def _run_script(step: dict, tmp_path: Path, cwd: Path, env_extra: dict) -> subprocess.CompletedProcess:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "gha-step.sh"
    script.write_text(step["run"], encoding="utf-8")
    env = os.environ.copy()
    env.update(env_extra)
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=cwd, env=env, capture_output=True, text=True, check=False,
    )


def _run_emit(
    tmp_path: Path,
    *,
    mode: str = "entry",
    junit_xml: str | None = None,
    result: bytes | None | object = Ellipsis,
    env_extra: dict | None = None,
    quality_outcome: str = "success",
    tests_outcome: str = "skipped",
) -> tuple[subprocess.CompletedProcess, str]:
    artifact = tmp_path / "gate-quality"
    junit_dir = artifact / "junit"
    junit_dir.mkdir(parents=True)
    legacy = tmp_path / "legacy-junit"
    legacy.mkdir()
    target = junit_dir if mode == "entry" else legacy
    if junit_xml is not None:
        (target / "pytest.xml").write_text(junit_xml, encoding="utf-8")
    if result is Ellipsis and mode == "entry":
        (artifact / "gate-quality-result.json").write_bytes(_result_json())
    elif isinstance(result, (bytes, bytearray)):
        (artifact / "gate-quality-result.json").write_bytes(result)
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    env = {
        "EVENT_NAME": "pull_request",
        "HEAD_SHA": HEAD,
        "GITHUB_SHA": MERGE,
        "QUALITY_MODE": mode,
        "RUN_QUALITY_OUTCOME": quality_outcome,
        "RUN_TESTS_OUTCOME": tests_outcome,
        "GATE_ARTIFACT_DIR": str(artifact),
        "LEGACY_JUNIT_DIR": str(legacy),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    if env_extra:
        env.update(env_extra)
    completed = _run_script(_step_by_name("Emit tested evidence"), tmp_path, tmp_path, env)
    return completed, summary.read_text(encoding="utf-8")


def _install_helper(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    completed = _run_script(
        _step_by_id("install-termination-helper"), tmp_path, tmp_path,
        {"RUNNER_TEMP": str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stderr


def _write_entry(cwd: Path, body: str = "exit 0") -> None:
    scripts = cwd / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    entry = scripts / "gate-quality"
    entry.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    entry.chmod(entry.stat().st_mode | stat.S_IEXEC)


def test_emit_step_wiring_and_source_does_not_echo_evidence_line():
    steps = _quality_steps()
    names = [step.get("name") for step in steps]
    emit = _step_by_name("Emit tested evidence")
    tests = _step_by_name("Tests")
    assert names.index("Tests") < names.index("Emit tested evidence")
    assert names.index("Emit tested evidence") < names.index(
        "Checkout gate actions for diff-coverage advisory"
    )
    assert emit.get("id") == "emit-tested-evidence"
    assert emit.get("if") == "always()"
    assert emit.get("continue-on-error") is True
    env = emit.get("env") or {}
    assert env["EVENT_NAME"] == "${{ github.event_name }}"
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert env["QUALITY_MODE"] == "${{ steps.quality-entry.outputs.mode }}"
    assert env["RUN_QUALITY_OUTCOME"] == "${{ steps.run-quality.outcome }}"
    assert env["RUN_TESTS_OUTCOME"] == "${{ steps.run-tests.outcome }}"
    assert env["GATE_ARTIFACT_DIR"] == "${{ runner.temp }}/gate-quality"
    assert env["LEGACY_JUNIT_DIR"] == LEGACY_JUNIT_DIR
    run = emit["run"]
    assert "set -x" not in run
    assert "<<'PY'" in run
    assert WARN in run
    assert "::warning::" + WARN in run
    text = WORKFLOW.read_text(encoding="utf-8")
    assert MARKER not in text
    assert "PYTEST_ADDOPTS" not in tests["run"]
    assert "PYTEST_ADDOPTS" not in emit["run"]


def test_quality_does_not_hard_fail_on_evidence_prefix_in_logs():
    for step in _quality_steps():
        run = str(step.get("run", ""))
        if MARKER in run or "GATE-TESTED-EVIDENCE" in run:
            assert "exit 1" not in run or WARN in run
            assert step.get("continue-on-error") is True or "|| echo" in run


def test_legacy_junitxml_only_on_pytest_invocations():
    run = _step_by_id("run-tests")["run"]
    assert 'rm -rf "${RUNNER_TEMP:?}/gate-tested-evidence-junit"' in run
    invoke = [line for line in run.splitlines() if "pytest -q" in line]
    assert invoke and all("--junitxml=" in line for line in invoke)
    assert all(
        "--junitxml=" not in line
        for line in run.splitlines()
        if "make test" in line or "npm test" in line or "no tests detected" in line
    )
    assert "PYTEST_ADDOPTS" not in run


def test_run_quality_clears_stale_junit_and_result_json():
    run = _step_by_id("run-quality")["run"]
    assert "gate-quality-result.json" in run
    assert "/junit" in run
    assert "rm -rf" in run
    assert run.strip().startswith("rm -rf") or "rm -rf" in run.splitlines()[0] or run.find("rm -rf") < run.find("gate_run_and_record")


def test_entry_passed_junit_emits_one_line(tmp_path):
    xml = _passing_xml(tmp_path)
    completed, summary = _run_emit(tmp_path, junit_xml=xml)
    assert completed.returncode == 0, completed.stderr
    lines = _evidence_lines(completed.stdout)
    assert lines == [
        f"{MARKER} head_sha={HEAD} merge_sha={MERGE} mode=entry"
    ]
    assert summary == ""
    assert WARN not in completed.stdout


def test_entry_scope_skip_result_does_not_emit(tmp_path):
    xml = _passing_xml(tmp_path)
    completed, summary = _run_emit(
        tmp_path, junit_xml=xml, result=_result_json(stage="setup", outcome="not_started", evidence="not_applicable"),
    )
    assert completed.returncode == 0
    assert _evidence_lines(completed.stdout) == []
    assert "tested-evidence: not_emitted reason=result_unqualified" in summary


def test_entry_missing_result_json_does_not_emit(tmp_path):
    completed, summary = _run_emit(
        tmp_path, junit_xml=_passing_xml(tmp_path), result=None,
    )
    assert completed.returncode == 0
    assert _evidence_lines(completed.stdout) == []
    assert "reason=result_unqualified" in summary


def test_entry_junit_unqualified_does_not_emit(tmp_path):
    cases = {
        "skip": (
            _junit_xml(tmp_path, "import pytest\ndef test_skip():\n    pytest.skip('x')\n", "skip"),
            "junit_all_skipped",
        ),
        "fail": (
            _junit_xml(tmp_path, "def test_fail():\n    assert False\n", "fail"),
            "junit_failed",
        ),
        "error": (ERROR_XML, "junit_failed"),
        "corrupt": ("this is not xml", "junit_invalid"),
        "contradiction": (CONTRADICTION_XML, "junit_invalid"),
    }
    for label, (xml, reason) in cases.items():
        completed, summary = _run_emit(tmp_path / label, junit_xml=xml)
        assert completed.returncode == 0, (label, completed.stderr)
        assert _evidence_lines(completed.stdout) == [], label
        assert f"reason={reason}" in summary, (label, summary)


def test_entry_zero_tests_does_not_emit(tmp_path):
    work = tmp_path / "jw-empty"
    work.mkdir()
    (work / "mod.py").write_text("# no tests\n", encoding="utf-8")
    xml = work / "out.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", "mod.py", f"--junitxml={xml}", "-q", "-p", "no:cacheprovider"],
        cwd=work, check=False, capture_output=True, text=True,
    )
    assert xml.is_file()
    text = xml.read_text(encoding="utf-8")
    assert "<testsuites" in text
    completed, summary = _run_emit(tmp_path, junit_xml=text)
    assert completed.returncode == 0
    assert _evidence_lines(completed.stdout) == []
    assert "reason=junit_empty" in summary


def test_entry_missing_junit_does_not_emit(tmp_path):
    completed, summary = _run_emit(tmp_path, junit_xml=None)
    assert completed.returncode == 0
    assert _evidence_lines(completed.stdout) == []
    assert "reason=junit_missing" in summary


def test_entry_junit_ok_but_step_failure_does_not_emit(tmp_path):
    completed, summary = _run_emit(
        tmp_path, junit_xml=_passing_xml(tmp_path), quality_outcome="failure",
    )
    assert completed.returncode == 0
    assert _evidence_lines(completed.stdout) == []
    assert "reason=step_not_success" in summary


def test_nested_suites_count_testcases_once_and_emit(tmp_path):
    completed, summary = _run_emit(tmp_path, junit_xml=NESTED_XML)
    assert completed.returncode == 0, completed.stderr
    assert _evidence_lines(completed.stdout) == [
        f"{MARKER} head_sha={HEAD} merge_sha={MERGE} mode=entry"
    ]
    assert summary == ""


def test_mixed_pass_and_skip_still_emits(tmp_path):
    xml = _junit_xml(
        tmp_path,
        "import pytest\ndef test_ok():\n    assert True\ndef test_skip():\n    pytest.skip('x')\n",
        "mix",
    )
    completed, summary = _run_emit(tmp_path, junit_xml=xml)
    assert completed.returncode == 0
    assert _evidence_lines(completed.stdout) == [
        f"{MARKER} head_sha={HEAD} merge_sha={MERGE} mode=entry"
    ]


def test_stale_entry_reports_cleared_by_run_quality(tmp_path):
    _install_helper(tmp_path)
    artifact = tmp_path / "gate-quality"
    junit = artifact / "junit"
    junit.mkdir(parents=True)
    (junit / "old.xml").write_text(_passing_xml(tmp_path / "stale"), encoding="utf-8")
    (artifact / "gate-quality-result.json").write_bytes(_result_json())
    cwd = tmp_path / "src"
    _write_entry(cwd, "exit 0")
    github_output = tmp_path / "github-output"
    completed = _run_script(
        _step_by_id("run-quality"), tmp_path / "runq", cwd,
        {
            "GITHUB_OUTPUT": str(github_output),
            "RUNNER_TEMP": str(tmp_path),
            "GATE_ARTIFACT_DIR": str(artifact),
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert not (junit / "old.xml").exists()
    assert not (artifact / "gate-quality-result.json").exists()
    assert junit.is_dir()
    emit, summary = _run_emit(
        tmp_path / "emit",
        env_extra={
            "GATE_ARTIFACT_DIR": str(artifact),
            "RUN_QUALITY_OUTCOME": "success",
        },
        junit_xml=None, result=None,
    )
    assert emit.returncode == 0
    assert _evidence_lines(emit.stdout) == []
    assert "not_emitted" in summary


def test_legacy_pytest_success_emits_mode_pytest(tmp_path):
    completed, summary = _run_emit(
        tmp_path, mode="legacy", junit_xml=_passing_xml(tmp_path),
        tests_outcome="success", quality_outcome="skipped",
    )
    assert completed.returncode == 0, completed.stderr
    assert _evidence_lines(completed.stdout) == [
        f"{MARKER} head_sha={HEAD} merge_sha={MERGE} mode=pytest"
    ]
    assert summary == ""


def test_legacy_success_without_junit_does_not_emit(tmp_path):
    completed, summary = _run_emit(
        tmp_path, mode="legacy", junit_xml=None,
        tests_outcome="success", quality_outcome="skipped",
    )
    assert completed.returncode == 0
    assert _evidence_lines(completed.stdout) == []
    assert "reason=junit_missing" in summary


def test_legacy_make_test_clears_dir_and_writes_no_xml(tmp_path):
    _install_helper(tmp_path)
    cwd = tmp_path / "src"
    cwd.mkdir()
    (cwd / "Makefile").write_text(".PHONY: test\ntest:\n\ttrue\n", encoding="utf-8")
    junit_dir = tmp_path / "gate-tested-evidence-junit"
    junit_dir.mkdir()
    (junit_dir / "stale.xml").write_text(_passing_xml(tmp_path / "make-stale"), encoding="utf-8")
    completed = _run_script(
        _step_by_id("run-tests"), tmp_path / "runt", cwd,
        {
            "GITHUB_OUTPUT": str(tmp_path / "github-output"),
            "RUNNER_TEMP": str(tmp_path),
        },
    )
    assert completed.returncode == 0, completed.stderr
    xmls = list(junit_dir.rglob("*.xml")) if junit_dir.exists() else []
    assert xmls == []


def test_non_pull_request_and_invalid_sha_do_not_emit(tmp_path):
    xml = _passing_xml(tmp_path)
    cases = [
        ({"EVENT_NAME": "push"}, "not_pull_request"),
        ({"EVENT_NAME": "pull_request_target"}, "not_pull_request"),
        ({"EVENT_NAME": "workflow_dispatch"}, "not_pull_request"),
        ({"HEAD_SHA": ""}, "invalid_sha"),
        ({"HEAD_SHA": "A" * 40}, "invalid_sha"),
        ({"HEAD_SHA": "a" * 39}, "invalid_sha"),
        ({"GITHUB_SHA": "c" * 39}, "invalid_sha"),
    ]
    for extra, reason in cases:
        completed, summary = _run_emit(tmp_path / reason / extra.get("EVENT_NAME", extra.get("HEAD_SHA", "x")[:4]), junit_xml=xml, env_extra=extra)
        assert completed.returncode == 0, extra
        assert _evidence_lines(completed.stdout) == [], extra
        assert f"reason={reason}" in summary, extra


def test_emit_failed_warning_keeps_exit_zero(tmp_path):
    summary_dir = tmp_path / "sumdir"
    summary_dir.mkdir()
    completed, _summary = _run_emit(
        tmp_path,
        env_extra={"EVENT_NAME": "push", "GITHUB_STEP_SUMMARY": str(summary_dir)},
        junit_xml=_passing_xml(tmp_path),
    )
    assert completed.returncode == 0
    assert WARN in completed.stdout
    assert _evidence_lines(completed.stdout) == []
