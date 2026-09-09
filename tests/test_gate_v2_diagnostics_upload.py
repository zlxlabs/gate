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
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["primary"]["steps"]


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
    assert manifest_index < upload_index
    audit_index = next(
        i for i, step in enumerate(steps)
        if step.get("name") == "Upload canonical primary audit"
    )
    assert manifest_index < audit_index < upload_index
    assert manifest["if"] == "always()"
    assert manifest["continue-on-error"] is True
    assert manifest["env"] == {
        "DIAGNOSTICS_DIR": "${{ runner.temp }}/primary-review-diagnostics",
        "RUN_ATTEMPT": "${{ github.run_attempt }}",
    }
    run = manifest["run"]
    for field in ("directory_exists", "file_count", '"name"', '"bytes"', '"mtime"'):
        assert field in run
    assert "allowed_names" in run
    assert "::warning::" in run
    assert not any(token in run for token in ("cat ", "read_bytes", "read_text(", "response_body"))
    assert upload["if"] == "always()"
    assert upload["uses"] == UPLOAD_ACTION
    assert upload["with"]["path"] == DIAGNOSTICS_PATH
    assert upload["with"]["if-no-files-found"] == "ignore"


def test_manifest_producer_emits_metadata_only_payload(tmp_path):
    manifest_step = next(
        step for step in _primary_steps()
        if step.get("name") == "Write primary review diagnostics manifest"
    )
    run_script = manifest_step["run"]
    assert "<<'PY'\n" in run_script and "\nPY\n" in run_script
    program = run_script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    diagnostics_dir = tmp_path / "primary-review-diagnostics"

    subprocess.run([sys.executable, "-c", program, str(diagnostics_dir), "4"], check=True)
    payload = json.loads((diagnostics_dir / "manifest.json").read_text(encoding="utf-8"))
    assert payload["attempt"] == 4
    assert payload["directory_exists"] is False
    assert payload["file_count"] == 0
    assert payload["files"] == []
    (diagnostics_dir / "provider-diagnostics.json").write_bytes(b"{}")
    (diagnostics_dir / "provider.stderr").write_text("provider secret", encoding="utf-8")
    subprocess.run([sys.executable, "-c", program, str(diagnostics_dir), "5"], check=True)
    payload = json.loads((diagnostics_dir / "manifest.json").read_text(encoding="utf-8"))
    assert payload["directory_exists"] is True
    assert payload["file_count"] == 1
    assert payload["files"][0]["name"] == "provider-diagnostics.json"
    assert payload["files"][0]["bytes"] == 2
    assert isinstance(payload["files"][0]["mtime"], (int, float))
