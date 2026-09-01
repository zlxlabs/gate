"""Cross-process producer/consumer contract tests for convergence receipts."""

import argparse
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


def _scoped_audit(scope=SCOPE, *, run_id=77, run_attempt=1, verdict="pass"):
    return {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": scope.repository_id, "head_sha": scope.head_sha,
        "run_id": run_id, "run_attempt": run_attempt, "pr": scope.pr_number,
        "verdict": verdict, "reviewer": "codex-sub",
        "base_sha": scope.base_sha, "diff_digest": scope.diff_digest,
        "policy_version": scope.policy_version, "policy_digest": scope.policy_digest,
        "tier": scope.tier, "caller_sha": scope.caller_sha,
        "reusable_workflow_sha": scope.reusable_workflow_sha,
        "result": {"findings": []},
    }


def _receipt_from_payload(payload):
    scope = CONV.Scope(**payload["scope"])
    return CONV.Receipt(
        schema_version=payload["schema_version"],
        scope=scope,
        epoch=payload["epoch"],
        processing_key=CONV.ProcessingKey(*payload["processing_key"]),
        round_key=CONV.RoundKey(*payload["round_key"]),
        event_id=payload["event_id"],
        run_id=payload["run_id"],
        run_attempt=payload["run_attempt"],
        audit_digest=payload["audit_digest"],
        verdict=payload["verdict"],
        p1_ids=tuple(payload["p1_ids"]),
        source_attempt=payload["source_attempt"],
        artifact_id=payload["artifact_id"],
        artifact_name=payload["artifact_name"],
        receipt_kind=payload["receipt_kind"],
        reported_decision=payload["decision"],
        reported_clean_streak=payload["clean_streak"],
        reported_eligible_rounds=payload["eligible_rounds"],
    )


def test_aggregate_cli_receipt_bytes_validate_and_replay(capfd, tmp_path):
    audit = _scoped_audit()
    audit_bytes = json.dumps(audit, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    audit_dir = tmp_path / "primary-audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_bytes(audit_bytes)
    summary_path = tmp_path / "summary.md"
    terminal_path = tmp_path / "gate-terminal.json"
    receipt_path = tmp_path / "convergence-receipt" / "convergence-receipt.json"
    artifact_name = f"primary-audit-v2-{SCOPE.repository_id}-{SCOPE.head_sha}-77-1"
    argv = [
        sys.executable, str(AGGREGATE_PATH),
        "--quality-result", "success", "--primary-result", "success",
        "--runner", "self", "--is-draft", "false", "--review-expected", "true",
        "--repository-id", str(SCOPE.repository_id), "--repository", "zlxlabs/gate",
        "--head-sha", SCOPE.head_sha, "--run-id", "77", "--run-attempt", "1",
        "--pr-number", str(SCOPE.pr_number), "--audit-source-attempt", "1",
        "--audit-artifact-name", artifact_name, "--audit-dir", str(audit_dir),
        "--summary-path", str(summary_path), "--terminal-path", str(terminal_path),
        "--convergence-receipt-path", str(receipt_path),
    ]

    completed = subprocess.run(argv, check=True, env=os.environ.copy())
    stdout, stderr = capfd.readouterr()
    assert completed.args == argv
    assert "Convergence receipt: produced" in stdout
    assert stderr == ""

    payload_bytes = receipt_path.read_bytes()
    payload = json.loads(payload_bytes)
    expected_digest = CONV.canonical_audit_digest(audit)
    assert payload["audit_digest"] == expected_digest
    assert payload_bytes == json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    receipt = _receipt_from_payload(payload)
    CONV.validate_receipt(receipt, SCOPE)

    audit_digest = expected_digest
    primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=SCOPE.repository_id,
        pr_number=SCOPE.pr_number, head_sha=SCOPE.head_sha,
        run_id=77, run_attempt=1, verdict="pass", p1_ids=(),
    )
    decision = CONV.evaluate_round(
        state=CONV.initial_state(SCOPE), scope=SCOPE, primary=primary,
        audit_digest=audit_digest, waiver_receipts=(),
        processing_key=CONV.ProcessingKey(SCOPE.repository_id, SCOPE.pr_number, 77, 1),
    )
    replayed = CONV.replay_receipts(scope=SCOPE, receipts=(receipt,))
    assert (replayed.clean_streak, replayed.eligible_rounds, replayed.unavailable_streak) == (
        decision.state.clean_streak,
        decision.state.eligible_rounds,
        decision.state.unavailable_streak,
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


def test_audit_digest_is_canonical_not_raw_bytes():
    first = _scoped_audit()
    first["duration_ms"] = 1
    second = dict(first)
    second["duration_ms"] = 999
    first_bytes = json.dumps(first, indent=2).encode()
    second_bytes = json.dumps(second, indent=2).encode()
    assert CONV.canonical_audit_digest(first) == CONV.canonical_audit_digest(second)
    assert hashlib.sha256(first_bytes).hexdigest() != hashlib.sha256(second_bytes).hexdigest()


def test_disposition_producer_writes_minimal_receipt_bytes_from_raw_audit(tmp_path):
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "pr": 42, "head_sha": SCOPE.head_sha,
        "base_sha": SCOPE.base_sha, "diff_digest": SCOPE.diff_digest,
        "policy_version": SCOPE.policy_version, "policy_digest": SCOPE.policy_digest,
        "tier": SCOPE.tier,
        "caller_sha": SCOPE.caller_sha, "reusable_workflow_sha": SCOPE.reusable_workflow_sha,
        "run_id": 77, "run_attempt": 1,
        "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    audit_bytes = json.dumps(audit, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    audit_path = tmp_path / "canonical-audit.json"
    audit_path.write_bytes(audit_bytes)
    output_dir = tmp_path / "artifacts"
    epoch = CONV.derive_epoch(SCOPE)
    digest = CONV.canonical_audit_digest(audit)
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository-id", "123", "--pr-number", "42", "--head-sha", SCOPE.head_sha,
        "--finding-id", "p1",
        "--scope-json", json.dumps(SCOPE.as_dict(), sort_keys=True),
        "--reason", "locked upstream behavior",
        "--approver", "octocat",
        "--approver-id", "1",
        "--approved-at", "2026-08-30T12:00:00Z",
    ]
    producer_env = {"PATH": os.environ["PATH"], "GITHUB_RUN_ID": "control-77", "GITHUB_ACTOR": "maintainer"}
    first = subprocess.run(argv, check=True, capture_output=True, text=True, env=producer_env)
    assert first.args == argv
    result = json.loads(first.stdout)
    artifact_path = Path(result["path"])
    payload_bytes = artifact_path.read_bytes()
    payload = json.loads(payload_bytes)
    assert result["artifact"] == f"gate-disposition-receipt-v2-{epoch}-{digest[:12]}-p1"
    assert payload_bytes == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert payload["kind"] == CONV.DISPOSITION_RECEIPT_KIND
    assert set(payload) - {"kind"} == set(CONV.DispositionReceipt.__dataclass_fields__)
    assert payload["audit_digest"] == digest and payload["finding_id"] == "p1"
    assert payload["approver"] == "octocat"
    assert payload["approver_id"] == 1
    assert payload["approved_at"] == "2026-08-30T12:00:00Z"
    receipt = CONV.DispositionReceipt(**{
        field: payload[field]
        for field in CONV.DispositionReceipt.__dataclass_fields__
        if field in payload
    })
    assert receipt.as_dict() == {field: payload[field] for field in receipt.__dataclass_fields__}
    second = subprocess.run(argv, check=True, capture_output=True, text=True, env=producer_env)
    assert json.loads(second.stdout)["written"] is False
    assert artifact_path.read_bytes() == payload_bytes
    parsed = CONV.parse_disposition_receipt(json.loads(payload_bytes))
    primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=SCOPE.repository_id, pr_number=SCOPE.pr_number,
        head_sha=SCOPE.head_sha, run_id=77, run_attempt=1, verdict="fail", p1_ids=("p1",),
    )
    status = CONV.validate_disposition_receipt(
        parsed, scope=SCOPE, primary=primary, audit_digest=digest,
    )
    assert (status.consumable, status.reason) == (True, "active_false_positive")
    assert (parsed.approver, parsed.approver_id, parsed.approved_at) == (
        "octocat", 1, "2026-08-30T12:00:00Z",
    )


def test_disposition_producer_rejects_non_p1_finding(tmp_path):
    audit = {
        "result": {"findings": [{"id": "minor", "severity": "minor"}]},
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit, sort_keys=True))
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "issue", "--output-dir", str(tmp_path),
        "--audit-path", str(audit_path), "--repository-id", "123", "--pr-number", "42",
        "--head-sha", SCOPE.head_sha, "--finding-id", "minor", "--reason", "reason",
        "--approver", "octocat", "--approver-id", "1",
        "--approved-at", "2026-08-30T12:00:00Z",
        "--scope-json", json.dumps(SCOPE.as_dict()),
    ]
    failed = subprocess.run(argv, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert failed.returncode == 1
    assert "finding_id must identify a P1 finding" in failed.stderr


def test_issue_function_bytes_feed_parse_disposition_receipt(tmp_path):
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "pr": 42, "head_sha": SCOPE.head_sha,
        "base_sha": SCOPE.base_sha, "diff_digest": SCOPE.diff_digest,
        "policy_version": SCOPE.policy_version, "policy_digest": SCOPE.policy_digest,
        "tier": SCOPE.tier,
        "caller_sha": SCOPE.caller_sha, "reusable_workflow_sha": SCOPE.reusable_workflow_sha,
        "run_id": 77, "run_attempt": 1,
        "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    audit_path = tmp_path / "canonical-audit.json"
    audit_path.write_bytes(json.dumps(audit, indent=2).encode("utf-8") + b"\n")
    output_dir = tmp_path / "out"
    spec = importlib.util.spec_from_file_location("gate_disposition_issue_receipt", DISPOSITION_PRODUCER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    args = argparse.Namespace(
        output_dir=str(output_dir),
        audit_path=str(audit_path),
        repository_id="123",
        pr_number="42",
        head_sha=SCOPE.head_sha,
        finding_id="p1",
        reason="locked upstream behavior",
        approver="octocat",
        approver_id="7",
        approved_at="2026-08-30T12:00:00Z",
        scope_json=json.dumps(SCOPE.as_dict(), sort_keys=True),
        input_stdin=False,
    )
    assert module.issue(args, {}) == 0
    produced = next(output_dir.iterdir())
    payload = json.loads(produced.read_bytes())
    receipt = CONV.parse_disposition_receipt(payload)
    primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=SCOPE.repository_id, pr_number=SCOPE.pr_number,
        head_sha=SCOPE.head_sha, run_id=77, run_attempt=1, verdict="fail", p1_ids=("p1",),
    )
    status = CONV.validate_disposition_receipt(
        receipt, scope=SCOPE, primary=primary,
        audit_digest=CONV.canonical_audit_digest(audit),
    )
    assert status.reason == "active_false_positive"
    assert (receipt.approver, receipt.approver_id, receipt.approved_at) == (
        "octocat", 7, "2026-08-30T12:00:00Z",
    )


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"--approver": "   "}, "approver must be non-empty"),
        ({"--approver-id": "0"}, "approver_id must be a positive integer"),
        ({"--approver-id": None}, "approver_id is required"),
        ({"--approved-at": "2026-08-30"}, "time-of-day"),
        ({"--approved-at": None}, "approved_at is required"),
        ({"--approved-at": "2026-08-30Tnot-a-time"}, "ISO-8601 timestamp"),
    ],
)
def test_disposition_producer_rejects_malformed_auth_fields(tmp_path, override, needle):
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "pr": 42, "head_sha": SCOPE.head_sha,
        "base_sha": SCOPE.base_sha, "diff_digest": SCOPE.diff_digest,
        "policy_version": SCOPE.policy_version, "policy_digest": SCOPE.policy_digest,
        "tier": SCOPE.tier,
        "caller_sha": SCOPE.caller_sha, "reusable_workflow_sha": SCOPE.reusable_workflow_sha,
        "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit, sort_keys=True))
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "issue",
        "--output-dir", str(tmp_path), "--audit-path", str(audit_path),
        "--repository-id", "123", "--pr-number", "42", "--head-sha", SCOPE.head_sha,
        "--finding-id", "p1", "--reason", "reason",
        "--approver", "octocat", "--approver-id", "1",
        "--approved-at", "2026-08-30T12:00:00Z",
        "--scope-json", json.dumps(SCOPE.as_dict()),
    ]
    flag, value = next(iter(override.items()))
    index = argv.index(flag)
    if value is None:
        del argv[index:index + 2]
    else:
        argv[index + 1] = value
    failed = subprocess.run(argv, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert failed.returncode == 1
    assert needle in failed.stderr


def _p1_issue_argv(tmp_path, *, approved_at="2026-08-30T12:00:00Z", reason="locked upstream behavior"):
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "pr": 42, "head_sha": SCOPE.head_sha,
        "base_sha": SCOPE.base_sha, "diff_digest": SCOPE.diff_digest,
        "policy_version": SCOPE.policy_version, "policy_digest": SCOPE.policy_digest,
        "tier": SCOPE.tier,
        "caller_sha": SCOPE.caller_sha, "reusable_workflow_sha": SCOPE.reusable_workflow_sha,
        "run_id": 77, "run_attempt": 1,
        "result": {"findings": [{"id": "p1", "severity": "major"}]},
    }
    audit_path = tmp_path / "canonical-audit.json"
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "issue",
        "--output-dir", str(tmp_path / "artifacts"), "--audit-path", str(audit_path),
        "--repository-id", "123", "--pr-number", "42", "--head-sha", SCOPE.head_sha,
        "--finding-id", "p1",
        "--scope-json", json.dumps(SCOPE.as_dict(), sort_keys=True),
        "--reason", reason,
        "--approver", "octocat",
        "--approver-id", "1",
        "--approved-at", approved_at,
    ]
    return argv, {"PATH": os.environ["PATH"]}


def test_disposition_producer_same_params_new_approved_at_is_noop(tmp_path):
    first_argv, env = _p1_issue_argv(tmp_path, approved_at="2026-08-30T12:00:00Z")
    first = subprocess.run(first_argv, check=True, capture_output=True, text=True, env=env)
    first_result = json.loads(first.stdout)
    artifact_path = Path(first_result["path"])
    original = artifact_path.read_bytes()
    second_argv, env = _p1_issue_argv(tmp_path, approved_at="2026-08-30T12:00:01Z")
    second = subprocess.run(second_argv, capture_output=True, text=True, env=env)
    assert second.returncode == 0
    assert json.loads(second.stdout)["written"] is False
    assert artifact_path.read_bytes() == original
    assert "keeping the original" in second.stderr


def test_disposition_producer_reason_change_still_conflicts(tmp_path):
    first_argv, env = _p1_issue_argv(tmp_path, reason="locked upstream behavior")
    subprocess.run(first_argv, check=True, capture_output=True, text=True, env=env)
    second_argv, env = _p1_issue_argv(tmp_path, reason="a different reason")
    second = subprocess.run(second_argv, capture_output=True, text=True, env=env)
    assert second.returncode == 1
    assert "immutable artifact conflict" in second.stderr


def _failing_runtime_audit(*, duration_ms, run_attempt, findings=None):
    findings = findings or [{"id": "p1", "severity": "major", "file": "lock.py", "line": 12}]
    return {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": SCOPE.repository_id, "pr": SCOPE.pr_number,
        "head_sha": SCOPE.head_sha, "base_sha": SCOPE.base_sha,
        "diff_digest": SCOPE.diff_digest, "policy_version": SCOPE.policy_version,
        "policy_digest": SCOPE.policy_digest, "tier": SCOPE.tier,
        "caller_sha": SCOPE.caller_sha, "reusable_workflow_sha": SCOPE.reusable_workflow_sha,
        "run_id": 77, "run_attempt": run_attempt, "verdict": "fail", "reviewer": "codex-sub",
        "duration_ms": duration_ms, "total_tokens": duration_ms * 3,
        "started_at": f"2026-08-27T0{run_attempt}:00:00Z",
        "result": {"findings": findings},
    }


def test_disposition_receipt_consumes_same_findings_across_runtime_bytes(tmp_path):
    first = _failing_runtime_audit(duration_ms=11, run_attempt=2)
    second = _failing_runtime_audit(duration_ms=99, run_attempt=4)
    assert CONV.canonical_audit_digest(first) == CONV.canonical_audit_digest(second)
    assert hashlib.sha256(json.dumps(first).encode()).hexdigest() != hashlib.sha256(
        json.dumps(second).encode()
    ).hexdigest()
    audit_path = tmp_path / "audit.json"
    audit_path.write_bytes(json.dumps(first, indent=2).encode() + b"\n")
    argv = [
        sys.executable, str(DISPOSITION_PRODUCER), "issue",
        "--output-dir", str(tmp_path / "out"), "--audit-path", str(audit_path),
        "--repository-id", "123", "--pr-number", "42", "--head-sha", SCOPE.head_sha,
        "--finding-id", "p1", "--reason", "locked upstream behavior",
        "--approver", "octocat", "--approver-id", "1",
        "--approved-at", "2026-08-30T12:00:00Z",
        "--scope-json", json.dumps(SCOPE.as_dict(), sort_keys=True),
    ]
    produced = subprocess.run(argv, check=True, capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    payload = json.loads(Path(json.loads(produced.stdout)["path"]).read_bytes())
    receipt = CONV.parse_disposition_receipt(payload)
    identity = AGG.Identity(123, SCOPE.head_sha, 77, 4, 42)
    digest = CONV.canonical_audit_digest(second)
    outcome = AGG.evaluate(
        quality_result="success", primary_result="failure", runner="self",
        is_draft=False, review_expected=True, audit=second, audit_error=None,
        identity=identity, audit_source_attempt=4, audit_artifact_name="primary-audit-v2-4",
        scope=SCOPE, audit_digest=digest, waiver_receipts=(receipt,),
    )
    assert outcome.gate_result == "pass"
    assert outcome.resolved_findings
    other = _failing_runtime_audit(
        duration_ms=99, run_attempt=4,
        findings=[{"id": "p2", "severity": "major", "file": "lock.py", "line": 12}],
    )
    mismatched = AGG.evaluate(
        quality_result="success", primary_result="failure", runner="self",
        is_draft=False, review_expected=True, audit=other, audit_error=None,
        identity=identity, audit_source_attempt=4, audit_artifact_name="primary-audit-v2-4",
        scope=SCOPE, audit_digest=CONV.canonical_audit_digest(other),
        waiver_receipts=(receipt,),
    )
    assert mismatched.gate_result == "fail"
    assert mismatched.resolved_findings == []


def test_legacy_raw_bytes_receipt_still_consumes_same_audit_file():
    audit = _failing_runtime_audit(duration_ms=11, run_attempt=2)
    raw = json.dumps(audit, indent=2).encode() + b"\n"
    legacy = hashlib.sha256(raw).hexdigest()
    canonical = CONV.canonical_audit_digest(audit)
    assert canonical != legacy
    receipt = CONV.DispositionReceipt(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="false-positive",
        repository_id=str(SCOPE.repository_id), pr_number=SCOPE.pr_number,
        epoch=CONV.derive_epoch(SCOPE), head_sha=SCOPE.head_sha,
        audit_digest=legacy, finding_id="p1", reason="locked upstream behavior",
        approver="octocat", approver_id=1, approved_at="2026-08-30T12:00:00Z",
    )
    identity = AGG.Identity(123, SCOPE.head_sha, 77, 2, 42)
    kwargs = dict(
        quality_result="success", primary_result="failure", runner="self",
        is_draft=False, review_expected=True, audit=audit, audit_error=None,
        identity=identity, audit_source_attempt=2, audit_artifact_name="primary-audit-v2-2",
        scope=SCOPE, audit_digest=canonical, waiver_receipts=(receipt,),
    )
    without_legacy = AGG.evaluate(**kwargs)
    with_legacy = AGG.evaluate(**kwargs, legacy_raw_audit_digest=legacy)
    assert without_legacy.gate_result == "fail"
    assert with_legacy.gate_result == "pass"


def test_aggregate_envelope_preserves_scope_attempt_artifact_and_digest():
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
