"""Contract tests for Required Gate v2 (.github/workflows/gate-v2.yml,
templates/caller-gate-v2.yml, .github/actions/gate-aggregator/aggregate.py).

Scope: this is the D1 "Required Gate" half of the shadow-review-independence
rollout (see the private gate-hub repo's
ceo-plans/2026-07-24-shadow-review-independence.md). Legacy
.github/workflows/gate.yml and its own tests/test_gate_contract.py are
kept behaviorally aligned with this file.
"""
import json
import re
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
COMMENT_RECEIPT_NAME_EXPR = (
    "gate-pr-comment-receipt-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
    "-${{ github.run_id }}-${{ github.run_attempt }}"
)
QUALITY_ENTRY_PATH = "scripts/gate-quality"
QUALITY_ENTRY_MODE = "steps.quality-entry.outputs.mode"
# fromJSON('["self-hosted","linux","ci"]') — capture each array literal in runs-on.
_FROMJSON_LABELS_RE = re.compile(r"fromJSON\('(\[[^\]]*\])'\)")


def _load_workflow():
    raw = yaml.safe_load(WORKFLOW.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _load_caller():
    raw = yaml.safe_load(CALLER_TEMPLATE.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _fromjson_label_sets(runs_on: str) -> list[set[str]]:
    """Parse each fromJSON('[...]') label array in a runs-on expression into a set."""
    out: list[set[str]] = []
    for match in _FROMJSON_LABELS_RE.finditer(runs_on):
        labels = json.loads(match.group(1))
        out.append(set(labels))
    return out


def _self_hosted_label_set(runs_on: str) -> set[str]:
    """Return the self-hosted pool label set (the fromJSON array that is not ubuntu-latest)."""
    for labels in _fromjson_label_sets(runs_on):
        if labels != {"ubuntu-latest"}:
            return labels
    raise AssertionError(f"no self-hosted fromJSON label set found in: {runs_on!r}")


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


def test_control_runner_input_defaults_to_follow_runner():
    _, trigger = _load_workflow()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["control_runner"]["default"] == ""


def test_all_required_jobs_present():
    raw, _ = _load_workflow()
    assert set(raw["jobs"].keys()) == {
        "quality", "primary", "resolve_advisory", "ocr", "gate", "ledger", "notify",
    }


def test_ocr_uses_advisory_event_subdirectory_and_pr_write_permissions():
    raw, _ = _load_workflow()
    ocr = raw["jobs"]["ocr"]
    assert ocr["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "write",
    }

    review_step = next(s for s in ocr["steps"] if s.get("name") == "Run OCR advisory review")
    assert review_step["env"]["REVIEW_SHADOW_EVENT_DIR"] == "${{ runner.temp }}/shadow-events"
    assert "REVIEW_SHADOW_ADVISORY_EVENT_DIR" not in review_step["env"]

    comment_step = next(s for s in ocr["steps"] if s.get("name") == "Post advisory PR comment")
    assert "REVIEW_SHADOW_EVENT_DIR/advisory/advisory-comment-${REVIEWER}.md" in comment_step["run"]
    assert 'gh pr comment "$PR_NUMBER" --body-file "$comment_path"' in comment_step["run"]

    upload_step = next(s for s in ocr["steps"] if s.get("name") == "Upload advisory review event")
    assert upload_step["with"]["path"] == "${{ runner.temp }}/shadow-events/advisory"
    assert raw["jobs"]["gate"]["needs"] == ["quality", "primary"]


# ── concurrency contract ─────────────────────────────────────────────────────

def test_concurrency_group_is_required_v2_and_defined_once_at_workflow_level():
    raw, _ = _load_workflow()
    assert "concurrency" not in raw
    assert not raw["jobs"]["gate"].get("concurrency", {})
    ledger_concurrency = raw["jobs"]["ledger"].get("concurrency", {})
    assert ledger_concurrency.get("cancel-in-progress") is False
    assert ledger_concurrency.get("queue") == "max"
    group = str(ledger_concurrency.get("group", ""))
    assert group.startswith("gate-required-v2-ledger-")
    assert "github.repository_id" in group
    assert "github.event.pull_request.number" not in group
    # Independence from the (future) Shadow Calibration group is a naming
    # contract, not something this file alone can prove — but the literal
    # prefix must never collide with `gate-shadow-v2-`.
    assert "shadow" not in group
    # Review jobs retain only workflow-level PR cancellation; ledger owns the
    # additional repository-level writer lock.
    for job_name, job in raw["jobs"].items():
        if job_name != "ledger":
            assert "concurrency" not in job


# ── gate aggregator job: required-check identity + always() ─────────────────

def test_gate_job_id_is_literally_gate_and_runs_always():
    raw, _ = _load_workflow()
    gate_job = raw["jobs"]["gate"]
    assert str(gate_job.get("if", "")) == "always()"
    needs = gate_job.get("needs")
    assert set(needs if isinstance(needs, list) else [needs]) == {"quality", "primary"}


def test_gate_and_notify_runs_on_use_the_same_guarded_control_plane_route():
    raw, _ = _load_workflow()
    expected = (
        "${{ (inputs.runner == 'self' && inputs.control_runner != 'github-hosted' && "
        "(github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository)) "
        "&& fromJSON('[\"self-hosted\",\"linux\",\"codex\"]') || fromJSON('[\"ubuntu-latest\"]') }}"
    )
    workflow_text = WORKFLOW.read_text()
    assert "gate-control" not in workflow_text
    for job_name in ("gate", "notify"):
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
    assert 'echo "artifact_name="' in resolver_run and 'echo "artifact_name=$artifact_name"' in resolver_run
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
    terminal_upload = next(s for s in gate_steps if s.get("name") == "Upload gate terminal envelope")
    assert terminal_upload["if"] == "always()" and terminal_upload["uses"] == "actions/upload-artifact@v4" and terminal_upload["with"] == {"name": "gate-terminal-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}-${{ github.run_id }}-${{ github.run_attempt }}", "path": "${{ runner.temp }}/gate-terminal.json", "if-no-files-found": "error"} and "continue-on-error" not in terminal_upload


def test_gate_job_forwards_selected_audit_source_attempt_to_aggregator():
    raw, _ = _load_workflow()
    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    assert aggregate_step["env"]["AUDIT_SOURCE_ATTEMPT"] == (
        "${{ steps.resolve-audit-artifact.outputs.source_attempt }}"
    )
    assert '--audit-source-attempt "$AUDIT_SOURCE_ATTEMPT"' in aggregate_step["run"]
    assert aggregate_step["env"]["AUDIT_ARTIFACT_NAME"] == "${{ steps.resolve-audit-artifact.outputs.artifact_name }}"
    assert '--audit-artifact-name "$AUDIT_ARTIFACT_NAME"' in aggregate_step["run"]


def test_ledger_job_builds_and_uploads_v2_review_ledger_without_gating():
    raw, _ = _load_workflow()
    quality_steps = raw["jobs"]["quality"]["steps"]
    gate_steps = raw["jobs"]["gate"]["steps"]
    ledger = raw["jobs"]["ledger"]
    steps = ledger["steps"]
    input_upload = next(step for step in quality_steps if step.get("name") == "Upload v2 review ledger inputs")
    input_download = next(step for step in steps if step.get("name") == "Download v2 review ledger inputs")
    build_index = next(i for i, step in enumerate(steps) if step.get("name") == "Build v2 review effectiveness ledger")
    upload_index = next(i for i, step in enumerate(steps) if step.get("name") == "Upload v2 review effectiveness ledger")

    assert ledger["needs"] == ["quality", "primary", "gate"]
    assert ledger["if"] == "always()"
    assert not any(step.get("name") == "Build v2 review effectiveness ledger" for step in gate_steps)
    assert not any(step.get("name") == "Upload v2 review effectiveness ledger" for step in gate_steps)
    assert input_upload["if"] == "always()"
    assert input_upload["continue-on-error"] is True
    assert input_download["with"]["artifact-ids"] == "${{ steps.resolve-ledger-artifacts.outputs.input_artifact_id }}"
    assert "pr-size-preflight.json" in input_upload["with"]["path"]
    assert "install-result.json" in input_upload["with"]["path"]
    assert input_download["with"]["path"] == "${{ runner.temp }}/review-ledger-input"
    build = steps[build_index]
    assert build["uses"] == "./_gate-aggregator-src/.github/actions/review-ledger"
    assert build["with"]["audit-path"] == "${{ runner.temp }}/primary-audit/primary-review-audit.json"
    assert build["with"]["codex-expected"] == raw["jobs"]["primary"]["if"]
    assert build["with"]["codex-waived"] is False
    assert build["with"]["expected-repository-id"] == "${{ github.repository_id }}"
    assert build["with"]["expected-base-sha"] == "${{ github.event.pull_request.base.sha }}"
    assert build["with"]["expected-caller-sha"] == "${{ github.workflow_sha }}"
    assert build["with"]["expected-reusable-workflow-sha"] == "${{ job.workflow_sha }}"

    upload = steps[upload_index]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"] == {
        "name": "codex-review-ledger-v2",
        "path": "${{ runner.temp }}/review-ledger/ledger.jsonl",
        "if-no-files-found": "error",
        "retention-days": 90,
    }


def test_ledger_resolver_is_strict_about_current_run_artifact_attempts():
    raw, _ = _load_workflow()
    resolver = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Resolve v2 ledger artifacts")
    assert resolver["if"] == "always()"
    assert resolver["env"]["CURRENT_ATTEMPT"] == "${{ github.run_attempt }}"
    assert resolver["env"]["REVIEW_EXPECTED"] == (
        "${{ github.event.pull_request.draft != true && github.event.pull_request.head.repo.full_name == github.repository && inputs.runner == 'self' }}"
    )
    run = resolver["run"]
    for marker in ("--paginate", "expired", "<= current", "input_artifact_id", "audit_artifact_id"):
        assert marker in run
    assert "No matching required ledger input artifact found" in run
    assert "No matching canonical primary audit artifact found" in run


def test_ledger_persistence_steps_are_fail_closed():
    raw, _ = _load_workflow()
    ledger_steps = raw["jobs"]["ledger"]["steps"]
    for name in (
        "Download v2 review ledger inputs",
        "Download canonical primary audit for ledger",
        "Build v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger",
    ):
        step = next(s for s in ledger_steps if s.get("name") == name)
        assert "continue-on-error" not in step


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
    assert env["REPOSITORY"] == "${{ github.repository }}"
    run = aggregate_step["run"]
    for flag in (
        "--quality-result", "--primary-result", "--runner", "--is-draft", "--review-expected",
        "--repository-id", "--head-sha", "--run-id", "--run-attempt", "--pr-number",
        "--audit-dir", "--summary-path", "--repository", "--terminal-path", "--audit-artifact-name",
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


def test_gate_job_enables_the_stage4_pr_comment_receipt():
    # Cross-publish-boundary pin, same technique as the REVIEW_EXPECTED pin
    # above: the workflow must pass BOTH the switch and a token env, and the
    # portable script's argparse must actually accept the flag — a workflow
    # passing a flag the checked-out script rejects would abort the required
    # gate job with an argparse error, and a script reading a token from argv
    # would leak it into process lists/logs.
    raw, _ = _load_workflow()
    aggregate_step = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Aggregate required verdict")
    assert aggregate_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "--pr-comment true" in aggregate_step["run"]
    script = AGGREGATOR_SCRIPT.read_text(encoding="utf-8")
    assert '"--pr-comment"' in script
    assert 'os.environ.get("GITHUB_TOKEN")' in script


def test_gate_job_publishes_the_durable_pr_comment_receipt():
    raw, _ = _load_workflow()
    gate_steps = raw["jobs"]["gate"]["steps"]
    aggregate_step = next(s for s in gate_steps if s.get("name") == "Aggregate required verdict")
    assert aggregate_step["env"]["COMMENT_RECEIPT_PATH"] == "${{ runner.temp }}/gate-pr-comment-receipt.json"
    assert '--comment-receipt-path "$COMMENT_RECEIPT_PATH"' in aggregate_step["run"]
    assert "--pr-comment true" in aggregate_step["run"]

    upload = next(s for s in gate_steps if s.get("name") == "Upload gate PR-comment receipt")
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"] == {
        "name": COMMENT_RECEIPT_NAME_EXPR,
        "path": "${{ runner.temp }}/gate-pr-comment-receipt.json",
        "if-no-files-found": "error",
        "retention-days": 30,
    }
    assert "continue-on-error" not in upload
    assert upload["with"]["path"] == aggregate_step["env"]["COMMENT_RECEIPT_PATH"]


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
    for job_name in ("gate", "ledger", "notify", "primary", "resolve_advisory", "ocr"):
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
    assert preflight["uses"] == "./_gate-action-src/.github/actions/pr-size-preflight"


def test_quality_preflight_checks_out_the_reusable_workflow_source():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["quality"]["steps"]
    checkout = next(
        step for step in steps
        if step.get("name") == "Checkout gate actions at this workflow's own commit"
    )
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"] == {
        "repository": "${{ job.workflow_repository }}",
        "ref": "${{ job.workflow_sha }}",
        "path": "_gate-action-src",
        "sparse-checkout": ".github/actions",
    }
    names = [step.get("name") for step in steps]
    assert names.index(checkout["name"]) < names.index("PR size preflight")


def test_quality_removes_gate_sources_before_any_caller_check():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["quality"]["steps"]
    names = [step.get("name") for step in steps]
    stale_cleanup = next(s for s in steps if s.get("name") == "Remove stale gate source directories")
    source_checkout = next(
        s for s in steps if s.get("name") == "Checkout gate actions at this workflow's own commit"
    )
    preflight_index = names.index("PR size preflight")
    cleanup = next(s for s in steps if s.get("name") == "Remove gate action source before caller checks")
    cleanup_index = names.index(cleanup["name"])
    first_caller_check = min(
        names.index(name)
        for name in (
            "Run scripts/gate-quality",
            "Lint / format",
            "Duplicate check (jscpd, advisory)",
            "Dependency direction (dependency-cruiser)",
            "Install dependencies",
            "Tests",
        )
    )

    assert names.index(stale_cleanup["name"]) < names.index(source_checkout["name"])
    assert names.index(source_checkout["name"]) < preflight_index < cleanup_index < first_caller_check
    assert cleanup_index == preflight_index + 1
    assert names[cleanup_index + 1] == "Run scripts/gate-quality"
    assert stale_cleanup["if"] == "always()"
    assert cleanup["if"] == "always()"
    assert 'rm -rf "$GITHUB_WORKSPACE/_gate-action-src"' in cleanup["run"]
    assert 'if [ -e "$GITHUB_WORKSPACE/_gate-action-src" ]; then' in cleanup["run"]
    assert 'if [ -e "$GITHUB_WORKSPACE/_gate-aggregator-src" ]; then' in cleanup["run"]
    assert 'rm -rf "$GITHUB_WORKSPACE/_gate-aggregator-src"' in stale_cleanup["run"]
    assert "for path in _gate-action-src _gate-aggregator-src; do" in stale_cleanup["run"]
    assert 'if [ -e "$GITHUB_WORKSPACE/$path" ]; then' in stale_cleanup["run"]

    for name in (
        "Run scripts/gate-quality",
        "Lint / format",
        "Duplicate check (jscpd, advisory)",
        "Dependency direction (dependency-cruiser)",
        "Install dependencies",
        "Tests",
    ):
        assert cleanup_index < names.index(name), f"gate source cleanup must precede {name}"


def test_v2_aggregator_jobs_do_not_execute_caller_quality_code():
    raw, _ = _load_workflow()
    caller_markers = (
        "scripts/gate-quality",
        "make lint",
        "jscpd",
        "depcruise",
        "npm test",
        "pytest",
        "uv sync",
        "pnpm install",
    )
    for job_name in ("gate", "ledger"):
        job = raw["jobs"][job_name]
        assert not any(
            marker in f"{step.get('run', '')} {step.get('uses', '')}"
            for step in job["steps"]
            for marker in caller_markers
        ), f"jobs.{job_name} caller-code scan must use run/uses fields"
        assert not any(
            step.get("uses", "").startswith("./_gate-action-src/")
            for step in job["steps"]
        )


def test_quality_action_sparse_checkout_excludes_tests_tree():
    raw, _ = _load_workflow()
    checkout = next(
        step for step in raw["jobs"]["quality"]["steps"]
        if step.get("name") == "Checkout gate actions at this workflow's own commit"
    )
    sparse_paths = checkout["with"]["sparse-checkout"]
    if isinstance(sparse_paths, str):
        sparse_paths = sparse_paths.splitlines()
    assert ".github/actions" in sparse_paths
    assert not any(path == "tests" or path.startswith("tests/") for path in sparse_paths)


def test_quality_entry_contract_covers_missing_non_executable_and_executable_states():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["quality"]["steps"]
    detect = next(step for step in steps if step.get("name") == "Detect repository quality entry")
    run = detect["run"]
    assert detect["id"] == "quality-entry"
    assert f"[ -e {QUALITY_ENTRY_PATH} ]" in run
    assert "DEPRECATION: scripts/gate-quality is missing" in run
    assert 'echo "mode=legacy" >> "$GITHUB_OUTPUT"' in run
    assert "[ ! -x scripts/gate-quality ]" in run
    assert "scripts/gate-quality exists but is not executable" in run
    assert "exit 1" in run

    entry = next(step for step in steps if step.get("name") == "Run scripts/gate-quality")
    assert entry["if"] == "${{ steps.quality-entry.outputs.mode == 'entry' }}"
    assert entry["working-directory"] == "${{ github.workspace }}"
    assert entry["run"].strip() == "./scripts/gate-quality"
    assert detect["env"]["GATE_ARTIFACT_DIR"] == "${{ runner.temp }}/gate-quality"
    assert entry["env"]["GATE_ARTIFACT_DIR"] == "${{ runner.temp }}/gate-quality"
    names = [step.get("name") for step in steps]
    assert names.count("Run scripts/gate-quality") == 1
    assert sum(step.get("run", "").strip() == "./scripts/gate-quality" for step in steps) == 1
    assert names.index("PR size preflight") < names.index("Run scripts/gate-quality")

    legacy_steps = [step for step in steps if step.get("name") in {
        "Lint / format", "Duplicate check (jscpd, advisory)",
        "Dependency direction (dependency-cruiser)", "Install dependencies", "Tests",
    }]
    assert legacy_steps
    for step in legacy_steps:
        assert f"{QUALITY_ENTRY_MODE} == 'legacy'" in str(step.get("if", ""))


def test_v2_quality_entry_detection_and_legacy_python_selection_match_v1():
    legacy_raw = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "gate.yml").read_text())
    legacy_steps = legacy_raw["jobs"]["gate"]["steps"]
    raw, _ = _load_workflow()
    quality_steps = raw["jobs"]["quality"]["steps"]
    for name in ("Detect repository quality entry", "Run scripts/gate-quality"):
        legacy_step = next(step for step in legacy_steps if step.get("name") == name)
        v2_step = next(step for step in quality_steps if step.get("name") == name)
        assert v2_step.get("run") == legacy_step.get("run")
        assert v2_step.get("env") == legacy_step.get("env")

    legacy_tests = next(step for step in legacy_steps if step.get("name") == "Tests")
    v2_tests = next(step for step in quality_steps if step.get("name") == "Tests")
    assert "uv run --frozen pytest -q || uv run pytest -q" not in v2_tests["run"]
    assert "if [ -f uv.lock ]; then" in v2_tests["run"]
    assert v2_tests["run"] == legacy_tests["run"]


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
