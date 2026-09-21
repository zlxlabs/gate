from __future__ import annotations

import json
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import v2_tag_promotion_evidence as evidence


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
FIXTURE_DIR = Path(__file__).parent / "fixtures/canary_referenced_workflows"


class FakeAPI:
    def __init__(self, runs, details=None, jobs=None):
        self.calls = []
        self.runs = runs
        self.details = details or {}
        self.jobs = jobs or {}

    def __call__(self, endpoint):
        self.calls.append(endpoint)
        if endpoint.endswith(
            f"actions/workflows/{evidence.CANARY_GATE_WORKFLOW_ID}/runs?per_page=100"
        ):
            return {"workflow_runs": self.runs}
        for run_id, detail in self.details.items():
            if endpoint == f"repos/{evidence.CANARY_REPOSITORY}/actions/runs/{run_id}":
                return detail
        for run_id, jobs in self.jobs.items():
            if endpoint == f"repos/{evidence.CANARY_REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100":
                return {"jobs": jobs}
        raise AssertionError(f"unexpected API endpoint: {endpoint}")


def _run(run_id):
    return {"id": run_id, "created_at": "2026-09-14T00:00:00Z"}


def _load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _gate_reference(sha):
    real_reference = _load_fixture("run-34854213991.json")["referenced_workflows"][0]
    return {**real_reference, "sha": sha}


def _detail(*, conclusion="success", gate_sha=SHA_A, refs=True):
    references = [] if refs else None
    if refs and gate_sha is not None:
        references = [_gate_reference(gate_sha)]
    return {"conclusion": conclusion, "referenced_workflows": references}


def _select(api, candidates):
    with patch.object(evidence, "workflows_match_main_tip", return_value=True):
        return evidence.select_latest_verified_commit(candidates, SHA_C, api)


def test_latest_candidate_with_successful_gate_and_primary_is_selected():
    api = FakeAPI(
        [_run(1)],
        {"1": _detail()},
        {"1": [{"name": "gate / primary", "conclusion": "success"}]},
    )
    result = _select(api, [SHA_A, SHA_B])
    assert result.selected_sha == SHA_A
    assert result.checked[0].run_id == "1"


@pytest.mark.parametrize(
    ("run_id", "fixture_name", "candidate_sha"),
    [
        (
            "31358374139",
            "run-31358374139.json",
            "7bd2bbd2e92c33d3e0381e38730beaff1f1d69e5",
        ),
        (
            "34854213991",
            "run-34854213991.json",
            "b382411be9a6c261e60c9a261a7beaf80d0b9b41",
        ),
    ],
)
def test_real_canary_payloads_select_the_referenced_gate_sha(
    run_id, fixture_name, candidate_sha
):
    api = FakeAPI(
        [_run(run_id)],
        {run_id: _load_fixture(fixture_name)},
        {run_id: [{"name": "gate / primary", "conclusion": "success"}]},
    )
    result = _select(api, [candidate_sha])
    assert result.selected_sha == candidate_sha
    assert result.checked[0].run_id == run_id


def test_latest_without_evidence_falls_back_to_next_candidate():
    api = FakeAPI(
        [_run(1)],
        {"1": _detail(gate_sha=SHA_B)},
        {"1": [{"name": "gate / primary", "conclusion": "success"}]},
    )
    result = _select(api, [SHA_A, SHA_B])
    assert result.selected_sha == SHA_B
    assert result.checked[0].eligible is False
    assert "expected" in result.checked[0].reason


def test_failed_run_is_ineligible():
    api = FakeAPI([_run(1)], {"1": _detail(conclusion="failure")})
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "conclusion='failure'" in result.checked[0].reason


def test_successful_run_with_skipped_primary_is_ineligible():
    api = FakeAPI(
        [_run(1)],
        {"1": _detail()},
        {"1": [{"name": "gate / primary", "conclusion": "skipped"}]},
    )
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "primary job conclusion=skipped" in result.checked[0].reason


@pytest.mark.parametrize(
    ("gate_shas", "expected_sha", "reason_fragment"),
    [
        ([], None, "no gate-v2 reference found"),
        ([SHA_A, SHA_B], None, "references inconsistent"),
        ([SHA_A, SHA_A], SHA_A, None),
    ],
)
def test_gate_references_must_all_match_candidate(gate_shas, expected_sha, reason_fragment):
    api = FakeAPI(
        [_run(1)],
        {
            "1": {
                "conclusion": "success",
                "referenced_workflows": [
                    _gate_reference(sha)
                    for sha in gate_shas
                ],
            }
        },
        {"1": [{"name": "gate / primary", "conclusion": "success"}]},
    )
    result = _select(api, [SHA_A])
    assert result.selected_sha == expected_sha
    assert reason_fragment is None or reason_fragment in result.checked[0].reason


def test_all_primary_jobs_must_succeed():
    api = FakeAPI(
        [_run(1)],
        {"1": _detail()},
        {"1": [{"name": "gate / primary", "conclusion": "success"}, {"name": "gate / primary", "conclusion": "skipped"}]},
    )
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "skipped" in result.checked[0].reason
    assert "all primary jobs required success" in result.checked[0].reason


def test_successful_run_with_wrong_gate_sha_is_ineligible():
    api = FakeAPI([_run(1)], {"1": _detail(gate_sha=SHA_B)})
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "expected" in result.checked[0].reason


def test_other_referenced_workflow_reports_no_gate_reference():
    api = FakeAPI(
        [_run(1)],
        {
            "1": {
                "conclusion": "success",
                "referenced_workflows": [
                    {
                        "path": "zlxlabs/other/.github/workflows/other.yml@main",
                        "sha": SHA_A,
                    }
                ],
            }
        },
    )
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "no gate-v2 reference found" in result.checked[0].reason


def test_gate_reference_with_unrecognized_path_shape_reports_shape():
    api = FakeAPI(
        [_run(1)],
        {
            "1": {
                "conclusion": "success",
                "referenced_workflows": [
                    {
                        "path": "zlxlabs/gate/.github/workflows/gate-v2.yml",
                        "sha": SHA_A,
                    }
                ],
            }
        },
    )
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "gate-v2 reference shape is unrecognized" in result.checked[0].reason


def test_missing_referenced_workflows_preserves_absent_reason():
    api = FakeAPI([_run(1)], {"1": _detail(refs=False)})
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "referenced_workflows is absent" in result.checked[0].reason


def test_missing_primary_job_is_ineligible():
    api = FakeAPI([_run(1)], {"1": _detail()}, {"1": []})
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "primary job 'primary' is absent" in result.checked[0].reason


def test_query_error_is_not_converted_to_eligibility():
    def broken_api(endpoint):
        raise evidence.EvidenceQueryError(f"HTTP 500 for {endpoint}")

    with pytest.raises(evidence.EvidenceQueryError, match="HTTP 500"):
        _select(broken_api, [SHA_A])


def test_cli_query_error_returns_nonzero(monkeypatch, capsys):
    def broken_api(endpoint):
        raise evidence.EvidenceQueryError(f"HTTP 500 for {endpoint}")

    monkeypatch.setattr(evidence, "_api_json", broken_api)
    monkeypatch.setattr(evidence, "workflows_match_main_tip", lambda *_: True)
    assert evidence.main(["--candidate", SHA_A, "--main-tip", SHA_C]) == 1
    assert "evidence query failed" in capsys.readouterr().err


def test_api_http_4xx_is_a_query_error(monkeypatch):
    error = urllib.error.HTTPError(
        "https://api.github.com", 403, "forbidden", {}, None
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(evidence.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(evidence.EvidenceQueryError, match="HTTP 403"):
        evidence._api_json(
            "repos/zlxlabs/ci-infra-canary/actions/workflows/gate.yml/runs?per_page=100"
        )


def test_malformed_api_response_is_a_query_error():
    with pytest.raises(evidence.EvidenceQueryError, match="workflow_runs"):
        _select(lambda endpoint: {"workflow_runs": "not-a-list"}, [SHA_A])


def test_no_candidate_is_normal_no_move_and_preserves_each_reason():
    api = FakeAPI([_run(1)], {"1": _detail(gate_sha=SHA_C)})
    result = _select(api, [SHA_A, SHA_B])
    assert result.selected_sha is None
    assert [row.sha for row in result.checked] == [SHA_A, SHA_B]
    assert all(not row.eligible for row in result.checked)


def test_workflow_mismatch_is_ineligible_before_canary_evidence(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "workflows_match_main_tip",
        lambda candidate, main_tip: candidate != SHA_A,
    )
    api = FakeAPI(
        [_run(1)],
        {"1": _detail(gate_sha=SHA_B)},
        {"1": [{"name": "gate / primary", "conclusion": "success"}]},
    )
    result = evidence.select_latest_verified_commit([SHA_A, SHA_B], SHA_C, api)
    assert result.selected_sha == SHA_B
    assert result.checked[0].sha == SHA_A
    assert result.checked[0].eligible is False
    assert "workflows differs from main tip" in result.checked[0].reason


def test_workflow_filter_preserves_candidate_report_order(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "workflows_match_main_tip",
        lambda candidate, main_tip: candidate == SHA_A,
    )
    api = FakeAPI(
        [_run(1)],
        {"1": _detail(gate_sha=SHA_B)},
        {"1": [{"name": "gate / primary", "conclusion": "success"}]},
    )
    result = evidence.select_latest_verified_commit([SHA_A, SHA_B], SHA_C, api)
    assert [row.sha for row in result.checked] == [SHA_A, SHA_B]


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, True), (1, False)],
)
def test_workflow_diff_exit_zero_or_one_is_the_eligibility_answer(
    monkeypatch, returncode, expected
):
    calls = []

    def fake_run(argv, check):
        calls.append((argv, check))
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr(evidence.subprocess, "run", fake_run)
    assert evidence.workflows_match_main_tip(SHA_A, SHA_B) is expected
    assert calls == [
        (
            ["git", "diff", "--quiet", SHA_A, SHA_B, "--", ".github/workflows"],
            False,
        )
    ]


def test_workflow_diff_query_failure_is_fail_loud(monkeypatch):
    def broken_run(argv, check):
        return subprocess.CompletedProcess(argv, 2)

    monkeypatch.setattr(evidence.subprocess, "run", broken_run)
    with pytest.raises(evidence.EvidenceQueryError, match="workflow compatibility check failed"):
        evidence.workflows_match_main_tip(SHA_A, SHA_B)


def test_ancestor_check_rejects_non_descendant(monkeypatch):
    calls = []

    def fake_run(argv, check):
        calls.append((argv, check))
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(evidence.subprocess, "run", fake_run)
    assert not evidence.is_descendant_or_equal(SHA_A, SHA_B)
    assert calls == [
        (["git", "merge-base", "--is-ancestor", SHA_A, SHA_B], False)
    ]


def test_ancestor_check_accepts_equal_without_git_call(monkeypatch):
    monkeypatch.setattr(
        evidence.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("equal target must not invoke git"),
    )
    assert evidence.is_descendant_or_equal(SHA_A, SHA_A)


def test_cli_ancestor_check_does_not_require_main_tip(monkeypatch, capsys):
    monkeypatch.setattr(evidence, "is_descendant_or_equal", lambda *_: True)
    assert evidence.main(["--check-ancestor", SHA_A, SHA_B]) == 0
    assert "ancestry verified" in capsys.readouterr().err


def test_primary_job_name_is_read_from_gate_v2_job_id():
    workflow = (Path(__file__).parents[1] / ".github/workflows/gate-v2.yml").read_text()
    assert "\n  primary:\n" in workflow
    assert evidence.PRIMARY_JOB_ID == "primary"


def test_http_error_reader_is_fail_closed():
    error = urllib.error.HTTPError(
        "https://api.github.com", 404, "not found", {}, None
    )

    def broken_api(endpoint):
        raise evidence.EvidenceQueryError(f"HTTP {error.code} for {endpoint}")

    with pytest.raises(evidence.EvidenceQueryError, match="HTTP 404"):
        _select(broken_api, [SHA_A])


def test_filtered_workflow_404_is_fail_closed_without_fallback():
    endpoints = []

    def missing_workflow(endpoint):
        endpoints.append(endpoint)
        raise evidence.EvidenceQueryError("HTTP 404 for filtered workflow")

    with pytest.raises(evidence.EvidenceQueryError, match="HTTP 404"):
        _select(missing_workflow, [SHA_A])
    assert endpoints == [
        "repos/zlxlabs/ci-infra-canary/actions/workflows/gate.yml/runs?per_page=100"
    ]


@pytest.mark.parametrize(
    ("age_hours", "expected_exit", "expects_error"),
    [
        (evidence.NO_ELIGIBLE_CANDIDATE_ALERT_AFTER_HOURS + 1, 1, True),
        (evidence.NO_ELIGIBLE_CANDIDATE_ALERT_AFTER_HOURS - 1, 0, False),
    ],
)
def test_v2_without_eligible_candidate_alerts_only_when_stale(
    age_hours, expected_exit, expects_error, monkeypatch, capsys
):
    api = FakeAPI([_run(1)], {"1": _detail(gate_sha=SHA_B)})
    monkeypatch.setattr(evidence, "_api_json", api)
    monkeypatch.setattr(evidence, "workflows_match_main_tip", lambda *_: True)
    stale_time = (
        datetime.now(timezone.utc)
        - timedelta(hours=age_hours)
    ).isoformat()
    assert evidence.main(
        [
            "--candidate",
            SHA_A,
            "--main-tip",
            SHA_C,
            "--current-v2-commit-time",
            stale_time,
        ]
    ) == expected_exit
    output = capsys.readouterr().err
    assert ("::error::" in output) is expects_error
    assert expects_error or "no eligible canary-verified main commit" in output
