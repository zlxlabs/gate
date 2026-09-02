"""Contract tests for Required Gate v2 (.github/workflows/gate-v2.yml,
templates/caller-gate-v2.yml, .github/actions/gate-aggregator/aggregate.py).

Scope: this is the D1 "Required Gate" half of the shadow-review-independence
rollout (see the private gate-hub repo's
ceo-plans/2026-07-24-shadow-review-independence.md). Legacy
.github/workflows/gate.yml and its own tests/test_gate_contract.py are
kept behaviorally aligned with this file.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from _gha_lint import (
    find_arithmetic_gha_expression_offenders,
    materialize_jobs_api_snippet_for_probe,
    probe_jobs_api_failure_exit_code,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
DISPOSITION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2-disposition.yml"
CALLER_TEMPLATE = REPO_ROOT / "templates" / "caller-gate-v2.yml"
DISPOSITION_CALLER_TEMPLATE = REPO_ROOT / "templates" / "caller-gate-disposition.yml"
DISPOSITION_CALLER_PIN = "__PINNED_GATE_SHA__"
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
DIAGNOSTICS_NAME_EXPR = (
    "primary-review-diagnostics-v2-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
    "-${{ github.run_id }}-${{ github.run_attempt }}"
)
DIAGNOSTICS_PATH = "${{ runner.temp }}/primary-review-diagnostics/"
PANEL_DELIVERY_NAME_EXPR = (
    "gate-status-panel-delivery-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
    "-${{ github.run_id }}-${{ github.run_attempt }}"
)
CONVERGENCE_RECEIPT_NAME_EXPR = (
    "gate-convergence-receipt-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
    "-${{ github.run_id }}-${{ github.run_attempt }}"
)
CONVERGENCE_RECEIPT_PATH = "${{ runner.temp }}/convergence-receipt"
QUALITY_ENTRY_PATH = "scripts/gate-quality"
QUALITY_ENTRY_MODE = "steps.quality-entry.outputs.mode"
CHECKOUT_ACTION = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
EXPECTED_ACTION_REFS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/cache": "0057852bfaa89a56745cba8c7296529d2fc39830",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
}
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


def _load_disposition_workflow():
    raw = yaml.safe_load(DISPOSITION_WORKFLOW.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _disposition_scope_python() -> str:
    """Return the inline python that constructs Scope from the disposition workflow."""
    raw, _ = _load_disposition_workflow()
    step = next(
        s
        for s in raw["jobs"]["control"]["steps"]
        if s.get("name") == "Construct canonical scope and derive epoch"
    )
    run = step["run"]
    start = run.index("<<'PY'\n") + len("<<'PY'\n")
    end = run.index("\nPY\n", start)
    return run[start:end]


def _load_disposition_caller():
    raw = yaml.safe_load(DISPOSITION_CALLER_TEMPLATE.read_text())
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


def test_disposition_workflow_is_protected_and_cannot_publish_gate_result():
    raw, trigger = _load_disposition_workflow()
    assert raw["name"] == "gate-v2 disposition control"
    assert "workflow_dispatch" in trigger
    assert "workflow_call" in trigger
    assert raw["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    control = raw["jobs"]["control"]
    assert control["environment"] == {"name": "gate-disposition"}
    expected_inputs = {
        "pr_number", "primary_run_id", "primary_run_attempt", "finding_id", "reason", "gate_ref",
    }
    assert "operation" not in trigger["workflow_dispatch"]["inputs"]
    assert "repository_id" not in trigger["workflow_dispatch"]["inputs"]
    assert "epoch" not in trigger["workflow_dispatch"]["inputs"]
    assert set(trigger["workflow_dispatch"]["inputs"]) == expected_inputs
    assert set(trigger["workflow_call"]["inputs"]) == expected_inputs
    for kind in ("workflow_dispatch", "workflow_call"):
        gate_ref = trigger[kind]["inputs"]["gate_ref"]
        assert gate_ref["required"] is True
        assert gate_ref["type"] == "string"
    text = DISPOSITION_WORKFLOW.read_text()
    assert "issue_receipt.py issue" in text
    assert "issue_receipt.py revoke" not in text
    assert "evidence" not in text.lower()
    assert "GITHUB_ACTOR" not in text
    assert "pull-requests: write" not in text
    assert "checks: write" not in text
    assert "statuses: write" not in text
    assert "gate/gate" not in text
    assert "check-runs" not in text
    upload = next(step for step in control["steps"] if step.get("name") == "Upload immutable disposition artifact")
    assert upload["uses"] == UPLOAD_ARTIFACT_ACTION
    assert upload["with"]["if-no-files-found"] == "error"
    resolve = next(step for step in control["steps"] if step.get("name") == "Resolve current PR head and canonical primary audit")
    assert resolve["env"]["GH_REPO"] == "${{ github.repository }}"
    assert 'audit_name="primary-audit-v2-${GITHUB_REPOSITORY_ID}-${head_sha}-${PRIMARY_RUN_ID}-${PRIMARY_RUN_ATTEMPT}"' in resolve["run"]
    assert 'gh run download -R "$GITHUB_REPOSITORY" "$PRIMARY_RUN_ID" --name "$audit_name"' in resolve["run"]
    issue = next(step for step in control["steps"] if step.get("name") == "Issue immutable disposition artifact")
    assert '--scope-json "$CURRENT_SCOPE_JSON"' in issue["run"]
    assert issue["env"]["DISPOSITION_APPROVER"] == "${{ github.triggering_actor }}"
    assert issue["env"]["DISPOSITION_APPROVER_ID"] == "${{ github.actor_id }}"
    assert "--approver \"$DISPOSITION_APPROVER\"" in issue["run"]
    assert "--approver-id \"$DISPOSITION_APPROVER_ID\"" in issue["run"]
    assert "--approved-at \"$approved_at\"" in issue["run"]
    assert "inputs.approver" not in text
    assert "${{ github.triggering_actor }}" in text
    assert "${{ github.actor_id }}" in text


def test_gate_disposition_receipt_names_include_epoch_and_audit_digest():
    text = DISPOSITION_WORKFLOW.read_text()
    producer = (REPO_ROOT / ".github" / "actions" / "gate-disposition" / "issue_receipt.py").read_text()
    consumer = (REPO_ROOT / ".github" / "actions" / "gate-aggregator" / "convergence.py").read_text()
    assert "disposition_receipt_artifact_name" in producer
    assert "DISPOSITION_APPROVER" in producer
    assert "steps.disposition.outputs.artifact_name" in text
    assert "CURRENT_AUDIT_DIGEST" in text
    assert "receipt.audit_digest[:12]" in consumer


def test_required_disposition_lines_is_the_only_g4_line_builder():
    hits = []
    for path in (REPO_ROOT / ".github").rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".yml"}:
            continue
        if "resolved by receipt" in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == [".github/actions/gate-aggregator/convergence.py"]


def test_disposition_inline_python_registers_sys_modules_before_dataclass_exec(tmp_path):
    """Lock issue #88: dataclass Scope crashes unless sys.modules is registered first."""
    source = _disposition_scope_python()
    register = "sys.modules[spec.name] = module"
    exec_call = "spec.loader.exec_module(module)"
    assert register in source
    assert exec_call in source
    assert source.index(register) < source.index(exec_call)
    assert "if spec is None or spec.loader is None" in source

    head_sha = "a" * 40
    base_sha = "b" * 40
    audit = {
        "diff_digest": "d" * 64,
        "policy_version": "1",
        "policy_digest": "p" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "r" * 40,
        "base_sha": base_sha,
        "head_sha": head_sha,
    }
    pull = {"base": {"sha": base_sha}, "head": {"sha": head_sha}}
    audit_path = tmp_path / "audit.json"
    pr_path = tmp_path / "pr.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    pr_path.write_text(json.dumps(pull), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-", str(audit_path), str(pr_path), "123", "42"],
        input=source,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["scope"]["repository_id"] == 123
    assert payload["scope"]["pr_number"] == 42
    assert payload["scope"]["head_sha"] == head_sha
    assert isinstance(payload["epoch"], str) and len(payload["epoch"]) == 64


def test_disposition_sparse_checkout_lists_files_and_disables_cone_mode():
    """Lock issue #88: file-path sparse-checkout requires cone-mode off."""
    raw, _ = _load_disposition_workflow()
    checkout = next(
        step
        for step in raw["jobs"]["control"]["steps"]
        if step.get("name") == "Checkout disposition producer"
    )
    sparse = checkout["with"]["sparse-checkout"]
    listed = {line.strip() for line in sparse.splitlines() if line.strip()}
    assert listed == {
        ".github/actions/gate-disposition/issue_receipt.py",
        ".github/actions/gate-aggregator/convergence.py",
    }
    assert checkout["with"].get("sparse-checkout-cone-mode") is False


def test_disposition_checkout_pins_zlxlabs_gate_at_gate_ref():
    raw, _ = _load_disposition_workflow()
    checkout = next(
        step
        for step in raw["jobs"]["control"]["steps"]
        if step.get("name") == "Checkout disposition producer"
    )
    assert checkout["with"]["repository"] == "zlxlabs/gate"
    assert checkout["with"]["ref"] == "${{ inputs.gate_ref }}"


def test_disposition_requires_lowercase_40_hex_gate_ref_before_checkout():
    raw, _ = _load_disposition_workflow()
    steps = raw["jobs"]["control"]["steps"]
    names = [step.get("name") for step in steps]
    validate_name = "Require 40-hex gate_ref"
    checkout_name = "Checkout disposition producer"
    assert validate_name in names
    assert names.index(validate_name) < names.index(checkout_name)
    validate = next(step for step in steps if step.get("name") == validate_name)
    assert validate["env"]["GATE_REF"] == "${{ inputs.gate_ref }}"
    run = validate["run"]
    assert "^[0-9a-f]{40}$" in run
    assert '[[ ! "$GATE_REF" =~ ^[0-9a-f]{40}$ ]]' in run
    assert "::error::" in run
    assert "exit 1" in run
    assert "rev-parse" not in run
    assert "git " not in run


def test_production_v2_official_actions_are_exactly_sha_pinned():
    raw, _ = _load_workflow()
    actual = {}
    for job in raw["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if uses.startswith("actions/"):
                action, ref = uses.split("@", 1)
                actual.setdefault(action, set()).add(ref)
    assert actual == {action: {ref} for action, ref in EXPECTED_ACTION_REFS.items()}


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
    assert 'gate-v2-ocr-advisory:${REVIEWER}:v1' in comment_step["run"]
    assert 'issues/$PR_NUMBER/comments?per_page=100' in comment_step["run"]
    assert "gh_api user" in comment_step["run"]
    assert "timeout --foreground" in comment_step["run"]
    assert comment_step["env"]["OCR_GITHUB_TIMEOUT_SECONDS"] == 15
    assert comment_step["env"]["OCR_PUBLISH_BUDGET_SECONDS"] == 120
    assert 'case "$http_status" in' in comment_step["run"]
    assert "403|404)" in comment_step["run"]
    assert "workflow_id=41898282" in comment_step["run"]
    assert "workflow_login='github-actions[bot]'" in comment_step["run"]
    assert 'identity_source="actions_bot_fallback"' in comment_step["run"]
    assert "identity_source: $identity_source" in comment_step["run"]
    assert "((.user.id // null) != null)" in comment_step["run"]
    assert "((.user.id // null) == null)" in comment_step["run"]
    assert "--paginate --slurp" in comment_step["run"]
    assert "owner_id" in comment_step["run"] and "WORKFLOW_LOGIN" in comment_step["run"]
    assert '--method PATCH' in comment_step["run"]
    assert '--method POST' in comment_step["run"]
    assert '--method DELETE' in comment_step["run"]
    assert "post verification" in comment_step["run"]
    assert 'advisory-delivery-${REVIEWER}.json' in comment_step["run"]

    upload_step = next(s for s in ocr["steps"] if s.get("name") == "Upload advisory review event")
    assert upload_step["with"]["path"] == "${{ runner.temp }}/shadow-events/advisory"


def test_ocr_resolve_job_id_uses_jq_arg_not_env_builtin():
    # Same matrix-leg naming pattern as gate-shadow-v2.yml's shadow job — must pass
    # JOB_NAME_SUFFIX via jq --arg and fail-loud with categorized diagnostics.
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"]["ocr"]["steps"]
        if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID"
    )
    assert step["env"]["REVIEWER"] == "${{ matrix.reviewer }}"
    run = step["run"]
    assert 'JOB_NAME_SUFFIX="${{ github.job }} ($REVIEWER)"' in run
    assert "jq -r --arg suffix" in run
    assert "env.JOB_NAME_SUFFIX" not in run
    assert "matching name is empty because REVIEWER is unset" in run
    assert "Jobs API call failed" in run
    assert "(exit=${rc})" in run
    assert "|| rc=$?" in run
    assert "if ! api_json=" not in run
    assert "err_preview" not in run
    assert "api_err" not in run
    assert "no matching job for JOB_NAME_SUFFIX=" in run
    assert "timeout --foreground" in run
    assert "${{ matrix.reviewer }}" not in run
    assert raw["jobs"]["gate"]["needs"] == ["quality", "primary"]


def test_ocr_resolve_jobs_api_failure_probe_reports_exit_42(tmp_path):
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"]["ocr"]["steps"]
        if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID"
    )
    gh_api_wrapper = (
        'gh_api() {\n'
        '  timeout --foreground "${OCR_GITHUB_TIMEOUT_SECONDS}s" gh api "$@"\n'
        '}'
    )
    snippet = gh_api_wrapper + "\n" + materialize_jobs_api_snippet_for_probe(
        step["run"],
        start_prefix="jobs_api=",
        end_prefix='if [ -z "$api_json" ]',
    )
    proc = probe_jobs_api_failure_exit_code(snippet, tmp_path, with_timeout_stub=True)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "exit=42" in output, output


# ── concurrency contract ─────────────────────────────────────────────────────
# Two job-level locks, no workflow-level group: quality/primary cancel stale
# work per PR; gate/ledger keep cancel-in-progress: false so writers finish.

_WRITER_CONCURRENCY = {
    "gate": {
        "group": "gate-required-v2-panel-${{ github.repository_id }}-${{ github.event.pull_request.number }}",
        "cancel-in-progress": False,
    },
    "ledger": {
        "group": "gate-required-v2-ledger-${{ github.repository_id }}",
        "cancel-in-progress": False,
    },
}


def test_required_v2_has_no_workflow_level_concurrency():
    raw, _ = _load_workflow()
    assert "concurrency" not in raw


def test_gate_and_ledger_writer_locks_remain_cancel_false():
    raw, _ = _load_workflow()
    # Byte-exact contract with base: the full mapping (complete group literal
    # + cancel-in-progress) must equal these constants, not just pass per-field
    # shape probes.
    for job_name, expected in _WRITER_CONCURRENCY.items():
        assert raw["jobs"][job_name].get("concurrency") == expected


def _assert_expensive_job_cancel_lock(job_name: str, concurrency: dict) -> None:
    # Full-expression equality, not substring shape probes: the fallback order
    # (PR number first, run_id last) is part of the contract.
    assert concurrency == {
        "group": (
            f"gate-required-v2-{job_name}-${{{{ github.repository_id }}}}"
            f"-${{{{ github.event.pull_request.number || github.run_id }}}}"
        ),
        "cancel-in-progress": True,
    }


def test_quality_and_primary_have_independent_cancel_true_pr_locks():
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"].get("concurrency", {})
    primary = raw["jobs"]["primary"].get("concurrency", {})
    _assert_expensive_job_cancel_lock("quality", quality)
    _assert_expensive_job_cancel_lock("primary", primary)


# ── quality short-circuit on primary failure (gate#105 方案 A, owner 2026-09-02) ──
# quality needs primary so a primary failure skips quality entirely and lets
# `gate / gate` conclude without waiting on the slowest job. The `always()` in
# the `if:` is mandatory, not decorative: with GitHub's default success()
# gate, a primary that is cancelled (concurrency supersede on a newer head)
# or skipped (draft PR / fork / hosted runner) would ALSO skip quality —
# quality must still run in those cases; only primary failure short-circuits.


def test_quality_needs_primary_and_short_circuits_only_on_primary_failure():
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    assert quality["needs"] == ["primary"]
    # Locked as a full literal: `!= 'failure'` means primary skipped (draft /
    # fork / hosted) or cancelled (concurrency supersede) still runs quality.
    assert quality["if"] == "always() && needs.primary.result != 'failure'"


def test_non_writer_non_expensive_jobs_have_no_concurrency():
    raw, _ = _load_workflow()
    for job_name, job in raw["jobs"].items():
        if job_name not in {"ledger", "gate", "quality", "primary"}:
            assert "concurrency" not in job


def test_all_workflow_concurrency_mappings_use_only_github_supported_keys():
    allowed_keys = {"group", "cancel-in-progress"}
    workflow_paths = sorted(REPO_ROOT.joinpath(".github", "workflows").glob("*.yml"))
    workflow_paths += sorted(REPO_ROOT.joinpath(".github", "workflows").glob("*.yaml"))
    assert workflow_paths
    for workflow_path in workflow_paths:
        raw = yaml.safe_load(workflow_path.read_text())
        mappings = []
        if isinstance(raw, dict) and "concurrency" in raw:
            mappings.append(("workflow", raw["concurrency"]))
        jobs = raw.get("jobs", {}) if isinstance(raw, dict) else {}
        for job_name, job in jobs.items():
            if isinstance(job, dict) and "concurrency" in job:
                mappings.append((f"job {job_name}", job["concurrency"]))
        for location, concurrency in mappings:
            assert isinstance(concurrency, dict), f"{workflow_path}:{location} concurrency must be a mapping"
            unexpected = set(concurrency) - allowed_keys
            assert not unexpected, f"{workflow_path}:{location} has unsupported concurrency keys: {sorted(unexpected)}"


def test_gate_status_panel_publish_happens_after_terminal_upload():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["gate"]["steps"]
    names = [step.get("name") for step in steps]
    upload_index = names.index("Upload gate terminal envelope")
    publish_index = names.index("Publish gate status panel")
    assert upload_index < publish_index
    publish = steps[publish_index]
    assert "steps.upload-gate-terminal.outcome == 'success'" in str(publish.get("if"))
    assert "--publish-only" in publish["run"]


def test_gate_uploads_convergence_receipt_before_terminal_and_panel_publication():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["gate"]["steps"]
    names = [step.get("name") for step in steps]
    aggregate_index = names.index("Aggregate required verdict")
    receipt_index = names.index("Upload convergence receipt")
    terminal_index = names.index("Upload gate terminal envelope")
    panel_index = names.index("Publish gate status panel")
    assert aggregate_index < receipt_index < terminal_index < panel_index

    aggregate = steps[aggregate_index]
    assert aggregate["id"] == "aggregate-required-verdict"
    assert "--convergence-receipt-path \"$CONVERGENCE_RECEIPT_PATH\"" in aggregate["run"]
    assert 'echo "convergence-receipt=present" >> "$GITHUB_OUTPUT"' in aggregate["run"]
    assert aggregate["env"]["CONVERGENCE_RECEIPT_PATH"] == (
        "${{ runner.temp }}/convergence-receipt/convergence-receipt.json"
    )

    upload = steps[receipt_index]
    assert upload["if"] == "always() && steps.aggregate-required-verdict.outputs.convergence-receipt == 'present'"
    assert upload["uses"] == UPLOAD_ARTIFACT_ACTION
    assert "always()" in str(upload["if"])
    assert "continue-on-error" not in upload
    assert upload["with"] == {
        "name": CONVERGENCE_RECEIPT_NAME_EXPR,
        "path": CONVERGENCE_RECEIPT_PATH,
        "if-no-files-found": "error",
        "retention-days": 30,
    }


def test_gate_aggregate_writes_receipt_output_and_transparently_exits_with_aggregate_rc():
    raw, _ = _load_workflow()
    aggregate = next(
        step for step in raw["jobs"]["gate"]["steps"]
        if step.get("name") == "Aggregate required verdict"
    )
    run = aggregate["run"]
    python_index = run.index("python3 _gate-aggregator-src/.github/actions/gate-aggregator/aggregate.py")
    set_plus_index = run.index("set +e")
    rc_index = run.index("rc=$?", python_index)
    set_minus_index = run.index("set -e", rc_index)
    condition = 'if [ -f "$CONVERGENCE_RECEIPT_PATH" ]; then'
    condition_index = run.index(condition, set_minus_index)
    output_index = run.index('echo "convergence-receipt=present" >> "$GITHUB_OUTPUT"')
    else_index = run.index("else", condition_index)
    absent_index = run.index('echo "convergence-receipt=absent" >> "$GITHUB_OUTPUT"', else_index)
    fi_index = run.index("fi", absent_index)
    exit_index = run.index('exit "$rc"')
    assert set_plus_index < python_index < rc_index < set_minus_index < condition_index
    assert condition_index < output_index < else_index < absent_index < fi_index < exit_index
    assert "|| true" not in run
    assert "continue-on-error" not in aggregate

    upload = next(
        step for step in raw["jobs"]["gate"]["steps"]
        if step.get("name") == "Upload convergence receipt"
    )
    assert upload["if"] == "always() && steps.aggregate-required-verdict.outputs.convergence-receipt == 'present'"
    assert "continue-on-error" not in upload


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
    assert upload["uses"] == UPLOAD_ARTIFACT_ACTION
    assert upload["with"]["name"] == ARTIFACT_NAME_EXPR
    # fail-closed: unlike legacy's advisory codex-audit upload, this upload has no
    # continue-on-error. P1 fix (2026-07-26, canary probe #2): if-no-files-found MUST be
    # explicit `error` — actions/upload-artifact@v4's own default is `warn` (a step
    # annotation, not a failure), which is what let canary's primary job conclude
    # `success` despite writing no audit at all; relying on "we didn't set `ignore`"
    # alone was never sufficient fail-closed enforcement.
    assert "continue-on-error" not in upload
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "${{ runner.temp }}/primary-review-audit.json"
    assert upload["with"]["retention-days"] == 30


def test_primary_uploads_review_diagnostics_after_canonical_audit():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["primary"]["steps"]
    audit_index = next(i for i, step in enumerate(steps) if step.get("name") == "Upload canonical primary audit")
    diagnostics_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Upload primary review diagnostics"
    )
    assert diagnostics_index == audit_index + 1
    assert steps[diagnostics_index] == {
        "name": "Upload primary review diagnostics",
        "if": "always()",
        "uses": UPLOAD_ARTIFACT_ACTION,
        "with": {
            "name": DIAGNOSTICS_NAME_EXPR,
            "path": DIAGNOSTICS_PATH,
            "if-no-files-found": "ignore",
            "retention-days": 30,
        },
    }

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
    assert terminal_upload["if"] == "always()" and terminal_upload["uses"] == UPLOAD_ARTIFACT_ACTION and terminal_upload["with"] == {"name": "gate-terminal-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}-${{ github.run_id }}-${{ github.run_attempt }}", "path": "${{ runner.temp }}/gate-terminal.json", "if-no-files-found": "error"} and "continue-on-error" not in terminal_upload


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
    retry_upload = next(
        step for step in quality_steps if step.get("name") == "Retry upload v2 review ledger inputs"
    )
    assert input_upload["if"] == "always()"
    assert input_upload["continue-on-error"] is True
    assert retry_upload["continue-on-error"] is True
    assert retry_upload["if"] == "always() && steps.ledger-input-upload.outcome == 'failure'"
    assert retry_upload["with"]["name"] == input_upload["with"]["name"]
    assert retry_upload["with"]["path"] == input_upload["with"]["path"]
    assert retry_upload["uses"] == input_upload["uses"]
    assert retry_upload["with"]["overwrite"] is True
    assert input_download["with"]["artifact-ids"] == "${{ steps.resolve-ledger-artifacts.outputs.input_artifact_id }}"
    assert "pr-size-preflight.json" in input_upload["with"]["path"]
    assert "install-result.json" in input_upload["with"]["path"]
    assert input_download["with"]["path"] == "${{ runner.temp }}/review-ledger-input"
    terminal_download = next(step for step in steps if step.get("name") == "Download gate terminal envelope for ledger")
    assert terminal_download["with"]["artifact-ids"] == "${{ steps.resolve-ledger-artifacts.outputs.terminal_artifact_id }}"
    assert terminal_download["with"]["path"] == "${{ runner.temp }}/gate-terminal"
    assert "continue-on-error" not in terminal_download
    build = steps[build_index]
    assert build["uses"] == "./_gate-aggregator-src/.github/actions/review-ledger"
    assert build["with"]["audit-path"] == "${{ runner.temp }}/primary-audit/primary-review-audit.json"
    assert build["with"]["codex-expected"] == raw["jobs"]["primary"]["if"]
    assert build["with"]["codex-waived"] is False
    assert build["with"]["expected-repository-id"] == "${{ github.repository_id }}"
    assert build["with"]["expected-base-sha"] == "${{ github.event.pull_request.base.sha }}"
    assert build["with"]["expected-caller-sha"] == "${{ github.workflow_sha }}"
    assert build["with"]["expected-reusable-workflow-sha"] == "${{ job.workflow_sha }}"
    assert build["with"]["terminal-path"] == "${{ runner.temp }}/gate-terminal/gate-terminal.json"

    upload = steps[upload_index]
    assert upload["uses"] == UPLOAD_ARTIFACT_ACTION
    assert upload["with"] == {
        "name": "codex-review-ledger-v2",
        "path": "${{ runner.temp }}/review-ledger/ledger.jsonl",
        "if-no-files-found": "error",
        "retention-days": 90,
    }


def test_quality_exposes_ledger_input_upload_outcome_to_ledger():
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    assert quality["outputs"]["ledger_input_upload"] == (
        "${{ steps.ledger-input-upload-outcome.outputs.ledger_input_upload }}"
    )
    resolver = next(
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Resolve v2 ledger artifacts"
    )
    assert resolver["env"]["QUALITY_LEDGER_INPUT_UPLOAD"] == (
        "${{ needs.quality.outputs.ledger_input_upload }}"
    )


def test_only_ocr_job_has_continue_on_error():
    raw, _ = _load_workflow()
    jobs = raw["jobs"]
    assert {job for job, spec in jobs.items() if spec.get("continue-on-error") is True} == {"ocr"}


def test_ledger_resolver_is_strict_about_current_run_artifact_attempts():
    raw, _ = _load_workflow()
    resolver = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Resolve v2 ledger artifacts")
    assert resolver["if"] == "always()"
    assert resolver["env"]["CURRENT_ATTEMPT"] == "${{ github.run_attempt }}"
    assert resolver["env"]["REVIEW_EXPECTED"] == (
        "${{ github.event.pull_request.draft != true && github.event.pull_request.head.repo.full_name == github.repository && inputs.runner == 'self' }}"
    )
    run = resolver["run"]
    for marker in (
        "--paginate", "expired", "<= current",
        "input_artifact_id", "audit_artifact_id", "terminal_artifact_id",
        "terminal_source_attempt",
    ):
        assert marker in run
    assert "exact_attempt" not in run
    assert resolver["env"]["TERMINAL_PREFIX"] == (
        "gate-terminal-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}-${{ github.run_id }}-"
    )
    assert resolver["env"]["REPOSITORY"] == "${{ github.repository }}"
    assert resolver["env"]["RUN_ID"] == "${{ github.run_id }}"
    assert "No matching required ledger input artifact found" in run
    assert "No matching canonical primary audit artifact found" in run
    assert "No matching required gate terminal artifact found" in run
    assert "Aggregator ran on this attempt but did not produce a terminal artifact" in run
    assert "Cannot attribute missing current-attempt terminal: run_started_at is missing or unparseable" in run
    assert "/attempts/" in run and "/jobs" in run
    assert "run_started_at" in run


def _ledger_resolver_python() -> str:
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Resolve v2 ledger artifacts"
    )
    run = step["run"]
    start = run.index("<<'PY'\n") + len("<<'PY'\n")
    end = run.index("\nPY\n", start)
    return run[start:end]


def _run_ledger_resolver(
    tmp_path, *, artifacts, current, review_expected="false",
    jobs=None, jobs_path=None, attempt=None, attempt_path=None,
    extra_env=None,
):
    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    argv = [
        sys.executable, "-",
        str(listing),
        "review-ledger-input-v2-",
        "primary-audit-v2-",
        "gate-terminal-v1-",
        str(current),
        review_expected,
        str(output),
    ]
    extra = jobs is not None or jobs_path is not None or attempt is not None or attempt_path is not None
    if extra:
        if jobs_path is not None:
            argv.append(str(jobs_path))
        elif jobs is not None:
            jobs_file = tmp_path / "jobs.json"
            jobs_file.write_text(json.dumps(jobs), encoding="utf-8")
            argv.append(str(jobs_file))
        else:
            argv.append("")
        if attempt_path is not None:
            argv.append(str(attempt_path))
        elif attempt is not None:
            attempt_file = tmp_path / "attempt.json"
            attempt_file.write_text(json.dumps(attempt), encoding="utf-8")
            argv.append(str(attempt_file))
    env = os.environ.copy()
    for key in (
        "REPOSITORY", "RUN_ID", "GITHUB_REPOSITORY", "GITHUB_RUN_ID",
        "QUALITY_LEDGER_INPUT_UPLOAD", "QUALITY_RESULT", "PRIMARY_RESULT",
    ):
        env.pop(key, None)
    # The workflow always sets these via needs.*.result; default to the
    # non-short-circuit happy path, overridable per test via extra_env.
    env["QUALITY_RESULT"] = "success"
    env["PRIMARY_RESULT"] = "success"
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        argv,
        input=_ledger_resolver_python(),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, output.read_text(encoding="utf-8")


ISSUE_101_RUN_STARTED_AT = "2026-09-01T02:57:51Z"


def _jobs(*names, started_at=None):
    jobs = []
    for index, name in enumerate(names, start=1):
        job = {"name": name, "id": index}
        if started_at is not None:
            job["started_at"] = started_at
        jobs.append(job)
    return {"jobs": jobs}


def _issue_101_attempt2_jobs():
    return {
        "jobs": [
            {"name": "gate / gate", "id": 99721378127, "run_attempt": 2, "started_at": "2026-09-01T02:49:00Z", "conclusion": "success"},
            {"name": "gate / quality", "id": 99721377855, "run_attempt": 2, "started_at": "2026-09-01T02:30:19Z", "conclusion": "success"},
            {"name": "gate / primary", "id": 99721378200, "run_attempt": 2, "started_at": "2026-09-01T02:30:19Z", "conclusion": "success"},
            {"name": "gate / ledger", "id": 99721378122, "run_attempt": 2, "started_at": "2026-09-01T02:57:57Z", "conclusion": "failure"},
            {"name": "gate / ocr (ocr-minimax-m3)", "id": 99721377502, "run_attempt": 2, "started_at": "2026-09-01T02:57:57Z", "conclusion": "success"},
            {"name": "gate / notify", "id": 99721378564, "run_attempt": 2, "started_at": "2026-09-01T02:57:54Z", "conclusion": "skipped"},
        ]
    }


def test_ledger_resolver_refuses_stale_terminal_when_current_attempt_is_missing(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-1", "expired": False, "id": 101},
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
    ]
    result, _output = _run_ledger_resolver(tmp_path, artifacts=artifacts, current=2)
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
    assert "terminal_artifact_id=201" in output
    assert "terminal_source_attempt=1" in output


def test_ledger_resolver_selects_current_attempt_terminal_not_an_older_one(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
        {"name": "gate-terminal-v1-2", "expired": False, "id": 202},
    ]
    result, output = _run_ledger_resolver(tmp_path, artifacts=artifacts, current=2)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "terminal_artifact_id=202" in output
    assert "terminal_source_attempt=2" in output


def test_ledger_resolver_refuses_future_terminal_artifact(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-3", "expired": False, "id": 203},
    ]
    result, _output = _run_ledger_resolver(tmp_path, artifacts=artifacts, current=2)
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "No matching required gate terminal artifact found" in combined
    assert "terminal_artifact_id=203" not in _output


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
    assert "terminal_artifact_id=201" in output
    assert "terminal_source_attempt=1" in output
    assert "terminal_artifact_id=203" not in output


@pytest.mark.parametrize("gate_job_name", ["gate", "gate / gate"])
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
    assert "terminal_artifact_id=202" in output
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


def _prior_terminal_artifacts():
    return [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]


def test_ledger_resolver_falls_back_when_copied_aggregator_job_predates_attempt(tmp_path):
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=_prior_terminal_artifacts(), current=2,
        jobs=_issue_101_attempt2_jobs(),
        attempt={"run_started_at": ISSUE_101_RUN_STARTED_AT},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "terminal_artifact_id=201" in output
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
    ({}, "quality upload outcome: unknown"),
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


# ── gate#105 A (PR #119 r1): short-circuited quality has no input BY DESIGN ──
# With quality needs primary + `if: != 'failure'`, a primary failure skips
# quality, so the review-ledger-input artifact legitimately does not exist and
# needs.quality.outputs.ledger_input_upload is empty. The ledger job (if:
# always()) must still write its row — the resolver makes the input optional
# ONLY for this exact combination; every other missing-input case stays fatal.


def test_ledger_resolver_short_circuited_quality_makes_input_optional(tmp_path):
    artifacts = [
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, output = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1,
        extra_env={"QUALITY_RESULT": "skipped", "PRIMARY_RESULT": "failure"},
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
        extra_env={"QUALITY_RESULT": "skipped", "PRIMARY_RESULT": "success"},
    )
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "quality upload outcome: unknown" in combined
    assert "No matching required ledger input artifact found" in combined


def test_ledger_resolver_step_env_and_download_guard_literals():
    raw, _ = _load_workflow()
    ledger_steps = raw["jobs"]["ledger"]["steps"]
    resolve = next(s for s in ledger_steps if s.get("name") == "Resolve v2 ledger artifacts")
    assert resolve["env"]["QUALITY_RESULT"] == "${{ needs.quality.result }}"
    assert resolve["env"]["PRIMARY_RESULT"] == "${{ needs.primary.result }}"
    download = next(s for s in ledger_steps if s.get("name") == "Download v2 review ledger inputs")
    assert download["if"] == "steps.resolve-ledger-artifacts.outputs.input_artifact_id != ''"


def test_ledger_build_step_forwards_input_short_circuited():
    """W5: Build 步 with: 必须把解析器的 input_short_circuited 原样传进 action。"""
    raw, _ = _load_workflow()
    build = next(
        s for s in raw["jobs"]["ledger"]["steps"]
        if s.get("name") == "Build v2 review effectiveness ledger"
    )
    assert "input-short-circuited" in build.get("with", {})
    assert build["with"]["input-short-circuited"] == (
        "${{ steps.resolve-ledger-artifacts.outputs.input_short_circuited }}"
    )


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


def test_ledger_persistence_steps_are_fail_closed():
    raw, _ = _load_workflow()
    ledger_steps = raw["jobs"]["ledger"]["steps"]
    for name in (
        "Download v2 review ledger inputs",
        "Download canonical primary audit for ledger",
        "Download gate terminal envelope for ledger",
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
    assert upload["uses"] == UPLOAD_ARTIFACT_ACTION
    assert upload["with"] == {
        "name": PANEL_DELIVERY_NAME_EXPR,
        "path": "${{ runner.temp }}/gate-status-panel-delivery.json",
        "if-no-files-found": "error",
        "retention-days": 30,
    }
    assert "continue-on-error" not in upload
    assert upload["with"]["path"] == publish_step["env"]["PANEL_DELIVERY_PATH"]


def test_gate_job_timeout_matches_aggregate_publish_budget():
    raw, _ = _load_workflow()
    assert raw["jobs"]["gate"]["timeout-minutes"] == 8
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert "aggregation is seconds, publish is capped at <=2 minutes" in workflow_text


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
    assert checkout["uses"] == CHECKOUT_ACTION
    assert checkout["with"] == {
        "repository": "${{ job.workflow_repository }}",
        "ref": "${{ job.workflow_sha }}",
        "path": "_gate-action-src",
        "sparse-checkout": ".github/actions\nscripts/scrub_outbound.py\n",
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
    assert cleanup_index == preflight_index + 1 and names[cleanup_index + 1] == "Run scripts/gate-quality"
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
    assert "scripts/scrub_outbound.py" in sparse_paths
    assert not any(path == "scripts" or path.startswith("scripts/") and path != "scripts/scrub_outbound.py" for path in sparse_paths)
    assert not any(path == "tests" or path.startswith("tests/") for path in sparse_paths)


def test_every_scrub_import_has_checkout_coverage_for_action_and_module():
    raw, _ = _load_workflow()
    for job_name, job in raw["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if not uses.startswith("./_") or "/.github/actions/" not in uses:
                continue
            action_root, action_relative = uses[2:].split("/.github/actions/", 1)
            action_dir = REPO_ROOT / ".github" / "actions" / action_relative
            import_files = [
                path for path in action_dir.rglob("*.py")
                if "from scripts.scrub_outbound import" in path.read_text()
            ]
            if not import_files:
                continue

            checkouts = [
                candidate for candidate in job["steps"]
                if candidate.get("uses", "").startswith(CHECKOUT_ACTION)
                and candidate.get("with", {}).get("path") == action_root
            ]
            assert checkouts, f"{job_name}: expected at least one checkout for {action_root}"
            for checkout in checkouts:
                sparse = checkout["with"].get("sparse-checkout")
                sparse_paths = None if sparse is None else (
                    sparse.splitlines() if isinstance(sparse, str) else sparse
                )

                def covered(path):
                    return sparse_paths is None or any(
                        path == prefix or path.startswith(f"{prefix.rstrip('/')}/")
                        for prefix in sparse_paths
                    )

                for import_file in import_files:
                    relative_import = import_file.relative_to(REPO_ROOT).as_posix()
                    assert covered(relative_import), (
                        f"{job_name}: checkout for {uses} misses {relative_import}"
                    )
                    assert covered("scripts/scrub_outbound.py"), (
                        f"{job_name}: checkout for {uses} misses scripts/scrub_outbound.py"
                    )


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


def test_diff_coverage_advisory_runs_after_caller_tests_with_continue_on_error():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["quality"]["steps"]
    names = [step.get("name") for step in steps]
    tests_index = names.index("Tests")
    checkout_index = names.index("Checkout gate actions for diff-coverage advisory")
    advisory_index = names.index("Diff coverage advisory")
    prune_index = names.index("Prune uv cache before persistence")

    assert tests_index < checkout_index < advisory_index < prune_index

    checkout = steps[checkout_index]
    assert checkout["if"] == "always()"
    assert checkout["continue-on-error"] is True
    assert checkout["timeout-minutes"] == 5
    assert checkout["uses"] == CHECKOUT_ACTION
    assert checkout["with"] == {
        "repository": "${{ job.workflow_repository }}",
        "ref": "${{ job.workflow_sha }}",
        "path": "_gate-action-src",
        "sparse-checkout": ".github/actions\nscripts/scrub_outbound.py\n",
    }

    advisory = steps[advisory_index]
    assert advisory["if"] == "always()"
    assert advisory["continue-on-error"] is True
    assert advisory["timeout-minutes"] == 10
    assert advisory["uses"] == "./_gate-action-src/.github/actions/diff-coverage-advisory"
    assert advisory["with"] == {
        "base-sha": "${{ github.event.pull_request.base.sha }}",
        "head-sha": "${{ github.event.pull_request.head.sha }}",
        "token": "${{ github.token }}",
    }


def test_diff_coverage_advisory_never_gates_quality_job():
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    advisory = next(
        step for step in quality["steps"] if step.get("name") == "Diff coverage advisory"
    )
    assert advisory["continue-on-error"] is True
    assert "exit 1" not in advisory.get("run", "")


def test_disposition_caller_forwards_business_inputs_and_pins_gate_ref():
    raw, trigger = _load_disposition_caller()
    assert raw["name"] == "gate-disposition"
    assert set(trigger["workflow_dispatch"]["inputs"]) == {
        "pr_number", "primary_run_id", "primary_run_attempt", "finding_id", "reason",
    }
    assert "workflow_call" not in trigger
    # reusable-workflow token is caller ∩ callee; upload-artifact needs write, gh api pulls needs pull-requests: read.
    expected_permissions = {
        "actions": "write",
        "contents": "read",
        "pull-requests": "read",
    }
    assert raw["permissions"] == expected_permissions
    assert "concurrency" not in raw
    assert set(raw["jobs"]) == {"disposition"}
    job = raw["jobs"]["disposition"]
    assert job["permissions"] == expected_permissions
    assert job.get("secrets") == "inherit"
    assert "environment" not in job
    uses = job["uses"]
    assert uses == (
        "zlxlabs/gate/.github/workflows/gate-v2-disposition.yml@" + DISPOSITION_CALLER_PIN
    )
    pin = uses.rsplit("@", 1)[1]
    assert job["with"]["gate_ref"] == pin
    for key in ("pr_number", "primary_run_id", "primary_run_attempt", "finding_id", "reason"):
        assert job["with"][key] == "${{ inputs." + key + " }}"
    assert set(job["with"]) == {
        "pr_number", "primary_run_id", "primary_run_attempt", "finding_id", "reason", "gate_ref",
    }
    text = DISPOSITION_CALLER_TEMPLATE.read_text()
    non_comment_text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "pull-requests: write" not in text
    assert "secrets." not in non_comment_text
    assert "environment:" not in text

