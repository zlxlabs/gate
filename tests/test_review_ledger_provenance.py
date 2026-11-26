import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
BUILD_LEDGER = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"
UNKNOWN_REASON = "live ledger producer has no exact value for this field"


def _primary_audit() -> dict:
    return {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "repository": "zlxlabs/gate", "pr": 42,
        "base_sha": "b" * 40, "head_sha": "a" * 40, "diff_digest": "d" * 64,
        "policy_version": "policy-v1", "policy_digest": "e" * 64,
        "registry_commit": "0123456789abcdef0123456789abcdef01234567", "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40, "run_id": 999, "run_attempt": 1,
        "job_id": 99, "reviewer": "codex-sub", "verdict": "fail",
        "merge_base_sha": "9" * 40, "candidate_commit_sha": "f" * 40,
        "candidate_tree_sha": "1" * 40, "run_mode": "PAYLOAD_ONLY",
        "spec_source": "event_payload", "pr_body_digest": "empty",
        "attempts": [], "shadow_mode": "detached", "expected_shadows": [],
        "result": {"verdict": "fail", "summary": "blocking finding", "findings": [
            {"id": "correctness.bad-state", "severity": "major", "category": "correctness"},
        ], "finding_dispositions": []},
        "cost": 0, "tokens": [], "runtime": None,
    }


def _produce_record(tmp_path: Path, audit: dict):
    tmp_path.mkdir(parents=True, exist_ok=True)
    audit_path = tmp_path / "primary-review-audit.json"
    preflight_path = tmp_path / "preflight.json"
    ledger_path = tmp_path / "ledger.jsonl"
    record_path = tmp_path / "review-record.json"
    audit_path.write_text(json.dumps(audit, sort_keys=True), encoding="utf-8")
    preflight_path.write_text(json.dumps({
        "diff_lines": 100, "classification": "single", "review_plan": "single",
        "thresholds": {"single_turn_lines": 4000},
    }), encoding="utf-8")
    command = [
        sys.executable, str(BUILD_LEDGER), "--audit-path", str(audit_path),
        "--preflight-path", str(preflight_path), "--install-path", str(tmp_path / "missing-install.json"),
        "--output", str(ledger_path), "--record-output", str(record_path),
        "--repository", "zlxlabs/gate", "--pr-number", "42", "--run-id", "999",
        "--run-attempt", "1", "--head-sha", "a" * 40, "--expected-repository-id", "123",
        "--expected-base-sha", "b" * 40, "--expected-caller-sha", "c" * 40,
        "--expected-reusable-workflow-sha", "w" * 40, "--codex-expected", "true",
    ]
    env = os.environ.copy()
    env.pop("GITHUB_STEP_SUMMARY", None)
    completed = subprocess.run(command, capture_output=True, text=True, env=env)
    return completed, audit_path, record_path


def test_sparse_consumer_checkout_builds_ledger_without_repository_root(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "gate-v2.yml").read_text(encoding="utf-8"))
    consumer_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("env", {}).get("GATE_CHECKOUT_PATH") == "_gate-ledger-consumer-src"
    ]
    assert len(consumer_steps) == 1
    sparse_files = consumer_steps[0]["env"]["GATE_CHECKOUT_SPARSE"].splitlines()
    assert sparse_files == [
        ".github/workflows/gate-v2.yml",
        ".github/actions/review-ledger/action.yml",
        ".github/actions/review-ledger/build_ledger.py",
        "scripts/scrub_outbound.py",
    ]

    sparse_root = tmp_path / "_gate-ledger-consumer-src"
    for relative_path in sparse_files:
        source = ROOT / relative_path
        destination = sparse_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    sparse_script = sparse_root / ".github" / "actions" / "review-ledger" / "build_ledger.py"

    audits = (
        ("normal", _primary_audit()),
        ("provenance", _primary_audit() | {"prompt_digest": "a" * 64, "model_used": "gpt-6-luna"}),
    )
    for name, audit in audits:
        run_dir = tmp_path / name
        run_dir.mkdir()
        audit_path = run_dir / "primary-review-audit.json"
        preflight_path = run_dir / "preflight.json"
        ledger_path = run_dir / "ledger.jsonl"
        record_path = run_dir / "review-record.json"
        audit_path.write_text(json.dumps(audit, sort_keys=True), encoding="utf-8")
        preflight_path.write_text(json.dumps({
            "diff_lines": 100, "classification": "single", "review_plan": "single",
            "thresholds": {"single_turn_lines": 4000},
        }), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable, str(sparse_script), "--audit-path", str(audit_path),
                "--preflight-path", str(preflight_path), "--install-path", str(run_dir / "missing-install.json"),
                "--output", str(ledger_path), "--record-output", str(record_path),
                "--repository", "zlxlabs/gate", "--pr-number", "42", "--run-id", "999",
                "--run-attempt", "1", "--head-sha", "a" * 40, "--expected-repository-id", "123",
                "--expected-base-sha", "b" * 40, "--expected-caller-sha", "c" * 40,
                "--expected-reusable-workflow-sha", "w" * 40, "--codex-expected", "true",
            ],
            cwd=run_dir, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(audit_path.read_bytes()) == audit

        ledger_bytes = ledger_path.read_bytes()
        assert ledger_bytes.endswith(b"\n")
        ledger_entry = json.loads(ledger_bytes)
        assert ledger_entry["repository"] == "zlxlabs/gate"
        assert ledger_entry["pr_number"] == 42
        record_bytes = record_path.read_bytes()
        assert record_bytes.endswith(b"\n")
        record = json.loads(record_bytes)
        assert record["schema_version"] == "review-record/v1"
        if name == "normal":
            assert record["prompt_digest"] is None
            assert record["model_used"] is None
            assert record["provenance"]["unknown_fields"]["prompt_digest"] == UNKNOWN_REASON
            assert record["provenance"]["unknown_fields"]["model_used"] == UNKNOWN_REASON
        else:
            assert record["prompt_digest"] == "a" * 64
            assert record["model_used"] == "gpt-6-luna"
            assert b'"prompt_digest": "' + b"a" * 64 + b'"' in record_bytes
            assert b'"model_used": "gpt-6-luna"' in record_bytes


def test_primary_provenance_reaches_record_bytes_and_legacy_stays_unknown(tmp_path):
    prompt_digest = "a" * 64
    model_used = "gpt-6-luna"
    audit = _primary_audit() | {"prompt_digest": prompt_digest, "model_used": model_used}
    completed, audit_path, record_path = _produce_record(tmp_path / "current", audit)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(audit_path.read_bytes()) == audit
    record_bytes = record_path.read_bytes()
    assert record_bytes.endswith(b"\n")
    record = json.loads(record_bytes)
    assert record["prompt_digest"] == prompt_digest
    assert record["model_used"] == model_used
    assert b'"prompt_digest": "' + prompt_digest.encode() + b'"' in record_bytes
    assert b'"model_used": "gpt-6-luna"' in record_bytes
    assert "prompt_digest" not in record["provenance"]["unknown_fields"]
    assert "model_used" not in record["provenance"]["unknown_fields"]

    legacy, _legacy_audit, legacy_record_path = _produce_record(tmp_path / "legacy", _primary_audit())
    assert legacy.returncode == 0, legacy.stderr
    legacy_record = json.loads(legacy_record_path.read_bytes())
    assert legacy_record["prompt_digest"] is None
    assert legacy_record["model_used"] is None
    assert legacy_record["provenance"]["unknown_fields"]["prompt_digest"] == UNKNOWN_REASON
    assert legacy_record["provenance"]["unknown_fields"]["model_used"] == UNKNOWN_REASON
    assert legacy_record["provenance"]["unknown_fields"]["round_index"]
    assert legacy_record["provenance"]["unknown_fields"]["profile"]

    null_audit = _primary_audit() | {"prompt_digest": None, "model_used": None}
    null_values, _null_audit_path, null_record_path = _produce_record(tmp_path / "null", null_audit)
    assert null_values.returncode == 0, null_values.stderr
    null_record = json.loads(null_record_path.read_bytes())
    assert null_record["prompt_digest"] is None
    assert null_record["model_used"] is None
    assert null_record["provenance"]["unknown_fields"]["prompt_digest"] == UNKNOWN_REASON
    assert null_record["provenance"]["unknown_fields"]["model_used"] == UNKNOWN_REASON


@pytest.mark.parametrize(("field", "invalid"), [
    ("prompt_digest", 123), ("prompt_digest", ""),
    ("model_used", 123), ("model_used", ""),
])
def test_primary_provenance_rejects_invalid_values(tmp_path, field, invalid):
    completed, _audit_path, record_path = _produce_record(
        tmp_path, _primary_audit() | {field: invalid},
    )
    assert completed.returncode != 0
    assert f"primary audit {field} must be a non-empty string" in completed.stderr
    assert not record_path.exists()
