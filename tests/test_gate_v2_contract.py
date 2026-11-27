"""Contract tests for Required Gate v2 (.github/workflows/gate-v2.yml,
templates/caller-gate-v2.yml, .github/actions/gate-aggregator/aggregate.py).

Scope: this is the D1 "Required Gate" half of the shadow-review-independence
rollout (see the private gate-hub repo's
ceo-plans/2026-07-24-shadow-review-independence.md). These tests cover the
supported v2 workflow.
"""
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from scripts import silo_store as silo_store
from scripts import gate_bounded_retry as retry
from _gha_lint import find_arithmetic_gha_expression_offenders
from pr_size_preflight_compare_support import run_preflight_with_compare_stub
from test_pr_size_preflight import _repo

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
DISPOSITION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2-disposition.yml"
CALLER_TEMPLATE = REPO_ROOT / "templates" / "caller-gate-v2.yml"
DISPOSITION_CALLER_TEMPLATE = REPO_ROOT / "templates" / "caller-gate-disposition.yml"
DISPOSITION_CALLER_PIN = "__PINNED_GATE_SHA__"
# gate#278: the disposition entry point materializes its tools below the
# workspace, exactly like every gate-v2 workflow_sha checkout site.
DISPOSITION_CHECKOUT_SUBDIR = "_gate-disposition-src"
DISPOSITION_PRODUCER_SPARSE = [
    ".github/actions/gate-disposition/issue_receipt.py",
    ".github/actions/gate-aggregator/convergence.py",
    ".github/actions/gate-aggregator/convergence_contract.py",
    ".github/actions/gate-aggregator/disposition_receipts.py",
    ".github/actions/gate-aggregator/dispositions.py",
    ".github/actions/gate-aggregator/gate_action.py",
    ".github/actions/gate-aggregator/counterevidence_argv.py",
    ".github/actions/gate-aggregator/behavior_counterevidence.py",
    "scripts/silo_store.py",
    "scripts/silo_exec.sh",
]
AGGREGATOR_SCRIPT = REPO_ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
ABANDONED_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "primary-abandoned-run-34740209146.json"

FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
DRAFT_GUARD = "github.event.pull_request.draft != true"
RUNNER_GUARD = "inputs.runner == 'self'"
CONTROL_RUNNER_GUARD = "inputs.control_runner != 'github-hosted'"
CLASSIFY_GUARD = "needs.classify_pr_paths.outputs.review_expected != 'false'"
CLASSIFY_JOB_ID = "classify_pr_paths"
CLASSIFY_SCRIPT = "_gate-classify-src/scripts/classify_pr_reviewable_paths.py"
PR_AUTHOR_JSON = "toJSON(github.event.pull_request.user.login)"
PR_AUTHOR_HUMAN_GUARD = (
    f"startsWith({PR_AUTHOR_JSON}, '\"') && endsWith({PR_AUTHOR_JSON}, '\"') && "
    f"{PR_AUTHOR_JSON} != '\"\"' && {PR_AUTHOR_JSON} != '\"dependabot[bot]\"'"
)
REVIEW_EXPECTED_IF = (
    "${{ github.event.pull_request.draft != true && "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "inputs.runner == 'self' && "
    "needs.classify_pr_paths.outputs.review_expected != 'false' && "
    f"{PR_AUTHOR_HUMAN_GUARD} }}}}"
)
PRIMARY_RESULT_EXPR = (
    "${{ needs.primary.result == 'success' && 'success' "
    "|| needs.primary.result == 'failure' && 'failure' "
    "|| needs.primary.result == 'cancelled' && 'cancelled' "
    "|| needs.primary.result == 'skipped' && 'skipped' "
    "|| 'cancelled' }}"
)
PRIMARY_RESULT_RAW_EXPR = "${{ needs.primary.result }}"
PRIMARY_RESULT_DOMAIN = ("success", "failure", "cancelled", "skipped")
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
GATE_AGGREGATOR_JOB_NAMES = ("gate", "gate / gate", "gate / gate (draft)")
GATE_AGGREGATOR_NAME_EXPRESSION = "${{ format('gate{0}', github.event.pull_request.draft && ' (draft)' || '') }}"
QUALITY_ENTRY_PATH = "scripts/gate-quality"
QUALITY_ENTRY_MODE = "steps.quality-entry.outputs.mode"
QUALITY_RESULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gate-quality-result-v1-failed.json"
CHECKOUT_ACTION = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
BOUNDED_RETRY_HELPER = REPO_ROOT / "scripts" / "gate_bounded_retry.py"
CHECKOUT_HELPER_RUN = 'python3 "${RUNNER_TEMP}/gate_bounded_retry.py" checkout'
BOOTSTRAP_CALL = 'bash -euo pipefail -c "$GATE_SOURCE_BOOTSTRAP_SCRIPT"'
GATE_CHECKOUT_MIRROR_CALL = 'bash -euo pipefail -c "$GATE_CHECKOUT_MIRROR_SCRIPT"'
MAGICDNS_HELPER_RUN = 'python3 "${RUNNER_TEMP}/gate_bounded_retry.py" magicdns'
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
EXPECTED_ACTION_REFS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/cache": "0057852bfaa89a56745cba8c7296529d2fc39830",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
}
# fromJSON('["self-hosted","linux","ci"]') — capture each array literal in runs-on.
_FROMJSON_LABELS_RE = re.compile(r"fromJSON\('(\[[^\]]*\])'\)")


def _load_workflow():
    raw = yaml.safe_load(WORKFLOW.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _evaluate_primary_result_expr(expression: str, raw_result: str) -> str:
    """Evaluate the small equality/and/or subset used by this GHA expression."""
    body = expression.removeprefix("${{").removesuffix("}}").strip()
    for disjunction in body.split("||"):
        value = True
        for operand in disjunction.split("&&"):
            operand = operand.strip()
            comparison = re.fullmatch(r"needs\.primary\.result == '([^']+)'", operand)
            if comparison:
                value = raw_result == comparison.group(1)
            elif operand.startswith("'") and operand.endswith("'"):
                value = operand[1:-1]
            elif operand == "needs.primary.result":
                value = raw_result
            else:
                raise AssertionError(f"unsupported PRIMARY_RESULT expression operand: {operand!r}")
            if not value:
                break
        if value:
            return value if isinstance(value, str) else "true"
    return ""


def _load_caller():
    raw = yaml.safe_load(CALLER_TEMPLATE.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _load_disposition_workflow():
    raw = yaml.safe_load(DISPOSITION_WORKFLOW.read_text())
    trigger = raw.get("on", raw.get(True))
    return raw, trigger


def _sparse_env_paths(step: dict) -> list[str]:
    raw = (step.get("env") or {}).get("GATE_CHECKOUT_SPARSE", "")
    if not isinstance(raw, str):
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def assert_workflow_sha_checkout(step: dict, *, path: str | None = None, sparse: list[str] | str | None = None) -> None:
    """Lock reusable-workflow identity + helper invocation without weakening fields.

    Every call site must name a destination below the workspace: gate#278 was a
    disposition site that declared none, and `gate_source.checkout_from_environment`
    rejects an empty or "." path, so `path=""` no longer means "workspace root"
    anywhere.  Passing `path` asserts one specific subdirectory; omitting it
    still asserts that a real subdirectory is declared.
    """

    uses = str(step.get("uses", ""))
    assert not uses.startswith("actions/checkout"), step.get("name")
    env = step["env"]
    assert env["GATE_CHECKOUT_REPOSITORY"] == "${{ job.workflow_repository }}"
    assert env["GATE_CHECKOUT_REF"] == "${{ job.workflow_sha }}"
    declared = env.get("GATE_CHECKOUT_PATH", "")
    assert declared and declared != ".", (
        f"{step.get('name')}: GATE_CHECKOUT_PATH must name a subdirectory below the workspace, got {declared!r}"
    )
    assert not declared.startswith("/") and ".." not in declared.split("/"), declared
    if path is not None:
        assert declared == path
    assert env["GATE_GITHUB_TOKEN"] == "${{ github.token }}"
    if sparse is not None:
        expected = sparse.splitlines() if isinstance(sparse, str) else list(sparse)
        expected = [line.strip() for line in expected if line.strip()]
        assert _sparse_env_paths(step) == expected
    run = step["run"]
    assert CHECKOUT_HELPER_RUN in run
    # The Contents API fetch moved into the one workflow-level bootstrap script
    # that all 10 call sites share (gate-hub W3a); the token -> header write and
    # the curl live there now, so the invariant is asserted where the bytes are.
    bootstrap = _load_workflow()[0]["env"]["GATE_SOURCE_BOOTSTRAP_SCRIPT"]
    assert BOOTSTRAP_CALL in run
    assert "os.environ['GATE_GITHUB_TOKEN']" in bootstrap
    assert "-H \"@${RUNNER_TEMP}/gate-api.hdr\"" in bootstrap
    assert "${{" not in run
    assert "x-access-token" not in run
    assert "github.token" not in run


def _disposition_checkout_step() -> dict:
    raw, _ = _load_disposition_workflow()
    return next(
        step
        for step in raw["jobs"]["control"]["steps"]
        if step.get("name") == "Checkout disposition producer"
    )


def _disposition_step(name: str) -> dict:
    raw, _ = _load_disposition_workflow()
    return next(step for step in raw["jobs"]["control"]["steps"] if step.get("name") == name)


def _checkout_shaped_workspace(tmp_path, subdir: str, sparse: list[str]) -> Path:
    """A workspace shaped exactly like the tool-sparse checkout leaves it.

    Only `subdir` exists: every declared sparse file is present below it and
    absent from the workspace root, so a consumer still addressing the root
    resolves to nothing instead of silently working in the source tree.
    """
    workspace = tmp_path / "workspace"
    for relative in sparse:
        target = workspace / subdir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / relative, target)
    return workspace


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




def test_disposition_workflow_resolves_magicdns_before_s3_and_has_no_upload_artifact():
    raw, _ = _load_disposition_workflow()
    text = DISPOSITION_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("100.100.100.100") >= 1
    assert raw.get("env", {}).get("SILO_ENDPOINT") == "https://zlx-vm-work-i5-infra.taile9071.ts.net:9000"
    assert raw.get("env", {}).get("SILO_BUCKET") == "ci-artifacts"
    control = raw["jobs"]["control"]
    assert control["env"]["SILO_STORE"].endswith("scripts/silo_store.py")
    assert control["runs-on"] == ["self-hosted", "linux", "ci", "git-source-service"]
    names = [step.get("name") for step in control["steps"]]
    dns_index = names.index("Resolve Silo hostname via MagicDNS")
    first_s3 = next(
        index
        for index, step in enumerate(control["steps"])
        if "$SILO_EXEC" in str(step.get("run", ""))
        and any(token in str(step.get("run", "")) for token in (" put ", " put-dir ", " get "))
    )
    assert dns_index < first_s3
    dns = control["steps"][dns_index]
    assert "id -u" in dns["run"]
    assert "100.100.100.100" in dns["run"]
    assert MAGICDNS_HELPER_RUN in dns["run"]
    download_run = next(
        step["run"] for step in control["steps"]
        if step.get("name") == "Resolve current PR head and canonical primary audit"
    )
    fallback_at = download_run.index("gh run download")
    silo_get_at = download_run.index("get --key")
    assert silo_get_at < fallback_at
    after_fallback_branch = download_run[download_run.index('if [ "$silo_rc" -eq 2 ]; then'):]
    assert "gh run download" in after_fallback_branch
    before_fallback = download_run[:download_run.index('if [ "$silo_rc" -eq 2 ]; then')]
    assert "gh run download" not in before_fallback


def test_magicdns_failure_messages_point_to_underlying_retry_reason():
    for workflow_name, loader in (
        ("gate-v2.yml", _load_workflow),
        ("gate-v2-disposition.yml", _load_disposition_workflow),
    ):
        raw, _ = loader()
        for job_name, job in raw["jobs"].items():
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                if MAGICDNS_HELPER_RUN not in run:
                    continue
                messages = re.findall(r'echo "::error::([^"\n]+)" >&2', run)
                assert len(messages) == 2, (workflow_name, job_name, step.get("name"))
                for message in messages:
                    assert "Silo MagicDNS lookup failed" in message
                    assert "MagicDNS attempt N/M failed:" in message
                    assert "SILO_ENDPOINT=" in message
                    assert "tailnet" not in message
                    assert "100.100.100.100" not in message
















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


def test_gate_v2_has_no_github_artifact_actions():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "download-artifact" not in text
    raw, _ = _load_workflow()
    uploads = [
        (job_name, step.get("id"), (step.get("with") or {}).get("path"))
        for job_name, job in raw["jobs"].items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert uploads == [
        ("quality", "upload-quality-junit", "${{ runner.temp }}/gate-quality/junit/"),
        ("quality", "upload-quality-junit-legacy", "${{ runner.temp }}/gate-tested-evidence-junit/"),
        ("quality", "upload-quality-reuse-receipt", "${{ runner.temp }}/gate-quality-reuse/quality-reuse-receipt.json"),
    ], "GitHub artifact uploads are quality junit retention and quality reuse receipt paths"
    assert raw.get("env", {}).get("SILO_ENDPOINT") == "https://zlx-vm-work-i5-infra.taile9071.ts.net:9000"
    assert raw.get("env", {}).get("SILO_BUCKET") == "ci-artifacts"
    hold = REPO_ROOT / ".github" / "v2-tag-sync.hold"
    # 冻结已解除；hold 文件回归会静默冻住 v2 tag（gate-hub#860）
    assert not hold.exists()


def test_silo_touching_jobs_resolve_magicdns_before_s3():
    raw, _ = _load_workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("100.100.100.100") >= 4
    for job_name in ("primary", "gate", "ledger"):
        steps = raw["jobs"][job_name]["steps"]
        names = [step.get("name") for step in steps]
        assert "Resolve Silo hostname via MagicDNS" in names
        dns_index = names.index("Resolve Silo hostname via MagicDNS")
        first_transfer = None
        for index, step in enumerate(steps):
            run = str(step.get("run", ""))
            if "$SILO_EXEC" not in run:
                continue
            if any(token in run for token in (" put ", " put-dir ", " get ", " resolve")):
                first_transfer = index
                break
        assert first_transfer is not None, job_name
        assert dns_index < first_transfer, job_name
        dns = steps[dns_index]
        assert "id -u" in dns["run"]
        assert "100.100.100.100" in dns["run"]
        assert MAGICDNS_HELPER_RUN in dns["run"]
        assert "--silo-store \"$SILO_STORE\"" in dns["run"]

    # gate#185: Upload terminal is if: always() and needs /etc/hosts. Skipping
    # MagicDNS whenever primary is skipped made exempt PRs fail ledger.
    # Self-hosted same-repo still has tailnet; hosted/fork stay off.
    gate_dns = next(s for s in raw["jobs"]["gate"]["steps"] if s.get("name") == "Resolve Silo hostname via MagicDNS")
    gate_runs_on = str(raw["jobs"]["gate"]["runs-on"])
    self_hosted_cond = gate_runs_on.split("&& fromJSON", 1)[0]
    for token in (RUNNER_GUARD, CONTROL_RUNNER_GUARD):
        assert token in self_hosted_cond, token
        assert token in gate_dns["if"], token
    assert gate_dns["if"] == (
        "${{ always() && inputs.runner == 'self' && "
        "inputs.control_runner != 'github-hosted' && "
        "github.event.pull_request.head.repo.full_name == github.repository }}"
    )
    assert FORK_GUARD in gate_dns["if"]
    assert "needs.primary.result != 'skipped'" not in str(gate_dns.get("if", ""))

    primary_dns = next(s for s in raw["jobs"]["primary"]["steps"] if s.get("name") == "Resolve Silo hostname via MagicDNS")
    assert primary_dns["if"] == "always()"
    ledger_dns = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Resolve Silo hostname via MagicDNS")
    assert ledger_dns["if"] == "always()"


def test_silo_store_env_aligns_with_job_checkout_path():
    raw, _ = _load_workflow()
    checkout_dirs = {
        "primary": "_gate-silo-src",
        "gate": "_gate-aggregator-src",
        "ledger": "_gate-aggregator-src",
    }
    for job_name in ("primary", "gate", "ledger"):
        job = raw["jobs"][job_name]
        silo_store_env = job.get("env", {}).get("SILO_STORE", "")
        checkout_dir = checkout_dirs[job_name]
        assert silo_store_env == f"${{{{ github.workspace }}}}/{checkout_dir}/scripts/silo_store.py", (
            f"{job_name}: SILO_STORE must point to an absolute scripts/silo_store.py"
        )
        assert job["env"]["SILO_EXEC"] == (
            f"${{{{ github.workspace }}}}/{checkout_dir}/scripts/silo_exec.sh"
        )
        checkout_step = next(
            (
                s
                for s in job["steps"]
                if (s.get("env") or {}).get("GATE_CHECKOUT_PATH") == checkout_dir
            ),
            None,
        )
        assert checkout_step is not None, f"{job_name}: no checkout step for {checkout_dir}"
        assert_workflow_sha_checkout(checkout_step, path=checkout_dir)


def test_secrets_explicit_and_feishu_compatibility():
    code = "\n".join(ln for ln in WORKFLOW.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert "inherit" not in code
    _, trigger = _load_workflow()
    secrets = trigger["workflow_call"].get("secrets", {})
    assert "FEISHU_CI_WEBHOOK" in secrets
    assert set(secrets.keys()) == {"FEISHU_CI_WEBHOOK", "SILO_ACCESS_KEY", "SILO_SECRET_KEY"}
    assert secrets["FEISHU_CI_WEBHOOK"].get("required") is False
    assert secrets["SILO_ACCESS_KEY"].get("required") is False
    assert secrets["SILO_SECRET_KEY"].get("required") is False


def test_control_runner_input_defaults_to_follow_runner():
    _, trigger = _load_workflow()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["control_runner"]["default"] == ""


def test_all_required_jobs_present():
    raw, _ = _load_workflow()
    assert set(raw["jobs"].keys()) == {
        "classify_pr_paths", "quality", "primary", "advisor_classify", "advisor", "gate", "ledger",
    }


def test_classify_job_always_runs_and_exposes_review_expected():
    raw, _ = _load_workflow()
    job = raw["jobs"][CLASSIFY_JOB_ID]
    assert job.get("if") == "always()"
    assert "needs" not in job
    assert job["runs-on"] == raw["jobs"]["gate"]["runs-on"]
    assert job["outputs"]["review_expected"] == "${{ steps.classify.outputs.review_expected }}"
    step = next(s for s in job["steps"] if s.get("id") == "classify")
    run = step["run"]
    assert CLASSIFY_SCRIPT in run
    assert 'echo "review_expected=true" >> "$GITHUB_OUTPUT"' in run
    assert "review_expected=false" not in run
    assert "exit 0" in run
    assert "compare/" in run
    assert "pulls/" not in run
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert step["env"]["REVIEW_EXEMPT_PATHS"] == "${{ inputs.review_exempt_paths }}"
    assert "${{ inputs.review_exempt_paths }}" not in run
    assert "== 'true'" not in str(raw["jobs"]["primary"].get("if", ""))
    assert CLASSIFY_GUARD in str(raw["jobs"]["primary"].get("if", ""))


def test_workflow_call_inputs_include_review_exempt_paths():
    _, trigger = _load_workflow()
    assert set(trigger["workflow_call"]["inputs"]) == {
        "tier",
        "runner",
        "has_ui",
        "design_doc",
        "max_diff_lines",
        "timeout_minutes",
        "max_review_shards",
        "pr_size_warn_lines",
        "primary_timeout_minutes",
        "control_runner",
        "review_exempt_paths",
        "quality_prepare_mise",
        "extra_runner_label", "extra_runner_label_ci", "extra_runner_label_review",
    }
    declared = trigger["workflow_call"]["inputs"]["review_exempt_paths"]
    assert declared["type"] == "string"
    assert declared["default"] == ""


def test_classify_job_checks_out_this_workflow_commit():
    raw, _ = _load_workflow()
    checkout = next(
        s for s in raw["jobs"][CLASSIFY_JOB_ID]["steps"]
        if s.get("name") == "Checkout classify script at this workflow's own commit"
    )
    assert_workflow_sha_checkout(checkout, path="_gate-classify-src", sparse=[])


def test_workflow_sha_checkouts_use_centralized_bounded_retry():
    raw, _ = _load_workflow()
    env = raw["env"]
    assert env["GATE_NET_RETRY_ATTEMPTS"] == str(retry.DEFAULT_RETRY_ATTEMPTS)
    assert env["GATE_NET_RETRY_BACKOFF_SECS"] == " ".join(str(item) for item in retry.DEFAULT_RETRY_BACKOFF_SECS)
    assert env["GATE_NET_ATTEMPT_TIMEOUT_SECS"] == str(retry.DEFAULT_ATTEMPT_TIMEOUT_SECS)
    assert env["GATE_HTTP_LOW_SPEED_LIMIT"] == str(retry.DEFAULT_LOW_SPEED_LIMIT)
    assert env["GATE_HTTP_LOW_SPEED_TIME"] == str(retry.DEFAULT_LOW_SPEED_TIME)
    assert retry.worst_case_secs() <= 180
    disposition, _ = _load_disposition_workflow()
    for key in (
        "GATE_NET_RETRY_ATTEMPTS", "GATE_NET_RETRY_BACKOFF_SECS",
        "GATE_NET_ATTEMPT_TIMEOUT_SECS", "GATE_HTTP_LOW_SPEED_LIMIT", "GATE_HTTP_LOW_SPEED_TIME",
    ):
        assert disposition["env"][key] == env[key]
    sites = []
    for job_name, job in raw["jobs"].items():
        for step in job.get("steps", []):
            step_env = step.get("env") or {}
            uses = str(step.get("uses", ""))
            if step_env.get("GATE_CHECKOUT_REF") == "${{ job.workflow_sha }}":
                sites.append((job_name, step.get("name")))
                assert_workflow_sha_checkout(step)
            if uses.startswith("actions/checkout"):
                assert (step.get("with") or {}).get("ref") != "${{ job.workflow_sha }}"
    assert len(sites) == 8
    producer = next(
        step for step in disposition["jobs"]["control"]["steps"]
        if step.get("name") == "Checkout disposition producer"
    )
    assert_workflow_sha_checkout(producer, path=DISPOSITION_CHECKOUT_SUBDIR)


def test_every_workflow_sha_checkout_names_a_workspace_subdirectory():
    """gate#278: one call site out of ten declared no destination and the whole
    disposition job died in `checkout_from_environment`.  Sweep every real call
    site of every gate workflow instead of trusting a hand-written list; the
    count is reported, never frozen."""
    helper_sites = []
    mirror_sites = []
    for path in sorted((REPO_ROOT / ".github/workflows").glob("gate*.yml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (raw.get("jobs") or {}).items():
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                step_env = step.get("env") or {}
                if step_env.get("GATE_CHECKOUT_REF") != "${{ job.workflow_sha }}":
                    continue
                site = (path.name, job_name, step.get("name"))
                if CHECKOUT_HELPER_RUN in run:
                    helper_sites.append(site)
                    assert_workflow_sha_checkout(step)
                    continue
                if GATE_CHECKOUT_MIRROR_CALL in run:
                    mirror_sites.append(site)
                    # The mirror prefetch checks out into
                    # `$GITHUB_WORKSPACE/$GATE_CHECKOUT_PATH`, so it owes the same
                    # non-empty, non-"." destination.
                    declared = step_env.get("GATE_CHECKOUT_PATH", "")
                    assert declared and declared != ".", site
    assert helper_sites, "no workflow_sha checkout call sites found"
    assert len(helper_sites) + len(mirror_sites) >= 10, f"call sites disappeared: {helper_sites}"
    disposition = [site for site in helper_sites if site[0] == "gate-v2-disposition.yml"]
    assert [site[1:] for site in disposition] == [("control", "Checkout disposition producer")]




def _seed_caller_head(repo: Path, *, relpath="src/guard.py", literal="counterevidence-literal"):
    repo.mkdir(parents=True, exist_ok=True)
    (repo / relpath).parent.mkdir(parents=True, exist_ok=True)
    (repo / relpath).write_text(f"{literal}\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "gate-test",
        "GIT_AUTHOR_EMAIL": "gate-test@example.com",
        "GIT_COMMITTER_NAME": "gate-test",
        "GIT_COMMITTER_EMAIL": "gate-test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "gate-test@example.com"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "gate-test"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True, env=env)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, env=env).strip()
    return sha, {
        "command": f"git grep -n -F -e {literal} {sha} -- {relpath}",
        "output": "forged",
        "result": "refuted",
        "pointer": relpath,
    }




# gate#275: a caller still pinned to the legacy five inputs carries no evidence
# channel at all.  The receipt rejects it either way; the preflight only makes
# that rejection readable, and only for shapes the receipt rejects for ANY audit.
DISPOSITION_PREFLIGHT_ERROR = "DISPOSITION_INPUT_PREFLIGHT_FAILED"
PREFLIGHT_STEP_NAME = "Preflight disposition evidence inputs"
# The env the workflow maps the three optional inputs into, at the preflight
# step and at the authoritative receipt step; both must read the same values.
EVIDENCE_INPUT_ENV = {
    "DISPOSITION_KIND": "${{ inputs.disposition }}",
    "DISPOSITION_COUNTEREVIDENCE_JSON": "${{ inputs.counterevidence_json }}",
    "DISPOSITION_TRACKING_ISSUE": "${{ inputs.tracking_issue }}",
}
VALID_COUNTEREVIDENCE = "ALLOWLISTED_GIT_GREP"
# What a legacy five-input caller sends: every optional input absent/empty.
LEGACY_FIVE_INPUTS = {
    "pr_number": "42", "primary_run_id": "7", "primary_run_attempt": "1",
    "finding_id": "finding-one", "reason": "canonical evidence reviewed",
}


def _preflight_step() -> dict:
    return _disposition_step(PREFLIGHT_STEP_NAME)


def _input_env(step: dict, values: dict[str, str]) -> dict[str, str]:
    """Render a step's input-backed env from workflow input values (absent -> "")."""
    rendered = {}
    for var, expression in step["env"].items():
        match = re.fullmatch(r"\$\{\{ inputs\.(\w+) \}\}", expression)
        if match is None:
            continue  # github.* context, filled by the runner or by the caller below
        rendered[var] = values.get(match.group(1), "")
    return {**os.environ, **rendered}


def _run_preflight(values: dict[str, str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    step = _preflight_step()
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env=env or _input_env(step, values), capture_output=True, text=True, timeout=120,
    )


def _run_receipt_step(tmp_path, values: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the receipt step's own `run` body against the real producer bytes."""
    step = _disposition_step("Issue immutable disposition artifact")
    checkout = _disposition_checkout_step()
    workspace = _checkout_shaped_workspace(
        tmp_path, checkout["env"]["GATE_CHECKOUT_PATH"], _sparse_env_paths(checkout)
    )
    caller_head = tmp_path / "caller-head"
    head_sha, seeded = _seed_caller_head(caller_head)
    values = dict(values)
    if values.get("counterevidence_json") == VALID_COUNTEREVIDENCE:
        values["counterevidence_json"] = json.dumps(seeded)
    scope = {
        "repository_id": 123, "pr_number": 42, "base_sha": "b" * 40,
        "head_sha": head_sha, "diff_digest": "d" * 64, "policy_version": "policy-v1",
        "policy_digest": "p" * 64, "tier": "personal", "caller_sha": "c" * 40,
        "reusable_workflow_sha": "r" * 40,
    }
    audit = {
        **scope, "repository": "zlxlabs/gate", "pr": 42, "run_id": 7, "run_attempt": 1,
        "verdict": "fail",
        "result": {"findings": [{
            "id": "finding-one", "severity": "major", "trigger_kind": "inferred",
            "file": "src/guard.py", "line": 12, "category": "correctness",
        }]},
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    output_dir = tmp_path / "artifact"
    output_dir.mkdir(exist_ok=True)
    github_env = tmp_path / "github-env"
    github_env.write_text("")
    github_output = tmp_path / "github-output"
    github_output.write_text("")
    env = {
        **_input_env(step, values),
        "AUDIT_PATH": str(audit_path),
        "CURRENT_HEAD_SHA": scope["head_sha"],
        "CURRENT_AUDIT_DIGEST": "d" * 64,
        "CURRENT_EPOCH": "e" * 64,
        "CURRENT_SCOPE_JSON": json.dumps(scope),
        "REPOSITORY_ID": "123",
        "PR_NUMBER": "42",
        "FINDING_ID": "finding-one",
        "DISPOSITION_REASON": "canonical evidence reviewed",
        "DISPOSITION_APPROVER": "owner",
        "DISPOSITION_APPROVER_ID": "10",
        "DISPOSITION_REPOSITORY": "zlxlabs/gate",
        "OUTPUT_DIR": str(output_dir),
        "GITHUB_TRIGGERING_ACTOR": "owner",
        "GITHUB_ENV": str(github_env),
        "GITHUB_OUTPUT": str(github_output),
        "CALLER_HEAD_DIR": str(caller_head),
        "GATE_SOURCE_DECIDE_SCRIPT": _load_disposition_workflow()[0]["env"]["GATE_SOURCE_DECIDE_SCRIPT"],
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=180,
    )
    result.receipt_dir = output_dir  # type: ignore[attr-defined]
    return result








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


@pytest.mark.parametrize(
    "raw_result,expected",
    [
        *((result, result) for result in PRIMARY_RESULT_DOMAIN),
        ("", "cancelled"),
        ("abandoned", "cancelled"),
        ("weird_future_value", "cancelled"),
    ],
)
def test_primary_result_expression_preserves_domain_and_normalizes_everything_else(raw_result, expected):
    assert _evaluate_primary_result_expr(PRIMARY_RESULT_EXPR, raw_result) == expected




def test_classify_listing_failure_does_not_output_false(tmp_path):
    raw, _ = _load_workflow()
    step = next(s for s in raw["jobs"][CLASSIFY_JOB_ID]["steps"] if s.get("id") == "classify")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/bash\necho gh-failed >&2\nexit 42\n", encoding="utf-8")
    gh.chmod(0o755)
    output = tmp_path / "github_output"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GH_TOKEN": "x",
        "REPOSITORY": "zlxlabs/gate",
        "BASE_SHA": "0" * 40,
        "HEAD_SHA": "a" * 40,
    }
    proc = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    text = output.read_text(encoding="utf-8") if output.is_file() else ""
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "review_expected=false" not in text
    assert "review_expected=true" in text
    assert "exit=42" in proc.stdout + proc.stderr + text or "listing PR files failed" in proc.stdout + proc.stderr


def test_aggregator_check_name_separates_draft_events():
    raw, _ = _load_workflow()
    assert "gate" in raw["jobs"]
    gate = raw["jobs"]["gate"]
    assert gate.get("name") == GATE_AGGREGATOR_NAME_EXPRESSION
    assert gate["if"] == "${{ always() && (github.event_name != 'pull_request' || github.event.action != 'converted_to_draft') }}"
    assert gate["needs"] == ["quality", "primary", "classify_pr_paths"]
    assert raw["jobs"]["ledger"]["needs"] == ["quality", "primary", "gate", "classify_pr_paths"]


@pytest.mark.parametrize("job_name", ["primary"])
def test_job_id_resolution_retries_with_timeout(job_name):
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"][job_name]["steps"]
        if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID"
    )
    run = step["run"]

    assert "max_attempts=3" in run
    assert "for attempt in 1 2 3; do" in run
    normalized_run = "\n".join(line.strip() for line in run.splitlines())
    assert 'if [ "$rc" -eq 0 ]; then\nbreak\nfi' in normalized_run
    assert "timeout --foreground" in run
    assert '|| rc=$?' in run
    assert 'sleep "$retry_delay_seconds"' in run
    assert "retry_delay_seconds=$((retry_delay_seconds * 2))" in run
    assert 'if [ "$rc" -ne 0 ]; then' in run
    assert "exit 1" in run
    assert "while true" not in run
    assert "until true" not in run


@pytest.mark.parametrize("job_name", ["primary"])
def test_job_id_resolution_separates_api_failure_from_empty_result(job_name):
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"][job_name]["steps"]
        if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID"
    )
    run = step["run"]

    run_lines = run.splitlines()

    def shell_if_branch(condition):
        start = next(i for i, line in enumerate(run_lines) if line.strip() == condition)
        end = next(i for i in range(start + 1, len(run_lines)) if run_lines[i].strip() == "fi")
        return "\n".join(run_lines[start:end])

    api_failure_branch = shell_if_branch('if [ "$rc" -ne 0 ]; then')
    no_match_branch = shell_if_branch('if [ -z "$job_id" ]; then')

    assert "Jobs API call failed after" in api_failure_branch
    assert "no matching job" not in api_failure_branch
    assert "no matching job" in no_match_branch
    assert "Jobs API call failed after" not in no_match_branch


# ── concurrency contract ─────────────────────────────────────────────────────
# Full lock matrix: no workflow-level group; quality/primary cancel stale work
# per PR; gate serializes its per-PR panel writer; ledger has no lock because
# its files and artifact are run-local; every other job stays unlocked.

_GATE_WRITER_CONCURRENCY = {
    "group": "gate-required-v2-panel-${{ github.repository_id }}-${{ github.event.pull_request.number }}",
    "cancel-in-progress": False,
}


def test_required_v2_has_no_workflow_level_concurrency():
    raw, _ = _load_workflow()
    assert "concurrency" not in raw


def test_gate_panel_writer_lock_remains_cancel_false():
    raw, _ = _load_workflow()
    # Byte-exact contract with base: the full mapping (complete group literal
    # + cancel-in-progress) must equal these constants, not just pass per-field
    # shape probes.
    assert raw["jobs"]["gate"].get("concurrency") == _GATE_WRITER_CONCURRENCY


def _assert_expensive_job_cancel_lock(job_name: str, concurrency: dict) -> None:
    # Full-expression equality, not substring shape probes: the fallback order
    # (PR number first, run_id last) is part of the contract.
    assert concurrency == {
        "group": (
            f"gate-required-v2-{job_name}-${{{{ github.workflow }}}}"
            f"-${{{{ github.repository_id }}}}"
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


# ── quality runs independently of primary (gate#129, owner 2026-09-07) ───────


@pytest.mark.parametrize("primary_result", ["success", "failure", "cancelled", "skipped"])
def test_quality_runs_for_each_primary_result_without_dependency(primary_result):
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    assert primary_result in {"success", "failure", "cancelled", "skipped"}
    assert "primary" not in quality.get("needs", [])
    assert quality["needs"] == ["classify_pr_paths"]
    assert quality["if"] == "${{ !cancelled() && !(github.event_name == 'pull_request' && github.event.action == 'converted_to_draft') && !(github.event_name == 'pull_request' && github.event.action == 'ready_for_review' && needs.classify_pr_paths.outputs.quality_reusable == 'true') }}"


def test_non_writer_non_expensive_jobs_have_no_concurrency():
    raw, _ = _load_workflow()
    for job_name, job in raw["jobs"].items():
        if job_name not in {"gate", "quality", "primary"}:
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


def test_gate_terminal_upload_declares_explicit_retention():
    raw, _ = _load_workflow()
    upload = next(
        step
        for step in raw["jobs"]["gate"]["steps"]
        if step.get("name") == "Upload gate terminal envelope"
    )
    assert "--tier d30" in upload["run"]
    assert "$SILO_EXEC" in upload["run"]
    assert upload["env"]["ARTIFACT_NAME"] == (
        "gate-terminal-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
        "-${{ github.run_id }}-${{ github.run_attempt }}"
    )


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
    assert "uses" not in upload
    assert "always()" in str(upload["if"])
    # bookkeeping (design A.4): a receipt upload failure must not flip the gate
    # job; it emits the G3 replay-queue line instead.
    assert upload["continue-on-error"] is True
    assert upload["env"]["ARTIFACT_NAME"] == CONVERGENCE_RECEIPT_NAME_EXPR
    assert upload["env"]["RECEIPT_DIR"] == CONVERGENCE_RECEIPT_PATH
    assert "--tier d3" in upload["run"]
    assert "$SILO_EXEC" in upload["run"]
    # the legacy "no swallowed exit codes" guard, refined: the Silo invocation
    # itself must never carry `|| true` (the `|| true` on the deferred-row
    # reason extraction only guards grep's no-match exit under pipefail)
    assert not any("$SILO_EXEC" in line and "|| true" in line for line in upload["run"].splitlines())
    assert "GATE-ARCHIVE-DEFERRED kind=convergence-receipt " in upload["run"]


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
    assert upload["continue-on-error"] is True
    assert "GATE-ARCHIVE-DEFERRED" in upload["run"]


# ── gate aggregator job: required-check identity + always() ─────────────────

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


@pytest.mark.parametrize(
    ("job_name", "step_name"),
    [
        ("ledger", "Resolve v2 ledger artifacts"),
    ],
)
def test_artifact_listing_resolvers_retry_with_bounded_timeout(job_name, step_name):
    raw, _ = _load_workflow()
    step = next(s for s in raw["jobs"][job_name]["steps"] if s.get("name") == step_name)
    run = step["run"]

    assert "$SILO_EXEC" in run
    assert " resolve" in run
    assert "--attempt" in run
    assert "SILO_ACCESS_KEY 未传入" in run
    assert "while true" not in run
    assert "until true" not in run


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




def _evaluate_ledger_if(expression: str, outcomes: dict[str, str], *, job_success: bool) -> bool:
    expression = re.sub(
        r"steps\.([a-z0-9-]+)\.outcome",
        lambda match: repr(outcomes[match.group(1)]),
        expression,
    )
    expression = re.sub(r"\bsuccess\(\)", str(job_success), expression)
    expression = expression.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(expression, mode="eval")

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [evaluate(value) for value in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            left, right = evaluate(node.left), evaluate(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
        raise AssertionError(f"unsupported ledger if expression node: {ast.dump(node)}")

    return bool(evaluate(tree))




def _assert_ledger_scheduling_contract(workflow: dict) -> None:
    ledger = workflow["jobs"]["ledger"]
    assert ledger["timeout-minutes"] == 10
    assert "concurrency" not in ledger










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


def _silo_prefix(name: str) -> str:
    if name.startswith("review-ledger-input-v2-"):
        return f"d1/1/{name}"
    if name.startswith("primary-audit-v2-"):
        return f"d14/1/{name}"
    if name.startswith("gate-terminal-v1-"):
        return f"d30/1/{name}"
    raise ValueError(name)


def _resolve_env_from_artifacts(artifacts, current: int) -> dict[str, str]:
    names = [
        artifact["name"]
        for artifact in artifacts
        if isinstance(artifact, dict)
        and isinstance(artifact.get("name"), str)
        and artifact.get("expired") is not True
    ]

    def picked(prefix: str, tier: str) -> str:
        selected = silo_store.select_attempt(names, prefix, current)
        if selected is None:
            return ""
        name, attempt = selected
        return f"{tier}/1/{name}\t{attempt}\t{name}"

    return {
        "INPUT_RESOLVE": picked("review-ledger-input-v2-", "d1"),
        "AUDIT_RESOLVE": picked("primary-audit-v2-", "d14"),
        "TERMINAL_RESOLVE": picked("gate-terminal-v1-", "d30"),
    }


def _run_ledger_resolver(
    tmp_path, *, artifacts, current, review_expected="false",
    jobs=None, jobs_path=None, attempt=None, attempt_path=None,
    extra_env=None, process_timeout=None,
):
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    argv = [
        sys.executable, "-",
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
        "PRIMARY_RESULT_RAW", "INPUT_RESOLVE", "AUDIT_RESOLVE", "TERMINAL_RESOLVE",
    ):
        env.pop(key, None)
    # The workflow always sets these via needs.*.result; default to the
    # non-short-circuit happy path, overridable per test via extra_env.
    env["QUALITY_RESULT"] = "success"
    env["PRIMARY_RESULT"] = "success"
    env["QUALITY_LEDGER_INPUT_UPLOAD"] = "success"
    env["INPUT_PREFIX"] = "review-ledger-input-v2-"
    env["REPO_ID"] = "1"
    env.update(_resolve_env_from_artifacts(artifacts, current))
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(
            argv,
            input=_ledger_resolver_python(),
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=process_timeout,
        )
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(argv, 124, "", "resolver exceeded test harness timeout")
    return result, output.read_text(encoding="utf-8")


def _write_ledger_gh_stub(tmp_path, *, target, mode):
    executable = tmp_path / "gh-bin" / "gh"
    executable.parent.mkdir()
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        f"target = {target!r}\n"
        f"mode = {mode!r}\n"
        "path = next(arg for arg in sys.argv if '/attempts/' in arg)\n"
        "endpoint = 'jobs' if path.endswith('/jobs') else 'meta'\n"
        "if endpoint == 'jobs' and target == 'meta':\n"
        "    print('[{\\\"jobs\\\": []}]')\n"
        "    raise SystemExit(0)\n"
        "if endpoint == target and mode == 'hang':\n"
        "    time.sleep(60)\n"
        "if endpoint == target and mode == 'fail':\n"
        "    raise SystemExit(23)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


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
























def _prior_terminal_artifacts():
    return [
        {"name": "review-ledger-input-v2-2", "expired": False, "id": 102},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]










# ── gate#105 A (PR #119 r1): short-circuited quality has no input BY DESIGN ──
# With quality needs primary + `if: != 'failure'`, a primary failure skips
# quality, so the review-ledger-input artifact legitimately does not exist and
# needs.quality.outputs.ledger_input_upload is empty. The ledger job (if:
# always()) must still write its row — the resolver makes the input optional
# ONLY for this exact combination; every other missing-input case stays fatal.










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


# ── primary job: draft/fork/hosted skip + fail-closed upload ─────────────────

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
    assert_workflow_sha_checkout(
        checkout,
        path="_gate-action-src",
        sparse=[".github/actions", "scripts/gate_source.py", "scripts/scrub_outbound.py", "scripts/install_result_telemetry.py", "scripts/quality_reuse.py"],
    )
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
            "Install dependencies",
            "Tests",
        )
    )

    assert names.index(stale_cleanup["name"]) < names.index(source_checkout["name"])
    assert names.index(source_checkout["name"]) < preflight_index < cleanup_index < first_caller_check
    # gate-hub#2419: retention lint runs while _gate-action-src is present.
    assert preflight_index < names.index("Artifact retention lint") < cleanup_index
    assert cleanup_index == names.index("Artifact retention lint") + 1
    assert names[cleanup_index + 1] == "Checkout review-ledger consumer source at this workflow's own commit"
    assert names[cleanup_index + 2:cleanup_index + 4] == ["Start entry install-result telemetry", "Run scripts/gate-quality"]
    assert stale_cleanup["if"] == "always()"
    assert cleanup["if"] == "always()"
    assert 'rm -rf "$GITHUB_WORKSPACE/_gate-action-src"' in cleanup["run"]
    assert 'if [ -e "$GITHUB_WORKSPACE/_gate-action-src" ]; then' in cleanup["run"]
    assert 'if [ -e "$GITHUB_WORKSPACE/_gate-aggregator-src" ]; then' in cleanup["run"]
    assert 'rm -rf "$GITHUB_WORKSPACE/_gate-aggregator-src"' in stale_cleanup["run"]
    assert "for path in _gate-action-src _gate-aggregator-src; do" in stale_cleanup["run"]
    assert "_gate-ledger-consumer-src" not in stale_cleanup["run"]
    assert 'if [ -e "$GITHUB_WORKSPACE/$path" ]; then' in stale_cleanup["run"]

    for name in (
        "Run scripts/gate-quality",
        "Lint / format",
        "Install dependencies",
        "Tests",
    ):
        assert cleanup_index < names.index(name), f"gate source cleanup must precede {name}"


def test_install_dependencies_and_tests_run_for_every_tier_in_legacy_mode():
    raw, _ = _load_workflow()
    steps = _quality_steps(raw)
    install = next(step for step in steps if step.get("name") == "Install dependencies")
    tests = next(step for step in steps if step.get("name") == "Tests")
    install_if = str(install.get("if", ""))
    tests_if = str(tests.get("if", ""))

    assert "inputs.tier" not in install_if
    assert "inputs.tier" not in tests_if
    assert install_if == tests_if
    assert f"{QUALITY_ENTRY_MODE} == 'legacy'" in install_if


def test_v2_aggregator_jobs_do_not_execute_caller_quality_code():
    raw, _ = _load_workflow()
    caller_markers = (
        "scripts/gate-quality",
        "make lint",
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


SHARED_ACTION_MODULES = ("scripts/gate_source.py", "scripts/scrub_outbound.py", "scripts/install_result_telemetry.py")


def test_quality_action_sparse_checkout_excludes_tests_tree():
    raw, _ = _load_workflow()
    checkout = next(
        step for step in raw["jobs"]["quality"]["steps"]
        if step.get("name") == "Checkout gate actions at this workflow's own commit"
    )
    sparse_paths = _sparse_env_paths(checkout)
    assert ".github/actions" in sparse_paths
    assert "scripts/scrub_outbound.py" in sparse_paths
    assert not any(
        path == "scripts" or path.startswith("scripts/") and path not in SHARED_ACTION_MODULES and path != "scripts/quality_reuse.py"
        for path in sparse_paths
    )
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
            imported_modules = [
                "scripts.gate_source" if "from scripts.gate_source import" in path.read_text()
                else "scripts.scrub_outbound"
                for path in import_files
            ]
            if not import_files:
                continue

            checkouts = [
                candidate for candidate in job["steps"]
                if (
                    (
                        candidate.get("uses", "").startswith(CHECKOUT_ACTION)
                        and candidate.get("with", {}).get("path") == action_root
                    )
                    or (
                        (candidate.get("env") or {}).get("GATE_CHECKOUT_PATH") == action_root
                        and CHECKOUT_HELPER_RUN in str(candidate.get("run", ""))
                    )
                )
            ]
            assert checkouts, f"{job_name}: expected at least one checkout for {action_root}"
            for checkout in checkouts:
                sparse = checkout.get("with", {}).get("sparse-checkout")
                if sparse is None:
                    sparse = (checkout.get("env") or {}).get("GATE_CHECKOUT_SPARSE")
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
                    for module in set(imported_modules):
                        assert covered(module.replace(".", "/") + ".py"), (
                            f"{job_name}: checkout for {uses} misses {module}"
                        )


def test_silo_src_aggregate_sparse_covers_load_time_neighbors():
    # Derive load-time same-dir deps from aggregate.py (with_name("<file>.py")),
    # then require every job that runs that script from _gate-silo-src to have
    # checked those files out into that directory before the run.
    deps = re.findall(r'with_name\("([^"]+\.py)"\)', AGGREGATOR_SCRIPT.read_text())
    deps.append("counterevidence_argv.py")
    assert deps, "aggregate.py has no with_name(\"<file>.py\") load-time neighbors"
    needle = "_gate-silo-src/.github/actions/gate-aggregator/aggregate.py"
    raw, _ = _load_workflow()
    jobs_that_run = []
    for job_name, job in raw["jobs"].items():
        steps = job.get("steps") or []
        if not any(needle in str(step.get("run", "")) for step in steps):
            continue
        jobs_that_run.append(job_name)
        last_silo_sparse = None
        for step in steps:
            env = step.get("env") or {}
            if (
                env.get("GATE_CHECKOUT_PATH") == "_gate-silo-src"
                and env.get("GATE_CHECKOUT_SPARSE")
            ):
                last_silo_sparse = _sparse_env_paths(step)
            if needle not in str(step.get("run", "")):
                continue
            assert last_silo_sparse is not None, (
                f"{job_name}: runs {needle} without a prior "
                "_gate-silo-src GATE_CHECKOUT_SPARSE"
            )
            for dep in deps:
                rel = f".github/actions/gate-aggregator/{dep}"
                covered = any(
                    rel == prefix or rel.startswith(f"{prefix.rstrip('/')}/")
                    for prefix in last_silo_sparse
                )
                assert covered, (
                    f"{job_name}: _gate-silo-src sparse checkout missing "
                    f"aggregate.py load-time neighbor {rel}"
                )
    assert jobs_that_run, f"no job runs {needle}"


from _gate_v2_contract_support import _run_quality_result_render

def _quality_result(**overrides):
    result = {
        "schema": "gate-quality-result/v1",
        "mode": "full",
        "stage": "tests",
        "outcome": "failed",
        "failed_nodeids": None,
        "evidence": "junit_missing",
        "flake_signal": "undetermined",
    }
    result.update(overrides)
    return (json.dumps(result, separators=(",", ":")) + "\n").encode()


def test_quality_result_render_step_uses_real_workflow_script_and_producer_fixture(tmp_path):
    fixture = QUALITY_RESULT_FIXTURE.read_bytes()
    assert fixture == (
        b'{"schema":"gate-quality-result/v1","mode":"full","stage":"tests",'
        b'"outcome":"failed","failed_nodeids":["tests/test_git_mirror_fetch.py::'
        b'test_a_controlled_wait_is_attributed_to_its_lock_in_jsonl[mirror.lock-'
        b'mirror_lock_ms-consume_lock_ms]"],"evidence":"junit","flake_signal":"undetermined"}'
        b"\n"
    )
    _step, completed, summary_bytes = _run_quality_result_render(tmp_path, fixture)
    summary = summary_bytes.decode()
    nodeid = (
        "tests/test_git_mirror_fetch.py::test_a_controlled_wait_is_attributed_to_its_lock_in_jsonl"
        "[mirror.lock-mirror_lock_ms-consume_lock_ms]"
    )
    assert completed.returncode == 0
    assert "::warning::" not in completed.stdout + completed.stderr
    assert "### Quality result" in summary
    assert "- Stage: tests" in summary
    assert "- Outcome: failed" in summary
    assert "- Evidence: junit" in summary
    assert "- Mode: full" in summary
    assert "- Failed tests (1):" in summary
    assert f"\n{nodeid}\n" in summary


def test_quality_result_render_distinguishes_empty_and_unknown_nodes(tmp_path):
    _step, empty_run, empty_bytes = _run_quality_result_render(
        tmp_path / "empty", _quality_result(failed_nodeids=[], evidence="junit")
    )
    empty_summary = empty_bytes.decode()
    assert empty_run.returncode == 0
    assert "- Failed tests: none" in empty_summary

    _step, unknown_run, unknown_bytes = _run_quality_result_render(tmp_path / "unknown", _quality_result())
    unknown_summary = unknown_bytes.decode()
    assert unknown_run.returncode == 0
    assert "- Failed tests: unknown (evidence=junit_missing)" in unknown_summary
    assert "none" not in unknown_summary


@pytest.mark.parametrize(
    ("result_bytes", "reason"),
    [
        (b"{not-json", "invalid JSON"),
        (_quality_result(schema="other/v1"), "schema mismatch"),
        (_quality_result(failed_nodeids=7), "failed_nodeids must be an array of strings or null"),
        (_quality_result(failed_nodeids=["ok", 7]), "failed_nodeids must be an array of strings or null"),
    ],
)
def test_quality_result_render_invalid_payload_warns_without_failing(tmp_path, result_bytes, reason):
    _step, completed, summary_bytes = _run_quality_result_render(tmp_path, result_bytes)
    summary = summary_bytes.decode()
    assert completed.returncode == 0
    assert f"- Result: invalid ({reason})" in summary
    assert "::warning::GATE-QUALITY-RESULT-INVALID" in completed.stdout + completed.stderr


def test_quality_result_render_missing_nodeids_field_is_invalid(tmp_path):
    result = json.loads(_quality_result())
    del result["failed_nodeids"]
    result_bytes = (json.dumps(result, separators=(",", ":")) + "\n").encode()
    _step, completed, summary_bytes = _run_quality_result_render(tmp_path, result_bytes)
    assert completed.returncode == 0
    assert "- Result: invalid (failed_nodeids is required)" in summary_bytes.decode()
    assert "::warning::GATE-QUALITY-RESULT-INVALID" in completed.stdout + completed.stderr


def test_quality_result_render_missing_file_is_unadopted_without_warning(tmp_path):
    _step, completed, summary_bytes = _run_quality_result_render(tmp_path)
    assert completed.returncode == 0
    assert summary_bytes == (
        b"### Quality result\n"
        b"- Result: not_produced (caller has not adopted GATE-QUALITY-RESULT-V1)\n"
    )
    assert "::warning::" not in completed.stdout + completed.stderr


def test_quality_result_render_sanitizes_and_caps_failed_nodeids(tmp_path):
    nodeids = ["bad`" + "x" * 350 + "\x01"] + [f"node-{number}" for number in range(30)]
    _step, completed, summary_bytes = _run_quality_result_render(
        tmp_path,
        _quality_result(failed_nodeids=nodeids, evidence="junit"),
    )
    summary = summary_bytes.decode()
    code_block = summary.split("```\n", 1)[1].split("\n```", 1)[0]
    node_lines = code_block.splitlines()
    assert completed.returncode == 0
    assert "- Failed tests (31):" in summary
    assert len(node_lines) == 30
    assert node_lines[0] == "bad" + "x" * 297
    assert all("`" not in line for line in node_lines)
    assert "node-29" not in code_block
    assert "… and 1 more" in summary


def test_quality_result_render_step_order_and_fail_open_contract():
    raw, _ = _load_workflow()
    steps = _quality_steps(raw)
    names = [step.get("id") for step in steps]
    render = next(step for step in steps if step.get("id") == "render-quality-result")
    assert names.index("run-quality") < names.index("render-quality-result")
    assert names.index("render-quality-result") < names.index("upload-quality-junit")
    assert render["name"] == "Render gate-quality result"
    assert "always()" in render["if"]
    assert render["continue-on-error"] is True
    assert render["env"]["GATE_ARTIFACT_DIR"] == "${{ runner.temp }}/gate-quality"
    assert "_gate-action-src" not in render["run"]
    assert "_gate-aggregator-src" not in render["run"]


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
    # gate-hub#1696 redo: the bare run text became a helper-wrapped call
    # (no custom shell:) — the entry itself must still be invoked exactly once.
    assert '. "${RUNNER_TEMP}/gate_record_termination.sh"' in entry["run"]
    assert "gate_run_and_record ./scripts/gate-quality" in entry["run"]
    assert detect["env"]["GATE_ARTIFACT_DIR"] == "${{ runner.temp }}/gate-quality"
    assert entry["env"]["GATE_ARTIFACT_DIR"] == "${{ runner.temp }}/gate-quality"
    names = [step.get("name") for step in steps]
    assert names.count("Run scripts/gate-quality") == 1
    assert sum("gate_run_and_record ./scripts/gate-quality" in str(step.get("run", "")) for step in steps) == 1
    assert names.index("PR size preflight") < names.index("Run scripts/gate-quality")

    legacy_steps = [step for step in steps if step.get("name") in {
        "Lint / format", "Install dependencies", "Tests",
    }]
    assert legacy_steps
    for step in legacy_steps:
        assert f"{QUALITY_ENTRY_MODE} == 'legacy'" in str(step.get("if", ""))


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


def test_caller_forwards_silo_secrets():
    raw, _ = _load_caller()
    secrets = raw["jobs"]["gate"]["secrets"]
    assert secrets["SILO_ACCESS_KEY"] == "${{ secrets.SILO_ACCESS_KEY }}"
    assert secrets["SILO_SECRET_KEY"] == "${{ secrets.SILO_SECRET_KEY }}"
    text = CALLER_TEMPLATE.read_text(encoding="utf-8")
    assert "SILO_ACCESS_KEY 未传入" in text
    assert "公开仓应配置专属仓级密钥" in text


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
    assert_workflow_sha_checkout(
        checkout,
        path="_gate-action-src",
        sparse=[".github/actions", "scripts/gate_source.py", "scripts/scrub_outbound.py"],
    )

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




# ── gate#199 根治：quality 自带 caller_checks 证据链 ─────────────────────
#
# 跨发布边界接线（producer → consumer）必须读仓库内真实的 gate-v2.yml 断言，
# 自造 fixture 只做单元测试，不证明接线落地。

# 业务检查相：name → 记录步引用的 id。PR size preflight 单算一相；
# Diff coverage advisory 是 advisory 性质（continue-on-error），不计入。
CALLER_CHECKS_BUSINESS_STEPS = {
    "Run scripts/gate-quality": "run-quality",
    "Lint / format": "lint-format",
    "Install dependencies": "install",
    "Tests": "run-tests",
}
CALLER_CHECKS_PREFLIGHT_STEP = ("PR size preflight", "pr-size-preflight")


def _quality_steps(raw):
    return raw["jobs"]["quality"]["steps"]


def test_business_steps_carry_ids_for_caller_checks_evidence():
    raw, _ = _load_workflow()
    by_name = {s.get("name"): s for s in _quality_steps(raw)}
    for name, step_id in list(CALLER_CHECKS_BUSINESS_STEPS.items()) + [CALLER_CHECKS_PREFLIGHT_STEP]:
        assert by_name[name].get("id") == step_id, f"{name} must carry id: {step_id}"


def test_caller_checks_record_step_covers_every_business_step():
    raw, _ = _load_workflow()
    record = next(s for s in _quality_steps(raw) if s.get("id") == "caller-checks-outcome")
    assert record["if"] == "always()"
    env_text = " ".join(str(v) for v in record["env"].values())
    for _name, step_id in list(CALLER_CHECKS_BUSINESS_STEPS.items()) + [CALLER_CHECKS_PREFLIGHT_STEP]:
        assert f"steps.{step_id}.outcome" in env_text, f"record step must read steps.{step_id}.outcome"
    run = record["run"]
    for value in ("caller_checks=failed", "caller_checks=passed", "caller_checks=not_started"):
        assert value in run
    assert "preflight_result=" in run
    assert "GITHUB_OUTPUT" in run
    assert record["env"]["PREFLIGHT_STATUS"] == "${{ steps.pr-size-preflight.outputs.preflight-result }}"
    assert "success:success" in run
    assert "failure:blocked" in run
    assert "failure:unavailable" in run
    assert "preflight_result=unavailable" in run


def test_quality_exposes_caller_checks_evidence_outputs():
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    assert quality["outputs"]["caller_checks"] == (
        "${{ steps.caller-checks-outcome.outputs.caller_checks }}"
    )
    assert quality["outputs"]["preflight_result"] == (
        "${{ steps.caller-checks-outcome.outputs.preflight_result }}"
    )


def test_gate_forwards_caller_checks_at_both_call_sites():
    raw, _ = _load_workflow()
    gate_steps = raw["jobs"]["gate"]["steps"]
    consumers = [
        s for s in gate_steps
        if "aggregate.py" in str(s.get("run", "")) and "QUALITY_RESULT" in str(s.get("env", ""))
    ]
    assert len(consumers) == 2, "aggregate + publish-only call sites must both forward the evidence"
    for step in consumers:
        assert step["env"]["QUALITY_CALLER_CHECKS"] == "${{ needs.classify_pr_paths.outputs.quality_reusable == 'true' && needs.quality.result == 'skipped' && needs.classify_pr_paths.outputs.reuse_caller_checks || needs.quality.outputs.caller_checks }}"
        assert step["env"]["QUALITY_PREFLIGHT_RESULT"] == "${{ needs.classify_pr_paths.outputs.quality_reusable == 'true' && needs.quality.result == 'skipped' && needs.classify_pr_paths.outputs.reuse_preflight_result || needs.quality.outputs.preflight_result }}"
        assert '--caller-checks "$QUALITY_CALLER_CHECKS"' in step["run"]
        assert '--preflight-result "$QUALITY_PREFLIGHT_RESULT"' in step["run"]


def test_caller_checks_evidence_path_uses_no_log_keyword_matching():
    raw, _ = _load_workflow()
    record = next(s for s in _quality_steps(raw) if s.get("id") == "caller-checks-outcome")
    for keyword in ("curl", "timeout", "handshake", "github.com"):
        assert keyword not in record["run"], f"evidence must read steps outcome, not match {keyword!r}"
    evidence_lines = [
        line for line in AGGREGATOR_SCRIPT.read_text().splitlines()
        if "caller_checks" in line or "preflight" in line or "quality_infra" in line
    ]
    assert evidence_lines, "aggregator must carry the evidence through named variables"
    for keyword in ("curl", "timeout", "handshake", "github.com"):
        assert not any(keyword in line for line in evidence_lines), (
            f"aggregator evidence判据 must not match {keyword!r}"
        )


def test_preflight_action_publishes_structured_result_output():
    raw, _ = _load_workflow()
    preflight = next(s for s in _quality_steps(raw) if s.get("id") == "pr-size-preflight")
    action = yaml.safe_load((REPO_ROOT / ".github" / "actions" / "pr-size-preflight" / "action.yml").read_text())
    assert action["outputs"]["preflight-result"]["value"] == "${{ steps.measure.outputs.preflight-result }}"
    assert preflight["uses"] == "./_gate-action-src/.github/actions/pr-size-preflight"


@pytest.mark.parametrize(
    ("outcome", "status", "expected"),
    [("success", "success", "success"), ("failure", "blocked", "blocked"),
     ("failure", "unavailable", "unavailable"), ("failure", "", "unavailable"),
     ("success", "", "unavailable"), ("skipped", "", "unavailable"),
     ("cancelled", "", "unavailable"), ("failure", "unknown", "unavailable")],
)
def test_caller_checks_maps_action_payload_to_aggregator_state(tmp_path, outcome, status, expected):
    raw, _ = _load_workflow()
    record = next(s for s in _quality_steps(raw) if s.get("id") == "caller-checks-outcome")
    output = tmp_path / "github-output"
    env = os.environ.copy()
    env.update({
        "GITHUB_OUTPUT": str(output),
        "PREFLIGHT_OUTCOME": outcome,
        "PREFLIGHT_STATUS": status,
        "RUN_QUALITY_OUTCOME": "success",
        "LINT_FORMAT_OUTCOME": "success", "RETENTION_LINT_OUTCOME": "success", "INSTALL_OUTCOME": "success",
        "RUN_TESTS_OUTCOME": "success",
    })
    subprocess.run(["bash", "-c", record["run"]], env=env, check=True)
    lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert lines["preflight_result"] == expected


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_classification", "expected_text"),
    [
        ("unavailable", "unavailable", "review_unavailable", "git measurement failed"),
        ("blocked", "blocked", "ci_failure", "split the PR"),
    ],
)
def test_real_preflight_payload_reaches_workflow_and_aggregator_cli(
    tmp_path, mode, expected_status, expected_classification, expected_text,
):
    repo, base_sha, head_sha = _repo(tmp_path, 30)

    producer_output = tmp_path / "producer-output"
    producer_env = os.environ.copy()
    producer_env.update({"GITHUB_OUTPUT": str(producer_output), "PR_NUMBER": "0"})
    producer = run_preflight_with_compare_stub(
        ["--base-sha", base_sha, "--head-sha", head_sha,
         "--max-diff-lines", "1", "--warn-lines", "2", "--max-review-shards", "1",
         "--output", str(tmp_path / "pr-size-preflight.json")],
        cwd=repo, env=producer_env,
        merge_base_sha=base_sha if mode == "blocked" else None,
        http_status=404 if mode == "unavailable" else None,
    )
    assert producer.returncode == 1
    producer_payload = json.loads((tmp_path / "pr-size-preflight.json").read_text())
    producer_lines = dict(line.split("=", 1) for line in producer_output.read_text().splitlines())
    assert producer_payload["preflight_result"] == expected_status
    assert producer_lines["preflight-result"] == expected_status

    raw_workflow, _ = _load_workflow()
    record = next(s for s in raw_workflow["jobs"]["quality"]["steps"] if s.get("id") == "caller-checks-outcome")
    workflow_output = tmp_path / "workflow-output"
    workflow_env = os.environ.copy()
    workflow_env.update({
        "GITHUB_OUTPUT": str(workflow_output), "PREFLIGHT_OUTCOME": "failure",
        "PREFLIGHT_STATUS": producer_lines["preflight-result"], "RUN_QUALITY_OUTCOME": "success",
        "LINT_FORMAT_OUTCOME": "success", "RETENTION_LINT_OUTCOME": "success", "INSTALL_OUTCOME": "success",
        "RUN_TESTS_OUTCOME": "success",
    })
    subprocess.run(["bash", "-c", record["run"]], env=workflow_env, check=True)
    workflow_lines = dict(line.split("=", 1) for line in workflow_output.read_text().splitlines())
    assert workflow_lines["preflight_result"] == expected_status

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps({
        "kind": "primary_review", "schema_version": 1, "repository_id": 123,
        "head_sha": "a" * 40, "run_id": 999, "run_attempt": 1, "pr": 42,
        "verdict": "pass", "reviewer": "test",
    }))
    aggregator_summary = tmp_path / "aggregator-summary.md"
    aggregator = subprocess.run(
        [sys.executable, str(AGGREGATOR_SCRIPT), "--quality-result", "failure", "--caller-checks", "passed",
         "--preflight-result", workflow_lines["preflight_result"], "--primary-result", "success", "--runner", "self",
         "--is-draft", "false", "--is-fork", "false", "--classify-review-expected", "true",
         "--review-expected", "true", "--pr-author-json", json.dumps("test-human"),
         "--repository-id", "123", "--repository", "zlxlabs/gate",
         "--head-sha", "a" * 40, "--run-id", "999", "--run-attempt", "1", "--pr-number", "42",
         "--audit-source-attempt", "1", "--audit-artifact-name", "primary-audit-v2-1", "--audit-dir", str(audit_dir),
         "--summary-path", str(aggregator_summary), "--terminal-path", str(tmp_path / "gate-terminal.json")],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert aggregator.returncode == 1
    summary = aggregator_summary.read_text()
    assert f"classification=`{expected_classification}`" in summary
    assert expected_text in summary
    if mode == "unavailable":
        assert "split the PR" not in summary


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


LEDGER_INPUT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "quality-ledger-input"


def _quality_job_blob():
    raw, _ = _load_workflow()
    return raw["jobs"]["quality"]


def test_quality_job_has_no_private_storage_secrets_or_silo_calls():
    quality = _quality_job_blob()
    env = quality.get("env") or {}
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "SILO_STORE" not in env
    assert "SILO_EXEC" not in env
    blob = yaml.dump(quality)
    assert "secrets.SILO" not in blob
    assert "SILO_ACCESS_KEY" not in blob
    assert "silo_store.py" not in blob
    assert "silo_exec.sh" not in blob
    names = [step.get("name") for step in quality["steps"]]
    assert "Checkout silo store at this workflow's own commit" not in names
    assert "Resolve Silo hostname via MagicDNS" not in names
    assert "Diagnose silo network after job failure" not in names
    assert "Publish v2 review ledger inputs" in names
    publish = next(s for s in quality["steps"] if s.get("name") == "Publish v2 review ledger inputs")
    assert "gate_bounded_retry.py" not in publish["run"]
    assert "RUNNER_TEMP" not in publish["run"]


def test_quality_publish_and_ledger_consume_roundtrip_fixture_bytes(tmp_path):
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    publish = next(s for s in quality["steps"] if s.get("name") == "Publish v2 review ledger inputs")
    download = next(s for s in raw["jobs"]["ledger"]["steps"] if s.get("name") == "Download v2 review ledger inputs")
    install_step = next(s for s in quality["steps"] if s.get("id") == "install")
    assert 'telemetry finish "$status"' in install_step["run"]
    printf = "printf '{\"ecosystem\":\"%s\",\"status\":\"%s\",\"duration_s\":%s,\"cache_hit\":%s,\"cache_scope\":%s,\"runner_name\":\"%s\",\"cache_new_bytes\":%s}\\n'"

    repo, base, head = _repo(tmp_path, 1)
    (repo / "资料").mkdir()
    (repo / "资料" / "数据.bin").write_bytes(bytes(range(256)))
    (repo / "资料" / "说明.pdf").write_text("pdf-shaped text\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "excluded"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    live_preflight = tmp_path / "live-preflight.json"
    producer_env = {**os.environ, "GITHUB_OUTPUT": str(tmp_path / "preflight-github-output"),
                    "GITHUB_REPOSITORY": "spoof/from-pr-payload", "PR_NUMBER": "0"}
    producer = run_preflight_with_compare_stub(
        ["--base-sha", base, "--head-sha", head,
         "--max-diff-lines", "8000", "--warn-lines", "12000", "--max-review-shards", "8",
         "--output", str(live_preflight)],
        cwd=repo, env=producer_env, merge_base_sha=base, check=True,
    )
    preflight = live_preflight.read_text(encoding="utf-8")
    payload = json.loads(preflight)
    excluded = {(item["path"], item["rule"]) for item in payload["excluded_files"]}
    assert ("资料/数据.bin", "R1") in excluded
    assert ("资料/说明.pdf", "R2") in excluded
    assert payload["repository"] == "spoof/from-pr-payload"
    snap = json.loads((LEDGER_INPUT_FIXTURE / "pr-size-preflight.json").read_text(encoding="utf-8"))
    assert {(item["path"], item["rule"]) for item in snap["excluded_files"]} == excluded

    live_install = tmp_path / "live-install.json"
    subprocess.run(
        ["bash", "-c", f'ecosystem=uv; status=ok; start=0; end=1; cache_hit=true; cache_scope=uv-per-slot; cache_scope_json="\\"uv-per-slot\\""; RUNNER_NAME=gatehub-a1b2c3d4e5f6-slot-3; cache_new_bytes=0; {printf} '
         '"$ecosystem" "$status" "$((end - start))" "$cache_hit" "$cache_scope_json" "$RUNNER_NAME" "$cache_new_bytes" | tee "$INSTALL_RESULT_PATH"'],
        env={**os.environ, "INSTALL_RESULT_PATH": str(live_install)},
        check=True,
    )
    install = live_install.read_text(encoding="utf-8")
    assert install == (LEDGER_INPUT_FIXTURE / "install-result.json").read_text(encoding="utf-8")

    src = tmp_path / "producer"
    src.mkdir()
    (src / "pr-size-preflight.json").write_text(preflight, encoding="utf-8")
    (src / "install-result.json").write_text(install, encoding="utf-8")
    github_output = tmp_path / "github_output"
    env = {**os.environ, "PREFLIGHT": str(src / "pr-size-preflight.json"),
           "INSTALL": str(src / "install-result.json"), "GITHUB_OUTPUT": str(github_output)}
    subprocess.run(["bash", "-c", publish["run"]], env=env, check=True)
    text = github_output.read_text(encoding="utf-8")
    start = text.index("bundle<<GATE_LEDGER_BUNDLE_EOF\n") + len("bundle<<GATE_LEDGER_BUNDLE_EOF\n")
    end = text.index("\nGATE_LEDGER_BUNDLE_EOF\n", start)
    bundle = json.loads(text[start:end])
    assert bundle["preflight"] == preflight
    assert bundle["install"] == install

    dest = tmp_path / "review-ledger-input"
    consume_env = os.environ.copy()
    consume_env.update({"LEDGER_INPUT_BUNDLE": json.dumps(bundle), "DEST": str(dest)})
    subprocess.run(["bash", "-c", download["run"]], env=consume_env, check=True)
    assert (dest / "pr-size-preflight.json").read_bytes() == live_preflight.read_bytes()
    assert (dest / "install-result.json").read_bytes() == live_install.read_bytes()


def test_ledger_resolver_ignores_silo_listing_and_spoofed_prefix_for_input(tmp_path):
    artifacts = [
        {"name": "review-ledger-input-v2-oldhead", "expired": False, "id": 101},
        {"name": "gate-terminal-v1-1", "expired": False, "id": 201},
    ]
    result, output = _run_ledger_resolver(tmp_path, artifacts=artifacts, current=1)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "review-ledger-input-v2-oldhead" not in output
    assert f"input_artifact_id={_silo_prefix('review-ledger-input-v2-1')}" in output

    missing = _run_ledger_resolver(
        tmp_path, artifacts=artifacts, current=1,
        extra_env={"INPUT_PREFIX": "", "REPO_ID": "1"},
    )[0]
    assert missing.returncode != 0
    assert "INPUT_PREFIX and REPO_ID are required to bind ledger input identity" in missing.stderr + missing.stdout
