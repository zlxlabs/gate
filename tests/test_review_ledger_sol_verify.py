"""Sol-verify section acceptance on the ledger consumer side (§8.9 L2, consumer-first).

build_ledger.py must accept canonical primary audits carrying the producer-side
``sol_verify`` section written by gate-hub review-primary (PR #2645; contract:
gate-hub scripts/review/sol_verify_contract.py), shallow-validate its shape
fail-fast, keep audits without the section byte-identical, and leave deeper
cross-record checks to the gate-hub contract. The section is never projected
into the ledger entry. Producer evidence: gate-hub joint check
LEDGER-CONTRACT-MISMATCH producer-only fields: ['sol_verify'].
"""
import json

import pytest

from test_review_ledger import _live_record_audit, _run_live_record_cli

SECTION = {
    "schema_version": "sol-verify-audit/v1",
    "sol_leg": {"status": "ok"},
    "verifier": {"status": "ok"},
    "merged_finding_ids": ["correctness.bad-state"],
    "released_finding_ids": [],
    "findings": [],
    "synthetic_finding_id": None,
}


def _run(tmp_path, audit):
    completed, ledger_path, record_path = _run_live_record_cli(tmp_path, audit=audit)
    ledger = ledger_path.read_bytes() if ledger_path.exists() else None
    record = record_path.read_bytes() if record_path.exists() else None
    return completed, ledger, record


def test_sol_verify_section_accepted_and_outputs_identical_without_it(tmp_path):
    with_completed, with_ledger, with_record = _run(tmp_path / "with", {**_live_record_audit(), "sol_verify": SECTION})
    assert with_completed.returncode == 0, with_completed.stderr
    without_completed, without_ledger, without_record = _run(tmp_path / "without", _live_record_audit())
    assert without_completed.returncode == 0, without_completed.stderr
    # The section rides along unprojected: identical ledger line and record
    # bytes modulo recorded_at, the wall-clock stamp the CLI sets per run.
    strip = lambda line: {k: v for k, v in json.loads(line).items() if k != "recorded_at"}
    assert strip(with_ledger) == strip(without_ledger)
    assert strip(with_record) == strip(without_record)
    assert "sol_verify" not in json.loads(with_ledger)


def test_sol_verify_missing_key_rejected(tmp_path):
    for key in SECTION:
        section = {k: v for k, v in SECTION.items() if k != key}
        completed, _, _ = _run(tmp_path / key, {**_live_record_audit(), "sol_verify": section})
        assert completed.returncode != 0
        assert "canonical primary sol_verify section shape is invalid" in completed.stderr


def test_sol_verify_extra_key_rejected(tmp_path):
    completed, _, _ = _run(tmp_path, {**_live_record_audit(), "sol_verify": {**SECTION, "origin": "gate-hub"}})
    assert completed.returncode != 0
    assert "canonical primary sol_verify section shape is invalid" in completed.stderr


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", "sol-verify-audit/v2"),
    ("schema_version", 1),
    ("schema_version", None),
    ("sol_leg", []),
    ("sol_leg", None),
    ("verifier", "ok"),
    ("merged_finding_ids", "correctness.bad-state"),
    ("merged_finding_ids", [42]),
    ("merged_finding_ids", [""]),
    ("released_finding_ids", {}),
    ("released_finding_ids", [None]),
    ("findings", {}),
    ("findings", None),
    ("synthetic_finding_id", 7),
    ("synthetic_finding_id", ""),
])
def test_sol_verify_type_errors_rejected(tmp_path, field, value):
    audit = {**_live_record_audit(), "sol_verify": {**SECTION, field: value}}
    completed, _, _ = _run(tmp_path / field / repr(value), audit)
    assert completed.returncode != 0
    assert "canonical primary sol_verify section shape is invalid" in completed.stderr


def test_sol_verify_none_rejected_not_treated_as_absent(tmp_path):
    # A missing key is legacy; an explicit null is a malformed section.
    completed, _, _ = _run(tmp_path, {**_live_record_audit(), "sol_verify": None})
    assert completed.returncode != 0
    assert "canonical primary sol_verify section shape is invalid" in completed.stderr


def test_unknown_top_level_field_still_rejected(tmp_path):
    """Gatekeeper (not red-on-base): PRIMARY_ALLOWED_FIELDS stays closed."""
    completed, _, _ = _run(tmp_path, {**_live_record_audit(), "bogus_field": 1})
    assert completed.returncode != 0
    assert "extra=['bogus_field']" in completed.stderr
