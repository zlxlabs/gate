"""Contract tests for Shadow Calibration v2 (.github/workflows/gate-shadow-v2.yml,
templates/caller-gate-shadow-v2.yml).

Scope: this is the D2 "Shadow Calibration" half of the shadow-review-independence
rollout (see the private gate-hub repo's ceo-plans/2026-07-24-shadow-review-independence.md).
D1's Required Gate v2 (.github/workflows/gate-v2.yml, templates/caller-gate-v2.yml,
tests/test_gate_v2_contract.py) is untouched and unaffected by this file — several tests
below load gate-v2.yml too, READ-ONLY, purely to assert this file's guards stay
byte-identical to (or, for the concurrency group, deliberately DIFFERENT from) D1's own.
"""
from pathlib import Path

import yaml

from _gha_lint import find_arithmetic_gha_expression_offenders

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-shadow-v2.yml"
CALLER_TEMPLATE = REPO_ROOT / "templates" / "caller-gate-shadow-v2.yml"
REQUIRED_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"

# Byte-identical to test_gate_v2_contract.py's own FORK_GUARD/DRAFT_GUARD/RUNNER_GUARD —
# redefined locally (rather than imported) so this test file has no import-time coupling
# to test_gate_v2_contract.py's own module contents; the actual cross-file EQUALITY is
# still asserted directly against gate-v2.yml's parsed YAML below, not against these
# string constants alone.
FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
DRAFT_GUARD = "github.event.pull_request.draft != true"
RUNNER_GUARD = "inputs.runner == 'self'"


def _load_workflow():
    raw = yaml.safe_load(WORKFLOW.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _load_caller():
    raw = yaml.safe_load(CALLER_TEMPLATE.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _load_required_workflow():
    raw = yaml.safe_load(REQUIRED_WORKFLOW.read_text())
    return raw


# ── reusable workflow shape ──────────────────────────────────────────────────

def test_is_workflow_call_named_gate_shadow():
    raw, trigger = _load_workflow()
    assert "workflow_call" in trigger
    assert raw["name"] == "gate-shadow"


def test_no_secrets_declared():
    # No notify job, no PR comment — this workflow needs no secret at all (contrast with
    # gate-v2.yml's optional FEISHU_CI_WEBHOOK).
    _, trigger = _load_workflow()
    assert "secrets" not in trigger["workflow_call"]


def test_all_three_jobs_present_and_no_notify_job():
    raw, _ = _load_workflow()
    assert set(raw["jobs"].keys()) == {"resolve", "shadow", "summary"}
    assert "notify" not in raw["jobs"]


def test_permissions_have_no_pr_or_issue_write_scope():
    raw, _ = _load_workflow()
    assert raw["permissions"] == {"actions": "read", "contents": "read"}


# ── concurrency contract: independent from, and never equal to, Required Gate's ────

def test_concurrency_group_is_shadow_v2_and_defined_once_at_workflow_level():
    raw, _ = _load_workflow()
    concurrency = raw.get("concurrency", {})
    assert concurrency.get("cancel-in-progress") is True
    group = str(concurrency.get("group", ""))
    assert group.startswith("gate-shadow-v2-")
    assert "github.repository_id" in group
    assert "github.event.pull_request.number" in group
    for job in raw["jobs"].values():
        assert "concurrency" not in job


def test_concurrency_group_expression_differs_from_required_gates():
    # The plan's "required 与 shadow 的 group 永不相同" — assert the two FULL group
    # expression strings are literally unequal (not just "one contains 'shadow' and the
    # other doesn't" — a stronger, D1-style full-string check).
    raw, _ = _load_workflow()
    shadow_group = str(raw["concurrency"]["group"])
    required_raw = _load_required_workflow()
    required_group = str(required_raw["concurrency"]["group"])
    assert shadow_group != required_group
    assert shadow_group.replace("gate-shadow-v2-", "") == required_group.replace("gate-required-v2-", "")


# ── resolve job: draft/fork/hosted guard byte-identical to gate-v2.yml's `primary` ──

def test_resolve_if_is_byte_identical_to_gate_v2_primary_if():
    raw, _ = _load_workflow()
    required_raw = _load_required_workflow()
    resolve_if = str(raw["jobs"]["resolve"].get("if", ""))
    primary_if = str(required_raw["jobs"]["primary"].get("if", ""))
    for guard in (DRAFT_GUARD, FORK_GUARD, RUNNER_GUARD):
        assert guard in resolve_if, f"resolve job if is missing {guard!r}"
    assert resolve_if == primary_if, (
        "gate-shadow-v2.yml's `resolve` job if: must be byte-for-byte identical to "
        "gate-v2.yml's `primary` job if:\n"
        f"resolve if:  {resolve_if!r}\n"
        f"primary if:  {primary_if!r}"
    )


def test_resolve_runs_on_is_byte_identical_to_gate_v2_primary_runs_on():
    raw, _ = _load_workflow()
    required_raw = _load_required_workflow()
    resolve_runs_on = str(raw["jobs"]["resolve"]["runs-on"])
    primary_runs_on = str(required_raw["jobs"]["primary"]["runs-on"])
    assert resolve_runs_on == primary_runs_on, (
        "gate-shadow-v2.yml's `resolve` job runs-on: must be byte-for-byte identical to "
        "gate-v2.yml's `primary` job runs-on:\n"
        f"resolve runs-on:  {resolve_runs_on!r}\n"
        f"primary runs-on:  {primary_runs_on!r}"
    )


def test_shadow_runs_on_matches_the_same_fork_guard_ternary():
    raw, _ = _load_workflow()
    required_raw = _load_required_workflow()
    shadow_runs_on = str(raw["jobs"]["shadow"]["runs-on"])
    primary_runs_on = str(required_raw["jobs"]["primary"]["runs-on"])
    assert shadow_runs_on == primary_runs_on


def test_resolve_has_no_checkout_step():
    # resolve_policy.py only needs github.repository + GATE_HUB_DIR's own registry.yaml —
    # never the reviewed repo's own code.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["resolve"]["steps"]
    assert not any(str(s.get("uses", "")).startswith("actions/checkout") for s in steps)


# ── resolve -> shadow matrix data flow, including the empty-list boundary ──────

def test_resolve_outputs_shadow_reviewers_from_resolve_policy_step():
    raw, _ = _load_workflow()
    resolve_job = raw["jobs"]["resolve"]
    assert resolve_job["outputs"]["shadow_reviewers"] == "${{ steps.resolve-policy.outputs.shadow_reviewers }}"
    steps = resolve_job["steps"]
    step = next(s for s in steps if s.get("id") == "resolve-policy")
    assert "resolve_policy.py" in step["run"]
    assert 'echo "shadow_reviewers=$shadow_reviewers" >> "$GITHUB_OUTPUT"' in step["run"]
    # Fail-closed: no continue-on-error anywhere on this step — a PolicyError must fail
    # the `resolve` job outright, never silently degrade to an empty shadow list.
    assert "continue-on-error" not in step


def test_shadow_matrix_uses_fallback_to_empty_array_not_raw_needs_output():
    # The safety net for "resolve was skipped -> needs.resolve.outputs.shadow_reviewers
    # is an empty string" MUST be the `|| '[]'` fallback inside fromJSON, not solely the
    # job-level `if:` (GitHub Actions' precise evaluation order between a job's `if:` and
    # its own `strategy.matrix` expression is not something this suite treats as safe to
    # assume) — assert the fallback literal is actually present.
    raw, _ = _load_workflow()
    matrix_expr = str(raw["jobs"]["shadow"]["strategy"]["matrix"]["reviewer"])
    assert "fromJSON(needs.resolve.outputs.shadow_reviewers || '[]')" in matrix_expr


def test_shadow_job_needs_resolve_and_gates_on_its_result():
    raw, _ = _load_workflow()
    shadow_job = raw["jobs"]["shadow"]
    assert shadow_job["needs"] == "resolve"
    assert str(shadow_job.get("if", "")) == "needs.resolve.result == 'success'"


# ── shadow matrix job: fail-fast/max-parallel/timeout/artifact naming ──────────

def test_shadow_strategy_fail_fast_false():
    raw, _ = _load_workflow()
    assert raw["jobs"]["shadow"]["strategy"]["fail-fast"] is False


def test_shadow_max_parallel_is_one_and_documented_as_canary_scoped_not_fleet_wide():
    raw, _ = _load_workflow()
    assert raw["jobs"]["shadow"]["strategy"]["max-parallel"] == 1
    text = WORKFLOW.read_text()
    # The comment must explicitly disclaim fleet-wide reuse (D2 task requirement) — pin
    # on the key phrases rather than the whole comment paragraph, so minor prose edits
    # don't spuriously break this test while the actual disclaimer stays enforced.
    assert "CANARY-SCOPED" in text
    assert "NOT A FLEET-LEVEL RATE LIMITER" in text
    assert "DO NOT reuse `max-parallel`" in text
    assert "broker-lite" in text


def test_shadow_timeout_is_fifteen_minutes():
    raw, _ = _load_workflow()
    assert raw["jobs"]["shadow"]["timeout-minutes"] == 15


def test_shadow_upload_step_name_pattern_matches_summary_download_pattern():
    raw, _ = _load_workflow()
    shadow_steps = raw["jobs"]["shadow"]["steps"]
    upload = next(s for s in shadow_steps if s.get("name") == "Upload shadow calibration event")
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    upload_name = str(upload["with"]["name"])
    assert upload_name == "shadow-event-${{ matrix.reviewer }}-${{ github.run_id }}-${{ github.run_attempt }}"
    assert upload["with"]["if-no-files-found"] == "warn"

    summary_steps = raw["jobs"]["summary"]["steps"]
    download = next(s for s in summary_steps if s.get("name") == "Download all shadow event artifacts for this run")
    pattern = str(download["with"]["pattern"])
    assert pattern == "shadow-event-*-${{ github.run_id }}-${{ github.run_attempt }}"
    # The download pattern must be the upload name with `${{ matrix.reviewer }}`
    # wildcarded and nothing else changed — assert this structurally, not just by
    # eyeballing the two literals above.
    assert upload_name.replace("${{ matrix.reviewer }}", "*") == pattern
    assert download["with"]["merge-multiple"] is True


def test_shadow_job_id_resolution_accounts_for_matrix_leg_naming():
    # Extends gate-v2.yml's own (non-matrix) "Resolve numeric job id" step: a matrix
    # leg's Jobs-API `name` is rendered as "<job id> (<matrix value>)", optionally
    # prefixed by the calling job's own name — assert the jq selector accounts for the
    # `(${{ matrix.reviewer }})` suffix gate-v2.yml's own copy never needed.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["shadow"]["steps"]
    step = next(s for s in steps if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID")
    run = step["run"]
    assert '.name == "${{ github.job }} (${{ matrix.reviewer }})"' in run
    assert 'endswith("/ ${{ github.job }} (${{ matrix.reviewer }})")' in run


def test_run_review_shadow_env_has_required_v2_identity_vars():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["shadow"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Run review-shadow")
    env = run_step["env"]
    assert env["REVIEW_JOB_ID"] == "${{ steps.resolve-job-id.outputs.job_id }}"
    assert env["REVIEW_CALLER_SHA"] == "${{ github.workflow_sha }}"
    assert env["REVIEW_REUSABLE_WORKFLOW_SHA"] == "${{ job.workflow_sha }}"
    assert "review-shadow" in run_step["run"]
    assert '"${{ github.event.pull_request.number }}"' in run_step["run"]
    assert '"${{ matrix.reviewer }}"' in run_step["run"]
    # REVIEW_GATE_TIMEOUT_S deliberately NOT overridden — review-shadow's own default
    # (780s) already matches the plan's stated shadow budget.
    assert "REVIEW_GATE_TIMEOUT_S" not in env


# ── summary job: always() + explicit non-empty guard, no PR-write permission ───

def test_summary_job_needs_resolve_and_shadow():
    raw, _ = _load_workflow()
    needs = raw["jobs"]["summary"]["needs"]
    assert set(needs if isinstance(needs, list) else [needs]) == {"resolve", "shadow"}


def test_summary_if_contains_always_and_explicit_non_empty_guards():
    # Tightened, D1-style: `always()` alone is not enough — review-summary itself
    # requires >=1 expected reviewer argv, so this job must ALSO skip cleanly whenever
    # `resolve` produced an empty/absent shadow list (draft/fork/hosted skip, OR a
    # same-repo trusted PR whose resolved policy legitimately has zero shadow
    # reviewers) — assert `always()` is present AND both explicit guard clauses are
    # present, rather than accepting a bare `if: always()`.
    raw, _ = _load_workflow()
    summary_if = str(raw["jobs"]["summary"].get("if", ""))
    assert "always()" in summary_if
    assert "needs.resolve.outputs.shadow_reviewers != ''" in summary_if
    assert "needs.resolve.outputs.shadow_reviewers != '[]'" in summary_if


def test_summary_runs_on_self_hosted_with_no_fork_guard_ternary():
    # Unlike resolve/shadow, summary never checks out or executes the reviewed repo's
    # own code (only trusted gate-hub scripts + this run's own structured artifacts), so
    # it carries no defensive fork-guard runs-on ternary of its own — assert this is a
    # plain label list, not a ternary expression string.
    raw, _ = _load_workflow()
    runs_on = raw["jobs"]["summary"]["runs-on"]
    assert isinstance(runs_on, list)
    assert set(runs_on) == {"self-hosted", "linux", "codex"}


def test_summary_timeout_is_five_minutes():
    raw, _ = _load_workflow()
    assert raw["jobs"]["summary"]["timeout-minutes"] == 5


def test_summary_invokes_review_summary_with_events_dir_and_reviewer_args():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["summary"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Summarize shadow calibration results")
    run = run_step["run"]
    assert "review-summary" in run
    assert "shadow-events-all" in run
    assert '>> "$GITHUB_STEP_SUMMARY"' in run
    env = run_step["env"]
    assert env["REVIEW_SUMMARY_REPOSITORY_ID"] == "${{ github.repository_id }}"
    assert env["REVIEW_SUMMARY_HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"


def test_no_job_grants_pull_request_or_issue_write():
    # Belt-and-suspenders alongside the top-level permissions test: no job in this file
    # declares its own (more permissive) job-level `permissions:` override either.
    raw, _ = _load_workflow()
    for job in raw["jobs"].values():
        assert "permissions" not in job


# ── caller template ──────────────────────────────────────────────────────────

def test_caller_name_is_gate_shadow_not_gate():
    raw, _ = _load_caller()
    assert raw["name"] == "gate-shadow"
    assert "gate" not in raw["jobs"]


def test_caller_declares_ready_for_review_and_converted_to_draft():
    _, trigger = _load_caller()
    types = trigger["pull_request"]["types"]
    assert "ready_for_review" in types
    assert "converted_to_draft" in types
    assert set(types) == {"opened", "synchronize", "reopened", "ready_for_review", "converted_to_draft"}


def test_caller_paths_ignore_mirrors_gate_v2_callers_convention():
    _, trigger = _load_caller()
    paths_ignore = trigger["pull_request"]["paths-ignore"]
    assert paths_ignore == ["**.md", "docs/**"]


def test_caller_job_uses_gate_shadow_v2_workflow_with_pinned_sha_placeholder():
    raw, _ = _load_caller()
    job = raw["jobs"]["gate-shadow"]
    assert job["uses"].startswith("zlxlabs/gate/.github/workflows/gate-shadow-v2.yml@")
    assert "__PINNED_GATE_SHA__" in job["uses"]


def test_caller_permissions_minimal_and_no_secrets_inherit():
    raw, _ = _load_caller()
    assert raw["permissions"] == {"actions": "read", "contents": "read"}
    code = "\n".join(ln for ln in CALLER_TEMPLATE.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert "inherit" not in code
    assert "secrets" not in raw["jobs"]["gate-shadow"]


# ── no GHA arithmetic operators anywhere (shared helper, covers both files) ────

def test_no_gha_expression_anywhere_uses_arithmetic_operators():
    offenders = find_arithmetic_gha_expression_offenders(WORKFLOW)
    assert not offenders, f"found arithmetic-looking operator(s) inside GHA expression(s): {offenders!r}"


def test_caller_template_also_has_no_gha_arithmetic_operators():
    offenders = find_arithmetic_gha_expression_offenders(CALLER_TEMPLATE)
    assert not offenders, f"found arithmetic-looking operator(s) inside GHA expression(s): {offenders!r}"
