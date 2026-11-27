"""Domain-specific Required Gate v2 contract tests."""
from test_gate_v2_contract import (
    ABANDONED_FIXTURE,
    AGGREGATOR_SCRIPT,
    ARTIFACT_NAME_EXPR,
    CLASSIFY_GUARD,
    CLASSIFY_JOB_ID,
    DIAGNOSTICS_NAME_EXPR,
    DIAGNOSTICS_PATH,
    DRAFT_GUARD,
    FORK_GUARD,
    PANEL_DELIVERY_NAME_EXPR,
    PRIMARY_RESULT_EXPR,
    PRIMARY_RESULT_RAW_EXPR,
    PR_AUTHOR_HUMAN_GUARD,
    PR_AUTHOR_JSON,
    REPO_ROOT,
    REVIEW_EXPECTED_IF,
    RUNNER_GUARD,
    WORKFLOW,
    _assert_expensive_job_cancel_lock,
    _fromjson_label_sets,
    _load_caller,
    _load_disposition_workflow,
    _load_workflow,
    _quality_job_blob,
    _quality_result,
    _quality_steps,
    _repo,
    _run_quality_result_render,
    _self_hosted_label_set,
    _sparse_env_paths,
    assert_workflow_sha_checkout,
    json,
    os,
    pytest,
    re,
    retry,
    run_preflight_with_compare_stub,
    shlex,
    subprocess,
    sys,
    yaml,

)

def test_model_jobs_and_review_expected_copies_need_classify_and_match_primary_if():
    raw, _ = _load_workflow()
    primary_if = raw["jobs"]["primary"]["if"]
    assert primary_if == REVIEW_EXPECTED_IF
    assert raw["jobs"]["primary"]["needs"] == [CLASSIFY_JOB_ID]
    aggregate = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    publish = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Publish gate status panel")
    resolver = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Resolve v2 ledger artifacts")
    build = next(
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Build v2 review effectiveness ledger"
    )
    copies = [
        aggregate["env"]["REVIEW_EXPECTED"],
        publish["env"]["REVIEW_EXPECTED"],
        resolver["env"]["REVIEW_EXPECTED"],
        build["with"]["codex-expected"],
    ]
    for copy in copies:
        assert copy == primary_if
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert workflow_text.count(CLASSIFY_GUARD) == 5
    assert "outputs.review_expected == 'true'" not in workflow_text
def test_aggregate_passes_skip_reason_facts_from_the_review_expected_atoms():
    raw, _ = _load_workflow()
    gate = raw["jobs"]["gate"]
    aggregate = next(s for s in gate["steps"] if s.get("name") == "Aggregate required verdict")
    env = aggregate["env"]
    assert CLASSIFY_JOB_ID in gate["needs"]
    assert raw["jobs"]["primary"]["if"] == REVIEW_EXPECTED_IF
    assert env["REVIEW_EXPECTED"] == REVIEW_EXPECTED_IF
    assert env["IS_DRAFT"] == "${{ github.event.pull_request.draft }}"
    assert env["IS_FORK"] == "${{ github.event.pull_request.head.repo.full_name != github.repository }}"
    assert env["RUNNER_MODE"] == "${{ inputs.runner }}"
    assert env["CLASSIFY_REVIEW_EXPECTED"] == "${{ needs.classify_pr_paths.outputs.review_expected }}"
    atom_positions = [
        REVIEW_EXPECTED_IF.index(DRAFT_GUARD),
        REVIEW_EXPECTED_IF.index(FORK_GUARD),
        REVIEW_EXPECTED_IF.index(RUNNER_GUARD),
        REVIEW_EXPECTED_IF.index(CLASSIFY_GUARD),
    ]
    assert atom_positions == sorted(atom_positions)
    for flag, variable in (
        ("--is-draft", "IS_DRAFT"),
        ("--is-fork", "IS_FORK"),
        ("--runner", "RUNNER_MODE"),
        ("--classify-review-expected", "CLASSIFY_REVIEW_EXPECTED"),
        ("--review-expected", "REVIEW_EXPECTED"),
    ):
        assert f'{flag} "${variable}"' in aggregate["run"]
def test_observed_abandoned_primary_is_normalized_and_raw_value_is_preserved():
    fixture = json.loads(ABANDONED_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["observed_env"]["PRIMARY_RESULT"] == "abandoned"
    raw, _ = _load_workflow()
    aggregate = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    publish = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Publish gate status panel")
    resolver = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Resolve v2 ledger artifacts")
    for step in (aggregate, publish, resolver):
        assert step["env"]["PRIMARY_RESULT"] == PRIMARY_RESULT_EXPR
        assert step["env"]["PRIMARY_RESULT_RAW"] == PRIMARY_RESULT_RAW_EXPR
        assert "::warning::PRIMARY_RESULT normalized from" in step["run"]
def test_gate_job_id_is_literally_gate_and_runs_always():
    raw, _ = _load_workflow()
    gate_job = raw["jobs"]["gate"]
    assert str(gate_job.get("if", "")) == "${{ always() && (github.event_name != 'pull_request' || github.event.action != 'converted_to_draft') }}"
    needs = gate_job.get("needs")
    assert set(needs if isinstance(needs, list) else [needs]) == {
        "quality", "primary", "classify_pr_paths",
    }
def test_gate_and_notify_runs_on_use_the_same_guarded_control_plane_route():
    raw, _ = _load_workflow()
    expected = (
        "${{ (inputs.runner == 'self' && inputs.control_runner != 'github-hosted' && "
        "(github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository)) "
        "&& fromJSON('[\"self-hosted\",\"linux\",\"codex\"]') || fromJSON('[\"ubuntu-latest\"]') }}"
    )
    workflow_text = WORKFLOW.read_text()
    assert "gate-control" not in workflow_text
    for job_name in ("gate", "classify_pr_paths"):
        runs_on = str(raw["jobs"][job_name]["runs-on"])
        assert runs_on == expected
        assert FORK_GUARD in runs_on
        assert RUNNER_GUARD in runs_on
        assert "github.event_name != 'pull_request'" in runs_on
        assert "ubuntu-latest" in runs_on
        assert all(label in runs_on for label in ("self-hosted", "linux", "codex"))
def test_gate_job_never_invokes_aggregator_via_a_moving_uses_ref():
    # `uses:` cannot itself take an expression, so a `uses: .../gate-aggregator@main`
    # reference would float independently of whatever SHA a canary caller pinned
    # for gate-v2.yml — defeating the pinned-SHA canary governance. The gate job
    # must instead checkout the reusable workflow's own source (via the `job`
    # context's job.workflow_repository/job.workflow_sha — GitHub's documented
    # mechanism for a reusable workflow to check out its own commit; NOT
    # `github.job_workflow_sha`, which does not exist) and invoke the
    # aggregator script directly.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["gate"]["steps"]
    checkout = next(s for s in steps if s.get("name") == "Checkout gate-aggregator at this workflow's own commit")
    assert_workflow_sha_checkout(checkout, path="_gate-aggregator-src", sparse=[])
    assert not any(str(s.get("uses", "")).startswith("zlxlabs/gate/.github/actions/gate-aggregator") for s in steps)
    aggregate_step = next(s for s in steps if s.get("name") == "Aggregate required verdict")
    assert "aggregate.py" in aggregate_step["run"]
    assert AGGREGATOR_SCRIPT.is_file()
def test_primary_publishes_verdict_output_and_silo_copy_shares_key_template():
    """Verdict channel producer contract (design A.1) + retention-copy key parity.

    The job output carries the canonical audit to gate/ledger (gzip+base64,
    single line — gate#377: bounded by the consumer's single env string
    MAX_ARG_STRLEN=131072, not the 1 MiB expression budget); the Silo d14
    upload is bookkeeping only but must keep the same key
    template so terminal envelopes and archives stay addressable.
    """
    raw, _ = _load_workflow()
    primary = raw["jobs"]["primary"]
    outputs = primary["outputs"]
    assert outputs["audit_json_gz_b64"] == "${{ steps.publish-audit-output.outputs.audit_json_gz_b64 }}"
    assert outputs["audit_bytes"] == "${{ steps.publish-audit-output.outputs.audit_bytes }}"
    assert outputs["audit_attempt"] == "${{ steps.publish-audit-output.outputs.audit_attempt }}"
    assert outputs["audit_key"] == "${{ steps.publish-audit-output.outputs.audit_key }}"

    publish = next(s for s in primary["steps"] if s.get("name") == "Publish canonical primary audit to job output")
    assert publish["if"] == "always()"
    assert publish["id"] == "publish-audit-output"
    assert "uses" not in publish
    # fail-closed verdict channel: missing/empty audit file must fail the step
    # (and the primary job), never publish an empty record.
    assert "continue-on-error" not in publish
    assert "[ ! -s \"$AUDIT_PATH\" ]" in publish["run"]
    assert "exit 1" in publish["run"]
    # gate#377: gzip 载荷 + 单环境串上限 + fail-loud 锚行
    assert "gzip -n -9 -c \"$AUDIT_PATH\" | base64 -w0" in publish["run"]
    assert publish["env"]["MAX_PAYLOAD_CHARS"] == "120000"
    assert "AUDIT-PAYLOAD-OVER-ENV-LIMIT" in publish["run"]
    assert publish["env"]["AUDIT_KEY"] == ARTIFACT_NAME_EXPR
    assert 'echo "audit_json_gz_b64=$payload"' in publish["run"]
    assert 'echo "audit_bytes=' in publish["run"]
    assert 'echo "audit_attempt=${{ github.run_attempt }}"' in publish["run"]
    assert 'echo "audit_key=$AUDIT_KEY"' in publish["run"]

    upload = next(s for s in primary["steps"] if s.get("name") == "Upload canonical primary audit")
    assert upload["if"] == "always()"
    assert "uses" not in upload
    assert upload["env"]["ARTIFACT_NAME"] == ARTIFACT_NAME_EXPR
    # bookkeeping (design A.3): the Silo copy must never flip the primary result.
    assert upload["continue-on-error"] is True
    assert "--tier d14" in upload["run"]
    assert upload["env"]["AUDIT_PATH"] == "${{ runner.temp }}/primary-review-audit.json"
    # the legacy "no swallowed exit codes" guard, refined: the Silo invocation
    # itself must never carry `|| true` (the `|| true` on the deferred-row
    # reason extraction only guards grep's no-match exit under pipefail)
    assert not any("$SILO_EXEC" in line and "|| true" in line for line in upload["run"].splitlines())
    assert "SILO_ACCESS_KEY 未传入" in upload["run"]
    assert "GATE-ARCHIVE-DEFERRED kind=primary-audit " in upload["run"]
def test_primary_uploads_review_diagnostics_after_canonical_audit():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    audit_index = next(i for i, step in enumerate(steps) if step.get("name") == "Upload canonical primary audit")
    diagnostics_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Upload primary review diagnostics"
    )
    assert diagnostics_index == audit_index + 1
    diagnostics = steps[diagnostics_index]
    assert diagnostics["name"] == "Upload primary review diagnostics"
    assert diagnostics["if"] == "always()"
    assert "uses" not in diagnostics
    assert diagnostics["env"]["ARTIFACT_NAME"] == DIAGNOSTICS_NAME_EXPR
    assert diagnostics["env"]["DIAGNOSTICS_DIR"] == DIAGNOSTICS_PATH.rstrip("/")
    assert "--tier d3" in diagnostics["run"]
    assert "--empty skip" in diagnostics["run"]

    gate_steps = raw["jobs"]["gate"]["steps"]
    # Verdict channel consumer (design A.2): no Silo resolve/download for the
    # audit any more — gate reads needs.primary.outputs directly.
    assert not any("audit" in str(s.get("name", "")).lower() and "$SILO_EXEC" in str(s.get("run", "")) for s in gate_steps)
    reader = next(s for s in gate_steps if s.get("name") == "Read canonical primary audit from primary job output")
    assert reader["id"] == "read-primary-audit"
    assert reader["if"] == "${{ needs.primary.result != 'skipped' }}"
    assert "continue-on-error" not in reader
    assert reader["env"]["AUDIT_B64"] == "${{ needs.primary.outputs.audit_json_gz_b64 }}"
    assert reader["env"]["AUDIT_BYTES_DECLARED"] == "${{ needs.primary.outputs.audit_bytes }}"
    assert reader["env"]["AUDIT_DIR"] == "${{ runner.temp }}/primary-audit"
    assert reader["env"]["AUDIT_FILENAME"] == "primary-review-audit.json"
    reader_run = reader["run"]
    # fail-closed collapse paths: every bad shape must leave the audit dir
    # without a usable file (aggregator audit_missing) and print the reason.
    assert '[ -z "$AUDIT_B64" ]' in reader_run
    assert "audit_missing" in reader_run
    assert "base64 -d" in reader_run
    assert "gunzip -c" in reader_run
    assert 'audit_missing" >&2' in reader_run
    assert 'truncated or padded' in reader_run
    assert "mkdir -p \"$AUDIT_DIR\"" in reader_run
    assert 'mv "$tmp" "$AUDIT_DIR/$AUDIT_FILENAME"' in reader_run

    terminal_upload = next(s for s in gate_steps if s.get("name") == "Upload gate terminal envelope")
    assert terminal_upload["if"] == "always()"
    assert "uses" not in terminal_upload
    assert terminal_upload["continue-on-error"] is True
    assert terminal_upload["env"]["ARTIFACT_NAME"] == (
        "gate-terminal-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
        "-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert "--tier d30" in terminal_upload["run"]
    terminal_retry = next(s for s in gate_steps if s.get("name") == "Retry upload gate terminal envelope")
    assert terminal_retry["if"] == "always() && steps.upload-gate-terminal.outcome == 'failure'"
    assert terminal_retry["id"] == "retry-upload-gate-terminal"
    assert terminal_retry["continue-on-error"] is True
    assert "uses" not in terminal_retry
    assert terminal_retry["env"]["ARTIFACT_NAME"] == terminal_upload["env"]["ARTIFACT_NAME"]
    assert "--tier d30" in terminal_retry["run"]
    publish = next(s for s in gate_steps if s.get("name") == "Publish gate status panel")
    assert publish["if"] == (
        "always() && (steps.upload-gate-terminal.outcome == 'success' || "
        "steps.retry-upload-gate-terminal.outcome == 'success')"
    )
def test_gate_job_forwards_selected_audit_source_attempt_to_aggregator():
    """The attempt/name declared by the primary job output, not a Silo resolve."""
    raw, _ = _load_workflow()
    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    assert aggregate_step["env"]["AUDIT_SOURCE_ATTEMPT"] == "${{ needs.primary.outputs.audit_attempt }}"
    assert '--audit-source-attempt "$AUDIT_SOURCE_ATTEMPT"' in aggregate_step["run"]
    assert aggregate_step["env"]["AUDIT_ARTIFACT_NAME"] == (
        "primary-audit-v2-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
        "-${{ github.run_id }}-${{ needs.primary.outputs.audit_attempt }}"
    )
    assert '--audit-artifact-name "$AUDIT_ARTIFACT_NAME"' in aggregate_step["run"]
def test_review_ledger_input_uploads_declare_one_day_retention():
    raw, _ = _load_workflow()
    ledger_steps = raw["jobs"]["ledger"]["steps"]
    upload = next(
        step for step in ledger_steps if step.get("name") == "Upload v2 review ledger inputs"
    )
    retry = next(
        step
        for step in ledger_steps
        if step.get("name") == "Retry upload v2 review ledger inputs"
    )
    assert "--tier d1" in upload["run"]
    assert "--tier d1" in retry["run"]
    assert "$SILO_EXEC" in upload["run"]
    assert upload["env"]["ARTIFACT_NAME"] == retry["env"]["ARTIFACT_NAME"]
    assert upload["env"]["ARTIFACT_NAME"] == (
        "review-ledger-input-v2-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
        "-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert "needs.quality" not in upload["env"]["ARTIFACT_NAME"]
    assert "--repo-id \"${{ github.repository_id }}\"" in upload["run"]
def test_quality_exposes_ledger_input_upload_outcome_to_ledger():
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    assert quality["outputs"]["ledger_input_upload"] == (
        "${{ steps.ledger-input-upload-outcome.outputs.ledger_input_upload }}"
    )
    assert quality["outputs"]["ledger_input_bundle"] == (
        "${{ steps.ledger-input-upload.outputs.bundle }}"
    )
    resolver = next(
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Resolve v2 ledger artifacts"
    )
    assert resolver["env"]["QUALITY_LEDGER_INPUT_UPLOAD"] == (
        "${{ needs.quality.outputs.ledger_input_upload }}"
    )
    download = next(
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Download v2 review ledger inputs"
    )
    assert download["env"]["LEDGER_INPUT_BUNDLE"] == (
        "${{ needs.quality.outputs.ledger_input_bundle }}"
    )
def test_only_ledger_job_has_continue_on_error():
    raw, _ = _load_workflow()
    jobs = raw["jobs"]
    assert {job for job, spec in jobs.items() if spec.get("continue-on-error") is True} == {"ledger"}
def test_review_ledger_action_declares_and_forwards_input_short_circuited():
    """W5: action.yml 声明 input，composite run 按字面量转发，不用 shell 兜底。"""
    action = yaml.safe_load(
        (REPO_ROOT / ".github" / "actions" / "review-ledger" / "action.yml").read_text(
            encoding="utf-8"
        )
    )
    assert "input-short-circuited" in action.get("inputs", {})
    spec = action["inputs"]["input-short-circuited"]
    assert spec.get("required") in (None, False)
    assert str(spec.get("default")) == "false"
    run = action["runs"]["steps"][0]["run"]
    assert "--input-short-circuited" in run
    assert "${{ inputs.input-short-circuited }}" in run
    assert "${x:-" not in run
    assert ":-}" not in run
def test_gate_job_review_expected_matches_primary_jobs_own_condition():
    # Tightened (2026-07-26): these two strings are meant to be byte-for-byte
    # the SAME expression (recomputed in a different job only because a
    # skipped job's `if:` isn't itself exposed via `needs.*`) — assert full
    # equality, not just "all three guard substrings happen to be present",
    # which would tolerate the two copies silently drifting apart in other
    # ways (extra clauses, different operators, reordering with different
    # short-circuit semantics).
    raw, _ = _load_workflow()
    primary_if = str(raw["jobs"]["primary"].get("if", ""))
    for guard in (DRAFT_GUARD, FORK_GUARD, RUNNER_GUARD, CLASSIFY_GUARD):
        assert guard in primary_if, f"primary job if is missing {guard!r}"

    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    review_expected = str(aggregate_step["env"]["REVIEW_EXPECTED"])
    assert review_expected == primary_if, (
        "gate job's REVIEW_EXPECTED must be byte-for-byte identical to primary job's `if:`\n"
        f"primary if:        {primary_if!r}\n"
        f"REVIEW_EXPECTED:   {review_expected!r}"
    )
def test_gate_job_passes_the_identity_quintuple_to_the_aggregator():
    raw, _ = _load_workflow()
    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    env = aggregate_step["env"]
    assert env["REPOSITORY_ID"] == "${{ github.repository_id }}"
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert env["RUN_ID"] == "${{ github.run_id }}"
    assert env["RUN_ATTEMPT"] == "${{ github.run_attempt }}"
    assert env["PR_NUMBER"] == "${{ github.event.pull_request.number }}"
    assert env["REPOSITORY"] == "${{ github.repository }}"
    run = aggregate_step["run"]
    for flag in (
        "--quality-result", "--primary-result", "--runner", "--is-draft", "--review-expected",
        "--repository-id", "--head-sha", "--run-id", "--run-attempt", "--pr-number",
        "--audit-dir", "--summary-path", "--repository", "--terminal-path", "--audit-artifact-name",
    ):
        assert flag in run
def test_finding_relation_history_crosses_jobs_in_the_canonical_audit():
    raw, _ = _load_workflow()
    primary = next(
        step for step in raw["jobs"]["primary"]["steps"]
        if step.get("name") == "Annotate primary findings with cross-round relation"
    )
    aggregate = next(
        step for step in raw["jobs"]["gate"]["steps"]
        if step.get("name") == "Aggregate required verdict"
    )

    primary_tokens = [token for token in shlex.split(primary["run"]) if token != "\n"]
    env_keys = ("GH_TOKEN", "HEAD_SHA", "REPOSITORY_ID", "RUN_ID", "RUN_ATTEMPT", "PR_NUMBER")
    assert tuple(primary["env"].get(key) for key in env_keys) == (
        "${{ github.token }}", "${{ github.event.pull_request.head.sha }}", "${{ github.repository_id }}",
        "${{ github.run_id }}", "${{ github.run_attempt }}", "${{ github.event.pull_request.number }}",
    )
    annotate_index = primary_tokens.index("--annotate-primary-audit")
    assert primary_tokens[annotate_index - 2:annotate_index + 5] == [
        "python3", "$agg", "--annotate-primary-audit", "--audit-path", "$audit",
        "--previous-findings-path", "${RUNNER_TEMP}/previous-findings.json",
    ]
    assert 'audit="${{ runner.temp }}/primary-review-audit.json"' in primary["run"]

    previous_tokens = [token for token in shlex.split(next(
        step["run"] for step in raw["jobs"]["primary"]["steps"]
        if step.get("name") == "Inject previous-round findings into primary review context"
    )) if token != "\n"]
    previous_index = previous_tokens.index("--previous-json")
    assert previous_tokens[previous_index + 1] == "${RUNNER_TEMP}/previous-findings.json"

    aggregate_tokens = [token for token in shlex.split(aggregate["run"]) if token != "\n"]
    aggregate_index = aggregate_tokens.index(
        "_gate-aggregator-src/.github/actions/gate-aggregator/aggregate.py"
    )
    aggregate_end = aggregate_tokens.index("rc=$?", aggregate_index)
    aggregate_args = aggregate_tokens[aggregate_index + 1:aggregate_end]
    assert "--previous-findings-path" not in aggregate_args
    audit_dir_index = aggregate_args.index("--audit-dir")
    assert aggregate_args[audit_dir_index + 1] == "${{ runner.temp }}/primary-audit"
def test_gate_job_forwards_the_raw_runner_input_for_strict_validation():
    # The aggregator itself must strictly validate `runner` (self|hosted) —
    # see aggregate.py's RUNNER_DOMAIN — rather than trusting a workflow-side
    # expression to have already screened out typos.
    raw, _ = _load_workflow()
    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    assert aggregate_step["env"]["RUNNER_MODE"] == "${{ inputs.runner }}"
    assert '--runner "$RUNNER_MODE"' in aggregate_step["run"]
def test_gate_job_enables_the_sticky_status_panel_without_a_per_run_switch():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["gate"]["steps"]
    aggregate_step = next(s for s in steps if s.get("name") == "Aggregate required verdict")
    publish_step = next(s for s in steps if s.get("name") == "Publish gate status panel")
    assert "GH_TOKEN" in publish_step["env"]
    assert "--publish-only" in publish_step["run"]
    assert "--panel-delivery-path \"$PANEL_DELIVERY_PATH\"" in publish_step["run"]
    assert "--panel-delivery-path" not in aggregate_step["run"]
    assert "--pr-comment" not in publish_step["run"]
    script = AGGREGATOR_SCRIPT.read_text(encoding="utf-8")
    assert '"--pr-comment"' not in script
    assert 'os.environ.get("GITHUB_TOKEN")' in script
def test_gate_job_publishes_the_durable_panel_delivery_diagnostic():
    raw, _ = _load_workflow()
    gate_steps = raw["jobs"]["gate"]["steps"]
    publish_step = next(s for s in gate_steps if s.get("name") == "Publish gate status panel")
    assert publish_step["env"]["PANEL_DELIVERY_PATH"] == "${{ runner.temp }}/gate-status-panel-delivery.json"
    assert publish_step["env"]["GATE_PUBLISH_BUDGET_SECONDS"] == "${{ vars.GATE_PUBLISH_BUDGET_SECONDS || '120' }}"
    assert '--panel-delivery-path "$PANEL_DELIVERY_PATH"' in publish_step["run"]

    upload = next(s for s in gate_steps if s.get("name") == "Upload gate status panel delivery diagnostic")
    assert upload["if"] == "always()"
    assert "uses" not in upload
    assert upload["env"]["ARTIFACT_NAME"] == PANEL_DELIVERY_NAME_EXPR
    assert upload["env"]["PANEL_PATH"] == "${{ runner.temp }}/gate-status-panel-delivery.json"
    assert "--tier d3" in upload["run"]
    assert "$SILO_EXEC" in upload["run"]
    assert upload["id"] == "upload-gate-status-panel-diagnostic"
    assert upload["continue-on-error"] is True
    assert upload["env"]["PANEL_PATH"] == publish_step["env"]["PANEL_DELIVERY_PATH"]
    warning = next(
        s for s in gate_steps
        if s.get("name") == "Warn when gate status panel delivery diagnostic is unavailable"
    )
    assert warning["if"] == (
        "always() && (steps.upload-gate-status-panel-diagnostic.outcome == 'failure' || "
        "(steps.upload-gate-terminal.outcome == 'failure' && "
        "steps.retry-upload-gate-terminal.outcome == 'failure'))"
    )
    assert warning["env"] == {
        "PANEL_DIAGNOSTIC_OUTCOME": "${{ steps.upload-gate-status-panel-diagnostic.outcome }}",
        "TERMINAL_UPLOAD_OUTCOME": "${{ steps.upload-gate-terminal.outcome }}",
        "TERMINAL_RETRY_OUTCOME": "${{ steps.retry-upload-gate-terminal.outcome }}",
    }
    assert "::warning::" in warning["run"]
    assert "Gate terminal envelope upload failed after retry" in warning["run"]
def test_gate_job_timeout_matches_aggregate_publish_budget():
    raw, _ = _load_workflow()
    assert raw["jobs"]["gate"]["timeout-minutes"] == 15
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert "15 minutes: observed slow-API aggregator wallclock reached 11m04" in workflow_text
    assert "aggregation is seconds, publish is capped at <=2 minutes" not in workflow_text
def test_primary_job_if_gates_draft_fork_and_runner():
    raw, _ = _load_workflow()
    primary_if = str(raw["jobs"]["primary"].get("if", ""))
    assert DRAFT_GUARD in primary_if
    assert FORK_GUARD in primary_if
    assert RUNNER_GUARD in primary_if
    assert CLASSIFY_GUARD in primary_if
    assert primary_if == REVIEW_EXPECTED_IF
def test_pr_author_source_guards_review_and_reaches_aggregator_as_json():
    raw, _ = _load_workflow()
    assert PR_AUTHOR_HUMAN_GUARD in str(raw["jobs"]["quality"]["runs-on"])
    steps = raw["jobs"]["gate"]["steps"]
    aggregate = next(step for step in steps if step.get("name") == "Aggregate required verdict")
    assert aggregate["env"]["PR_AUTHOR_JSON"] == "${{ " + PR_AUTHOR_JSON + " }}"
    assert '--pr-author-json "$PR_AUTHOR_JSON"' in aggregate["run"]
    assert "github.actor" not in aggregate["run"]
def test_primary_runs_on_has_fork_guard_and_hosted_fallback():
    raw, _ = _load_workflow()
    runs_on = str(raw["jobs"]["primary"]["runs-on"])
    assert FORK_GUARD in runs_on
    assert "inputs.runner == 'self'" in runs_on
    assert "ubuntu-latest" in runs_on
def test_quality_runs_on_matches_legacy_fork_guard_pattern():
    raw, _ = _load_workflow()
    runs_on = str(raw["jobs"]["quality"]["runs-on"])
    assert FORK_GUARD in runs_on
    assert "github.event_name != 'pull_request'" in runs_on
    assert "ubuntu-latest" in runs_on
def test_quality_self_branch_routes_to_uncredentialed_ci_pool():
    # D7 (gate-hub docs/designs/runner-ci-pool-split.md): quality's self-hosted
    # branch must land on the uncredentialed CI pool label set, never the
    # review-credential codex pool.
    raw, _ = _load_workflow()
    runs_on = str(raw["jobs"]["quality"]["runs-on"])
    assert _self_hosted_label_set(runs_on) == {"self-hosted", "linux", "ci"}
    assert "codex" not in runs_on
def test_non_quality_jobs_do_not_use_ci_pool_label():
    # Guard against accidental migration of review/control-plane jobs onto the
    # uncredentialed CI pool. Assert on parsed fromJSON labels, not bare
    # substring match (would false-positive on words containing "ci").
    raw, _ = _load_workflow()
    for job_name in ("gate", "ledger", "primary", "classify_pr_paths"):
        runs_on = str(raw["jobs"][job_name]["runs-on"])
        for labels in _fromjson_label_sets(runs_on):
            assert "ci" not in labels, (
                f"{job_name} runs-on label set must not include 'ci': {labels!r}"
            )
def test_primary_run_review_primary_env_has_required_v2_identity_vars():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Run review-primary")
    env = run_step["env"]
    assert env["REVIEW_JOB_ID"] == "${{ steps.resolve-job-id.outputs.job_id }}"
    assert env["REVIEW_CALLER_SHA"] == "${{ github.workflow_sha }}"
    assert env["REVIEW_REUSABLE_WORKFLOW_SHA"] == "${{ job.workflow_sha }}"
    assert env["REVIEW_RUN_MODE"] == "PAYLOAD_ONLY"
    assert "review-primary" in run_step["run"]
    assert "${{ github.event.pull_request.number }}" in run_step["run"]
def test_run_review_primary_invokes_python3_not_bash():
    # P0 fix (2026-07-26, canary probe #2): review-primary is a Python entry point
    # (`#!/usr/bin/env python3` shebang) — an earlier draft invoked it via `bash
    # "$GATE_HUB_DIR/..."` (copied from legacy's own bash-script invocation pattern),
    # which fed Python source to bash and failed with a bash syntax error. Must be
    # invoked with an explicit `python3` interpreter, never relying on the shebang +
    # the file's own executable bit.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Run review-primary")
    run = run_step["run"].strip()
    assert run.startswith("python3 "), f"expected an explicit python3 invocation, got: {run!r}"
    assert not run.startswith("bash ")
def test_quality_and_primary_use_decoupled_timeout_inputs():
    # Tightened (2026-07-26): quality and primary must NOT share one timeout
    # input — primary needs a smaller, independently-tunable budget that
    # leaves headroom for review-primary to finalize before GitHub's hard
    # SIGKILL. Assert they are DIFFERENT input names, not just "both
    # parameterized somehow".
    raw, trigger = _load_workflow()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["timeout_minutes"]["default"] == 45
    assert inputs["primary_timeout_minutes"]["default"] == 25
    quality_timeout = raw["jobs"]["quality"]["timeout-minutes"]
    primary_timeout = raw["jobs"]["primary"]["timeout-minutes"]
    assert quality_timeout == "${{ inputs.timeout_minutes }}"
    assert primary_timeout == "${{ inputs.primary_timeout_minutes }}"
    assert quality_timeout != primary_timeout
def test_review_gate_timeout_s_is_computed_in_shell_not_in_a_gha_expression():
    # P1 fix (2026-07-26, codex re-review): GitHub Actions expression syntax
    # has NO arithmetic operators (the official Operators reference table is
    # limited to `() [] . ! < <= > >= == != && ||`) — an earlier draft used
    # `${{ (inputs.primary_timeout_minutes - 5) * 60 }}`, which fails
    # workflow parsing outright and would have broken every Required Gate
    # run. REVIEW_GATE_TIMEOUT_S must instead be computed with real shell
    # arithmetic in a `run:` step and exported via $GITHUB_ENV.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    compute_step = next(
        s for s in steps if s.get("name") == "Compute and validate review-primary's internal timeout budget"
    )
    run = compute_step["run"]
    assert "primary_timeout_minutes=${{ inputs.primary_timeout_minutes }}" in run
    assert "$(( (primary_timeout_minutes - 5) * 60 ))" in run
    assert "REVIEW_GATE_TIMEOUT_S=" in run
    assert "GITHUB_ENV" in run

    # The compute step must run BEFORE "Run review-primary" so the exported
    # env var is already present in the job's process environment.
    names = [s.get("name") for s in steps]
    assert names.index(compute_step["name"]) < names.index("Run review-primary")

    # "Run review-primary" must NOT re-declare REVIEW_GATE_TIMEOUT_S itself —
    # it inherits it from $GITHUB_ENV exported above.
    run_step = next(s for s in steps if s.get("name") == "Run review-primary")
    assert "REVIEW_GATE_TIMEOUT_S" not in run_step.get("env", {})
def test_primary_timeout_minutes_lower_bound_is_validated_and_fails_closed():
    # P2 fix (2026-07-26, codex re-review): a caller passing <= 5 would make
    # the derived review budget zero or negative. Must fail closed with a
    # clear error, not silently produce a nonsensical/negative timeout.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    compute_step = next(
        s for s in steps if s.get("name") == "Compute and validate review-primary's internal timeout budget"
    )
    run = compute_step["run"]
    assert '[ "$primary_timeout_minutes" -le 5 ]' in run
    assert "exit 1" in run
def test_primary_injects_previous_findings_and_names_the_degrade_line():
    # GATE-FINDING-RELATION-DEGRADED: file:tests/test_gate_v2_contract.py:1
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    names = [step.get("name") for step in steps]
    inject = names.index("Inject previous-round findings into primary review context")
    review = names.index("Run review-primary")
    annotate = names.index("Annotate primary findings with cross-round relation")
    assert inject < review < annotate
    assert "GATE-FINDING-RELATION-DEGRADED" in WORKFLOW.read_text(encoding="utf-8")
    inject_run = steps[inject].get("run", "")
    lines = inject_run.splitlines()
    status_assignment = lines.index("inject_status=$?")
    failure_branch = lines.index('if [ "$inject_status" -ne 0 ]; then')
    outer_else = lines.index("else", failure_branch)
    outer_fi = lines.index("fi", outer_else)
    output_check = '  if [ -s "${RUNNER_TEMP}/previous-findings.json" ]; then'
    export = '    echo "REVIEW_PREVIOUS_ROUND_PATH=${RUNNER_TEMP}/previous-findings.json" >> "$GITHUB_ENV"'
    assert output_check in lines[outer_else:outer_fi]
    assert export in lines
    output_nonempty = lines.index(output_check, outer_else)
    export_line = lines.index(export)
    assert status_assignment < failure_branch < outer_else < output_nonempty < export_line < outer_fi
    run_step = steps[review]
    assert "DESIGN_DOC" not in run_step.get("env", {})
    assert "REVIEW_PREVIOUS_ROUND_PATH" not in run_step.get("env", {})
def test_primary_does_not_splice_previous_findings_into_design_doc():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    names = [step.get("name") for step in steps]
    inject = names.index("Inject previous-round findings into primary review context")
    inject_step = steps[inject]
    assert inject_step["env"]["DESIGN_DOC_INPUT"] == "${{ inputs.design_doc }}"
    inject_run = inject_step.get("run", "")
    assert ".gate-review-context.md" not in inject_run
    assert "--design-doc" not in inject_run
    assert "DESIGN_DOC=.gate-review-context.md" not in inject_run
    lines = inject_run.splitlines()
    export = 'echo "DESIGN_DOC=${DESIGN_DOC_INPUT}" >> "$GITHUB_ENV"'
    assert lines.count(export) == 1
    assert sum(1 for line in lines if "DESIGN_DOC=" in line) == 1
    assert lines.index("inject_status=$?") < lines.index(export) < lines.index('if [ "$inject_status" -ne 0 ]; then')
