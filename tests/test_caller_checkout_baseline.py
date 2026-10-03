"""Caller checkout stays on `actions/checkout` in this PR (gate-hub W3a narrowing).

The five caller-checkout step groups — network-bytes measurement, prime step,
`actions/checkout`, trailing network-bytes measurement — must remain byte-equal
to the origin/main baseline that predates the source-service work.  The baseline
bytes live in ``tests/fixtures/caller-checkout-baseline/`` (extracted from the
baseline commit when this contract was pinned); any edit to these steps, including
re-adding a mode guard, breaks the byte equality and fails this test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2.yml"
SHADOW_WORKFLOW = REPO_ROOT / ".github/workflows/gate-shadow-v2.yml"
BASELINE_DIR = REPO_ROOT / "tests" / "fixtures" / "caller-checkout-baseline"

CHECKOUT_ACTION = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"

SITES = (
    (WORKFLOW, "quality"),
    (WORKFLOW, "primary"),
    (WORKFLOW, "ocr"),
    (SHADOW_WORKFLOW, "classify_pr_paths"),
    (SHADOW_WORKFLOW, "shadow"),
)


def _checkout_group(workflow_path: Path, job_name: str) -> list[dict]:
    raw = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = raw["jobs"][job_name]["steps"]
    indexes = [
        index for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith(CHECKOUT_ACTION)
    ]
    assert len(indexes) == 1, f"{workflow_path.name}:{job_name} has {len(indexes)} checkouts"
    index = indexes[0]
    group = steps[index - 2 : index + 2]
    assert group[1].get("name") == "Prime checkout from host Git mirror"
    return group


def _canonical(group: list[dict]) -> str:
    return yaml.safe_dump(group, sort_keys=False, allow_unicode=True)


def test_caller_checkout_step_groups_match_the_baseline_bytes():
    for workflow_path, job_name in SITES:
        group = _checkout_group(workflow_path, job_name)
        baseline = (BASELINE_DIR / f"{job_name}.yaml").read_text(encoding="utf-8")
        assert _canonical(group) == baseline, (
            f"{workflow_path.name}:{job_name} caller checkout group drifted from the "
            "origin/main baseline — caller checkout keeps actions/checkout byte-exact "
            "in this PR (see docs/design/host-source-service.md)"
        )


def test_baseline_covers_prime_checkout_and_both_byte_measurements():
    for workflow_path, job_name in SITES:
        group = _checkout_group(workflow_path, job_name)
        before, prime, checkout, after = group
        assert before.get("name", "").startswith("Record network bytes before checkout")
        assert after.get("name", "").startswith("Record network bytes after checkout")
        assert checkout.get("uses", "").startswith(CHECKOUT_ACTION)
        assert prime.get("env", {}).get("GATE_GITHUB_TOKEN") == "${{ github.token }}"
        # The mode guard that skipped actions/checkout on service hosts was part of
        # the withdrawn caller-checkout replacement; it must not come back here.
        for step in group:
            assert "gate-checkout-prime" not in str(step.get("if", ""))
            assert "mode != 'service'" not in str(step.get("if", ""))
        assert "gate-checkout-prime" not in str(prime)
