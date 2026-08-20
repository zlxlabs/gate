"""Cross-process producer/consumer contract tests for convergence receipts."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AGGREGATE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
DISPOSITION_PRODUCER = ROOT / ".github" / "actions" / "gate-disposition" / "issue_receipt.py"


def _aggregate():
    spec = importlib.util.spec_from_file_location("gate_aggregate_artifact", AGGREGATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGG = _aggregate()
CONV = AGG._CONVERGENCE


def _scope(**changes):
    values = dict(
        repository_id=123,
        pr_number=42,
        base_sha="b" * 40,
        head_sha="h" * 40,
        diff_digest="d" * 64,
        policy_version="policy-v1",
        policy_digest="p" * 64,
        tier="internal",
        effective_tier="internal",
        infra_classifier_version="infra-v1",
        infra_diff=False,
        caller_sha="c" * 40,
        reusable_workflow_sha="w" * 40,
    )
    values.update(changes)
    return CONV.Scope(**values)


SCOPE = _scope()


def _receipt(scope=SCOPE, *, run_id=1, run_attempt=1, digest="a", verdict="pass", p1_ids=(), artifact=None, source_attempt=None, reported=None):
    digest = digest * 64 if len(digest) == 1 else digest
    epoch = CONV.derive_epoch(scope)
    processing = CONV.ProcessingKey(scope.repository_id, scope.pr_number, run_id, run_attempt)
    artifact = artifact or f"primary-audit-{run_id}-{run_attempt}"
    return CONV.Receipt(
        schema_version=1,
        scope=scope,
        epoch=epoch,
        processing_key=processing,
        round_key=CONV.RoundKey(epoch, run_id, digest),
        event_id=CONV._event_id(epoch=epoch, run_id=run_id, run_attempt=run_attempt, audit_digest=digest),
        run_id=run_id,
        run_attempt=run_attempt,
        audit_digest=digest,
        verdict=verdict,
        p1_ids=tuple(p1_ids),
        source_attempt=run_attempt if source_attempt is None else source_attempt,
        artifact_id=artifact,
        reported_decision=reported,
    )


def test_producer_payload_preserves_all_attempt_guards(tmp_path):
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "head_sha": SCOPE.head_sha, "run_id": 77,
        "run_attempt": 1, "pr": 42, "verdict": "pass", "reviewer": "codex",
        "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    audit_bytes = json.dumps(audit, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    audit_path = tmp_path / "primary-audit.json"
    output_path = tmp_path / "receipt.json"
    audit_path.write_bytes(audit_bytes)
    epoch = CONV.derive_epoch(SCOPE)
    runner = (
        "import hashlib, importlib.util, json, os, sys\n"
        "from pathlib import Path\n"
        "spec=importlib.util.spec_from_file_location('gate_aggregate_subprocess', sys.argv[1])\n"
        "mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)\n"
        "assert os.environ['GATE_TIER']=='internal' and os.environ['GATE_ARTIFACT']=='primary-audit-v1-1'\n"
        "raw=Path(sys.argv[2]).read_bytes(); audit=json.loads(raw)\n"
        "scope=mod._CONVERGENCE.Scope(**json.loads(os.environ['GATE_SCOPE']))\n"
        "identity=mod.Identity(123, scope.head_sha, 77, 2, 42)\n"
        "out=mod.evaluate(quality_result='success', primary_result='success', runner='self', is_draft=False, review_expected=True, audit=audit, audit_error=None, identity=identity, audit_source_attempt=1, audit_artifact_name=os.environ['GATE_ARTIFACT'], scope=scope, audit_digest=hashlib.sha256(raw).hexdigest())\n"
        "Path(sys.argv[3]).write_bytes(json.dumps(out.convergence_envelope, sort_keys=True, separators=(',', ':')).encode() + b'\\n')\n"
    )
    argv = [
        sys.executable,
        "-c", runner, str(AGGREGATE_PATH), str(audit_path), str(output_path),
    ]
    env = {"PATH": os.environ["PATH"], "GATE_TIER": "internal", "GATE_ARTIFACT": "primary-audit-v1-1", "GATE_SCOPE": json.dumps(SCOPE.as_dict(), sort_keys=True)}
    completed = subprocess.run(argv, check=True, capture_output=True, text=True, env=env)
    assert completed.args == argv
    payload_bytes = output_path.read_bytes()
    payload = json.loads(payload_bytes)
    assert payload_bytes.endswith(b"\n") and payload["scope"] == SCOPE.as_dict()
    assert (payload["epoch"], payload["source_attempt"], payload["artifact_name"]) == (epoch, 1, "primary-audit-v1-1")
    assert payload["audit_digest"] == hashlib.sha256(audit_bytes).hexdigest()
    assert env["GATE_TIER"] == "internal" and env["GATE_ARTIFACT"] == "primary-audit-v1-1"
    CONV.validate_scope(CONV.Scope(**payload["scope"]))


def test_audit_digest_is_raw_bytes_digest():
    raw = b'{"z":1,"a":2}\n'
    parsed = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() != hashlib.sha256(json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_disposition_producer_writes_bound_receipt_bytes_from_raw_audit(tmp_path):
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "pr": 42, "head_sha": SCOPE.head_sha,
        "base_sha": SCOPE.base_sha, "diff_digest": SCOPE.diff_digest,
        "policy_version": SCOPE.policy_version, "policy_digest": SCOPE.policy_digest,
        "tier": SCOPE.tier, "effective_tier": SCOPE.effective_tier,
        "infra_classifier_version": SCOPE.infra_classifier_version, "infra_diff": SCOPE.infra_diff,
        "caller_sha": SCOPE.caller_sha, "reusable_workflow_sha": SCOPE.reusable_workflow_sha,
        "run_id": 77, "run_attempt": 1,
        "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    audit_bytes = json.dumps(audit, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    audit_path = tmp_path / "canonical-audit.json"
    audit_path.write_bytes(audit_bytes)
    evidence_path = tmp_path / "evidence.txt"
    evidence_bytes = b"immutable evidence\n"
    evidence_path.write_bytes(evidence_bytes)
    blob_sha = hashlib.sha1(f"blob {len(evidence_bytes)}\0".encode() + evidence_bytes).hexdigest()
    evidence_ref = f"blob:{blob_sha}"
    manifest_path = tmp_path / "evidence-manifest.json"
    manifest_path.write_text(json.dumps([{
        "type": "blob", "ref": evidence_ref, "path": str(evidence_path),
        "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
    }]))
    output_dir = tmp_path / "artifacts"
    epoch = CONV.derive_epoch(SCOPE)
    digest = hashlib.sha256(audit_bytes).hexdigest()
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository-id", "123", "--pr-number", "42", "--epoch", epoch,
        "--head-sha", SCOPE.head_sha, "--diff-digest", SCOPE.diff_digest,
        "--primary-run-id", "77", "--primary-run-attempt", "1", "--audit-digest", digest,
        "--finding-id", "p1", "--issuer-login", "maintainer", "--issuer-user-id", "9001",
        "--pr-author-login", "author",
        "--control-run-id", "control-77", "--approval-ref", "issuer-not-pr-author:author",
        "--scope-json", json.dumps(SCOPE.as_dict(), sort_keys=True),
        "--evidence-manifest-path", str(manifest_path),
        "--issued-at", "2026-08-20T08:00:00Z", "--expires-at", "2099-08-20T08:00:00Z",
        "--nonce", "nonce-77", "--reason", "locked upstream behavior",
        "--evidence-ref", evidence_ref,
    ]
    producer_env = {"PATH": os.environ["PATH"], "GITHUB_RUN_ID": "control-77", "GITHUB_ACTOR": "maintainer"}
    first = subprocess.run(argv, check=True, capture_output=True, text=True, env=producer_env)
    assert first.args == argv
    result = json.loads(first.stdout)
    artifact_path = Path(result["path"])
    payload_bytes = artifact_path.read_bytes()
    payload = json.loads(payload_bytes)
    assert result["artifact"] == f"gate-disposition-receipt-v1-{epoch}-{digest[:12]}-nonce-77"
    assert payload_bytes == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert payload["kind"] == "gate-disposition-receipt-v1"
    assert payload["audit_digest"] == digest
    assert payload["finding_id"] == "p1"
    assert payload["evidence_refs"] == [evidence_ref]
    assert payload["evidence_manifest"][0]["sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
    receipt = CONV.DispositionReceipt(**{
        field: payload[field]
        for field in CONV.DispositionReceipt.__dataclass_fields__
        if field in payload
    })
    assert payload["receipt_digest"] == CONV.disposition_receipt_digest(receipt)
    second = subprocess.run(argv, check=True, capture_output=True, text=True, env=producer_env)
    assert json.loads(second.stdout)["written"] is False
    assert artifact_path.read_bytes() == payload_bytes
    wrong_digest = argv.copy()
    wrong_digest[wrong_digest.index(digest)] = "0" * 64
    wrong = subprocess.run(wrong_digest, capture_output=True, text=True, env=producer_env)
    assert wrong.returncode == 1
    assert "dispatch audit_digest does not match raw audit bytes" in wrong.stderr
    missing_epoch = argv.copy()
    epoch_index = missing_epoch.index("--epoch")
    del missing_epoch[epoch_index:epoch_index + 2]
    missing_epoch_result = subprocess.run(missing_epoch, capture_output=True, text=True, env=producer_env)
    assert missing_epoch_result.returncode == 1
    assert "epoch is required" in missing_epoch_result.stderr
    self_issue = argv.copy()
    self_issue[self_issue.index("maintainer")] = "author"
    self_failed = subprocess.run(self_issue, capture_output=True, text=True, env=producer_env)
    assert self_failed.returncode == 1
    assert "issuer must differ from PR author" in self_failed.stderr


def test_disposition_producer_rejects_unverifiable_evidence_ref(tmp_path):
    audit = {
        "kind": "primary_review", "schema_version": 1, "repository_id": 123,
        "pr": 42, "base_sha": SCOPE.base_sha, "head_sha": SCOPE.head_sha,
        "diff_digest": SCOPE.diff_digest, "policy_version": SCOPE.policy_version,
        "policy_digest": SCOPE.policy_digest, "tier": SCOPE.tier,
        "effective_tier": SCOPE.effective_tier, "infra_classifier_version": SCOPE.infra_classifier_version,
        "infra_diff": SCOPE.infra_diff, "caller_sha": SCOPE.caller_sha,
        "reusable_workflow_sha": SCOPE.reusable_workflow_sha, "run_id": 77,
        "run_attempt": 1, "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit, sort_keys=True))
    digest = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps([{"type": "url", "ref": "not-an-immutable-reference", "path": str(audit_path), "sha256": digest}]))
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "issue", "--output-dir", str(tmp_path),
        "--audit-path", str(audit_path), "--repository-id", "123", "--pr-number", "42",
        "--epoch", CONV.derive_epoch(SCOPE), "--scope-json", json.dumps(SCOPE.as_dict()),
        "--head-sha", SCOPE.head_sha, "--diff-digest", SCOPE.diff_digest,
        "--primary-run-id", "77", "--primary-run-attempt", "1", "--audit-digest", digest,
        "--finding-id", "p1", "--issuer-login", "maintainer", "--issuer-user-id", "9001",
        "--pr-author-login", "author", "--control-run-id", "control-77", "--approval-ref", "issuer-not-pr-author:author",
        "--issued-at", "2026-08-20T08:00:00Z", "--expires-at", "2099-08-20T08:00:00Z",
        "--nonce", "nonce-invalid", "--reason", "reason", "--evidence-ref", "not-an-immutable-reference",
        "--evidence-manifest-path", str(manifest_path),
    ]
    failed = subprocess.run(argv, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert failed.returncode == 1
    assert "only blob:<git-sha> is allowlisted" in failed.stderr
    evidence_bytes = b"actual immutable evidence\n"
    evidence_path = tmp_path / "evidence-mismatch.txt"
    evidence_path.write_bytes(evidence_bytes)
    blob_sha = hashlib.sha1(f"blob {len(evidence_bytes)}\0".encode() + evidence_bytes).hexdigest()
    manifest_path.write_text(json.dumps([{
        "type": "blob", "ref": f"blob:{blob_sha}", "path": str(evidence_path), "sha256": "0" * 64,
    }]))
    mismatch = argv.copy()
    mismatch[mismatch.index("not-an-immutable-reference")] = f"blob:{blob_sha}"
    mismatch_result = subprocess.run(mismatch, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert mismatch_result.returncode == 1
    assert "evidence content digest mismatch" in mismatch_result.stderr


def test_disposition_revocation_producer_is_append_only(tmp_path):
    epoch = CONV.derive_epoch(SCOPE)
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "revoke",
        "--output-dir", str(tmp_path), "--epoch", epoch, "--nonce", "nonce-77",
        "--reason", "evidence withdrawn", "--actor", "maintainer",
        "--revoked-at", "2026-08-20T09:00:00Z", "--evidence-ref", "artifact:withdrawal-1",
    ]
    first = subprocess.run(argv, check=True, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    artifact_path = Path(json.loads(first.stdout)["path"])
    original = artifact_path.read_bytes()
    second = subprocess.run(argv, check=True, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert json.loads(second.stdout)["written"] is False
    assert artifact_path.read_bytes() == original
    conflict = argv.copy()
    conflict[conflict.index("evidence withdrawn")] = "different reason"
    failed = subprocess.run(conflict, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert failed.returncode == 1
    assert "immutable artifact conflict" in failed.stderr
    assert artifact_path.read_bytes() == original


def test_aggregate_envelope_preserves_scope_attempt_artifact_and_raw_digest():
    identity = AGG.Identity(repository_id=123, head_sha=SCOPE.head_sha, run_id=77, run_attempt=2, pr=42)
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "head_sha": SCOPE.head_sha, "run_id": 77,
        "run_attempt": 1, "pr": 42, "verdict": "pass", "reviewer": "codex",
        "result": {"findings": [{"id": "p1", "severity": "major"}, {"id": "p2", "severity": "minor"}]},
    }
    raw = json.dumps(audit, indent=2).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    outcome = AGG.evaluate(
        quality_result="success", primary_result="success", runner="self",
        is_draft=False, review_expected=True, audit=audit, audit_error=None,
        identity=identity, audit_source_attempt=1,
        audit_artifact_name="primary-audit-v1-1", scope=SCOPE, audit_digest=digest,
    )
    envelope = outcome.convergence_envelope
    assert envelope["schema_version"] == 1
    assert envelope["kind"] == "gate_convergence_round"
    assert envelope["scope"] == SCOPE.as_dict()
    assert envelope["audit_digest"] == digest
    assert (envelope["source_attempt"], envelope["artifact_name"]) == (1, "primary-audit-v1-1")
    assert envelope["state"]["clean_streak"] == 0


def _aggregate_case(audit, *, primary_result="success", identity=None):
    identity = identity or AGG.Identity(repository_id=123, head_sha=SCOPE.head_sha, run_id=77, run_attempt=2, pr=42)
    raw = json.dumps(audit, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    return AGG.evaluate(
        quality_result="success", primary_result=primary_result, runner="self",
        is_draft=False, review_expected=True, audit=audit, audit_error=None,
        identity=identity, audit_source_attempt=1, audit_artifact_name="primary-audit-v1-1",
        scope=SCOPE, audit_digest=hashlib.sha256(raw).hexdigest(),
    )


@pytest.mark.parametrize("finding", [{}, {"severity": None}, {"severity": 3}, {"severity": "unknown"}, {"id": "minor", "severity": "minor"}, {"id": "nit", "severity": "nit"}])
def test_handoff_requires_known_finding_severity_and_verified_identity(finding):
    audit = {
        "kind": "primary_review", "schema_version": 1, "repository_id": 123,
        "head_sha": SCOPE.head_sha, "run_id": 77, "run_attempt": 1, "pr": 42,
        "verdict": "pass", "reviewer": "codex", "result": {"findings": [finding]},
    }
    outcome = _aggregate_case(audit)
    if finding.get("severity") in ("minor", "nit"):
        assert outcome.convergence_envelope is not None
    else:
        assert outcome.convergence_envelope is None and outcome.reason_code == "audit_invalid"


def test_handoff_rejects_identity_and_job_verdict_mismatch():
    audit = {
        "kind": "primary_review", "schema_version": 1, "repository_id": 123,
        "head_sha": SCOPE.head_sha, "run_id": 77, "run_attempt": 1, "pr": 42,
        "verdict": "pass", "reviewer": "codex", "result": {"findings": []},
    }
    bad_identity = dict(audit, repository_id=999)
    assert _aggregate_case(bad_identity).convergence_envelope is None
    assert _aggregate_case(dict(audit, verdict="fail"), primary_result="success").convergence_envelope is None


def test_multiple_runs_same_head_replay_in_run_id_order():
    later = _receipt(run_id=2, digest="2")
    earlier = _receipt(run_id=1, digest="1")
    forward = CONV.replay_receipts(scope=SCOPE, receipts=[later, earlier])
    reverse = CONV.replay_receipts(scope=SCOPE, receipts=[earlier, later])
    assert forward.as_dict() == reverse.as_dict()
    assert forward.decision == "converged"


def test_rerun_failed_reuses_audit_without_double_counting():
    first = _receipt(run_id=3, run_attempt=1, digest="3", source_attempt=1, artifact="primary-audit-3-1")
    rerun = _receipt(run_id=3, run_attempt=2, digest="3", source_attempt=1, artifact="primary-audit-3-1")
    state = CONV.replay_receipts(scope=SCOPE, receipts=[rerun, first])
    assert (state.clean_streak, state.eligible_rounds) == (1, 1)


def test_parallel_receipts_are_order_independent_and_conflicts_fail_closed():
    one = _receipt(run_id=4, digest="4")
    two = _receipt(run_id=5, digest="5")
    assert CONV.replay_receipts(scope=SCOPE, receipts=[one, two]).as_dict() == CONV.replay_receipts(scope=SCOPE, receipts=[two, one]).as_dict()
    conflict = _receipt(run_id=4, digest="4", artifact="other-artifact")
    assert CONV.replay_receipts(scope=SCOPE, receipts=[one, conflict]).decision == "fail_closed"


def test_replay_uses_receipt_bytes_not_reported_counters():
    receipt = _receipt(run_id=6, digest="6", reported="manual_required")
    receipt = CONV.Receipt(**{**receipt.__dict__, "reported_clean_streak": 999, "reported_eligible_rounds": 999})
    state = CONV.replay_receipts(scope=SCOPE, receipts=[receipt])
    assert (state.clean_streak, state.eligible_rounds) == (1, 1)


def test_scope_change_excludes_old_epoch_receipts_from_replay():
    changed = _scope(head_sha="n" * 40)
    old = _receipt(SCOPE, run_id=7, digest="7")
    current = _receipt(changed, run_id=8, digest="8")
    state = CONV.replay_receipts(scope=changed, receipts=[old, current])
    assert state.epoch == CONV.derive_epoch(changed)
    assert (state.clean_streak, state.eligible_rounds) == (1, 1)


def test_receipt_source_attempt_artifact_and_epoch_guards_fail_closed_together():
    receipt = _receipt(run_id=9, digest="9", source_attempt=1)
    with pytest.raises(CONV.ReceiptValidationError):
        CONV.validate_receipt(replace(receipt, source_attempt=2), SCOPE)
    with pytest.raises(CONV.ReceiptValidationError):
        CONV.validate_receipt(replace(receipt, artifact_name=receipt.artifact_id, artifact_id="other"), SCOPE)
    with pytest.raises(CONV.ReceiptValidationError):
        CONV.validate_receipt(replace(receipt, epoch="0" * 64), SCOPE)
