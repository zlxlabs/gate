"""Blocking-matrix v1 acceptance on the ledger consumer side (C10e round 2).

build_ledger.py must accept canonical primary audits that carry the
producer-side ``blocking_matrix`` composite ({version: 1, tier}) written by
gate-hub review-primary (C10a), reject malformed matrices fail-fast, and
leave audits without the field byte-for-byte unchanged. Issuance/consumption
rules live in gate-aggregator/gate_action.py; the ledger only validates the
envelope shape. Producer evidence: gate-hub joint check run 44845318362
(LEDGER-CONTRACT-MISMATCH producer-only fields: ['blocking_matrix']).
"""
import json

import pytest

from test_review_ledger import _intent_envelope, _module, _preflight

MATRIX = {"version": 1, "tier": "personal"}


def _matrix_audit(**matrix_changes):
    audit = _intent_envelope()
    audit["blocking_matrix"] = {**MATRIX, **matrix_changes}
    return audit


def _build(module, audit):
    return module.build_entry(
        repository=audit["repository"], pr_number=audit["pr"], run_id=audit["run_id"],
        run_attempt=audit["run_attempt"], head_sha=audit["head_sha"],
        preflight=_preflight(), audit=audit,
    )


def test_matrix_v1_envelope_is_accepted_and_not_projected_into_entry():
    module = _module()
    audit = _matrix_audit()
    entry = _build(module, audit)
    assert entry["primary_identity"]["head_sha"] == audit["head_sha"]
    # The ledger records the envelope, not the matrix: consumers that need the
    # gate_action rules read the audit, so no second copy lives in the entry.
    assert "blocking_matrix" not in entry["primary_identity"]
    assert json.dumps(entry)  # entry serializes cleanly


def test_matrix_version_must_be_literal_one():
    module = _module()
    for version in (2, "1", True, None, 1.0):
        with pytest.raises(ValueError, match="blocking_matrix must be exactly"):
            _build(module, _matrix_audit(version=version))


def test_matrix_tier_must_be_declared():
    module = _module()
    for tier in ("opensource", "", None):
        with pytest.raises(ValueError, match="blocking_matrix must be exactly"):
            _build(module, _matrix_audit(tier=tier))


def test_matrix_extra_keys_rejected():
    module = _module()
    with pytest.raises(ValueError, match="blocking_matrix must be exactly"):
        _build(module, _matrix_audit(producer="gate-hub"))


def test_matrix_none_value_rejected_not_treated_as_absent():
    module = _module()
    audit = _intent_envelope()
    audit["blocking_matrix"] = None
    # A missing key is legacy; an explicit null is a malformed matrix.
    with pytest.raises(ValueError, match="blocking_matrix must be exactly"):
        _build(module, audit)


def test_legacy_audit_without_matrix_unchanged():
    """Legacy sentinel (not red-on-base): audits without blocking_matrix keep
    the exact pre-change accept path and produce the same entry as before."""
    module = _module()
    audit = _intent_envelope()
    assert "blocking_matrix" not in audit
    entry = _build(module, audit)
    assert entry["primary_identity"]["head_sha"] == audit["head_sha"]
    without = {key: value for key, value in audit.items() if key != "blocking_matrix"}
    assert _build(module, without)["primary_identity"] == entry["primary_identity"]
