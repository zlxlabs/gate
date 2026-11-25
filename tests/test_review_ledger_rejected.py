"""Rejected receipts must survive the full producer→publisher chain.

Cross-boundary contract (gate-hub design 261122 §8.9 card L3 follow-up): a
real rejected receipt issued by issue_receipt.py must flow through
convergence parsing/booking, the aggregator's receipt projection, and
build_ledger's submitter-claim validation without dying on its empty
evidence_pointer (rejected carries no evidence pointer by design;
false-positive/deferred keep rejecting empty pointers, as do unknown
disposition values).
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / ".github" / "actions" / "gate-disposition" / "issue_receipt.py"
CONVERGENCE = ROOT / ".github" / "actions" / "gate-aggregator" / "convergence.py"
AGGREGATE = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
LEDGER = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"

SCOPE = {
    "repository_id": 123,
    "pr_number": 42,
    "base_sha": "b" * 40,
    "head_sha": "h" * 40,
    "diff_digest": "d" * 64,
    "policy_version": "policy-v1",
    "policy_digest": "p" * 64,
    "tier": "internal",
    "caller_sha": "c" * 40,
    "reusable_workflow_sha": "w" * 40,
}


def _load(unique_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(unique_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _audit() -> dict:
    """Canonical audit whose single finding is a sol-verify must_respond P1."""
    return {
        "repository_id": SCOPE["repository_id"],
        "pr": SCOPE["pr_number"],
        "run_id": 7,
        "run_attempt": 1,
        "base_sha": SCOPE["base_sha"],
        "head_sha": SCOPE["head_sha"],
        "diff_digest": SCOPE["diff_digest"],
        "policy_version": SCOPE["policy_version"],
        "policy_digest": SCOPE["policy_digest"],
        "tier": SCOPE["tier"],
        "caller_sha": SCOPE["caller_sha"],
        "reusable_workflow_sha": SCOPE["reusable_workflow_sha"],
        "verdict": "fail",
        "blocking_matrix": {"version": 1, "tier": SCOPE["tier"]},
        "result": {
            "findings": [{
                "id": "finding-one",
                "severity": "major",
                "trigger_kind": "inferred",
                "file": "src/guard.py",
                "line": 12,
                "category": "correctness",
                "origin": "sol_verify",
                "gate_action": "must_respond",
            }],
        },
    }


def _issue_rejected(tmp_path: Path):
    """Run the real producer: issue a rejected receipt for the sol finding."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(_audit()))
    output_dir = tmp_path / "output"
    command = [
        sys.executable, str(PRODUCER), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository", "zlxlabs/gate",
        "--repository-id", str(SCOPE["repository_id"]), "--pr-number", "42",
        "--head-sha", SCOPE["head_sha"], "--finding-id", "finding-one",
        "--reason", "sol finding rejected after verifier triage",
        "--scope-json", json.dumps(SCOPE),
        "--approver", "owner", "--approver-id", "10",
        "--approved-at", "2026-11-25T09:00:00Z", "--triggering-actor", "owner",
        "--disposition", "rejected",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result, output_dir


def test_rejected_receipt_survives_ledger_projection_and_publisher(tmp_path):
    conv = _load("rejected_ledger_convergence", CONVERGENCE)
    agg = _load("rejected_ledger_aggregate", AGGREGATE)
    ledger = _load("rejected_ledger_build_ledger", LEDGER)

    result, output_dir = _issue_rejected(tmp_path / "issue")
    assert result.returncode == 0, result.stderr
    raw_bytes = (output_dir / json.loads(result.stdout)["artifact"]).read_bytes()
    receipt = conv.parse_disposition_receipt(json.loads(raw_bytes))
    assert receipt.disposition == "rejected"

    scope = conv.Scope(**SCOPE)
    primary = conv.CanonicalPrimary(
        schema_version=1,
        repository_id=SCOPE["repository_id"],
        pr_number=SCOPE["pr_number"],
        head_sha=SCOPE["head_sha"],
        run_id=7,
        run_attempt=1,
        verdict="fail",
        p1_ids=("finding-one",),
        p1_findings=(("finding-one", "major", "inferred", "src/guard.py", 12, "correctness"),),
    )
    audit_digest = conv.canonical_audit_digest(_audit())
    disposition_audit = conv.record_dispositions(
        primary.p1_ids, (receipt,), scope=scope, primary=primary, audit_digest=audit_digest,
    )
    assert disposition_audit.remaining_p1_ids == ()

    projected = agg.project_disposition_receipt_audit(disposition_audit)
    resolved = projected["resolved"]
    assert len(resolved) == 1
    assert resolved[0]["disposition"] == "rejected"
    assert resolved[0]["evidence_pointer"] == ""

    validated = ledger.validate_disposition_receipt_audit(projected)
    assert validated["resolved"][0]["disposition"] == "rejected"
    assert validated["consumed_count"] == 1


def _block_with_pointer(disposition: str, pointer: str) -> dict:
    return {
        "resolved": [{
            "finding_id": "p1",
            "receipt": "receipt-artifact",
            "approver": "octocat",
            "approver_id": 1,
            "approved_at": "2026-11-25T09:00:00Z",
            "reason": "publisher validation fixture",
            "disposition": disposition,
            "evidence_pointer": pointer,
        }],
        "consumed_count": 1,
        "rejected_count": 0,
        "rejected_reasons": {},
        "fail_closed": False,
    }


def test_ledger_still_rejects_empty_pointer_for_false_positive():
    ledger = _load("rejected_ledger_build_ledger_fp", LEDGER)
    block = _block_with_pointer("false-positive", "")
    try:
        ledger.validate_disposition_receipt_audit(block)
    except ValueError as exc:
        assert "invalid evidence_pointer" in str(exc)
    else:
        raise AssertionError("false-positive empty evidence_pointer must stay rejected")


def test_ledger_still_rejects_empty_pointer_for_deferred():
    ledger = _load("rejected_ledger_build_ledger_deferred", LEDGER)
    block = _block_with_pointer("deferred", "")
    try:
        ledger.validate_disposition_receipt_audit(block)
    except ValueError as exc:
        assert "invalid evidence_pointer" in str(exc)
    else:
        raise AssertionError("deferred empty evidence_pointer must stay rejected")


def test_ledger_rejects_unknown_disposition_value():
    ledger = _load("rejected_ledger_build_ledger_unknown", LEDGER)
    block = _block_with_pointer("regrade", "docs/triage.md")
    try:
        ledger.validate_disposition_receipt_audit(block)
    except ValueError as exc:
        assert "invalid disposition" in str(exc)
    else:
        raise AssertionError("unknown disposition_claim must stay rejected")
