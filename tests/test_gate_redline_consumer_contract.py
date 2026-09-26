"""Consumer-side contract: gate aggregator is immune to redline annotations.

gate-hub card 1 (PR #1111) added ``redline`` / ``original_severity`` fields
to findings on the canonical primary audit; sub-redline majors are demoted
to minor during producer normalization. This aggregator consumes that audit
cross-repo, and ``gate-hub:docs/designs/risk-tier-calibrated-blocking.md``
section 6 pins two invariants on the gate side:

- Invariant 4 (single source of truth): P1 is computed from ``severity``
  only; ``redline`` / ``original_severity`` are never read.
- Invariant 6 (cross-repo publish boundary): this repo carries a byte copy
  of the real producer golden fixture
  ``gate-hub:tests/fixtures/primary-audit-redline-golden.json`` pinned by a
  hardcoded sha256 digest, so any fixture drift turns CI red.

This file adds tests only; no production code changes.
"""

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "primary-audit-redline-golden.json"

# sha256 of the byte copy of
# gate-hub:tests/fixtures/primary-audit-redline-golden.json at gate-hub
# commit 158e9a759ac58e9fbbb4526d829d4d9ae5d5df6b (merged via PR #1111,
# merge commit 1e186d33; git blob 05f0c75907a727d40a7b2ffa1f0a2caba623df39).
# 1913 bytes. When gate-hub updates the golden, re-copy the bytes verbatim
# (no reformatting) and update this constant together.
FIXTURE_SHA256 = "6c043a6dcc26a733097d6580b40327f0bb7cde9caafffef455fedb841841bc45"


def _module():
    spec = importlib.util.spec_from_file_location("gate_aggregate_redline", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGG = _module()


def _load_fixture():
    return json.loads(FIXTURE_PATH.read_bytes().decode("utf-8"))


def _strip_redline(audit):
    stripped = copy.deepcopy(audit)
    for finding in stripped["result"]["findings"]:
        finding.pop("redline", None)
        finding.pop("original_severity", None)
    return stripped


def _with_future_field(audit):
    extended = copy.deepcopy(audit)
    for finding in extended["result"]["findings"]:
        assert "x_future_field" not in finding
        finding["x_future_field"] = {"k": 1}
    return extended


def _overlay_missing_fields(audit):
    """Overlay identity + scope fields the golden deliberately omits.

    Only adds keys; never overwrites fixture bytes. Returns the overlaid
    audit and the overlay dict for the zero-intersection assertion.
    """
    overlaid = copy.deepcopy(audit)
    overlay = {
        "head_sha": "a" * 40,
        "run_id": 999,
        "run_attempt": 1,
        "base_sha": "b" * 40,
        "diff_digest": "d" * 64,
        "policy_digest": "p" * 64,
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
    }
    assert not (set(overlay) & set(overlaid)), (
        sorted(set(overlay) & set(overlaid))
    )
    overlaid.update(overlay)
    return overlaid, overlay


def _evaluate_overlaid(audit):
    overlaid, overlay = _overlay_missing_fields(audit)
    identity = AGG.Identity(
        repository_id=overlaid["repository_id"],
        head_sha=overlay["head_sha"],
        run_id=overlay["run_id"],
        run_attempt=overlay["run_attempt"],
        pr=overlaid["pr"],
    )
    scope, missing = AGG._convergence_scope_from_audit(overlaid, identity)
    assert not missing and scope is not None
    return AGG.evaluate(
        quality_result="success",
        primary_result="failure",
        runner="self",
        is_draft=False,
        review_expected=True,
        audit=overlaid,
        audit_error=None,
        identity=identity,
        pr_author="zj1123581321",  # REST PR #252 user.login fixture
        audit_source_attempt=overlay["run_attempt"],
        audit_artifact_name="primary-audit-v2-1",
        scope=scope,
        audit_digest=AGG._CONVERGENCE.canonical_audit_digest(overlaid),
    )


def test_fixture_digest_pin():
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_fixture_shape_guards_against_degenerate_golden():
    audit = _load_fixture()
    findings = audit["result"]["findings"]
    assert any(
        f.get("severity") == "major" and "redline" in f for f in findings
    )
    assert any(
        f.get("severity") == "minor" and "original_severity" in f
        for f in findings
    )
    assert any(
        f.get("severity") == "major" and "redline" not in f for f in findings
    )


def test_p1_set_comes_from_severity_only():
    audit = _load_fixture()
    p1 = AGG._canonical_p1_findings(audit)
    assert p1 is not None
    ids = {item[0] for item in p1}
    expected = {
        f["id"]
        for f in audit["result"]["findings"]
        if f["severity"] in AGG._CONVERGENCE.P1_SEVERITIES
    }
    assert ids == expected == {"correctness.data-loss", "correctness.unannotated"}
    assert "reliability.below-redline" not in ids


def test_stripping_redline_annotations_changes_nothing():
    audit = _load_fixture()
    assert AGG._canonical_p1_findings(_strip_redline(audit)) == AGG._canonical_p1_findings(audit)


def test_unknown_future_finding_field_is_not_rejected():
    audit = _load_fixture()
    extended = _with_future_field(audit)
    p1 = AGG._canonical_p1_findings(extended)
    assert p1 is not None
    assert p1 == AGG._canonical_p1_findings(audit)


def test_canonical_audit_digest_ignores_redline_annotations():
    audit = _load_fixture()
    assert AGG._CONVERGENCE.canonical_audit_digest(audit) == (
        AGG._CONVERGENCE.canonical_audit_digest(_strip_redline(audit))
    )


def test_evaluate_end_to_end_agrees_across_annotation_variants():
    audit = _load_fixture()
    variants = [audit, _strip_redline(audit), _with_future_field(audit)]
    triples = []
    for variant in variants:
        outcome = _evaluate_overlaid(variant)
        assert outcome.reason_code == "primary_findings"
        assert outcome.classification == "code_fail"
        assert outcome.reason_code != "audit_invalid"
        triples.append(
            (outcome.classification, outcome.reason_code, outcome.gate_result)
        )
    assert triples[0] == triples[1] == triples[2]
