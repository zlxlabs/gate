"""Unit tests for the Required Gate v2 aggregator's decision core
(.github/actions/gate-aggregator/aggregate.py).

Judgement matrix coverage (needs.* result x audit-state), per this PR's task:
quality fail, primary fail, audit missing, audit identity mismatch,
draft+skipped, non-draft+skipped, not_expected/waived acceptance + annotation,
and synthetic-audit generation.
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
        is_draft=False,
        review_expected=True,
        audit=_valid_primary_record(),
        audit_error=None,
        identity=IDENTITY,
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


# ── primary: audit identity mismatch ─────────────────────────────────────

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


def test_audit_wrong_kind_is_rejected():
    bad_audit = _valid_primary_record(kind="synthetic_primary")
    outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
    assert outcome.ok is False
    assert any("unexpected audit kind" in p for p in outcome.problems)


def test_audit_verdict_outside_domain_is_rejected():
    bad_audit = _valid_primary_record(verdict="banana")
    outcome = AGG.evaluate(**_base_kwargs(audit=bad_audit))
    assert outcome.ok is False


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
    assert any("unexplained" not in p or "expected" in p for p in outcome.problems)  # sanity: some problem recorded
    assert outcome.synthetic_audit is None  # skip is not a missing-audit situation


def test_non_draft_fork_or_hosted_skipped_primary_is_accepted():
    # review_expected False due to fork/hosted (not draft) — still accepted.
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="skipped", is_draft=False, review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is True


# ── not_expected / waived defensive acceptance ───────────────────────────

def test_not_expected_audit_with_reason_is_accepted_and_annotated():
    audit = _valid_primary_record(verdict="not_expected", reviewer=None, not_expected_reason="fork")
    outcome = AGG.evaluate(**_base_kwargs(audit=audit))
    assert outcome.ok is True
    assert any("T6 TODO" in n for n in outcome.notes)


def test_not_expected_audit_missing_reason_is_rejected():
    audit = _valid_primary_record(verdict="not_expected", reviewer=None)
    outcome = AGG.evaluate(**_base_kwargs(audit=audit))
    assert outcome.ok is False


def test_waived_audit_with_waiver_object_is_accepted_and_annotated():
    audit = _valid_primary_record(
        verdict="waived",
        reviewer=None,
        waiver={"approver": "octocat", "approved_at": "2026-07-24T10:00:00Z", "reason": "flaky infra"},
    )
    outcome = AGG.evaluate(**_base_kwargs(audit=audit))
    assert outcome.ok is True
    assert any("T6 TODO" in n for n in outcome.notes)


def test_waived_audit_missing_waiver_fields_is_rejected():
    audit = _valid_primary_record(verdict="waived", reviewer=None, waiver={"approver": "octocat"})
    outcome = AGG.evaluate(**_base_kwargs(audit=audit))
    assert outcome.ok is False


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


# ── CLI end-to-end (exit codes + step summary) ───────────────────────────

def test_main_exit_code_zero_on_pass(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(
        [
            "--quality-result", "success",
            "--primary-result", "success",
            "--is-draft", "false",
            "--review-expected", "true",
            "--repository-id", str(IDENTITY.repository_id),
            "--head-sha", IDENTITY.head_sha,
            "--run-id", str(IDENTITY.run_id),
            "--run-attempt", str(IDENTITY.run_attempt),
            "--pr-number", str(IDENTITY.pr),
            "--audit-dir", str(audit_dir),
            "--summary-path", str(summary_path),
        ]
    )
    assert rc == 0
    assert "pass" in summary_path.read_text()


def test_main_exit_code_nonzero_on_missing_audit(tmp_path):
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(
        [
            "--quality-result", "success",
            "--primary-result", "success",
            "--is-draft", "false",
            "--review-expected", "true",
            "--repository-id", str(IDENTITY.repository_id),
            "--head-sha", IDENTITY.head_sha,
            "--run-id", str(IDENTITY.run_id),
            "--run-attempt", str(IDENTITY.run_attempt),
            "--pr-number", str(IDENTITY.pr),
            "--audit-dir", str(tmp_path / "nope"),
            "--summary-path", str(summary_path),
        ]
    )
    assert rc == 1
    assert "Synthetic audit generated" in summary_path.read_text()
