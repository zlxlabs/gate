"""Rejected receipt inheritance is bound to one exact finding key and blob.

The Git evidence fixtures use two real commits; no git command is mocked.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE = ROOT / ".github/actions/gate-aggregator/aggregate.py"
LEDGER = ROOT / ".github/actions/review-ledger/build_ledger.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AGG = _load("gate_aggregate_inherited_rejection_tests", AGGREGATE)
CONV = AGG._CONVERGENCE
INHERITED = AGG._INHERITED_REJECTION
DIGEST_OLD = "a" * 64
DIGEST_CURRENT = "b" * 64


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _git_pair(tmp_path: Path, *, current_contents: bytes | None = None):
    repo = tmp_path / "git-evidence"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    (repo / "src").mkdir()
    path = "src/sol.py"
    old_contents = b"def check():\n    return True\n"
    (repo / path).write_bytes(old_contents)
    _run_git(repo, "add", path)
    _run_git(repo, "commit", "--quiet", "-m", "receipt head")
    old_head = _run_git(repo, "rev-parse", "HEAD")
    if current_contents is not None:
        (repo / path).write_bytes(current_contents)
        _run_git(repo, "add", path)
    else:
        (repo / "unrelated.txt").write_text("second commit\n", encoding="utf-8")
        _run_git(repo, "add", "unrelated.txt")
    _run_git(repo, "commit", "--quiet", "-m", "current head")
    current_head = _run_git(repo, "rev-parse", "HEAD")
    return repo, path, old_head, current_head


def _finding(
    *, finding_id="current-sol-id", file="src/sol.py", line=2,
    category="correctness", severity="major", origin="sol_verify",
    gate_action="must_respond", issue="problem A", acceptance="fix A",
):
    return {
        "id": finding_id,
        "file": file,
        "line": line,
        "category": category,
        "severity": severity,
        "origin": origin,
        "gate_action": gate_action,
        "issue": issue,
        "acceptance": acceptance,
        "trigger_kind": "inferred",
    }


def _scope(head_sha: str):
    return CONV.Scope(
        repository_id=123,
        pr_number=42,
        base_sha="c" * 40,
        head_sha=head_sha,
        diff_digest="d" * 64,
        policy_version="policy-v1",
        policy_digest="e" * 64,
        tier="personal",
        caller_sha="f" * 40,
        reusable_workflow_sha="1" * 40,
    )


def _receipt(old_head: str, finding: dict, *, disposition="rejected"):
    old_finding = {**finding, "id": "receipt-sol-id"}
    old_scope = _scope(old_head)
    changes = {}
    if disposition == "false-positive":
        argv = ["git", "grep", "-n", "-F", "-e", "locked-behavior", old_head, "--", finding["file"]]
        excerpt = f"{finding['file']}:1:locked-behavior"
        changes["counterevidence"] = {
            "command": f"git grep -n -F -e locked-behavior {old_head} -- {finding['file']}",
            "output": "one verified match",
            "result": "refuted",
            "pointer": "tests/test_sol.py::test_rule",
            "gate_rerun": {
                "argv": argv,
                "exit_code": 0,
                "match_count": 1,
                "stdout_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                "excerpt": excerpt,
            },
        }
    return CONV.DispositionReceipt(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition=disposition,
        repository_id="123",
        pr_number=42,
        epoch=CONV.derive_epoch(old_scope),
        head_sha=old_head,
        audit_digest=DIGEST_OLD,
        finding_id=old_finding["id"],
        finding_key=AGG._GATE_ACTION.canonical_finding_key(old_finding),
        reason="owner rejected the finding after verifier review",
        approver="owner",
        approver_id=7,
        approved_at="2026-11-26T06:00:00Z",
        **changes,
    )


def _matching_map(receipt, current_findings, repo, current_head):
    candidates = INHERITED.receipt_blob_candidates(
        receipts=(receipt,),
        findings=current_findings,
        current_head_sha=current_head,
        canonical_finding_key=AGG._GATE_ACTION.canonical_finding_key,
        is_rejected_eligible=AGG._CARRY._rejected_releases,
    )
    blob_oids = {}
    for sha, path in INHERITED.blob_fetch_requests(candidates):
        blob_oids[(sha, path)] = _run_git(repo, "rev-parse", "--verify", f"{sha}:{path}")
    return INHERITED.matching_finding_ids(candidates, blob_oids)


def _validate(receipt, current_findings, current_head, inherited_matches):
    scope = _scope(current_head)
    finding = current_findings[0]
    primary = CONV.CanonicalPrimary(
        schema_version=1,
        repository_id=scope.repository_id,
        pr_number=scope.pr_number,
        head_sha=current_head,
        run_id=18,
        run_attempt=1,
        verdict="pass",
        p1_ids=tuple(item["id"] for item in current_findings),
        p1_findings=tuple(
            (item["id"], item["severity"], item["trigger_kind"], item["file"], item["line"], item["category"])
            for item in current_findings
        ),
    )
    return CONV.validate_disposition_receipt(
        receipt,
        scope=scope,
        primary=primary,
        audit_digest=DIGEST_CURRENT,
        gate_actions={item["id"]: item["gate_action"] for item in current_findings},
        inherited_rejection_matches=inherited_matches,
    )


def test_rejected_receipt_inherits_same_blob_and_key_with_new_finding_id(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    current = _finding()
    receipt = _receipt(old_head, current)
    matches = _matching_map(receipt, [current], repo, current_head)

    status = _validate(receipt, [current], current_head, matches)

    assert (status.valid, status.active, status.matched_by) == (True, True, "inherited_rejected")
    assert status.matched_finding_id == current["id"]
    assert status.reason_code == "active_rejected"
    assert AGG.relate_findings([current], [_finding(finding_id="receipt-sol-id")])[0]["relation_to_previous"] == "repeat"

    audit = {
        "kind": "primary_review", "schema_version": 1, "repository_id": 123,
        "head_sha": current_head, "run_id": 18, "run_attempt": 1, "pr": 42,
        "verdict": "pass", "reviewer": "test-reviewer",
        "base_sha": "c" * 40, "diff_digest": "d" * 64,
        "policy_version": "policy-v1", "policy_digest": "e" * 64,
        "tier": "personal", "caller_sha": "f" * 40,
        "reusable_workflow_sha": "1" * 40,
        "blocking_matrix": {"version": 1, "tier": "personal"},
        "result": {"findings": [current]},
    }
    identity = AGG.Identity(123, current_head, 18, 1, 42)
    outcome = AGG.evaluate(
        quality_result="success", primary_result="success", preflight_result="success", runner="self",
        is_draft=False, is_fork=False, review_expected=True,
        classify_review_expected="true", audit=audit, audit_error=None,
        identity=identity, pr_author="owner", scope=_scope(current_head),
        audit_source_attempt=1, audit_artifact_name="primary-audit-v2-test",
        audit_digest=CONV.canonical_audit_digest(audit),
        waiver_receipts=(receipt,), repository="zlxlabs/gate",
        inherited_rejection_matches=matches,
    )
    assert outcome.gate_result == "pass"
    assert outcome.disposition_audit.remaining_p1_ids == ()


def test_rejected_does_not_inherit_when_file_blob_differs(tmp_path):
    repo, path, old_head, current_head = _git_pair(
        tmp_path, current_contents=b"def check():\n    return False\n",
    )
    current = _finding()
    receipt = _receipt(old_head, current)
    matches = _matching_map(receipt, [current], repo, current_head)

    assert matches == {}
    assert _validate(receipt, [current], current_head, matches).reason_code == "head_sha_mismatch"


@pytest.mark.parametrize(
    "field,value",
    [("line", 3), ("category", "security"), ("severity", "minor")],
)
def test_rejected_does_not_inherit_when_any_finding_key_component_differs(tmp_path, field, value):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    old_shape = _finding()
    receipt = _receipt(old_head, old_shape)
    current = {**old_shape, field: value}
    matches = _matching_map(receipt, [current], repo, current_head)

    assert matches == {}
    assert _validate(receipt, [current], current_head, matches).reason_code == "head_sha_mismatch"


def test_rejected_does_not_inherit_for_non_sol_verify_current_finding(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    receipt = _receipt(old_head, _finding())
    current = _finding(origin="primary")
    matches = _matching_map(receipt, [current], repo, current_head)

    assert matches == {}
    assert _validate(receipt, [current], current_head, matches).reason_code == "head_sha_mismatch"


def test_rejected_does_not_inherit_for_owner_protected_current_category(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    current = _finding(category="security")
    receipt = _receipt(old_head, current)
    matches = _matching_map(receipt, [current], repo, current_head)

    assert matches == {}
    assert _validate(receipt, [current], current_head, matches).reason_code == "head_sha_mismatch"


def test_rejected_does_not_inherit_for_sol_verify_infrastructure_id(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    current = _finding(finding_id="sol_verify_infra.network")
    receipt = _receipt(old_head, current)
    matches = _matching_map(receipt, [current], repo, current_head)

    assert matches == {}
    assert _validate(receipt, [current], current_head, matches).reason_code == "head_sha_mismatch"


def test_rejected_does_not_inherit_when_same_key_has_two_current_findings(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    current = _finding()
    receipt = _receipt(old_head, current)
    duplicate = {**current, "id": "second-current-id"}
    matches = _matching_map(receipt, [current, duplicate], repo, current_head)

    assert matches == {}
    assert _validate(receipt, [current, duplicate], current_head, matches).reason_code == "head_sha_mismatch"


def test_false_positive_still_uses_original_head_gate_not_inherited_rejection(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    current = _finding()
    receipt = _receipt(old_head, current, disposition="false-positive")
    rejected_only = _matching_map(receipt, [current], repo, current_head)

    assert rejected_only == {}
    assert _validate(receipt, [current], current_head, rejected_only).reason_code == "head_sha_mismatch"


def test_missing_blob_evidence_never_counts_as_same_blob(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    current = _finding()
    receipt = _receipt(old_head, current)
    candidates = INHERITED.receipt_blob_candidates(
        receipts=(receipt,), findings=[current], current_head_sha=current_head,
        canonical_finding_key=AGG._GATE_ACTION.canonical_finding_key,
        is_rejected_eligible=AGG._CARRY._rejected_releases,
    )

    assert candidates
    assert INHERITED.matching_finding_ids(candidates, {}) == {}
    assert _validate(receipt, [current], current_head, {}).reason_code == "head_sha_mismatch"


def test_same_location_different_sol_problem_inherits_by_owner_ruling(tmp_path):
    """Known Astra cost: new prose at the same four-tuple still inherits."""
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    previous = _finding(finding_id="old-astra-id", issue="old issue", acceptance="old acceptance")
    current = _finding(finding_id="new-astra-id", issue="different issue", acceptance="different acceptance")
    receipt = _receipt(old_head, previous)
    matches = _matching_map(receipt, [current], repo, current_head)

    status = _validate(receipt, [current], current_head, matches)

    assert status.matched_by == "inherited_rejected"
    assert status.matched_finding_id == "new-astra-id"
    relation = AGG.relate_findings([current], [previous])
    assert relation[0]["relation_to_previous"] == "repeat"
    assert relation[0]["previous_finding_id"] == "old-astra-id"


def test_primary_relation_uses_released_open_findings_not_raw_previous_findings(tmp_path):
    previous_finding = _finding(
        finding_id="old-primary-id", origin="primary",
        issue="old requirement", acceptance="old acceptance",
    )
    current = _finding(
        finding_id="current-primary-id", origin="primary",
        issue="new requirement", acceptance="new acceptance",
    )
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps({
        "available": True, "head_sha": "a" * 40,
        "findings": [previous_finding], "open_findings": [],
    }), encoding="utf-8")
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps({
        "verdict": "pass",
        "result": {"verdict": "pass", "findings": [current]},
    }), encoding="utf-8")

    assert AGG._annotate_primary_audit_cli([
        "--audit-path", str(audit_path), "--previous-findings-path", str(previous_path),
    ]) == 0
    relation = json.loads(audit_path.read_text(encoding="utf-8"))["finding_relation"]

    assert relation["counts"]["conflict"] == 0
    assert relation["items"][0]["relation_to_previous"] == "new"


def test_terminal_producer_bytes_preserve_inherited_fields_in_ledger_process(tmp_path):
    repo, _path, old_head, current_head = _git_pair(tmp_path)
    current = _finding()
    receipt = _receipt(old_head, current)
    matches = _matching_map(receipt, [current], repo, current_head)
    scope = _scope(current_head)
    primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=123, pr_number=42, head_sha=current_head,
        run_id=18, run_attempt=1, verdict="pass", p1_ids=(current["id"],),
        p1_findings=((current["id"], "major", "inferred", current["file"], current["line"], current["category"]),),
    )
    disposition_audit = CONV.record_dispositions(
        primary.p1_ids, (receipt,), scope=scope, primary=primary,
        audit_digest=DIGEST_CURRENT, gate_actions={current["id"]: "must_respond"},
        inherited_rejection_matches=matches,
    )
    producer_payload = AGG.project_disposition_receipt_audit(disposition_audit)
    terminal = {
        "schema_version": 1, "kind": "gate_terminal", "repository": "zlxlabs/gate",
        "pr_number": 42, "run_id": 18, "run_attempt": 1, "head_sha": current_head,
        "disposition_receipt_consumption": producer_payload,
    }
    producer_bytes = json.dumps(terminal, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    consumer_code = (
        "import importlib.util,json,sys; from pathlib import Path; "
        f"p=Path({str(LEDGER)!r}); s=importlib.util.spec_from_file_location('ledger_consumer',p); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "e=json.load(sys.stdin); "
        "v=m._disposition_receipt_consumption_from_terminal(e,repository='zlxlabs/gate',"
        "pr_number=42,run_id=18,run_attempt=1,head_sha=e['head_sha']); "
        "print(json.dumps(v,sort_keys=True,separators=(',',':')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", consumer_code], input=producer_bytes,
        capture_output=True, check=True,
    )
    ledger_projection = json.loads(result.stdout)
    resolved = ledger_projection["resolved"][0]
    assert resolved["matched_by"] == "inherited_rejected"
    assert resolved["receipt_head_sha"] == old_head
    assert resolved["receipt_finding_id"] == "receipt-sol-id"
    assert resolved["finding_id"] == current["id"]
