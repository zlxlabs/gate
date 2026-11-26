"""Carry blockers must keep an unhandled sol must_respond finding closed."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE_PATH = ROOT / ".github/actions/gate-aggregator/aggregate.py"
LEDGER_PATH = ROOT / ".github/actions/review-ledger/build_ledger.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AGG = _load("carry_blockers_aggregate_test", AGGREGATE_PATH)
LEDGER = _load("carry_blockers_ledger_test", LEDGER_PATH)
CARRY = AGG._CARRY_BLOCKERS

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Gate Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Gate Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV},
        check=True, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def _two_commit_repo(tmp_path: Path, *, change_file: bool = False) -> tuple[Path, str, str]:
    repo = tmp_path / "caller"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    (repo / "src").mkdir()
    (repo / "src/module.py").write_text("answer = 42\n", encoding="utf-8")
    _git("add", "src/module.py", cwd=repo)
    _git("commit", "-qm", "previous review head", cwd=repo)
    source = _git("rev-parse", "HEAD", cwd=repo)
    if change_file:
        (repo / "src/module.py").write_text("answer = 43\n", encoding="utf-8")
        _git("add", "src/module.py", cwd=repo)
        _git("commit", "-qm", "change finding file", cwd=repo)
    else:
        _git("commit", "--allow-empty", "-qm", "new head, same file blob", cwd=repo)
    target = _git("rev-parse", "HEAD", cwd=repo)
    return repo, source, target


def _finding(**changes):
    finding = {
        "id": "sol-review-1",
        "origin": "sol_verify",
        "gate_action": "must_respond",
        "file": "src/module.py",
        "line": 1,
        "severity": "major",
        "trigger_kind": "inferred",
        "category": "correctness",
        "trigger": "输入处于边界条件时执行该路径",
        "evidence": "代码路径未检查该输入范围",
        "title": "检查边界条件",
        "issue": "边界条件未处理",
        "acceptance": "覆盖边界输入并验证结果",
    }
    finding.update(changes)
    return finding


def _audit(*, target: str, current_findings: list[dict] | None = None) -> dict:
    findings = current_findings or []
    verdict = "fail" if findings else "pass"
    return {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "repository": "zlxlabs/caller", "pr": 42,
        "head_sha": target, "base_sha": "b" * 40, "diff_digest": "d" * 64,
        "policy_version": "policy-v1", "policy_digest": "p" * 64,
        "registry_commit": "r" * 40, "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40, "run_id": 999, "run_attempt": 1,
        "job_id": 777, "reviewer": "fixture", "verdict": verdict, "tier": "personal",
        "result": {"verdict": verdict, "findings": findings},
    }


def _annotate(
    tmp_path, monkeypatch, repo, source, target, *, prior=None, current_findings=None,
    open_findings=None, missing_blobs=False,
):
    prior = prior or _finding()
    previous_path = tmp_path / "previous-findings.json"
    previous_path.write_text(json.dumps({
        "available": True, "head_sha": source, "findings": [prior],
        "open_findings": [prior] if open_findings is None else open_findings,
    }), encoding="utf-8")
    audit_path = tmp_path / "primary-audit.json"
    audit_path.write_text(json.dumps(_audit(target=target, current_findings=current_findings)), encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", "zlxlabs/caller")
    monkeypatch.setenv("REPOSITORY_ID", "123")
    monkeypatch.setenv("HEAD_SHA", target)
    monkeypatch.setenv("RUN_ID", "999")
    monkeypatch.setenv("RUN_ATTEMPT", "1")
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setattr(AGG._NEIGHBOR_EVIDENCE, "collect_anchor_updates", lambda **_kwargs: None)

    def collect_blobs(*, head_paths, **_kwargs):
        if missing_blobs:
            return {}
        return {
            (sha, path): _git("rev-parse", "--verify", f"{sha}:{path}", cwd=repo)
            for sha, path in head_paths
        }

    monkeypatch.setattr(AGG._NEIGHBOR_EVIDENCE, "collect_file_blob_oids", collect_blobs)
    assert AGG._annotate_primary_audit_cli([
        "--audit-path", str(audit_path), "--previous-findings-path", str(previous_path),
    ]) == 0
    return audit_path


def _run_gate(tmp_path, monkeypatch, audit_path, target, *, quality_result="success", receipts=()):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(exist_ok=True)
    (audit_dir / "primary-review-audit.json").write_bytes(audit_path.read_bytes())
    terminal = tmp_path / "gate-terminal.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setattr(AGG, "_fetch_disposition_receipts", lambda **_kwargs: tuple(receipts))
    primary_result = "failure" if json.loads(audit_path.read_bytes())["verdict"] == "fail" else "success"
    args = [
        "--quality-result", quality_result,
        "--caller-checks", "failed" if quality_result == "failure" else "passed",
        "--preflight-result", "success", "--primary-result", primary_result,
        "--runner", "self", "--is-draft", "false", "--is-fork", "false",
        "--pr-author-json", '"owner"', "--review-expected", "true",
        "--repository-id", "123", "--repository", "zlxlabs/caller",
        "--head-sha", target, "--run-id", "999", "--run-attempt", "1",
        "--pr-number", "42", "--audit-source-attempt", "1",
        "--audit-artifact-name", "primary-audit-v2-fixture",
        "--audit-dir", str(audit_dir), "--terminal-path", str(terminal),
        "--summary-path", str(summary),
    ]
    return AGG.main(args), terminal, summary


def test_primary_relation_records_previous_context_source_head(tmp_path):
    source = "a" * 40
    finding = _finding()
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps({
        "available": True, "head_sha": source,
        "findings": [finding], "open_findings": [finding],
    }), encoding="utf-8")
    audit_path = tmp_path / "primary-audit.json"
    audit_path.write_text(json.dumps({
        "verdict": "pass", "result": {"findings": []},
    }), encoding="utf-8")
    assert AGG._annotate_primary_audit_cli([
        "--audit-path", str(audit_path), "--previous-findings-path", str(previous_path),
    ]) == 0
    relation = json.loads(audit_path.read_text(encoding="utf-8"))["finding_relation"]
    assert relation["previous_head_sha"] == source


def test_primary_audit_carries_unreleased_sol_finding_and_current_run_rejection_releases(
    tmp_path, monkeypatch, capsys,
):
    repo, source, target = _two_commit_repo(tmp_path)
    audit_path = _annotate(tmp_path, monkeypatch, repo, source, target)
    raw_audit = audit_path.read_bytes()
    assert raw_audit.endswith(b"\n")
    audit = json.loads(raw_audit)
    prior = _finding()
    assert audit["result"]["findings"] == [prior]
    assert audit["result"]["verdict"] == audit["verdict"] == "fail"
    assert audit["finding_relation"]["carried_findings"] == [{
        "id": prior["id"], "carried_from_head_sha": source,
        "evidence_reason": "blob_unchanged",
    }]
    assert "GATE-CARRIED-FINDING id=sol-review-1 reason=blob_unchanged" in capsys.readouterr().out

    blocked, terminal_path, _summary = _run_gate(tmp_path, monkeypatch, audit_path, target)
    terminal = json.loads(terminal_path.read_bytes())
    assert blocked == 1
    assert (terminal["gate_result"], terminal["classification"], terminal["reason_code"]) == (
        "fail", "code_fail", "primary_findings",
    )
    assert terminal["finding_relation"]["carried_findings"] == audit["finding_relation"]["carried_findings"]
    assert LEDGER._validate_finding_relation(terminal["finding_relation"]) == terminal["finding_relation"]
    row = AGG._terminal_row(terminal, repository="zlxlabs/caller", repository_id=123, pr_number=42)
    panel = AGG.render_status_panel([row])
    assert "改代码，或对本轮该条签发 rejected。" in panel

    scope, missing = AGG._convergence_scope_from_audit(audit, AGG.Identity(123, target, 999, 1, 42))
    assert scope is not None and not missing
    receipt = AGG._CONVERGENCE.DispositionReceipt(
        schema_version=3, disposition="rejected", repository_id="123", pr_number=42,
        epoch=AGG._CONVERGENCE.derive_epoch(scope), head_sha=target,
        audit_digest=AGG._CONVERGENCE.canonical_audit_digest(audit),
        finding_id=prior["id"],
        finding_key=AGG._GATE_ACTION.canonical_finding_key(prior),
        reason="owner accepts the verifier rejection", approver="owner", approver_id=1,
        approved_at="2026-11-26T10:00:00Z", triggering_actor="owner",
        triggering_actor_source="cli",
    )
    released, released_path, _summary = _run_gate(
        tmp_path, monkeypatch, audit_path, target, receipts=(receipt,),
    )
    terminal = json.loads(released_path.read_bytes())
    assert released == 0
    assert (terminal["gate_result"], terminal["classification"], terminal["reason_code"]) == (
        "pass", "code_pass", "disposition_resolved",
    )
    assert terminal["disposition_receipt_consumption"]["consumed_count"] == 1
    assert terminal["disposition_receipt_consumption"]["remaining_p1_ids"] == []
    row = AGG._terminal_row(
        terminal, repository="zlxlabs/caller", repository_id=123, pr_number=42,
    )
    panel = AGG.render_status_panel([row])
    assert "上一轮未处置的 sol 问题（已并入本轮主审）" not in panel


def test_carry_keeps_an_existing_failure_reason(tmp_path, monkeypatch):
    repo, source, target = _two_commit_repo(tmp_path)
    audit_path = _annotate(tmp_path, monkeypatch, repo, source, target)
    exit_code, terminal_path, _summary = _run_gate(
        tmp_path, monkeypatch, audit_path, target, quality_result="failure",
    )
    terminal = json.loads(terminal_path.read_bytes())
    assert exit_code == 1
    assert terminal["finding_relation"]["carried_findings"]
    assert (terminal["gate_result"], terminal["classification"], terminal["reason_code"]) == (
        "fail", "ci_failure", "quality_failure",
    )


def test_changed_file_blob_releases_carry_without_terminal_field(tmp_path, monkeypatch):
    repo, source, target = _two_commit_repo(tmp_path, change_file=True)
    audit_path = _annotate(tmp_path, monkeypatch, repo, source, target)
    audit = json.loads(audit_path.read_bytes())
    assert audit["result"]["findings"] == []
    assert "carried_findings" not in audit["finding_relation"]
    exit_code, terminal_path, _summary = _run_gate(tmp_path, monkeypatch, audit_path, target)
    terminal = json.loads(terminal_path.read_bytes())
    assert exit_code == 0
    assert terminal["gate_result"] == "pass"
    assert "carry_blockers" not in terminal
    assert "carried_findings" not in terminal["finding_relation"]


def test_same_current_audit_key_is_not_added_as_duplicate_carry_finding(tmp_path, monkeypatch):
    repo, source, target = _two_commit_repo(tmp_path)
    current = _finding(id="current-review-id")
    audit_path = _annotate(tmp_path, monkeypatch, repo, source, target, current_findings=[current])
    audit = json.loads(audit_path.read_bytes())
    assert [finding["id"] for finding in audit["result"]["findings"]] == [current["id"]]
    assert "carried_findings" not in audit["finding_relation"]
    assert AGG._GATE_ACTION.canonical_finding_key(_finding()) == AGG._GATE_ACTION.canonical_finding_key(current)


def test_consumed_g2a_inherited_rejection_key_is_not_a_carry_blocker():
    finding = _finding()
    key = AGG._GATE_ACTION.canonical_finding_key(finding)
    audit = SimpleNamespace(statuses=(SimpleNamespace(
        receipt=SimpleNamespace(finding_key=key), active=True, valid=True,
        matched_by="inherited_rejected",
    ),))
    inherited = CARRY.inherited_rejection_keys(audit)
    assert inherited == {key}
    assert CARRY.candidates(
        open_findings=[finding], current_findings=[], source_head_sha="a" * 40,
        current_head_sha="b" * 40,
        canonical_finding_key=AGG._GATE_ACTION.canonical_finding_key,
        inherited_keys=inherited,
    ) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "reviewer"},
        {"id": "sol_verify_infra.fetch"},
        {"gate_action": "comment"},
    ],
    ids=["non-sol-origin", "infra-id", "comment-action"],
)
def test_non_sol_infra_and_comment_findings_never_carry(tmp_path, monkeypatch, changes):
    repo, source, target = _two_commit_repo(tmp_path)
    prior = _finding(**changes)
    audit_path = _annotate(tmp_path, monkeypatch, repo, source, target, prior=prior)
    audit = json.loads(audit_path.read_bytes())
    assert audit["result"]["findings"] == []
    assert "carried_findings" not in audit["finding_relation"]


def test_inherited_rejection_removed_from_open_context_is_not_carried(tmp_path, monkeypatch):
    repo, source, target = _two_commit_repo(tmp_path)
    audit_path = _annotate(tmp_path, monkeypatch, repo, source, target, open_findings=[])
    audit = json.loads(audit_path.read_bytes())
    assert audit["result"]["findings"] == []
    assert "carried_findings" not in audit["finding_relation"]


def test_blob_evidence_failure_carries_and_exposes_reason_in_primary_audit(tmp_path, monkeypatch, capsys):
    repo, source, target = _two_commit_repo(tmp_path)
    audit_path = _annotate(tmp_path, monkeypatch, repo, source, target, missing_blobs=True)
    audit = json.loads(audit_path.read_bytes())
    assert audit["result"]["findings"] == [_finding()]
    assert audit["finding_relation"]["carried_findings"][0]["evidence_reason"] == "blob_evidence_unavailable"
    assert "GATE-CARRIED-FINDING id=sol-review-1 reason=blob_evidence_unavailable" in capsys.readouterr().out
    exit_code, terminal_path, _summary = _run_gate(tmp_path, monkeypatch, audit_path, target)
    terminal = json.loads(terminal_path.read_bytes())
    assert exit_code == 1
    assert terminal["gate_result"] == "fail"
    assert terminal["finding_relation"]["carried_findings"][0]["evidence_reason"] == "blob_evidence_unavailable"


def test_no_carry_blocker_keeps_terminal_golden_shape():
    outcome = AGG.Outcome(
        ok=True, classification="code_pass", reason_code="primary_pass", gate_result="pass",
        audit_available=True, audit_source_attempt=1, audit_artifact_name="primary-audit-v2-1",
    )
    envelope = AGG.build_terminal_envelope(
        repository="zlxlabs/gate", identity=AGG.Identity(123, "a" * 40, 999, 1, 42),
        quality_result="success", primary_result="success", review_expected=True,
        is_draft=False, runner="self", outcome=outcome,
    )
    assert "carry_blockers" not in envelope
    serialized = json.dumps(envelope, ensure_ascii=False, indent=2) + "\n"
    assert '"disposition_receipt_consumption"' in serialized
    assert not serialized.endswith('"carry_blockers": []}\n')
