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
    # fail-closed: unlike legacy's advisory codex-audit upload, this upload has
    # no continue-on-error and no if-no-files-found: ignore.
    assert "continue-on-error" not in upload
    assert "if-no-files-found" not in upload["with"]

    gate_steps = raw["jobs"]["gate"]["steps"]
    download = next(s for s in gate_steps if s.get("name") == "Download canonical primary audit (best effort — may not exist)")
    assert download["with"]["name"] == ARTIFACT_NAME_EXPR
    assert download["continue-on-error"] is True
    assert download["if"] == "${{ needs.primary.result != 'skipped' }}"


def test_gate_job_review_expected_matches_primary_jobs_own_condition():
    raw, _ = _load_workflow()
    primary_if = str(raw["jobs"]["primary"].get("if", ""))
    for guard in (DRAFT_GUARD, FORK_GUARD, RUNNER_GUARD):
        assert guard in primary_if, f"primary job if is missing {guard!r}"

    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    review_expected = str(aggregate_step["env"]["REVIEW_EXPECTED"])
    for guard in (DRAFT_GUARD, FORK_GUARD, RUNNER_GUARD):
        assert guard in review_expected, f"gate job's REVIEW_EXPECTED is missing {guard!r}"


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
        "--quality-result", "--primary-result", "--is-draft", "--review-expected",
        "--repository-id", "--head-sha", "--run-id", "--run-attempt", "--pr-number",
        "--audit-dir", "--summary-path",
    ):
        assert flag in run


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
    assert "review-primary" in run_step["run"]
    assert "${{ github.event.pull_request.number }}" in run_step["run"]


def test_quality_timeout_is_parameterized():
    raw, trigger = _load_workflow()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["timeout_minutes"]["default"] == 45
    assert raw["jobs"]["quality"]["timeout-minutes"] == "${{ inputs.timeout_minutes }}"
    assert raw["jobs"]["primary"]["timeout-minutes"] == "${{ inputs.timeout_minutes }}"


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


def test_caller_reserves_merge_group_trigger():
    _, trigger = _load_caller()
    assert "merge_group" in trigger


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
