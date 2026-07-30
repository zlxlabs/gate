"""Contract tests for Required Gate v2 (.github/workflows/gate-v2.yml,
templates/caller-gate-v2.yml, .github/actions/gate-aggregator/aggregate.py).

Scope: this is the D1 "Required Gate" half of the shadow-review-independence
rollout (see the private gate-hub repo's
ceo-plans/2026-07-24-shadow-review-independence.md). Legacy
.github/workflows/gate.yml and its own tests/test_gate_contract.py are
untouched and unaffected by this file.
"""
from pathlib import Path

import yaml

from _gha_lint import find_arithmetic_gha_expression_offenders

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
CALLER_TEMPLATE = REPO_ROOT / "templates" / "caller-gate-v2.yml"
AGGREGATOR_SCRIPT = REPO_ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"

FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
DRAFT_GUARD = "github.event.pull_request.draft != true"
RUNNER_GUARD = "inputs.runner == 'self'"
ARTIFACT_NAME_EXPR = (
    "primary-audit-v2-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
    "-${{ github.run_id }}-${{ github.run_attempt }}"
)
ARTIFACT_PREFIX_EXPR = (
    "primary-audit-v2-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
    "-${{ github.run_id }}-"
)


def _load_workflow():
    raw = yaml.safe_load(WORKFLOW.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _load_caller():
    raw = yaml.safe_load(CALLER_TEMPLATE.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


# ── reusable workflow shape ──────────────────────────────────────────────────

def test_is_workflow_call_named_gate():
    raw, trigger = _load_workflow()
    assert "workflow_call" in trigger
    assert raw["name"] == "gate"


def test_secrets_explicit_and_feishu_optional():
    code = "\n".join(ln for ln in WORKFLOW.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert "inherit" not in code
    _, trigger = _load_workflow()
    secrets = trigger["workflow_call"].get("secrets", {})
    assert set(secrets.keys()) == {"FEISHU_CI_WEBHOOK"}
    assert secrets["FEISHU_CI_WEBHOOK"].get("required") is False


def test_control_runner_input_defaults_to_hosted():
    _, trigger = _load_workflow()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["control_runner"]["default"] == "github-hosted"


def test_all_four_jobs_present():
    raw, _ = _load_workflow()
    assert set(raw["jobs"].keys()) == {"quality", "primary", "gate", "notify"}


# ── concurrency contract ─────────────────────────────────────────────────────

def test_concurrency_group_is_required_v2_and_defined_once_at_workflow_level():
    raw, _ = _load_workflow()
    concurrency = raw.get("concurrency", {})
    assert concurrency.get("cancel-in-progress") is True
    group = str(concurrency.get("group", ""))
    assert group.startswith("gate-required-v2-")
    assert "github.repository_id" in group
    assert "github.event.pull_request.number" in group
    # Independence from the (future) Shadow Calibration group is a naming
    # contract, not something this file alone can prove — but the literal
    # prefix must never collide with `gate-shadow-v2-`.
    assert "shadow" not in group
    # Must be a single top-level definition, not one per job (a caller must
    # never be able to define a competing group).
    for job in raw["jobs"].values():
        assert "concurrency" not in job


# ── gate aggregator job: required-check identity + always() ─────────────────

def test_gate_job_id_is_literally_gate_and_runs_always():
    raw, _ = _load_workflow()
    gate_job = raw["jobs"]["gate"]
    assert str(gate_job.get("if", "")) == "always()"
    needs = gate_job.get("needs")
    assert set(needs if isinstance(needs, list) else [needs]) == {"quality", "primary"}


def test_gate_job_runs_on_control_runner_selector_defaulting_hosted():
    raw, _ = _load_workflow()
    runs_on = str(raw["jobs"]["gate"]["runs-on"])
    assert "inputs.control_runner == 'self-hosted-control'" in runs_on
    assert "ubuntu-latest" in runs_on


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
    assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
    assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
    assert not any(str(s.get("uses", "")).startswith("zlxlabs/gate/.github/actions/gate-aggregator") for s in steps)
    aggregate_step = next(s for s in steps if s.get("name") == "Aggregate required verdict")
    assert "aggregate.py" in aggregate_step["run"]
    assert AGGREGATOR_SCRIPT.is_file()


def test_gate_job_downloads_the_same_artifact_name_primary_uploads():
    raw, _ = _load_workflow()
    primary_steps = raw["jobs"]["primary"]["steps"]
    upload = next(s for s in primary_steps if s.get("name") == "Upload canonical primary audit")
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["name"] == ARTIFACT_NAME_EXPR
    # fail-closed: unlike legacy's advisory codex-audit upload, this upload has no
    # continue-on-error. P1 fix (2026-07-26, canary probe #2): if-no-files-found MUST be
    # explicit `error` — actions/upload-artifact@v4's own default is `warn` (a step
    # annotation, not a failure), which is what let canary's primary job conclude
    # `success` despite writing no audit at all; relying on "we didn't set `ignore`"
    # alone was never sufficient fail-closed enforcement.
    assert "continue-on-error" not in upload
    assert upload["with"]["if-no-files-found"] == "error"

    gate_steps = raw["jobs"]["gate"]["steps"]
    resolver = next(s for s in gate_steps if s.get("name") == "Resolve canonical primary audit artifact")
    assert resolver["id"] == "resolve-audit-artifact"
    assert resolver["if"] == "${{ needs.primary.result != 'skipped' }}"
    assert resolver["continue-on-error"] is True
    assert resolver["env"]["AUDIT_PREFIX"] == ARTIFACT_PREFIX_EXPR
    resolver_run = resolver["run"]
    assert "gh api" in resolver_run
    assert "actions/runs/${{ github.run_id }}/artifacts" in resolver_run
    assert "--paginate" in resolver_run
    assert "expired" in resolver_run
    assert "<= current_attempt" in resolver_run
    assert "max(" in resolver_run
    assert 'artifact_id=' in resolver_run
    assert 'source_attempt=' in resolver_run
    assert 'echo "artifact_id="' in resolver_run
    assert 'No matching canonical primary audit artifact found' in resolver_run

    download = next(s for s in gate_steps if s.get("name") == "Download canonical primary audit (best effort — may not exist)")
    assert download["with"]["artifact-ids"] == "${{ steps.resolve-audit-artifact.outputs.artifact_id }}"
    assert download["with"]["merge-multiple"] is True
    assert "name" not in download["with"]
    assert download["continue-on-error"] is True
    assert download["if"] == (
        "${{ needs.primary.result != 'skipped' && "
        "steps.resolve-audit-artifact.outputs.artifact_id != '' }}"
    )


def test_gate_job_forwards_selected_audit_source_attempt_to_aggregator():
    raw, _ = _load_workflow()
    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    assert aggregate_step["env"]["AUDIT_SOURCE_ATTEMPT"] == (
        "${{ steps.resolve-audit-artifact.outputs.source_attempt }}"
    )
    assert '--audit-source-attempt "$AUDIT_SOURCE_ATTEMPT"' in aggregate_step["run"]


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
    for guard in (DRAFT_GUARD, FORK_GUARD, RUNNER_GUARD):
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
    run = aggregate_step["run"]
    for flag in (
        "--quality-result", "--primary-result", "--runner", "--is-draft", "--review-expected",
        "--repository-id", "--head-sha", "--run-id", "--run-attempt", "--pr-number",
        "--audit-dir", "--summary-path",
    ):
        assert flag in run


def test_gate_job_forwards_the_raw_runner_input_for_strict_validation():
    # The aggregator itself must strictly validate `runner` (self|hosted) —
    # see aggregate.py's RUNNER_DOMAIN — rather than trusting a workflow-side
    # expression to have already screened out typos.
    raw, _ = _load_workflow()
    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    assert aggregate_step["env"]["RUNNER_MODE"] == "${{ inputs.runner }}"
    assert '--runner "$RUNNER_MODE"' in aggregate_step["run"]


def test_gate_job_timeout_is_five_minutes():
    raw, _ = _load_workflow()
    assert raw["jobs"]["gate"]["timeout-minutes"] == 5


# ── primary job: draft/fork/hosted skip + fail-closed upload ─────────────────

def test_primary_job_if_gates_draft_fork_and_runner():
    raw, _ = _load_workflow()
    primary_if = str(raw["jobs"]["primary"].get("if", ""))
    assert DRAFT_GUARD in primary_if
    assert FORK_GUARD in primary_if
    assert RUNNER_GUARD in primary_if


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


# Regression guard for the P1 above: GitHub Actions expressions can never contain
# `+ - * / %` as operators anywhere in this file (not just in the one spot that broke).
# The scan/regex logic itself now lives in _gha_lint.py (2026-07-26, extracted for the D2
# gate-shadow-v2 task so the same guard covers that file too, without a second hand-kept
# copy of this regex) — this test just points the shared helper at THIS workflow file.


def test_no_gha_expression_anywhere_uses_arithmetic_operators():
    offenders = find_arithmetic_gha_expression_offenders(WORKFLOW)
    assert not offenders, f"found arithmetic-looking operator(s) inside GHA expression(s): {offenders!r}"


def test_pr_size_preflight_runs_before_expensive_checks_in_quality():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["quality"]["steps"]
    names = [s.get("name") for s in steps]
    preflight_index = names.index("PR size preflight")
    assert preflight_index < names.index("Lint / format")
    preflight = steps[preflight_index]
    assert preflight["uses"].startswith("zlxlabs/gate/.github/actions/pr-size-preflight@")


# ── notify job ────────────────────────────────────────────────────────────

def test_notify_triggers_only_on_gate_failure():
    raw, _ = _load_workflow()
    notify = raw["jobs"]["notify"]
    needs = notify.get("needs")
    assert (needs if isinstance(needs, list) else [needs]) == ["gate"]
    assert notify.get("if") == "failure()"


def test_notify_webhook_secret_first_var_fallback():
    text = WORKFLOW.read_text()
    assert "secrets.FEISHU_CI_WEBHOOK || vars.FEISHU_CI_WEBHOOK" in text
    assert "vars.FEISHU_CI_TITLE_PREFIX" in text


# ── caller template ──────────────────────────────────────────────────────────

def test_caller_declares_ready_for_review_and_converted_to_draft():
    _, trigger = _load_caller()
    types = trigger["pull_request"]["types"]
    assert "ready_for_review" in types
    assert "converted_to_draft" in types
    assert set(types) == {"opened", "synchronize", "reopened", "ready_for_review", "converted_to_draft"}


def test_caller_merge_group_is_not_an_active_trigger():
    # Tightened (2026-07-26): a real merge_group event carries no
    # `pull_request` payload, and gate-v2.yml's expressions (plus the
    # aggregator's argparse contract) all assume one exists — a live
    # merge_group run was confirmed to abort with a hard argparse failure.
    # merge_group must NOT be an active trigger; it stays reserved only as a
    # comment (see the file's top-of-file explanation) until a follow-up PR
    # actually adapts the expressions/CLI contract for a PR-less payload.
    _, trigger = _load_caller()
    assert "merge_group" not in trigger
    assert set(trigger.keys()) == {"pull_request"}
    text = CALLER_TEMPLATE.read_text()
    assert "merge_group" in text  # still documented as a reserved non-trigger, in a comment


def test_caller_paths_ignore_mirrors_gate_hubs_existing_caller_convention():
    _, trigger = _load_caller()
    paths_ignore = trigger["pull_request"]["paths-ignore"]
    assert paths_ignore == ["**.md", "docs/**"]


def test_caller_job_id_and_workflow_name_are_gate():
    raw, _ = _load_caller()
    assert raw["name"] == "gate"
    assert "gate" in raw["jobs"]
    assert raw["jobs"]["gate"]["uses"].startswith("zlxlabs/gate/.github/workflows/gate-v2.yml@")


def test_caller_permissions_minimal_and_no_secrets_inherit():
    raw, _ = _load_caller()
    assert raw["permissions"] == {"actions": "read", "contents": "read", "pull-requests": "write"}
    code = "\n".join(ln for ln in CALLER_TEMPLATE.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert "inherit" not in code
