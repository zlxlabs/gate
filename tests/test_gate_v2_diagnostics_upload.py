"""Contracts for the primary failure/timeout diagnostics handoff."""

import json
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
UPLOAD_ACTION = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DIAGNOSTICS_PATH = "${{ runner.temp }}/primary-review-diagnostics/"


def _primary_steps():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["primary"]["steps"]


def test_primary_writes_restricted_manifest_before_diagnostics_upload():
    steps = _primary_steps()
    manifest_index = next(
        i for i, step in enumerate(steps)
        if step.get("name") == "Write primary review diagnostics manifest"
    )
    upload_index = next(
        i for i, step in enumerate(steps)
        if step.get("name") == "Upload primary review diagnostics"
    )
    manifest = steps[manifest_index]
    upload = steps[upload_index]
    audit_index = next(
        i for i, step in enumerate(steps)
        if step.get("name") == "Upload canonical primary audit"
    )
    assert manifest_index < audit_index < upload_index
    assert manifest["if"] == "always()"
    assert manifest["continue-on-error"] is True
    assert manifest["shell"] == "bash"
    assert manifest["env"] == {
        "DIAGNOSTICS_DIR": "${{ runner.temp }}/primary-review-diagnostics",
        "RUN_ATTEMPT": "${{ github.run_attempt }}",
    }
    run = manifest["run"]
    for field in ("directory_exists", "file_count", "other_file_count", '"name"', '"bytes"', '"mtime"'):
        assert field in run
    assert "attempt-[0-9]+" in run and "raw-output" in run
    assert "::warning::" in run
    assert not any(token in run for token in ("cat ", "read_bytes", "read_text(", "response_body"))
    assert upload["if"] == "always()"
    assert upload["uses"] == UPLOAD_ACTION
    assert upload["with"]["path"] == DIAGNOSTICS_PATH
    assert upload["with"]["if-no-files-found"] == "ignore"


def _manifest_program():
    manifest_step = next(
        step for step in _primary_steps()
        if step.get("name") == "Write primary review diagnostics manifest"
    )
    run_script = manifest_step["run"]
    assert "<<'PY'\n" in run_script and "\nPY\n" in run_script
    return run_script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _run_manifest(program, diagnostics_dir, attempt):
    subprocess.run([sys.executable, "-c", program, str(diagnostics_dir), str(attempt)], check=True)
    return json.loads((diagnostics_dir / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_producer_emits_absent_empty_and_populated_payloads(tmp_path):
    program = _manifest_program()
    absent = _run_manifest(program, tmp_path / "absent", 4)
    assert absent["attempt"] == 4
    assert absent["directory_exists"] is False
    assert absent["file_count"] == absent["other_file_count"] == 0
    assert absent["files"] == []

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    empty = _run_manifest(program, empty_dir, 5)
    assert empty["attempt"] == 5
    assert empty["directory_exists"] is True
    assert empty["file_count"] == empty["other_file_count"] == 0
    assert empty["files"] == []

    populated_dir = tmp_path / "populated"
    populated_dir.mkdir()
    raw_output = populated_dir / "attempt-01-codex-sub-raw-output.txt"
    raw_output.write_bytes(b"{}")
    (populated_dir / "unexpected-provider.stderr").write_text("provider secret", encoding="utf-8")
    populated = _run_manifest(program, populated_dir, 6)
    assert populated["attempt"] == 6
    assert populated["directory_exists"] is True
    assert populated["file_count"] == populated["other_file_count"] == 1
    assert populated["files"] == [{
        "name": raw_output.name,
        "bytes": 2,
        "mtime": raw_output.stat().st_mtime,
    }]
