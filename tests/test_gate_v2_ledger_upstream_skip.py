"""gate-hub#2498: review ledger input publish vs upstream-skipped producers.

Executes the real ledger-input-upload / ledger-input-upload-outcome /
resolve-ledger-artifacts scripts extracted from gate-v2.yml across the
GITHUB_OUTPUT / env boundary, locking both states:

- State A: every ledger input producer skipped (upstream checkout failed)
  -> publish skipped with a notice, consumer passes with a null input;
- State B: any producer ran but produced no file -> fail-loud, unchanged.
"""

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"

PUBLISH_NOTICE = (
    "review ledger input publish skipped: upstream step failed before "
    "ledger input producers ran"
)
RESOLVER_NOTICE = (
    "ledger input skipped: quality failed upstream before ledger input producers ran"
)
MISSING_ERROR = "review ledger input files missing"
PUBLISH_FAILED_ERROR = "review ledger input publish failed"
RESOLVER_MISSING_ERROR = "No matching required ledger input artifact found"

ALL_SKIPPED = {
    "PREFLIGHT_OUTCOME": "skipped",
    "ENTRY_INSTALL_RESULT_OUTCOME": "skipped",
    "INSTALL_OUTCOME": "skipped",
}
ONE_PRODUCER_RAN = {**ALL_SKIPPED, "PREFLIGHT_OUTCOME": "success"}


def _quality_step(step_id):
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(s for s in raw["jobs"]["quality"]["steps"] if s.get("id") == step_id)


def _resolver_python():
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Resolve v2 ledger artifacts"
    )
    run = step["run"]
    start = run.index("<<'PY'\n") + len("<<'PY'\n")
    return run[start:run.index("\nPY\n", start)]


def _run_publish(tmp_path, *, producer_outcomes):
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = _quality_step("ledger-input-upload")
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    env = os.environ.copy()
    for key in ("PREFLIGHT", "INSTALL", "PREFLIGHT_OUTCOME",
                "ENTRY_INSTALL_RESULT_OUTCOME", "INSTALL_OUTCOME"):
        env.pop(key, None)
    env.update({
        "PREFLIGHT": str(tmp_path / "pr-size-preflight.json"),
        "INSTALL": str(tmp_path / "install-result.json"),
        "GITHUB_OUTPUT": str(github_output),
    })
    env.update(producer_outcomes)
    result = subprocess.run(
        ["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    return result, github_output.read_text(encoding="utf-8")


def _run_outcome_step(tmp_path, *, upload_outcome, bundle, producer_outcomes):
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = _quality_step("ledger-input-upload-outcome")
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    env = os.environ.copy()
    for key in ("LEDGER_INPUT_UPLOAD", "LEDGER_INPUT_BUNDLE", "PREFLIGHT_OUTCOME",
                "ENTRY_INSTALL_RESULT_OUTCOME", "INSTALL_OUTCOME"):
        env.pop(key, None)
    env.update({
        "LEDGER_INPUT_UPLOAD": upload_outcome,
        "LEDGER_INPUT_BUNDLE": bundle,
        "GITHUB_OUTPUT": str(github_output),
    })
    env.update(producer_outcomes)
    result = subprocess.run(
        ["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    return result, github_output.read_text(encoding="utf-8")


def _run_resolver(tmp_path, *, quality_result, quality_upload):
    tmp_path.mkdir(parents=True, exist_ok=True)
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    env = os.environ.copy()
    for key in ("QUALITY_RESULT", "PRIMARY_RESULT", "QUALITY_LEDGER_INPUT_UPLOAD",
                "PRIMARY_RESULT_RAW", "INPUT_RESOLVE", "TERMINAL_RESOLVE",
                "REPOSITORY", "RUN_ID"):
        env.pop(key, None)
    env.update({
        "QUALITY_RESULT": quality_result,
        "PRIMARY_RESULT": "success",
        "QUALITY_LEDGER_INPUT_UPLOAD": quality_upload,
        "INPUT_PREFIX": "review-ledger-input-v2-",
        "REPO_ID": "1",
        "INPUT_RESOLVE": "",
        "TERMINAL_RESOLVE": "",
    })
    result = subprocess.run(
        [sys.executable, "-", "1", "false", str(github_output)],
        input=_resolver_python(), capture_output=True, text=True, env=env)
    return result, github_output.read_text(encoding="utf-8")


def _upload_state(output_text):
    return next(
        line.split("=", 1)[1] for line in output_text.splitlines()
        if line.startswith("ledger_input_upload="))


def test_publish_upstream_skipped_producers_vs_ran_but_missing_fails_loud(tmp_path):
    # State A: checkout failed upstream, every producer skipped, no files.
    result, output = _run_publish(tmp_path / "a", producer_outcomes=ALL_SKIPPED)
    assert result.returncode == 0, result.stderr + result.stdout
    combined = result.stdout + result.stderr
    assert PUBLISH_NOTICE in combined
    assert MISSING_ERROR not in combined
    assert "bundle<<" not in output
    # State B: a producer ran but wrote no file -> fail-loud, unchanged.
    result_b, _output_b = _run_publish(tmp_path / "b", producer_outcomes=ONE_PRODUCER_RAN)
    assert result_b.returncode != 0
    combined_b = result_b.stdout + result_b.stderr
    assert MISSING_ERROR in combined_b
    assert PUBLISH_NOTICE not in combined_b


def test_outcome_step_maps_upstream_skipped_failure_and_success(tmp_path):
    # State A: publish exited 0 with the upstream-skip notice -> no bundle.
    result, output = _run_outcome_step(
        tmp_path / "a", upload_outcome="success", bundle="",
        producer_outcomes=ALL_SKIPPED)
    assert result.returncode == 0, result.stderr + result.stdout
    assert _upload_state(output) == "upstream_skipped"
    assert PUBLISH_FAILED_ERROR not in result.stdout + result.stderr
    # State B: producer ran but missing file -> publish failed -> failure.
    result_b, output_b = _run_outcome_step(
        tmp_path / "b", upload_outcome="failure", bundle="",
        producer_outcomes=ONE_PRODUCER_RAN)
    assert result_b.returncode == 0, result_b.stderr + result.stdout
    assert _upload_state(output_b) == "failure"
    assert PUBLISH_FAILED_ERROR in result_b.stdout
    # A published bundle keeps the success verdict even with skipped producers.
    result_ok, output_ok = _run_outcome_step(
        tmp_path / "ok", upload_outcome="success", bundle='{"preflight":"x"}',
        producer_outcomes=ONE_PRODUCER_RAN)
    assert result_ok.returncode == 0
    assert _upload_state(output_ok) == "success"
    assert "ledger_input_upload=upstream_skipped" not in output_ok


def test_resolver_passes_upstream_skipped_and_still_fails_loud_on_failure(tmp_path):
    # State A: quality failed upstream before producers ran -> pass, null input.
    result, output = _run_resolver(
        tmp_path / "a", quality_result="failure", quality_upload="upstream_skipped")
    assert result.returncode == 0, result.stderr + result.stdout
    assert RESOLVER_NOTICE in result.stdout
    assert "input_artifact_id=\n" in output
    assert "input_short_circuited=true" in output
    assert RESOLVER_MISSING_ERROR not in result.stdout + result.stderr
    # State B: producer ran but file missing (upload=failure) -> fail-loud.
    result_b, _output_b = _run_resolver(
        tmp_path / "b", quality_result="failure", quality_upload="failure")
    assert result_b.returncode != 0
    assert RESOLVER_MISSING_ERROR in result_b.stdout + result_b.stderr
    # Contradiction (quality success but upload=upstream_skipped) never passes.
    result_c, _output_c = _run_resolver(
        tmp_path / "c", quality_result="success", quality_upload="upstream_skipped")
    assert result_c.returncode != 0


def test_producer_to_consumer_contract_over_github_output_boundary(tmp_path):
    # State A end to end: publish -> outcome -> resolver on the real boundary.
    published, publish_output = _run_publish(tmp_path / "a", producer_outcomes=ALL_SKIPPED)
    assert published.returncode == 0
    assert "bundle<<" not in publish_output  # producer emitted no bundle payload
    outcome_result, outcome_output = _run_outcome_step(
        tmp_path / "a-outcome", upload_outcome="success", bundle="",
        producer_outcomes=ALL_SKIPPED)
    assert outcome_result.returncode == 0
    upload_state = _upload_state(outcome_output)
    assert upload_state == "upstream_skipped"
    resolver_result, resolver_output = _run_resolver(
        tmp_path / "a-resolver", quality_result="failure", quality_upload=upload_state)
    assert resolver_result.returncode == 0, resolver_result.stderr + resolver_result.stdout
    assert "input_artifact_id=\n" in resolver_output
    assert RESOLVER_NOTICE in resolver_result.stdout
    # State B end to end: producer failure propagates as failure everywhere.
    published_b, _publish_output_b = _run_publish(tmp_path / "b", producer_outcomes=ONE_PRODUCER_RAN)
    assert published_b.returncode != 0
    outcome_b, outcome_output_b = _run_outcome_step(
        tmp_path / "b-outcome", upload_outcome="failure", bundle="",
        producer_outcomes=ONE_PRODUCER_RAN)
    assert outcome_b.returncode == 0, outcome_b.stderr + outcome_b.stdout
    upload_state_b = _upload_state(outcome_output_b)
    assert upload_state_b == "failure"
    resolver_b, _resolver_output_b = _run_resolver(
        tmp_path / "b-resolver", quality_result="failure", quality_upload=upload_state_b)
    assert resolver_b.returncode != 0
    assert RESOLVER_MISSING_ERROR in resolver_b.stdout + resolver_b.stderr
