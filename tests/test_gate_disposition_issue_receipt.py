import hashlib
import importlib.util
import json
import os
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


def _git_env():
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "gate-test",
        "GIT_AUTHOR_EMAIL": "gate-test@example.com",
        "GIT_COMMITTER_NAME": "gate-test",
        "GIT_COMMITTER_EMAIL": "gate-test@example.com",
    })
    return env


def _seed_caller_repo(root, *, relpath="src/lock.py", literal="locked-behavior"):
    repo = Path(root) / "caller"
    (repo / relpath).parent.mkdir(parents=True, exist_ok=True)
    (repo / relpath).write_text(f"{literal}\n", encoding="utf-8")
    env = _git_env()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "gate-test@example.com"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "gate-test"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True, env=env)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, env=env).strip()
    return repo, sha, relpath, literal


def _allowlisted_ce(sha, *, relpath="src/lock.py", literal="locked-behavior", output="1 passed"):
    return {
        "command": f"git grep -n -F -e {literal} {sha} -- {relpath}",
        "output": output,
        "result": "refuted",
        "pointer": relpath,
    }


def _audit(tier="personal", *, trigger_kind="inferred", head_sha=None):
    audit = {
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
    if head_sha is not None:
        audit["head_sha"] = head_sha
    return audit


def _issue(tmp_path, *, disposition=None, counterevidence=None, tracking_issue=None,
           tier="personal", trigger_kind="inferred", head_sha=None, repo_dir=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    head_sha = head_sha or SCOPE["head_sha"]
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(_audit(tier, trigger_kind=trigger_kind, head_sha=head_sha)))
    output_dir = tmp_path / "output"
    scope = {**SCOPE, "tier": tier, "head_sha": head_sha}
    command = [
        sys.executable, str(PRODUCER), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository", REPOSITORY,
        "--repository-id", "123", "--pr-number", "42",
        "--head-sha", head_sha, "--finding-id", "finding-one",
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
    if repo_dir is not None:
        command.extend(("--repo-dir", str(repo_dir)))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result, output_dir


def test_false_positive_producer_writes_v3_counterevidence_payload(tmp_path):
    repo, sha, relpath, literal = _seed_caller_repo(tmp_path)
    counterevidence = _allowlisted_ce(sha, relpath=relpath, literal=literal)
    result, output_dir = _issue(
        tmp_path / "issue", disposition="false-positive",
        counterevidence=counterevidence, head_sha=sha, repo_dir=repo,
    )

    assert result.returncode == 0, result.stderr
    artifact = json.loads(result.stdout)["artifact"]
    payload_bytes = (output_dir / artifact).read_bytes()
    payload = json.loads(payload_bytes)
    assert payload_bytes == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert payload["kind"] == "gate-disposition-receipt-v3"
    assert payload["schema_version"] == 3
    assert payload["disposition"] == "false-positive"
    submitted = {key: value for key, value in payload["counterevidence"].items() if key != "gate_rerun"}
    assert submitted == counterevidence
    rerun = payload["counterevidence"]["gate_rerun"]
    assert rerun["exit_code"] == 0
    assert rerun["match_count"] >= 1
    assert rerun["argv"] == ["git", "grep", "-n", "-F", "-e", literal, sha, "--", relpath]
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
def test_deferred_producer_reports_tier_rejection_for_valid_same_repository_reference(
    tmp_path, tracking_issue,
):
    result, output_dir = _issue(tmp_path, disposition="deferred", tracking_issue=tracking_issue)

    assert result.returncode != 0
    assert "deferred_not_allowed_for_tier" in result.stderr
    assert "tracking_issue_" not in result.stderr
    assert not output_dir.exists()


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


@pytest.mark.parametrize("tier", ["personal", "internal", "saas"])
def test_deferred_producer_rejected_at_every_tier(tmp_path, tier):
    result, output_dir = _issue(tmp_path / tier, disposition="deferred", tracking_issue="#12", tier=tier)

    assert result.returncode != 0
    assert "deferred_not_allowed_for_tier" in result.stderr
    assert not output_dir.exists()


def test_false_positive_producer_rejects_measured_p1(tmp_path):
    result, output_dir = _issue(
        tmp_path, disposition="false-positive", counterevidence=COUNTEREVIDENCE,
        trigger_kind="measured",
    )

    assert result.returncode != 0
    assert "finding_id must identify an inferred P1 finding" in result.stderr
    assert not output_dir.exists()


def test_legacy_five_input_producer_call_fails_closed_without_evidence(tmp_path):
    result, output_dir = _issue(tmp_path)

    assert result.returncode != 0
    assert "counterevidence_required" in result.stderr
    assert not output_dir.exists()


# gate#240: same file, line null, same category/severity — the four-field
# stable key cannot tell these P1s apart, so the exact finding_id must.
SAME_KEY_IDS = (
    "correctness-incomplete-producer-discovery",
    "correctness-dynamic-producer-declaration-gap",
)
SAME_KEY_FINDING = {
    "severity": "major",
    "trigger_kind": "inferred",
    "file": "src/producers.py",
    "line": None,
    "category": "correctness",
}


def _same_key_audit(head_sha=None):
    audit = _audit(head_sha=head_sha)
    audit["result"] = {
        "findings": [{"id": finding_id, **SAME_KEY_FINDING} for finding_id in SAME_KEY_IDS]
    }
    return audit


def _issue_same_key(tmp_path, *, finding_id, disposition="false-positive",
                    counterevidence=COUNTEREVIDENCE, head_sha=None, repo_dir=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    head_sha = head_sha or SCOPE["head_sha"]
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(_same_key_audit(head_sha=head_sha)))
    output_dir = tmp_path / "output"
    scope = {**SCOPE, "head_sha": head_sha}
    command = [
        sys.executable, str(PRODUCER), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository", REPOSITORY,
        "--repository-id", "123", "--pr-number", "42",
        "--head-sha", head_sha, "--finding-id", finding_id,
        "--reason", "canonical evidence reviewed", "--scope-json", json.dumps(scope),
        "--approver", "owner", "--approver-id", "10",
        "--approved-at", "2026-09-25T09:00:00Z", "--triggering-actor", "owner",
        "--disposition", disposition,
    ]
    if counterevidence is not None:
        command.extend(("--counterevidence-json", json.dumps(counterevidence)))
    if repo_dir is not None:
        command.extend(("--repo-dir", str(repo_dir)))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result, output_dir


def test_same_key_null_line_p1s_issue_distinct_false_positive_receipts_per_finding_id(tmp_path):
    repo, sha, relpath, literal = _seed_caller_repo(tmp_path)
    counterevidence = _allowlisted_ce(sha, relpath=relpath, literal=literal)
    first, first_dir = _issue_same_key(
        tmp_path / "first", finding_id=SAME_KEY_IDS[0],
        counterevidence=counterevidence, head_sha=sha, repo_dir=repo,
    )
    second, second_dir = _issue_same_key(
        tmp_path / "second", finding_id=SAME_KEY_IDS[1],
        counterevidence=counterevidence, head_sha=sha, repo_dir=repo,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_artifact = json.loads(first.stdout)["artifact"]
    second_artifact = json.loads(second.stdout)["artifact"]
    assert first_artifact != second_artifact
    first_payload = json.loads((first_dir / first_artifact).read_bytes())
    second_payload = json.loads((second_dir / second_artifact).read_bytes())
    assert first_payload["finding_id"] == SAME_KEY_IDS[0]
    assert second_payload["finding_id"] == SAME_KEY_IDS[1]
    assert first_payload["finding_key"] == second_payload["finding_key"]


def test_same_key_audit_still_rejects_unknown_finding_id(tmp_path):
    result, output_dir = _issue_same_key(tmp_path, finding_id="no-such-finding")

    assert result.returncode != 0
    assert "finding_id must identify exactly one canonical audit finding" in result.stderr
    assert not output_dir.exists()


def _convergence_module():
    path = ROOT / ".github" / "actions" / "gate-aggregator" / "convergence.py"
    spec = importlib.util.spec_from_file_location("gate_convergence_issue_receipt_tests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_producer_receipt_bytes_consume_against_same_key_primary(tmp_path):
    convergence = _convergence_module()
    repo, sha, relpath, literal = _seed_caller_repo(tmp_path)
    counterevidence = _allowlisted_ce(sha, relpath=relpath, literal=literal)
    receipts = []
    for finding_id in SAME_KEY_IDS:
        result, output_dir = _issue_same_key(
            tmp_path / finding_id, finding_id=finding_id,
            counterevidence=counterevidence, head_sha=sha, repo_dir=repo,
        )
        assert result.returncode == 0, result.stderr
        artifact = json.loads(result.stdout)["artifact"]
        receipts.append(
            convergence.parse_disposition_receipt(json.loads((output_dir / artifact).read_bytes()))
        )

    scope_fields = {**SCOPE, "head_sha": sha}
    scope = convergence.Scope(**scope_fields)
    primary = convergence.CanonicalPrimary(
        schema_version=1,
        repository_id=SCOPE["repository_id"],
        pr_number=SCOPE["pr_number"],
        head_sha=sha,
        run_id=7,
        run_attempt=1,
        verdict="fail",
        p1_ids=SAME_KEY_IDS,
        p1_findings=tuple(
            (finding_id, SAME_KEY_FINDING["severity"], SAME_KEY_FINDING["trigger_kind"],
             SAME_KEY_FINDING["file"], SAME_KEY_FINDING["line"], SAME_KEY_FINDING["category"])
            for finding_id in SAME_KEY_IDS
        ),
    )
    audit_digest = convergence.canonical_audit_digest(_same_key_audit(head_sha=sha))

    statuses = tuple(
        convergence.validate_disposition_receipt(
            receipt, scope=scope, primary=primary, audit_digest=audit_digest,
        )
        for receipt in receipts
    )
    assert [(status.valid, status.active) for status in statuses] == [(True, True), (True, True)]
    assert [status.reason_code for status in statuses] == [
        "active_false_positive", "active_false_positive",
    ]

    full = convergence.record_dispositions(
        primary.p1_ids, tuple(receipts), scope=scope, primary=primary, audit_digest=audit_digest,
    )
    assert full.recorded_finding_ids == SAME_KEY_IDS
    assert full.remaining_p1_ids == ()

    partial = convergence.record_dispositions(
        primary.p1_ids, (receipts[0],), scope=scope, primary=primary, audit_digest=audit_digest,
    )
    assert partial.recorded_finding_ids == (SAME_KEY_IDS[0],)
    assert partial.remaining_p1_ids == (SAME_KEY_IDS[1],)


def _producer_module():
    spec = importlib.util.spec_from_file_location("issue_receipt_under_test", PRODUCER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(
            f"git grep -n -F -e locked-behavior {'b' * 40} -- src/lock.py",
            id="sha_not_equal_head_sha",
        ),
        pytest.param(
            f"git grep -n -F -e locked-behavior abc1234 -- src/lock.py",
            id="short_sha",
        ),
        pytest.param(
            f"git grep -n -F -e locked-behavior {SCOPE['head_sha']} -- src/lock.py extra",
            id="extra_argument",
        ),
        pytest.param(
            f"git grep -n -F -e locked-behavior {SCOPE['head_sha']}",
            id="missing_pathspec",
        ),
        pytest.param(
            f"git grep -n -P -e locked-behavior {SCOPE['head_sha']} -- src/lock.py",
            id="other_flag_perl",
        ),
        pytest.param(
            f"git grep -n -E -e locked-behavior {SCOPE['head_sha']} -- src/lock.py",
            id="other_flag_extended",
        ),
        pytest.param(
            f"git grep -n -F -e -n {SCOPE['head_sha']} -- src/lock.py",
            id="literal_starting_with_dash",
        ),
        pytest.param(
            f"git grep -n -F -e locked-behavior {SCOPE['head_sha']} -- src/../lock.py",
            id="pathspec_contains_dotdot",
        ),
        pytest.param(
            f"git grep -n -F -e locked-behavior {SCOPE['head_sha']} -- /abs/lock.py",
            id="pathspec_absolute",
        ),
        pytest.param(
            f"git grep -n -F -e locked-behavior {SCOPE['head_sha']} -- :(literal)src/lock.py",
            id="pathspec_magic_colon",
        ),
    ],
)
def test_false_positive_producer_rejects_command_not_allowlisted(tmp_path, command):
    counterevidence = {**COUNTEREVIDENCE, "command": command}
    result, output_dir = _issue(
        tmp_path, disposition="false-positive", counterevidence=counterevidence,
    )
    assert result.returncode != 0
    assert "counterevidence_command_not_allowlisted" in result.stderr
    assert not output_dir.exists()


def test_false_positive_producer_rejects_rerun_no_match(tmp_path):
    repo, sha, relpath, _literal = _seed_caller_repo(tmp_path, literal="present-text")
    counterevidence = _allowlisted_ce(sha, relpath=relpath, literal="absent-literal")
    result, output_dir = _issue(
        tmp_path / "issue", disposition="false-positive",
        counterevidence=counterevidence, head_sha=sha, repo_dir=repo,
    )
    assert result.returncode != 0
    assert "counterevidence_rerun_no_match" in result.stderr
    assert not output_dir.exists()


def test_false_positive_producer_rejects_forged_output_when_rerun_no_match(tmp_path):
    repo, sha, relpath, _literal = _seed_caller_repo(tmp_path, literal="present-text")
    counterevidence = _allowlisted_ce(
        sha, relpath=relpath, literal="forged-absent-literal",
        output=f"{relpath}:1:forged-absent-literal",
    )
    result, output_dir = _issue(
        tmp_path / "issue", disposition="false-positive",
        counterevidence=counterevidence, head_sha=sha, repo_dir=repo,
    )
    assert result.returncode != 0
    assert "counterevidence_rerun_no_match" in result.stderr
    assert not output_dir.exists()


def test_false_positive_producer_rejects_rerun_failed_nonzero_exit(tmp_path):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    sha = "a" * 40
    counterevidence = _allowlisted_ce(sha)
    result, output_dir = _issue(
        tmp_path / "issue", disposition="false-positive",
        counterevidence=counterevidence, head_sha=sha, repo_dir=empty,
    )
    assert result.returncode != 0
    assert "counterevidence_rerun_failed" in result.stderr
    assert not output_dir.exists()


def test_rerun_timeout_is_counterevidence_rerun_failed(tmp_path, monkeypatch):
    repo, sha, relpath, literal = _seed_caller_repo(tmp_path)
    module = _producer_module()

    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=30)

    monkeypatch.setattr(module.subprocess, "run", boom)
    with pytest.raises(ValueError, match="counterevidence_rerun_failed"):
        module.rerun_counterevidence(
            f"git grep -n -F -e {literal} {sha} -- {relpath}", sha, repo,
        )


def test_false_positive_producer_writes_gate_rerun_from_real_git_grep(tmp_path):
    repo, sha, relpath, literal = _seed_caller_repo(tmp_path)
    forged = "forged-output-that-must-not-be-trusted"
    counterevidence = _allowlisted_ce(sha, relpath=relpath, literal=literal, output=forged)
    result, output_dir = _issue(
        tmp_path / "issue", disposition="false-positive",
        counterevidence=counterevidence, head_sha=sha, repo_dir=repo,
    )
    assert result.returncode == 0, result.stderr
    artifact = json.loads(result.stdout)["artifact"]
    payload_bytes = (output_dir / artifact).read_bytes()
    payload = json.loads(payload_bytes)
    rerun = payload["counterevidence"]["gate_rerun"]
    actual = subprocess.run(
        rerun["argv"], cwd=repo, capture_output=True, check=True,
    )
    assert rerun["argv"] == ["git", "grep", "-n", "-F", "-e", literal, sha, "--", relpath]
    assert rerun["exit_code"] == 0
    assert rerun["match_count"] >= 1
    assert rerun["stdout_sha256"] == hashlib.sha256(actual.stdout).hexdigest()
    assert actual.stdout.decode("utf-8")[:2000] == rerun["excerpt"]
    assert literal in rerun["excerpt"]
    assert payload["counterevidence"]["output"] == forged
    convergence = _convergence_module()
    receipt = convergence.parse_disposition_receipt(payload)
    scope = convergence.Scope(**{**SCOPE, "head_sha": sha})
    primary = convergence.CanonicalPrimary(
        schema_version=1,
        repository_id=SCOPE["repository_id"],
        pr_number=SCOPE["pr_number"],
        head_sha=sha,
        run_id=7,
        run_attempt=1,
        verdict="fail",
        p1_ids=("finding-one",),
        p1_findings=(("finding-one", "major", "inferred", "src/guard.py", 12, "correctness"),),
    )
    status = convergence.validate_disposition_receipt(
        receipt, scope=scope, primary=primary,
        audit_digest=convergence.canonical_audit_digest(_audit(head_sha=sha)),
    )
    assert (status.valid, status.active, status.reason) == (True, True, "active_false_positive")
