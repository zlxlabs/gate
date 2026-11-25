"""Soft-landing master switch forward (gate-hub design 261122 §8.9, card L3).

Lives in a sibling file because tests/test_gate_v2_contract.py is past the
file-size ratchet (4067 lines vs hard limit 2500); see the pointer comment on
test_primary_run_review_primary_env_has_required_v2_identity_vars there.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gate-v2.yml"


def test_run_review_primary_env_forwards_sol_verifier_soft_landing():
    # The soft-landing master switch is a caller repo variable forwarded
    # verbatim into review-primary's environment (same shape as the
    # NET_SYSFS_ROOT precedent). This repo must not parse or default it:
    # the byte-exact expression is the contract, and deleting the env line
    # must fail this assertion (KeyError or mismatch).
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = raw["jobs"]["primary"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Run review-primary")
    assert run_step["env"]["SOL_VERIFIER_SOFT_LANDING"] == (
        "${{ vars.SOL_VERIFIER_SOFT_LANDING }}"
    )
