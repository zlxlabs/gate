import argparse
import ast
import hashlib
import importlib.util
import inspect
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"
PRIMARY_REVIEW_V2_TIER_SOURCE_URL = (
    "https://github.com/zlxlabs/fd-satisfaction-survey/actions/runs/32446501755/"
    "artifacts/9434236632"
)


def _module():
    spec = importlib.util.spec_from_file_location("review_ledger", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _audit(sha: str, ids: list[str], duration: int = 30) -> dict:
    return {
        "status": "fail" if ids else "pass",
        "reviewed_sha": sha,
        "coverage": {"mode": "single", "complete": True, "diff_lines": 100, "shards": 1},
        "runtime": {"duration_s": duration, "codex_version": "codex-cli test", "model": "default"},
        "result": {
            "verdict": "fail" if ids else "pass",
            "summary": "result",
            "findings": [
                {"id": finding_id, "severity": "major", "category": "correctness"}
                for finding_id in ids
            ],
        },
    }


def _preflight(diff_lines: int = 100, *, plan: str = "single") -> dict:
    return {"diff_lines": diff_lines, "classification": plan, "review_plan": plan, "thresholds": {"single_turn_lines": 4000}}


def _v2_audit(verdict: str, *, cost=None, tokens=None, runtime=None, expected_shadows=None) -> dict:
    result = None if verdict not in {"pass", "fail"} else {
        "verdict": verdict,
        "summary": "result",
        "findings": [] if verdict == "pass" else [
            {"id": "correctness.bad-state", "severity": "major", "category": "correctness"}
        ],
    }
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "repository": "zlxlabs/app", "pr": 7,
        "base_sha": "base", "head_sha": "head", "diff_digest": "d" * 64,
        "policy_version": "v1", "policy_digest": "e" * 64,
        "registry_commit": "a" * 40, "caller_sha": "b" * 40,
        "reusable_workflow_sha": "c" * 40, "run_id": 10, "run_attempt": 1,
        "job_id": 99, "reviewer": None if verdict in {"not_expected", "waived"} else "codex-sub",
        "verdict": verdict, "attempts": [], "shadow_mode": "detached",
        "expected_shadows": [] if expected_shadows is None else expected_shadows,
        "result": result, "cost": cost, "tokens": tokens, "runtime": runtime,
        "merge_base_sha": "merge-base", "candidate_commit_sha": "candidate-commit",
        "candidate_tree_sha": "candidate-tree", "run_mode": "PAYLOAD_ONLY",
    }
    if verdict == "not_expected":
        audit["not_expected_reason"] = "hosted_runner"
    elif verdict == "waived":
        audit["waiver"] = {"approver": "owner", "approved_at": "2026-08-09T00:00:00Z", "reason": "test"}
    return audit


def _primary_risk() -> dict:
    return {
        "declared_tier": "internal",
        "effective_tier": "saas",
        "rule_hits": ["risk-tier", "security"],
        "rules_version": "risk-rules/v1",
        "human_required": True,
        "security_pass": False,
    }


def _build_primary_risk_entry(module, risk: dict) -> dict:
    return module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit={**_v2_audit("pass"), "risk": risk},
    )


EXPECTED_IDENTITY = {
    "repository_id": 123,
    "base_sha": "base",
    "caller_sha": "b" * 40,
    "reusable_workflow_sha": "c" * 40,
}

def test_install_metrics_flow_through_when_present_and_default_to_none():
    module = _module()
    with_install = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
        install={"ecosystem": "uv", "status": "ok", "duration_s": 42, "cache_hit": True},
    )
    without_install = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=11, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
    )

    assert with_install["install"] == {
        "ecosystem": "uv", "status": "ok", "duration_s": 42, "cache_hit": True,
    }
    # Old callers (and old ledger entries with no "install" key) must not break —
    # the field is purely additive.
    assert without_install["install"] is None


def test_missing_install_result_file_yields_null_install_field(tmp_path):
    # tier != personal 时 Install 步骤整体不跑,
    # install-result.json *不存在*(不是一份 skipped JSON)。_load_json 必须
    # 对缺文件(以及空文件)容错为 None,进而 ledger 条目 install 字段为 null,
    # 不能报错 —— 否则非 personal tier 的每次 run 都会在 ledger 步骤炸掉。
    module = _module()

    missing = tmp_path / "does-not-exist" / "install-result.json"
    assert module._load_json(missing) is None

    empty = tmp_path / "install-result.json"
    empty.write_text("")
    assert module._load_json(empty) is None

    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
        install=module._load_json(missing),
    )
    assert entry["install"] is None
    assert json.loads(json.dumps(entry))["install"] is None


def test_ledger_deduplicates_run_attempts_and_writes_jsonl(tmp_path):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
    )
    output = tmp_path / "ledger.jsonl"
    module.write_ledger(output, entry)

    lines = output.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["review"]["status"] == "pass"


@pytest.mark.parametrize("verdict", ["pass", "fail", "unavailable", "not_expected", "waived"])
def test_v2_primary_audit_projects_verdict_and_identity(verdict):
    module = _module()
    audit = _v2_audit(verdict)
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
    )

    assert entry["review"]["status"] == verdict
    assert entry["review"]["verdict"] == verdict
    assert entry["primary_identity"] == {key: audit[key] for key in module.PRIMARY_IDENTITY_FIELDS if key in audit}
    if verdict in {"not_expected", "waived"}: assert entry["review"]["result"] is None and entry["review"]["finding_count"] == 0


def test_review_ledger_consumes_real_primary_v2_tier_artifact_bytes():
    module = _module()
    fixture_bytes = (ROOT / "tests/data/primary_review_v2_with_tier.json").read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == (
        "5f8beb2607fd3cf7a79b0adb539d85a68ee189aed463bd08932835a436f3abb8"
    )
    audit = json.loads(fixture_bytes)
    findings = audit["result"]["findings"]
    audit["finding_relation"] = {
        "schema_version": 1,
        "counts": {"new": len(findings), "repeat": 0, "conflict": 0},
        "review_terminal": "unchanged",
        "items": [
            {"id": item["id"], "relation_to_previous": "new"} for item in findings
        ],
        "open_findings": findings,
        "history_status": "available",
    }

    entry = module.build_entry(
        repository=audit["repository"], pr_number=audit["pr"], run_id=audit["run_id"],
        run_attempt=audit["run_attempt"], head_sha=audit["head_sha"], preflight=_preflight(),
        audit=audit,
    )

    assert audit["run_id"] == 32446501755, PRIMARY_REVIEW_V2_TIER_SOURCE_URL
    assert entry["primary_identity"]["tier"] == "internal"


@pytest.mark.parametrize("tier", ["personal", "internal", "saas"])
@pytest.mark.parametrize("verdict", ["pass", "not_expected"])
def test_primary_v2_tier_accepts_domain_values_for_review_and_no_review(verdict, tier):
    module = _module()
    audit = _v2_audit(verdict)
    audit.update(schema_version=2, tier=tier)

    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
    )

    assert entry["primary_identity"]["tier"] == tier


@pytest.mark.parametrize("tier", [None, "", 1, True, "enterprise"])
def test_primary_v2_tier_rejects_invalid_domain_values(tier):
    module = _module()
    audit = _v2_audit("pass")
    audit.update(schema_version=2, tier=tier)

    with pytest.raises(ValueError, match="canonical primary tier"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


def test_primary_v2_tier_does_not_allow_other_unknown_fields():
    module = _module()
    audit = _v2_audit("pass")
    audit.update(schema_version=2, tier="personal", unexpected_primary_field="value")

    with pytest.raises(ValueError, match="extra=.*unexpected_primary_field"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


# gate-hub PR #1900 (42535dae38) made canonical primary audits carry this optional
# composite. Cross-repo publish boundary (gate-hub issue #1879): the fixture is the
# recorded producer primary envelope (tests/data/primary_review_v2_with_tier.json)
# plus the ``intent_provenance`` value copied verbatim from
# ``gh api repos/zlxlabs/gate-hub/contents/tests/fixtures/primary-audit-redline-golden.json``.
INTENT_PROVENANCE_FIXTURE = ROOT / "tests" / "fixtures" / "primary-intent-provenance-envelope.json"
# sha256 of the fixture bytes; drift here means the recorded envelope or the producer
# value changed and the red-on-drift contract must be re-established deliberately.
INTENT_PROVENANCE_FIXTURE_SHA256 = (
    "580efc6d580ef47c5840889649dbe9c8427d33869361b805be01095b437538a0"
)
# The producer's value, transcribed from the gate-hub artifact named above.
RECORDED_INTENT_PROVENANCE = {
    "intent_source": "missing",
    "task_id": None,
    "issue_numbers": [],
    "intent_chars": 0,
    "skipped_sources": [],
}


def _intent_envelope() -> dict:
    return json.loads(INTENT_PROVENANCE_FIXTURE.read_bytes().decode("utf-8"))


def _build_intent_entry(module, audit: dict) -> dict:
    return module.build_entry(
        repository=audit["repository"], pr_number=audit["pr"], run_id=audit["run_id"],
        run_attempt=audit["run_attempt"], head_sha=audit["head_sha"],
        preflight=_preflight(), audit=audit,
    )


def test_recorded_primary_intent_provenance_envelope_is_accepted():
    module = _module()
    fixture_bytes = INTENT_PROVENANCE_FIXTURE.read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == INTENT_PROVENANCE_FIXTURE_SHA256
    audit = _intent_envelope()
    assert audit["kind"] == "primary_review"
    assert audit["intent_provenance"] == RECORDED_INTENT_PROVENANCE

    entry = _build_intent_entry(module, audit)

    assert entry["primary_identity"]["head_sha"] == audit["head_sha"]
    assert "intent_provenance" not in entry["primary_identity"]


def test_primary_intent_provenance_does_not_open_the_envelope_to_other_fields():
    module = _module()
    audit = {**_intent_envelope(), "bogus_field": 1}

    with pytest.raises(ValueError, match=r"invalid canonical primary envelope: extra=\['bogus_field'\]"):
        _build_intent_entry(module, audit)


@pytest.mark.parametrize(("field", "value"), [
    ("intent_source", "task_card"),
    ("intent_source", None),
    ("task_id", ""),
    ("task_id", 7),
    ("issue_numbers", ["385"]),
    ("issue_numbers", [0]),
    ("issue_numbers", True),
    ("intent_chars", -1),
    ("intent_chars", True),
    ("skipped_sources", [""]),
    ("skipped_sources", "none"),
])
def test_primary_intent_provenance_rejects_illegal_values(field, value):
    module = _module()
    audit = _intent_envelope()
    audit["intent_provenance"] = {**audit["intent_provenance"], field: value}

    with pytest.raises(ValueError, match="intent_provenance"):
        _build_intent_entry(module, audit)


@pytest.mark.parametrize("shape", ["not_object", "missing_field", "unknown_field"])
def test_primary_intent_provenance_rejects_incomplete_or_unknown_shapes(shape):
    module = _module()
    audit = _intent_envelope()
    provenance = dict(audit["intent_provenance"])
    if shape == "not_object":
        audit["intent_provenance"] = ["missing"]
    elif shape == "missing_field":
        provenance.pop("skipped_sources")
        audit["intent_provenance"] = provenance
    else:
        provenance["future_intent_signal"] = True
        audit["intent_provenance"] = provenance

    with pytest.raises(ValueError, match="intent_provenance"):
        _build_intent_entry(module, audit)


def test_primary_intent_provenance_stays_optional():
    module = _module()
    audit = _intent_envelope()
    audit.pop("intent_provenance")

    entry = _build_intent_entry(module, audit)

    assert "intent_provenance" not in entry["primary_identity"]


def test_main_writes_primary_risk_fields_to_jsonl_without_inference(tmp_path, monkeypatch):
    module = _module()
    audit = _v2_audit("fail")
    risk = _primary_risk()
    risk.update(rule_hits=[], security_pass=None)
    audit["risk"] = risk

    rc, output, _audit = _run_ledger_main(
        module, tmp_path, monkeypatch, preflight=_preflight(), audit=audit,
    )

    output_bytes = output.read_bytes()
    assert rc == 0
    assert output_bytes.endswith(b"\n") and output_bytes.count(b"\n") == 1
    row = json.loads(output_bytes)
    assert row["risk"] == risk


@pytest.mark.parametrize("tier", ["personal", "internal", "saas"])
def test_primary_risk_accepts_declared_and_effective_tier_values(tier):
    module = _module()
    risk = _primary_risk()
    risk.update(declared_tier=tier, effective_tier=tier)
    entry = _build_primary_risk_entry(module, risk)

    assert entry["risk"] == risk


@pytest.mark.parametrize("field", [
    "declared_tier", "effective_tier", "rule_hits", "rules_version",
    "human_required", "security_pass",
])
def test_primary_risk_preserves_nullable_schema_fields(field):
    module = _module()
    risk = _primary_risk()
    risk[field] = None
    entry = _build_primary_risk_entry(module, risk)

    assert entry["risk"] == risk


@pytest.mark.parametrize(("field", "value"), [
    ("declared_tier", ""), ("declared_tier", 1), ("declared_tier", True),
    ("declared_tier", "enterprise"), ("effective_tier", []),
    ("effective_tier", "enterprise"), ("rules_version", ""),
    ("rules_version", 1), ("rule_hits", [""]), ("rule_hits", [1]),
    ("rule_hits", {}), ("human_required", 0), ("human_required", "false"),
    ("security_pass", 1), ("security_pass", "true"),
])
def test_primary_risk_rejects_invalid_field_values(field, value):
    module = _module()
    risk = _primary_risk()
    risk[field] = value
    with pytest.raises(ValueError, match="canonical primary risk"):
        _build_primary_risk_entry(module, risk)


@pytest.mark.parametrize("shape", ["not_object", "missing_field", "unknown_field"])
def test_primary_risk_rejects_incomplete_or_unknown_shapes(shape):
    module = _module()
    risk = _primary_risk()
    if shape == "not_object":
        audit_risk = None
    elif shape == "missing_field":
        risk.pop("rules_version")
        audit_risk = risk
    else:
        risk["future_policy_signal"] = True
        audit_risk = risk
    with pytest.raises(ValueError, match="canonical primary risk"):
        _build_primary_risk_entry(module, audit_risk)


@pytest.mark.parametrize("diff_lines,plan,mode,complete,shards", [(100, "single", "single", True, 1), (5000, "sharded", "sharded+cross-module integration", False, None)])
def test_v2_review_preserves_result_and_recomputes_legacy_coverage(
    diff_lines, plan, mode, complete, shards,
):
    module = _module()
    audit = _v2_audit("fail", cost=1.25, tokens=[{"input": 3}], runtime={"duration_s": 12.5})
    audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 12.5, "cost_usd": 1.25}]
    entry = module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(diff_lines, plan=plan), audit=audit)

    review = entry["review"]
    assert review["result"] == audit["result"]
    assert review["cost_usd"] == 1.25
    assert review["tokens"] == [{"input": 3}]
    assert review["runtime"] == {"duration_s": 12.5}
    assert review["coverage"] == {
        "mode": mode, "complete": complete, "diff_lines": diff_lines, "shards": shards,
    }
    assert review["shadows"] == {}
    assert review["finding_count"] == 1


# ── gate#121: quality short-circuit must still write the ledger row ──────────
# Production path: primary failure skips quality, Download is skipped, Build
# still runs with a missing preflight file. main() maps that to {}. The new
# cell is selected only by an explicit input_short_circuited boolean — empty
# preflight without that flag must keep raising.


def _short_circuit_fail_audit(**kwargs):
    audit = _v2_audit("fail", runtime={"duration_s": 9.5}, **kwargs)
    audit["attempts"] = [
        {"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 9.5, "cost_usd": 0},
    ]
    audit["result"]["findings"] = [
        {
            "id": "correctness.bad-state",
            "severity": "major",
            "category": "correctness",
            "trigger_kind": "measured",
        },
        {
            "id": "security.leak",
            "severity": "blocker",
            "category": "security",
            "trigger_kind": "inferred",
        },
    ]
    return audit


def _assert_short_circuit_audit_projection(review, *, audit):
    assert review["coverage"] is None
    assert review["severity_counts"] == {"blocker": 1, "major": 1}
    assert review["category_counts"] == {"correctness": 1, "security": 1}
    assert review["trigger_kind_counts"] == {"inferred": 1, "measured": 1}
    assert review["finding_ids"] == ["correctness.bad-state", "security.leak"]
    assert review["finding_count"] == 2
    assert review["inferred_p1_count"] == 1
    assert review["runtime"] == {"duration_s": 9.5}
    assert review["reviewer"] == "codex-sub"
    assert review["verdict"] == "fail"
    assert review["status"] == "fail"
    assert review["shadows"] == {}
    assert review["result"] == audit["result"]


def _run_ledger_main(
    module,
    tmp_path,
    monkeypatch,
    *,
    extra_argv=(),
    preflight=None,
    install=None,
    audit=None,
):
    audit = audit or _short_circuit_fail_audit()
    work = tmp_path
    work.mkdir(parents=True, exist_ok=True)
    audit_path = work / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    if preflight is None:
        preflight_path = work / "missing" / "pr-size-preflight.json"
    else:
        preflight_path = work / "pr-size-preflight.json"
        preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    if install is None:
        install_path = work / "missing" / "install-result.json"
    else:
        install_path = work / "install-result.json"
        install_path.write_text(json.dumps(install), encoding="utf-8")
    output = work / "ledger.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "build_ledger.py",
        "--audit-path", str(audit_path),
        "--preflight-path", str(preflight_path),
        "--install-path", str(install_path),
        "--output", str(output),
        "--repository", "zlxlabs/app",
        "--pr-number", "7",
        "--run-id", "10",
        "--run-attempt", "1",
        "--head-sha", "head",
        "--expected-repository-id", "123",
        "--expected-base-sha", "base",
        "--expected-caller-sha", "b" * 40,
        "--expected-reusable-workflow-sha", "c" * 40,
        "--codex-expected", "true",
        *extra_argv,
    ])
    rc = module.main()
    return rc, output, audit


def _live_record_audit() -> dict:
    return {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": 123, "repository": "zlxlabs/gate", "pr": 42,
        "base_sha": "b" * 40, "head_sha": "a" * 40, "diff_digest": "d" * 64,
        "policy_version": "policy-v1", "policy_digest": "e" * 64,
        "registry_commit": "0123456789abcdef0123456789abcdef01234567", "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40, "run_id": 999, "run_attempt": 1,
        "job_id": 99, "reviewer": "codex-sub", "verdict": "fail",
        "merge_base_sha": "9" * 40, "candidate_commit_sha": "f" * 40,
        "candidate_tree_sha": "1" * 40, "run_mode": "PAYLOAD_ONLY",
        "spec_source": "event_payload", "pr_body_digest": "empty",
        "attempts": [], "shadow_mode": "detached", "expected_shadows": [],
        "result": {"verdict": "fail", "summary": "blocking finding", "findings": [
            {"id": "correctness.bad-state", "severity": "major", "category": "correctness"},
        ], "finding_dispositions": []},
        "cost": 0, "tokens": [], "runtime": None,
        "risk": _primary_risk(),
    }


def _live_record_terminal(*, skip_reason=None, run_attempt=1, unexpected_primary_skip=False):
    agg = _aggregator()
    identity = agg.Identity(repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=run_attempt, pr=42)
    is_draft = skip_reason == "draft"
    is_fork = skip_reason == "fork"
    runner = "hosted" if skip_reason == "hosted_runner" else "self"
    classify_review_expected = "false" if skip_reason == "review_exempt" else ""
    skipped = skip_reason is not None or unexpected_primary_skip
    outcome = agg.evaluate(
        quality_result="success", primary_result="skipped" if skipped else "failure",
        runner=runner, is_draft=is_draft, is_fork=is_fork,
        review_expected=not skipped, classify_review_expected=classify_review_expected,
        audit=None if skipped else _live_record_audit(), audit_error=None,
        identity=identity, pr_author="owner",
    )
    outcome.convergence = {"fail_count": None, "reconsider": None} if skipped else {"fail_count": 3, "reconsider": True}
    return agg.build_terminal_envelope(
        repository="zlxlabs/gate", identity=identity, quality_result="success",
        primary_result="skipped" if skipped else "failure", review_expected=not skipped,
        is_draft=is_draft, runner=runner, outcome=outcome,
    )


def _run_live_record_cli(tmp_path, *, audit=None, terminal=None, run_attempt=1, record_output=True):
    work = tmp_path
    work.mkdir(parents=True, exist_ok=True)
    audit_path, preflight_path = work / "audit.json", work / "preflight.json"
    terminal_path, ledger_path = work / "terminal.json", work / "ledger.jsonl"
    record_path = work / "review-record.jsonl"
    if audit is not None:
        audit_path.write_text(json.dumps(audit), encoding="utf-8")
    preflight_path.write_text(json.dumps(_preflight()), encoding="utf-8")
    if terminal is not None:
        terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    command = [
        sys.executable, str(MODULE_PATH), "--audit-path", str(audit_path),
        "--preflight-path", str(preflight_path), "--install-path", str(work / "missing-install.json"),
        "--output", str(ledger_path), "--repository", "zlxlabs/gate", "--pr-number", "42",
        "--run-id", "999", "--run-attempt", str(run_attempt), "--head-sha", "a" * 40,
        "--expected-repository-id", "123", "--expected-base-sha", "b" * 40,
        "--expected-caller-sha", "c" * 40, "--expected-reusable-workflow-sha", "w" * 40,
        "--codex-expected", "true",
    ]
    if terminal is not None:
        command.extend(["--terminal-path", str(terminal_path)])
    if record_output:
        command.extend(["--record-output", str(record_path)])
    env = os.environ.copy()
    env.pop("GITHUB_STEP_SUMMARY", None)
    completed = subprocess.run(command, capture_output=True, text=True, env=env)
    return completed, ledger_path, record_path


@pytest.mark.parametrize(("verdict", "primary", "warning"), [
    ("pass", "success", False), ("fail", "failure", False), ("unavailable", "failure", False),
    ("fail", "success", True), ("unavailable", "success", True), ("pass", "failure", True),
])
def test_primary_consistency_reads_produced_compact_record(tmp_path, verdict, primary, warning):
    audit = _live_record_audit()
    audit["verdict"] = verdict
    audit["result"] = None if verdict == "unavailable" else {
        "verdict": verdict, "summary": "result", "findings": [] if verdict == "pass" else audit["result"]["findings"],
    }
    completed, _ledger, record_path = _run_live_record_cli(tmp_path, audit=audit)
    assert completed.returncode == 0, completed.stderr
    message = _module().primary_record_consistency_warning(
        record_path, primary_result=primary, run_id=999, attempt=1, head_sha="a" * 40,
    )
    assert (message is not None) is warning
    assert message is None or "mismatch" in message


@pytest.mark.parametrize(("field", "wrong"), [("run_id", 1000), ("attempt", 2), ("head_sha", "f" * 40)])
def test_primary_consistency_warns_on_written_identity_mismatch_and_ignores_skip(tmp_path, field, wrong):
    completed, _ledger, record_path = _run_live_record_cli(
        tmp_path / "identity", audit=_live_record_audit(),
    )
    assert completed.returncode == 0, completed.stderr
    record = json.loads(record_path.read_bytes())
    record[field] = wrong
    record_path.write_bytes(json.dumps(record).encode())
    warning = _module().primary_record_consistency_warning(
        record_path, primary_result="success", run_id=999, attempt=1, head_sha="a" * 40,
    )
    assert warning and "identity" in warning
    skipped, _ledger, skipped_path = _run_live_record_cli(
        tmp_path / "skip", terminal=_live_record_terminal(skip_reason="draft"),
    )
    assert skipped.returncode == 0, skipped.stderr
    compact = json.loads(skipped_path.read_bytes())
    assert compact["verdict"] is None and compact["skip"]["reason"] == "draft"
    assert _module().primary_record_consistency_warning(
        skipped_path, primary_result="skipped", run_id=999, attempt=1, head_sha="a" * 40,
    ) is None


@pytest.mark.parametrize(("primary", "skip_reason", "verdict", "corrupt", "warning"), [
    ("skipped", "draft", None, None, None), ("cancelled", "fork", None, None, None),
    ("skipped", None, "pass", None, "mismatch"), ("success", "draft", None, None, "mismatch"),
    ("skipped", None, None, None, "could not compare"), ("unknown", None, "pass", None, "could not compare"),
    ("success", None, "pass", ["invalid"], "could not compare"),
])
def test_primary_consistency_classifies_produced_record_boundaries(
    tmp_path, primary, skip_reason, verdict, corrupt, warning,
):
    audit = None if verdict is None else _live_record_audit()
    if audit is not None:
        audit["verdict"] = verdict
        audit["result"] = None if verdict == "unavailable" else {
            "verdict": verdict, "summary": "result", "findings": [] if verdict == "pass" else audit["result"]["findings"],
        }
    terminal = _live_record_terminal(skip_reason=skip_reason) if skip_reason else None
    completed, _ledger, record_path = _run_live_record_cli(tmp_path, audit=audit, terminal=terminal)
    assert completed.returncode == 0, completed.stderr
    if corrupt:
        record = json.loads(record_path.read_bytes())
        record["verdict"] = corrupt
        record_path.write_bytes(json.dumps(record).encode())
    message = _module().primary_record_consistency_warning(
        record_path, primary_result=primary, run_id=999, attempt=1, head_sha="a" * 40,
    )
    assert (message is None) if warning is None else (message is not None and warning in message)


def test_live_record_cli_preserves_primary_and_security_review_stages(tmp_path):
    fixture_path = ROOT / "tests/data/primary_security_stage_producer_audit.json"
    audit_bytes = fixture_path.read_bytes()
    assert hashlib.sha256(audit_bytes).hexdigest() == "9b06d5a3f4abaea77c2fac0bc2708af24c9445b553a857f413b951e6805c1c6e"
    audit = json.loads(audit_bytes)
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(json.dumps(_preflight()), encoding="utf-8")
    output_path = tmp_path / "ledger.jsonl"
    cli_args = {
        "--audit-path": fixture_path, "--preflight-path": preflight_path,
        "--install-path": tmp_path / "missing-install.json", "--output": output_path,
        "--repository": audit["repository"], "--pr-number": audit["pr"],
        "--run-id": audit["run_id"], "--run-attempt": audit["run_attempt"],
        "--head-sha": audit["head_sha"], "--expected-repository-id": audit["repository_id"],
        "--expected-base-sha": audit["base_sha"], "--expected-caller-sha": audit["caller_sha"],
        "--expected-reusable-workflow-sha": audit["reusable_workflow_sha"], "--codex-expected": "true",
    }
    command = [sys.executable, str(MODULE_PATH), *[part for key, value in cli_args.items() for part in (key, str(value))]]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    ledger_bytes = output_path.read_bytes()
    row = json.loads(ledger_bytes)
    assert [attempt["review_stage"] for attempt in row["review"]["attempts"]] == ["primary", "security"]
    assert [finding["review_stage"] for finding in row["review"]["result"]["findings"]] == ["security"]
    assert b'"review_stage": "primary"' in ledger_bytes
    assert b'"review_stage": "security"' in ledger_bytes

    legacy = _live_record_audit()
    legacy["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 4, "cost_usd": 0}]
    legacy["runtime"] = {"duration_s": 4}
    old, old_ledger_path, _ = _run_live_record_cli(
        tmp_path / "legacy", audit=legacy, terminal=_live_record_terminal(), record_output=False,
    )
    assert old.returncode == 0, old.stderr
    old_bytes = old_ledger_path.read_bytes()
    old_row = json.loads(old_bytes)
    assert old_row["review"]["attempts"] == [legacy["attempts"][0] | {"diag_snippet": None}]
    assert b'"review_stage"' not in old_bytes


@pytest.mark.parametrize("legacy_null_findings", [False, True])
def test_live_record_cli_writes_contract_fixture_and_keeps_legacy_row(tmp_path, legacy_null_findings):
    audit = _live_record_audit() if not legacy_null_findings else {
        "status": "fail", "result": {"verdict": "fail", "findings": None},
    }
    terminal = _live_record_terminal() if not legacy_null_findings else None
    completed, ledger_path, record_path = _run_live_record_cli(
        tmp_path / "with-record", audit=audit, terminal=terminal,
    )
    assert completed.returncode == 0, completed.stderr
    record_bytes = record_path.read_bytes()
    assert record_bytes.endswith(b"\n") and record_bytes.count(b"\n") == 1
    record = json.loads(record_bytes)
    assert set(record) == {
        "schema_version", "source", "repo", "pr", "base_sha", "merge_base_sha", "head_sha",
        "run_id", "attempt", "round_index", "gate_hub_commit", "prompt_digest", "model_used",
        "profile", "reviewer_id", "verdict", "findings", "finding_dispositions", "convergence",
        "risk", "skip", "recorded_at", "provenance", "legacy_finding_dispositions",
    }
    assert legacy_null_findings or record["risk"] == audit["risk"]
    assert legacy_null_findings or record["convergence"] == terminal["convergence"]
    assert record["skip"] == {"reason": None}
    assert legacy_null_findings or "skip.reason" not in record["provenance"]["unknown_fields"]
    assert (record["findings"] == []) is legacy_null_findings
    assert legacy_null_findings or record["findings"][0]["introduced"] is None
    assert legacy_null_findings or "findings[0].introduced" in record["provenance"]["unknown_fields"]
    assert record["provenance"]["origin"] == "gate-live-review-ledger-v2"
    assert json.loads(completed.stdout) == json.loads(ledger_path.read_bytes())

    fixture_path = ROOT / "tests" / "fixtures" / "review-record-v1-live.json"
    fixture = json.loads(fixture_path.read_bytes())
    assert legacy_null_findings or {key: value for key, value in record.items() if key != "recorded_at"} == {
        key: value for key, value in fixture.items() if key != "recorded_at"
    }
    without_sidecar, old_ledger_path, old_record_path = _run_live_record_cli(
        tmp_path / "legacy-cli", audit=audit, terminal=terminal, record_output=False,
    )
    assert without_sidecar.returncode == 0, without_sidecar.stderr
    assert not old_record_path.exists()
    old_row, sidecar_run_row = json.loads(old_ledger_path.read_bytes()), json.loads(ledger_path.read_bytes())
    assert {key: value for key, value in old_row.items() if key != "recorded_at"} == {
        key: value for key, value in sidecar_run_row.items() if key != "recorded_at"
    }


@pytest.mark.parametrize("skip_reason", ["draft", "fork", "hosted_runner", "review_exempt"])
def test_live_record_cli_projects_each_verified_terminal_skip(tmp_path, skip_reason):
    terminal = _live_record_terminal(skip_reason=skip_reason)
    completed, _ledger_path, record_path = _run_live_record_cli(tmp_path, terminal=terminal)
    assert completed.returncode == 0, completed.stderr
    record = json.loads(record_path.read_bytes())
    assert record["verdict"] is None
    assert record["skip"] == {"reason": skip_reason}
    assert "skip.reason" not in record["provenance"]["unknown_fields"]


def test_live_record_cli_preserves_unexpected_primary_skip_as_unknown(tmp_path):
    terminal = _live_record_terminal(unexpected_primary_skip=True)
    assert (terminal["primary_result"], terminal["reason_code"], terminal["skip_reason"]) == (
        "skipped", "unexpected_primary_skip", None,
    )
    completed, _ledger_path, record_path = _run_live_record_cli(tmp_path, terminal=terminal)
    assert completed.returncode == 0, completed.stderr
    record = json.loads(record_path.read_bytes())
    assert record["skip"] == {"reason": None}
    assert "unexpected_primary_skip" in record["provenance"]["unknown_fields"]["skip.reason"]


@pytest.mark.parametrize(("field", "value", "run_attempt"), [
    ("repository_id", 124, 1), ("repository", "zlxlabs/other", 1),
    ("pr_number", 43, 1), ("run_id", 1000, 1), ("head_sha", "f" * 40, 1),
    ("run_attempt", 1, 2),
    ("skip_reason", "unexpected_primary_skip", 1),
])
def test_live_record_cli_rejects_invalid_terminal_skip_projection(
    tmp_path, field, value, run_attempt,
):
    terminal_attempt = value if field == "run_attempt" else run_attempt
    terminal = _live_record_terminal(skip_reason="draft", run_attempt=terminal_attempt)
    if field != "run_attempt":
        terminal[field] = value
    completed, ledger_path, record_path = _run_live_record_cli(
        tmp_path, terminal=terminal, run_attempt=run_attempt,
    )
    if field not in {"repository_id", "run_attempt"}:
        assert completed.returncode != 0
        assert not record_path.exists()
        return
    assert completed.returncode == 0, completed.stderr
    record = json.loads(record_path.read_bytes())
    if field == "run_attempt":
        assert json.loads(ledger_path.read_bytes())["terminal_source_attempt"] == value
    assert record["skip"] == {"reason": None}
    assert record["provenance"]["unknown_fields"]["skip.reason"]


def test_live_record_cli_marks_missing_terminal_unknown(tmp_path):
    missing, _ledger_path, missing_path = _run_live_record_cli(tmp_path / "missing")
    assert missing.returncode == 0, missing.stderr
    missing_record = json.loads(missing_path.read_bytes())
    assert missing_record["skip"] == {"reason": None}
    assert missing_record["convergence"] == {"fail_count": None, "reconsider": None}
    assert missing_record["provenance"]["unknown_fields"]["skip.reason"]


def test_live_record_gate_hub_commit_matches_primary_registry_commit(tmp_path):
    audit = _live_record_audit()
    completed, _ledger_path, record_path = _run_live_record_cli(
        tmp_path, audit=audit, terminal=_live_record_terminal(),
    )
    assert completed.returncode == 0, completed.stderr
    record = json.loads(record_path.read_bytes())
    assert record["gate_hub_commit"] == audit["registry_commit"]
    assert "gate_hub_commit" not in record["provenance"]["unknown_fields"]


def test_live_record_gate_hub_commit_rejects_non_hex_registry_commit():
    module = _module()
    audit = _live_record_audit()
    audit["registry_commit"] = "r" * 40
    entry = module.build_entry(
        repository="zlxlabs/gate", pr_number=42, run_id=999, run_attempt=1,
        head_sha="a" * 40, preflight=_preflight(), audit=audit,
    )
    with pytest.raises(ValueError, match="GATE-HUB-COMMIT-INVALID"):
        module.build_live_review_record(entry, repository_id=123, terminal_envelope=None)


def test_live_record_gate_hub_commit_unknown_when_primary_absent(tmp_path):
    completed, _ledger_path, record_path = _run_live_record_cli(
        tmp_path, terminal=_live_record_terminal(skip_reason="draft"),
    )
    assert completed.returncode == 0, completed.stderr
    record = json.loads(record_path.read_bytes())
    assert record["gate_hub_commit"] is None
    assert record["provenance"]["unknown_fields"]["gate_hub_commit"]



def test_main_no_network_writes_one_v2_row_with_current_fields(tmp_path, monkeypatch):
    module = _module()

    def fail_socket(*args, **kwargs):
        raise RuntimeError("network access is forbidden in ledger producer")

    monkeypatch.setattr(socket, "socket", fail_socket)
    rc, output, audit = _run_ledger_main(
        module,
        tmp_path,
        monkeypatch,
        preflight=_preflight(),
        install={"ecosystem": "uv", "status": "ok", "duration_s": 2, "cache_hit": True},
    )

    assert rc == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(module.LEDGER_ENTRY_REQUIRED_FIELDS) <= set(row)
    assert row["schema_version"] == 2
    assert isinstance(row["recorded_at"], str) and row["recorded_at"]
    assert row["repository"] == "zlxlabs/app"
    assert row["pr_number"] == 7
    assert row["run_id"] == 10
    assert row["run_attempt"] == 1
    assert row["head_sha"] == "head"
    assert row["preflight"] == _preflight()
    assert row["install"] == {"ecosystem": "uv", "status": "ok", "duration_s": 2, "cache_hit": True}
    assert row["primary_identity"] is not None
    assert row["review"]["finding_ids"] == [
        "correctness.bad-state", "security.leak",
    ]
    assert row["review"]["result"] == audit["result"]
    assert "risk" not in row
    assert row["finding_dispositions"] == {}
    assert row["false_positive_count"] == 0
    assert row["disposition_status"] == "success"
    assert not {"review_round", "comparison", "history_status", "ledger_conflict", "convergence_projection"} & row.keys()
    assert not set(module.LEDGER_ENTRY_OPTIONAL_FIELDS) & row.keys()


def test_build_ledger_imports_have_no_network_modules():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        node.names[0].name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
    }
    imported.update(
        node.module.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported & {"urllib", "http", "socket", "ssl"}


def test_short_circuited_empty_preflight_writes_ledger_row():
    """W1: short-circuit + empty/missing preflight → row written, coverage None."""
    module = _module()
    assert "input_short_circuited" in inspect.signature(module.build_entry).parameters
    assert "input_short_circuited" in inspect.signature(module._review_summary).parameters
    audit = _short_circuit_fail_audit()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=audit,
        install=None, expected_identity=EXPECTED_IDENTITY, input_short_circuited=True,
    )
    assert entry["preflight"] is None
    assert entry["install"] is None
    _assert_short_circuit_audit_projection(entry["review"], audit=audit)


def test_input_short_circuited_option_is_registered_on_parser(monkeypatch):
    """CLI option must be registered on ArgumentParser, not merely appear in source text."""
    module = _module()
    captured: list[str] = []

    def grab(self, args=None, namespace=None):
        captured.extend(
            option
            for action in self._actions
            for option in action.option_strings
        )
        raise SystemExit("captured-parser")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", grab)
    monkeypatch.setattr(sys, "argv", ["build_ledger.py"])
    with pytest.raises(SystemExit, match="captured-parser"):
        module.main()
    assert "--input-short-circuited" in captured


def test_main_short_circuited_missing_preflight_writes_jsonl(tmp_path, monkeypatch):
    """W1 main(): missing input files + --input-short-circuited true → jsonl row."""
    module = _module()
    rc, output, audit = _run_ledger_main(
        module, tmp_path, monkeypatch,
        extra_argv=["--input-short-circuited", "true"],
    )
    assert rc == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["preflight"] is None
    _assert_short_circuit_audit_projection(row["review"], audit=audit)


def test_primary_review_empty_preflight_without_short_circuit_still_raises():
    """W2: non-short-circuit + empty preflight must keep raising. Do not relax."""
    module = _module()
    with pytest.raises(ValueError, match="canonical primary preflight has invalid coverage shape"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight={}, audit=_v2_audit("fail"),
        )
    params = inspect.signature(module.build_entry).parameters
    if "input_short_circuited" in params:
        with pytest.raises(ValueError, match="canonical primary preflight has invalid coverage shape"):
            module.build_entry(
                repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
                head_sha="head", preflight={}, audit=_v2_audit("fail"),
                input_short_circuited=False,
            )


def test_short_circuited_valid_preflight_still_computes_coverage():
    """W3: short-circuit flag is not a switch to ignore a present preflight."""
    module = _module()
    assert "input_short_circuited" in inspect.signature(module.build_entry).parameters
    audit = _v2_audit("fail")
    preflight = _preflight(100)
    kwargs = dict(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=preflight, audit=audit, expected_identity=EXPECTED_IDENTITY,
    )
    normal = module.build_entry(**kwargs)
    shorted = module.build_entry(**kwargs, input_short_circuited=True)
    assert shorted["review"]["coverage"] == normal["review"]["coverage"]
    assert shorted["review"]["coverage"]["complete"] is True
    assert shorted["review"]["coverage"]["diff_lines"] == 100
    assert shorted["preflight"] == preflight


@pytest.mark.parametrize("raw", ["yes", "1", "TRUE", "True", "", "maybe", "false "])
def test_input_short_circuited_illegal_value_is_fail_loud(tmp_path, monkeypatch, raw):
    """W4: domain is exactly {true, false}; anything else SystemExit."""
    module = _module()
    with pytest.raises(SystemExit) as exc:
        _run_ledger_main(
            module, tmp_path, monkeypatch,
            extra_argv=["--input-short-circuited", raw],
        )
    assert exc.value.code == "--input-short-circuited must be one of true, false"


def test_omitted_input_short_circuited_matches_explicit_false(tmp_path, monkeypatch):
    """W4: omitting the CLI flag must behave identically to passing false."""
    module = _module()

    def outcome(label, extra):
        try:
            _run_ledger_main(module, tmp_path / label, monkeypatch, extra_argv=extra)
        except SystemExit as exc:
            return ("systemexit", str(exc))
        except ValueError as exc:
            return ("valueerror", str(exc))
        return ("ok", "")

    omitted = outcome("omitted", ())
    explicit = outcome("explicit-false", ("--input-short-circuited", "false"))
    assert omitted == explicit
    assert omitted[0] == "valueerror"
    assert "canonical primary preflight has invalid coverage shape" in omitted[1]


def test_v2_detached_shadows_are_recorded_as_unavailable_without_losing_expectations():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm", "gemini-pro"])

    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
    )

    assert entry["review"]["shadows"] == {
        "shadow_mode": "detached",
        "status": "detached_unavailable",
        "expected_shadows": ["claude-glm", "gemini-pro"],
        "outcomes": None,
    }
    assert entry["review"]["shadows"] != {}


@pytest.mark.parametrize("value", [None, {}, "claude-glm", [""], ["claude-glm", 3]])
def test_v2_detached_shadows_reject_malformed_expected_names(value):
    module = _module()
    audit = _v2_audit("pass")
    audit["expected_shadows"] = value

    with pytest.raises(ValueError, match="expected_shadows"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


def test_v2_detached_shadows_reject_non_detached_mode():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm"])
    audit["shadow_mode"] = "inline"

    with pytest.raises(ValueError, match="shadow_mode"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


def test_v2_runtime_rejects_attempt_duration_sum_mismatch():
    module = _module()
    audit = _v2_audit("pass", runtime={"duration_s": 1})
    audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 2, "cost_usd": None}]
    with pytest.raises(ValueError, match="runtime"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit)


def _ledger_runtime_case(schema_version, verdict, runtime_case):
    reviewer_verdict = verdict not in {"not_expected", "waived"}
    audit = _v2_audit(verdict, runtime=None)
    audit["schema_version"] = schema_version
    if reviewer_verdict and runtime_case in {"valid", "mismatch"}:
        audit["attempts"] = [{"reviewer": "codex-sub", "exit_code": 0, "reason": "", "duration_s": 1.0, "cost_usd": None}]
    if runtime_case == "missing":
        audit.pop("runtime", None)
    else:
        audit["runtime"] = {"null": None, "valid": {"duration_s": 1.0},
                              "mismatch": {"duration_s": 2.0}, "invalid": {"duration_s": "bad"}}[runtime_case]
    return audit


@pytest.mark.parametrize("schema_version", [1, 2, 99])
@pytest.mark.parametrize("runtime_case", ["missing", "null", "valid", "mismatch", "invalid"])
@pytest.mark.parametrize("verdict", ["pass", "fail", "unavailable", "not_expected", "waived"])
def test_ledger_runtime_schema_upgrade_matrix(schema_version, runtime_case, verdict):
    module = _module()
    reviewer_verdict = verdict not in {"not_expected", "waived"}
    expected = (
        schema_version in {1, 2}
        and (runtime_case == "null" or (runtime_case == "valid" and reviewer_verdict)
             or (runtime_case == "missing" and schema_version == 1))
    )
    audit = _ledger_runtime_case(schema_version, verdict, runtime_case)
    call = lambda: module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight=_preflight(), audit=audit,
    )
    if expected:
        entry = call()
        assert entry["review"]["runtime"] is None if runtime_case == "missing" else True
    else:
        with pytest.raises(ValueError, match="canonical primary|runtime|schema"):
            call()


def test_ledger_consumes_historical_v1_fixture_without_runtime():
    module = _module()
    fixture = json.loads((ROOT / "tests/data/primary_review_v1_missing_runtime.json").read_text())
    entry = module.build_entry(
        repository=fixture["repository"], pr_number=fixture["pr"], run_id=fixture["run_id"],
        run_attempt=fixture["run_attempt"], head_sha=fixture["head_sha"], preflight=_preflight(),
        audit=fixture,
    )
    assert entry["review"]["runtime"] is None


@pytest.mark.parametrize("field,value", [("cost", -1), ("cost", float("inf")), ("tokens", {}), ("runtime", {"duration_s": -1}), ("runtime", {"duration_s": float("nan")})])
def test_v2_review_rejects_invalid_telemetry(field, value):
    module = _module()
    audit = _v2_audit("pass", **{field: value})
    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit)


@pytest.mark.parametrize("mutation", [lambda audit: audit["result"].pop("findings"), lambda audit: audit["result"]["findings"].append({"id": "broken"}), lambda audit: audit["attempts"].append({"reviewer": 3})])
def test_v2_review_rejects_malformed_consumed_payload(mutation):
    module = _module()
    audit = _v2_audit("pass")
    mutation(audit)
    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1, head_sha="head", preflight=_preflight(), audit=audit)


def test_v2_primary_audit_rejects_mismatched_parent_identity():
    module = _module()
    audit = _v2_audit("pass", expected_shadows=["claude-glm"])
    audit["head_sha"] = "stale"

    with pytest.raises(ValueError, match="primary audit identity mismatch"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


@pytest.mark.parametrize("field", list(EXPECTED_IDENTITY))
def test_v2_primary_audit_binds_every_workflow_identity_field(field):
    module = _module()
    audit = _v2_audit("pass")
    audit[field] = 999 if isinstance(EXPECTED_IDENTITY[field], int) else "stale"

    with pytest.raises(ValueError, match="primary audit identity mismatch"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
            expected_identity=EXPECTED_IDENTITY,
        )


@pytest.mark.parametrize("field,value", [("diff_digest", "D" * 64), ("policy_digest", "D" * 64), ("candidate_tree_sha", None)])
def test_v2_primary_audit_rejects_malformed_canonical_shape(field, value):
    module = _module()
    audit = _v2_audit("pass")
    if value is None:
        del audit[field]
    else:
        audit[field] = value

    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )


@pytest.mark.parametrize(
    "verdict,field",
    [(verdict, field) for verdict in ["pass", "fail", "unavailable"] for field in ["waiver", "not_expected_reason"]]
    + [("not_expected", "waiver"), ("waived", "not_expected_reason")],
)
def test_v2_primary_audit_rejects_companion_fields_for_wrong_verdict(verdict, field):
    module = _module()
    audit = _v2_audit(verdict)
    audit[field] = {} if field == "waiver" else "hosted_runner"

    with pytest.raises(ValueError, match="canonical primary"):
        module.build_entry(
            repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
            head_sha="head", preflight=_preflight(), audit=audit,
        )



def test_step_summary_scrubs_runtime_values(monkeypatch, tmp_path):
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="head", preflight={}, audit=_audit("head", []),
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("RUNNER_NAME", "runner-secret")
    entry["review"]["reviewer"] = "runner-secret"

    module._append_summary(entry, str(summary))

    text = summary.read_text()
    assert "runner-secret" not in text
    assert "[REDACTED:RUNNER_NAME]" in text


def test_review_summary_includes_reviewer_attempts_and_failover_from_audit():
    """P0: ledger review block must carry who ran, hop path, and whether failover happened."""
    module = _module()
    audit = _audit("sha", [])
    audit["reviewer"] = "codex-sub"
    audit["attempts"] = [
        {
            "reviewer": "claude-glm",
            "exit_code": 20,
            "reason": "Claude 模型服务过载（529）",
            "cost_usd": 0,
            "tokens": None,
            "duration_s": 175,
            "diag_snippet": "api_error_status=529 该模型当前访问量过大，请您稍后再试",
        },
        {
            "reviewer": "codex-sub",
            "exit_code": 0,
            "reason": "",
            "cost_usd": None,
            "tokens": None,
            "duration_s": 42,
            "diag_snippet": None,
        },
    ]
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    review = entry["review"]
    assert review["reviewer"] == "codex-sub"
    assert review["failover"] is True
    assert review["attempts"] == [
        {
            "reviewer": "claude-glm",
            "exit_code": 20,
            "reason": "Claude 模型服务过载（529）",
            "duration_s": 175,
            "cost_usd": 0,
            "diag_snippet": "api_error_status=529 该模型当前访问量过大，请您稍后再试",
        },
        {
            "reviewer": "codex-sub",
            "exit_code": 0,
            "reason": "",
            "duration_s": 42,
            "cost_usd": None,
            "diag_snippet": None,
        },
    ]


def test_review_summary_no_failover_when_single_successful_hop():
    module = _module()
    audit = _audit("sha", [])
    audit["reviewer"] = "claude-glm"
    audit["attempts"] = [
        {"reviewer": "claude-glm", "exit_code": 0, "reason": "", "duration_s": 12, "cost_usd": 0.9},
    ]
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["reviewer"] == "claude-glm"
    assert entry["review"]["failover"] is False
    assert len(entry["review"]["attempts"]) == 1


def test_review_summary_defaults_when_audit_missing():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=None,
        fallback_status="not_run",
    )
    assert entry["review"]["reviewer"] is None
    assert entry["review"]["failover"] is False
    assert entry["review"]["attempts"] == []
    assert entry["review"]["trigger_kind_counts"] == {}
    assert entry["review"]["inferred_p1_count"] == 0


def test_review_summary_counts_measured_inferred_and_missing_trigger_kind():
    module = _module()
    audit = _audit("sha", ["a", "b", "c"])
    findings = audit["result"]["findings"]
    findings[0]["trigger_kind"] = "measured"
    findings[1]["trigger_kind"] = "inferred"
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["trigger_kind_counts"] == {
        "inferred": 1, "measured": 1, "unspecified": 1,
    }


def test_review_summary_inferred_p1_count_covers_blocker_and_major():
    module = _module()
    audit = _audit("sha", ["a", "b", "c", "d"])
    findings = audit["result"]["findings"]
    findings[0].update(severity="blocker", trigger_kind="inferred")
    findings[1].update(severity="major", trigger_kind="inferred")
    findings[2].update(severity="minor", trigger_kind="inferred")
    findings[3].update(severity="blocker", trigger_kind="measured")
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["inferred_p1_count"] == 2


def test_review_summary_invalid_trigger_kind_counts_as_unspecified():
    module = _module()
    audit = _audit("sha", ["a", "b"])
    findings = audit["result"]["findings"]
    findings[0]["trigger_kind"] = "guess"
    findings[1]["trigger_kind"] = 1
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=audit,
    )
    assert entry["review"]["trigger_kind_counts"] == {"unspecified": 2}


def _seed_caller_repo(root, *, relpath="src/lock.py", literal="locked-behavior"):
    repo = Path(root) / "caller"
    (repo / relpath).parent.mkdir(parents=True, exist_ok=True)
    (repo / relpath).write_text(f"{literal}\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "gate-test",
        "GIT_AUTHOR_EMAIL": "gate-test@example.com",
        "GIT_COMMITTER_NAME": "gate-test",
        "GIT_COMMITTER_EMAIL": "gate-test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "gate-test@example.com"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "gate-test"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True, env=env)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, env=env).strip()
    return repo, sha, relpath, literal


def _aggregator():
    path = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
    spec = importlib.util.spec_from_file_location("gate_aggregate_for_ledger", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _producer_terminal(*, receipts=(), run_attempt=1):
    agg = _aggregator()
    conv = agg._CONVERGENCE
    identity = agg.Identity(
        repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=run_attempt, pr=42,
    )
    audit = {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": identity.repository_id,
        "repository": "zlxlabs/gate",
        "head_sha": identity.head_sha,
        "run_id": identity.run_id,
        "run_attempt": identity.run_attempt,
        "pr": identity.pr,
        "verdict": "fail",
        "reviewer": "claude-glm",
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "e" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "registry_commit": "r" * 40,
        "job_id": 99,
        "shadow_mode": "detached",
        "expected_shadows": [],
        "attempts": [],
        "cost": 0,
        "tokens": [],
        "runtime": None,
        "result": {
            "verdict": "fail",
            "summary": "primary reviewer found a blocking issue",
            "findings": [{
                "id": "p1", "severity": "major", "trigger_kind": "inferred",
                "file": "src/lock.py", "line": 12, "category": "correctness",
            }],
        },
    }
    assert (scope := agg._convergence_scope_from_audit(audit, identity)[0]) is not None
    digest = "a" * 64
    typed_receipts = []
    for changes in receipts:
        fields = dict(
            schema_version=conv.DISPOSITION_RECEIPT_SCHEMA_VERSION,
            disposition="false-positive",
            repository_id=str(identity.repository_id),
            pr_number=identity.pr,
            epoch=conv.derive_epoch(scope),
            head_sha=identity.head_sha,
            audit_digest=digest,
            finding_id="p1",
            reason="locked upstream behavior",
            approver="octocat",
            approver_id=1,
            approved_at="2026-08-30T12:00:00Z",
            counterevidence={
                "command": "pytest -q tests/test_regression.py",
                "output": "1 passed",
                "result": "refuted",
                "pointer": "tests/test_regression.py::test_regression",
                "gate_rerun": {
                    "argv": [
                        "git", "grep", "-n", "-F", "-e", "locked-behavior",
                        identity.head_sha, "--", "src/lock.py",
                    ],
                    "exit_code": 0,
                    "match_count": 1,
                    "stdout_sha256": hashlib.sha256(b"src/lock.py:1:locked-behavior").hexdigest(),
                    "excerpt": "src/lock.py:1:locked-behavior",
                },
            },
        )
        fields.update(changes)
        typed_receipts.append(conv.DispositionReceipt(**fields))
    outcome = agg.evaluate(
        quality_result="success",
        primary_result="failure",
        runner="self",
        is_draft=False,
        review_expected=True,
        audit=audit,
        audit_error=None,
        identity=identity,
        pr_author="zj1123581321",
        audit_source_attempt=identity.run_attempt,
        audit_artifact_name="primary-audit-v2-1",
        scope=scope,
        audit_digest=digest,
        waiver_receipts=tuple(typed_receipts),
    )
    terminal = agg.build_terminal_envelope(
        repository="zlxlabs/gate", identity=identity, quality_result="success",
        primary_result="failure", review_expected=True, is_draft=False, runner="self",
        outcome=outcome,
    )
    return agg, conv, identity, typed_receipts, outcome, terminal


def test_ledger_projects_real_producer_terminal_claim_record():
    module = _module()
    agg, conv, identity, receipts, outcome, terminal = _producer_terminal(receipts=[{}])
    assert outcome.disposition_audit is not None
    assert terminal["disposition_receipt_consumption"]["consumed_count"] == 1
    entry = module.build_entry(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        terminal_envelope=terminal,
    )
    assert entry["disposition_receipt_consumption"] == terminal["disposition_receipt_consumption"]
    resolved = entry["disposition_receipt_consumption"]["resolved"]
    assert len(resolved) == 1
    assert resolved[0]["finding_id"] == "p1"
    assert resolved[0]["disposition"] == "false-positive"
    assert resolved[0]["evidence_pointer"] == "tests/test_regression.py::test_regression"


def test_ledger_preserves_recorded_human_id_and_stable_key():
    module = _module()
    block = {
        "resolved": [{
            "finding_id": "p1",
            "finding_key": "stable-key",
            "receipt": "receipt-artifact",
            "approver": "octocat",
            "approver_id": 1,
            "approved_at": "2026-08-30T12:00:00Z",
            "reason": "locked upstream behavior",
        }],
        "consumed_count": 1,
        "rejected_count": 0,
        "rejected_reasons": {},
        "fail_closed": False,
    }
    assert module.validate_disposition_receipt_audit(block) == {
        **block,
        "recorded": [],
    }


def test_ledger_empty_audit_when_producer_had_no_receipts():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    entry = module.build_entry(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        terminal_envelope=terminal,
    )
    expected = module.empty_disposition_receipt_audit()
    assert terminal["disposition_receipt_consumption"] == {
        **expected, "remaining_p1_ids": ["p1"],
    }
    assert entry["disposition_receipt_consumption"] == terminal["disposition_receipt_consumption"]


def test_ledger_omits_consumption_when_terminal_is_absent():
    module = _module()
    entry = module.build_entry(
        repository="zlxlabs/app", pr_number=7, run_id=10, run_attempt=1,
        head_sha="sha", preflight={}, audit=_audit("sha", []),
    )
    assert "disposition_receipt_consumption" not in entry


def test_disposition_audit_stays_out_of_review_summary_and_compact_attempts():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    entry = module.build_entry(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        terminal_envelope=terminal,
    )
    assert "disposition_receipt_consumption" in entry
    assert "disposition_receipt_consumption" not in entry["review"]
    for attempt in entry["review"]["attempts"]:
        assert "disposition_receipt_consumption" not in attempt
    assert "disposition_receipt_consumption" not in inspect.getsource(module._review_summary)
    assert "disposition_receipt_consumption" not in inspect.getsource(module._compact_attempts)


@pytest.mark.parametrize(
    ("scenario", "current_id", "current_line", "receipt_kind", "carry_ids"),
    [
        ("same-key-distinct-id", "p1", 12, "false-positive", ["p1", "other"]),
        ("id-drift", "p2", 12, "false-positive", ["p1", "p2"]),
        ("neighbor-key", "p1", 13, "false-positive", ["old", "p1"]),
        ("invalid", "p1", 12, "invalid", ["p1"]),
        ("deferred", "p1", 12, "deferred", ["p1"]),
    ],
)
def test_real_disposition_producer_closes_only_consumed_current_carry(
    tmp_path, monkeypatch, scenario, current_id, current_line, receipt_kind, carry_ids,
):
    agg = _aggregator()
    module = _module()
    conv = agg._CONVERGENCE
    repo, sha, relpath, literal = _seed_caller_repo(tmp_path)
    identity = agg.Identity(
        repository_id=123, head_sha=sha, run_id=999, run_attempt=1, pr=42,
    )
    audit = {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": identity.repository_id,
        "repository": "zlxlabs/gate",
        "head_sha": identity.head_sha,
        "run_id": identity.run_id,
        "run_attempt": identity.run_attempt,
        "pr": identity.pr,
        "verdict": "fail",
        "reviewer": "claude-glm",
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "e" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "registry_commit": "r" * 40,
        "job_id": 99,
        "shadow_mode": "detached",
        "expected_shadows": [],
        "attempts": [],
        "cost": 0,
        "tokens": [],
        "runtime": None,
        "result": {
            "verdict": "fail",
            "summary": "primary reviewer found a blocking issue",
            "findings": [
                {
                    "id": "p1", "severity": "major", "trigger_kind": "inferred",
                    "file": "src/lock.py", "line": 12, "category": "correctness",
                },
            ],
        },
    }
    scope, missing = agg._convergence_scope_from_audit(audit, identity)
    assert not missing and scope is not None
    audit_path = tmp_path / "canonical-audit.json"
    audit_path.write_bytes(json.dumps(audit, indent=2).encode("utf-8") + b"\n")
    output_dir = tmp_path / "receipts"
    command = [
        sys.executable, str(ROOT / ".github/actions/gate-disposition/issue_receipt.py"), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository-id", str(identity.repository_id), "--pr-number", str(identity.pr),
        "--head-sha", identity.head_sha, "--finding-id", "p1",
        "--reason", "locked upstream behavior",
        "--counterevidence-json", json.dumps({
            "command": f"git grep -n -F -e {literal} {sha} -- {relpath}",
            "output": "1 passed",
            "result": "refuted",
            "pointer": "tests/test_lock.py::test_lock_regression",
        }, sort_keys=True),
        "--approver", "octocat", "--approver-id", "1",
        "--approved-at", "2026-08-30T12:00:00Z",
        "--scope-json", json.dumps(scope.as_dict(), sort_keys=True),
        "--repo-dir", str(repo),
    ]
    produced = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "GITHUB_RUN_ID": "control-999", "GITHUB_TRIGGERING_ACTOR": "octocat"},
    )
    producer_result = json.loads(produced.stdout)
    receipt_bytes = Path(producer_result["path"]).read_bytes()
    receipt_payload = json.loads(receipt_bytes)
    assert receipt_bytes == json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode()
    receipt = producer_receipt = conv.parse_disposition_receipt(receipt_payload)
    if receipt_kind == "invalid":
        receipt = conv.parse_disposition_receipt({
            **receipt.as_dict(), "counterevidence": {**receipt.counterevidence, "result": "observed"},
        })
    elif receipt_kind == "deferred":
        receipt = conv.parse_disposition_receipt({
            **receipt.as_dict(), "disposition": "deferred", "counterevidence": None,
            "tracking_issue": "#123",
        })

    current = dict(audit["result"]["findings"][0], id=current_id, line=current_line)
    carry = [
        dict(current, id=finding_id, line=(12 if scenario == "neighbor-key" and finding_id == "old" else current_line))
        for finding_id in carry_ids
    ]
    prior_relation = agg.build_finding_relation(carry, {"available": False})
    prior_audit = json.loads(json.dumps(audit))
    prior_audit["result"]["findings"] = carry
    prior_outcome = agg.Outcome(
        ok=False, classification="code_fail", reason_code="primary_findings", gate_result="fail",
        finding_relation=prior_relation,
    )
    prior_terminal = agg.build_terminal_envelope(
        repository="zlxlabs/gate", identity=agg.Identity(123, sha, 999, 1, 42), quality_result="success",
        primary_result="failure", review_expected=True, is_draft=False, runner="self",
        outcome=prior_outcome,
    )
    prior_entry = module.build_entry(
        repository="zlxlabs/gate", pr_number=42, run_id=999, run_attempt=1, head_sha=sha,
        preflight=_preflight(), audit=prior_audit, terminal_envelope=prior_terminal,
    )
    ledger_path = tmp_path / "round-1.jsonl"
    module.write_ledger(ledger_path, prior_entry)
    latest_body = [ledger_path.read_bytes()]

    def fake_silo_cli(argv, timeout_s):
        assert argv[0] == "get"
        Path(argv[argv.index("--dest") + 1]).write_bytes(latest_body[0])
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "zlxlabs/gate")
    monkeypatch.setattr(agg, "_silo_configured", lambda: True)
    monkeypatch.setattr(agg, "_silo_cli", fake_silo_cli)
    monkeypatch.setattr(agg, "_silo_objects_under", lambda prefix, timeout_s: [])
    monkeypatch.setattr(agg, "_load_pr_convergence_history", lambda **kwargs: (
        "history_unavailable", (), agg.HistoryLoad(incomplete_reasons=["missing history"]),
    ))
    monkeypatch.setattr(agg, "_fetch_disposition_receipts", lambda **kwargs: (receipt,))
    rerun_argv = producer_receipt.counterevidence["gate_rerun"]["argv"]
    monkeypatch.setattr(agg._NEIGHBOR_EVIDENCE, "collect_neighbor_evidence", lambda **kwargs: {
        receipt.finding_key: conv.NeighborEvidence(
            current_head_sha=sha, anchor_line_current=current_line, anchor_window_touched=False,
            rerun_argv=rerun_argv, rerun_exit_code=0, rerun_match_count=1, receipt_scope=scope,
        ),
    })
    previous_path, audit_dir = tmp_path / "previous.json", tmp_path / "current-audit"
    audit_dir.mkdir()
    audit_path = audit_dir / "primary-audit.json"
    audit["result"]["findings"] = [current]
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    render = lambda run_id: agg.main([
        "--render-previous-context", "--repository-id", "123", "--pr-number", "42",
        "--run-id", str(run_id), "--run-attempt", "2", "--previous-json", str(previous_path),
    ])
    assert render(999) == 0
    assert [item["id"] for item in json.loads(previous_path.read_bytes())["findings"]] == carry_ids
    assert agg.main([
        "--annotate-primary-audit", "--audit-path", str(audit_path),
        "--previous-findings-path", str(previous_path),
    ]) == 0

    terminal_path = tmp_path / "gate-terminal.json"
    def aggregate(run_id, attempt):
        return agg.main([
            "--quality-result", "success", "--primary-result", "failure", "--runner", "self",
            "--is-draft", "false", "--review-expected", "true", "--pr-author-json",
            json.dumps("zj1123581321"), "--repository-id", "123",
            "--repository", "zlxlabs/gate", "--head-sha", sha, "--run-id", str(run_id),
            "--run-attempt", str(attempt), "--pr-number", "42", "--audit-source-attempt", "1",
            "--audit-artifact-name", "primary-audit-v2-1", "--audit-dir", str(audit_dir),
            "--terminal-path", str(terminal_path),
        ])
    rc = aggregate(999, 2)
    expected_gate = "pass" if receipt_kind == "false-positive" else "fail"
    assert rc == (0 if expected_gate == "pass" else 1)
    terminal = json.loads(terminal_path.read_bytes())
    assert terminal["gate_result"] == expected_gate and terminal["audit"]["source_attempt"] == 1
    assert terminal["disposition_receipt_consumption"]["consumed_count"] == int(receipt_kind == "false-positive")
    assert terminal["disposition_receipt_consumption"]["remaining_p1_ids"] == (
        [] if receipt_kind == "false-positive" else [current_id]
    )
    expected_ids = [finding_id for finding_id in carry_ids if receipt_kind != "false-positive" or finding_id != current_id]
    assert [item["id"] for item in terminal["finding_relation"]["open_findings"]] == expected_ids
    resolved = terminal["disposition_receipt_consumption"]["resolved"]
    if receipt_kind == "false-positive":
        assert [item["finding_id"] for item in resolved] == [current_id]
        assert resolved[0]["receipt"] == producer_result["artifact"]

    ledger = module.build_entry(
        repository="zlxlabs/gate", pr_number=42, run_id=999, run_attempt=2, head_sha=sha,
        preflight=_preflight(), audit=json.loads(audit_path.read_bytes()), terminal_envelope=terminal,
    )
    module.write_ledger(ledger_path, ledger)
    latest_body[0] = ledger_path.read_bytes()
    recorded = json.loads(latest_body[0])
    assert latest_body[0] == (json.dumps(recorded, ensure_ascii=False, sort_keys=True) + "\n").encode()
    assert render(1000) == 0
    assert [item["id"] for item in json.loads(previous_path.read_bytes())["findings"]] == expected_ids

    if scenario == "same-key-distinct-id":
        audit3 = json.loads(audit_path.read_bytes())
        for finding in audit3["result"]["findings"]:
            finding.pop("relation_to_previous", None)
            finding.pop("previous_finding_id", None)
        audit3.update(run_id=1000, run_attempt=1)
        audit_path.write_text(json.dumps(audit3), encoding="utf-8")
        assert agg.main([
            "--annotate-primary-audit", "--audit-path", str(audit_path),
            "--previous-findings-path", str(previous_path),
        ]) == 0
        reopened = json.loads(audit_path.read_bytes())["finding_relation"]
        assert reopened["items"][0]["relation_to_previous"] == "new"
        assert current_id in {item["id"] for item in reopened["open_findings"]}
        monkeypatch.setattr(agg, "_fetch_disposition_receipts", lambda **kwargs: ())
        assert aggregate(1000, 1) == 1
        terminal3 = json.loads(terminal_path.read_bytes())
        assert current_id in {item["id"] for item in terminal3["finding_relation"]["open_findings"]}
        ledger3 = module.build_entry(
            repository="zlxlabs/gate", pr_number=42, run_id=1000, run_attempt=1, head_sha=sha,
            preflight=_preflight(), audit=json.loads(audit_path.read_bytes()), terminal_envelope=terminal3,
        )
        module.write_ledger(ledger_path, ledger3)
        latest_body[0] = ledger_path.read_bytes()
        assert render(1001) == 0
        assert current_id in {item["id"] for item in json.loads(previous_path.read_bytes())["findings"]}



def test_saas_deferred_is_rejected_by_producer_and_aggregator(tmp_path):
    import os
    import subprocess

    agg = _aggregator()
    conv = agg._CONVERGENCE
    identity = agg.Identity(
        repository_id=123, head_sha="a" * 40, run_id=1001, run_attempt=1, pr=42,
    )
    finding = {
        "id": "p1", "severity": "major", "trigger_kind": "inferred",
        "file": "src/lock.py", "line": 12, "category": "correctness",
    }
    audit = {
        "kind": "primary_review", "schema_version": 1,
        "repository_id": identity.repository_id, "repository": "zlxlabs/gate",
        "head_sha": identity.head_sha, "run_id": identity.run_id,
        "run_attempt": identity.run_attempt, "pr": identity.pr, "verdict": "fail",
        "base_sha": "b" * 40, "diff_digest": "d" * 64,
        "policy_version": "policy-v1", "policy_digest": "e" * 64, "tier": "saas",
        "caller_sha": "c" * 40, "reusable_workflow_sha": "w" * 40,
        "registry_commit": "r" * 40, "job_id": 100, "reviewer": "test-reviewer",
        "shadow_mode": "detached", "expected_shadows": [], "attempts": [],
        "cost": 0, "tokens": [], "runtime": None,
        "result": {"verdict": "fail", "summary": "blocking issue", "findings": [finding]},
    }
    scope, missing = agg._convergence_scope_from_audit(audit, identity)
    assert not missing and scope is not None
    audit_path = tmp_path / "saas-audit.json"
    audit_path.write_bytes(json.dumps(audit, indent=2).encode("utf-8") + b"\n")
    output_dir = tmp_path / "saas-receipts"
    command = [
        sys.executable, str(ROOT / ".github/actions/gate-disposition/issue_receipt.py"), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(audit_path),
        "--repository-id", str(identity.repository_id), "--pr-number", str(identity.pr),
        "--head-sha", identity.head_sha, "--finding-id", "p1", "--reason", "tracked",
        "--disposition", "deferred", "--tracking-issue", "#123",
        "--approver", "octocat", "--approver-id", "1",
        "--approved-at", "2026-08-30T12:00:00Z",
        "--scope-json", json.dumps(scope.as_dict(), sort_keys=True),
    ]
    producer = subprocess.run(
        command, capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "GITHUB_RUN_ID": "control-1001", "GITHUB_TRIGGERING_ACTOR": "octocat"},
    )
    assert producer.returncode != 0
    assert "deferred_not_allowed_for_tier" in producer.stderr
    assert not output_dir.exists() or list(output_dir.iterdir()) == []

    digest = conv.canonical_audit_digest(audit)
    receipt = conv.DispositionReceipt(
        schema_version=conv.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="deferred", repository_id=str(identity.repository_id),
        pr_number=identity.pr, epoch=conv.derive_epoch(scope),
        head_sha=identity.head_sha, audit_digest=digest, finding_id="p1",
        finding_key=conv.canonical_finding_key(finding), reason="tracked",
        tracking_issue="#123", approver="octocat", approver_id=1,
        approved_at="2026-08-30T12:00:00Z", triggering_actor="octocat",
        triggering_actor_source="workflow",
    )
    handwritten_path = tmp_path / "handwritten-saas-receipt.json"
    handwritten_path.write_bytes(json.dumps(
        receipt.as_dict(), sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    consumer_receipt = conv.parse_disposition_receipt(json.loads(handwritten_path.read_bytes()))
    outcome = agg.evaluate(
        quality_result="success", primary_result="failure", runner="self",
        is_draft=False, review_expected=True, audit=audit, audit_error=None,
        identity=identity, pr_author="zj1123581321", audit_source_attempt=identity.run_attempt,
        audit_artifact_name="primary-audit-v2-1", scope=scope, audit_digest=digest,
        waiver_receipts=(consumer_receipt,), repository="zlxlabs/gate",
    )
    assert outcome.gate_result == "fail"
    terminal = agg.build_terminal_envelope(
        repository="zlxlabs/gate", identity=identity, quality_result="success",
        primary_result="failure", review_expected=True, is_draft=False, runner="self",
        outcome=outcome,
    )
    assert terminal["gate_result"] == "fail"
    assert terminal["disposition_receipt_consumption"]["rejected_reasons"] == {
        "deferred_not_allowed_for_tier": 1,
    }


def _write_terminal(tmp_path, payload):
    path = tmp_path / "gate-terminal.json"
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_missing_or_empty_terminal_file_is_fail_loud_not_empty_consumption(tmp_path):
    module = _module()
    missing = tmp_path / "missing" / "gate-terminal.json"
    empty = tmp_path / "gate-terminal.json"
    empty.write_text("")
    with pytest.raises(ValueError, match="missing or empty"):
        module.load_gate_terminal_envelope(missing)
    with pytest.raises(ValueError, match="missing or empty"):
        module.load_gate_terminal_envelope(empty)


def test_corrupt_terminal_json_is_fail_loud_not_empty_consumption(tmp_path):
    module = _module()
    path = _write_terminal(tmp_path, "{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        module.load_gate_terminal_envelope(path)


def test_terminal_array_payload_is_fail_loud(tmp_path):
    module = _module()
    path = _write_terminal(tmp_path, "[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        module.load_gate_terminal_envelope(path)


def test_missing_consumption_block_is_fail_loud_not_empty_default():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    del terminal["disposition_receipt_consumption"]
    with pytest.raises(ValueError, match="missing disposition_receipt_consumption"):
        module.build_entry(
            repository=terminal["repository"],
            pr_number=terminal["pr_number"],
            run_id=terminal["run_id"],
            run_attempt=terminal["run_attempt"],
            head_sha=terminal["head_sha"],
            preflight={},
            audit=None,
            terminal_envelope=terminal,
        )


@pytest.mark.parametrize("kind, match", [
    ("resolved_not_array", "resolved must be an array"),
    ("missing_receipt", "missing receipt"),
    ("approver_id", "approver_id must be a positive integer"),
    ("consumed_count", "consumed_count does not match resolved"),
    ("rejected_count", "rejected_count does not match rejected_reasons"),
    ("fail_closed", "fail_closed must be a boolean"),
])
def test_validator_rejects_malformed_consumption_shapes(kind, match):
    module = _module()
    *_rest, terminal = _producer_terminal(receipts=[{}])
    block = json.loads(json.dumps(terminal["disposition_receipt_consumption"]))
    if kind == "resolved_not_array":
        block["resolved"] = {}
    elif kind in {"missing_receipt", "approver_id", "consumed_count"}:
        block["resolved"] = [{
            "finding_id": "p1", "receipt": "artifact", "approver": "owner",
            "approver_id": 1, "approved_at": "2026-01-01T00:00:00Z", "reason": "test",
        }]
        block["consumed_count"] = 1
        if kind == "missing_receipt":
            del block["resolved"][0]["receipt"]
        elif kind == "approver_id":
            block["resolved"][0]["approver_id"] = 0
        else:
            block["consumed_count"] = 0
    elif kind == "rejected_count":
        block["rejected_count"] = 3
    else:
        block["fail_closed"] = "false"
    with pytest.raises(ValueError, match=match):
        module.validate_disposition_receipt_audit(block)


def test_malformed_audit_block_is_fail_loud():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    terminal["disposition_receipt_consumption"]["consumed_count"] = "1"
    with pytest.raises(ValueError, match="consumed_count"):
        module.build_entry(
            repository=terminal["repository"],
            pr_number=terminal["pr_number"],
            run_id=terminal["run_id"],
            run_attempt=terminal["run_attempt"],
            head_sha=terminal["head_sha"],
            preflight={},
            audit=None,
            terminal_envelope=terminal,
        )


def test_terminal_identity_mismatch_is_fail_loud():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    with pytest.raises(ValueError, match="identity mismatch"):
        module.build_entry(
            repository=terminal["repository"],
            pr_number=terminal["pr_number"],
            run_id=terminal["run_id"] + 1,
            run_attempt=terminal["run_attempt"],
            head_sha=terminal["head_sha"],
            preflight={},
            audit=None,
            terminal_envelope=terminal,
        )


def _build_from_terminal(module, terminal, **overrides):
    kwargs = dict(
        repository=terminal["repository"],
        pr_number=terminal["pr_number"],
        run_id=terminal["run_id"],
        run_attempt=terminal["run_attempt"],
        head_sha=terminal["head_sha"],
        preflight={},
        audit=None,
        terminal_envelope=terminal,
    )
    kwargs.update(overrides)
    return module.build_entry(**kwargs)


_SAME_ATTEMPT_TERMINAL_ENTRY_KEYS = {
    "schema_version", "recorded_at", "repository", "pr_number", "run_id",
    "run_attempt", "head_sha", "disposition_status",
    "preflight", "install",
    "primary_identity", "review", "finding_dispositions", "false_positive_count",
    "disposition_receipt_consumption",
}


def test_prior_attempt_terminal_from_producer_is_accepted():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}], run_attempt=1)
    assert terminal["run_attempt"] == 1
    entry = _build_from_terminal(module, terminal, run_attempt=2)
    assert entry["disposition_receipt_consumption"] == terminal["disposition_receipt_consumption"]


def test_same_attempt_terminal_entry_keys_do_not_add_source_attempt():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    entry = _build_from_terminal(module, terminal)
    assert "terminal_source_attempt" not in entry
    assert set(entry) == _SAME_ATTEMPT_TERMINAL_ENTRY_KEYS


def test_prior_attempt_terminal_entry_records_source_attempt():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}], run_attempt=1)
    entry = _build_from_terminal(module, terminal, run_attempt=2)
    assert entry["terminal_source_attempt"] == 1
    assert set(entry) == _SAME_ATTEMPT_TERMINAL_ENTRY_KEYS | {"terminal_source_attempt"}


def test_future_attempt_terminal_from_producer_is_rejected():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(run_attempt=3)
    assert terminal["run_attempt"] == 3
    with pytest.raises(ValueError, match="gate terminal identity mismatch"):
        _build_from_terminal(module, terminal, run_attempt=2)


@pytest.mark.parametrize("bad_attempt", [0, -1, 1.5, "1", True, False, None])
def test_invalid_terminal_run_attempt_is_rejected(bad_attempt):
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    terminal["run_attempt"] = bad_attempt
    with pytest.raises(ValueError, match="gate terminal identity mismatch"):
        _build_from_terminal(module, terminal, run_attempt=1)


@pytest.mark.parametrize("field, value", [
    ("repository", "other/repo"),
    ("pr_number", 99),
    ("run_id", 1),
    ("head_sha", "b" * 40),
])
def test_terminal_identity_still_requires_exact_non_attempt_fields(field, value):
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    with pytest.raises(ValueError, match="gate terminal identity mismatch"):
        _build_from_terminal(module, terminal, **{field: value})


def test_unsupported_terminal_schema_is_fail_loud(tmp_path):
    module = _module()
    path = _write_terminal(tmp_path, {"schema_version": 2, "kind": "gate_terminal"})
    with pytest.raises(ValueError, match="unsupported schema"):
        module.load_gate_terminal_envelope(path)


def _relation_block(relation, previous_id=None):
    item = {"id": "f1", "relation_to_previous": relation, "file": "a.py", "line": 1}
    if previous_id:
        item["previous_finding_id"] = previous_id
    counts = {"new": 0, "repeat": 0, "conflict": 0}
    counts[relation] = 1
    return {
        "schema_version": 1,
        "counts": counts,
        "review_terminal": "manual_required" if relation == "conflict" else "unchanged",
        "items": [item],
    }


def test_ledger_copies_finding_relation_counts():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    terminal["finding_relation"] = _relation_block("repeat", "f1")
    terminal["finding_relation"]["history_status"] = "available"
    terminal["finding_relation"]["open_findings"] = [{
        "id": "f1", "quote": "raw quote", "anchor": {"path": "a.py", "line": 1},
    }]
    entry = _build_from_terminal(module, terminal)
    assert entry["finding_relation"]["counts"] == {"new": 0, "repeat": 1, "conflict": 0}
    assert entry["finding_relation"]["history_status"] == "available"
    assert entry["finding_relation"]["open_findings"] == terminal["finding_relation"]["open_findings"]


def test_ledger_rejects_unknown_finding_relation():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    block = _relation_block("new")
    block["items"][0]["relation_to_previous"] = "maybe"
    terminal["finding_relation"] = block
    with pytest.raises(ValueError, match="GATE-FINDING-RELATION-UNKNOWN"):
        _build_from_terminal(module, terminal)


def test_ledger_rejects_non_object_open_finding():
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal()
    terminal["finding_relation"] = _relation_block("new")
    terminal["finding_relation"]["open_findings"] = ["not-a-finding"]
    with pytest.raises(ValueError, match="open_findings"):
        _build_from_terminal(module, terminal)
