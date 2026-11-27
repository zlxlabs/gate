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


def test_non_draft_non_fork_self_runner_skipped_primary_fails():
    # review_expected True + skipped == an unexplained skip, never accepted.
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="skipped", is_draft=False, review_expected=True, audit=None, audit_error=None)
    )
    assert outcome.ok is False
    assert outcome.synthetic_audit is None  # skip is not a missing-audit situation


def test_non_draft_fork_or_hosted_skipped_primary_is_accepted():
    # review_expected False due to fork/hosted (not draft) — still accepted.
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="skipped", is_draft=False, is_fork=True, review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is True


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


@pytest.mark.parametrize("author", [None, "", 17, True, [], {}])
def test_missing_or_invalid_pr_author_is_unavailable_before_skip_acceptance(author):
    outcome = AGG.evaluate(**_base_kwargs(
        pr_author=author, primary_result="skipped", is_draft=False, is_fork=True,
        runner="hosted", classify_review_expected="false", review_expected=False, audit=None,
    ))
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "primary_unavailable", "unavailable",
    )
    assert outcome.skip_reason is None
    assert any("PR author" in problem for problem in outcome.problems)

def test_dependabot_draft_skip_is_expected_from_payload():
    outcome = AGG.evaluate(**_base_kwargs(
        pr_author="dependabot[bot]", primary_result="skipped", is_draft=True,
        review_expected=False, audit=None,
    ))
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "expected_skip", "review_not_expected", "skipped",
    )








@pytest.mark.parametrize(
    "case,expected_primary,expected_reason,expected_draft,expected_gate_result,expected_classification,expected_reason_code",
    [
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "false", "review_expected": "false"}, "skipped", "review_exempt", False, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "skipped", "is_draft": "true", "is_fork": "true", "runner": "hosted", "classify_review_expected": "false", "review_expected": "false"}, "skipped", "draft", True, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "true", "runner": "self", "classify_review_expected": "true", "review_expected": "false"}, "skipped", "fork", False, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "false", "runner": "hosted", "classify_review_expected": "true", "review_expected": "false"}, "skipped", "hosted_runner", False, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "success", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "true", "review_expected": "true"}, "executed", None, False, "pass", "code_pass", "primary_pass"),
        ({"primary_result": "failure", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "true", "review_expected": "true"}, "executed", None, False, "fail", "code_fail", "primary_findings"),
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "true", "review_expected": "false"}, "skipped", None, False, "unavailable", "integration_error", "unexpected_primary_skip"),
    ],
)
def test_real_aggregator_subprocess_publishes_one_verdict_line(
    tmp_path, case, expected_primary, expected_reason, expected_draft, expected_gate_result,
    expected_classification, expected_reason_code,
):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    if case["primary_result"] in ("success", "failure"):
        (audit_dir / "primary-review-audit.json").write_text(
            json.dumps(_valid_primary_record(verdict="fail" if case["primary_result"] == "failure" else "pass")),
            encoding="utf-8",
        )
    summary_path = tmp_path / "summary.md"
    args = _cli_args(audit_dir, summary_path, **case)
    env = {**os.environ, "GH_TOKEN": "", "GITHUB_TOKEN": ""}
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), *args],
        cwd=ROOT, env=env, capture_output=True, check=False,
    )
    assert result.returncode == (0 if expected_gate_result in ("pass", "skipped") else 1)
    prefix = b"AGENT-GATE-VERDICT-V1 "
    verdict_lines = [line for line in result.stdout.splitlines() if line.startswith(prefix)]
    assert len(verdict_lines) == 1
    verdict = json.loads(verdict_lines[0][len(prefix):])
    assert verdict_lines[0] == prefix + json.dumps(
        verdict, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert verdict == {
        "classification": expected_classification,
        "draft": expected_draft,
        "gate_result": expected_gate_result,
        "primary": expected_primary,
        "reason_code": expected_reason_code,
        "skip_reason": expected_reason,
        "v": 1,
    }
    assert verdict_lines[0].decode("utf-8") in summary_path.read_text(encoding="utf-8")
    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_text(encoding="utf-8"))
    assert terminal["skip_reason"] == expected_reason


def test_quality_skipped_by_primary_failure_reports_short_circuit_not_quality_problem():
    # gate#105 方案 A: when the workflow short-circuits quality because primary
    # already failed, the problems text must say WHY quality did not run (a
    # short-circuit, not a quality defect). The other skipped combination —
    # (quality=skipped, primary=success) — is unchanged and still fails as
    # quality_skipped (matrix cell above).
    outcome = AGG.evaluate(**_base_kwargs(
        quality_result="skipped", primary_result="failure",
        audit=_valid_primary_record(verdict="fail"),
    ))
    assert outcome.ok is False
    assert any("short-circuit" in p for p in outcome.problems)
    assert not any("quality job result is 'skipped'" in p for p in outcome.problems)






@pytest.mark.parametrize(
    "runner,is_fork,classify_review_expected",
    [
        ("self", False, "true"),
        ("hosted", False, "true"),
        ("self", True, "false"),
        ("hosted", True, "false"),
    ],
)
def test_draft_skipped_primary_uses_payload_for_all_caller_modes(
    runner, is_fork, classify_review_expected,
):
    outcome = AGG.evaluate(**_base_kwargs(
        primary_result="skipped", is_draft=True, review_expected=False, audit=None,
        runner=runner, is_fork=is_fork,
        classify_review_expected=classify_review_expected,
    ))
    assert outcome.ok is True
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "expected_skip", "review_not_expected", "skipped",
    )
    assert outcome.skip_reason == "draft"
    assert any("draft stage" in note for note in outcome.notes)






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




@pytest.mark.parametrize(
    "quality_result,primary_result,verdict,is_draft,review_expected,caller_checks,expected_gate,expected_exit",
    [
        ("failure", "success", "pass", "false", "true", "failed", "fail", 1),
        ("skipped", "failure", "fail", "false", "true", None, "fail", 1),
        ("skipped", "failure", "unavailable", "false", "true", None, "unavailable", 1),
        ("success", "skipped", None, "true", "false", None, "skipped", 0),
    ],
    ids=["quality-failure-with-primary-pass", "primary-fail", "primary-unavailable", "draft-skip"],
)
def test_primary_verdict_is_transported_from_validated_audit_to_terminal_history(
    tmp_path, quality_result, primary_result, verdict, is_draft, review_expected,
    caller_checks, expected_gate, expected_exit,
):
    audit_dir = tmp_path / "audit"
    if verdict is not None:
        audit_dir.mkdir()
        (audit_dir / "primary-review-audit.json").write_text(
            json.dumps(_valid_primary_record(verdict=verdict)), encoding="utf-8",
        )
    summary_path = tmp_path / "summary.md"
    exit_code = AGG.main(_cli_args(
        audit_dir, summary_path,
        quality_result=quality_result, primary_result=primary_result,
        is_draft=is_draft, review_expected=review_expected, caller_checks=caller_checks,
    ))

    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_bytes())
    row = AGG._terminal_row(
        terminal, repository="zlxlabs/gate", repository_id=IDENTITY.repository_id,
        pr_number=IDENTITY.pr,
    )
    assert (exit_code, terminal["gate_result"]) == (expected_exit, expected_gate)
    if verdict is None:
        assert "primary_verdict" not in terminal
    else:
        assert terminal["primary_verdict"] == verdict
    assert row["primary_verdict"] == verdict






@pytest.mark.parametrize("verdict", ["not-a-primary-verdict", "not_expected", "waived"])
def test_invalid_primary_verdict_marks_terminal_history_incomplete(monkeypatch, verdict):
    record = json.loads(_canary_terminal_bytes())
    record["primary_verdict"] = verdict
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("gate-terminal.json", json.dumps(record))
    monkeypatch.setattr(AGG, "_download_terminal_zip", lambda **kwargs: archive.getvalue())
    history = AGG.HistoryLoad()

    AGG._consume_terminal_artifact(
        history,
        artifact={"name": "gate-terminal-v1-1327629472-test", "expired": False,
                  "archive_download_url": "https://api.github.com/artifacts/1/zip"},
        token="test-token", repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID,
        pr_number=42,
    )

    assert history.rows == []
    assert len(history.skipped_records) == 1
    assert "primary_verdict" in history.incomplete_reasons[0]






def _visible_scenario(tmp_path, overrides, audit_record="__default__"):
    audit_dir = tmp_path / "audit"
    if audit_record is not None:
        audit_dir.mkdir()
        record = _valid_primary_record() if audit_record == "__default__" else audit_record
        (audit_dir / "primary-review-audit.json").write_text(json.dumps(record))
    summary_path = tmp_path / "summary.md"
    return audit_dir, summary_path, _cli_args(audit_dir, summary_path, **overrides)






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














































def test_publish_budget_stops_hanging_http_call_and_records_operations(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv("GATE_PUBLISH_BUDGET_SECONDS", "0.05")
    started = threading.Event()
    released = threading.Event()
    finished = threading.Event()
    calls = []

    def hanging_urlopen(request, timeout=None):
        calls.append((request.full_url, timeout))
        started.set()
        released.wait(timeout=timeout)
        raise socket.timeout("stub hung")

    monkeypatch.setattr(urllib.request, "urlopen", hanging_urlopen)
    result = []

    def invoke_publish():
        result.append(
            AGG._post_status_panel_fail_open(
                current=_panel_terminal_row(1, 1, "pass"),
                repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
            )[1]
        )
        finished.set()

    worker = threading.Thread(target=invoke_publish, daemon=True)
    worker.start()
    try:
        assert started.wait(timeout=0.5)
        assert finished.wait(timeout=0.5), "publish must stop a hanging call within its budget"
    finally:
        released.set()
        worker.join(timeout=1)

    assert len(result) == 1
    assert calls and calls[0][1] <= 0.05
    receipt = result[0]
    assert receipt["reason_code"] == "publish_budget_exhausted"
    assert receipt["completed_operations"] == []
    assert receipt["pending_operations"]


def test_slow_repo_wide_history_yields_to_comment_publish(monkeypatch):
    """A large repository artifact listing must not starve the panel POST."""
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv(AGG.PUBLISH_BUDGET_ENV, "0.10")
    monkeypatch.setenv(AGG.HISTORY_RECONSTRUCTION_BUDGET_ENV, "0.03")
    owner = {"id": 99, "login": "workflow-bot"}
    posted_body = None
    comment_gets = 0

    def fake_urlopen(request, timeout=None):
        nonlocal posted_body, comment_gets
        url = request.full_url
        if url.endswith("/user"):
            return _FakeResponse(json.dumps(owner).encode())
        if "/issues/42/comments" in url:
            if request.get_method() == "POST":
                posted_body = json.loads(request.data.decode())["body"]
                return _FakeResponse()
            comment_gets += 1
            comments = [] if comment_gets == 1 else [{
                "id": 77,
                "created_at": "2026-08-16T00:00:00Z",
                "body": posted_body or AGG.PANEL_MARKER,
                "user": owner,
            }]
            return _FakeResponse(json.dumps(comments).encode())
        if "/actions/artifacts" in url:
            time.sleep(0.01)
            page = int(url.rsplit("page=", 1)[1])
            artifacts = [{"name": f"unrelated-{page}-{index}"} for index in range(100)] if page <= 22 else []
            return _FakeResponse(json.dumps({"artifacts": artifacts}).encode())
        raise AssertionError(f"unexpected GitHub API call: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )

    assert receipt["reason_code"] == "posted"
    assert receipt["history_incomplete"] is True
    assert any("history" in reason and ("budget" in reason or "share" in reason) for reason in receipt["history_incomplete_reasons"])
    assert "COMMENT_PUBLISH" in receipt["completed_operations"]
    assert "COMMENT_PUBLISH" not in receipt["pending_operations"]


@pytest.mark.parametrize(
    "failure,expected_delivery,expected_status",
    [
        (urllib.error.HTTPError("https://api.github.com/x", 429, "rate", hdrs=None, fp=None), "not_created", 429),
        (urllib.error.URLError("Authorization: Bearer ghs_xxx https://api.github.com/path?token=query-secret"), "unknown", None),
    ],
    ids=["http-429", "network-timeout"],
)
def test_publish_only_transport_failures_are_fail_open_and_leave_receipt(monkeypatch, capsys, tmp_path, failure, expected_delivery, expected_status):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    terminal_path = tmp_path / "gate-terminal.json"
    delivery_path = tmp_path / "panel-delivery.json"
    assert AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))) == 0

    owner = {"id": 99, "login": "workflow-bot"}
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    post_calls = []
    def fail_post(**kwargs):
        post_calls.append(kwargs)
        raise failure
    monkeypatch.setattr(AGG, "_post_issue_comment", fail_post)
    args = _cli_args(
        audit_dir, summary_path, terminal_path=str(terminal_path), panel_delivery_path=str(delivery_path),
    ) + ["--publish-only"]
    assert AGG.main(args) == 0
    receipt = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert receipt["delivery"] == expected_delivery
    assert receipt["http_status"] == expected_status
    assert len(post_calls) == 1
    assert receipt["operation"] == "comment_create"
    assert receipt["attempts"] == 1
    assert receipt["exception_type"] == type(failure).__name__
    summary = summary_path.read_text(encoding="utf-8")
    assert "HTTP status" in summary
    assert "面板发布失败，面板可能显示旧状态" in summary
    output = capsys.readouterr().out
    assert "PANEL_PUBLISH_FAILED operation=comment_create attempts=1" in output
    assert "exc_type=" + type(failure).__name__ in output
    assert "ghs_xxx" not in output + summary + json.dumps(receipt)
    assert "query-secret" not in output + summary + json.dumps(receipt)






def test_panel_delivery_write_failure_clears_stale_file_and_stays_fail_open(monkeypatch, capsys, tmp_path):
    path = tmp_path / "panel-delivery.json"
    path.write_text("old receipt", encoding="utf-8")
    monkeypatch.setattr(AGG, "_write_panel_delivery", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    AGG._persist_panel_delivery(str(path), {"delivery": "created"})
    assert not path.exists()
    assert "file is missing and upload will red" in capsys.readouterr().out


def test_panel_delivery_cleanup_failure_writes_invalid_marker(monkeypatch, capsys, tmp_path):
    path = tmp_path / "panel-delivery.json"
    path.write_text("old receipt", encoding="utf-8")
    monkeypatch.setattr(Path, "unlink", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("locked")))
    monkeypatch.setattr(AGG, "_write_panel_delivery", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    AGG._persist_panel_delivery(str(path), {"delivery": "created"})
    assert path.read_bytes() == b"invalid gate PR-comment receipt\n"
    assert "stale receipt was destroyed; an invalid marker was written" in capsys.readouterr().out


def test_patch_request_uses_issue_comment_endpoint(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        request.captured_timeout = timeout
        calls.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    AGG._patch_issue_comment(repository="zlxlabs/gate", comment_id=77, body="new", token="tok")
    request = calls[0]
    assert request.full_url == "https://api.github.com/repos/zlxlabs/gate/issues/comments/77"
    assert request.get_method() == "PATCH"
    assert request.captured_timeout == 15
    assert json.loads(request.data.decode("utf-8")) == {"body": "new"}


def test_post_issue_comment_uses_issue_comments_endpoint(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        request.captured_timeout = timeout
        calls.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    AGG._post_issue_comment(repository="zlxlabs/gate", pr_number=42, body="hello gate", token="tok")
    request = calls[0]
    assert request.full_url == "https://api.github.com/repos/zlxlabs/gate/issues/42/comments"
    assert request.get_method() == "POST"
    assert request.captured_timeout == 15
    assert json.loads(request.data.decode("utf-8")) == {"body": "hello gate"}


def test_panel_marker_candidates_require_own_author_and_choose_earliest(monkeypatch):
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [
        {"id": 1, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 1, "login": "human"}},
        {"id": 3, "created_at": "2026-08-16T00:02:00Z", "body": AGG.PANEL_MARKER, "user": owner},
        {"id": 2, "created_at": "2026-08-16T00:01:00Z", "body": AGG.PANEL_MARKER, "user": owner},
    ]
    selected = AGG._find_panel_comments(comments, owner)
    assert [comment["id"] for comment in selected] == [2, 3]
    assert all(comment["user"]["id"] == owner["id"] for comment in selected)


def test_panel_marker_matching_prefers_id_over_login():
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [
        {"id": 1, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 7, "login": "workflow-bot"}},
        {"id": 2, "created_at": "2026-08-16T00:01:00Z", "body": AGG.PANEL_MARKER, "user": {"login": "workflow-bot"}},
    ]

    selected = AGG._find_panel_comments(comments, owner)

    assert [comment["id"] for comment in selected] == [2]


def test_panel_comment_lookup_paginates_all_pages(monkeypatch):
    owner = {"id": 99, "login": "workflow-bot"}
    page_one = [{"id": i, "body": "ordinary", "user": owner} for i in range(100)]
    page_two = [{"id": 101, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": owner}]
    calls = []
    def fake_json(**kwargs):
        calls.append(kwargs["url"])
        return page_one if "&page=1" in kwargs["url"] else page_two
    monkeypatch.setattr(AGG, "_github_json", fake_json)
    comments = AGG._fetch_panel_comments(token="tok", repository="zlxlabs/gate", pr_number=42)
    assert len(comments) == 101
    assert calls == [
        "https://api.github.com/repos/zlxlabs/gate/issues/42/comments?per_page=100&page=1",
        "https://api.github.com/repos/zlxlabs/gate/issues/42/comments?per_page=100&page=2",
    ]


def test_post_self_heal_deletes_duplicate_and_patches_earliest_own_panel(monkeypatch):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    comments = [
        [],
        [
            {"id": 20, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 99, "login": "workflow-bot"}},
            {"id": 21, "created_at": "2026-08-16T00:01:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 99, "login": "workflow-bot"}},
        ],
    ]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: {"id": 99, "login": "workflow-bot"})
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments.pop(0))
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: operations.append(("POST", kwargs["body"])))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(("PATCH", kwargs["comment_id"], kwargs["body"])))
    monkeypatch.setattr(AGG, "_delete_issue_comment", lambda **kwargs: operations.append(("DELETE", kwargs["comment_id"])))

    _, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert operations[0][0] == "POST"
    assert ("PATCH", 20, operations[1][2]) in operations
    assert ("DELETE", 21) in operations
    assert receipt["comment_created"] is True




def test_existing_panel_cache_is_parsed_and_incomplete_history_is_explicit():
    body = "\n".join([
        AGG.PANEL_MARKER,
        "| Run | Attempt | Head | 状态 | 收件人动作 |",
        "| ---: | ---: | :--- | :--- | :--- |",
        "| [7](https://github.com/zlxlabs/gate/actions/runs/7) | 1 | `abcdef1` | `fail` | 要修代码 |",
    ])
    cached = AGG._parse_panel_history(body)
    assert cached[0]["run_id"] == 7
    rendered = AGG.render_status_panel(
        [_panel_terminal_row(8, 1, "pass", "b" * 40), *cached],
        history_warning="expired artifact gate-terminal-v1-123-old",
    )
    assert "历史可能不完整" in rendered
    assert "| [7]" in rendered and "| [8]" in rendered


def test_existing_panel_cache_is_unioned_when_artifact_history_is_incomplete(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    cached_body = AGG.render_status_panel([_panel_terminal_row(7, 1, "fail", "a" * 40)])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": cached_body, "user": owner}]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(
        AGG, "_fetch_terminal_history",
        lambda **kwargs: AGG.HistoryLoad(
            rows=[current],
            skipped_records=[{"name": "gate-terminal-v1-123-old", "reason": "expired_artifact"}],
            incomplete_reasons=["expired artifact gate-terminal-v1-123-old"],
        ),
    )
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["history_incomplete"] is True
    assert receipt["history_skipped_count"] == 1
    assert "历史可能不完整" in body
    assert "| [7]" in body and "| [8]" in body
    assert operations == [body]


def test_existing_panel_cache_is_incomplete_when_artifact_history_is_empty(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    cached_body = AGG.render_status_panel([_panel_terminal_row(7, 1, "fail", "a" * 40)])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": cached_body, "user": owner}]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )

    assert receipt["history_incomplete"] is True
    assert "历史可能不完整" in body
    assert "1 个制品历史缺少面板缓存行" in body
    assert receipt["history_incomplete_reasons"] == ["artifact history does not contain cached rows: 7/1"]
    assert operations == [body]


def test_existing_panel_cache_is_complete_when_artifact_history_covers_it(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    cached = _panel_terminal_row(7, 1, "fail", "a" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    cached_body = AGG.render_status_panel([cached])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": cached_body, "user": owner}]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[cached, current]))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )

    assert receipt["history_incomplete"] is False
    assert "历史可能不完整" not in body
    assert receipt["reason_code"] == "patched"
    assert operations == [body]


def test_history_api_failure_preserves_existing_panel_body_and_records_diagnostic(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    old_body = AGG.render_status_panel([_panel_terminal_row(7, 1, "fail", "a" * 40)])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": old_body, "user": owner}]
    error = urllib.error.HTTPError("https://api.github.com/actions/artifacts", 503, "down", hdrs=None, fp=None)
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must preserve old body")))
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert body == old_body
    assert receipt["delivery"] == "not_created"
    assert receipt["reason_code"] == "history_unavailable"
    assert receipt["http_status"] == 503
    assert receipt["history_error"]


def test_history_skip_count_is_written_to_step_summary(tmp_path, capsys):
    summary = tmp_path / "summary.md"
    receipt = AGG._build_panel_delivery(
        body="panel", repository="zlxlabs/gate", pr_number=42, identity=IDENTITY,
        delivery="updated", reason_code="patched",
        history_skipped_records=[{"name": "old", "reason": "expired_artifact"}],
    )
    AGG._append_panel_diagnostic(str(summary), receipt)
    assert "Skipped history records: `1`" in summary.read_text(encoding="utf-8")
    assert "`old`: `expired_artifact`" in capsys.readouterr().out


def test_status_panel_aggregates_repeated_http_reasons_and_keeps_receipt_detail():
    reasons = [
        f"artifact gate-terminal-v1-123-{index}: HTTPError: HTTP Error 401: signed blob rejected"
        for index in range(21)
    ]
    body = AGG.render_status_panel(
        [_panel_row(1, 1, "pass")], history_reasons=reasons,
    )
    warning_line = next(line for line in body.splitlines() if line.startswith("> 历史可能不完整："))
    receipt = AGG._build_panel_delivery(
        body=body, repository="zlxlabs/gate", pr_number=42, identity=IDENTITY,
        delivery="updated", reason_code="history_incomplete",
        history_incomplete_reasons=reasons,
    )

    assert "21 个制品下载失败：HTTP 401" in body
    assert body.count("制品下载失败：HTTP 401") == 1
    assert len(warning_line) <= AGG.MAX_HISTORY_WARNING_CHARS
    assert receipt["history_incomplete_reasons"] == reasons
    assert len(receipt["history_incomplete_reasons"]) == 21

    long_body = AGG.render_status_panel(
        [_panel_row(1, 1, "pass")],
        history_reasons=[f"unclassified reason {index}: {'x' * 40}" for index in range(21)],
    )
    long_warning_line = next(
        line for line in long_body.splitlines() if line.startswith("> 历史可能不完整：")
    )
    assert len(long_warning_line) <= AGG.MAX_HISTORY_WARNING_CHARS
    assert "完整明细见 delivery 诊断制品" in long_warning_line



def _panel_row(run_id, run_attempt, gate_result, *, head_sha=None):
    return {
        "schema_version": AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": "zlxlabs/gate",
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha or (chr(96 + run_id) * 40),
        "gate_result": gate_result,
        "classification": {
            "pass": "code_pass",
            "fail": "code_fail",
            "skipped": "expected_skip",
            "unavailable": "integration_error",
        }[gate_result],
        "reason_code": {
            "pass": "primary_pass",
            "fail": "primary_findings",
            "skipped": "review_not_expected",
            "unavailable": "audit_missing",
        }[gate_result],
    }


def _budget_exhausted_attempt(*, reason=None, exit_code=22, reviewer="reviewer"):
    return {
        "reviewer": reviewer,
        "model": "model",
        "exit_code": AGG.PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE if reason is None else exit_code,
        "reason": AGG.PRIMARY_BUDGET_EXHAUSTED_REASON if reason is None else reason,
        "cost_usd": None,
        "tokens": None,
        "duration_s": 1.0,
    }


def _budget_exhausted_audit(*, attempts=None):
    audit = _valid_primary_record(
        verdict="unavailable",
        attempts=attempts if attempts is not None else [
            _budget_exhausted_attempt(reviewer="reviewer-a"),
            _budget_exhausted_attempt(reviewer="reviewer-b"),
        ],
    )
    return audit














# ── false-positive disposition consumption (required verdict) ─────────────

CONV = AGG._CONVERGENCE
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _failing_scoped_audit(*, finding_id="p1", severity="major"):
    return _valid_scoped_primary_record(
        verdict="fail",
        result={"findings": [{
            "id": finding_id, "severity": severity, "trigger_kind": "inferred",
            "file": "src/lock.py", "line": 12, "category": "correctness",
        }]},
    )


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


def _false_positive_receipt(scope, *, audit_digest=_DIGEST_A, finding_id="p1", **changes):
    head_sha = changes.get("head_sha", IDENTITY.head_sha)
    fields = dict(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="false-positive",
        repository_id=str(IDENTITY.repository_id),
        pr_number=IDENTITY.pr,
        epoch=CONV.derive_epoch(scope),
        head_sha=head_sha,
        audit_digest=audit_digest,
        finding_id=finding_id,
        reason="locked upstream behavior",
        counterevidence={
            "command": "pytest -q tests/test_regression.py",
            "output": "1 passed",
            "result": "refuted",
            "pointer": "tests/test_regression.py::test_regression",
            "gate_rerun": _gate_rerun(head_sha),
        },
        approver="octocat",
        approver_id=1,
        approved_at="2026-08-30T12:00:00Z",
    )
    fields.update(changes)
    return CONV.DispositionReceipt(**fields)


def _deferred_receipt(scope, *, audit_digest=_DIGEST_A, finding_id="p1", tracking_issue="#12", **changes):
    fields = dict(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="deferred",
        repository_id=str(IDENTITY.repository_id),
        pr_number=IDENTITY.pr,
        epoch=CONV.derive_epoch(scope),
        head_sha=IDENTITY.head_sha,
        audit_digest=audit_digest,
        finding_id=finding_id,
        reason="tracked below the gate redline",
        tracking_issue=tracking_issue,
        approver="octocat",
        approver_id=1,
        approved_at="2026-08-30T12:00:00Z",
    )
    fields.update(changes)
    return CONV.DispositionReceipt(**fields)


def _resolved_line(receipt):
    return CONV.recorded_disposition_lines(
        CONV.DispositionAudit(
            primary_p1_ids=(),
            recorded_receipts=(receipt,),
            rejected_receipts=(),
            recorded_finding_ids=(receipt.finding_id,),
        )
    )[0]


def _evaluate_failing_primary(audit, *, audit_digest=_DIGEST_A, waiver_receipts=()):
    return AGG.evaluate(
        **_base_kwargs(
            primary_result="failure",
            audit=audit,
            scope=_scope_for(audit),
            audit_digest=audit_digest,
            waiver_receipts=waiver_receipts,
            repository="zlxlabs/gate",
        )
    )












def _zip_receipt_bytes(payload):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr("receipt.json", json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return buf.getvalue()
























def _terminal_for(outcome, *, primary_result="failure"):
    return AGG.build_terminal_envelope(
        repository="zlxlabs/gate", identity=IDENTITY, quality_result="success",
        primary_result=primary_result, review_expected=True, is_draft=False, runner="self",
        outcome=outcome,
    )








































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




def _canary_terminal_bytes(gate_result="pass", *, run_id=_CANARY_RUN_ID, run_attempt=_CANARY_ATTEMPT, head_sha=_CANARY_SHA):
    record = {
        "schema_version": 1,
        "kind": "gate_terminal",
        "repository": "zlxlabs/gate",
        "repository_id": _CANARY_REPO_ID,
        "pr_number": 42,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
        "gate_result": gate_result,
        "primary_result": "success" if gate_result == "pass" else "failure",
        "audit": {"available": True, "source_attempt": 1, "artifact_name": "primary-audit-v2-canary"},
        "classification": "code_pass" if gate_result == "pass" else "code_fail",
        "reason_code": "primary_pass" if gate_result == "pass" else "primary_findings",
    }
    return json.dumps(record).encode()


def _canary_terminal_zip(gate_result="pass", **kwargs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr("gate-terminal.json", _canary_terminal_bytes(gate_result, **kwargs))
    return buf.getvalue()


def _primary_convergence_row(
    run_id, attempt, verdict, *, primary_result=None, skip_reason=None, audit_available=None,
    head_sha=IDENTITY.head_sha,
):
    record = json.loads(_canary_terminal_bytes(
        "fail" if verdict == "fail" or primary_result == "failure" else "pass",
        run_id=run_id, run_attempt=attempt, head_sha=head_sha,
    ))
    record.update({"primary_result": primary_result or ("failure" if verdict == "fail" else "success"),
                   "skip_reason": skip_reason, "audit": {"available": verdict is not None if audit_available is None else audit_available}})
    if verdict is not None:
        record["primary_verdict"] = verdict
    return AGG._terminal_row(record, repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42)


def _primary_convergence_history(target_run_ids, rows):
    history = AGG.HistoryLoad(rows=rows)
    history.target_run_ids = target_run_ids
    return history


def _run_pr_convergence_cli(tmp_path, monkeypatch, *, audit_verdict, primary_result, quality_result="success", caller_checks="", history=None):
    audit_dir = tmp_path / "audit"
    if audit_verdict is not None:
        audit_dir.mkdir()
        (audit_dir / "primary-review-audit.json").write_text(
            json.dumps(_valid_scoped_primary_record(verdict=audit_verdict, result={"findings": []})), encoding="utf-8",
        )
        if history is not None:
            monkeypatch.setattr(AGG, "_load_pr_convergence_history", lambda **kwargs: ("available", (), history))
    monkeypatch.setattr(AGG, "_fetch_disposition_receipts", lambda **kwargs: ())
    monkeypatch.setattr(AGG, "load_previous_round_findings", lambda **kwargs: {"available": False, "detail": "fixture", "findings": []})
    summary_path = tmp_path / "summary.md"
    exit_code = AGG.main(_cli_args(
        audit_dir, summary_path, quality_result=quality_result, caller_checks=caller_checks,
        primary_result=primary_result,
    ))
    return exit_code, json.loads(summary_path.with_name("gate-terminal.json").read_bytes())








def _install_dual_read(monkeypatch, *, github_artifacts, github_blobs, silo_objects, github_error=None, silo_error=None):
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")

    def fake_github_json(**kwargs):
        if github_error is not None:
            raise github_error
        return {"artifacts": github_artifacts}

    def fake_download(**kwargs):
        return github_blobs[kwargs["url"]]

    def fake_silo(prefix, timeout_s):
        if silo_error is not None:
            raise silo_error
        return [(key, body) for key, body in silo_objects if key.startswith(prefix)]

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            prefix = argv[2]
            keys = [key for key, _body in silo_objects if key.startswith(prefix)]
            if silo_error is not None:
                raise silo_error
            return subprocess.CompletedProcess(
                argv, 0, stdout="".join(f"{key}\n" for key in keys), stderr="",
            )
        key = argv[argv.index("--key") + 1]
        body = dict(silo_objects)[key]
        dest = argv[argv.index("--dest") + 1]
        Path(dest).write_bytes(body)
        return subprocess.CompletedProcess(argv, 0, stdout=dest, stderr="")

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    monkeypatch.setattr(AGG, "_download_terminal_zip", fake_download)
    monkeypatch.setattr(AGG, "_silo_objects_under", fake_silo)
    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
































def _real_convergence_history_silo_objects():
    fixture = json.loads(CONVERGENCE_HISTORY_FIXTURE.read_text(encoding="utf-8"))
    objects = [
        (key, json.dumps(record, sort_keys=True).encode("utf-8"))
        for key, record in zip(fixture["source_keys"], fixture["terminal_envelopes"], strict=True)
    ]
    return fixture, objects








def _canary_receipt(reason="locked upstream behavior", finding_id="p1"):
    scope = _scope_for(_failing_scoped_audit())
    return _false_positive_receipt(
        scope, repository_id=str(_CANARY_REPO_ID), reason=reason, finding_id=finding_id,
    )


def _receipt_payload(receipt):
    return {**receipt.as_dict(), "kind": CONV.DISPOSITION_RECEIPT_KIND}


def _receipt_key(receipt):
    name = CONV.disposition_receipt_artifact_name(receipt)
    return f"d30/{_CANARY_REPO_ID}/{name}/{name}"










def test_silo_objects_under_has_no_in_process_client_branch(monkeypatch):
    # Silo 控制路径零运行期装包：旧的进程内直连分支（含第三方 SDK 探测）已
    # 删除，模块不再提供 _silo_store_mod；唯一出口是纯标准库 CLI（打桩断言）。
    import subprocess

    assert not hasattr(AGG, "_silo_store_mod")
    prefix = f"d30/{_CANARY_REPO_ID}/"
    canonical_key = f"d30/{_CANARY_REPO_ID}/artifact-abc/receipt.json"
    payload = b'{"receipt": true}'
    recorded_calls = []

    def fake_cli(argv, timeout_s):
        recorded_calls.append(list(argv))
        assert "--dest" in argv
        dest_dir = Path(argv[argv.index("--dest") + 1])
        parts = canonical_key.split("/", 3)
        file_path = dest_dir / parts[2] / parts[3]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(payload)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=f"{canonical_key}\n", stderr="")

    monkeypatch.setattr(AGG, "_silo_cli", fake_cli)
    objects = AGG._silo_objects_under(prefix, AGG.SILO_CLI_CALL_TIMEOUT_SECONDS)
    assert objects == [(canonical_key, payload)]
    assert len(recorded_calls) == 1
    assert recorded_calls[0][0:3] == ["list", "--prefix", prefix]


def test_silo_cli_argv_uses_interpreter_without_package_manager(monkeypatch):
    # 跨进程边界断言：聚合器实际发出的 argv 必须是 [解释器, 仓内脚本, ...]，
    # 不得出现 uv/uvx/pip/npx。每次调用必须带墙钟 timeout（gate#290 教训：
    # 无界 Silo 子进程会拖死汇总 job），由调用点显式声明预算来源。
    recorded = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        recorded.append((list(argv), kwargs.copy()))
        return _Proc()

    monkeypatch.setattr(AGG.subprocess, "run", fake_run)
    monkeypatch.setenv("SILO_STORE", "/tmp/fake-silo-store.py")
    AGG._silo_cli(["list", "--prefix", "d30/1/"], 30)
    assert len(recorded) == 1
    argv, kwargs = recorded[0]
    assert kwargs["timeout"] == 30
    assert argv[0] == sys.executable
    assert argv[1] == "/tmp/fake-silo-store.py"
    assert argv[2:] == ["list", "--prefix", "d30/1/"]
    joined = " ".join(argv)
    for banned in ("uv run", "uvx", "pip install", "npx", "--with"):
        assert banned not in joined


# ── gate#199 止血：主审未产出结论时 quality 不得定罪 ──────────────────────

def test_issue199_quality_failure_with_cancelled_primary_is_unavailable():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure", primary_result="cancelled", audit=None, audit_error=None))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "primary_cancelled", "unavailable")
    terminal = AGG.build_terminal_envelope(repository="zlxlabs/gate", identity=IDENTITY, quality_result="failure", primary_result="cancelled", review_expected=True, is_draft=False, runner="self", outcome=outcome)
    assert terminal["quality_result"] == "failure"
    assert any("quality" in p for p in outcome.problems)
    assert any("cancelled" in p for p in outcome.problems)


def test_issue199_quality_cancelled_with_cancelled_primary_is_unavailable():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="cancelled", primary_result="cancelled", audit=None, audit_error=None))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "primary_cancelled", "unavailable")


def test_issue199_quality_skipped_with_cancelled_primary_is_unavailable():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="skipped", primary_result="cancelled", audit=None, audit_error=None))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "primary_cancelled", "unavailable")


def test_issue199_draft_skipped_primary_with_quality_failure_stays_code_problem():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", primary_result="skipped", is_draft=True, review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")


def test_issue199_hosted_skipped_primary_with_quality_failure_stays_code_problem():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", primary_result="skipped", is_draft=False, runner="hosted", review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")


def test_issue199_quality_failure_with_passing_primary_is_unavailable_without_failed_evidence():
    # 根治翻转（#199）：primary=success + quality=failure 时，没有
    # caller_checks=failed 证据就不能定罪为代码问题，一律基础设施不可用。
    # ok 仍为 False（合并照样被挡），只是桶从「要修代码」换到「修基础设施」。
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure"))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "quality_infra", "unavailable")


def test_issue199_quality_failure_with_failing_primary_stays_fail():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", primary_result="failure", audit=_valid_primary_record(verdict="fail"))
    )
    assert outcome.ok is False
    assert outcome.gate_result == "fail"


def test_issue199_unavailable_panel_renders_infra_action():
    body = AGG.render_status_panel([{
        "schema_version": AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": "zlxlabs/gate",
        "run_id": 1,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "gate_result": "unavailable",
        "classification": "review_unavailable",
        "reason_code": "primary_cancelled",
    }])
    assert "修基础设施" in body
    assert "要修代码" not in body


# ── gate#199 根治判定表（quality=failure 前提）─────────────────────────

@pytest.mark.parametrize("caller_checks,expected", [
    ("failed", ("ci_failure", "quality_failure", "fail")),
    ("signal_terminated", ("review_unavailable", "quality_infra", "unavailable")),
    ("passed", ("review_unavailable", "quality_infra", "unavailable")),
    ("not_started", ("review_unavailable", "quality_infra", "unavailable")),
    ("", ("review_unavailable", "quality_infra", "unavailable")),  # 证据缺席：旧调用方/记录步前死亡
])
def test_caller_checks_evidence_matrix(caller_checks, expected):
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure", caller_checks=caller_checks))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == expected


def test_preflight_blocked_is_legitimate_fail_despite_passing_checks():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="passed", preflight_result="blocked")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")
    assert any("exceeds the single-review budget" in problem for problem in outcome.problems)


def test_preflight_blocked_outranks_missing_evidence():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="", preflight_result="blocked")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")


def test_preflight_unavailable_precedes_business_failure_without_size_claim():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", preflight_result="unavailable")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable"
    )
    assert not any("split the PR" in problem for problem in outcome.problems)


@pytest.mark.parametrize("quality_result", ["success", "failure"])
def test_preflight_unavailable_never_passes_even_when_quality_result_is_not_red(quality_result):
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result=quality_result, caller_checks="passed", preflight_result="unavailable")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable"
    )


@pytest.mark.parametrize("preflight_result", ["", "skipped", "cancelled"])
def test_quality_success_requires_explicit_preflight_success(preflight_result):
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="success", caller_checks="passed", preflight_result=preflight_result)
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable"
    )
    assert not any("split the PR" in problem for problem in outcome.problems)


def test_signal_terminated_problem_names_the_signal_number():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="signal_terminated", caller_signal="15")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable",
    )
    assert any(
        "signal 15" in problem and "not a code problem" in problem
        for problem in outcome.problems
    )


def test_unknown_caller_checks_value_fails_closed():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure", caller_checks="banana"))
    assert outcome.ok is False
    assert outcome.classification is None
    assert any("caller_checks" in p for p in outcome.problems)


def test_quality_cancelled_ignores_caller_checks_evidence():
    # 只有 failure 腿读证据；cancelled/skipped 保持原语义。
    outcome = AGG.evaluate(**_base_kwargs(quality_result="cancelled", caller_checks="passed"))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_cancelled", "fail")


def test_quality_infra_panel_renders_infra_action():
    body = AGG.render_status_panel([{
        "schema_version": AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": "zlxlabs/gate",
        "run_id": 1,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "gate_result": "unavailable",
        "classification": "review_unavailable",
        "reason_code": "quality_infra",
    }])
    assert "修基础设施" in body
    assert "要修代码" not in body




def _relation_audit(**finding):
    record = _valid_scoped_primary_record(verdict="pass")
    record["result"] = {"verdict": "pass", "findings": [finding]}
    return record


def _write_relation_audit(tmp_path, finding):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_relation_audit(**finding)))
    return audit_dir


def test_same_finding_id_is_repeat_with_previous_pointer(tmp_path, capsys):
    finding = {
        "id": "same-id", "severity": "minor", "file": "a.py", "line": 3,
        "category": "correctness", "issue": "kept", "acceptance": "kept",
    }
    audit_dir = _write_relation_audit(tmp_path, finding)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"available": True, "detail": "", "findings": [finding]}))
    assert AGG.main([
        "--annotate-primary-audit", "--audit-path",
        str(audit_dir / "primary-review-audit.json"), "--previous-findings-path", str(previous),
    ]) == 0
    summary = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary))
    assert rc == 0
    terminal = json.loads(summary.with_name("gate-terminal.json").read_text())
    item = terminal["finding_relation"]["items"][0]
    assert item["relation_to_previous"] == "repeat"
    assert item["previous_finding_id"] == "same-id"
    assert terminal["finding_relation"]["counts"] == {"new": 0, "repeat": 1, "conflict": 0}
    assert "GATE-FINDING-RELATION-DEGRADED" not in capsys.readouterr().out


def test_opposite_same_location_requirement_is_conflict_and_not_pass(tmp_path):
    current = {
        "id": "round-b", "severity": "major", "file": "scripts/retro/log_acceptance.py", "line": 40,
        "category": "correctness", "issue": "must not advance the lease",
        "acceptance": "leave the lease at evidence",
    }
    previous_finding = {
        "id": "round-a", "severity": "major", "file": "scripts/retro/log_acceptance.py", "line": 40,
        "category": "correctness", "issue": "must advance the lease",
        "acceptance": "advance the lease to closed",
    }
    audit_dir = _write_relation_audit(tmp_path, current)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"available": True, "detail": "", "findings": [previous_finding]}))
    assert AGG.main([
        "--annotate-primary-audit", "--audit-path",
        str(audit_dir / "primary-review-audit.json"), "--previous-findings-path", str(previous),
    ]) == 0
    summary = tmp_path / "summary.md"
    receipt = tmp_path / "convergence-receipt.json"
    rc = AGG.main(_cli_args(audit_dir, summary, convergence_receipt_path=str(receipt)))
    terminal = json.loads(summary.with_name("gate-terminal.json").read_text())
    relation = terminal["finding_relation"]
    assert rc == 1
    assert terminal["gate_result"] != "pass"
    assert relation["review_terminal"] == "manual_required"
    assert relation["counts"]["conflict"] == 1
    assert relation["items"][0]["relation_to_previous"] == "conflict"
    assert relation["items"][0]["previous_finding_id"] == "round-a"
    body = AGG.render_status_panel([AGG._terminal_row(
        terminal, repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )])
    assert "manual_required" in body and "round-a" in body
    assert json.loads(receipt.read_text())["decision"] != "converged"
    same = dict(previous_finding, id="other", acceptance=current["acceptance"], issue=current["issue"])
    assert AGG.relate_findings([current], [same])[0]["relation_to_previous"] == "new"






def test_missing_previous_ledger_marks_new_and_job_succeeds(tmp_path, capsys):
    finding = {
        "id": "only-now", "severity": "minor", "file": "a.py", "line": 1,
        "category": "testing", "issue": "nit", "acceptance": "none",
    }
    audit_dir = _write_relation_audit(tmp_path, finding)
    summary = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary))
    out = capsys.readouterr().out
    terminal = json.loads(summary.with_name("gate-terminal.json").read_text())
    item = terminal["finding_relation"]["items"][0]
    assert rc == 0
    assert terminal["gate_result"] == "pass"
    assert terminal["finding_relation"]["history_status"] == "unknown"
    assert terminal["finding_relation"]["open_findings"] == [finding]
    assert item["relation_to_previous"] == "new"
    assert "previous_finding_id" not in item
    assert "GATE-FINDING-RELATION-DEGRADED: source=primary-audit detail=unknown" in out


def _previous_round_fixture_objects():
    ledger_path = ROOT / "tests/fixtures/previous-round-pr1063-ledger.json"
    receipt_path = ROOT / "tests/fixtures/previous-round-pr1063-disposition.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = dict(receipt.get("counterevidence") or {})
    if evidence and "gate_rerun" not in evidence:
        evidence["gate_rerun"] = _gate_rerun(receipt["head_sha"])
        receipt["counterevidence"] = evidence
    receipt_bytes = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    repository_id = ledger["primary_identity"]["repository_id"]
    ledger_name = (
        f"codex-review-ledger-v2-{repository_id}-{ledger['head_sha']}-"
        f"{ledger['run_id']}-{ledger['run_attempt']}"
    )
    ledger_key = f"d30/{repository_id}/{ledger_name}/ledger.json"
    latest_key = AGG._ledger_latest_key(repository_id, ledger["pr_number"])
    receipt_name = CONV.disposition_receipt_artifact_name(CONV.parse_disposition_receipt(receipt))
    receipt_key = f"d30/{repository_id}/{receipt_name}/{receipt_name}.json"
    ledger_bytes = ledger_path.read_bytes()
    return ledger, receipt, [
        (ledger_key, ledger_bytes),
        (latest_key, ledger_bytes),
        (receipt_key, receipt_bytes),
    ]


def _render_previous_round_fixture(
    tmp_path, monkeypatch, silo_objects, *, silo_configured=True,
    run_id=35989919390, run_attempt=1,
):
    ledger, _, _ = _previous_round_fixture_objects()
    by_key = dict(silo_objects)

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            raise AssertionError("previous-round findings must not list Silo objects")
        key = argv[argv.index("--key") + 1]
        dest = argv[argv.index("--dest") + 1]
        body = by_key.get(key)
        if body is None:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="Silo object not found")
        Path(dest).write_bytes(body)
        return subprocess.CompletedProcess(argv, 0, stdout=dest, stderr="")

    monkeypatch.setattr(AGG, "_silo_configured", lambda: silo_configured)
    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    monkeypatch.setattr(
        AGG, "_silo_objects_under",
        lambda prefix, timeout_s: [(key, raw) for key, raw in silo_objects if key.startswith(prefix)],
    )
    output = tmp_path / "previous-findings.json"
    rc = AGG.main([
        "--render-previous-context",
        "--repository-id", str(ledger["primary_identity"]["repository_id"]),
        "--pr-number", str(ledger["pr_number"]),
        "--run-id", str(run_id),
        "--run-attempt", str(run_attempt),
        "--previous-json", str(output),
    ])
    return rc, json.loads(output.read_text(encoding="utf-8"))


def test_render_previous_context_projects_real_pr1063_ledger_and_disposition(tmp_path, monkeypatch, capsys):
    ledger, _, objects = _previous_round_fixture_objects()
    assert "open_findings" not in ledger["finding_relation"]
    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, objects)
    out = capsys.readouterr().out

    assert rc == 0
    assert set(payload) == {"available", "detail", "head_sha", "findings", "dispositions", "open_findings"}
    assert payload["available"] is True
    assert payload["head_sha"] == ledger["head_sha"]
    assert payload["findings"] == ledger["review"]["result"]["findings"]
    assert {finding["id"] for finding in payload["open_findings"]} <= {finding["id"] for finding in payload["findings"]}
    assert payload["findings"] and any(
        finding.get("id") == "reliability_alternates_survive_hard_kill"
        for finding in payload["findings"]
    )
    assert len(payload["dispositions"]) == 1
    disposition = payload["dispositions"][0]
    assert disposition["finding_id"] == "reliability_alternates_survive_hard_kill"
    assert disposition["disposition"] == "false-positive"
    assert disposition["counterevidence"]["result"] == "refuted"
    assert all(disposition["counterevidence"][field] for field in ("command", "output", "result", "pointer"))
    assert disposition["head_sha"] == ledger["head_sha"]
    assert set(disposition) == {
        "finding_id", "disposition", "head_sha", "approved_at", "reason", "counterevidence", "tracking_issue",
    }
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=1 dropped=0" in out


def test_render_previous_context_preserves_full_findings_and_prior_head(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    expected_findings = list(ledger["review"]["result"]["findings"])
    template = expected_findings[-1]
    for index in range(31 - len(expected_findings)):
        finding = dict(template)
        finding.update(
            id=f"prior-finding-{index:02}",
            severity="minor",
            file=f"src/prior-{index}.py",
            line=index + 1,
            issue=f"prior issue {index}",
            acceptance=f"prior acceptance {index}",
        )
        expected_findings.append(finding)
    expected_findings[-1].update(
        id="prior-major-after-limit",
        severity="major",
        file="src/last-major.py",
        line=91,
        issue="last major finding",
        acceptance="keep the complete old machine fact",
        quote='raw quote with "quotes" and a newline\nthat must survive',
        anchor={"path": "src/last-major.py", "line": 91, "range": [91, 92]},
    )

    latest_key = AGG._ledger_latest_key(
        ledger["primary_identity"]["repository_id"], ledger["pr_number"],
    )
    expanded_objects = []
    for key, raw in objects:
        if key == latest_key:
            entry = json.loads(raw)
            entry["review"]["result"]["findings"] = expected_findings
            raw = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        expanded_objects.append((key, raw))

    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, expanded_objects)
    output_path = tmp_path / "previous-findings.json"
    written_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert rc == 0
    assert written_payload["findings"] == expected_findings
    assert written_payload.get("head_sha") == ledger["head_sha"]
    assert payload["findings"][-1]["severity"] == "major"
    assert payload["findings"][-1]["quote"] == expected_findings[-1]["quote"]
    assert payload["findings"][-1]["anchor"] == expected_findings[-1]["anchor"]

    loaded = AGG.load_previous_round_findings(
        path=str(output_path), repository_id=ledger["primary_identity"]["repository_id"],
        pr_number=ledger["pr_number"], run_id=35989919390, run_attempt=1,
    )
    assert loaded["head_sha"] == ledger["head_sha"]
    assert loaded["findings"] == expected_findings
    tail = expected_findings[-1]
    current = {
        field: tail[field]
        for field in ("id", "severity", "file", "line", "category", "issue", "acceptance")
    }
    relation = AGG.build_finding_relation([current], loaded)
    assert relation["items"][0]["relation_to_previous"] == "repeat"
    assert relation["items"][0]["previous_finding_id"] == "prior-major-after-limit"


def _anchor_git_heads(root: Path, mode: str) -> tuple[Path, str, str, str | None]:
    remote, work = root / "anchor.git", root / "anchor-work"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    def git(*args):
        return subprocess.run(["git", "-C", str(work), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("config", "user.name", "Gate Test")
    git("config", "user.email", "gate@example.invalid")
    path = work / "src" / "check.py"
    path.parent.mkdir()
    old_lines = [*(f"source line {line}" for line in range(1, 17)), "exact source bytes", "tail"]
    path.write_text("\n".join(old_lines) + "\n")
    git("add", "src/check.py")
    git("commit", "-m", "old anchor")
    old_sha = git("rev-parse", "HEAD")
    git("push", "origin", "HEAD:main")
    if mode == "adjacent":
        new_lines = old_lines[:17] + ["inserted nearby"] + old_lines[17:]
    elif mode in {"shift-delete", "unavailable"}:
        new_lines = old_lines[:16] + ["inserted before quote"] + old_lines[16:]
    else:
        new_lines = [*old_lines[:16], "replacement source bytes", *old_lines[17:]]
    path.write_text("\n".join(new_lines) + "\n")
    git("add", "src/check.py")
    git("commit", "-m", "next source")
    new_sha = git("rev-parse", "HEAD")
    third_sha = None
    if mode == "shift-delete":
        path.write_text("\n".join([*new_lines[:17], "replacement source bytes", *new_lines[18:]]) + "\n")
        git("add", "src/check.py")
        git("commit", "-m", "delete old anchor")
        third_sha = git("rev-parse", "HEAD")
    if mode != "unavailable":
        git("push", "origin", "HEAD:main")
    return remote, old_sha, new_sha, third_sha


@pytest.mark.parametrize(
    "case",
    [
        ("primary_review_v2_with_tier.json", "none", "new", ["open-with-evidence", "new-current"], "pass"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "actual", "silent", [], "pass"),
        ("primary_review_v2_with_withdrawal.json", "actual", "same", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "actual", "mapped", ["reliability_alternates_survive_hard_kill", "mapped-current"], "fail"),
        ("primary_review_v2_with_withdrawal.json", "blank", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "unknown", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "missing", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "empty", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_tier.json", "none", "new", ["new-current"], "fail", "changed"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "adjacent"),
        ("primary_review_v2_with_tier.json", "none", "new", ["open-with-evidence", "new-current"], "fail", "shift-delete"),
        ("primary_review_v2_with_tier.json", "none", "same", ["open-with-evidence"], "pass", "changed"),
        ("primary_review_v2_with_tier.json", "none", "mapped", ["open-with-evidence", "mapped-current"], "fail", "changed"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "unavailable"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "missing-quote"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "wrong-quote"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "line-null"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "unavailable", "review-unavailable"),
    ],
    ids=["old-plus-new", "old-plus-empty", "withdrawal-closes", "same-id-wins", "stable-key-wins",
         "blank-reason-preserves", "unknown-id-preserves", "missing-withdrawal-preserves", "empty-withdrawals-preserves",
         "anchor-deleted", "adjacent-insertion-preserves", "shift-before-delete-three-rounds",
         "re-report-preserves", "mapped-report-preserves",
         "fetch-unavailable-preserves", "missing-quote-preserves", "wrong-quote-preserves",
         "line-null-preserves", "unavailable-review-preserves"],
)
def test_silent_round_carries_open_finding_through_ledger_jsonl(
    tmp_path, case,
):
    audit_fixture, withdrawal_case, report_case, expected_ids, expected_gate = case[:5]
    anchor_case = case[5] if len(case) > 5 else "none"
    ledger_script = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"
    prior_id = (
        "reliability_alternates_survive_hard_kill"
        if audit_fixture.endswith("with_withdrawal.json") else "open-with-evidence"
    )
    finding = {
        "id": prior_id, "severity": "major", "file": "src/check.py", "line": 17,
        "category": "correctness", "issue": "the quote must remain open",
        "acceptance": "retain the original anchor", "quote": "exact source bytes",
        "anchor": {"path": "src/check.py", "line": 17, "range": [17, 18]},
    }
    remote = None
    previous_head, current_head = "a" * 40, "b" * 40
    third_head = None
    if anchor_case in {"changed", "adjacent", "shift-delete", "same", "mapped", "unavailable", "missing-quote", "wrong-quote"}:
        remote, previous_head, current_head, third_head = _anchor_git_heads(tmp_path, anchor_case)
    if anchor_case == "line-null":
        finding.update(line=None)
        finding.pop("quote"); finding.pop("anchor")
    elif anchor_case == "missing-quote":
        finding.pop("quote")
    elif anchor_case == "wrong-quote":
        finding["quote"] = " exact source bytes "
    round_dir = tmp_path / "round-2"
    audit_dir = round_dir / "audit"
    audit_dir.mkdir(parents=True)
    identity = AGG.Identity(repository_id=123, head_sha=current_head, run_id=1000, run_attempt=1, pr=42)
    audit_bytes = (ROOT / "tests" / "data" / audit_fixture).read_bytes()
    audit = json.loads(audit_bytes)
    if audit_fixture.endswith("with_withdrawal.json"):
        # This is the captured producer result; adapter metadata contains local paths.
        canonical_producer_bytes = json.dumps(audit, ensure_ascii=False, separators=(",", ":")).encode()
        assert hashlib.sha256(canonical_producer_bytes).hexdigest() == "7a5df2c44f5664436ceb93ada76cc3723ce0b74424b72e56ad842918633913e7"
        audit = json.loads((ROOT / "tests" / "data" / "primary_review_v2_with_tier.json").read_bytes())
        audit["result"].update(json.loads(audit_bytes)["result"])
    if withdrawal_case == "actual":
        assert audit["result"]["withdrawals"] == [{
            "finding_id": prior_id,
            "reason": "The current review confirmed each job uses a disposable container, so this state cannot survive the job.",
        }]
    audit.update(repository="zlxlabs/gate", repository_id=123, pr=42, run_id=identity.run_id,
                 run_attempt=1, head_sha=identity.head_sha)
    if anchor_case in {"changed", "shift-delete"} and report_case == "new":
        verdict = "fail"
    elif anchor_case == "review-unavailable":
        verdict = "unavailable"
    else:
        verdict = "pass"
    audit["verdict"] = audit["result"]["verdict"] = verdict
    if withdrawal_case == "blank":
        audit["result"]["withdrawals"][0]["reason"] = "  "
    elif withdrawal_case == "unknown":
        audit["result"]["withdrawals"][0]["finding_id"] = "unknown-finding"
    elif withdrawal_case == "missing":
        audit["result"].pop("withdrawals")
    elif withdrawal_case == "empty":
        audit["result"]["withdrawals"] = []
    elif withdrawal_case == "none":
        audit["result"].pop("withdrawals", None)
    if report_case == "new":
        current_findings = [{
            "id": "new-current", "severity": "major", "file": "src/new-check.py", "line": 29,
            "category": "correctness", "issue": "a distinct current-round finding",
            "acceptance": "retain this finding with the historical open finding",
        }]
    elif report_case == "same":
        current_findings = [{**finding, "quote": "rechecked source bytes"}]
    elif report_case == "mapped":
        current_findings = [{
            **finding, "id": "mapped-current", "issue": "a newly reported claim at the same location",
            "acceptance": "review the changed claim at the same location",
        }]
    else:
        current_findings = []
    audit["result"]["findings"] = current_findings
    original_receipt_audit = {
        field: audit["result"].get(field)
        for field in ("finding_dispositions", "final_finding_dispositions")
    }
    audit_path = audit_dir / "primary-review-audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    previous_path = round_dir / "previous-findings.json"
    previous_path.write_text(json.dumps({
        "available": True, "detail": "", "head_sha": previous_head, "findings": [finding],
    }), encoding="utf-8")

    clean_env = os.environ.copy()
    for key in ("SILO_ENDPOINT", "SILO_STORE", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORY",
                "REPOSITORY_ID", "HEAD_SHA", "RUN_ID", "RUN_ATTEMPT", "PR_NUMBER"):
        clean_env.pop(key, None)
    if anchor_case != "none":
        clean_env.update({"GITHUB_REPOSITORY": "zlxlabs/gate", "REPOSITORY_ID": "123",
                          "HEAD_SHA": current_head, "RUN_ID": "1000", "RUN_ATTEMPT": "1",
                          "PR_NUMBER": "42", "GH_TOKEN": "fixture-token"})
    if remote is not None:
        git_home = tmp_path / "git-home"
        git_home.mkdir()
        git_config = git_home / ".gitconfig"
        git_config.write_text(
            f'[url "file://{remote}"]\n\tinsteadOf = https://github.com/zlxlabs/gate.git\n',
            encoding="utf-8",
        )
        clean_env.update(HOME=str(git_home), GIT_CONFIG_GLOBAL=str(git_config))
    annotation = subprocess.run([
        sys.executable, str(MODULE_PATH), "--annotate-primary-audit",
        "--audit-path", str(audit_path), "--previous-findings-path", str(previous_path),
    ], cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False)
    assert annotation.returncode == 0, annotation.stderr
    if withdrawal_case == "blank":
        assert "GATE-WITHDRAWAL-IGNORED: invalid explicit withdrawal record" in annotation.stdout
    audit_bytes = audit_path.read_bytes()
    annotated_audit = json.loads(audit_bytes)
    assert annotated_audit["finding_relation"]["history_status"] == "available"
    expected_carry = {**finding, "line": 18} if anchor_case == "shift-delete" else finding
    expected_open_findings = [expected_carry, *current_findings]
    if report_case == "same":
        expected_open_findings = [{**finding, **current_findings[0]}]
    if withdrawal_case == "actual" and report_case == "silent":
        expected_open_findings = []
    if anchor_case == "changed" and report_case == "new":
        expected_open_findings = current_findings
    assert annotated_audit["finding_relation"]["open_findings"] == expected_open_findings
    assert [item["id"] for item in annotated_audit["finding_relation"]["open_findings"]] == expected_ids
    assert [item["id"] for item in annotated_audit["result"]["findings"]] == [item["id"] for item in current_findings]
    expected_counts = {
        "new": int(report_case == "new"),
        "repeat": int(report_case == "same"),
        "conflict": int(report_case == "mapped"),
    }
    assert annotated_audit["finding_relation"]["counts"] == expected_counts
    assert [item["id"] for item in annotated_audit["finding_relation"]["items"]] == [
        item["id"] for item in current_findings
    ]
    if anchor_case == "shift-delete":
        assert annotated_audit["result"]["findings"] == [
            {**current_findings[0], "relation_to_previous": "new"}
        ]
        assert annotated_audit["finding_relation"]["items"][0]["line"] == current_findings[0]["line"]
    assert {field: annotated_audit["result"].get(field) for field in original_receipt_audit} == original_receipt_audit
    assert annotated_audit["result"].get("withdrawals") == audit["result"].get("withdrawals")

    summary = round_dir / "summary.md"
    primary_result = (
        "failure" if anchor_case == "review-unavailable"
        or (anchor_case in {"changed", "shift-delete"} and report_case == "new") else "success"
    )
    gate_args = _cli_args(audit_dir, summary, identity=identity, primary_result=primary_result)
    gate = subprocess.run(
        [sys.executable, str(MODULE_PATH), *gate_args], cwd=ROOT, env=clean_env,
        capture_output=True, text=True, check=False,
    )
    assert gate.returncode == (1 if expected_gate in {"fail", "unavailable"} else 0), gate.stderr
    terminal_path = summary.with_name("gate-terminal.json")
    terminal_bytes = terminal_path.read_bytes()
    terminal = json.loads(terminal_bytes)
    assert terminal["gate_result"] == expected_gate
    assert terminal["finding_relation"]["open_findings"] == expected_open_findings
    assert [item["id"] for item in terminal["finding_relation"]["open_findings"]] == expected_ids
    assert terminal["finding_relation"]["items"] == annotated_audit["finding_relation"]["items"]
    assert terminal["finding_relation"]["counts"] == expected_counts
    if current_findings:
        legacy_round = round_dir / "legacy"
        legacy_dir = legacy_round / "audit"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "primary-review-audit.json").write_text(
            json.dumps(audit), encoding="utf-8",
        )
        legacy_summary = legacy_round / "summary.md"
        legacy_gate = subprocess.run(
            [sys.executable, str(MODULE_PATH), *_cli_args(
                legacy_dir, legacy_summary, identity=identity,
                primary_result="failure" if anchor_case in {"changed", "shift-delete"} and report_case == "new" else "success")],
            cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False,
        )
        assert legacy_gate.returncode == (1 if anchor_case in {"changed", "shift-delete"} and report_case == "new" else 0), legacy_gate.stderr
        legacy_terminal = json.loads(legacy_summary.with_name("gate-terminal.json").read_bytes())
        assert legacy_terminal["finding_relation"]["history_status"] == "unknown"
        assert [item["id"] for item in legacy_terminal["finding_relation"]["open_findings"]] == [
            item["id"] for item in current_findings
        ]

    preflight_path = round_dir / "preflight.json"
    preflight_path.write_text(json.dumps({
        "diff_lines": 100, "classification": "single", "review_plan": "single",
        "thresholds": {"single_turn_lines": 4000},
    }), encoding="utf-8")
    output_path = round_dir / "ledger.jsonl"
    ledger = subprocess.run([
        sys.executable, str(ledger_script), "--audit-path", str(audit_path),
        "--preflight-path", str(preflight_path), "--install-path", str(round_dir / "missing-install.json"),
        "--terminal-path", str(terminal_path), "--output", str(output_path),
        "--repository", "zlxlabs/gate", "--pr-number", "42", "--run-id", str(identity.run_id),
        "--run-attempt", "1", "--head-sha", identity.head_sha, "--expected-repository-id", "123",
        "--expected-base-sha", audit["base_sha"], "--expected-caller-sha", audit["caller_sha"],
        "--expected-reusable-workflow-sha", audit["reusable_workflow_sha"], "--codex-expected", "true",
    ], cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False)
    assert ledger.returncode == 0, ledger.stderr
    ledger_bytes = output_path.read_bytes()
    assert ledger_bytes.endswith(b"\n")
    ledger_entry = json.loads(ledger_bytes.splitlines()[0])
    assert ledger_entry["finding_relation"]["open_findings"] == expected_open_findings
    assert [item["id"] for item in ledger_entry["finding_relation"]["open_findings"]] == expected_ids
    assert ledger_entry["finding_relation"]["items"] == annotated_audit["finding_relation"]["items"]
    assert ledger_entry["finding_relation"]["counts"] == expected_counts

    silo_store = round_dir / "silo_store.py"
    silo_store.write_text(
        "import os, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'get':\n"
        "    shutil.copyfile(os.environ['C7_LEDGER_PATH'], args[args.index('--dest') + 1])\n"
        "elif args[0] != 'list':\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    loader_env = {**clean_env, "SILO_ENDPOINT": "local-test", "SILO_STORE": str(silo_store),
                  "C7_LEDGER_PATH": str(output_path)}
    previous_round_path = round_dir / "next-round-findings.json"
    loader = subprocess.run([
        sys.executable, str(MODULE_PATH), "--render-previous-context", "--repository-id", "123",
        "--pr-number", "42", "--run-id", "1001", "--run-attempt", "1",
        "--previous-json", str(previous_round_path),
    ], cwd=ROOT, env=loader_env, capture_output=True, text=True, check=False)
    assert loader.returncode == 0, loader.stderr
    previous_bytes = previous_round_path.read_bytes()
    assert json.loads(previous_bytes)["findings"] == expected_open_findings
    assert [item["id"] for item in json.loads(previous_bytes)["findings"]] == expected_ids
    if anchor_case == "shift-delete":
        third_audit = json.loads(audit_path.read_bytes())
        third_audit.pop("finding_relation")
        third_audit.update(run_id=1001, head_sha=third_head, verdict="pass")
        third_audit["result"].update(verdict="pass", findings=[])
        third_path = audit_dir / "round-3-primary-review-audit.json"
        third_path.write_text(json.dumps(third_audit), encoding="utf-8")
        third_env = {**clean_env, "HEAD_SHA": third_head, "RUN_ID": "1001"}
        third_annotation = subprocess.run([
            sys.executable, str(MODULE_PATH), "--annotate-primary-audit",
            "--audit-path", str(third_path), "--previous-findings-path", str(previous_round_path),
        ], cwd=ROOT, env=third_env, capture_output=True, text=True, check=False)
        assert third_annotation.returncode == 0, third_annotation.stderr
        closed_audit = json.loads(third_path.read_bytes())
        assert closed_audit["result"]["findings"] == []
        assert closed_audit["finding_relation"]["open_findings"] == [current_findings[0]]
        assert closed_audit["finding_relation"]["items"] == []
    if (withdrawal_case == "actual" and report_case == "silent") or (anchor_case == "changed" and report_case == "new"):
        reopened = json.loads(audit_path.read_bytes())
        reopened.pop("finding_relation")
        reopened.update(run_id=1001, head_sha="c" * 40)
        reopened["result"]["findings"] = [finding]
        reopened["result"]["withdrawals"] = []
        reopened_path = audit_dir / "reopened-primary-audit.json"
        reopened_path.write_text(json.dumps(reopened), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(MODULE_PATH), "--annotate-primary-audit",
            "--audit-path", str(reopened_path), "--previous-findings-path", str(previous_round_path),
        ], cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        reopened_relation = json.loads(reopened_path.read_bytes())["finding_relation"]
        expected_reopened_ids = [prior_id] if withdrawal_case == "actual" else ["new-current", prior_id]
        assert [item["id"] for item in reopened_relation["open_findings"]] == expected_reopened_ids
        assert reopened_relation["counts"] == {"new": 1, "repeat": 0, "conflict": 0}


def test_render_previous_context_keeps_same_head_older_attempt(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    rc, payload = _render_previous_round_fixture(
        tmp_path, monkeypatch, objects,
        run_id=ledger["run_id"], run_attempt=ledger["run_attempt"] + 1,
    )

    assert rc == 0
    assert payload["available"] is True
    assert payload.get("head_sha") == ledger["head_sha"]
    assert payload["findings"] == ledger["review"]["result"]["findings"]


@pytest.mark.parametrize("latest_state", ["same-attempt", "future-attempt"])
def test_render_previous_context_degrades_current_or_future_attempt(tmp_path, monkeypatch, capsys, latest_state):
    ledger, _, objects = _previous_round_fixture_objects()
    run_attempt = ledger["run_attempt"]
    if latest_state == "future-attempt":
        latest_key = AGG._ledger_latest_key(ledger["primary_identity"]["repository_id"], ledger["pr_number"])
        future_objects = []
        for key, raw in objects:
            if key == latest_key:
                entry = json.loads(raw)
                entry["run_attempt"] += 1
                entry["primary_identity"]["run_attempt"] += 1
                raw = json.dumps(entry, ensure_ascii=False).encode("utf-8")
            future_objects.append((key, raw))
        objects = future_objects
    rc, payload = _render_previous_round_fixture(
        tmp_path, monkeypatch, objects, run_id=ledger["run_id"], run_attempt=run_attempt,
    )

    assert rc == 0
    assert payload == {"available": False, "detail": "silo-ledger-latest-not-prior", "head_sha": None, "findings": [], "dispositions": [], "open_findings": []}
    assert (
        "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger "
        "detail=silo-ledger-latest-not-prior"
    ) in capsys.readouterr().out


def test_render_previous_context_keeps_missing_prior_head_unknown(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    latest_key = AGG._ledger_latest_key(
        ledger["primary_identity"]["repository_id"], ledger["pr_number"],
    )
    no_head_objects = []
    for key, raw in objects:
        if key == latest_key:
            entry = json.loads(raw)
            entry.pop("head_sha")
            raw = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        no_head_objects.append((key, raw))

    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, no_head_objects)
    output_path = tmp_path / "previous-findings.json"
    loaded = AGG.load_previous_round_findings(
        path=str(output_path), repository_id=ledger["primary_identity"]["repository_id"],
        pr_number=ledger["pr_number"], run_id=35989919390, run_attempt=1,
    )

    assert rc == 0
    assert ledger["primary_identity"]["head_sha"]
    assert "head_sha" in payload
    assert payload["head_sha"] is None
    assert "head_sha" in loaded
    assert loaded["head_sha"] is None
    assert loaded["findings"] == ledger["review"]["result"]["findings"]


def test_render_previous_context_drops_invalid_dispositions_and_reports_count(tmp_path, monkeypatch, capsys):
    _, valid, objects = _previous_round_fixture_objects()
    repo_objects = list(objects)
    base_key, base_raw = repo_objects[-1]
    base = json.loads(base_raw)
    for suffix, mutate in (
        ("missing-pointer", lambda item: item["counterevidence"].pop("pointer")),
        ("confirmed", lambda item: item["counterevidence"].update(result="confirmed")),
    ):
        invalid = json.loads(json.dumps(base))
        mutate(invalid)
        repo_objects.append((f"{base_key}-{suffix}", json.dumps(invalid).encode("utf-8")))

    _, payload = _render_previous_round_fixture(tmp_path, monkeypatch, repo_objects)
    out = capsys.readouterr().out

    assert len(payload["dispositions"]) == 1
    assert payload["dispositions"][0]["finding_id"] == valid["finding_id"]
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=1 dropped=2" in out


def test_render_previous_context_writes_only_previous_findings_json(tmp_path, monkeypatch):
    _, _, objects = _previous_round_fixture_objects()
    before = {path.name for path in tmp_path.iterdir()}
    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, objects)
    assert rc == 0
    assert set(payload) == {"available", "detail", "head_sha", "findings", "dispositions", "open_findings"}
    assert {path.name for path in tmp_path.iterdir()} - before == {"previous-findings.json"}


def test_render_previous_context_rejects_output_flag(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setattr(
        AGG, "_silo_objects_under",
        lambda prefix, timeout_s: [(key, raw) for key, raw in objects if key.startswith(prefix)],
    )
    target = tmp_path / "x"
    with pytest.raises(SystemExit):
        AGG.main([
            "--render-previous-context",
            "--repository-id", str(ledger["primary_identity"]["repository_id"]),
            "--pr-number", str(ledger["pr_number"]),
            "--run-id", "35989919390",
            "--run-attempt", "1",
            "--output", str(target),
            "--previous-json", str(tmp_path / "previous-findings.json"),
        ])
    assert not target.exists()


def test_previous_findings_context_renderer_removed():
    assert not hasattr(AGG, "render_previous_findings_context")
    assert not hasattr(AGG, "PREVIOUS_CONTEXT_MAX_CHARS")


def test_render_previous_context_limits_dispositions_and_projects_deferred(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone

    _, base, objects = _previous_round_fixture_objects()
    _, receipt_raw = objects[-1]
    template = json.loads(receipt_raw)
    repo_id = template["repository_id"]
    receipts = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(35):
        item = json.loads(json.dumps(template))
        item["finding_id"] = f"finding-{index:02}"
        item["approved_at"] = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        item["reason"] = "r" * 300
        item["counterevidence"]["command"] = "c" * 300
        item["counterevidence"]["output"] = "o" * 300
        item["counterevidence"]["pointer"] = "p" * 300
        if index == 34:
            item["disposition"] = "deferred"
            item.pop("counterevidence")
            item["tracking_issue"] = "#123"
        receipts.append((f"d30/{repo_id}/gate-disposition-receipt-v3-test-{index}/receipt.json", json.dumps(item).encode()))
    ledger_objects = [pair for pair in objects if "codex-review-ledger-" in pair[0]]

    _, payload = _render_previous_round_fixture(tmp_path, monkeypatch, ledger_objects + receipts)
    out = capsys.readouterr().out

    assert len(payload["dispositions"]) == 30
    assert [item["finding_id"] for item in payload["dispositions"]] == [f"finding-{i:02}" for i in range(34, 4, -1)]
    assert payload["dispositions"][0]["counterevidence"] is None
    assert payload["dispositions"][0]["tracking_issue"] == "#123"
    assert all(len(value) <= 240 for item in payload["dispositions"] for value in _string_values(item))
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=30 dropped=5" in out


def _string_values(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for nested in value.values() for part in _string_values(nested)]
    if isinstance(value, list):
        return [part for nested in value for part in _string_values(nested)]
    return []


def test_render_previous_context_silo_unconfigured_has_complete_empty_contract(tmp_path, monkeypatch, capsys):
    _, _, objects = _previous_round_fixture_objects()
    rc, payload = _render_previous_round_fixture(
        tmp_path, monkeypatch, objects, silo_configured=False,
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert payload == {"available": False, "detail": "silo-not-configured", "head_sha": None, "findings": [], "dispositions": [], "open_findings": []}
    assert "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=silo-not-configured" in out


def _latest_store(tmp_path, *, body):
    argv_log = tmp_path / "silo-argv.jsonl"
    body_path = tmp_path / "latest-body.jsonl"
    if body is not None:
        body_path.write_bytes(body)
    store = tmp_path / "fake_silo.py"
    store.write_text(
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(argv_log)!r})\n"
        "with log.open('a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + chr(10))\n"
        "args = sys.argv[1:]\n"
        "if args[0] != 'get':\n"
        "    raise SystemExit('list must not be called' if args[0] == 'list' else args[0])\n"
        f"src = pathlib.Path({str(body_path)!r})\n"
        "if not src.exists():\n"
        "    raise SystemExit(2)\n"
        "dest = pathlib.Path(args[args.index('--dest') + 1])\n"
        "dest.parent.mkdir(parents=True, exist_ok=True)\n"
        "dest.write_bytes(src.read_bytes())\n",
        encoding="utf-8",
    )
    return store, argv_log


def _assert_single_latest_get(argv_log, key):
    argvs = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(argvs) == 1
    argv = argvs[0]
    assert argv[0] == "get"
    assert argv[argv.index("--key") + 1] == key
    assert "list" not in argv


def test_previous_findings_get_latest_once_without_list(tmp_path, monkeypatch):
    ledger, _, _ = _previous_round_fixture_objects()
    repo_id = ledger["primary_identity"]["repository_id"]
    key = AGG._ledger_latest_key(repo_id, ledger["pr_number"])
    store, argv_log = _latest_store(tmp_path, body=(ROOT / "tests/fixtures/previous-round-pr1063-ledger.json").read_bytes())
    monkeypatch.setenv("SILO_STORE", str(store))
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    head_sha, findings = AGG._previous_findings_from_silo(
        repository_id=repo_id, pr_number=ledger["pr_number"], run_id=35989919390, run_attempt=1,
    )
    assert head_sha == ledger["head_sha"]
    assert any(item.get("id") == "reliability_alternates_survive_hard_kill" for item in findings)
    _assert_single_latest_get(argv_log, key)


def test_previous_findings_absent_latest_degrades_without_list(tmp_path, monkeypatch, capsys):
    ledger, _, _ = _previous_round_fixture_objects()
    repo_id = ledger["primary_identity"]["repository_id"]
    key = AGG._ledger_latest_key(repo_id, ledger["pr_number"])
    store, argv_log = _latest_store(tmp_path, body=None)
    monkeypatch.setenv("SILO_STORE", str(store))
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    previous = AGG.load_previous_round_findings(
        path=None, repository_id=repo_id, pr_number=ledger["pr_number"],
        run_id=35989919390, run_attempt=1,
    )
    assert previous == {
        "available": False, "detail": "silo-ledger-latest-absent", "head_sha": None, "findings": [],
    }
    assert "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=silo-ledger-latest-absent" in capsys.readouterr().out
    _assert_single_latest_get(argv_log, key)


def test_previous_findings_not_prior_latest_degrades_without_list(tmp_path, monkeypatch, capsys):
    ledger, _, _ = _previous_round_fixture_objects()
    repo_id = ledger["primary_identity"]["repository_id"]
    key = AGG._ledger_latest_key(repo_id, ledger["pr_number"])
    store, argv_log = _latest_store(
        tmp_path, body=(ROOT / "tests/fixtures/previous-round-pr1063-ledger.json").read_bytes(),
    )
    monkeypatch.setenv("SILO_STORE", str(store))
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    previous = AGG.load_previous_round_findings(
        path=None, repository_id=repo_id, pr_number=ledger["pr_number"],
        run_id=ledger["run_id"], run_attempt=ledger["run_attempt"],
    )
    assert previous == {
        "available": False, "detail": "silo-ledger-latest-not-prior", "head_sha": None, "findings": [],
    }
    assert "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=silo-ledger-latest-not-prior" in capsys.readouterr().out
    _assert_single_latest_get(argv_log, key)
