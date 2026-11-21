"""Blocking-matrix-v1 consumption (card C10e): the gate aggregator releases
`code_fail` only when every finding whose gate_action is block/must_respond
is covered by a valid receipt — block accepts only false-positive,
must_respond accepts false-positive or deferred (same-repo tracking issue).
Audits without blocking_matrix keep the legacy P1-only behavior byte-for-byte.
Shared contract: gate-hub review-primary writes audit.blocking_matrix and
audit.result.findings[i].gate_action (see gate-hub
docs/sessions/261120-review-recall/DESIGN.md).
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"


def _module():
    spec = importlib.util.spec_from_file_location("gate_aggregate_matrix", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGG = _module()
CONV = AGG._CONVERGENCE

IDENTITY = AGG.Identity(repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=1, pr=42)
_DIGEST_A = "a" * 64


def _audit(*, tier="personal", verdict="fail", findings=(), matrix=None):
    record = {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": IDENTITY.repository_id,
        "head_sha": IDENTITY.head_sha,
        "run_id": IDENTITY.run_id,
        "run_attempt": IDENTITY.run_attempt,
        "pr": IDENTITY.pr,
        "verdict": verdict,
        "reviewer": "claude-glm",
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "p" * 64,
        "tier": tier,
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "result": {"findings": list(findings)},
    }
    if matrix is not None:
        record["blocking_matrix"] = matrix
    return record


def _matrix(tier="personal", version=1):
    return {"version": version, "tier": tier}


def _finding(finding_id, severity="major", gate_action="block", **changes):
    finding = {
        "id": finding_id, "severity": severity, "trigger_kind": "inferred",
        "file": "src/lock.py", "line": 12, "category": "correctness",
    }
    if gate_action is not None:
        finding["gate_action"] = gate_action
    finding.update(changes)
    return finding


def _scope_for(audit):
    scope, missing = AGG._convergence_scope_from_audit(audit, IDENTITY)
    assert not missing and scope is not None
    return scope


def _gate_rerun(head_sha):
    literal = "locked-behavior"
    pathspec = "src/lock.py"
    argv = ["git", "grep", "-n", "-F", "-e", literal, head_sha, "--", pathspec]
    excerpt = f"{pathspec}:1:{literal}"
    return {
        "argv": argv,
        "exit_code": 0,
        "match_count": 1,
        "stdout_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "excerpt": excerpt,
    }


def _receipt(scope, *, disposition, finding_id="f1", audit_digest=_DIGEST_A, **changes):
    fields = dict(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition=disposition,
        repository_id=str(IDENTITY.repository_id),
        pr_number=IDENTITY.pr,
        epoch=CONV.derive_epoch(scope),
        head_sha=IDENTITY.head_sha,
        audit_digest=audit_digest,
        finding_id=finding_id,
        reason="reviewer overreach",
        approver="octocat",
        approver_id=1,
        approved_at="2026-08-30T12:00:00Z",
    )
    if disposition == "false-positive":
        fields["counterevidence"] = {
            "command": "pytest -q tests/test_regression.py",
            "output": "1 passed",
            "result": "refuted",
            "pointer": "tests/test_regression.py::test_regression",
            "gate_rerun": _gate_rerun(fields["head_sha"]),
        }
    else:
        fields["tracking_issue"] = "#12"
    fields.update(changes)
    return CONV.DispositionReceipt(**fields)


def _evaluate(audit, waiver_receipts=(), audit_digest=_DIGEST_A):
    return AGG.evaluate(
        quality_result="success",
        primary_result="failure" if audit["verdict"] == "fail" else "success",
        preflight_result="success",
        runner="self",
        is_draft=False,
        review_expected=True,
        audit=audit,
        audit_error=None,
        identity=IDENTITY,
        pr_author="zj1123581321",
        audit_source_attempt=1,
        audit_artifact_name="primary-audit-v2-1",
        scope=_scope_for(audit),
        audit_digest=audit_digest,
        waiver_receipts=waiver_receipts,
        repository="zlxlabs/gate",
    )


def _primary(finding_tuples, verdict="fail"):
    return CONV.CanonicalPrimary(
        schema_version=1,
        repository_id=IDENTITY.repository_id,
        pr_number=IDENTITY.pr,
        head_sha=IDENTITY.head_sha,
        run_id=IDENTITY.run_id,
        run_attempt=IDENTITY.run_attempt,
        verdict=verdict,
        p1_ids=tuple(item[0] for item in finding_tuples),
        p1_findings=finding_tuples,
    )


def test_v1_personal_must_respond_deferred_receipt_releases_gate():
    audit = _audit(tier="personal", findings=[_finding("f1", gate_action="must_respond")], matrix=_matrix())
    outcome = _evaluate(audit, waiver_receipts=(_receipt(_scope_for(audit), disposition="deferred"),))
    assert outcome.classification == "code_pass"
    assert outcome.reason_code == "disposition_resolved"
    assert outcome.gate_result == "pass"
    assert outcome.ok is True


def test_v1_saas_minor_must_respond_requires_receipt():
    audit = _audit(
        tier="saas",
        findings=[_finding("m1", severity="minor", gate_action="must_respond")],
        matrix=_matrix("saas"),
    )
    outcome = _evaluate(audit)
    assert outcome.classification == "code_fail"
    assert outcome.reason_code == "primary_findings"
    assert outcome.disposition_audit.primary_p1_ids == ("m1",)
    assert outcome.disposition_audit.remaining_p1_ids == ("m1",)


def test_v1_saas_minor_must_respond_deferred_receipt_releases_gate():
    audit = _audit(
        tier="saas",
        findings=[_finding("m1", severity="minor", gate_action="must_respond")],
        matrix=_matrix("saas"),
    )
    outcome = _evaluate(audit, waiver_receipts=(_receipt(_scope_for(audit), disposition="deferred", finding_id="m1"),))
    assert outcome.classification == "code_pass"
    assert outcome.reason_code == "disposition_resolved"


def test_v1_missing_gate_action_fails_closed_to_block():
    audit = _audit(findings=[_finding("f1", gate_action=None)], matrix=_matrix())
    outcome = _evaluate(audit)
    assert outcome.classification == "code_fail"
    assert outcome.disposition_audit.remaining_p1_ids == ("f1",)
    assert any("GATE-ACTION-INVALID" in note for note in outcome.notes)


def test_v1_missing_gate_action_deferred_receipt_keeps_gate_failed():
    audit = _audit(findings=[_finding("f1", gate_action=None)], matrix=_matrix())
    outcome = _evaluate(audit, waiver_receipts=(_receipt(_scope_for(audit), disposition="deferred"),))
    assert outcome.classification == "code_fail"
    assert outcome.reason_code == "primary_findings"
    assert any("GATE-ACTION-INVALID" in note for note in outcome.notes)


def test_v1_invalid_gate_action_false_positive_releases_gate_with_marker():
    audit = _audit(findings=[_finding("f1", gate_action="banana")], matrix=_matrix())
    outcome = _evaluate(audit, waiver_receipts=(_receipt(_scope_for(audit), disposition="false-positive"),))
    assert outcome.classification == "code_pass"
    assert outcome.reason_code == "disposition_resolved"
    assert any("GATE-ACTION-INVALID" in note for note in outcome.notes)


def test_v1_unknown_matrix_version_fails_closed_all_findings_block():
    audit = _audit(
        findings=[_finding("n1", severity="nit", gate_action="comment")],
        matrix=_matrix(version=2),
    )
    outcome = _evaluate(audit, waiver_receipts=(_receipt(_scope_for(audit), disposition="false-positive", finding_id="n1"),))
    assert outcome.classification == "code_pass"
    assert outcome.reason_code == "disposition_resolved"
    assert any("GATE-ACTION-INVALID" in note for note in outcome.notes)


def test_matrix_audit_json_file_roundtrip_binds_receipt():
    """The blocking_matrix/gate_action fields survive the producer file
    boundary and bind a v1 receipt written against the file bytes."""
    audit = _audit(
        tier="personal",
        findings=[_finding("f1", gate_action="must_respond")],
        matrix=_matrix(),
    )
    import tempfile

    with tempfile.TemporaryDirectory(prefix="matrix-audit-") as dest:
        path = Path(dest) / "primary-audit-v2-1.json"
        path.write_bytes(json.dumps(audit).encode("utf-8"))
        loaded, error, raw = AGG._read_audit_file(Path(dest))
        assert error is None
        assert loaded["blocking_matrix"] == _matrix()
    outcome = _evaluate(loaded, waiver_receipts=(_receipt(_scope_for(loaded), disposition="deferred"),))
    assert outcome.classification == "code_pass"
    assert outcome.reason_code == "disposition_resolved"


def test_gate_actions_deferred_active_only_for_must_respond():
    finding = AGG._GATE_ACTION.validated_canonical_findings(
        _audit(findings=[_finding("f1")]), full=True,
    )[0]
    primary = _primary((finding,))
    scope = _scope_for(_audit(findings=[_finding("f1")]))
    receipt = _receipt(scope, disposition="deferred")
    active = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest=_DIGEST_A,
        gate_actions={"f1": "must_respond"},
    )
    assert (active.valid, active.active, active.reason) == (True, True, "active_deferred")
    rejected = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest=_DIGEST_A,
        gate_actions={"f1": "block"},
    )
    assert (rejected.valid, rejected.active, rejected.reason_code) == (
        False, False, "deferred_not_allowed_for_tier",
    )


def test_gate_actions_widen_coverable_severity_with_stable_key():
    audit = _audit(
        tier="saas",
        findings=[_finding("m1", severity="minor", gate_action="must_respond")],
        matrix=_matrix("saas"),
    )
    finding = AGG._GATE_ACTION.validated_canonical_findings(audit, full=True)[0]
    primary = _primary((finding,))
    scope = _scope_for(audit)
    stable_key = CONV.canonical_finding_key(audit["result"]["findings"][0])
    receipt = _receipt(
        scope, disposition="deferred", finding_id=stable_key, finding_key=stable_key,
    )
    status = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest=_DIGEST_A,
        gate_actions={"m1": "must_respond"},
    )
    assert (status.valid, status.active, status.reason) == (True, True, "active_deferred")


def test_legacy_audit_without_matrix_deferred_still_rejected():
    """Legacy sentinel (not red-on-base): audits without blocking_matrix keep
    the exact legacy verdict path — deferred stays rejected and the P1-only
    flip condition is untouched."""
    audit = _audit(findings=[_finding("f1")])
    outcome = _evaluate(audit, waiver_receipts=(_receipt(_scope_for(audit), disposition="deferred"),))
    assert outcome.classification == "code_fail"
    block = AGG.project_disposition_receipt_audit(outcome.disposition_audit)
    assert block["rejected_reasons"] == {"deferred_not_allowed_for_tier": 1}
    assert block["remaining_p1_ids"] == ["f1"]


def test_legacy_audit_without_matrix_false_positive_flip_unchanged():
    """Legacy sentinel (not red-on-base): the byte-level flip reason for an
    audit without blocking_matrix is the legacy disposition_resolved path."""
    audit = _audit(findings=[_finding("f1")])
    outcome = _evaluate(audit, waiver_receipts=(_receipt(_scope_for(audit), disposition="false-positive"),))
    assert outcome.classification == "code_pass"
    assert outcome.reason_code == "disposition_resolved"
    assert "all current P1 findings are covered by active evidence-bound gate-disposition receipts" in outcome.notes
    assert not any("GATE-ACTION-INVALID" in note for note in outcome.notes)


def test_legacy_audit_deferred_rejected_without_gate_actions_kwarg():
    """Legacy sentinel (not red-on-base): the convergence-layer default keeps
    rejecting deferred receipts when gate_actions is not supplied."""
    finding = AGG._GATE_ACTION.validated_canonical_findings(_audit(findings=[_finding("f1")]))[0]
    primary = _primary((finding,))
    scope = _scope_for(_audit(findings=[_finding("f1")]))
    receipt = _receipt(scope, disposition="deferred")
    status = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest=_DIGEST_A,
    )
    assert (status.valid, status.active, status.reason_code) == (
        False, False, "deferred_not_allowed_for_tier",
    )
