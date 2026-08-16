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
import socket
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"


def _module():
    spec = importlib.util.spec_from_file_location("gate_aggregate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    # dataclasses (used by aggregate.py) resolves annotation types via
    # sys.modules[cls.__module__] under `from __future__ import annotations` —
    # it must be registered before exec_module, or the dataclass decorator
    # crashes with "'NoneType' object has no attribute '__dict__'".
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGG = _module()

IDENTITY = AGG.Identity(repository_id=123, head_sha="a" * 40, run_id=999, run_attempt=1, pr=42)


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


def _base_kwargs(**overrides):
    kwargs = dict(
        quality_result="success",
        primary_result="success",
        runner="self",
        is_draft=False,
        review_expected=True,
        audit=_valid_primary_record(),
        audit_error=None,
        identity=IDENTITY, audit_source_attempt=IDENTITY.run_attempt, audit_artifact_name="primary-audit-v2-1",
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
        **_base_kwargs(primary_result="skipped", is_draft=False, review_expected=False, audit=None, audit_error=None)
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


# ── find_audit_file IO edge cases ────────────────────────────────────────

def test_find_audit_file_missing_directory(tmp_path):
    record, error = AGG.find_audit_file(tmp_path / "does-not-exist")
    assert record is None
    assert "not present" in error


def test_find_audit_file_empty_directory(tmp_path):
    record, error = AGG.find_audit_file(tmp_path)
    assert record is None
    assert "no *.json" in error


def test_find_audit_file_multiple_files(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")
    record, error = AGG.find_audit_file(tmp_path)
    assert record is None
    assert "found 2" in error


def test_find_audit_file_invalid_json(tmp_path):
    (tmp_path / "a.json").write_text("{not valid json")
    record, error = AGG.find_audit_file(tmp_path)
    assert record is None
    assert "could not parse" in error


def test_find_audit_file_valid_json_roundtrips(tmp_path):
    payload = _valid_primary_record()
    (tmp_path / "primary-review-audit.json").write_text(json.dumps(payload))
    record, error = AGG.find_audit_file(tmp_path)
    assert error is None
    assert record == payload


def test_find_audit_file_non_dict_json_roundtrips_as_is(tmp_path):
    # find_audit_file itself doesn't judge shape — that's evaluate()'s job.
    (tmp_path / "weird.json").write_text(json.dumps([1, 2, 3]))
    record, error = AGG.find_audit_file(tmp_path)
    assert error is None
    assert record == [1, 2, 3]


# ── CLI end-to-end (exit codes + step summary) ───────────────────────────

def _cli_args(audit_dir, summary_path, **overrides):
    values = dict(
        quality_result="success",
        primary_result="success",
        runner="self",
        is_draft="false",
        review_expected="true",
        repository_id=str(IDENTITY.repository_id),
        head_sha=IDENTITY.head_sha,
        run_id=str(IDENTITY.run_id),
        run_attempt=str(IDENTITY.run_attempt),
        pr_number=str(IDENTITY.pr), repository="zlxlabs/gate",
        audit_source_attempt=str(IDENTITY.run_attempt), audit_artifact_name="primary-audit-v2-1", terminal_path=str(Path(summary_path).with_name("gate-terminal.json")),
    )
    values.update(overrides)
    args = [
        "--quality-result", values["quality_result"],
        "--primary-result", values["primary_result"],
        "--runner", values["runner"],
        "--is-draft", values["is_draft"],
        "--review-expected", values["review_expected"],
        "--repository-id", values["repository_id"], "--repository", values["repository"],
        "--head-sha", values["head_sha"],
        "--run-id", values["run_id"],
        "--run-attempt", values["run_attempt"],
        "--audit-artifact-name", values["audit_artifact_name"], "--terminal-path", values["terminal_path"],
        "--audit-dir", str(audit_dir),
        "--summary-path", str(summary_path),
    ]
    if values.get("pr_number") is not None:
        args.extend(["--pr-number", values["pr_number"]])
    if "audit_source_attempt" in values:
        args.extend(["--audit-source-attempt", values["audit_source_attempt"]])
    if values.get("panel_delivery_path") is not None:
        args.extend(["--panel-delivery-path", values["panel_delivery_path"]])
    return args

def test_main_exit_code_zero_on_pass(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary_path))
    assert rc == 0
    assert "pass" in summary_path.read_text() and json.loads(summary_path.with_name("gate-terminal.json").read_text())["kind"] == "gate_terminal"


def test_main_summary_and_notice_include_cross_attempt_source(capsys, tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit = _valid_primary_record(run_attempt=1)
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(audit))
    summary_path = tmp_path / "summary.md"
    args = _cli_args(audit_dir, summary_path, run_attempt="2", audit_source_attempt="1")
    rc = AGG.main(args)
    assert rc == 0
    assert "source run_attempt=1" in summary_path.read_text()
    assert "source run_attempt=1" in capsys.readouterr().out


def test_main_exit_code_nonzero_on_missing_audit(tmp_path):
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(tmp_path / "nope", summary_path))
    assert rc == 1
    assert "Synthetic audit generated" in summary_path.read_text()
    assert json.loads(summary_path.with_name("gate-terminal.json").read_text())["audit"] == {"available": False, "source_attempt": None, "artifact_name": None}


def test_main_exit_code_nonzero_on_malformed_boolean_input(tmp_path):
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(tmp_path / "nope", summary_path, is_draft="banana"))
    assert rc == 1
    text = summary_path.read_text()
    assert "malformed boolean input" in text


def test_main_exit_code_nonzero_on_unknown_runner(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary_path, runner="slef"))
    assert rc == 1
    assert "runner input" in summary_path.read_text()

def _assert_terminal_classification(outcome, expected):
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == expected
    if expected[0] == "integration_error":
        assert (outcome.audit_available, outcome.audit_source_attempt, outcome.audit_artifact_name) == (False, None, None)

@pytest.mark.parametrize("kwargs,expected", [
    ({"quality_result": "failure"}, ("ci_failure", "quality_failure", "fail")), ({"quality_result": "cancelled"}, ("ci_failure", "quality_cancelled", "fail")),
    ({"quality_result": "skipped"}, ("ci_failure", "quality_skipped", "fail")), ({"primary_result": "skipped", "is_draft": True, "review_expected": False, "audit": None}, ("expected_skip", "review_not_expected", "skipped")),
    ({}, ("code_pass", "primary_pass", "pass")), ({"primary_result": "failure", "audit": _valid_primary_record(verdict="fail")}, ("code_fail", "primary_findings", "fail")),
    ({"primary_result": "failure", "audit": _valid_primary_record(verdict="unavailable")}, ("review_unavailable", "primary_unavailable", "unavailable")), ({"primary_result": "cancelled", "audit": None}, ("review_unavailable", "primary_cancelled", "unavailable")),
    ({"primary_result": "skipped", "audit": None}, ("integration_error", "unexpected_primary_skip", "unavailable")), ({"audit": None, "audit_error": "missing"}, ("integration_error", "audit_missing", "unavailable")),
    ({"audit": _valid_primary_record(kind="synthetic_primary")}, ("integration_error", "audit_invalid", "unavailable")), ({"audit_source_attempt": 2}, ("integration_error", "audit_source_mismatch", "unavailable")),
    ({"primary_result": "failure", "audit": _valid_primary_record(verdict="pass")}, ("integration_error", "job_audit_mismatch", "unavailable")), ({"quality_result": "failure", "audit": None, "audit_error": "missing"}, ("integration_error", "audit_missing", "unavailable")),
    ({"primary_result": "success", "audit": _valid_primary_record(verdict="fail")}, ("integration_error", "job_audit_mismatch", "unavailable")), ({"primary_result": "success", "audit": _valid_primary_record(verdict="unavailable")}, ("integration_error", "job_audit_mismatch", "unavailable")),
    ({"audit_source_attempt": None}, ("integration_error", "audit_source_mismatch", "unavailable")), ({"audit_artifact_name": ""}, ("integration_error", "audit_source_mismatch", "unavailable")), ({"audit_artifact_name": None}, ("integration_error", "audit_source_mismatch", "unavailable")),
])
def test_terminal_classification_matrix(kwargs, expected):
    _assert_terminal_classification(AGG.evaluate(**_base_kwargs(**kwargs)), expected)

def test_terminal_publish_barrier_failures(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    summary_path.mkdir()
    with pytest.raises(OSError):
        AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path)))
    assert not terminal_path.exists()
    original_write_text = Path.write_text
    def partial_write_then_fail(path, data, **kwargs):
        if path.name == ".gate-terminal.json.tmp":
            path.write_bytes(data.encode())
            raise OSError("simulated partial terminal write")
        return original_write_text(path, data, **kwargs)
    monkeypatch.setattr(Path, "write_text", partial_write_then_fail)
    with pytest.raises(OSError, match="simulated partial terminal write"):
        AGG.main(_cli_args(audit_dir, tmp_path / "summary.txt", terminal_path=str(terminal_path)))
    assert not terminal_path.exists()


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
  }
}
"""


def test_terminal_envelope_bytes_unchanged_by_rendering_work():
    # gate-terminal.json is a cross-job publish boundary whose schema this
    # card must NOT touch: lock the envelope bytes for a fixed outcome.
    outcome = AGG.Outcome(
        ok=True, classification="code_pass", reason_code="primary_pass", gate_result="pass",
        audit_available=True, audit_source_attempt=1, audit_artifact_name="primary-audit-v2-1",
    )
    envelope = AGG.build_terminal_envelope(
        repository="zlxlabs/gate", identity=IDENTITY, quality_result="success",
        primary_result="success", review_expected=True, is_draft=False, runner="self", outcome=outcome,
    )
    assert json.dumps(envelope, ensure_ascii=False, indent=2) + "\n" == _TERMINAL_GOLDEN


def _visible_scenario(tmp_path, overrides, audit_record="__default__"):
    audit_dir = tmp_path / "audit"
    if audit_record is not None:
        audit_dir.mkdir()
        record = _valid_primary_record() if audit_record == "__default__" else audit_record
        (audit_dir / "primary-review-audit.json").write_text(json.dumps(record))
    summary_path = tmp_path / "summary.md"
    return audit_dir, summary_path, _cli_args(audit_dir, summary_path, **overrides)


@pytest.mark.parametrize(
    "overrides,audit_record,classification,reason_code,gate_result,exit_code,annotation",
    [
        ({}, "__default__", "code_pass", "primary_pass", "pass", 0, "::notice::"),
        (
            {"primary_result": "failure"}, _valid_primary_record(verdict="fail"),
            "code_fail", "primary_findings", "fail", 1, "::error::",
        ),
        (
            {"primary_result": "skipped", "is_draft": "true", "review_expected": "false"}, None,
            "expected_skip", "review_not_expected", "skipped", 0, "::notice::",
        ),
        (
            {"primary_result": "failure"}, _valid_primary_record(verdict="unavailable"),
            "review_unavailable", "primary_unavailable", "unavailable", 1, "::error::",
        ),
        (
            {"quality_result": "failure"}, "__default__",
            "ci_failure", "quality_failure", "fail", 1, "::error::",
        ),
        ({}, None, "integration_error", "audit_missing", "unavailable", 1, "::error::"),
    ],
)
def test_visible_terminal_state_axis(
    capsys, tmp_path, overrides, audit_record, classification, reason_code, gate_result, exit_code, annotation,
):
    _, summary_path, args = _visible_scenario(tmp_path, overrides, audit_record)
    rc = AGG.main(args)
    out = capsys.readouterr().out
    text = summary_path.read_text()

    # Exit code is exactly the pre-existing pass/fail semantic (skipped -> 0).
    assert rc == exit_code

    # Output slot 1 — Summary top line shows the four-state gate_result, and
    # never one of the other three states (skipped must not read as pass,
    # unavailable must not read as fail).
    assert f"**Result: {gate_result}**" in text
    for other in AGG.GATE_RESULT_DOMAIN:
        if other != gate_result:
            assert f"**Result: {other}**" not in text

    # Output slot 2 — Summary body exposes all three terminal fields.
    assert (
        f"classification=`{classification}`, reason_code=`{reason_code}`, gate_result=`{gate_result}`"
    ) in text

    # Output slot 3 — annotation of the expected type carrying reason_code,
    # visible on the checks list page without expanding the run.
    assert f"{annotation}gate terminal state:" in out
    assert f"reason_code={reason_code}" in out
    opposite = "::error::" if annotation == "::notice::" else "::notice::"
    assert f"{opposite}gate terminal state:" not in out


def test_visible_expected_skip_summary_names_draft_as_the_skip_reason(capsys, tmp_path):
    _, summary_path, args = _visible_scenario(
        tmp_path, {"primary_result": "skipped", "is_draft": "true", "review_expected": "false"}, None,
    )
    assert AGG.main(args) == 0
    text = summary_path.read_text()
    assert "**Result: skipped**" in text
    assert "draft=True" in text


# ── wording axis (issue #43): unreadable-conclusion ≠ reviewer rejection ────


@pytest.mark.parametrize(
    "kwargs,must_say,must_not_say",
    [
        (
            {"primary_result": "failure", "audit": _valid_primary_record(verdict="fail")},
            ["REJECTED", "Problems"],
            ["could NOT be read"],
        ),
        (
            {"audit": None, "audit_error": "missing"},
            ["could NOT be read", "check the primary job logs", "not a reviewer rejection"],
            ["REJECTED"],
        ),
        (
            {"audit": _valid_primary_record(kind="synthetic_primary")},
            ["could NOT be read", "check the primary job logs", "not a reviewer rejection"],
            ["REJECTED"],
        ),
        (
            {"audit_source_attempt": 2},
            ["could NOT be read", "check the primary job logs", "not a reviewer rejection"],
            ["REJECTED"],
        ),
        (
            {"primary_result": "failure", "audit": _valid_primary_record(verdict="pass")},
            ["contradicts", "Check the primary job logs"],
            ["could NOT be read", "REJECTED"],
        ),
        (
            {"primary_result": "skipped", "audit": None},
            ["should have run but was skipped", "NOT the normal draft/fork"],
            ["could NOT be read"],
        ),
    ],
    ids=["primary_findings", "audit_missing", "audit_invalid", "audit_source_mismatch", "job_audit_mismatch", "unexpected_primary_skip"],
)
def test_reason_code_wording_cannot_be_misread(kwargs, must_say, must_not_say):
    outcome = AGG.evaluate(**_base_kwargs(**kwargs))
    text = AGG.render_summary(outcome)
    for phrase in must_say:
        assert phrase in text
    for phrase in must_not_say:
        assert phrase not in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"audit": None, "audit_error": "missing"},
        {"audit": _valid_primary_record(kind="synthetic_primary")},
        {"audit_source_attempt": 2},
        {"primary_result": "cancelled", "audit": None},
    ],
    ids=["audit_missing", "audit_invalid", "audit_source_mismatch", "primary_cancelled"],
)
def test_synthetic_verdict_null_is_explained_before_the_json(kwargs):
    # A human sentence explaining `"verdict": null` must precede the synthetic
    # audit JSON, and it must say "not a reviewer rejection" (issue #43).
    outcome = AGG.evaluate(**_base_kwargs(**kwargs))
    assert outcome.synthetic_audit is not None
    text = AGG.render_summary(outcome)
    explanation = '"verdict": null` below means the primary conclusion could not be read'
    assert explanation in text
    assert "NOT a reviewer rejection" in text
    assert text.index(explanation) < text.index('  "verdict": null')


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


@pytest.mark.parametrize(
    "overrides,audit_record,gate_result,action_phrases,needs_run_url",
    [
        ({}, "__default__", "pass", ["No action needed"], False),
        (
            {"primary_result": "failure"}, _valid_primary_record(verdict="fail"),
            "fail", ["Action needed", "the primary reviewer rejected this change"], True,
        ),
        (
            {"primary_result": "skipped", "is_draft": "true", "review_expected": "false"}, None,
            "skipped", ["No action needed", "draft", "ready for review"], False,
        ),
        (
            {"primary_result": "skipped", "runner": "hosted", "review_expected": "false"}, None,
            "skipped", ["No action needed", "runner=self", "runner=hosted"], False,
        ),
        (
            {"primary_result": "cancelled"}, None,
            "unavailable", ["Action needed", "could not be determined"], True,
        ),
    ],
    ids=["pass", "fail", "skipped_draft", "skipped_hosted", "unavailable"],
)
def test_action_line_precedes_machine_codes_for_every_gate_result(tmp_path, overrides, audit_record, gate_result, action_phrases, needs_run_url):
    _, summary_path, args = _visible_scenario(tmp_path, overrides, audit_record)
    AGG.main(args)
    text = summary_path.read_text()

    # Axis 2: Result stays the first verdict line and the machine codes stay
    # behind it…
    assert text.index(f"**Result: {gate_result}**") < text.index("Terminal state: classification=")
    # …and EVERY action-sentence phrase lands before the machine codes (axis
    # 1: the human sentence first, classification= never ahead of it).
    terminal_pos = text.index("Terminal state: classification=")
    for phrase in action_phrases:
        assert phrase in text
        assert text.index(phrase) < terminal_pos
    if needs_run_url:
        # Fail/unavailable must hand the recipient a direct run entry point
        # (axis 1) — built from repository + run_id, before the machine codes.
        assert _RUN_URL in text
        assert text.index(_RUN_URL) < terminal_pos


def test_primary_fail_summary_no_longer_points_at_problems_for_findings(tmp_path):
    # P2-1: the Problems list only mirrors the verdict string; the summary
    # must not claim the findings live there.
    _, summary_path, args = _visible_scenario(tmp_path, {"primary_result": "failure"}, _valid_primary_record(verdict="fail"))
    AGG.main(args)
    text = summary_path.read_text()
    assert "see the findings listed under Problems" not in text
    assert _RUN_URL in text


# ── Status panel sticky delivery and durable-history contract ────────────────

class _FakeResponse:
    def __init__(self, payload=b""):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.payload


def _panel_terminal_row(run_id, attempt, gate_result, head_sha=None):
    return _panel_row(run_id, attempt, gate_result, head_sha=head_sha)


def test_status_panel_publisher_creates_once_then_patches(monkeypatch):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    history = [_panel_terminal_row(i, 1, result, chr(96 + i) * 40) for i, result in enumerate(
        ["skipped", "fail", "fail", "unavailable"], start=1
    )]
    owner = {"id": 99, "login": "workflow-bot"}
    existing_comment = {"id": 77, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER + "\nold", "user": owner}
    comments = [[], [existing_comment], [existing_comment]]
    operations = []

    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments.pop(0))
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=history))
    monkeypatch.setattr(
        AGG, "_post_issue_comment",
        lambda **kwargs: operations.append(("POST", kwargs["body"])),
    )
    monkeypatch.setattr(
        AGG, "_patch_issue_comment",
        lambda **kwargs: operations.append(("PATCH", kwargs["comment_id"], kwargs["body"])),
    )

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] == "created"
    assert operations[0][0] == "POST"
    assert body.count("| [") == 5

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] == "updated"
    assert operations[1][0:2] == ("PATCH", 77)
    assert body.count("| [") == 5
    assert sum(operation[0] == "POST" for operation in operations) == 1


@pytest.mark.parametrize("status", [403, 404])
def test_installation_token_identity_403_or_404_falls_back_and_publishes(monkeypatch, status):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    identity_error = urllib.error.HTTPError("https://api.github.com/user", status, "forbidden", hdrs=None, fp=None)
    calls = []
    operations = []

    def fake_json(**kwargs):
        url = kwargs["url"]
        calls.append(url)
        if url == "https://api.github.com/user":
            raise identity_error
        if "/issues/42/comments" in url:
            return []
        if "/actions/artifacts" in url:
            return {"artifacts": []}
        raise AssertionError(f"unexpected GitHub API call: {url}")

    monkeypatch.setenv("GH_TOKEN", "installation-token")
    monkeypatch.setattr(AGG, "_github_json", fake_json)
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))

    _, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )

    assert calls.count("https://api.github.com/user") == 1
    assert operations and AGG.PANEL_MARKER in operations[0]
    assert receipt["delivery"] == "created"
    assert receipt["operation"] == "POST"
    assert receipt["identity_source"] == "actions_bot_fallback"


def test_identity_http_500_is_reported_as_identity_failure(monkeypatch):
    error = urllib.error.HTTPError("https://api.github.com/user", 500, "server error", hdrs=None, fp=None)
    monkeypatch.setenv("GH_TOKEN", "installation-token")
    monkeypatch.setattr(AGG, "_github_json", lambda **kwargs: (_ for _ in ()).throw(error))

    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )

    assert receipt["operation"] == "IDENTITY"
    assert receipt["http_status"] == 500
    assert receipt["error_category"] == "server_error"


def test_status_panel_rebuild_after_comment_deletion_uses_terminal_history(monkeypatch):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    history = [_panel_terminal_row(i, 1, result, chr(96 + i) * 40) for i, result in enumerate(
        ["skipped", "fail", "fail", "unavailable"], start=1
    )]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    owner = {"id": 99, "login": "workflow-bot"}
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=history))
    monkeypatch.setattr(
        AGG, "_post_issue_comment",
        lambda **kwargs: operations.append(("POST", kwargs["body"])),
    )
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] == "created"
    assert sum(operation[0] == "POST" for operation in operations) == 1
    assert body.count("| [") == 5
    assert "当前状态：**pass** · **可合并**" in body


def test_fetch_terminal_history_consumes_a_persisted_terminal_artifact(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    assert AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))) == 0
    terminal_bytes = terminal_path.read_bytes()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("gate-terminal.json", terminal_bytes)

    monkeypatch.setattr(
        AGG, "_github_json",
        lambda **kwargs: {"artifacts": [{
            "name": "gate-terminal-v1-123-" + "a" * 40 + "-999-1",
            "expired": False,
            "archive_download_url": "https://api.github.com/archive/1",
        }]},
    )
    monkeypatch.setattr(AGG, "_github_request", lambda **kwargs: archive.getvalue())
    rows = AGG._fetch_terminal_history(
        token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )
    assert rows.rows[0]["run_id"] == IDENTITY.run_id
    assert rows.rows[0]["gate_result"] == "pass"


@pytest.mark.parametrize("existing", [False, True], ids=["POST", "PATCH"])
def test_status_panel_post_or_patch_failure_is_fail_open(monkeypatch, existing):
    error = urllib.error.HTTPError("https://api.github.com/x", 403, "forbidden", hdrs=None, fp=None)
    current = _panel_terminal_row(1, 1, "pass", "a" * 40)
    monkeypatch.setenv("GH_TOKEN", "tok")
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": owner}] if existing else []
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    if existing:
        monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: (_ for _ in ()).throw(error))
    else:
        monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: (_ for _ in ()).throw(error))
    _, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["http_status"] == 403
    assert receipt["error_category"] == "permission_or_rate_limit"
    assert receipt["comment_created"] is False


def test_status_panel_mail_invariant_for_five_runs(monkeypatch):
    states = ["skipped", "fail", "fail", "unavailable", "pass"]
    rows = []
    comments = []
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    owner = {"id": 99, "login": "workflow-bot"}

    def fake_comments(**kwargs):
        return comments

    def post(**kwargs):
        operations.append("POST")
        comments[:] = [{"id": 1, "created_at": "2026-08-16T00:00:00Z", "body": kwargs["body"], "user": owner}]

    def patch(**kwargs):
        operations.append("PATCH")
        comments[0]["body"] = kwargs["body"]

    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", fake_comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=list(rows)))
    monkeypatch.setattr(AGG, "_post_issue_comment", post)
    monkeypatch.setattr(AGG, "_patch_issue_comment", patch)

    for run_id, state in enumerate(states, start=1):
        current = _panel_terminal_row(run_id, 1, state, chr(96 + run_id) * 40)
        AGG._post_status_panel_fail_open(
            current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
            identity=IDENTITY,
        )
        rows.append(current)

    assert operations.count("POST") == 1
    assert operations.count("PATCH") == 4
    assert len(comments) == 1
    assert comments[0]["body"].count("| [") == 5
    assert "当前状态：**pass** · **可合并**" in comments[0]["body"]


def test_terminal_artifact_written_by_real_aggregate_is_panel_history_producer_fixture(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    args = _cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))
    assert AGG.main(args) == 0
    record = json.loads(terminal_path.read_text(encoding="utf-8"))
    row = AGG._terminal_row(record, repository="zlxlabs/gate", repository_id=123, pr_number=42)
    assert row["schema_version"] == AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION
    assert AGG.PANEL_MARKER in AGG.render_status_panel([row])


def test_publish_only_consumes_the_real_terminal_producer_fixture_after_upload(monkeypatch, tmp_path):
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
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))
    publish_args = _cli_args(
        audit_dir, summary_path, terminal_path=str(terminal_path), panel_delivery_path=str(delivery_path),
    ) + ["--publish-only"]
    assert AGG.main(publish_args) == 0
    receipt = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert receipt["delivery"] == "created"
    assert receipt["history_incomplete"] is False
    assert receipt["completed_operations"] == [
        "IDENTITY", "COMMENT_LOOKUP", "HISTORY_RECONSTRUCTION", "COMMENT_PUBLISH", "POST_VERIFY",
    ]
    assert receipt["pending_operations"] == []
    assert operations and AGG.PANEL_MARKER in operations[0]


@pytest.mark.parametrize(
    "status,category",
    [(403, "permission_or_rate_limit"), (500, "server_error")],
)
def test_status_panel_http_failure_is_fail_open_and_diagnostic(monkeypatch, capsys, status, category):
    monkeypatch.setenv("GH_TOKEN", "tok")
    error = urllib.error.HTTPError("https://api.github.com/x", status, "boom", hdrs=None, fp=None)
    monkeypatch.setattr(AGG, "_github_json", lambda **kwargs: (_ for _ in ()).throw(error))
    current = _panel_terminal_row(1, 1, "pass", "a" * 40)
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] in {"not_created", "unknown"}
    assert receipt["http_status"] == status
    assert receipt["error_category"] == category
    output = capsys.readouterr().out
    assert "HTTP status=" + str(status) in output
    assert "permission category=" + category in output


def test_status_panel_missing_token_is_fail_open_without_network(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network call")))
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert receipt["delivery"] == "not_created"
    assert receipt["reason_code"] == "missing_token"
    assert receipt["error_category"] == "configuration"


def test_github_timeout_and_publish_budget_defaults_are_centralized(monkeypatch):
    assert AGG.GITHUB_API_TIMEOUT_SECONDS == 15
    assert AGG.DEFAULT_PUBLISH_BUDGET_SECONDS <= 120
    monkeypatch.delenv(AGG.PUBLISH_BUDGET_ENV, raising=False)
    assert AGG._PublishBudget.from_environment().seconds == 120
    monkeypatch.setenv(AGG.PUBLISH_BUDGET_ENV, "3.5")
    assert AGG._PublishBudget.from_environment().seconds == 3.5

    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    urlopen_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "urlopen"
    ]
    assert len(urlopen_calls) == 1
    timeout_keywords = [keyword for keyword in urlopen_calls[0].keywords if keyword.arg == "timeout"]
    assert len(timeout_keywords) == 1


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


@pytest.mark.parametrize(
    "failure,expected_delivery,expected_status",
    [
        (urllib.error.HTTPError("https://api.github.com/x", 429, "rate", hdrs=None, fp=None), "not_created", 429),
        (urllib.error.URLError("timed out"), "unknown", None),
    ],
    ids=["http-429", "network-timeout"],
)
def test_publish_only_transport_failures_are_fail_open_and_leave_receipt(monkeypatch, tmp_path, failure, expected_delivery, expected_status):
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
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: (_ for _ in ()).throw(failure))
    args = _cli_args(
        audit_dir, summary_path, terminal_path=str(terminal_path), panel_delivery_path=str(delivery_path),
    ) + ["--publish-only"]
    assert AGG.main(args) == 0
    receipt = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert receipt["delivery"] == expected_delivery
    assert receipt["http_status"] == expected_status
    assert "HTTP status" in summary_path.read_text(encoding="utf-8")


def test_warning_output_failure_stays_fail_open(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    def broken_print(*args, **kwargs):
        raise OSError("closed stdout")
    monkeypatch.setattr("builtins.print", broken_print)
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert receipt["reason_code"] == "missing_token"


def test_panel_delivery_receipt_uses_temp_file_then_atomic_replace(monkeypatch, tmp_path):
    path = tmp_path / "panel-delivery.json"
    replacements = []
    original_replace = Path.replace
    def record_replace(source, target):
        replacements.append((source, target))
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", record_replace)
    AGG._persist_panel_delivery(str(path), {"delivery": "created"})
    assert replacements == [(path.with_name(".panel-delivery.json.tmp"), path)]
    assert json.loads(path.read_text(encoding="utf-8"))["delivery"] == "created"
    assert not path.with_name(".panel-delivery.json.tmp").exists()


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


def test_history_loader_skips_other_pr_and_bad_records_individually(monkeypatch):
    valid = {
        "schema_version": 1, "kind": "gate_terminal", "repository": "zlxlabs/gate",
        "repository_id": 123, "pr_number": 42, "run_id": 9, "run_attempt": 1,
        "head_sha": "a" * 40, "gate_result": "pass", "classification": "code_pass",
        "reason_code": "primary_pass",
    }
    other_pr = dict(valid, pr_number=77, run_id=8)
    bad_schema = dict(valid, schema_version=True, run_id=7)
    archives = {
        "valid": valid,
        "other": other_pr,
        "bad": bad_schema,
    }
    zipped = {}
    for name, record in archives.items():
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("gate-terminal.json", json.dumps(record))
        zipped[name] = archive.getvalue()
    monkeypatch.setattr(
        AGG, "_github_json",
        lambda **kwargs: {"artifacts": [
            {"name": "gate-terminal-v1-123-valid", "expired": False, "archive_download_url": "valid"},
            {"name": "gate-terminal-v1-123-other", "expired": False, "archive_download_url": "other"},
            {"name": "gate-terminal-v1-123-bad", "expired": False, "archive_download_url": "bad"},
            {"name": "gate-terminal-v1-123-expired", "expired": True, "archive_download_url": "expired"},
        ]},
    )
    monkeypatch.setattr(AGG, "_github_request", lambda **kwargs: zipped[kwargs["url"]])
    result = AGG._fetch_terminal_history(token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42)
    assert [row["run_id"] for row in result.rows] == [9]
    assert {entry["name"] for entry in result.skipped_records} == {
        "gate-terminal-v1-123-other", "gate-terminal-v1-123-bad", "gate-terminal-v1-123-expired",
    }
    assert any("expired" in reason for reason in result.incomplete_reasons)


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
    assert "artifact history does not contain cached rows: 7/1" in body
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


def test_panel_bucket_mapping_exhaustively_covers_current_gate_result_domain():
    assert set(AGG.PANEL_BUCKET_BY_GATE_RESULT) == set(AGG.GATE_RESULT_DOMAIN)


@pytest.mark.parametrize("gate_result", AGG.GATE_RESULT_DOMAIN)
def test_status_panel_renders_every_gate_result_and_action_bucket(gate_result):
    body = AGG.render_status_panel([_panel_row(1, 1, gate_result)])
    assert AGG.PANEL_MARKER in body
    assert f"当前状态：**{gate_result}**" in body
    assert AGG.PANEL_BUCKET_BY_GATE_RESULT[gate_result] in body


def test_status_panel_is_pure_and_history_is_sorted_by_durable_run_identity():
    rows = [_panel_row(3, 1, "pass"), _panel_row(2, 2, "fail"), _panel_row(2, 1, "skipped")]
    original = [dict(row) for row in rows]
    body = AGG.render_status_panel(rows)
    assert rows == original
    assert body.index("| [2]") < body.index("| [3]")
    assert body.index("| [2]") < body.index("| [2]") + 1
    assert "主审未跑，绿≠过审" in body
