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




def test_control_runner_input_defaults_to_follow_runner():
    _, trigger = _load_workflow()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["control_runner"]["default"] == ""
















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




# ── quality runs independently of primary (gate#129, owner 2026-09-07) ───────


@pytest.mark.parametrize("primary_result", ["success", "failure", "cancelled", "skipped"])
def test_quality_runs_for_each_primary_result_without_dependency(primary_result):
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"]
    assert primary_result in {"success", "failure", "cancelled", "skipped"}
    assert "primary" not in quality.get("needs", [])
    assert quality["needs"] == ["classify_pr_paths"]
    assert quality["if"] == "${{ !cancelled() && !(github.event_name == 'pull_request' && github.event.action == 'converted_to_draft') && !(github.event_name == 'pull_request' && github.event.action == 'ready_for_review' && needs.classify_pr_paths.outputs.quality_reusable == 'true') }}"






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








































# ── primary job: draft/fork/hosted skip + fail-closed upload ─────────────────























# Regression guard for the P1 above: GitHub Actions expressions can never contain
# `+ - * / %` as operators anywhere in this file (not just in the one spot that broke).
# The scan/regex logic itself now lives in _gha_lint.py (2026-07-26, extracted for the D2
# gate-shadow-v2 task so the same guard covers that file too, without a second hand-kept
# copy of this regex) — this test just points the shared helper at THIS workflow file.


def test_no_gha_expression_anywhere_uses_arithmetic_operators():
    offenders = find_arithmetic_gha_expression_offenders(WORKFLOW)
    assert not offenders, f"found arithmetic-looking operator(s) inside GHA expression(s): {offenders!r}"












SHARED_ACTION_MODULES = ("scripts/gate_source.py", "scripts/scrub_outbound.py", "scripts/install_result_telemetry.py")








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


















# ── caller template ──────────────────────────────────────────────────────────



















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






















LEDGER_INPUT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "quality-ledger-input"


def _quality_job_blob():
    raw, _ = _load_workflow()
    return raw["jobs"]["quality"]






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
