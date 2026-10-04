"""Contract tests for Shadow Calibration v2 (.github/workflows/gate-shadow-v2.yml,
templates/caller-gate-shadow-v2.yml).

Scope: this is the D2 "Shadow Calibration" half of the shadow-review-independence
rollout (see the private gate-hub repo's ceo-plans/2026-07-24-shadow-review-independence.md).
D1's Required Gate v2 (.github/workflows/gate-v2.yml, templates/caller-gate-v2.yml,
tests/test_gate_v2_contract.py) remains independently testable — several tests below
load gate-v2.yml too, READ-ONLY, to assert this file's guards stay byte-identical while
Required Gate leaves workflow-level concurrency unset and Shadow retains its own
shadow/draft lifecycle group.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile

import pytest
import yaml

from _gha_lint import (
    find_arithmetic_gha_expression_offenders,
    materialize_jobs_api_snippet_for_probe,
    probe_jobs_api_failure_exit_code,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-shadow-v2.yml"
CALLER_TEMPLATE = REPO_ROOT / "templates" / "caller-gate-shadow-v2.yml"
REQUIRED_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
SHADOW_SNAPSHOT_DIR = REPO_ROOT / "docs" / "sessions" / "261004-shadow-snapshot"
RESOLVED_POLICY_FIXTURE = SHADOW_SNAPSHOT_DIR / "resolved-policy.fixture.json"
OLD_SHADOW_CONSUMER = SHADOW_SNAPSHOT_DIR / "old-review-shadow"
NEW_SHADOW_CONSUMER = SHADOW_SNAPSHOT_DIR / "new-review-shadow"
_GHA_EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}", re.DOTALL)
_GITHUB_OUTPUT_HEREDOC = re.compile(
    rb"^([A-Za-z0-9_]+)<<(\S+)\n(.*?)\n\2(?:\n|$)", re.DOTALL | re.MULTILINE
)
_STUB_RESOLVE_POLICY = (
    "#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n"
    "Path(sys.argv[1]).read_bytes()\n"
    "sys.stdout.write(Path(os.environ['RESOLVE_POLICY_STDOUT_FIXTURE']).read_text())\n"
)

# Byte-identical to test_gate_v2_contract.py's own FORK_GUARD/DRAFT_GUARD/RUNNER_GUARD —
# redefined locally (rather than imported) so this test file has no import-time coupling
# to test_gate_v2_contract.py's own module contents; the actual cross-file EQUALITY is
# still asserted directly against gate-v2.yml's parsed YAML below, not against these
# string constants alone.
FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
DRAFT_GUARD = "github.event.pull_request.draft != true"
RUNNER_GUARD = "inputs.runner == 'self'"
CLASSIFY_GUARD = "needs.classify_pr_paths.outputs.review_expected != 'false'"
PR_AUTHOR_GUARD = "toJSON(github.event.pull_request.user.login)"
CLASSIFY_JOB_ID = "classify_pr_paths"
CLASSIFY_SCRIPT = "_gate-classify-src/scripts/classify_pr_reviewable_paths.py"
CHECKOUT_ACTION = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
EXPECTED_ACTION_REFS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
}


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


def _resolve_policy_step():
    raw, _ = _load_workflow()
    return next(s for s in raw["jobs"]["resolve"]["steps"] if s.get("id") == "resolve-policy")


def _run_review_shadow_step():
    raw, _ = _load_workflow()
    return next(s for s in raw["jobs"]["shadow"]["steps"] if s.get("name") == "Run review-shadow")


def _materialize_gha(run: str, values: dict[str, str]) -> str:
    def repl(match):
        key = " ".join(match.group(1).split())
        if key not in values:
            raise AssertionError(f"unsubstituted GHA expression {key!r}")
        return values[key]
    return _GHA_EXPRESSION.sub(repl, run)


def _parse_github_output(payload: bytes) -> dict[str, str]:
    parsed: dict[str, str] = {}
    remaining = payload
    while remaining:
        heredoc = _GITHUB_OUTPUT_HEREDOC.match(remaining)
        if heredoc:
            parsed[heredoc.group(1).decode()] = heredoc.group(3).decode()
            remaining = remaining[heredoc.end():]
            continue
        line, sep, remaining = remaining.partition(b"\n")
        if not line:
            if not sep:
                break
            continue
        if b"=" not in line:
            raise AssertionError(f"unparsed GITHUB_OUTPUT line: {line!r}")
        key, value = line.split(b"=", 1)
        parsed[key.decode()] = value.decode()
    return parsed


def _install_hub(directory: Path, *, consumer: Path, commit: str) -> Path:
    hub = directory / "gate-hub"
    review_dir = hub / "scripts" / "review"
    review_dir.mkdir(parents=True)
    (hub / "registry.yaml").write_text("shadow: [pi-glm-quote]\n", encoding="utf-8")
    (hub / "REGISTRY_COMMIT").write_text(commit, encoding="utf-8")
    resolve_script = review_dir / "resolve_policy.py"
    resolve_script.write_text(_STUB_RESOLVE_POLICY, encoding="utf-8")
    resolve_script.chmod(0o755)
    target = review_dir / "review-shadow"
    target.write_bytes(consumer.read_bytes())
    target.chmod(0o755)
    return hub


def _run_extracted_resolve(tmp_path: Path, *, commit: str, fixture: Path | None = None,
                           write_commit: bool = True) -> subprocess.CompletedProcess:
    hub = _install_hub(tmp_path, consumer=NEW_SHADOW_CONSUMER, commit=commit)
    if not write_commit:
        (hub / "REGISTRY_COMMIT").unlink()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    env = {
        **os.environ,
        "GATE_HUB_DIR": str(hub),
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "RUNNER_TEMP": str(runner_temp),
        "GATE_TIER": "personal",
        "RESOLVE_POLICY_STDOUT_FIXTURE": str(fixture or RESOLVED_POLICY_FIXTURE),
    }
    script = _materialize_gha(_resolve_policy_step()["run"], {"github.repository": "zlxlabs/example"})
    return subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True, check=False, cwd=tmp_path)


def _run_extracted_leg(tmp_path: Path, *, consumer: Path, env_extra: dict[str, str]):
    hub = _install_hub(tmp_path, consumer=consumer, commit="a" * 40)
    capture = tmp_path / "consumer-capture.json"
    env = {**os.environ, "GATE_HUB_DIR": str(hub), "REVIEWER": "pi-glm-quote",
           "CAPTURE_PATH": str(capture), **env_extra}
    script = _materialize_gha(_run_review_shadow_step()["run"], {"github.event.pull_request.number": "42"})
    proc = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True, check=False, cwd=tmp_path)
    return proc, capture


# ── reusable workflow shape ──────────────────────────────────────────────────

def test_is_workflow_call_named_gate_shadow():
    raw, trigger = _load_workflow()
    assert "workflow_call" in trigger
    assert raw["name"] == "gate-shadow"


def test_shadow_v2_official_actions_are_exactly_sha_pinned():
    raw, _ = _load_workflow()
    actual = {}
    for job in raw["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if uses.startswith("actions/"):
                action, ref = uses.split("@", 1)
                actual.setdefault(action, set()).add(ref)
    assert actual == {action: {ref} for action, ref in EXPECTED_ACTION_REFS.items()}


def test_no_secrets_declared():
    # No notify job, no PR comment — this workflow needs no secret at all (contrast with
    # gate-v2.yml's optional FEISHU_CI_WEBHOOK).
    _, trigger = _load_workflow()
    assert "secrets" not in trigger["workflow_call"]


def test_all_three_jobs_present_and_no_notify_job():
    raw, _ = _load_workflow()
    assert set(raw["jobs"].keys()) == {"classify_pr_paths", "resolve", "shadow", "summary"}
    assert "notify" not in raw["jobs"]


def test_permissions_have_no_pr_or_issue_write_scope():
    raw, _ = _load_workflow()
    assert raw["permissions"] == {"actions": "read", "contents": "read"}


def test_shadow_workflow_has_no_internal_gate_self_references():
    assert "zlxlabs/gate/" not in WORKFLOW.read_text()


# ── concurrency contract: Shadow lifecycle group, isolated from Required Gate ─────

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
    # Required Gate leaves top-level concurrency unset so independent runs can
    # reach the ledger writer; Shadow Calibration keeps its own workflow lock.
    raw, _ = _load_workflow()
    shadow_group = str(raw["concurrency"]["group"])
    required_raw = _load_required_workflow()
    assert "concurrency" not in required_raw
    assert shadow_group.startswith("gate-shadow-v2-")


# ── resolve job: draft/fork/hosted guard byte-identical to gate-v2.yml's `primary` ──

def test_resolve_if_is_byte_identical_to_gate_v2_primary_if():
    raw, _ = _load_workflow()
    required_raw = _load_required_workflow()
    resolve_if = str(raw["jobs"]["resolve"].get("if", ""))
    primary_if = str(required_raw["jobs"]["primary"].get("if", ""))
    for guard in (DRAFT_GUARD, FORK_GUARD, RUNNER_GUARD, CLASSIFY_GUARD, PR_AUTHOR_GUARD):
        assert guard in resolve_if, f"resolve job if is missing {guard!r}"
    assert resolve_if == primary_if, (
        "gate-shadow-v2.yml's `resolve` job if: must be byte-for-byte identical to "
        "gate-v2.yml's `primary` job if:\n"
        f"resolve if:  {resolve_if!r}\n"
        f"primary if:  {primary_if!r}"
    )
    assert raw["jobs"]["resolve"]["needs"] == [CLASSIFY_JOB_ID]


def test_shadow_classify_job_matches_required_gate_classifier():
    raw, _ = _load_workflow()
    required_raw = _load_required_workflow()
    shadow_job = raw["jobs"][CLASSIFY_JOB_ID]
    required_job = required_raw["jobs"][CLASSIFY_JOB_ID]
    assert shadow_job.get("if") == "always()"
    assert required_job.get("if") == "always()"
    assert "needs" not in shadow_job
    assert shadow_job["runs-on"] == raw["jobs"]["resolve"]["runs-on"]
    assert required_job["runs-on"] == required_raw["jobs"]["gate"]["runs-on"]
    for runs_on in (shadow_job["runs-on"], required_job["runs-on"]):
        assert FORK_GUARD in runs_on
        assert "ubuntu-latest" in runs_on
    assert shadow_job["outputs"] == required_job["outputs"]
    shadow_classify = next(s for s in shadow_job["steps"] if s.get("id") == "classify")
    required_classify = next(s for s in required_job["steps"] if s.get("id") == "classify")
    assert shadow_classify["run"] == required_classify["run"]
    assert CLASSIFY_SCRIPT in shadow_classify["run"]
    assert 'echo "review_expected=true" >> "$GITHUB_OUTPUT"' in shadow_classify["run"]
    assert "review_expected=false" not in shadow_classify["run"]
    assert "compare/" in shadow_classify["run"]
    assert "pulls/" not in shadow_classify["run"]
    assert shadow_classify["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert shadow_classify["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    shadow_checkout = next(
        s for s in shadow_job["steps"]
        if s.get("name") == "Checkout classify script at this workflow's own commit"
    )
    required_checkout = next(
        s for s in required_job["steps"]
        if s.get("name") == "Checkout classify script at this workflow's own commit"
    )
    # Shadow stays on pinned actions/checkout (out of this card's files). Required
    # gate switched to bounded-retry helper; both still pin the same reusable
    # workflow identity and destination path.
    assert shadow_checkout["uses"] == CHECKOUT_ACTION
    assert shadow_checkout["with"]["repository"] == "${{ job.workflow_repository }}"
    assert shadow_checkout["with"]["ref"] == "${{ job.workflow_sha }}"
    assert shadow_checkout["with"]["path"] == "_gate-classify-src"
    required_env = required_checkout["env"]
    assert required_env["GATE_CHECKOUT_REPOSITORY"] == "${{ job.workflow_repository }}"
    assert required_env["GATE_CHECKOUT_REF"] == "${{ job.workflow_sha }}"
    assert required_env["GATE_CHECKOUT_PATH"] == "_gate-classify-src"
    assert 'python3 "${RUNNER_TEMP}/gate_bounded_retry.py" checkout' in required_checkout["run"]


def test_workflow_call_inputs_include_review_exempt_paths():
    raw, trigger = _load_workflow()
    assert set(trigger["workflow_call"]["inputs"]) == {
        "tier",
        "runner",
        "design_doc",
        "max_diff_lines",
        "max_review_shards",
        "shadow_timeout_minutes",
        "review_exempt_paths",
    }
    declared = trigger["workflow_call"]["inputs"]["review_exempt_paths"]
    assert declared["type"] == "string"
    assert declared["default"] == ""
    shadow_classify = next(
        s for s in raw["jobs"][CLASSIFY_JOB_ID]["steps"] if s.get("id") == "classify"
    )
    required_classify = next(
        s for s in _load_required_workflow()["jobs"][CLASSIFY_JOB_ID]["steps"]
        if s.get("id") == "classify"
    )
    assert shadow_classify["env"]["REVIEW_EXEMPT_PATHS"] == "${{ inputs.review_exempt_paths }}"
    assert required_classify["env"]["REVIEW_EXEMPT_PATHS"] == "${{ inputs.review_exempt_paths }}"
    assert "${{ inputs.review_exempt_paths }}" not in shadow_classify["run"]
    assert "${{ inputs.review_exempt_paths }}" not in required_classify["run"]


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


def test_shadow_workflow_has_no_gate_source_checkout_or_caller_quality_step():
    raw, _ = _load_workflow()
    gate_source_markers = ("job.workflow_repository", "job.workflow_sha", "_gate-action-src", "_gate-aggregator-src", "./_gate-")
    caller_quality_markers = (
        "scripts/gate-quality",
        "make lint",
        "npm test",
        "pytest",
        "uv sync",
        "pnpm install",
    )
    for job_name, job in raw["jobs"].items():
        for step in job["steps"]:
            text = f"{step.get('run', '')} {step.get('uses', '')}"
            if step.get("uses") == CHECKOUT_ACTION:
                assert not any(marker in text for marker in gate_source_markers), (
                    f"jobs.{job_name} must not checkout gate source into the caller workspace"
                )
            assert not any(marker in text for marker in caller_quality_markers), (
                f"jobs.{job_name} must not run caller quality checks"
            )


# ── resolve -> shadow matrix data flow, including the empty-list boundary ──────
#
# P1 fix (2026-07-26, codex review of an earlier draft of this workflow): GitHub Actions
# hard-errors ("Matrix vector does not contain any values") the instant a
# `strategy.matrix` dimension evaluates to an empty array `[]` — it does NOT degrade to a
# clean zero-job-runs skip the way a job-level `if:` false does. The fix is a sentinel
# value (`__none__`, never a legal reviewer identifier) `resolve` substitutes for an empty
# shadow list, plus a separate plain-boolean `has_shadows` output the `shadow`/`summary`
# jobs actually gate their own `if:` on — the tests below replace the old (buggy) `|| '[]'`
# fallback assertions with sentinel-aware ones.

def test_resolve_outputs_shadow_reviewers_and_has_shadows_from_resolve_policy_step():
    raw, _ = _load_workflow()
    resolve_job = raw["jobs"]["resolve"]
    assert resolve_job["outputs"]["shadow_reviewers"] == "${{ steps.resolve-policy.outputs.shadow_reviewers }}"
    assert resolve_job["outputs"]["has_shadows"] == "${{ steps.resolve-policy.outputs.has_shadows }}"
    steps = resolve_job["steps"]
    step = next(s for s in steps if s.get("id") == "resolve-policy")
    assert "resolve_policy.py" in step["run"]
    assert "shadow_reviewers={json.dumps(reviewers)}" in step["run"]
    assert "has_shadows={str(has_shadows).lower()}" in step["run"]
    # Fail-closed: no continue-on-error anywhere on this step — a PolicyError (or an
    # unsafe reviewer name — see the next test) must fail the `resolve` job outright,
    # never silently degrade to an empty shadow list.
    assert "continue-on-error" not in step


def test_resolve_policy_step_validates_every_reviewer_name_against_safe_identifier_regex():
    # P2 fix (2026-07-26, codex review): the reviewer names resolve_policy.py returns
    # cross from gate-hub's trusted-config domain into THIS workflow's own shell/jq/
    # artifact-name interpolation sites — re-validated here against the SAME
    # safe-identifier shape gate-hub's resolve_policy.py/review-shadow already enforce,
    # fail-closed on any violation, before any name reaches those sites.
    raw, _ = _load_workflow()
    step = next(s for s in raw["jobs"]["resolve"]["steps"] if s.get("id") == "resolve-policy")
    run = step["run"]
    assert 'SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")' in run
    assert "SAFE_NAME_RE.match(name)" in run
    assert "sys.exit(1)" in run


def test_resolve_policy_step_uses_reserved_sentinel_for_empty_shadow_list():
    raw, _ = _load_workflow()
    step = next(s for s in raw["jobs"]["resolve"]["steps"] if s.get("id") == "resolve-policy")
    run = step["run"]
    assert 'SENTINEL = "__none__"' in run
    assert "reviewers = shadow if has_shadows else [SENTINEL]" in run


def test_resolve_policy_step_has_gate_tier_env_for_shadow_tier_gates():
    # reliability.shadow-resolve-missing-gate-tier (gate-hub canary PR #73, 2026-07-27):
    # this step used to call resolve_policy.py with NO `GATE_TIER` in its process
    # environment at all, while the `shadow` job's own "Run review-shadow" step (a few
    # jobs down in the same file) DID thread `GATE_TIER: ${{ inputs.tier }}` into its
    # env. resolve_policy.py's resolve() reads GATE_TIER as plain ambient env to apply
    # `review_policy.rollout.shadow_tier_gates` (a per-reviewer tier allowlist); with no
    # GATE_TIER set here, effective_gate_tier was always the empty string, so ANY
    # reviewer gated by shadow_tier_gates was unconditionally dropped from the resolved
    # shadow list before the matrix below is even built — the job for that reviewer
    # never appears at all (indistinguishable from "not configured" from the outside).
    # This had never fired before gate-hub PR #72 added the first-ever shadow_tier_gates
    # entry (`ocr-minimax-m3`, personal-tier-only); PR #73's canary is the real-world
    # reproduction that caught it (gate-shadow ran green with 2 legs, silently missing
    # the 3rd). Locks the fix so a future edit of this step can't silently regress it.
    raw, _ = _load_workflow()
    step = next(s for s in raw["jobs"]["resolve"]["steps"] if s.get("id") == "resolve-policy")
    assert step.get("env", {}).get("GATE_TIER") == "${{ inputs.tier }}"


def test_shadow_matrix_fallback_is_sentinel_array_never_empty_array():
    # The safety net for "resolve was skipped -> needs.resolve.outputs.shadow_reviewers
    # is an empty string" MUST be a NON-EMPTY-array fallback inside fromJSON (an empty
    # `[]` fallback is the exact P1 bug — GitHub Actions errors on an empty matrix
    # dimension regardless of why it's empty), and must not rely on any assumption about
    # whether a job's `if:` is evaluated before its own `strategy.matrix` expression.
    raw, _ = _load_workflow()
    matrix_expr = str(raw["jobs"]["shadow"]["strategy"]["matrix"]["reviewer"])
    assert "fromJSON(needs.resolve.outputs.shadow_reviewers || '[\"__none__\"]')" in matrix_expr
    assert "|| '[]'" not in matrix_expr


def test_shadow_job_needs_resolve_and_gates_on_has_shadows():
    raw, _ = _load_workflow()
    shadow_job = raw["jobs"]["shadow"]
    assert shadow_job["needs"] == "resolve"
    assert str(shadow_job.get("if", "")) == "needs.resolve.result == 'success' && needs.resolve.outputs.has_shadows == 'true'"


def test_shadow_job_steps_each_carry_the_sentinel_guard():
    # Innermost defense-in-depth layer (see the `shadow` job's own `if:` comment): even a
    # hypothetical sentinel leg that somehow still started does nothing at all.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["shadow"]["steps"]
    guarded_step_names = {
        CHECKOUT_ACTION,
        "Resolve numeric job id for REVIEW_JOB_ID",
        "Run review-shadow",
    }
    seen = set()
    for step in steps:
        label = step.get("name") or step.get("uses")
        if label in guarded_step_names:
            seen.add(label)
            # The checkout also carries the service-mode guard; the sentinel must
            # still appear verbatim inside it.
            assert "matrix.reviewer != '__none__'" in str(step.get("if", "")), (
                f"step {label!r} must carry the sentinel guard verbatim, got {step.get('if')!r}"
            )
    assert seen == guarded_step_names

    upload = next(s for s in steps if s.get("name") == "Upload shadow calibration event")
    assert str(upload.get("if", "")) == "always() && matrix.reviewer != '__none__'"


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


def test_shadow_timeout_is_parameterized_with_unchanged_default_and_headroom():
    raw, _ = _load_workflow()
    inputs = _load_workflow()[1]["workflow_call"]["inputs"]
    assert inputs["shadow_timeout_minutes"] == {"type": "number", "default": 15}
    assert raw["jobs"]["shadow"]["timeout-minutes"] == "${{ inputs.shadow_timeout_minutes }}"

    steps = raw["jobs"]["resolve"]["steps"]
    setup = next(s for s in steps if s.get("name") == "Validate shadow timeout input")
    assert "if" not in setup
    assert setup["env"] == {"SHADOW_TIMEOUT_MINUTES": "${{ inputs.shadow_timeout_minutes }}"}
    run = setup["run"]
    assert "$(( (SHADOW_TIMEOUT_MINUTES - 2) * 60 ))" in run
    assert 'echo "review_gate_timeout_s=$shadow_internal_timeout_s" >> "$GITHUB_OUTPUT"' in run
    assert 'shadow_kill_grace_s=30' in run
    assert 'shadow_finalize_reserve_s=60' in run
    assert 'shadow_min_hop_budget_s=30' in run
    assert 'if [ "$shadow_available_hop_budget_s" -lt "$shadow_min_hop_budget_s" ]' in run
    assert raw["jobs"]["resolve"]["outputs"]["review_gate_timeout_s"] == "${{ steps.validate-shadow-timeout.outputs.review_gate_timeout_s }}"


def _run_shadow_timeout_setup(setup, value):
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / "github-env"
        output_file = Path(directory) / "github-output"
        env = {
            "SHADOW_TIMEOUT_MINUTES": value,
            "GITHUB_ENV": str(env_file),
            "GITHUB_OUTPUT": str(output_file),
        }
        result = subprocess.run(
            ["bash", "-c", setup["run"]], env=env, text=True,
            capture_output=True, check=False,
        )
        payload = output_file.read_bytes() if output_file.exists() else b""
        if not payload and env_file.exists():
            payload = env_file.read_bytes()
        return result, payload


def _timeout_validation_step(raw):
    for job_name in ("resolve", "shadow"):
        for step in raw["jobs"][job_name]["steps"]:
            if "SHADOW_TIMEOUT_MINUTES" in step.get("env", {}):
                return step
    raise AssertionError("shadow timeout validation step is missing")


def test_shadow_timeout_rejects_when_available_hop_budget_is_not_meaningful():
    raw, _ = _load_workflow()
    setup = _timeout_validation_step(raw)

    result, payload = _run_shadow_timeout_setup(setup, "3")

    assert result.returncode != 0
    assert payload == b""


def test_shadow_timeout_validation_runs_in_resolve_and_exports_one_shared_budget():
    raw, _ = _load_workflow()
    resolve_job = raw["jobs"]["resolve"]
    steps = resolve_job["steps"]
    validation = next(s for s in steps if s.get("name") == "Validate shadow timeout input")
    policy = next(s for s in steps if s.get("id") == "resolve-policy")

    assert "if" not in validation
    assert validation["env"] == {"SHADOW_TIMEOUT_MINUTES": "${{ inputs.shadow_timeout_minutes }}"}
    assert steps.index(validation) < steps.index(policy)
    assert resolve_job["outputs"]["review_gate_timeout_s"] == "${{ steps.validate-shadow-timeout.outputs.review_gate_timeout_s }}"


def test_shadow_timeout_setup_emits_default_and_larger_internal_budgets():
    raw, _ = _load_workflow()
    setup = _timeout_validation_step(raw)

    default_result, default_payload = _run_shadow_timeout_setup(setup, "15")
    larger_result, larger_payload = _run_shadow_timeout_setup(setup, "20")
    minimum_result, minimum_payload = _run_shadow_timeout_setup(setup, "4")

    assert default_result.returncode == 0
    assert default_payload == b"review_gate_timeout_s=780\n"
    assert larger_result.returncode == 0
    assert larger_payload == b"review_gate_timeout_s=1080\n"
    assert minimum_result.returncode == 0
    assert minimum_payload == b"review_gate_timeout_s=120\n"


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "abc", "15.5", "61", "", "0003", "0008", "09", "1e2", "9" * 42],
)
def test_shadow_timeout_setup_rejects_invalid_or_excessive_values(value):
    raw, _ = _load_workflow()
    setup = _timeout_validation_step(raw)

    result, payload = _run_shadow_timeout_setup(setup, value)

    assert result.returncode != 0
    assert payload == b""


def test_shadow_upload_step_name_pattern_matches_summary_download_pattern():
    raw, _ = _load_workflow()
    shadow_steps = raw["jobs"]["shadow"]["steps"]
    upload = next(s for s in shadow_steps if s.get("name") == "Upload shadow calibration event")
    assert upload["if"] == "always() && matrix.reviewer != '__none__'"
    assert upload["uses"] == UPLOAD_ARTIFACT_ACTION
    upload_name = str(upload["with"]["name"])
    assert upload_name == "shadow-event-${{ matrix.reviewer }}-${{ github.run_id }}-${{ github.run_attempt }}"
    # P1 fix (2026-07-26, canary probe #2): must be explicit `error`, not the earlier
    # draft's `warn` — the D2 plan's actual invariant is "成功 leg 必有事件" (a leg that
    # reads as `success` must have a real uploaded event), which `warn` (an annotation,
    # not a failure) does not enforce.
    assert upload["with"]["if-no-files-found"] == "error"

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
    # prefixed by the calling job's own name — assert the jq selector accounts for this
    # suffix. `matrix.reviewer` is routed through the `REVIEWER` env var and composed
    # into JOB_NAME_SUFFIX in shell (P2 hygiene fix, 2026-07-26 codex review) then passed
    # to jq via --arg (never jq env.* — missing env vars silently match nothing).
    raw, _ = _load_workflow()
    steps = raw["jobs"]["shadow"]["steps"]
    step = next(s for s in steps if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID")
    assert step["env"]["REVIEWER"] == "${{ matrix.reviewer }}"
    run = step["run"]
    assert 'JOB_NAME_SUFFIX="${{ github.job }} ($REVIEWER)"' in run
    assert "jq -r --arg suffix" in run
    assert 'endswith("/ " + $suffix)' in run
    assert "env.JOB_NAME_SUFFIX" not in run
    assert "matching name is empty because REVIEWER is unset" in run
    assert "Jobs API call failed" in run
    assert "no matching job for JOB_NAME_SUFFIX=" in run
    assert "truncated" in run
    assert "${{ matrix.reviewer }}" not in run


def test_run_review_shadow_env_has_required_v2_identity_vars():
    raw, _ = _load_workflow()
    steps = raw["jobs"]["shadow"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Run review-shadow")
    env = run_step["env"]
    assert env["REVIEW_JOB_ID"] == "${{ steps.resolve-job-id.outputs.job_id }}"
    assert env["REVIEW_CALLER_SHA"] == "${{ github.workflow_sha }}"
    assert env["REVIEW_REUSABLE_WORKFLOW_SHA"] == "${{ job.workflow_sha }}"
    assert env["REVIEW_RUN_MODE"] == "PAYLOAD_ONLY"
    # P2 hygiene fix (2026-07-26 codex review): matrix.reviewer routed through env:,
    # referenced in the run: script as "$REVIEWER", never interpolated directly.
    assert env["REVIEWER"] == "${{ matrix.reviewer }}"
    assert env["REVIEW_RESOLVED_POLICY_JSON"] == "${{ needs.resolve.outputs.resolved_policy_json }}"
    assert env["REVIEW_REGISTRY_COMMIT"] == "${{ needs.resolve.outputs.registry_commit }}"
    assert "review-shadow" in run_step["run"]
    assert '"${{ github.event.pull_request.number }}"' in run_step["run"]
    assert '"$REVIEWER"' in run_step["run"]
    assert "${{ matrix.reviewer }}" not in run_step["run"]
    assert env["REVIEW_GATE_TIMEOUT_S"] == "${{ needs.resolve.outputs.review_gate_timeout_s }}"


def test_run_review_shadow_invokes_python3_not_bash():
    # P0 fix (2026-07-26, canary probe #2) — same bug class and fix as
    # test_gate_v2_contract.py's test_run_review_primary_invokes_python3_not_bash: an
    # earlier draft invoked review-shadow (a Python entry point) via `bash
    # "$GATE_HUB_DIR/..."`, which fed Python source to bash and failed with
    # `import: command not found`.
    raw, _ = _load_workflow()
    steps = raw["jobs"]["shadow"]["steps"]
    run_step = next(s for s in steps if s.get("name") == "Run review-shadow")
    run = run_step["run"].strip()
    invoke = [line.strip() for line in run.splitlines() if "scripts/review/review-shadow" in line]
    assert invoke, run
    assert invoke[0].startswith("python3 "), f"expected an explicit python3 invocation, got: {invoke[0]!r}"
    assert not invoke[0].startswith("bash ")
    assert "--require-resolved-policy" in invoke[0]


# ── summary job: always() + explicit non-empty guard, no PR-write permission ───

def test_summary_job_needs_resolve_and_shadow():
    raw, _ = _load_workflow()
    needs = raw["jobs"]["summary"]["needs"]
    assert set(needs if isinstance(needs, list) else [needs]) == {"resolve", "shadow"}


def test_summary_if_is_always_plus_has_shadows_guard():
    # Tightened, D1-style: `always()` alone is not enough — review-summary itself
    # requires >=1 expected reviewer argv, so this job must ALSO skip cleanly whenever
    # `resolve` produced an empty/absent shadow list (draft/fork/hosted skip, OR a
    # same-repo trusted PR whose resolved policy legitimately has zero shadow
    # reviewers). `has_shadows` (same boolean the `shadow` job's own `if:` gates on) is
    # the single source of truth for both triggers — assert the full string, not just
    # substring presence, since this is the exact expression GitHub Actions evaluates.
    raw, _ = _load_workflow()
    summary_if = str(raw["jobs"]["summary"].get("if", ""))
    assert summary_if == "always() && needs.resolve.outputs.has_shadows == 'true'"


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
    assert "shadow leg(s) failed" in run
    assert "cancelled or skipped" in run
    assert run_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    env = run_step["env"]
    assert env["REVIEW_SUMMARY_REPOSITORY_ID"] == "${{ github.repository_id }}"
    assert env["REVIEW_SUMMARY_HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    # Third of the three review-* entry points audited for the P0 bash-vs-python3 fix
    # (2026-07-26, canary probe #2) — review-summary is also a Python entry point;
    # confirmed this invocation was already correct (never touched by that fix).
    assert 'python3 "$gate_hub_dir/scripts/review/review-summary"' in run
    assert not any(ln.strip().startswith("bash ") for ln in run.splitlines())


# ── axis 1: job id resolution input shape × expected behavior (contract pins) ─

@pytest.mark.parametrize(
    "needle,description,forbidden",
    [
        ("jq -r --arg suffix", "pass suffix via jq --arg, not env builtin", False),
        ("matching name is empty because REVIEWER is unset", "empty match name: REVIEWER unset", False),
        ("matching name is empty because github.job is unset", "empty match name: github.job unset", False),
        ("Jobs API call failed", "API failure distinct from no-match", False),
        ("|| rc=$?", "API failure captures gh exit code via || not inverted if", False),
        ("(exit=${rc})", "API failure echoes captured gh exit code", False),
        ("no matching job for JOB_NAME_SUFFIX=", "no-match lists actual suffix", False),
        ("candidate job names", "no-match lists API candidate names", False),
        ("truncated", "candidate names bounded with truncation note", False),
        ('first(.jobs[] | select(.name == $suffix', "exact or suffix match via --arg", False),
        ("err_preview", "API failure does not echo raw gh stderr", True),
        ("api_err", "API failure does not use stderr scratch file", True),
        ("if ! api_json=", "API failure does not use inverted-if exit capture", True),
    ],
)
def test_resolve_job_id_step_pins_fail_loud_diagnostics(needle, description, forbidden):
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"]["shadow"]["steps"]
        if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID"
    )
    run = step["run"]
    if forbidden:
        assert needle not in run, description
    else:
        assert needle in run, description


def test_shadow_resolve_jobs_api_failure_probe_reports_exit_42(tmp_path):
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"]["shadow"]["steps"]
        if s.get("name") == "Resolve numeric job id for REVIEW_JOB_ID"
    )
    snippet = materialize_jobs_api_snippet_for_probe(
        step["run"],
        start_prefix="jobs_api=",
        end_prefix='if [ -z "$api_json" ]',
    )
    proc = probe_jobs_api_failure_exit_code(snippet, tmp_path)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "exit=42" in output, output


def test_shadow_job_id_resolution_retries_with_timeout():
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"]["shadow"]["steps"]
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


def test_shadow_job_id_resolution_separates_api_failure_from_empty_result():
    raw, _ = _load_workflow()
    step = next(
        s for s in raw["jobs"]["shadow"]["steps"]
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


# ── axis 2: shadow leg outcomes × summary conclusion (contract pins) ─────────

@pytest.mark.parametrize(
    "needle,description,forbidden",
    [
        ("success_count=0", "tracks successful shadow legs", False),
        ("failed_count", "tracks failed shadow legs", False),
        ("cancelled_skipped_count", "tracks cancelled/skipped legs separately from failures", False),
        ("shadow leg(s) failed", "all-failed path must not pass silently", False),
        ("cancelled or skipped", "all-cancelled/skipped path distinguishable from all-failed", False),
        ("(exit=${rc})", "summary Jobs API failure echoes captured gh exit code", False),
        ("|| rc=$?", "summary Jobs API failure captures gh exit code via || not inverted if", False),
        ("err_preview", "summary Jobs API failure does not echo raw gh stderr", True),
        ("api_err", "summary Jobs API failure does not use stderr scratch file", True),
        ("if ! api_json=", "summary Jobs API failure does not use inverted-if exit capture", True),
    ],
)
def test_summary_step_pins_shadow_leg_outcome_guards(needle, description, forbidden):
    raw, _ = _load_workflow()
    run = next(
        s for s in raw["jobs"]["summary"]["steps"]
        if s.get("name") == "Summarize shadow calibration results"
    )["run"]
    if forbidden:
        assert needle not in run, description
    else:
        assert needle in run, description


def test_shadow_summary_jobs_api_failure_probe_reports_exit_42(tmp_path):
    raw, _ = _load_workflow()
    run = next(
        s for s in raw["jobs"]["summary"]["steps"]
        if s.get("name") == "Summarize shadow calibration results"
    )["run"]
    snippet = materialize_jobs_api_snippet_for_probe(
        run,
        start_prefix="jobs_api=",
        end_prefix="success_count=0",
    )
    proc = probe_jobs_api_failure_exit_code(snippet, tmp_path)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "exit=42" in output, output


def test_no_job_grants_pull_request_or_issue_write():
    # Belt-and-suspenders alongside the top-level permissions test: no job in this file
    # declares its own (more permissive) job-level `permissions:` override either.
    raw, _ = _load_workflow()
    for job in raw["jobs"].values():
        assert "permissions" not in job


def test_no_run_block_directly_interpolates_matrix_reviewer():
    # P2 hygiene fix (2026-07-26 codex review): GitHub Actions substitutes `${{ ... }}`
    # expressions into a `run:` step's script text BEFORE bash/jq ever parses it — direct
    # interpolation of `${{ matrix.reviewer }}` into a `run:` block is the textbook
    # GitHub-Actions script-injection pattern. Every use of the reviewer name inside a
    # `run:` script in this file must go through an `env:`-declared variable instead
    # (`$REVIEWER` in shell, jq `--arg suffix` — never jq `env.*`) — scan every
    # step's `run:` field in this workflow (not `if:`/`with:`, which are GitHub Actions'
    # own expression contexts, not shell/jq script text, and carry no equivalent risk).
    raw, _ = _load_workflow()
    offenders = []
    for job_id, job in raw["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run")
            if run and "${{ matrix.reviewer }}" in run:
                offenders.append(f"{job_id}::{step.get('name', step.get('id', '<unnamed>'))}")
    assert not offenders, f"run: block(s) directly interpolate matrix.reviewer: {offenders!r}"


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


# ── resolved policy snapshot producer / consumer contract (card C) ─────────────


def test_snapshot_contract_wiring_and_no_gate_v2_change():
    text = REQUIRED_WORKFLOW.read_text()
    assert "--require-resolved-policy" not in text
    assert "REVIEW_RESOLVED_POLICY_JSON" not in text
    outputs = _load_workflow()[0]["jobs"]["resolve"]["outputs"]
    assert outputs["resolved_policy_json"] == "${{ steps.resolve-policy.outputs.resolved_policy_json }}"
    assert outputs["registry_commit"] == "${{ steps.resolve-policy.outputs.registry_commit }}"
    run = _resolve_policy_step()["run"]
    assert run.count("scripts/review/resolve_policy.py") == 1
    assert "github.sha" not in run and "GITHUB_SHA" not in run and "rev-parse" not in run
    assert "resolved_policy_json<<RESOLVED_POLICY_JSON_EOF" in run
    assert 'dirname "$registry_file")/REGISTRY_COMMIT' in run
    step = _run_review_shadow_step()
    assert step["env"]["REVIEW_RESOLVED_POLICY_JSON"] == "${{ needs.resolve.outputs.resolved_policy_json }}"
    assert step["env"]["REVIEW_REGISTRY_COMMIT"] == "${{ needs.resolve.outputs.registry_commit }}"
    invoke = [ln.strip() for ln in step["run"].splitlines() if "scripts/review/review-shadow" in ln]
    assert invoke and invoke[0].startswith("python3 ")
    assert "--require-resolved-policy" in invoke[0]
    assert step["run"].index("python3 ") < step["run"].index("--require-resolved-policy") < step["run"].index("${{ github.event.pull_request.number }}")


def test_resolve_shell_producer_writes_full_json_github_output_bytes(tmp_path):
    commit = "cafebabecafebabecafebabecafebabecafebabe"
    proc = _run_extracted_resolve(tmp_path, commit=commit)
    payload = (tmp_path / "github-output").read_bytes()
    assert proc.returncode == 0, proc.stderr
    parsed = _parse_github_output(payload)
    fixture = json.loads(RESOLVED_POLICY_FIXTURE.read_text(encoding="utf-8"))
    roundtrip = json.loads(parsed["resolved_policy_json"])
    assert roundtrip == fixture
    model = roundtrip["reviewers"]["pi-glm-quote"]["model"]
    assert '"' in model and "\n" in model
    assert roundtrip["reviewers"]["pi-glm-quote"]["auth"] == "glm-profile"
    assert parsed["registry_commit"] == commit
    assert json.loads(parsed["shadow_reviewers"]) == ["pi-glm-quote"]
    assert parsed["has_shadows"] == "true"
    assert not {"target_repo", "tier", "repository"} & set(roundtrip)
    assert RESOLVED_POLICY_FIXTURE.read_text(encoding="utf-8").strip().encode() in payload


def test_resolve_shell_producer_fail_fast_on_missing_or_invalid_registry_commit(tmp_path):
    missing = _run_extracted_resolve(tmp_path / "missing", commit="a" * 40, write_commit=False)
    empty = _run_extracted_resolve(tmp_path / "empty", commit="\n")
    short = _run_extracted_resolve(tmp_path / "short", commit="abc")
    assert missing.returncode and empty.returncode and short.returncode
    assert "REGISTRY_COMMIT" in (missing.stderr + missing.stdout)


def test_extracted_leg_passes_full_env_and_real_pr_reviewer_to_dummy_consumer(tmp_path):
    fixture = json.loads(RESOLVED_POLICY_FIXTURE.read_text(encoding="utf-8"))
    commit = "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
    proc, capture = _run_extracted_leg(
        tmp_path, consumer=NEW_SHADOW_CONSUMER,
        env_extra={"REVIEW_RESOLVED_POLICY_JSON": json.dumps(fixture, ensure_ascii=False),
                   "REVIEW_REGISTRY_COMMIT": commit},
    )
    assert proc.returncode == 0, proc.stderr
    dumped = json.loads(capture.read_text(encoding="utf-8"))
    assert dumped["argv"] == ["--require-resolved-policy", "42", "pi-glm-quote"]
    assert dumped["policy"] == fixture and dumped["registry_commit"] == commit


def test_missing_or_bad_snapshot_does_not_invoke_or_fails_closed(tmp_path):
    fixture = RESOLVED_POLICY_FIXTURE.read_text(encoding="utf-8")
    cases = [
        ({"REVIEW_REGISTRY_COMMIT": "b" * 40}, True),
        ({"REVIEW_RESOLVED_POLICY_JSON": "", "REVIEW_REGISTRY_COMMIT": "b" * 40}, True),
        ({"REVIEW_RESOLVED_POLICY_JSON": "{not-json", "REVIEW_REGISTRY_COMMIT": "c" * 40}, True),
        ({"REVIEW_RESOLVED_POLICY_JSON": fixture, "REVIEW_REGISTRY_COMMIT": "not-a-sha"}, True),
    ]
    for i, (extra, expect_no_capture) in enumerate(cases):
        proc, capture = _run_extracted_leg(tmp_path / f"c{i}", consumer=NEW_SHADOW_CONSUMER, env_extra=extra)
        assert proc.returncode != 0
        if expect_no_capture:
            assert not capture.exists()
    direct = subprocess.run(
        ["python3", str(NEW_SHADOW_CONSUMER), "--require-resolved-policy", "--extra", "42", "pi-glm-quote"],
        env={**os.environ, "REVIEW_RESOLVED_POLICY_JSON": fixture, "REVIEW_REGISTRY_COMMIT": "d" * 40},
        text=True, capture_output=True, check=False,
    )
    assert direct.returncode != 0 and "unknown flag" in direct.stderr


def test_stale_local_registry_after_resolve_does_not_change_leg_payload(tmp_path):
    commit = "1234567890abcdef1234567890abcdef12345678"
    proc = _run_extracted_resolve(tmp_path, commit=commit)
    assert proc.returncode == 0, proc.stderr
    parsed = _parse_github_output((tmp_path / "github-output").read_bytes())
    original = json.loads(parsed["resolved_policy_json"])
    (tmp_path / "gate-hub" / "registry.yaml").write_text("shadow: [stale-reviewer]\n", encoding="utf-8")
    leg_dir = tmp_path / "leg"
    leg_dir.mkdir()
    leg_proc, capture = _run_extracted_leg(
        leg_dir, consumer=NEW_SHADOW_CONSUMER,
        env_extra={"REVIEW_RESOLVED_POLICY_JSON": parsed["resolved_policy_json"],
                   "REVIEW_REGISTRY_COMMIT": parsed["registry_commit"]},
    )
    assert leg_proc.returncode == 0, leg_proc.stderr
    dumped = json.loads(capture.read_text(encoding="utf-8"))
    assert dumped["policy"] == original and dumped["policy"]["shadow"] == ["pi-glm-quote"]


def test_old_shadow_consumer_rejects_new_preamble_flag():
    env = {**os.environ, "REVIEW_RESOLVED_POLICY_JSON": RESOLVED_POLICY_FIXTURE.read_text(encoding="utf-8"),
           "REVIEW_REGISTRY_COMMIT": "e" * 40}
    argv = ["--require-resolved-policy", "42", "pi-glm-quote"]
    proc = subprocess.run(["python3", str(OLD_SHADOW_CONSUMER), *argv], env=env, text=True, capture_output=True, check=False)
    assert proc.returncode != 0 and "usage: review-shadow" in proc.stderr
    live = Path("/home/zlx/projects/personal/gate-hub/scripts/review/review-shadow")
    if live.is_file():
        live_proc = subprocess.run(["python3", str(live), *argv], text=True, capture_output=True, check=False)
        assert live_proc.returncode != 0 and "usage: review-shadow" in live_proc.stderr

