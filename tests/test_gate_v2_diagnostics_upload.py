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
    indexes = {step.get("name"): i for i, step in enumerate(steps)}
    manifest_index = indexes["Write primary review diagnostics manifest"]
    audit_index = indexes["Upload canonical primary audit"]
    upload_index = indexes["Upload primary review diagnostics"]
    manifest, upload = steps[manifest_index], steps[upload_index]
    assert manifest_index < audit_index < upload_index
    assert manifest["if"] == "always()"
    assert manifest["continue-on-error"] is True
    assert manifest["shell"] == "bash"
    assert manifest["env"]["DIAGNOSTICS_DIR"] == "${{ runner.temp }}/primary-review-diagnostics"
    assert manifest["env"]["RUN_ATTEMPT"] == "${{ github.run_attempt }}"
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
    step = next(
        step for step in _primary_steps()
        if step.get("name") == "Write primary review diagnostics manifest"
    )
    run_script = step["run"]
    assert "<<'PY'\n" in run_script and "\nPY\n" in run_script
    return run_script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _run_manifest(program, diagnostics_dir, attempt):
    subprocess.run([sys.executable, "-c", program, str(diagnostics_dir), str(attempt)], check=True)
    return json.loads((diagnostics_dir / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_producer_emits_absent_empty_and_populated_payloads(tmp_path):
    program = _manifest_program()
    for name, attempt in (("absent", 4), ("empty", 5), ("populated", 6)):
        diagnostics_dir = tmp_path / name
        expected_files = []
        if name != "absent":
            diagnostics_dir.mkdir()
        if name == "populated":
            raw_output = diagnostics_dir / "attempt-01-codex-sub-raw-output.txt"
            raw_output.write_bytes(b"{}")
            (diagnostics_dir / "unexpected-provider.stderr").write_text("provider secret", encoding="utf-8")
            expected_files = [{"name": raw_output.name, "bytes": 2, "mtime": raw_output.stat().st_mtime}]
        manifest = _run_manifest(program, diagnostics_dir, attempt)
        assert manifest == {
            "schema_version": 1,
            "attempt": attempt,
            "directory_exists": name != "absent",
            "file_count": len(expected_files),
            "other_file_count": int(name == "populated"),
            "files": expected_files,
        }
