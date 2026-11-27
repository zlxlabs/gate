"""Unit tests for the Required Gate v2 aggregator's decision core
(.github/actions/gate-aggregator/aggregate.py).

Judgement matrix coverage (needs.* result x audit-state), per the security
tightening pass (deep-review + codex merged fix card, 2026-07-26):
quality fail, primary fail, audit missing, audit identity mismatch (incl.
strict-type confusion: bool-as-int, str-as-int, non-dict top-level JSON),
draft+skipped, non-draft+skipped, not_expected/waived UNCONDITIONAL
rejection (T6 not wired), synthetic-audit generation, pass-verdict requires
primary_result == success, primary_result/runner domain validation, and
strict as_bool parsing.
"""
import ast
import builtins
import hashlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
ABANDONED_FIXTURE = ROOT / "tests" / "fixtures" / "primary-abandoned-run-34740209146.json"
CONVERGENCE_HISTORY_FIXTURE = ROOT / "tests" / "fixtures" / "convergence_history" / "gate-hub-pr-928.json"


from _gate_aggregator_support import _assert_terminal_classification, _module












AGG = _module()

IDENTITY = AGG.Identity(repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=1, pr=42)
# Captured from REST PR #252's real `user.login` field; this is a PR object
# fixture, not an Actions event or Dependabot run.
PR_AUTHOR_REST_FIXTURE = {"user": {"login": "zj1123581321"}}


def _cli_args(audit_dir, summary_path, **overrides):
    identity = overrides.pop("identity", IDENTITY)
    values = dict(
        quality_result="success",
        primary_result="success",
        preflight_result="success",
        runner="self",
        is_draft="false",
        is_fork="false",
        classify_review_expected="true",
        review_expected="true",
        repository_id=str(identity.repository_id),
        head_sha=identity.head_sha,
        run_id=str(identity.run_id),
        run_attempt=str(identity.run_attempt),
        pr_number=str(identity.pr), repository="zlxlabs/gate",
        pr_author_json=json.dumps(PR_AUTHOR_REST_FIXTURE["user"]["login"]),
        audit_source_attempt=str(identity.run_attempt), audit_artifact_name="primary-audit-v2-1", terminal_path=str(Path(summary_path).with_name("gate-terminal.json")),
    )
    values.update(overrides)
    args = [
        "--quality-result", values["quality_result"],
        "--primary-result", values["primary_result"],
        "--runner", values["runner"],
        "--is-draft", values["is_draft"],
        "--is-fork", values["is_fork"],
        "--classify-review-expected", values["classify_review_expected"],
        "--review-expected", values["review_expected"],
        "--repository-id", values["repository_id"], "--repository", values["repository"],
        "--head-sha", values["head_sha"],
        "--run-id", values["run_id"],
        "--run-attempt", values["run_attempt"],
        "--audit-artifact-name", values["audit_artifact_name"], "--terminal-path", values["terminal_path"],
        "--audit-dir", str(audit_dir),
        "--summary-path", str(summary_path),
    ]
    if values.get("pr_author_json") is not None:
        args.extend(["--pr-author-json", values["pr_author_json"]])
    if values.get("pr_number") is not None:
        args.extend(["--pr-number", values["pr_number"]])
    if "audit_source_attempt" in values:
        args.extend(["--audit-source-attempt", values["audit_source_attempt"]])
    if values.get("panel_delivery_path") is not None:
        args.extend(["--panel-delivery-path", values["panel_delivery_path"]])
    if values.get("caller_checks") is not None:
        args.extend(["--caller-checks", values["caller_checks"]])
    if values.get("caller_signal") is not None:
        args.extend(["--caller-signal", values["caller_signal"]])
    if values.get("preflight_result") is not None:
        args.extend(["--preflight-result", values["preflight_result"]])
    if values.get("convergence_receipt_path") is not None:
        args.extend(["--convergence-receipt-path", values["convergence_receipt_path"]])
    return args




def test_single_round_gate_outcome_is_not_convergence_state():
    outcome = AGG.evaluate(
        quality_result="success",
        primary_result="success",
        preflight_result="success",
        runner="self",
        is_draft=False,
        is_fork=False,
        classify_review_expected="true",
        review_expected=True,
        audit={
            **_valid_primary_record(),
            "result": {"findings": []},
        },
        audit_error=None,
        identity=IDENTITY,
        pr_author=PR_AUTHOR_REST_FIXTURE["user"]["login"],
        audit_source_attempt=1,
        audit_artifact_name="primary-audit-v2-1",
    )
    assert outcome.gate_result == "pass"
    assert not hasattr(outcome, "clean_streak")
    assert not hasattr(outcome, "epoch")
    assert outcome.convergence_envelope is None


def _valid_primary_record(**overrides):
    record = {
        "kind": "primary_review",
        "schema_version": 1,
        "repository_id": IDENTITY.repository_id,
        "head_sha": IDENTITY.head_sha,
        "run_id": IDENTITY.run_id,
        "run_attempt": IDENTITY.run_attempt,
        "pr": IDENTITY.pr,
        "verdict": "pass",
        "reviewer": "claude-glm",
    }
    record.update(overrides)
    return record


def _valid_scoped_primary_record(**overrides):
    record = {
        **_valid_primary_record(),
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "p" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
        "result": {"findings": []},
    }
    record.update(overrides)
    return record


def test_convergence_scope_contract_has_six_required_audit_fields():
    assert AGG._CONVERGENCE_SCOPE_FIELDS == (
        "base_sha", "diff_digest", "policy_version", "policy_digest",
        "caller_sha", "reusable_workflow_sha",
    )


def _base_kwargs(**overrides):
    kwargs = dict(
        quality_result="success",
        primary_result="success",
        preflight_result="success",
        runner="self",
        is_draft=False,
        review_expected=True,
        audit=_valid_primary_record(),
        audit_error=None,
        identity=IDENTITY, pr_author=PR_AUTHOR_REST_FIXTURE["user"]["login"],
        audit_source_attempt=IDENTITY.run_attempt, audit_artifact_name="primary-audit-v2-1",
    )
    kwargs.update(overrides)
    return kwargs


# ── quality ──────────────────────────────────────────────────────────────

def test_quality_failure_fails_the_gate_even_when_primary_passes():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure"))
    assert outcome.ok is False
    assert any("quality" in p for p in outcome.problems)


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_quality_any_non_success_fails(result):
    outcome = AGG.evaluate(**_base_kwargs(quality_result=result))
    assert outcome.ok is False


# ── primary: fail/unavailable verdicts ──────────────────────────────────

@pytest.mark.parametrize("verdict", ["fail", "unavailable"])
def test_primary_fail_or_unavailable_verdict_fails_the_gate(verdict):
    outcome = AGG.evaluate(
        **_base_kwargs(
            primary_result="failure",
            audit=_valid_primary_record(verdict=verdict),
        )
    )
    assert outcome.ok is False
    assert any(verdict in p for p in outcome.problems)
    assert outcome.synthetic_audit is None  # a valid audit exists — no synthetic needed


def test_primary_pass_with_inconsistent_job_result_fails_closed():
    # job result says failure/cancelled but the audit claims pass — never trust
    # the audit over the job's own conclusion.
    outcome = AGG.evaluate(**_base_kwargs(primary_result="failure", audit=_valid_primary_record(verdict="pass")))
    assert outcome.ok is False


def test_quality_success_primary_failure_with_a_legitimate_pass_audit_still_fails():
    # Explicit regression for the "passing verdict requires primary_result ==
    # success" rule: even a structurally perfect, identity-matched, verdict
    # 'pass' audit must NOT be trusted over a job result of 'failure'.
    outcome = AGG.evaluate(
        **_base_kwargs(
            quality_result="success",
            primary_result="failure",
            audit=_valid_primary_record(verdict="pass"),
        )
    )
    assert outcome.ok is False
    assert any("inconsistent" in p for p in outcome.problems)


# ── primary_result / runner domain validation ────────────────────────────

@pytest.mark.parametrize("bogus_result", ["succes", "FAILURE", "", "timed_out", "neutral"])
def test_primary_result_outside_known_domain_is_rejected(bogus_result):
    outcome = AGG.evaluate(**_base_kwargs(primary_result=bogus_result, audit=None, audit_error=None))
    assert outcome.ok is False
    assert any("not a recognized value" in p for p in outcome.problems)


@pytest.mark.parametrize("bogus_runner", ["slef", "Self", "HOSTED", "", "selfhosted"])
def test_runner_outside_self_hosted_domain_is_rejected_even_if_everything_else_passes(bogus_runner):
    outcome = AGG.evaluate(**_base_kwargs(runner=bogus_runner))
    assert outcome.ok is False
    assert any("runner input" in p for p in outcome.problems)


@pytest.mark.parametrize("runner", ["self", "hosted"])
def test_runner_valid_values_do_not_by_themselves_fail(runner):
    outcome = AGG.evaluate(**_base_kwargs(runner=runner))
    assert not any("runner input" in p for p in outcome.problems)


# ── primary: audit missing / corrupt ─────────────────────────────────────

def test_audit_missing_generates_synthetic_artifact_missing_and_fails():
    outcome = AGG.evaluate(**_base_kwargs(audit=None, audit_error="no *.json file found"))
    assert outcome.ok is False
    assert outcome.synthetic_audit is not None
    assert outcome.synthetic_audit["status"] == "artifact_missing"
    assert outcome.synthetic_audit["kind"] == "synthetic_primary"


def test_primary_cancelled_generates_synthetic_job_timed_out():
    outcome = AGG.evaluate(**_base_kwargs(primary_result="cancelled", audit=None, audit_error=None))
    assert outcome.ok is False
    assert outcome.synthetic_audit["status"] == "job_timed_out"


# ── primary: audit identity mismatch (value) ─────────────────────────────

@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("repository_id", 999),
        ("head_sha", "b" * 40),
        ("run_id", 111),
        ("run_attempt", 2),
        ("pr", 7),
    ],
)
def test_audit_identity_mismatch_on_any_quintuple_field_fails_and_generates_synthetic(field, bad_value):
    bad_audit = _valid_primary_record(**{field: bad_value})
    outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
    assert outcome.ok is False
    assert outcome.synthetic_audit is not None
    assert outcome.synthetic_audit["status"] == "artifact_missing"
    assert any("identity mismatch" in p for p in outcome.problems)


def test_cross_attempt_audit_from_earlier_run_attempt_is_accepted_and_traced():
    identity = AGG.Identity(repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=2, pr=42)
    audit = {
        **_valid_primary_record(),
        "run_attempt": 1,
    }
    outcome = AGG.evaluate(**_base_kwargs(identity=identity, audit=audit))
    assert outcome.ok is True
    assert any("source run_attempt=1" in note for note in outcome.notes)


def test_audit_from_future_run_attempt_is_rejected():
    identity = AGG.Identity(repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=2, pr=42)
    audit = {
        **_valid_primary_record(),
        "run_attempt": 3,
    }
    outcome = AGG.evaluate(**_base_kwargs(identity=identity, audit=audit))
    assert outcome.ok is False
    assert any("exceeds current run_attempt" in problem for problem in outcome.problems)


def test_audit_wrong_kind_is_rejected():
    bad_audit = _valid_primary_record(kind="synthetic_primary")
    outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
    assert outcome.ok is False
    assert any("unexpected audit kind" in p for p in outcome.problems)


def test_audit_verdict_outside_domain_is_rejected():
    bad_audit = _valid_primary_record(verdict="banana")
    outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
    assert outcome.ok is False


# ── strict type validation: bool-as-int, str-as-int, non-dict top level ──

@pytest.mark.parametrize("field", ["repository_id", "run_id", "run_attempt", "pr"])
def test_bool_masquerading_as_int_identity_field_is_rejected(field):
    # Python's bool is an int subclass — isinstance(True, int) is True — so a
    # naive `isinstance(x, int)` check would silently accept `run_attempt:
    # true`. This must be rejected explicitly.
    bad_audit = _valid_primary_record(**{field: True})
    outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
    assert outcome.ok is False
    assert any("genuine int" in p for p in outcome.problems)


@pytest.mark.parametrize("field", ["repository_id", "run_id", "run_attempt", "pr"])
def test_str_masquerading_as_int_identity_field_is_rejected(field):
    expected_value = getattr(IDENTITY, field)
    bad_audit = _valid_primary_record(**{field: str(expected_value)})
    outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
    assert outcome.ok is False
    assert any("genuine int" in p for p in outcome.problems)


def test_schema_version_accepts_primary_v1_and_v2_but_rejects_unknown_or_untyped():
    for bogus in (True, "1", 1.0, 99, None):
        bad_audit = _valid_primary_record(schema_version=bogus)
        outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
        assert outcome.ok is False, f"schema_version={bogus!r} should have been rejected"
        assert any("schema_version" in p for p in outcome.problems)
    for version in (1, 2):
        audit = _valid_primary_record(schema_version=version)
        outcome = AGG.evaluate(**_base_kwargs(audit=audit))
        assert outcome.ok is True

@pytest.mark.parametrize("bogus_top_level", [[1, 2, 3], "hello", 42, True, None])
def test_non_dict_top_level_audit_payload_is_rejected(bogus_top_level):
    outcome = AGG.evaluate(**_base_kwargs(audit=bogus_top_level))
    assert outcome.ok is False
    assert outcome.synthetic_audit is not None
    assert outcome.synthetic_audit["status"] == "artifact_missing"


def test_validate_audit_identity_directly_on_non_dict_payloads():
    for bogus in ([1, 2, 3], "hello", 42, True, None):
        errors = AGG.validate_audit_identity(bogus, IDENTITY)
        assert errors, f"{bogus!r} should have produced at least one error"


@pytest.mark.parametrize(
    "verdict,reviewer",
    [("pass", "reviewer"), ("fail", "reviewer"), ("unavailable", "reviewer"),
     ("not_expected", None), ("waived", None)],
)
def test_audit_identity_accepts_every_primary_verdict_domain_value(verdict, reviewer):
    audit = _valid_primary_record(verdict=verdict, reviewer=reviewer)

    assert not AGG.validate_audit_identity(audit, IDENTITY)


@pytest.mark.parametrize("verdict", ["pass", "fail", "unavailable"])
def test_reviewer_required_non_empty_string_for_verdict_bearing_records(verdict):
    for bogus_reviewer in (None, "", 123, True):
        bad_audit = _valid_primary_record(verdict=verdict, reviewer=bogus_reviewer)
        errors = AGG.validate_audit_identity(bad_audit, IDENTITY)
        assert any("reviewer" in e for e in errors), f"reviewer={bogus_reviewer!r} should have failed for {verdict}"


# ── draft / skip acceptance ───────────────────────────────────────────────

def test_draft_pr_with_skipped_primary_and_successful_quality_passes():
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="skipped", is_draft=True, review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is True
    assert outcome.synthetic_audit is None


# ── not_expected / waived: UNCONDITIONAL rejection (T6 not wired) ────────

def test_not_expected_audit_is_rejected_even_with_a_plausible_reason_string():
    # Regression for the deep-review finding: previously a not_expected_reason
    # of any non-empty string (including a nonsensical one) was accepted as
    # long as it was present. Canary-stage primary never legitimately writes
    # not_expected at all, so the WHOLE verdict must now be rejected outright
    # — a plausible-looking companion field must not launder it through.
    audit = _valid_primary_record(verdict="not_expected", reviewer=None, not_expected_reason="banana")
    outcome = AGG.evaluate(**_base_kwargs(audit=audit))
    assert outcome.ok is False
    assert any("not accepted" in p for p in outcome.problems)


def test_not_expected_audit_is_rejected_even_with_a_recognized_reason_value():
    # Even a *real* NOT_EXPECTED_REASONS-domain value ("fork") must still be
    # rejected in this PR — acceptance requires a real writer AND real
    # companion-field policy validation (T6), neither of which exists yet.
    audit = _valid_primary_record(verdict="not_expected", reviewer=None, not_expected_reason="fork")
    outcome = AGG.evaluate(**_base_kwargs(audit=audit))
    assert outcome.ok is False


def test_waived_audit_is_rejected_even_with_a_well_formed_waiver_object():
    audit = _valid_primary_record(
        verdict="waived",
        reviewer=None,
        waiver={"approver": "octocat", "approved_at": "2026-07-24T10:00:00Z", "reason": "flaky infra"},
    )
    outcome = AGG.evaluate(**_base_kwargs(audit=audit))
    assert outcome.ok is False
    assert any("not accepted" in p for p in outcome.problems)


def test_not_expected_and_waived_never_produce_a_synthetic_audit():
    # A structurally-valid-but-business-rejected record is real evidence on
    # its own; the aggregator must not paper over it with a synthetic one.
    for verdict, extra in (
        ("not_expected", {"not_expected_reason": "fork"}),
        ("waived", {"waiver": {"approver": "x", "approved_at": "2026-07-24T10:00:00Z", "reason": "y"}}),
    ):
        audit = _valid_primary_record(verdict=verdict, reviewer=None, **extra)
        outcome = AGG.evaluate(**_base_kwargs(audit=audit))
        assert outcome.ok is False
        assert outcome.synthetic_audit is None and (outcome.audit_available, outcome.audit_source_attempt, outcome.audit_artifact_name) == (False, None, None)


# ── build_synthetic_audit shape ───────────────────────────────────────────

def test_build_synthetic_audit_shape_matches_contracts_build_synthetic_primary():
    record = AGG.build_synthetic_audit(identity=IDENTITY, status="artifact_missing", reason="test reason")
    assert record["kind"] == "synthetic_primary"
    assert record["schema_version"] == 1
    assert record["status"] == "artifact_missing"
    assert record["reason"] == "test reason"
    assert record["verdict"] is None
    assert record["attempts"] == []
    assert record["shadow_mode"] == "detached"
    assert record["expected_shadows"] == []
    for key in ("repository_id", "head_sha", "run_id", "run_attempt", "pr"):
        assert key in record


def test_build_synthetic_audit_rejects_unknown_status():
    with pytest.raises(ValueError):
        AGG.build_synthetic_audit(identity=IDENTITY, status="bogus", reason="x")


# ── as_bool strict parsing ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("True", True), (" TRUE ", True), ("false", False), ("False", False), (" FALSE ", False)],
)
def test_as_bool_accepts_only_true_false_case_and_whitespace_insensitive(value, expected):
    assert AGG.as_bool(value) is expected


@pytest.mark.parametrize("bogus", ["banana", "", "1", "0", "yes", "no", "null", "None"])
def test_as_bool_raises_on_anything_else_instead_of_defaulting_to_false(bogus):
    with pytest.raises(AGG.BoolParseError):
        AGG.as_bool(bogus)


# ── _read_audit_file IO edge cases ───────────────────────────────────────

def test_read_audit_file_missing_directory(tmp_path):
    record, error, raw = AGG._read_audit_file(tmp_path / "does-not-exist")
    assert record is None
    assert "not present" in error
    assert raw is None


def test_read_audit_file_empty_directory(tmp_path):
    record, error, raw = AGG._read_audit_file(tmp_path)
    assert record is None
    assert "no *.json" in error
    assert raw is None


def test_read_audit_file_multiple_files(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")
    record, error, raw = AGG._read_audit_file(tmp_path)
    assert record is None
    assert "found 2" in error
    assert raw is None


def test_read_audit_file_invalid_json(tmp_path):
    (tmp_path / "a.json").write_text("{not valid json")
    record, error, raw = AGG._read_audit_file(tmp_path)
    assert record is None
    assert "could not parse" in error
    assert raw is None


def test_read_audit_file_valid_json_roundtrips(tmp_path):
    payload = _valid_primary_record()
    raw_payload = json.dumps(payload).encode()
    (tmp_path / "primary-review-audit.json").write_bytes(raw_payload)
    record, error, raw = AGG._read_audit_file(tmp_path)
    assert error is None
    assert record == payload
    assert raw == raw_payload


def test_read_audit_file_non_dict_json_roundtrips_as_is(tmp_path):
    # _read_audit_file itself doesn't judge shape — that's evaluate()'s job.
    (tmp_path / "weird.json").write_text(json.dumps([1, 2, 3]))
    record, error, raw = AGG._read_audit_file(tmp_path)
    assert error is None
    assert record == [1, 2, 3]
    assert raw == b"[1, 2, 3]"


# ── CLI end-to-end (exit codes + step summary) ───────────────────────────






def test_observed_abandoned_fixture_produces_fail_terminal_and_unreviewed_ledger_row(tmp_path, monkeypatch):
    fixture = json.loads(ABANDONED_FIXTURE.read_text(encoding="utf-8"))
    observed = fixture["observed_env"]
    source = fixture["identity"]
    identity = AGG.Identity(
        repository_id=source["repository_id"], head_sha=source["head_sha"],
        run_id=source["run_id"], run_attempt=source["run_attempt"], pr=source["pr_number"],
    )
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(
        tmp_path / "missing-audit",
        summary_path,
        identity=identity,
        repository=source["repository"],
        quality_result=observed["QUALITY_RESULT"],
        primary_result="cancelled",
        review_expected=observed["REVIEW_EXPECTED"],
    ))
    assert observed["PRIMARY_RESULT"] == "abandoned"
    assert rc == 1
    terminal_path = summary_path.with_name("gate-terminal.json")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal["primary_result"] == "cancelled"
    assert terminal["gate_result"] == "unavailable"  # existing cancelled fail-closed terminal state
    assert terminal["reason_code"] == "primary_cancelled"

    ledger_spec = importlib.util.spec_from_file_location(
        "review_ledger_for_abandoned", ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py",
    )
    ledger = importlib.util.module_from_spec(ledger_spec)
    assert ledger_spec.loader
    ledger_spec.loader.exec_module(ledger)
    preflight = tmp_path / "pr-size-preflight.json"
    install = tmp_path / "install-result.json"
    output = tmp_path / "ledger.jsonl"
    preflight.write_text(json.dumps({"reviewable": True}), encoding="utf-8")
    install.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "build_ledger.py", "--audit-path", str(tmp_path / "missing-audit.json"),
        "--preflight-path", str(preflight), "--install-path", str(install),
        "--terminal-path", str(terminal_path), "--output", str(output),
        "--repository", source["repository"], "--pr-number", str(source["pr_number"]),
        "--run-id", str(source["run_id"]), "--run-attempt", str(source["run_attempt"]),
        "--head-sha", source["head_sha"], "--expected-repository-id", str(source["repository_id"]),
        "--expected-base-sha", "redacted-base", "--expected-caller-sha", "redacted-caller",
        "--expected-reusable-workflow-sha", "redacted-workflow", "--codex-expected", "true",
    ])
    assert ledger.main() == 0
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert row["schema_version"] == 2
    assert row["review"]["status"] == "not_run"
    assert row["review"]["verdict"] is None
    assert row["review"]["result"] is None




































@pytest.mark.parametrize("kwargs,expected", [
    ({"quality_result": "failure", "caller_checks": "failed"}, ("ci_failure", "quality_failure", "fail")), ({"quality_result": "cancelled"}, ("ci_failure", "quality_cancelled", "fail")),
    ({"quality_result": "skipped"}, ("ci_failure", "quality_skipped", "fail")), ({"primary_result": "skipped", "is_draft": True, "review_expected": False, "audit": None}, ("expected_skip", "review_not_expected", "skipped")),
    ({"primary_result": "skipped", "is_draft": False, "is_fork": False, "classify_review_expected": "false", "review_expected": False, "audit": None}, ("expected_skip", "review_not_expected", "skipped")),
    ({"pr_author": "dependabot[bot]", "primary_result": "skipped", "review_expected": False, "classify_review_expected": "false", "audit": None}, ("review_unavailable", "primary_unavailable", "unavailable")),
    ({"pr_author": "dependabot[bot]", "primary_result": "success", "review_expected": False, "classify_review_expected": "false", "audit": _valid_primary_record()}, ("review_unavailable", "primary_unavailable", "unavailable")),
    ({}, ("code_pass", "primary_pass", "pass")), ({"primary_result": "failure", "audit": _valid_primary_record(verdict="fail")}, ("code_fail", "primary_findings", "fail")),
    # gate#105 方案 A: quality short-circuited by a failed primary keeps primary's classification.
    ({"quality_result": "skipped", "primary_result": "failure", "audit": _valid_primary_record(verdict="fail")}, ("code_fail", "primary_findings", "fail")),
    ({"primary_result": "failure", "audit": _valid_primary_record(verdict="unavailable")}, ("review_unavailable", "primary_unavailable", "unavailable")), ({"primary_result": "cancelled", "audit": None}, ("review_unavailable", "primary_cancelled", "unavailable")),
    ({"primary_result": "skipped", "audit": None}, ("integration_error", "unexpected_primary_skip", "unavailable")), ({"audit": None, "audit_error": "missing"}, ("integration_error", "audit_missing", "unavailable")),
    ({"audit": _valid_primary_record(kind="synthetic_primary")}, ("integration_error", "audit_invalid", "unavailable")), ({"audit_source_attempt": 2}, ("integration_error", "audit_source_mismatch", "unavailable")),
    ({"primary_result": "failure", "audit": _valid_primary_record(verdict="pass")}, ("integration_error", "job_audit_mismatch", "unavailable")), ({"quality_result": "failure", "audit": None, "audit_error": "missing"}, ("integration_error", "audit_missing", "unavailable")),
    ({"primary_result": "success", "audit": _valid_primary_record(verdict="fail")}, ("integration_error", "job_audit_mismatch", "unavailable")), ({"primary_result": "success", "audit": _valid_primary_record(verdict="unavailable")}, ("integration_error", "job_audit_mismatch", "unavailable")),
    ({"audit_source_attempt": None}, ("integration_error", "audit_source_mismatch", "unavailable")), ({"audit_artifact_name": ""}, ("integration_error", "audit_source_mismatch", "unavailable")), ({"audit_artifact_name": None}, ("integration_error", "audit_source_mismatch", "unavailable")),
])
def test_terminal_classification_matrix(kwargs, expected):
    _assert_terminal_classification(AGG.evaluate(**_base_kwargs(**kwargs)), expected)
























# ── visible terminal state (issues #32/#43): classification x output slot ──
#
# Axis table: every classification must render its four-state gate_result on
# the Step Summary top line, expose classification/reason_code/gate_result in
# the Summary body, and emit a same-typed annotation carrying reason_code —
# while the exit code stays exactly what the pass/fail semantics dictated
# before this rendering existed (expected_skip stays 0/green).

_TERMINAL_GOLDEN = """{
  "schema_version": 1,
  "kind": "gate_terminal",
  "repository": "zlxlabs/gate",
  "repository_id": 123,
  "pr_number": 42,
  "run_id": 999,
  "run_attempt": 1,
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "quality_result": "success",
  "primary_result": "success",
  "skip_reason": null,
  "review_expected": true,
  "is_draft": false,
  "runner": "self",
  "gate_result": "pass",
  "classification": "code_pass",
  "reason_code": "primary_pass",
  "audit": {
    "available": true,
    "source_attempt": 1,
    "artifact_name": "primary-audit-v2-1"
  },
  "disposition_receipt_consumption": {
    "recorded": [],
    "resolved": [],
    "consumed_count": 0,
    "rejected_count": 0,
    "rejected_reasons": {},
    "fail_closed": false,
    "remaining_p1_ids": []
  }
}
"""






















# ── wording axis (issue #43): unreadable-conclusion ≠ reviewer rejection ────








# ── human-first action line: every gate_result answers "what do I do now" ────
#
# Axis tables (task card, P2-1/P2-2/P3-1/P3-2):
#   axis 1 — every gate_result (pass/fail/skipped-draft/skipped-hosted/
#     unavailable) carries an ACTION SENTENCE telling the recipient what to do
#     now; fail and unavailable also carry the direct run URL built from the
#     identity quintuple (repository + run_id), never a new CLI/env input.
#   axis 2 — ordering: `**Result:` stays the first verdict line, the action
#     sentence comes BEFORE the `Terminal state:` machine codes
#     (classification= may never appear ahead of the human sentence), and a
#     primary-fail summary must not point at the Problems list for findings
#     it does not contain.

_RUN_URL = "https://github.com/zlxlabs/gate/actions/runs/999"






# ── Status panel sticky delivery and durable-history contract ────────────────

from _gate_aggregator_support import _FakeResponse


def _panel_terminal_row(run_id, attempt, gate_result, head_sha=None):
    return _panel_row(run_id, attempt, gate_result, head_sha=head_sha)


def _publish_only_panel_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    audit_record: dict[str, object],
    *,
    remove_audit_before_publish: bool = False,
) -> tuple[str, dict[str, object]]:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit_path = audit_dir / "primary-review-audit.json"
    audit_bytes = json.dumps(audit_record, ensure_ascii=False).encode("utf-8")
    audit_path.write_bytes(audit_bytes)
    summary_path = tmp_path / "summary.md"
    terminal_path = tmp_path / "gate-terminal.json"
    primary_result = "failure" if audit_record["verdict"] == "fail" else "success"
    assert AGG.main(_cli_args(
        audit_dir, summary_path, primary_result=primary_result, terminal_path=str(terminal_path),
    )) == (1 if primary_result == "failure" else 0)
    terminal_record = json.loads(terminal_path.read_bytes())
    if remove_audit_before_publish:
        audit_path.unlink()

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    owner = {"id": 99, "login": "workflow-bot"}
    comments = []
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: comments.append(kwargs["body"]))
    publish_args = _cli_args(
        audit_dir, summary_path, primary_result=primary_result,
        terminal_path=str(terminal_path), panel_delivery_path=str(tmp_path / "delivery.json"),
    ) + ["--publish-only"]
    assert AGG.main(publish_args) == 0
    assert len(comments) == 1
    if remove_audit_before_publish:
        assert not audit_path.exists()
    else:
        assert audit_path.read_bytes() == audit_bytes
    row = AGG._terminal_row(
        terminal_record, repository="zlxlabs/gate",
        repository_id=IDENTITY.repository_id, pr_number=IDENTITY.pr,
    )
    return comments[0], row


def test_status_panel_sends_scrubbed_body(monkeypatch):
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv("RUNNER_NAME", "runner-secret")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: {"id": 99, "login": "workflow-bot"})
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "render_status_panel", lambda *args, **kwargs: "runner-secret 10.0.0.1")
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda request, timeout: operations.append(json.loads(request.data.decode())) or _FakeResponse(),
    )

    AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="org/repo", repository_id=123, pr_number=7, identity=IDENTITY,
    )

    assert len(operations) == 1
    body = operations[0]["body"]
    assert "runner-secret" not in body
    assert "10.0.0.1" not in body
    assert "[REDACTED:RUNNER_NAME]" in body
    assert "[REDACTED:PRIVATE_IP]" in body











































































































































# ── false-positive disposition consumption (required verdict) ─────────────

CONV = AGG._CONVERGENCE
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


























































































# ── dual-read GitHub ∪ Silo (cross-run history + disposition receipts) ────
# Key morphology copied from B1 canary run 35082772768:
#   mcli ls --recursive silo/ci-artifacts/d30/  and  .../d14/
_CANARY_REPO_ID = 1327629472
_CANARY_SHA = "2cdbc1bd4719665b7ba7548ba04edea73ec65ffc"
_CANARY_RUN_ID = 35082772768
_CANARY_ATTEMPT = 1
_CANARY_TERMINAL_NAME = (
    f"gate-terminal-v1-{_CANARY_REPO_ID}-{_CANARY_SHA}-{_CANARY_RUN_ID}-{_CANARY_ATTEMPT}"
)
_CANARY_TERMINAL_KEY = f"d30/{_CANARY_REPO_ID}/{_CANARY_TERMINAL_NAME}/gate-terminal.json"
_CANARY_AUDIT_KEY = (
    f"d14/{_CANARY_REPO_ID}/primary-audit-v2-{_CANARY_REPO_ID}-"
    f"{_CANARY_SHA}-{_CANARY_RUN_ID}-{_CANARY_ATTEMPT}/primary-review-audit.json"
)














































































# ── gate#199 止血：主审未产出结论时 quality 不得定罪 ──────────────────────

















# ── gate#199 根治判定表（quality=failure 前提）─────────────────────────














































































def _string_values(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for nested in value.values() for part in _string_values(nested)]
    if isinstance(value, list):
        return [part for nested in value for part in _string_values(nested)]
    return []


from test_gate_aggregator_caller_checks import (
    _panel_row,
    _budget_exhausted_attempt,
    _budget_exhausted_audit,
    _failing_scoped_audit,
    _scope_for,
    _gate_rerun,
    _false_positive_receipt,
    _deferred_receipt,
    _resolved_line,
    _evaluate_failing_primary,
    _zip_receipt_bytes,
    _terminal_for,
    _canary_terminal_bytes,
    _canary_terminal_zip,
    _primary_convergence_row,
    _primary_convergence_history,
    _run_pr_convergence_cli,
    _install_dual_read,
    _real_convergence_history_silo_objects,
    _canary_receipt,
    _receipt_payload,
    _receipt_key,
)

from test_gate_aggregator_silo_io import (
    _visible_scenario,
    _latest_store,
    _assert_single_latest_get,
    _previous_round_fixture_objects,
    _render_previous_round_fixture,
)

from test_gate_aggregator_previous_context import (
    _relation_audit,
    _write_relation_audit,
    _previous_round_fixture_objects,
    _render_previous_round_fixture,
    _anchor_git_heads,
)
