"""Receipt production and fail-closed consumption across JSON and ZIP boundaries."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "quality_reuse.py"
SPEC = importlib.util.spec_from_file_location("quality_reuse_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
quality_reuse = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = quality_reuse
SPEC.loader.exec_module(quality_reuse)

VALUES = {
    "repository_id": 12345,
    "pr_number": 67,
    "head_sha": "a" * 40,
    "base_sha": "b" * 40,
    "merge_tree": "c" * 40,
    "runner": "hosted",
    "extra_runner_label_ci": "",
    "tier": "personal",
    "source_run_id": 321,
    "caller_checks": "passed",
    "preflight_result": "success",
}
REQUEST = quality_reuse.ReuseRequest(
    repository="owner/repo", repository_id=12345, pr_number=67,
    head_sha="a" * 40, base_sha="b" * 40, merge_tree="c" * 40,
    runner="hosted", extra_runner_label_ci="", tier="personal",
    current_run_id=999, event_action="ready_for_review",
)


def _build_receipt(tmp_path: Path, **overrides) -> bytes:
    values = {**VALUES, **overrides}
    output = tmp_path / "receipt.json"
    args = [sys.executable, str(SCRIPT), "build-receipt"]
    for key, value in values.items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    args.extend(["--out", str(output)])
    result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, check=False)
    assert result.returncode == 0, result.stderr
    return output.read_bytes()


def _zip(payload: bytes, filename: str = quality_reuse.RECEIPT_FILENAME) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, payload)
    return buffer.getvalue()


def _api(zip_bytes: bytes, *, conclusion="success", run_id=321):
    artifact_name = quality_reuse.artifact_name_for(12345, 67, "a" * 40)
    def fetch(url: str, timeout: float) -> bytes:
        assert timeout > 0
        if "/actions/artifacts?name=" in url:
            return json.dumps({"artifacts": [{"id": 88, "name": artifact_name,
                                               "expired": False, "workflow_run": {"id": run_id}}]}).encode()
        if url.endswith("/actions/artifacts/88/zip"):
            return zip_bytes
        if "/actions/runs/" in url and "/jobs?" in url:
            jobs = [] if conclusion is None else [{"name": "quality", "conclusion": conclusion}]
            return json.dumps({"jobs": jobs}).encode()
        raise AssertionError(f"unexpected API URL {url}")
    return fetch


def _run_lookup(fetcher, tmp_path: Path, *, request=REQUEST):
    output = tmp_path / "github-output"
    args = ["lookup", "--repository", request.repository,
            "--repository-id", str(request.repository_id), "--pr-number", str(request.pr_number),
            "--head-sha", request.head_sha, "--base-sha", request.base_sha,
            "--merge-tree", request.merge_tree, "--runner", request.runner,
            "--extra-runner-label-ci", request.extra_runner_label_ci,
            "--tier", request.tier, "--current-run-id", str(request.current_run_id),
            "--event-action", request.event_action, "--github-output", str(output), "--timeout", "3"]
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = quality_reuse.main(args, fetcher)
    return result, stdout.getvalue(), output.read_text(encoding="utf-8")


def test_build_cli_bytes_round_trip_through_real_zip_and_lookup(tmp_path):
    receipt_bytes = _build_receipt(tmp_path)
    assert receipt_bytes.endswith(b"\n")
    payload = json.loads(receipt_bytes)
    assert payload == quality_reuse.build_receipt(**VALUES)
    packed = _zip(receipt_bytes)
    result, stdout, output = _run_lookup(_api(packed), tmp_path)
    assert result == 0
    assert stdout.strip() == "GATE-QUALITY-REUSE-V1 reusable=true reason=reusable source_run_id=321"
    assert output.splitlines() == [
        "quality_reusable=true", "reuse_reason=reusable", "reuse_source_run_id=321",
        "reuse_caller_checks=passed", "reuse_preflight_result=success",
    ]


@pytest.mark.parametrize("field,value", [
    ("base_sha", "d" * 40), ("merge_tree", "d" * 40),
    ("runner", "self"), ("extra_runner_label_ci", "custom"), ("tier", "internal"),
])
def test_identity_change_never_reuses_even_with_same_head(tmp_path, field, value):
    payload = json.loads(_build_receipt(tmp_path))
    packed = _zip(json.dumps({**payload, field: value}).encode())
    decision = quality_reuse.lookup_reusable_quality(REQUEST, _api(packed))
    assert not decision.reusable and decision.reason == "receipt_mismatch"


@pytest.mark.parametrize("action", ["opened", "synchronize", "converted_to_draft", "reopened", ""])
def test_only_ready_for_review_can_reuse(tmp_path, action):
    calls = []
    request = quality_reuse.ReuseRequest(**{**REQUEST.__dict__, "event_action": action})
    decision = quality_reuse.lookup_reusable_quality(request, lambda url, timeout: calls.append(url))
    assert not decision.reusable and decision.reason == "event_not_ready_for_review"
    assert calls == []


def test_current_run_cannot_reuse_its_own_artifact(tmp_path):
    payload = json.loads(_build_receipt(tmp_path, source_run_id=999))
    decision = quality_reuse.lookup_reusable_quality(REQUEST, _api(_zip(json.dumps(payload).encode()), run_id=999))
    assert not decision.reusable and decision.reason == "source_run_is_current"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", None])
def test_quality_job_must_be_authoritatively_successful(tmp_path, conclusion):
    packed = _zip(_build_receipt(tmp_path))
    decision = quality_reuse.lookup_reusable_quality(REQUEST, _api(packed, conclusion=conclusion))
    assert not decision.reusable and decision.reason == "quality_job_not_success"


@pytest.mark.parametrize("field,value,reason", [
    ("caller_checks", "failed", "caller_checks_not_passed"),
    ("preflight_result", "blocked", "preflight_blocked"),
])
def test_receipt_quality_gate_evidence_must_pass(tmp_path, field, value, reason):
    payload = json.loads(_build_receipt(tmp_path, **{field: value}))
    decision = quality_reuse.lookup_reusable_quality(REQUEST, _api(_zip(json.dumps(payload).encode())))
    assert not decision.reusable and decision.reason == reason


@pytest.mark.parametrize("mode,reason", [
    ("api", "api_error"), ("zip", "zip_invalid"), ("missing_file", "artifact_contents_invalid"),
    ("bad_json", "json_invalid"), ("missing_field", "receipt_fields_invalid"),
    ("bad_sha", "receipt_sha_invalid"),
])
def test_lookup_faults_fail_closed_and_never_echo_token(tmp_path, capsys, mode, reason):
    payload = json.loads(_build_receipt(tmp_path))
    good_zip = _zip(json.dumps(payload).encode())
    api = _api(good_zip)
    if mode == "api":
        def api(url, timeout):
            raise RuntimeError("Bearer never-print-this-token")
    elif mode == "zip":
        api = _api(b"not a zip")
    elif mode == "missing_file":
        api = _api(_zip(json.dumps(payload).encode(), "other.json"))
    elif mode == "bad_json":
        api = _api(_zip(b"{"))
    elif mode == "missing_field":
        del payload["runner"]
        api = _api(_zip(json.dumps(payload).encode()))
    elif mode == "bad_sha":
        payload["base_sha"] = "x" * 40
        api = _api(_zip(json.dumps(payload).encode()))
    result, stdout, output = _run_lookup(api, tmp_path)
    assert result == 0
    assert "never-print-this-token" not in stdout and "never-print-this-token" not in output
    assert f"reusable=false reason={reason} source_run_id=none" in stdout
    assert "quality_reusable=false" in output and f"reuse_reason={reason}" in output
    assert f"GATE-QUALITY-REUSE-V1 reusable=false" in stdout


def test_build_cli_rejects_invalid_sha_with_nonzero_exit(tmp_path):
    output = tmp_path / "receipt.json"
    args = [sys.executable, str(SCRIPT), "build-receipt", "--repository-id", "1",
            "--pr-number", "2", "--head-sha", "not-a-sha", "--base-sha", "b" * 40,
            "--merge-tree", "c" * 40, "--runner", "hosted", "--extra-runner-label-ci", "",
            "--tier", "personal", "--source-run-id", "3", "--caller-checks", "passed",
            "--preflight-result", "success", "--out", str(output)]
    result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, check=False)
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("field,value,reason", [
    ("repository_id", True, "receipt_integer_invalid"),
    ("runner", 7, "receipt_string_invalid"),
    ("extra_runner_label_ci", None, "receipt_string_invalid"),
    ("schema_version", True, "receipt_version_invalid"),
])
def test_receipt_json_field_type_errors_fail_closed(tmp_path, field, value, reason):
    payload = json.loads(_build_receipt(tmp_path))
    payload[field] = value
    decision = quality_reuse.lookup_reusable_quality(REQUEST, _api(_zip(json.dumps(payload).encode())))
    assert not decision.reusable and decision.reason == reason


def test_artifact_name_and_receipt_validator_contract():
    assert quality_reuse.RECEIPT_SCHEMA_VERSION == 1
    assert quality_reuse.RECEIPT_KIND == "gate_quality_reuse"
    assert quality_reuse.artifact_name_for(12345, 67, "a" * 40) == "gate-quality-reuse-v1-12345-67-" + "a" * 40
    payload = quality_reuse.build_receipt(**VALUES)
    assert quality_reuse.validate_receipt(payload, REQUEST) == (True, "reusable")
