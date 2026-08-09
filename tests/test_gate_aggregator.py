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
import importlib.util
import json
import sys
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
        "--pr-number", values["pr_number"],
        "--audit-artifact-name", values["audit_artifact_name"], "--terminal-path", values["terminal_path"],
        "--audit-dir", str(audit_dir),
        "--summary-path", str(summary_path),
    ]
    if "audit_source_attempt" in values:
        args.extend(["--audit-source-attempt", values["audit_source_attempt"]])
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
