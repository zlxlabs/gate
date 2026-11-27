"""Domain-specific Required Gate v2 contract tests."""
from test_gate_v2_contract import ABANDONED_FIXTURE, GATE_AGGREGATOR_JOB_NAMES, ISSUE_101_RUN_STARTED_AT, PRIMARY_RESULT_EXPR, PRIMARY_RESULT_RAW_EXPR, _assert_ledger_scheduling_contract, _evaluate_ledger_if, _evaluate_primary_result_expr, _issue_101_attempt2_jobs, _jobs, _ledger_resolver_python, _load_workflow, _prior_terminal_artifacts, _run_ledger_resolver, _silo_prefix, _write_ledger_gh_stub, ast, json, os, pytest, subprocess, time

@pytest.mark.parametrize("raw_result", ["", "abandoned", "weird_future_value"])
def test_ledger_resolver_records_out_of_domain_primary_as_cancelled(tmp_path, raw_result):
    artifacts = [
        {"name": "review-ledger-input-v2-1", "expired": False, "id": 101},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, output = _run_ledger_resolver(
        tmp_path,
        artifacts=artifacts,
        current=1,
        review_expected="true",
        extra_env={
            "QUALITY_RESULT": "success",
            "PRIMARY_RESULT": _evaluate_primary_result_expr(PRIMARY_RESULT_EXPR, raw_result),
            "PRIMARY_RESULT_RAW": raw_result,
        },
    )
    combined = result.stderr + result.stdout
    assert result.returncode == 0, combined
    assert "audit_artifact_id=" not in output  # audit channel moved to job outputs (design A.3)
    assert f"input_artifact_id={_silo_prefix('review-ledger-input-v2-1')}" in output
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-1')}" in output
    assert f"::warning::PRIMARY_RESULT normalized from {raw_result!r} to 'cancelled'" in combined

def test_ledger_job_builds_and_uploads_v2_review_ledger_without_gating():
    raw, _ = _load_workflow()
    quality_steps = raw["jobs"]["quality"]["steps"]
    gate_steps = raw["jobs"]["gate"]["steps"]
    ledger = raw["jobs"]["ledger"]
    steps = ledger["steps"]
    input_publish = next(step for step in quality_steps if step.get("name") == "Publish v2 review ledger inputs")
    input_download = next(step for step in steps if step.get("name") == "Download v2 review ledger inputs")
    persist = next(step for step in steps if step.get("name") == "Upload v2 review ledger inputs")
    persist_retry = next(step for step in steps if step.get("name") == "Retry upload v2 review ledger inputs")
    build_index = next(i for i, step in enumerate(steps) if step.get("name") == "Build v2 review effectiveness ledger")
    upload_index = next(i for i, step in enumerate(steps) if step.get("name") == "Upload v2 review effectiveness ledger")

    assert ledger["needs"] == ["quality", "primary", "gate", "classify_pr_paths"]
    assert ledger["if"] == "always()"
    assert ledger["continue-on-error"] is True
    assert not any(step.get("name") == "Build v2 review effectiveness ledger" for step in gate_steps)
    assert not any(step.get("name") == "Upload v2 review effectiveness ledger" for step in gate_steps)
    assert input_publish["if"] == "always()"
    assert input_publish["continue-on-error"] is True
    assert "uses" not in input_publish
    assert "pr-size-preflight.json" in input_publish["env"]["PREFLIGHT"]
    assert "install-result.json" in input_publish["env"]["INSTALL"]
    assert persist["continue-on-error"] is True
    assert persist_retry["continue-on-error"] is True
    assert persist_retry["if"] == "always() && steps.persist-ledger-input.outcome == 'failure'"
    assert persist["env"]["ARTIFACT_NAME"] == persist_retry["env"]["ARTIFACT_NAME"]
    assert "--tier d1" in persist["run"] and "--tier d1" in persist_retry["run"]
    assert persist["env"]["PREFLIGHT"] == "${{ runner.temp }}/review-ledger-input/pr-size-preflight.json"
    assert "needs.quality.outputs.ledger_input_bundle" in input_download["env"]["LEDGER_INPUT_BUNDLE"]
    assert input_download["env"]["DEST"] == "${{ runner.temp }}/review-ledger-input"
    assert "ARTIFACT_PREFIX" not in input_download.get("env", {})
    assert "$SILO_EXEC" not in input_download["run"]
    terminal_download = next(step for step in steps if step.get("name") == "Download gate terminal envelope for ledger")
    assert terminal_download["id"] == "download-gate-terminal-for-ledger"
    assert terminal_download["env"]["ARTIFACT_PREFIX"] == "${{ steps.resolve-ledger-artifacts.outputs.terminal_artifact_id }}"
    assert terminal_download["env"]["DEST"] == "${{ runner.temp }}/gate-terminal"
    assert terminal_download["if"] == "always() && steps.resolve-ledger-artifacts.outputs.terminal_artifact_id != ''"
    assert terminal_download["continue-on-error"] is True
    # audit channel (design A.3): the ledger reads needs.primary.outputs instead
    # of resolving/downloading the audit from Silo.
    audit_read = next(step for step in steps if step.get("name") == "Read canonical primary audit from primary job output for ledger")
    assert audit_read["id"] == "read-primary-audit-for-ledger"
    assert audit_read["if"] == "always()"
    # Ledger-side read remains fail-loud at the step outcome; step-level
    # continue-on-error keeps this bookkeeping job check green.
    assert audit_read["continue-on-error"] is True
    assert audit_read["env"]["AUDIT_B64"] == "${{ needs.primary.outputs.audit_json_gz_b64 }}"
    assert audit_read["env"]["AUDIT_BYTES_DECLARED"] == "${{ needs.primary.outputs.audit_bytes }}"
    assert audit_read["env"]["AUDIT_FILENAME"] == "primary-review-audit.json"
    audit_read_run = audit_read["run"]
    assert 'success|failure) reviewed=true' in audit_read_run
    assert "refusing to ledger a reviewed run without its audit record" in audit_read_run
    assert "audit fields will be null" in audit_read_run
    assert "base64 -d" in audit_read_run
    assert "gunzip -c" in audit_read_run
    assert "truncated (declared=" in audit_read_run
    assert 'mv "$tmp" "$AUDIT_DIR/$AUDIT_FILENAME"' in audit_read_run
    build = steps[build_index]
    assert build["id"] == "build-review-ledger"
    assert build["uses"] == "./_gate-aggregator-src/.github/actions/review-ledger"
    assert build["with"]["audit-path"] == "${{ runner.temp }}/primary-audit/primary-review-audit.json"
    assert build["with"]["codex-expected"] == raw["jobs"]["primary"]["if"]
    assert build["with"]["codex-waived"] is False
    assert build["with"]["expected-repository-id"] == "${{ github.repository_id }}"
    assert build["with"]["expected-base-sha"] == "${{ github.event.pull_request.base.sha }}"
    assert build["with"]["expected-caller-sha"] == "${{ github.workflow_sha }}"
    assert build["with"]["expected-reusable-workflow-sha"] == "${{ job.workflow_sha }}"
    assert build["with"]["terminal-path"] == (
        "${{ steps.resolve-ledger-artifacts.outputs.terminal_artifact_id != '' && "
        "format('{0}/gate-terminal/gate-terminal.json', runner.temp) || '' }}"
    )
    assert "token" not in build["with"]

    upload = steps[upload_index]
    assert upload["if"] == "success() && steps.build-review-ledger.outcome == 'success'"
    assert "uses" not in upload
    assert upload["env"]["ARTIFACT_NAME"] == (
        "codex-review-ledger-v2-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
        "-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert upload["env"]["LEDGER_PATH"] == "${{ runner.temp }}/review-ledger/ledger.jsonl"
    assert "--tier d30" in upload["run"]
    assert "$SILO_EXEC" in upload["run"]

    latest = next(step for step in steps if step.get("name") == "Upload v2 review effectiveness ledger latest pointer")
    assert steps.index(latest) == upload_index + 1
    assert "uses" not in latest
    assert latest["if"] == "success() && steps.build-review-ledger.outcome == 'success'"
    assert latest["continue-on-error"] is True
    assert latest["env"]["ARTIFACT_NAME"] == (
        "codex-review-ledger-latest-v1-${{ github.repository_id }}-pr${{ github.event.pull_request.number }}"
    )
    assert latest["env"]["LEDGER_PATH"] == upload["env"]["LEDGER_PATH"]
    assert "--tier d30" in latest["run"]
    assert "--name \"$ARTIFACT_NAME\"" in latest["run"]
    assert "--file \"$LEDGER_PATH\"" in latest["run"]
    assert "$SILO_EXEC" in latest["run"]
    assert "list" not in latest["run"]

    # Silo uploads keep failures at the step outcome while making the job check green.
    assert upload["continue-on-error"] is True
    assert "GATE-ARCHIVE-DEFERRED kind=review-ledger " in upload["run"]
    assert "GATE-ARCHIVE-DEFERRED kind=review-ledger-latest " in latest["run"]

    archive_steps = [
        step for step in steps if step.get("name") == "Upload v2 raw review ledger to permanent archive"
    ]
    assert len(archive_steps) == 1
    archive = archive_steps[0]
    assert steps.index(archive) == steps.index(latest) + 1
    assert archive["if"] == "always() && steps.build-review-ledger.outcome == 'success'"
    assert archive["continue-on-error"] is True
    assert archive["env"]["ARTIFACT_NAME"] == upload["env"]["ARTIFACT_NAME"]
    assert archive["env"]["LEDGER_PATH"] == upload["env"]["LEDGER_PATH"]
    assert "--tier archive" in archive["run"]
    assert "--repo-id \"${{ github.repository_id }}\"" in archive["run"]
    assert "--name \"$ARTIFACT_NAME\"" in archive["run"]
    assert "--file \"$LEDGER_PATH\"" in archive["run"]
    assert "GATE-ARCHIVE-DEFERRED kind=review-ledger-archive " in archive["run"]

    audit_archive = next(step for step in steps if step.get("name") == "Archive canonical primary audit permanently")
    assert audit_archive["if"] == "always() && needs.primary.outputs.audit_key != ''"
    assert audit_archive["continue-on-error"] is True
    assert audit_archive["env"]["ARTIFACT_NAME"] == "${{ needs.primary.outputs.audit_key }}"
    assert "GATE-ARCHIVE-DEFERRED kind=primary-audit-archive " in audit_archive["run"]

    terminal_archive = next(step for step in steps if step.get("name") == "Archive gate terminal envelope permanently")
    assert terminal_archive["continue-on-error"] is True
    assert "GATE-ARCHIVE-DEFERRED kind=gate-terminal-archive " in terminal_archive["run"]

    record_upload = next(step for step in steps if step.get("name") == "Upload live review record/v1 to permanent Silo")
    assert record_upload["continue-on-error"] is True
    assert "GATE-ARCHIVE-DEFERRED kind=review-record " in record_upload["run"]

def test_ledger_resolve_failure_skips_build_and_both_uploads():
    steps = _load_workflow()[0]["jobs"]["ledger"]["steps"]
    named = {step.get("name"): step for step in steps}
    outcomes = {
        "resolve-ledger-artifacts": "failure",
        "read-primary-audit-for-ledger": "success",
        "download-gate-terminal-for-ledger": "skipped",
    }
    build = named["Build v2 review effectiveness ledger"]
    assert not _evaluate_ledger_if(build["if"], outcomes, job_success=True)
    outcomes["build-review-ledger"] = "skipped"
    for name in (
        "Upload v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger latest pointer",
    ):
        assert not _evaluate_ledger_if(named[name]["if"], outcomes, job_success=True)

    # Known-success control: optional terminal download may be skipped, but the
    # resolved inputs and audit are present, so build and uploads can proceed.
    outcomes.update(
        {
            "resolve-ledger-artifacts": "success",
            "read-primary-audit-for-ledger": "success",
            "build-review-ledger": "success",
        }
    )
    assert _evaluate_ledger_if(build["if"], outcomes, job_success=True)
    for name in (
        "Upload v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger latest pointer",
    ):
        assert _evaluate_ledger_if(named[name]["if"], outcomes, job_success=True)

@pytest.mark.parametrize("regression", ["three_minute_limit", "repository_only_lock"])
def test_ledger_scheduling_contract_rejects_known_regressions(regression):
    raw, _ = _load_workflow()
    ledger = raw["jobs"]["ledger"]
    if regression == "three_minute_limit":
        ledger["timeout-minutes"] = 3
    else:
        ledger["concurrency"] = {
            "group": "gate-required-v2-ledger-${{ github.repository_id }}",
            "cancel-in-progress": False,
        }

    with pytest.raises(AssertionError):
        _assert_ledger_scheduling_contract(raw)

def test_ledger_job_has_ten_minute_limit_and_no_shared_concurrency():
    raw, _ = _load_workflow()
    _assert_ledger_scheduling_contract(raw)

def test_ledger_build_step_has_one_minute_timeout():
    raw, _ = _load_workflow()
    build = next(
        step for step in raw["jobs"]["ledger"]["steps"]
        if step.get("name") == "Build v2 review effectiveness ledger"
    )
    assert build["timeout-minutes"] == 1

def test_ledger_steps_emit_start_progress_markers():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["ledger"]["steps"]
    names = (
        "Checkout ledger action at this workflow's own commit",
        "Resolve Silo hostname via MagicDNS",
        "Resolve v2 ledger artifacts",
        "Download v2 review ledger inputs",
        "Read canonical primary audit from primary job output for ledger",
        "Download gate terminal envelope for ledger",
        "Build v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger latest pointer",
    )

    assert len([step for step in steps if step.get("name") in names]) == len(names)
    for name in names:
        index = next(i for i, step in enumerate(steps) if step.get("name") == name)
        step = steps[index]
        marker = f'echo "::notice::step-start: {name}"'
        if "uses" in step:
            progress = steps[index - 1]
            assert progress["name"] == f"Mark {name} start"
            assert progress["run"] == marker
        else:
            assert step["run"].splitlines()[0] == marker

def test_ledger_resolver_is_strict_about_current_run_artifact_attempts():
    raw, _ = _load_workflow()
    resolver = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Resolve v2 ledger artifacts")
    assert resolver["if"] == "always()"
    assert resolver["env"]["CURRENT_ATTEMPT"] == "${{ github.run_attempt }}"
    assert resolver["env"]["REVIEW_EXPECTED"] == raw["jobs"]["primary"]["if"]
    run = resolver["run"]
    for marker in (
        "$SILO_EXEC", "resolve", "--attempt",
        "input_artifact_id", "terminal_artifact_id",
        "terminal_source_attempt",
    ):
        assert marker in run
    # audit no longer resolves from Silo (design A.3) — it arrives via the
    # primary job output in the following step.
    assert "audit_artifact_id" not in run
    assert "AUDIT_RESOLVE" not in run
    assert "exact_attempt" not in run
    assert resolver["env"].get("AUDIT_PREFIX") is None
    assert resolver["env"]["TERMINAL_PREFIX"] == (
        "gate-terminal-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}-${{ github.run_id }}-"
    )
    assert resolver["env"]["REPOSITORY"] == "${{ github.repository }}"
    assert resolver["env"]["RUN_ID"] == "${{ github.run_id }}"
    assert "No matching required ledger input artifact found" in run
    assert "No matching canonical primary audit artifact found" not in run
    assert "No matching required gate terminal artifact found" in run
    assert "Aggregator ran on this attempt but did not produce a terminal artifact" in run
    assert "Cannot attribute missing current-attempt terminal: run_started_at is missing or unparseable" in run
    assert "/attempts/" in run and "/jobs" in run
    assert "run_started_at" in run

@pytest.mark.parametrize(
    ("target", "timeout_message"),
    [
        ("jobs", "Resolve v2 ledger artifacts 超时：current-attempt jobs API 调用超过 30 秒"),
        ("meta", "Resolve v2 ledger artifacts 超时：current-attempt metadata API 调用超过 30 秒"),
    ],
)
def test_ledger_resolver_times_out_for_each_current_attempt_api_call(
    tmp_path, target, timeout_message,
):
    gh = _write_ledger_gh_stub(tmp_path, target=target, mode="hang")
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 101},
    ]
    started = time.perf_counter()
    result, _output = _run_ledger_resolver(
        tmp_path,
        artifacts=artifacts,
        current=2,
        extra_env={
            "PATH": f"{gh.parent}:{os.environ['PATH']}",
            "REPOSITORY": "owner/repo",
            "RUN_ID": "123",
        },
        process_timeout=45,
    )
    elapsed = time.perf_counter() - started

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert timeout_message in combined
    assert "Jobs API call failed while attributing a missing current-attempt terminal" not in combined
    assert 20 <= elapsed < 40, f"dedicated 30-second timeout took {elapsed:.1f}s"

@pytest.mark.parametrize("target", ["jobs", "meta"])
def test_ledger_resolver_keeps_nonzero_api_failure_distinct_from_timeout(tmp_path, target):
    gh = _write_ledger_gh_stub(tmp_path, target=target, mode="fail")
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 101},
    ]
    result, _output = _run_ledger_resolver(
        tmp_path,
        artifacts=artifacts,
        current=2,
        extra_env={
            "PATH": f"{gh.parent}:{os.environ['PATH']}",
            "REPOSITORY": "owner/repo",
            "RUN_ID": "123",
        },
        process_timeout=45,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Jobs API call failed while attributing a missing current-attempt terminal" in combined
    assert "超时" not in combined

def test_ledger_resolver_refuses_stale_terminal_when_current_attempt_is_missing(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-1", "expired": False, "id": 101},
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "primary-audit-v2-2", "expired": False, "id": 302},
    ]
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=2, review_expected="true",
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "No matching required gate terminal artifact found" in combined
    assert "identity mismatch" not in combined

def test_ledger_resolver_falls_back_to_prior_terminal_when_current_attempt_is_missing(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=2,
        jobs=_jobs("gate / ledger", "gate / quality"),
        attempt={"run_started_at": ISSUE_101_RUN_STARTED_AT},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-1')}" in output
    assert "terminal_source_attempt=1" in output

def test_ledger_resolver_selects_current_attempt_terminal_not_an_older_one(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
        {"name": "gate-terminal-v1-2", "expired": False, "id": 202},
    ]
    result, output = _run_ledger_resolver(tmp_path, artifacts=artifacts, current=2)
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-2')}" in output
    assert "terminal_source_attempt=2" in output

def test_ledger_resolver_refuses_future_terminal_artifact(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "primary-audit-v2-2", "expired": False, "id": 302},
        {"name": "gate-terminal-v1-3", "expired": False, "id": 203},
    ]
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=2, review_expected="true",
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "No matching required gate terminal artifact found" in combined
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-3')}" not in _output

def test_ledger_resolver_ignores_future_terminal_when_an_eligible_one_exists(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
        {"name": "gate-terminal-v1-3", "expired": False, "id": 203},
    ]
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=2, jobs=_jobs("gate / ledger"),
        attempt={"run_started_at": ISSUE_101_RUN_STARTED_AT},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-1')}" in output
    assert "terminal_source_attempt=1" in output
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-3')}" not in output

@pytest.mark.parametrize("gate_job_name", (*GATE_AGGREGATOR_JOB_NAMES, "ci / gate"))
def test_ledger_resolver_hard_fails_when_aggregator_ran_without_terminal(tmp_path, gate_job_name):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=2,
        jobs=_jobs(gate_job_name, "gate / ledger", started_at="2026-09-01T02:57:51Z"),
        attempt={"run_started_at": ISSUE_101_RUN_STARTED_AT},
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "Aggregator ran on this attempt but did not produce a terminal artifact" in combined
    assert "No matching required gate terminal artifact found" not in combined

def test_ledger_resolver_records_not_applicable_when_review_not_expected_without_terminal(tmp_path):
    """gate#185: attempt 1 也没有终态时，豁免路径记「不适用」而不是硬失败。"""
    artifacts = [
        {"name": "review-ledger-input-v2-1", "expired": False, "id": 101},
    ]
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1, review_expected="false",
        extra_env={"QUALITY_RESULT": "success", "PRIMARY_RESULT": "skipped"},
    )
    combined = result.stderr + result.stdout
    assert result.returncode == 0, combined
    assert "本轮未评审/豁免，无终态产物" in result.stdout
    assert "terminal_artifact_id=\n" in output
    assert "terminal_source_attempt=\n" in output
    assert "No matching required gate terminal artifact found" not in combined

def test_ledger_resolver_python_keeps_terminal_required_tied_to_review_expected():
    """YAML 抽出的 resolver AST：终态 parse_resolve 不得再写死 Constant True。"""
    tree = ast.parse(_ledger_resolver_python())
    required_name = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "terminal_required"
            for target in node.targets
        ):
            compare = node.value
            assert isinstance(compare, ast.Compare)
            assert isinstance(compare.left, ast.Name) and compare.left.id == "expected_text"
            assert isinstance(compare.comparators[0], ast.Constant)
            assert compare.comparators[0].value == "true"
            required_name = "terminal_required"
            break
    assert required_name == "terminal_required"
    terminal_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "parse_resolve"
            and len(node.args) >= 3
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "No matching required gate terminal artifact found"
        ):
            terminal_calls.append(node)
    assert len(terminal_calls) == 1
    third = terminal_calls[0].args[2]
    assert isinstance(third, ast.Name) and third.id == "terminal_required"

def test_ledger_resolver_skips_jobs_listing_when_current_terminal_exists(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
        {"name": "gate-terminal-v1-2", "expired": False, "id": 202},
    ]
    missing = tmp_path / "jobs-missing.json"
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=2, jobs_path=missing,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-2')}" in output
    assert "terminal_source_attempt=2" in output
    poison = tmp_path / "jobs-poison.json"
    poison.write_text("{not json", encoding="utf-8")
    attempt_poison = tmp_path / "attempt-poison.json"
    attempt_poison.write_text("{not json", encoding="utf-8")
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=2, jobs_path=poison, attempt_path=attempt_poison,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "terminal_source_attempt=2" in output

def test_ledger_resolver_falls_back_when_copied_aggregator_job_predates_attempt(tmp_path):
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=_prior_terminal_artifacts(), current=2,
        jobs=_issue_101_attempt2_jobs(),
        attempt={"run_started_at": ISSUE_101_RUN_STARTED_AT},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-1')}" in output
    assert "terminal_source_attempt=1" in output

def test_ledger_resolver_hard_fails_when_aggregator_started_at_or_after_attempt(tmp_path):
    jobs = _issue_101_attempt2_jobs()
    jobs["jobs"][0]["started_at"] = "2026-09-01T02:57:51Z"
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=_prior_terminal_artifacts(), current=2,
        jobs=jobs, attempt={"run_started_at": ISSUE_101_RUN_STARTED_AT},
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "Aggregator ran on this attempt but did not produce a terminal artifact" in combined
    assert "No matching required gate terminal artifact found" not in combined
    assert "run_started_at is missing or unparseable" not in combined

@pytest.mark.parametrize("attempt", [
    {},
    {"run_started_at": None},
    {"run_started_at": "not-a-timestamp"},
    {"run_started_at": "2026-09-01 02:57:51"},
])
def test_ledger_resolver_fail_louds_when_run_started_at_is_unusable(tmp_path, attempt):
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=_prior_terminal_artifacts(), current=2,
        jobs=_issue_101_attempt2_jobs(), attempt=attempt,
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "Cannot attribute missing current-attempt terminal: run_started_at is missing or unparseable" in combined
    assert "No matching required gate terminal artifact found" not in combined
    assert "Aggregator ran on this attempt but did not produce a terminal artifact" not in combined

@pytest.mark.parametrize("upload_env,expected_token", [
    ({"QUALITY_LEDGER_INPUT_UPLOAD": "failure"}, "quality upload outcome: failure"),
    ({"QUALITY_LEDGER_INPUT_UPLOAD": ""}, "quality upload outcome: unknown"),
])
def test_ledger_resolver_missing_input_reports_quality_upload_outcome(
    tmp_path, upload_env, expected_token,
):
    artifacts = [
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1, extra_env=upload_env,
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert expected_token in combined
    assert "No matching required ledger input artifact found" in combined

def test_ledger_resolver_short_circuited_quality_makes_input_optional(tmp_path):
    artifacts = [
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1,
        extra_env={
            "QUALITY_RESULT": "skipped",
            "PRIMARY_RESULT": "failure",
            "QUALITY_LEDGER_INPUT_UPLOAD": "",
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "input_artifact_id=\n" in output
    assert "input_short_circuited=true" in output
    assert "::notice::ledger input skipped" in result.stdout

def test_ledger_resolver_skipped_quality_without_primary_failure_still_requires_input(tmp_path):
    artifacts = [
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1,
        extra_env={"QUALITY_RESULT": "skipped", "PRIMARY_RESULT": "success", "QUALITY_LEDGER_INPUT_UPLOAD": ""},
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "quality upload outcome: unknown" in combined
    assert "No matching required ledger input artifact found" in combined

def test_ledger_resolver_step_env_and_download_guard_literals():
    raw, _ = _load_workflow()
    ledger_steps = raw["jobs"]["ledger"]["steps"]
    resolve = next(s for s in ledger_steps if s.get("name") == "Resolve v2 ledger artifacts")
    assert resolve["env"]["QUALITY_RESULT"] == "${{ needs.classify_pr_paths.outputs.quality_reusable == 'true' && needs.quality.result == 'skipped' && 'success' || needs.quality.result }}"
    assert resolve["env"]["PRIMARY_RESULT"] == PRIMARY_RESULT_EXPR
    assert resolve["env"]["PRIMARY_RESULT_RAW"] == PRIMARY_RESULT_RAW_EXPR
    download = next(s for s in ledger_steps if s.get("name") == "Download v2 review ledger inputs")
    assert download["if"] == "steps.resolve-ledger-artifacts.outputs.input_artifact_id != ''"
    terminal_download = next(s for s in ledger_steps if s.get("name") == "Download gate terminal envelope for ledger")
    assert terminal_download["if"] == "always() && steps.resolve-ledger-artifacts.outputs.terminal_artifact_id != ''"

def test_ledger_build_step_forwards_input_short_circuited():
    """W5: Build 步 with: 必须把解析器的 input_short_circuited 原样传进 action。"""
    raw, _ = _load_workflow()
    matches = [
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Build v2 review effectiveness ledger"
    ]
    assert len(matches) == 1
    build = matches[0]
    assert "input-short-circuited" in build.get("with", {})
    assert build["with"]["input-short-circuited"] == (
        "${{ steps.resolve-ledger-artifacts.outputs.input_short_circuited }}"
    )

@pytest.mark.parametrize(
    "env_key,bad",
    [
        ("QUALITY_RESULT", "bogus"),
        ("PRIMARY_RESULT", "timeout"),
        ("QUALITY_RESULT", ""),
        ("PRIMARY_RESULT", "SUCCESS"),
        ("QUALITY_RESULT", "true"),
        ("PRIMARY_RESULT", "0"),
    ],
)
def test_ledger_resolver_result_domain_rejects_illegal_values(tmp_path, env_key, bad):
    """W6: RESULT_DOMAIN 四值域；非法 QUALITY_RESULT / PRIMARY_RESULT 必须 fail-loud。"""
    artifacts = [
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    extra = {"QUALITY_RESULT": "success", "PRIMARY_RESULT": "success"}
    extra[env_key] = bad
    result, _output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1, extra_env=extra,
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "must be one of" in combined
    assert env_key in combined

@pytest.mark.parametrize("quality_result", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("primary_result", ["success", "failure", "cancelled", "skipped"])
def test_ledger_resolver_result_domain_accepts_legal_values(
    tmp_path, quality_result, primary_result,
):
    """W6 positive: every RESULT_DOMAIN value is accepted; domain check must not exit."""
    artifacts = [
        {"name": "review-ledger-input-v2-1", "expired": False, "id": 101},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1,
        extra_env={"QUALITY_RESULT": quality_result, "PRIMARY_RESULT": primary_result},
    )
    combined = result.stderr + result.stdout
    assert result.returncode == 0, combined
    assert "must be one of" not in combined
    assert f"input_artifact_id={_silo_prefix('review-ledger-input-v2-1')}" in output
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-1')}" in output

def test_ledger_resolver_has_no_audit_outputs_after_channel_split(tmp_path):
    """Audit resolve removed (design A.3): the resolver no longer emits audit_*.

    The audit reaches the ledger through needs.primary.outputs (see
    test_gate_v2_audit_output_channel.py for the read-step behavior contract).
    """
    fixture = json.loads(ABANDONED_FIXTURE.read_text(encoding="utf-8"))
    observed = fixture["observed_env"]
    artifacts = [
        {"name": "review-ledger-input-v2-1", "expired": False, "id": 101},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, output = _run_ledger_resolver(
        tmp_path,
        artifacts=artifacts,
        current=fixture["identity"]["run_attempt"],
        review_expected=observed["REVIEW_EXPECTED"],
        extra_env={
            "QUALITY_RESULT": observed["QUALITY_RESULT"],
            "PRIMARY_RESULT": "cancelled",
            "PRIMARY_RESULT_RAW": observed["PRIMARY_RESULT"],
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "audit_artifact_id=" not in output
    assert "audit_source_attempt=" not in output
    assert f"input_artifact_id={_silo_prefix('review-ledger-input-v2-1')}" in output
    assert f"terminal_artifact_id={_silo_prefix('gate-terminal-v1-1')}" in output

@pytest.mark.parametrize("primary_result", ["success", "failure"])
def test_ledger_audit_read_still_fail_louds_for_reviewed_success_or_failure(tmp_path, primary_result):
    """Reviewed run + missing audit output must fail the read step (exit 1).

    Same input contract the required Silo resolve enforced before the channel
    split; step-level continue-on-error keeps the ledger job check green.
    """
    raw, _ = _load_workflow()
    step = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Read canonical primary audit from primary job output for ledger")
    env = {
        "PRIMARY_RESULT_RAW": primary_result,
        "PRIMARY_RESULT": primary_result,
        "AUDIT_B64": "",
        "AUDIT_BYTES_DECLARED": "",
        "AUDIT_DIR": str(tmp_path / "primary-audit"),
        "AUDIT_FILENAME": "primary-review-audit.json",
    }
    proc = subprocess.run(["bash", "-c", step["run"]], env={**os.environ, **env}, capture_output=True, text=True, timeout=30)
    combined = proc.stderr + proc.stdout
    assert proc.returncode != 0
    assert "refusing to ledger a reviewed run without its audit record" in combined

def test_ledger_resolver_cancelled_primary_still_requires_terminal(tmp_path):
    result, _output = _run_ledger_resolver(
        tmp_path,
        artifacts=[{"name": "review-ledger-input-v2-1", "expired": False, "id": 101}],
        current=1,
        review_expected="true",
        extra_env={"QUALITY_RESULT": "success", "PRIMARY_RESULT": "cancelled"},
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "No matching required gate terminal artifact found" in combined

def test_ledger_resolver_quality_success_still_requires_input_on_cancelled_primary(tmp_path):
    result, _output = _run_ledger_resolver(
        tmp_path,
        artifacts=[{"name": "gate-terminal-v1-1", "expired": False, "id": 201}],
        current=1,
        review_expected="true",
        extra_env={"QUALITY_RESULT": "success", "PRIMARY_RESULT": "cancelled", "QUALITY_LEDGER_INPUT_UPLOAD": ""},
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "No matching required ledger input artifact found" in combined

def test_ledger_persistence_never_gates_the_conclusion():
    """Inverted contract (design A/E): Silo bookkeeping failures only warn.

    Every Silo bookkeeping step is protected at the step level; audit read still
    fails loudly in its step outcome, while the job's check stays green.
    """
    raw, _ = _load_workflow()
    ledger_steps = raw["jobs"]["ledger"]["steps"]
    for name in (
        "Download v2 review ledger inputs",
        "Build v2 review effectiveness ledger",
    ):
        step = next(s for s in ledger_steps if s.get("name") == name)
        assert "continue-on-error" not in step
    for name in (
        "Resolve v2 ledger artifacts",
        "Read canonical primary audit from primary job output for ledger",
        "Download gate terminal envelope for ledger",
    ):
        step = next(s for s in ledger_steps if s.get("name") == name)
        assert step["continue-on-error"] is True
    job_level = raw["jobs"]["ledger"].get("continue-on-error") is True
    assert job_level
    for name in (
        "Upload v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger latest pointer",
    ):
        step = next(s for s in ledger_steps if s.get("name") == name)
        assert step["continue-on-error"] is True
        assert "GATE-ARCHIVE-DEFERRED" in step["run"]
    for name in (
        "Upload v2 raw review ledger to permanent archive",
        "Archive canonical primary audit permanently",
        "Archive gate terminal envelope permanently",
        "Upload live review record/v1 to permanent Silo",
    ):
        step = next(s for s in ledger_steps if s.get("name") == name)
        assert step["continue-on-error"] is True
        assert "GATE-ARCHIVE-DEFERRED" in step["run"]

def test_empty_bundle_download_fails_loud(tmp_path):
    raw, _ = _load_workflow()
    download = next(
        step for step in raw["jobs"]["ledger"]["steps"]
        if step.get("name") == "Download v2 review ledger inputs"
    )
    dest = tmp_path / "review-ledger-input"
    env = {**os.environ, "LEDGER_INPUT_BUNDLE": "", "DEST": str(dest)}
    result = subprocess.run(["bash", "-c", download["run"]], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "review ledger input bundle missing" in result.stderr
