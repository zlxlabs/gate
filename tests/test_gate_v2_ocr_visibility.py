"""gate#410: contract for the `ocr_visibility` observation job (gate-v2.yml).

`gate / ocr` can be skipped on a non-draft PR (path exemption, no advisory,
bot author, ...) while `gate / gate` still reports SUCCESS, so consumers
cannot tell "scanned, clean" from "never scanned". The ocr_visibility job
emits exactly one GATE-OCR-SCAN-V1 line (log + step summary) naming the
reason; it is deliberately OUTSIDE gate/ledger needs and never blocks.

Red-on-base note: every test here fails on the base commit except
test_gate_and_ledger_needs_are_verbatim_and_exclude_ocr_visibility, which
guards verbatim needs of gate/ledger (trivially true on base by
construction); its red proof is the defect-injection check documented in the
task report.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
JOB_ID = "ocr_visibility"
STEP_NAME = "Emit GATE-OCR-SCAN-V1 visibility line"
GATE_NEEDS = ["quality", "primary", "classify_pr_paths"]
LEDGER_NEEDS = ["quality", "primary", "gate", "classify_pr_paths"]

_BASE = {
    "REVIEW_EXPECTED": "true",
    "RESOLVE_ADVISORY_RESULT": "success",
    "HAS_ADVISORY": "true",
    "PR_AUTHOR_LOGIN": '"octocat"',
}
SCENARIOS = [
    pytest.param(
        {**_BASE, "OCR_RESULT": "success"},
        dict(scanned="true", ocr_result="success", reason="scanned", warn=False),
        id="scanned",
    ),
    pytest.param(
        {**_BASE, "OCR_RESULT": "skipped", "REVIEW_EXPECTED": "false",
         "RESOLVE_ADVISORY_RESULT": "skipped", "HAS_ADVISORY": ""},
        dict(scanned="false", ocr_result="skipped", reason="review_exempt", warn=False),
        id="review_exempt",
    ),
    pytest.param(
        {**_BASE, "OCR_RESULT": "skipped", "RESOLVE_ADVISORY_RESULT": "skipped", "HAS_ADVISORY": ""},
        dict(scanned="false", ocr_result="skipped", reason="resolve_advisory_skipped", warn=True),
        id="resolve_advisory_skipped",
    ),
    pytest.param(
        {**_BASE, "OCR_RESULT": "skipped", "HAS_ADVISORY": "false"},
        dict(scanned="false", ocr_result="skipped", reason="no_advisory", warn=True),
        id="no_advisory",
    ),
    pytest.param(
        {**_BASE, "OCR_RESULT": "skipped", "PR_AUTHOR_LOGIN": '"dependabot[bot]"'},
        dict(scanned="false", ocr_result="skipped", reason="bot_author", warn=True),
        id="bot_author",
    ),
    pytest.param(
        {**_BASE, "OCR_RESULT": "failure"},
        dict(scanned="false", ocr_result="failure", reason="ocr_failure", warn=True),
        id="ocr_failure",
    ),
    pytest.param(
        {**_BASE, "OCR_RESULT": "cancelled"},
        dict(scanned="false", ocr_result="cancelled", reason="ocr_cancelled", warn=True),
        id="ocr_cancelled",
    ),
]


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _emit_run(workflow) -> str:
    step = next(
        s for s in workflow["jobs"][JOB_ID]["steps"] if s.get("name") == STEP_NAME
    )
    assert re.search(r"<<'PYEOF'\n", step["run"]), "emit step must carry the PYEOF heredoc"
    return step["run"]


def _emit(tmp_path: Path, run: str, env_overrides: dict):
    summary = tmp_path / "step_summary.md"
    proc = subprocess.run(
        ["bash", "-c", run],
        env={
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            **env_overrides,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return proc.stdout, summary.read_text(encoding="utf-8")


def _assert_visibility_line(stdout: str, summary: str, *, scanned, ocr_result, reason, warn):
    expected = f"GATE-OCR-SCAN-V1 scanned={scanned} ocr_result={ocr_result} reason={reason}"
    # step summary carries exactly one line; log carries it once, plus one
    # ::warning:: copy when the scan did not happen and was not exempt.
    assert summary == expected + "\n"
    assert [ln for ln in stdout.splitlines() if "GATE-OCR-SCAN-V1" in ln] == (
        [expected] if not warn else [expected, f"::warning::{expected}"]
    )


def test_ocr_visibility_job_contract():
    jobs = _workflow()["jobs"]
    job = jobs[JOB_ID]  # KeyError on base: job does not exist there
    assert job["if"] == "${{ always() && github.event.pull_request.draft != true }}"
    assert job["needs"] == ["classify_pr_paths", "resolve_advisory", "ocr"]
    # resource-minimal observation lane: same control-plane route as classify
    assert job["runs-on"] == jobs["classify_pr_paths"]["runs-on"]
    assert job["timeout-minutes"] == 5
    assert "secrets" not in yaml.safe_dump(job)


def test_gate_and_ledger_needs_are_verbatim_and_exclude_ocr_visibility():
    jobs = _workflow()["jobs"]
    assert jobs["gate"]["needs"] == GATE_NEEDS
    assert jobs["ledger"]["needs"] == LEDGER_NEEDS
    assert JOB_ID not in jobs["gate"]["needs"] + jobs["ledger"]["needs"]


@pytest.mark.parametrize(("env_overrides", "expected"), SCENARIOS)
def test_reason_mapping_and_warning_matrix(tmp_path, env_overrides, expected):
    stdout, summary = _emit(tmp_path, _emit_run(_workflow()), env_overrides)
    _assert_visibility_line(stdout, summary, **expected)
