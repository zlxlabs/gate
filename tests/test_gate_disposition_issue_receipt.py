import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / ".github/actions/gate-disposition/issue_receipt.py"
REPOSITORY = "zlxlabs/gate"
SCOPE = {
    "repository_id": 123,
    "pr_number": 42,
    "base_sha": "b" * 40,
    "head_sha": "h" * 40,
    "diff_digest": "d" * 64,
    "policy_version": "policy-v1",
    "policy_digest": "p" * 64,
    "tier": "personal",
    "caller_sha": "c" * 40,
    "reusable_workflow_sha": "w" * 40,
}
COUNTEREVIDENCE = {
    "command": "pytest -q tests/test_regression.py",
    "output": "1 passed",
    "result": "refuted",
    "pointer": "tests/test_regression.py::test_behavior",
}


def _audit(tier="personal", *, trigger_kind="inferred"):
    return {
        **SCOPE,
        "repository": REPOSITORY,
        "pr": 42,
        "run_id": 7,
        "run_attempt": 1,
        "verdict": "fail",
        "result": {
            "findings": [
                {
                    "id": "finding-one",
                    "severity": "major",
                    "trigger_kind": trigger_kind,
                    "file": "src/guard.py",
                    "line": 12,
                    "category": "correctness",
                }
            ]
        },
        "tier": tier,
    }


def _issue(tmp_path, *, disposition=None, counterevidence=None, tracking_issue=None,
           tier="personal", trigger_kind="inferred"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(_audit(tier, trigger_kind=trigger_kind)))
    output_dir = tmp_path / "output"
    scope = {**SCOPE, "tier": tier}
    command = [
        sys.executable, str(PRODUCER), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository", REPOSITORY,
        "--repository-id", "123", "--pr-number", "42",
        "--head-sha", SCOPE["head_sha"], "--finding-id", "finding-one",
        "--reason", "canonical evidence reviewed", "--scope-json", json.dumps(scope),
        "--approver", "owner", "--approver-id", "10",
        "--approved-at", "2026-09-25T09:00:00Z", "--triggering-actor", "owner",
    ]
    if disposition is not None:
        command.extend(("--disposition", disposition))
    if counterevidence is not None:
        command.extend(("--counterevidence-json", json.dumps(counterevidence)))
    if tracking_issue is not None:
        command.extend(("--tracking-issue", tracking_issue))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result, output_dir


def test_false_positive_producer_writes_v3_counterevidence_payload(tmp_path):
    result, output_dir = _issue(tmp_path, disposition="false-positive", counterevidence=COUNTEREVIDENCE)

    assert result.returncode == 0, result.stderr
    artifact = json.loads(result.stdout)["artifact"]
    payload_bytes = (output_dir / artifact).read_bytes()
    payload = json.loads(payload_bytes)
    assert payload_bytes == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert payload["kind"] == "gate-disposition-receipt-v3"
    assert payload["schema_version"] == 3
    assert payload["disposition"] == "false-positive"
    assert payload["counterevidence"] == COUNTEREVIDENCE
    assert "tracking_issue" not in payload


@pytest.mark.parametrize(
    "counterevidence",
    [
        None,
        {key: value for key, value in COUNTEREVIDENCE.items() if key != "command"},
        {**COUNTEREVIDENCE, "output": ""},
        {**COUNTEREVIDENCE, "pointer": "  "},
    ],
)
def test_false_positive_producer_rejects_missing_evidence_before_writing(tmp_path, counterevidence):
    result, output_dir = _issue(tmp_path, disposition="false-positive", counterevidence=counterevidence)

    assert result.returncode != 0
    assert "counterevidence_required" in result.stderr
    assert not output_dir.exists()


def test_false_positive_producer_requires_refuted_result(tmp_path):
    result, output_dir = _issue(
        tmp_path, disposition="false-positive",
        counterevidence={**COUNTEREVIDENCE, "result": "confirmed"},
    )

    assert result.returncode != 0
    assert "counterevidence_result_not_refuted" in result.stderr
    assert not output_dir.exists()


@pytest.mark.parametrize("tracking_issue", ["#12", "https://github.com/zlxlabs/gate/issues/12"])
def test_deferred_producer_accepts_same_repository_issue_reference(tmp_path, tracking_issue):
    result, output_dir = _issue(tmp_path, disposition="deferred", tracking_issue=tracking_issue)

    assert result.returncode == 0, result.stderr
    artifact = json.loads(result.stdout)["artifact"]
    payload = json.loads((output_dir / artifact).read_bytes())
    assert payload["disposition"] == "deferred"
    assert payload["tracking_issue"] == tracking_issue
    assert "counterevidence" not in payload


@pytest.mark.parametrize(
    "tracking_issue",
    [
        "",
        "12",
        "#0",
        "https://github.com/other/repo/issues/12",
        "https://github.com/zlxlabs/gate/pull/12",
    ],
)
def test_deferred_producer_rejects_invalid_or_cross_repository_reference(tmp_path, tracking_issue):
    result, output_dir = _issue(tmp_path, disposition="deferred", tracking_issue=tracking_issue)

    assert result.returncode != 0
    assert "tracking_issue_" in result.stderr
    assert not output_dir.exists()


def test_deferred_producer_rejects_saas_and_accepts_any_p1_trigger_kind(tmp_path):
    rejected, rejected_dir = _issue(
        tmp_path / "saas", disposition="deferred", tracking_issue="#12", tier="saas",
    )
    assert rejected.returncode != 0
    assert "deferred_not_allowed_for_tier" in rejected.stderr
    assert not rejected_dir.exists()

    accepted, accepted_dir = _issue(
        tmp_path / "measured", disposition="deferred", tracking_issue="#12", trigger_kind="measured",
    )
    assert accepted.returncode == 0, accepted.stderr
    artifact = json.loads(accepted.stdout)["artifact"]
    assert (accepted_dir / artifact).is_file()


def test_legacy_five_input_producer_call_fails_closed_without_evidence(tmp_path):
    result, output_dir = _issue(tmp_path)

    assert result.returncode != 0
    assert "counterevidence_required" in result.stderr
    assert not output_dir.exists()
