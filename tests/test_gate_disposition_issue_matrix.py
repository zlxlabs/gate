"""Blocking-matrix-v1 issuance (card C10e producer side): issue_receipt.py
signs receipts for any finding whose gate_action is block/must_respond when
the audit carries blocking_matrix.version==1; legacy audits keep the strict
P1-only issuance rule and reject deferred unconditionally (gate-hub#1072).
"""
import json

from test_gate_disposition_issue_receipt import (  # noqa: E402
    _allowlisted_ce,
    _issue,
    _seed_caller_repo,
)


def test_matrix_v1_allows_saas_minor_must_respond_deferred_receipt(tmp_path):
    result, output_dir = _issue(
        tmp_path, disposition="deferred", tracking_issue="#12", tier="saas",
        severity="minor", gate_action="must_respond", matrix=True,
    )

    assert result.returncode == 0, result.stderr
    artifact = json.loads(result.stdout)["artifact"]
    payload = json.loads((output_dir / artifact).read_bytes())
    assert payload["disposition"] == "deferred"
    assert payload["tracking_issue"] == "#12"


def test_matrix_v1_rejects_comment_gate_action_finding(tmp_path):
    result, output_dir = _issue(
        tmp_path, disposition="deferred", tracking_issue="#12", tier="saas",
        severity="minor", gate_action="comment", matrix=True,
    )

    assert result.returncode != 0
    assert "block or must_respond under blocking_matrix v1" in result.stderr
    assert not output_dir.exists()


def test_matrix_v1_deferred_rejected_for_block_gate_action(tmp_path):
    result, output_dir = _issue(
        tmp_path, disposition="deferred", tracking_issue="#12", tier="internal",
        gate_action="block", matrix=True,
    )

    assert result.returncode != 0
    assert "deferred_not_allowed_for_tier" in result.stderr
    assert not output_dir.exists()


def test_matrix_v1_allows_false_positive_for_block_finding(tmp_path):
    repo, sha, relpath, literal = _seed_caller_repo(tmp_path / "repo")
    result, output_dir = _issue(
        (tmp_path / "issue"), disposition="false-positive",
        counterevidence=_allowlisted_ce(sha, relpath=relpath, literal=literal),
        tier="internal", gate_action="block", matrix=True,
        head_sha=sha, repo_dir=repo,
    )

    assert result.returncode == 0, result.stderr
    artifact = json.loads(result.stdout)["artifact"]
    payload = json.loads((output_dir / artifact).read_bytes())
    assert payload["disposition"] == "false-positive"
    assert "tracking_issue" not in payload


def test_matrix_v1_unknown_version_fails_closed(tmp_path):
    result, output_dir = _issue(
        tmp_path, disposition="deferred", tracking_issue="#12", tier="saas",
        severity="minor", gate_action="must_respond", matrix={"version": 2},
    )

    assert result.returncode != 0
    assert "block or must_respond under blocking_matrix v1" in result.stderr
    assert not output_dir.exists()


def test_legacy_audit_still_rejects_minor_finding(tmp_path):
    result, output_dir = _issue(
        tmp_path, disposition="deferred", tracking_issue="#12",
        severity="minor",
    )

    assert result.returncode != 0
    assert "finding_id must identify a P1 finding" in result.stderr
    assert not output_dir.exists()


