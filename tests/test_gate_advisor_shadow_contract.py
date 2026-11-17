"""Structural contracts for the non-blocking advisor_shadow workflow job."""
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
CLASSIFIER_GUARD = "needs.classify_pr_paths.outputs.review_expected != 'false'"
CLASSIFIER_GUARD_EQUIVALENT = (
    "!contains(fromJSON('[\"false\"]'), "
    "needs.classify_pr_paths.outputs.review_expected)"
)


def test_advisor_shadow_is_isolated_nonblocking_and_publishes_summary_contract():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    shadow = jobs["advisor_shadow"]
    primary = jobs["primary"]

    expected_if = primary["if"].replace(CLASSIFIER_GUARD, CLASSIFIER_GUARD_EQUIVALENT)
    assert expected_if != primary["if"]
    assert shadow["if"] == expected_if
    assert shadow["runs-on"] == primary["runs-on"]
    assert shadow["needs"] == ["classify_pr_paths"]
    assert all(
        "advisor_shadow" not in job.get("needs", [])
        for name, job in jobs.items()
        if name != "advisor_shadow"
    )
    assert jobs["gate"]["needs"] == ["quality", "primary", "classify_pr_paths"]
    assert "advisor_shadow" not in str(jobs["gate"])

    assert shadow.get("continue-on-error") is not True
    assert len(shadow["steps"]) == 1
    step = shadow["steps"][0]
    assert step["id"] == "run-advisor-shadow"
    assert step["continue-on-error"] is True
    assert shadow["outputs"] == {
        "verdict": "${{ steps.run-advisor-shadow.outputs.verdict }}",
        "exit_code": "${{ steps.run-advisor-shadow.outputs.exit_code }}",
    }

    run = step["run"]
    assert "classify_human_required" in run
    assert 'hit.startswith("human:declared:")' in run
    assert "if not declared_hits or len(declared_hits) != len(human_hits):" in run
    assert "ADVISOR-SHADOW skipped reason=hits_not_declared_only" in run
    assert "scripts/review/review-advisor" in run
    assert "ADVISOR-VERDICT (no_backdoor|backdoor|uncertain)" in run
    assert (
        "ADVISOR-SHADOW verdict={verdict} exit={exit_code} "
        "head={os.environ['HEAD_SHA']}"
    ) in run
    assert 'outputs.write(f"verdict={verdict}' in run
    assert "except Exception as exc:" in run
