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
import builtins
import hashlib
import importlib.util
import json
import sys
import urllib.error
import urllib.request
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
    if values.get("comment_receipt_path") is not None:
        args.extend(["--comment-receipt-path", values["comment_receipt_path"]])
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


# ── Stage 4 PR-comment receipt: switch x outcome x failure injection ────────
#
# Axis tables (task card):
#   axis 1 — every gate_result (pass/fail/skipped/unavailable) AND the
#     malformed-input legacy path posts ONE new issue comment whose body is
#     byte-for-byte the same render_summary() product that went to the Step
#     Summary (no second, drifting copy of the text).
#   axis 2 — every send failure (missing token, 403 fork-PR token downgrade,
#     5xx, network error) is fail-open: ::warning:: only, never ::error::,
#     exit code identical to not sending at all.
#   axis 3 — the switch defaults OFF and then not a single network call may
#     happen (zero behavior change for every existing caller).
# The network seam is urllib.request.urlopen, monkeypatched everywhere — no
# test may emit a real HTTP request.


class _FakeCreatedResponse:
    """Stands in for the 201-Created response urlopen returns; the sender
    only needs it to be a context manager (urlopen itself raises HTTPError
    for any non-2xx)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_requests(monkeypatch):
    recorded = []

    def fake_urlopen(request, timeout=None):
        request.captured_timeout = timeout  # lets tests pin the timeout kwarg (P3-1)
        recorded.append(request)
        return _FakeCreatedResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return recorded


def _raise_urlopen(monkeypatch, exc):
    def fake_urlopen(request, timeout=None):
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _http_error(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "boom", hdrs=None, fp=None)


def _comment_scenario(tmp_path, overrides):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    return summary_path, _cli_args(audit_dir, summary_path, **overrides)


@pytest.mark.parametrize(
    "overrides,expected_rc",
    [
        ({}, 0),
        ({"quality_result": "failure"}, 1),
        ({"primary_result": "skipped", "is_draft": "true", "review_expected": "false"}, 0),
        ({"primary_result": "cancelled"}, 1),
        ({"is_draft": "banana"}, 1),
    ],
    ids=["pass", "fail", "skipped_draft", "unavailable_cancelled", "malformed_input"],
)
def test_pr_comment_body_matches_step_summary_for_every_outcome(monkeypatch, capsys, tmp_path, overrides, expected_rc):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "tok")
    recorded = _capture_requests(monkeypatch)
    summary_path, args = _comment_scenario(tmp_path, overrides)
    rc = AGG.main(args + ["--pr-comment", "true"])
    assert rc == expected_rc
    assert len(recorded) == 1
    # The summary file was created fresh with exactly one append, so its
    # content IS the render_summary() product — the comment body must equal
    # it byte-for-byte (axis 1: same text, never a second copy).
    assert json.loads(recorded[0].data.decode("utf-8")) == {"body": summary_path.read_text()}
    assert "::warning::" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["missing_token", "http_403", "http_429", "http_500", "url_error"])
@pytest.mark.parametrize("gate_ok", [True, False])
def test_pr_comment_send_failures_are_fail_open(monkeypatch, capsys, tmp_path, failure, gate_ok):
    if failure == "missing_token":
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        _raise_urlopen(monkeypatch, AssertionError("no network call without a token"))
    else:
        monkeypatch.setenv("GH_TOKEN", "tok")
        _raise_urlopen(
            monkeypatch,
            {"http_403": _http_error(403), "http_429": _http_error(429), "http_500": _http_error(500), "url_error": urllib.error.URLError("timed out")}[failure],
        )
    overrides = {} if gate_ok else {"quality_result": "failure"}
    summary_path, args = _comment_scenario(tmp_path, overrides)
    rc = AGG.main(args + ["--pr-comment", "true"])
    out = capsys.readouterr().out
    # Axis 2/4 hard contract: exit code identical to not sending at all.
    assert rc == (0 if gate_ok else 1)
    assert "::warning::" in out
    if gate_ok:
        # On a passing gate the ONLY annotations are ::notice:: — the comment
        # path must never add an ::error:: of its own.
        assert "::error::" not in out
    if failure == "http_403":
        # Fork-PR token downgrade is EXPECTED, not a malfunction — the
        # warning must say so (locked decision 3).
        warnings = [line for line in out.splitlines() if line.startswith("::warning::")]
        assert any("fork" in line for line in warnings)


@pytest.mark.parametrize(
    "status,reason_code,error_category",
    [
        (403, "http_403", "permission_or_rate_limit"),
        (500, "http_5xx", "server_error"),
    ],
)
def test_pr_comment_failures_write_a_cross_job_receipt(monkeypatch, capsys, tmp_path, status, reason_code, error_category):
    """The real CLI producer must leave durable evidence after fail-open POST failure."""
    monkeypatch.setenv("GH_TOKEN", "tok")
    _raise_urlopen(monkeypatch, _http_error(status))
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    summary_path, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})

    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "kind": "gate_pr_comment_receipt",
        "repository": "zlxlabs/gate",
        "repository_id": IDENTITY.repository_id,
        "pr_number": IDENTITY.pr,
        "run_id": IDENTITY.run_id,
        "run_attempt": IDENTITY.run_attempt,
        "head_sha": IDENTITY.head_sha,
        "comment_expected": True,
        "comment_created": False,
        "delivery": "not_created",
        "reason_code": reason_code,
        "error_category": error_category,
        "http_status": status,
        "comment_body_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
    }
    assert "::warning::" in capsys.readouterr().out


def test_pr_comment_success_writes_quiet_created_receipt(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _capture_requests(monkeypatch)
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    summary_path, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})

    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "kind": "gate_pr_comment_receipt",
        "repository": "zlxlabs/gate",
        "repository_id": IDENTITY.repository_id,
        "pr_number": IDENTITY.pr,
        "run_id": IDENTITY.run_id,
        "run_attempt": IDENTITY.run_attempt,
        "head_sha": IDENTITY.head_sha,
        "comment_expected": True,
        "comment_created": True,
        "delivery": "created",
        "reason_code": "posted",
        "error_category": None,
        "http_status": None,
        "comment_body_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
    }
    assert "::warning::" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure,delivery,reason_code,error_category,http_status,created",
    [
        ("http_429", "not_created", "http_429", "permission_or_rate_limit", 429, False),
        ("http_other", "not_created", "http_error", "http_error", 400, False),
        ("url_error", "unknown", "network_indeterminate", "network_error", None, None),
    ],
)
def test_pr_comment_receipt_distinguishes_known_http_and_indeterminate_network_failures(
    monkeypatch, tmp_path, failure, delivery, reason_code, error_category, http_status, created
):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _raise_urlopen(
        monkeypatch,
        {"http_429": _http_error(429), "http_other": _http_error(400), "url_error": urllib.error.URLError("reset")}[failure],
    )
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    _, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})

    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["delivery"] == delivery
    assert payload["reason_code"] == reason_code
    assert payload["error_category"] == error_category
    assert payload["http_status"] == http_status
    assert payload["comment_created"] is created


def test_pr_comment_receipt_records_missing_token_without_attempting_request(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    _raise_urlopen(monkeypatch, AssertionError("missing token must not call urlopen"))
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    _, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})

    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["delivery"] == "not_created"
    assert payload["reason_code"] == "missing_token"
    assert payload["error_category"] == "configuration"
    assert payload["http_status"] is None
    assert payload["comment_created"] is False


def test_missing_pr_number_is_cli_reachable_and_writes_missing_target_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _raise_urlopen(monkeypatch, AssertionError("missing target must not call urlopen"))
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    _, args = _comment_scenario(tmp_path, {"pr_number": None, "comment_receipt_path": str(receipt_path)})

    assert AGG.main(args + ["--pr-comment", "true"]) == 1
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["delivery"] == "not_created"
    assert payload["reason_code"] == "missing_target"
    assert payload["error_category"] == "configuration"
    assert payload["pr_number"] is None


def test_pr_comment_receipt_records_not_enabled_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _raise_urlopen(monkeypatch, AssertionError("disabled comment must not call urlopen"))
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    _, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})

    assert AGG.main(args + ["--pr-comment", "false"]) == 0
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["comment_expected"] is False
    assert payload["comment_created"] is None
    assert payload["delivery"] == "not_enabled"
    assert payload["reason_code"] == "not_enabled"
    assert payload["error_category"] is None
    assert payload["http_status"] is None


def test_malformed_comment_switch_still_writes_receipt_when_path_is_given(tmp_path):
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    _, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})

    assert AGG.main(args + ["--pr-comment", "banana"]) == 1
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["delivery"] == "not_enabled"
    assert payload["reason_code"] == "not_enabled"


def test_comment_receipt_uses_temp_file_then_atomic_replace(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _capture_requests(monkeypatch)
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    _, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})
    replaces = []
    original_replace = Path.replace

    def record_replace(source, target):
        if source.name == ".gate-pr-comment-receipt.json.tmp":
            replaces.append((source, target))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", record_replace)
    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    assert len(replaces) == 1
    assert replaces[0][1] == receipt_path
    assert receipt_path.is_file()
    assert not (tmp_path / ".gate-pr-comment-receipt.json.tmp").exists()


@pytest.mark.parametrize("quality_result,expected_rc", [("success", 0), ("failure", 1)])
def test_receipt_write_failure_is_fail_open_and_cannot_publish_stale_file(
    monkeypatch, capsys, tmp_path, quality_result, expected_rc
):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _capture_requests(monkeypatch)
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    receipt_path.write_text("old receipt", encoding="utf-8")
    _, args = _comment_scenario(
        tmp_path,
        {"comment_receipt_path": str(receipt_path), "quality_result": quality_result},
    )
    original_write_text = Path.write_text

    def fail_receipt_write(path, data, **kwargs):
        if path.name == ".gate-pr-comment-receipt.json.tmp":
            raise OSError("simulated disk full")
        return original_write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_receipt_write)
    assert AGG.main(args + ["--pr-comment", "true"]) == expected_rc
    assert not receipt_path.exists()
    assert "file is missing and upload will red" in capsys.readouterr().out


@pytest.mark.parametrize("quality_result,expected_rc", [("success", 0), ("failure", 1)])
def test_receipt_unlink_failure_and_write_failure_cannot_leave_stale_receipt(
    monkeypatch, capsys, tmp_path, quality_result, expected_rc
):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _capture_requests(monkeypatch)
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    receipt_path.write_text(json.dumps({"delivery": "created", "run_id": "old"}), encoding="utf-8")
    _, args = _comment_scenario(
        tmp_path,
        {"comment_receipt_path": str(receipt_path), "quality_result": quality_result},
    )
    original_unlink = Path.unlink
    original_write_text = Path.write_text
    unlink_calls = 0

    def fail_first_unlink(path, missing_ok=False):
        nonlocal unlink_calls
        if path == receipt_path and unlink_calls == 0:
            unlink_calls += 1
            raise OSError("simulated stale receipt lock")
        return original_unlink(path, missing_ok=missing_ok)

    def fail_receipt_write(path, data, **kwargs):
        if path.name == ".gate-pr-comment-receipt.json.tmp":
            raise OSError("simulated disk full")
        return original_write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)
    monkeypatch.setattr(Path, "write_text", fail_receipt_write)
    assert AGG.main(args + ["--pr-comment", "true"]) == expected_rc
    assert not receipt_path.exists()
    output = capsys.readouterr().out
    assert "stale receipt was cleared; upload will red" in output
    assert "receipt channel is untrusted" not in output
    assert unlink_calls == 1


def test_receipt_unlink_failure_and_write_success_replaces_old_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _capture_requests(monkeypatch)
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    receipt_path.write_text(json.dumps({"delivery": "created", "run_id": "old"}), encoding="utf-8")
    _, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})
    original_unlink = Path.unlink
    unlink_calls = 0

    def fail_first_unlink(path, missing_ok=False):
        nonlocal unlink_calls
        if path == receipt_path and unlink_calls == 0:
            unlink_calls += 1
            raise OSError("simulated stale receipt lock")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)
    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["delivery"] == "created"
    assert payload["run_id"] == IDENTITY.run_id
    assert payload["run_id"] != "old"
    assert unlink_calls == 1


def test_receipt_cleanup_failure_destroys_stale_payload_with_invalid_marker(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _capture_requests(monkeypatch)
    receipt_path = tmp_path / "gate-pr-comment-receipt.json"
    receipt_path.write_text(json.dumps({"delivery": "created", "run_id": "old"}), encoding="utf-8")
    _, args = _comment_scenario(tmp_path, {"comment_receipt_path": str(receipt_path)})
    original_unlink = Path.unlink
    original_write_text = Path.write_text

    def fail_receipt_write(path, data, **kwargs):
        if path.name == ".gate-pr-comment-receipt.json.tmp":
            raise OSError("simulated disk full")
        return original_write_text(path, data, **kwargs)

    def fail_all_unlinks(path, missing_ok=False):
        if path == receipt_path:
            raise OSError("simulated stale receipt lock")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "write_text", fail_receipt_write)
    monkeypatch.setattr(Path, "unlink", fail_all_unlinks)
    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    assert receipt_path.read_bytes() == b"invalid gate PR-comment receipt\n"
    with pytest.raises(json.JSONDecodeError):
        json.loads(receipt_path.read_text(encoding="utf-8"))
    assert "stale receipt was destroyed; an invalid marker was written, upload will pass but consumers' json.loads will fail-loud" in capsys.readouterr().out


def test_http_403_warning_names_rate_limit_or_permission_not_just_fork(monkeypatch, capsys, tmp_path):
    # Axis 3 (P3-3): this scenario is a SAME-REPO run (runner=self, non-draft),
    # where a 403 means rate-limit (GitHub secondary limits answer 403 or 429)
    # or a permission failure — attributing every 403 to the fork downgrade
    # would mask both. The warning must carry the status code and the
    # rate-limit/permission alternative, and must not ASSERT the fork cause.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "tok")
    _raise_urlopen(monkeypatch, _http_error(403))
    summary_path, args = _comment_scenario(tmp_path, {})
    rc = AGG.main(args + ["--pr-comment", "true"])
    assert rc == 0  # fail-open: exit code unchanged
    warnings = [line for line in capsys.readouterr().out.splitlines() if line.startswith("::warning::")]
    assert any("HTTP 403" in line and "rate-limit" in line and "permission" in line for line in warnings)
    # Fork may still be named as ONE possibility (the fork assertion above
    # stays green), but the warning must not present fork as the settled
    # cause of every 403.
    assert not any("expected on fork PRs, where GitHub" in line for line in warnings)


@pytest.mark.parametrize("failure", ["missing_token", "http_403", "http_429", "http_500", "url_error"])
@pytest.mark.parametrize("gate_ok", [True, False])
def test_pr_comment_warning_output_failure_is_still_fail_open(monkeypatch, tmp_path, failure, gate_ok):
    """Axis 1 (P3-NEW-1): even when the ::warning:: output ITSELF fails
    (probe: BrokenPipeError on stdout), nothing may escape `_finish` — the
    exit code stays exactly what the gate outcome dictates, ok or not."""
    if failure == "missing_token":
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        _raise_urlopen(monkeypatch, AssertionError("no network call without a token"))
    else:
        monkeypatch.setenv("GH_TOKEN", "tok")
        _raise_urlopen(
            monkeypatch,
            {"http_403": _http_error(403), "http_429": _http_error(429), "http_500": _http_error(500), "url_error": urllib.error.URLError("timed out")}[failure],
        )
    real_print = builtins.print

    def flaky_print(*args, **kwargs):
        # Only the warning annotations fail; the Step Summary / notices must
        # still print so _finish reaches the comment attempt at all.
        if args and isinstance(args[0], str) and args[0].startswith("::warning::"):
            raise BrokenPipeError("probe: warning output failed")
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", flaky_print)
    overrides = {} if gate_ok else {"quality_result": "failure"}
    summary_path, args = _comment_scenario(tmp_path, overrides)
    rc = AGG.main(args + ["--pr-comment", "true"])
    assert rc == (0 if gate_ok else 1)


@pytest.mark.parametrize("extra", [[], ["--pr-comment", "false"]], ids=["default_off", "explicit_false"])
def test_pr_comment_disabled_makes_no_network_call(monkeypatch, tmp_path, extra):
    monkeypatch.setenv("GH_TOKEN", "tok")
    _raise_urlopen(monkeypatch, AssertionError("no network call allowed while --pr-comment is off"))
    summary_path, args = _comment_scenario(tmp_path, {})
    assert AGG.main(args + extra) == 0
    assert "pass" in summary_path.read_text()


def test_pr_comment_malformed_switch_value_fails_closed(tmp_path):
    summary_path, args = _comment_scenario(tmp_path, {})
    rc = AGG.main(args + ["--pr-comment", "banana"])
    assert rc == 1
    assert "malformed boolean input" in summary_path.read_text()


@pytest.mark.parametrize(
    "github_token,gh_token,expected_token",
    [
        ("primary-tok", "fallback-tok", "primary-tok"),
        ("primary-tok", None, "primary-tok"),
        ("", "fallback-tok", "fallback-tok"),
        (None, "fallback-tok", "fallback-tok"),
    ],
    ids=["both_set", "github_only", "empty_github_falls_through", "gh_only"],
)
def test_pr_comment_token_resolution(monkeypatch, tmp_path, github_token, gh_token, expected_token):
    """Axis 3 (P3-2): GITHUB_TOKEN wins when set to a non-empty value; an
    EMPTY-string GITHUB_TOKEN must fall through to GH_TOKEN — the `or` in
    the env lookup is part of the fail-open contract."""
    if github_token is None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GITHUB_TOKEN", github_token)
    if gh_token is None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GH_TOKEN", gh_token)
    recorded = _capture_requests(monkeypatch)
    summary_path, args = _comment_scenario(tmp_path, {})
    assert AGG.main(args + ["--pr-comment", "true"]) == 0
    assert recorded[0].get_header("Authorization") == f"Bearer {expected_token}"


def test_post_issue_comment_posts_to_the_pr_issue_comments_api(monkeypatch):
    recorded = _capture_requests(monkeypatch)
    AGG._post_issue_comment(repository="zlxlabs/gate", pr_number=42, body="hello gate", token="tok")
    assert len(recorded) == 1
    request = recorded[0]
    assert request.full_url == "https://api.github.com/repos/zlxlabs/gate/issues/42/comments"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer tok"
    # The 15s timeout is part of the fail-open contract (it bounds the hang
    # risk of the synchronous POST) — pin it so a regression cannot silently
    # drop it (P3-1).
    assert request.captured_timeout == 15
    assert json.loads(request.data.decode("utf-8")) == {"body": "hello gate"}


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
