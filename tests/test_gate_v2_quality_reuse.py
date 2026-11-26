"""Contract for quality-receipt production, ready-for-review lookup and reuse."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from scripts.quality_reuse import RECEIPT_FIELDS, artifact_name_for

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gate-v2.yml"
UPLOAD_SHA = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
REUSE_GATE = "needs.classify_pr_paths.outputs.quality_reusable == 'true' && needs.quality.result == 'skipped'"
QUALITY_RESULT = f"${{{{ {REUSE_GATE} && 'success' || needs.quality.result }}}}"
QUALITY_CALLER_CHECKS = (
    f"${{{{ {REUSE_GATE} && needs.classify_pr_paths.outputs.reuse_caller_checks "
    "|| needs.quality.outputs.caller_checks }}"
)
QUALITY_PREFLIGHT_RESULT = (
    f"${{{{ {REUSE_GATE} && needs.classify_pr_paths.outputs.reuse_preflight_result "
    "|| needs.quality.outputs.preflight_result }}"
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    return next(step for step in _workflow()["jobs"][job]["steps"] if step.get("name") == name)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def test_classify_lookup_is_ready_same_repo_only_and_fail_closed():
    workflow = _workflow()
    job = workflow["jobs"]["classify_pr_paths"]
    assert job["outputs"]["quality_reusable"].endswith("outputs.quality_reusable || steps.quality-reuse-defaults.outputs.quality_reusable }}")
    for output in ("reuse_source_run_id", "reuse_caller_checks", "reuse_preflight_result", "reuse_reason"):
        assert output in job["outputs"]
    defaults = next(step for step in job["steps"] if step.get("id") == "quality-reuse-defaults")
    assert defaults["if"] == "always()"
    assert "quality_reusable=false" in defaults["run"]

    lookup = next(step for step in job["steps"] if step.get("id") == "quality-reuse-lookup")
    condition = lookup["if"]
    for token in (
        "github.event_name == 'pull_request'", "github.event.action == 'ready_for_review'",
        "github.event.pull_request.head.repo.full_name == github.repository",
        "startsWith(toJSON(github.event.pull_request.user.login), '\"')",
        "endsWith(toJSON(github.event.pull_request.user.login), '\"')",
        "toJSON(github.event.pull_request.user.login) != '\"\"'",
        "toJSON(github.event.pull_request.user.login) != '\"dependabot[bot]\"'",
    ):
        assert token in condition
    assert lookup["continue-on-error"] is True
    run = lookup["run"]
    assert 'timeout -k 5s --foreground 20s gh api "repos/${REPOSITORY}/git/commits/${GITHUB_SHA}" --jq \'.tree.sha\'' in run
    assert 'python3 "_gate-classify-src/scripts/quality_reuse.py" lookup' in run
    for argument in (
        "--repository", "--repository-id", "--pr-number", "--head-sha", "--base-sha",
        "--merge-tree", "--runner", "--extra-runner-label-ci", "--tier",
        "--current-run-id", "--event-action", "--github-output",
    ):
        assert argument in run


def test_quality_skip_and_all_aggregator_consumers_translate_reused_evidence():
    jobs = _workflow()["jobs"]
    quality = jobs["quality"]
    assert quality["needs"] == ["classify_pr_paths"]
    assert quality["if"] == (
        "${{ !cancelled() && !(github.event_name == 'pull_request' && github.event.action == 'converted_to_draft') "
        "&& !(github.event_name == 'pull_request' && github.event.action == 'ready_for_review' "
        "&& needs.classify_pr_paths.outputs.quality_reusable == 'true') }}"
    )
    gate = jobs["gate"]
    assert "always()" in gate["if"] and "github.event.action != 'converted_to_draft'" in gate["if"]

    gate_consumers = {
        step["name"]: step for step in gate["steps"]
        if step.get("name") in {"Aggregate required verdict", "Publish gate status panel"}
    }
    assert set(gate_consumers) == {"Aggregate required verdict", "Publish gate status panel"}
    for step in gate_consumers.values():
        assert step["env"]["QUALITY_RESULT"] == QUALITY_RESULT
        assert step["env"]["QUALITY_CALLER_CHECKS"] == QUALITY_CALLER_CHECKS
        assert step["env"]["QUALITY_PREFLIGHT_RESULT"] == QUALITY_PREFLIGHT_RESULT

    ledger = next(step for step in jobs["ledger"]["steps"] if step.get("name") == "Resolve v2 ledger artifacts")
    assert ledger["env"]["QUALITY_RESULT"] == QUALITY_RESULT
    assert ledger["env"]["QUALITY_CALLER_CHECKS"] == QUALITY_CALLER_CHECKS
    assert ledger["env"]["QUALITY_PREFLIGHT_RESULT"] == QUALITY_PREFLIGHT_RESULT


def test_receipt_uses_trusted_workflow_source_and_upload_fails_open():
    workflow = _workflow()
    quality = workflow["jobs"]["quality"]
    checkout = next(step for step in quality["steps"] if step.get("name") == "Checkout gate actions at this workflow's own commit")
    sparse = checkout["env"]["GATE_CHECKOUT_SPARSE"].splitlines()
    assert "scripts/quality_reuse.py" in [path.strip() for path in sparse]
    assert 'cp "$GITHUB_WORKSPACE/_gate-action-src/scripts/quality_reuse.py" "$RUNNER_TEMP/quality_reuse.py"' in checkout["run"]

    build = next(step for step in quality["steps"] if step.get("id") == "build-quality-reuse-receipt")
    assert build["if"] == (
        "${{ github.event_name == 'pull_request' && steps.caller-checks-outcome.outputs.caller_checks == 'passed' "
        "&& steps.caller-checks-outcome.outputs.preflight_result != 'blocked' }}"
    )
    assert "git rev-parse 'HEAD^{tree}'" in build["run"]
    assert 'python3 "$RUNNER_TEMP/quality_reuse.py" build-receipt' in build["run"]
    assert "scripts/quality_reuse.py" not in build["run"]
    for flag in (
        "--repository-id", "--pr-number", "--head-sha", "--base-sha", "--merge-tree",
        "--runner", "--extra-runner-label-ci", "--tier", "--source-run-id",
        "--caller-checks", "--preflight-result", "--out",
    ):
        assert flag in build["run"]

    upload = next(step for step in quality["steps"] if step.get("id") == "upload-quality-reuse-receipt")
    assert upload["uses"] == UPLOAD_SHA
    assert upload["continue-on-error"] is True
    assert "github.event_name == 'pull_request'" in upload["if"]
    assert "steps.caller-checks-outcome.outputs.caller_checks == 'passed'" in upload["if"]
    assert "steps.caller-checks-outcome.outputs.preflight_result != 'blocked'" in upload["if"]
    assert upload["with"]["retention-days"] == 7
    assert upload["with"]["name"] == (
        "gate-quality-reuse-v1-${{ github.repository_id }}-${{ github.event.pull_request.number }}-"
        "${{ github.event.pull_request.head.sha }}"
    )
    assert upload["with"]["path"] == "${{ runner.temp }}/gate-quality-reuse/quality-reuse-receipt.json"
    assert "# retention: 7 — 消费方" in WORKFLOW.read_text(encoding="utf-8")
    warning = next(step for step in quality["steps"] if step.get("name") == "Warn on quality reuse receipt upload failure")
    assert warning["if"] == "${{ always() && steps.upload-quality-reuse-receipt.outcome == 'failure' }}"
    assert "GATE-QUALITY-REUSE-UPLOAD-FAILED" in warning["run"]


def test_producer_payload_and_artifact_identity_match_lookup_consumer(tmp_path):
    build = next(
        step for step in _workflow()["jobs"]["quality"]["steps"]
        if step.get("id") == "build-quality-reuse-receipt"
    )
    repo = tmp_path / "caller-worktree"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "gate-contract")
    _git(repo, "config", "user.email", "gate-contract@example.invalid")
    (repo / "fixture.txt").write_text("real caller fixture\n", encoding="utf-8")
    _git(repo, "add", "fixture.txt")
    _git(repo, "commit", "--quiet", "-m", "fixture")
    head_sha = _git(repo, "rev-parse", "HEAD")
    merge_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    shutil.copyfile(ROOT / "scripts/quality_reuse.py", runner_temp / "quality_reuse.py")
    env = {
        **os.environ,
        "RUNNER_TEMP": str(runner_temp),
        "REPOSITORY_ID": "123",
        "PR_NUMBER": "42",
        "HEAD_SHA": head_sha,
        "BASE_SHA": "b" * 40,
        "RUNNER_MODE": "self",
        "EXTRA_RUNNER_LABEL_CI": "",
        "TIER": "personal",
        "SOURCE_RUN_ID": "9001",
        "CALLER_CHECKS": "passed",
        "PREFLIGHT_RESULT": "success",
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", build["run"]],
        cwd=repo, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    receipt_path = runner_temp / "gate-quality-reuse/quality-reuse-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt == {
        "kind": "gate_quality_reuse", "schema_version": 1,
        "repository_id": 123, "pr_number": 42, "head_sha": head_sha,
        "base_sha": "b" * 40, "merge_tree": merge_tree, "runner": "self",
        "extra_runner_label_ci": "", "tier": "personal", "source_run_id": 9001,
        "caller_checks": "passed", "preflight_result": "success",
    }

    artifact_template = _step("quality", "Upload quality reuse receipt")["with"]["name"]
    artifact_name = artifact_template.replace("${{ github.repository_id }}", "123")
    artifact_name = artifact_name.replace("${{ github.event.pull_request.number }}", "42")
    artifact_name = artifact_name.replace("${{ github.event.pull_request.head.sha }}", head_sha)
    assert artifact_name == artifact_name_for(receipt["repository_id"], receipt["pr_number"], receipt["head_sha"])

    lookup = next(step for step in _workflow()["jobs"]["classify_pr_paths"]["steps"] if step.get("id") == "quality-reuse-lookup")
    for field, producer_var, lookup_var in (
        ("repository_id", "REPOSITORY_ID", "REPOSITORY_ID"),
        ("pr_number", "PR_NUMBER", "PR_NUMBER"),
        ("head_sha", "HEAD_SHA", "HEAD_SHA"),
        ("base_sha", "BASE_SHA", "BASE_SHA"),
        ("runner", "RUNNER_MODE", "RUNNER_MODE"),
        ("extra_runner_label_ci", "EXTRA_RUNNER_LABEL_CI", "EXTRA_RUNNER_LABEL_CI"),
        ("tier", "TIER", "TIER"),
    ):
        assert build["env"][producer_var] == lookup["env"][lookup_var], field
    assert build["env"]["SOURCE_RUN_ID"] == "${{ github.run_id }}"
    assert lookup["env"]["CURRENT_RUN_ID"] == "${{ github.run_id }}"
    assert '"$merge_tree"' in lookup["run"] and "--merge-tree" in lookup["run"]
    for argument, variable in (
        ("--repository-id", "REPOSITORY_ID"), ("--pr-number", "PR_NUMBER"),
        ("--head-sha", "HEAD_SHA"), ("--base-sha", "BASE_SHA"),
        ("--runner", "RUNNER_MODE"), ("--extra-runner-label-ci", "EXTRA_RUNNER_LABEL_CI"),
        ("--tier", "TIER"), ("--current-run-id", "CURRENT_RUN_ID"),
    ):
        assert f'{argument} "${variable}"' in lookup["run"]
