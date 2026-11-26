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
        "category": "correctness",
        "title": "检查边界条件",
        "issue": "边界条件未处理",
    }
    finding.update(changes)
    return finding


def _audit(*, target: str, source: str, prior: dict, current_findings: list[dict] | None = None) -> dict:
    return {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": 123,
        "head_sha": target,
        "run_id": 999,
        "run_attempt": 1,
        "pr": 42,
        "verdict": "pass",
        "reviewer": "fixture",
        "result": {"findings": current_findings or []},
        "finding_relation": {
            "schema_version": 1,
            "counts": {"new": 0, "repeat": 0, "conflict": 0},
            "review_terminal": "unchanged",
            "items": [],
            "open_findings": [prior],
            "history_status": "available",
            "previous_head_sha": source,
        },
    }


def _run_gate(
    tmp_path, monkeypatch, repo, source, target, *, current_findings=None,
    missing_blobs=False, quality_result="success",
):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    prior = _finding()
    (audit_dir / "primary-review-audit.json").write_text(
        json.dumps(_audit(target=target, source=source, prior=prior, current_findings=current_findings)),
        encoding="utf-8",
    )
    terminal = tmp_path / "gate-terminal.json"
    summary = tmp_path / "summary.md"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def collect_blobs(*, head_paths, **_kwargs):
        if missing_blobs:
            return {}
        return {
            (sha, path): _git("rev-parse", "--verify", f"{sha}:{path}", cwd=repo)
            for sha, path in head_paths
        }

    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setattr(AGG._NEIGHBOR_EVIDENCE, "collect_file_blob_oids", collect_blobs)
    args = [
        "--quality-result", quality_result,
        "--caller-checks", "failed" if quality_result == "failure" else "passed",
        "--preflight-result", "success", "--primary-result", "success",
        "--runner", "self", "--is-draft", "false", "--is-fork", "false",
        "--pr-author-json", '"owner"', "--review-expected", "true",
        "--repository-id", "123", "--repository", "zlxlabs/caller",
        "--head-sha", target, "--run-id", "999", "--run-attempt", "1",
        "--pr-number", "42", "--audit-source-attempt", "1",
        "--audit-artifact-name", "primary-audit-v2-fixture",
        "--audit-dir", str(audit_dir), "--terminal-path", str(terminal),
        "--summary-path", str(summary),
    ]
    exit_code = AGG.main(args)
    return exit_code, terminal, summary


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


def test_unreleased_sol_finding_with_same_blob_fails_and_cross_process_ledger_keeps_payload(
    tmp_path, monkeypatch, capsys,
):
    repo, source, target = _two_commit_repo(tmp_path)
    exit_code, terminal_path, _summary = _run_gate(tmp_path, monkeypatch, repo, source, target)
    assert exit_code == 1
    raw_terminal = terminal_path.read_bytes()
    terminal = json.loads(raw_terminal)
    assert terminal["gate_result"] == "fail"
    assert terminal["carry_blockers"] == [{
        "id": "sol-review-1",
        "finding_key": AGG._GATE_ACTION.canonical_finding_key(_finding()),
        "source_head_sha": source,
        "file": "src/module.py",
        "line": 1,
        "title": "检查边界条件",
        "evidence_reason": "blob_unchanged",
    }]
    row = AGG._terminal_row(
        terminal, repository="zlxlabs/caller", repository_id=123, pr_number=42,
    )
    panel = AGG.render_status_panel([row])
    assert "上一轮未处置的 sol 问题（文件未变，继续拦截）" in panel
    assert "sol-review-1" in panel and "src/module.py:1" in panel
    assert "检查边界条件" in panel
    assert "改代码，或对上一轮 audit 的该条签发 rejected 后在当前 head 重跑 gate" in panel
    assert "GATE-CARRY-BLOCKER id=sol-review-1 reason=blob_unchanged" in capsys.readouterr().out

    # Feed the producer's actual terminal artifact bytes through the separate ledger process.
    inputs = {}
    for name in ("audit.json", "preflight.json", "install.json"):
        path = tmp_path / name
        path.write_text("{}\n", encoding="utf-8")
        inputs[name] = path
    ledger_path = tmp_path / "ledger.jsonl"
    command = [
        sys.executable, str(LEDGER_PATH),
        "--audit-path", str(inputs["audit.json"]),
        "--preflight-path", str(inputs["preflight.json"]),
        "--install-path", str(inputs["install.json"]),
        "--terminal-path", str(terminal_path), "--output", str(ledger_path),
        "--repository", "zlxlabs/caller", "--pr-number", "42",
        "--run-id", "999", "--run-attempt", "1", "--head-sha", target,
        "--expected-repository-id", "123", "--expected-base-sha", "b" * 40,
        "--expected-caller-sha", "c" * 40, "--expected-reusable-workflow-sha", "d" * 40,
    ]
    proc = subprocess.run(command, capture_output=True, text=True, check=True)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
    assert ledger["carry_blockers"] == [{
        "id": "sol-review-1",
        "finding_key": AGG._GATE_ACTION.canonical_finding_key(_finding()),
        "source_head_sha": source,
    }]
    assert json.loads(proc.stdout)["carry_blockers"] == ledger["carry_blockers"]


def test_carry_blocker_preserves_an_existing_failure_reason(tmp_path, monkeypatch):
    repo, source, target = _two_commit_repo(tmp_path)
    exit_code, terminal_path, _summary = _run_gate(
        tmp_path, monkeypatch, repo, source, target, quality_result="failure",
    )
    terminal = json.loads(terminal_path.read_bytes())
    assert exit_code == 1
    assert terminal["carry_blockers"]
    assert (terminal["gate_result"], terminal["classification"], terminal["reason_code"]) == (
        "fail", "ci_failure", "quality_failure",
    )


def test_changed_file_blob_releases_carry_without_terminal_field(tmp_path, monkeypatch):
    repo, source, target = _two_commit_repo(tmp_path, change_file=True)
    exit_code, terminal_path, _summary = _run_gate(tmp_path, monkeypatch, repo, source, target)
    terminal = json.loads(terminal_path.read_bytes())
    assert exit_code == 0
    assert terminal["gate_result"] == "pass"
    assert "carry_blockers" not in terminal


def test_same_current_audit_key_is_not_added_as_duplicate_carry_blocker():
    finding = _finding()
    key = AGG._GATE_ACTION.canonical_finding_key(finding)
    selected = CARRY.candidates(
        open_findings=[finding], current_findings=[_finding(id="current-review-id")],
        source_head_sha="a" * 40, current_head_sha="b" * 40,
        canonical_finding_key=AGG._GATE_ACTION.canonical_finding_key,
    )
    assert selected == []
    assert key == AGG._GATE_ACTION.canonical_finding_key(_finding(id="current-review-id"))


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
def test_non_sol_infra_and_comment_findings_never_carry(changes):
    finding = _finding(**changes)
    assert CARRY.candidates(
        open_findings=[finding], current_findings=[], source_head_sha="a" * 40,
        current_head_sha="b" * 40,
        canonical_finding_key=AGG._GATE_ACTION.canonical_finding_key,
    ) == []


def test_blob_evidence_failure_fails_closed_and_reason_is_visible(tmp_path, monkeypatch, capsys):
    repo, source, target = _two_commit_repo(tmp_path)
    exit_code, terminal_path, _summary = _run_gate(
        tmp_path, monkeypatch, repo, source, target, missing_blobs=True,
    )
    terminal = json.loads(terminal_path.read_bytes())
    assert exit_code == 1
    assert terminal["gate_result"] == "fail"
    assert terminal["carry_blockers"][0]["evidence_reason"] == "blob_evidence_unavailable"
    row = AGG._terminal_row(
        terminal, repository="zlxlabs/caller", repository_id=123, pr_number=42,
    )
    assert "blob_evidence_unavailable" in AGG.render_status_panel([row])
    assert "GATE-CARRY-BLOCKER id=sol-review-1 reason=blob_evidence_unavailable" in capsys.readouterr().out


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
