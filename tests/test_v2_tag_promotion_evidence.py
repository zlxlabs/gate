from __future__ import annotations

import subprocess
import urllib.error
from pathlib import Path

import pytest

from scripts import v2_tag_promotion_evidence as evidence


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


class FakeAPI:
    def __init__(self, runs, details=None, jobs=None):
        self.calls = []
        self.runs = runs
        self.details = details or {}
        self.jobs = jobs or {}

    def __call__(self, endpoint):
        self.calls.append(endpoint)
        if endpoint.endswith("actions/runs?per_page=100"):
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


def _detail(*, conclusion="success", gate_sha=SHA_A, refs=True):
    references = [] if refs else None
    if refs and gate_sha is not None:
        references = [{"path": evidence.GATE_WORKFLOW_REFERENCE, "sha": gate_sha}]
    return {"conclusion": conclusion, "referenced_workflows": references}


def _select(api, candidates):
    return evidence.select_latest_verified_commit(candidates, api)


def test_latest_candidate_with_successful_gate_and_primary_is_selected():
    api = FakeAPI(
        [_run(1)],
        {"1": _detail()},
        {"1": [{"name": "gate / primary", "conclusion": "success"}]},
    )
    result = _select(api, [SHA_A, SHA_B])
    assert result.selected_sha == SHA_A
    assert result.checked[0].run_id == "1"


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


def test_successful_run_with_wrong_gate_sha_is_ineligible():
    api = FakeAPI([_run(1)], {"1": _detail(gate_sha=SHA_B)})
    result = _select(api, [SHA_A])
    assert result.selected_sha is None
    assert "expected" in result.checked[0].reason


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
    assert evidence.main(["--candidate", SHA_A]) == 1
    assert "evidence query failed" in capsys.readouterr().err


def test_api_http_4xx_is_a_query_error(monkeypatch):
    error = urllib.error.HTTPError(
        "https://api.github.com", 403, "forbidden", {}, None
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(evidence.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(evidence.EvidenceQueryError, match="HTTP 403"):
        evidence._api_json("repos/zlxlabs/ci-infra-canary/actions/runs?per_page=100")


def test_malformed_api_response_is_a_query_error():
    with pytest.raises(evidence.EvidenceQueryError, match="workflow_runs"):
        _select(lambda endpoint: {"workflow_runs": "not-a-list"}, [SHA_A])


def test_no_candidate_is_normal_no_move_and_preserves_each_reason():
    api = FakeAPI([_run(1)], {"1": _detail(gate_sha=SHA_C)})
    result = _select(api, [SHA_A, SHA_B])
    assert result.selected_sha is None
    assert [row.sha for row in result.checked] == [SHA_A, SHA_B]
    assert all(not row.eligible for row in result.checked)


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
