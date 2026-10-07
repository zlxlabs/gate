"""C2b shadow contract: the `arbiter` job cannot move a gate verdict, and the
cross-family arbitration only runs on a countable, still-blocking round.

261006-review-precision C2b (design: gate-hub
docs/sessions/261006-review-precision/c2-arbiter-design.md, 方案 3/5).  The shadow
period exists to COLLECT real arbitration records (`ran=true` in the ARBITER-SHADOW
line, >= 30 for C2on) and must not become a second decision path.  Three invariants
are locked by construction:

1. no job consumes `arbiter`, and every PRE-EXISTING job definition stays
   byte-identical to the base commit (one content digest per job in
   tests/fixtures/gate-v2-base-jobs.json);
2. the job carries `continue-on-error: true`;
3. the arbitration step runs only when the round history was countable — its own
   `if:` references `state`, so it is never reached on `history_unavailable`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
BASE_JOBS = REPO_ROOT / "tests" / "fixtures" / "gate-v2-base-jobs.json"
ARBITER_JOB = "arbiter"
SHADOW_MIN_PRIOR_ROUNDS = 2
ARBITER_FIELDS = (
    "prior_rounds", "limit", "state", "ran", "skip_reason",
    "arbiter", "primary_family", "arbiter_family",
    "uphold", "overturn", "undetermined", "unavailable_reason",
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def test_no_job_consumes_the_arbiter():
    jobs = _workflow()["jobs"]
    assert ARBITER_JOB in jobs
    for job_name, job in jobs.items():
        if job_name == ARBITER_JOB:
            continue
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else list(needs)
        assert ARBITER_JOB not in needs, (
            f"jobs.{job_name} must not depend on the shadow arbiter: its result "
            "would become a gate input (invariant 1)"
        )


def test_arbiter_job_is_soft_failed():
    job = _workflow()["jobs"][ARBITER_JOB]
    assert job.get("continue-on-error") is True, (
        "the arbiter job must carry a job-level continue-on-error so neither its "
        "failure nor a slow model leg can change the run's conclusion (invariant 1)"
    )


def test_every_pre_existing_job_is_unchanged_from_the_base_commit():
    fixture = json.loads(BASE_JOBS.read_text(encoding="utf-8"))
    jobs = _workflow()["jobs"]
    baseline = fixture["jobs"]
    assert set(jobs) - {ARBITER_JOB} == set(baseline), (
        "the job set changed beyond the new arbiter job; if that is deliberate, "
        f"regenerate {BASE_JOBS.relative_to(REPO_ROOT)} from the new base commit"
    )
    for job_name, digest in baseline.items():
        canonical = json.dumps(jobs[job_name], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == digest, (
            f"jobs.{job_name} drifted from {fixture['base_workflow']}@{fixture['base_commit']}: "
            f"this card may only ADD the arbiter job. If the edit is deliberate, regenerate "
            f"{BASE_JOBS.relative_to(REPO_ROOT)}."
        )


def test_arbiter_runs_only_when_primary_actually_ran():
    raw = _workflow()
    arbiter = raw["jobs"][ARBITER_JOB]
    assert arbiter["needs"] == ["classify_pr_paths", "primary"]
    condition = str(arbiter["if"])
    # Without a status-check function GitHub implies success() and would skip this job
    # exactly when it is needed: after a failing primary review.
    assert "!cancelled()" in condition
    assert "needs.primary.result != 'skipped'" in condition
    for token in (
        "github.event.pull_request.draft != true",
        "github.event.pull_request.head.repo.full_name == github.repository",
        "inputs.runner == 'self'",
        "needs.classify_pr_paths.outputs.review_expected != 'false'",
        "toJSON(github.event.pull_request.user.login)",
    ):
        assert token in condition, token
    assert arbiter["runs-on"] == raw["jobs"]["primary"]["runs-on"]
    assert arbiter["timeout-minutes"] == 15


def test_arbiter_uses_the_same_source_bootstrap_and_primary_sparse_list():
    raw = _workflow()
    arbiter = raw["jobs"][ARBITER_JOB]
    tools = _step(arbiter, "Checkout gate tools at this workflow's own commit")
    primary_tools = _step(raw["jobs"]["primary"], "Inject previous-round findings into primary review context")
    assert '"$GATE_SOURCE_BOOTSTRAP_SCRIPT"' in tools["run"]
    assert 'python3 "${RUNNER_TEMP}/gate_bounded_retry.py" checkout' in tools["run"]
    assert tools["env"]["GATE_CHECKOUT_SPARSE"] == primary_tools["env"]["GATE_CHECKOUT_SPARSE"]
    dns_run = _step(arbiter, "Resolve Silo hostname via MagicDNS")["run"]
    # The tailnet nameserver literal itself is asserted by
    # test_gate_v2_contract.py::test_silo_touching_jobs_resolve_magicdns_before_s3,
    # which now covers this job too.
    assert "magicdns" in dns_run and "/etc/hosts" in dns_run
    assert '"$GATE_CHECKOUT_MIRROR_SCRIPT"' in _step(arbiter, "Prime caller checkout from host Git mirror")["run"]


def test_arbiter_prior_rounds_reuses_the_aggregate_identity_mode():
    step = _step(_workflow()["jobs"][ARBITER_JOB], "Compute prior rounds and this round's blocking findings")
    run = step["run"]
    assert "--prior-rounds" in run
    for flag in ("--repository-id", "--repository", "--pr-number", "--run-id", "--run-attempt", "--tier", "--audit-dir", "--out"):
        assert flag in run, flag
    assert "_gate-silo-src/.github/actions/gate-aggregator/aggregate.py" in run
    for env_key in ("REPOSITORY_ID", "PR_NUMBER", "RUN_ID", "RUN_ATTEMPT", "TIER", "GH_TOKEN"):
        assert step["env"][env_key], env_key


def test_history_unavailable_never_runs_the_arbitration():
    job = _workflow()["jobs"][ARBITER_JOB]
    decide = _step(job, "Decide whether the arbiter runs")
    arbitration = _step(job, "Run cross-family arbitration (shadow only)")
    assert decide["if"] == "always()"
    assert 'state == "history_unavailable"' in decide["run"]
    assert f"MIN_PRIOR_ROUNDS = {SHADOW_MIN_PRIOR_ROUNDS}" in decide["run"]
    assert 'reason = False, "no_active_blocker"' in decide["run"]
    # The step that spends the model call references the history state itself, not only
    # the decision output (defence in depth for invariant 3).
    condition = str(arbitration["if"])
    assert condition.startswith("${{ always() && ")
    assert "steps.decide.outputs.state != 'history_unavailable'" in condition
    assert "steps.decide.outputs.run == 'true'" in condition


def test_arbitration_invocation_matches_the_review_arbiter_cli():
    job = _workflow()["jobs"][ARBITER_JOB]
    step = _step(job, "Run cross-family arbitration (shadow only)")
    run = step["run"]
    assert '"${GATE_HUB_DIR:-/opt/gate-hub}/scripts/review/review-arbiter"' in run
    for flag in ("--primary-audit", "--out", "--prior-rounds"):
        assert flag in run, flag
    env = step["env"]
    assert env["REVIEW_RUN_MODE"] == "PAYLOAD_ONLY"
    assert env["GATE_TIER"] == "${{ inputs.tier }}"
    assert env["PRIMARY_AUDIT_PATH"] == "${{ runner.temp }}/primary-audit/primary-review-audit.json"
    assert env["ARBITER_AUDIT_PATH"] == "${{ runner.temp }}/arbiter/arbiter-audit.json"
    # The model budget must fit INSIDE the job timeout with a finalize reserve,
    # mirroring primary's (timeout - 5 minutes) shape.
    assert int(env["REVIEW_GATE_TIMEOUT_S"]) == (job["timeout-minutes"] - 5) * 60
    assert int(env["REVIEW_TIMEOUT_S"]) < int(env["REVIEW_GATE_TIMEOUT_S"])


def test_arbiter_audit_upload_uses_the_primary_audit_bucket_and_naming():
    step = _step(_workflow()["jobs"][ARBITER_JOB], "Upload arbiter audit to Silo")
    assert step["if"] == "${{ always() && steps.arbiter.outputs.audit == 'present' }}"
    assert step["env"]["ARTIFACT_NAME"] == (
        "arbiter-audit-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}"
        "-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert '--tier d14' in step["run"]
    assert '"$SILO_EXEC" put' in step["run"]


def test_arbiter_logs_one_greppable_line_with_every_required_field():
    step = _step(_workflow()["jobs"][ARBITER_JOB], "Log arbiter shadow outcome")
    assert step["if"] == "always()"
    assert "ARBITER-SHADOW: " in step["run"]
    assert '"$GITHUB_STEP_SUMMARY"' in step["run"]
    for field in ARBITER_FIELDS:
        assert f"{field}=" in step["run"], field
    # Every run leaves exactly one line, including the "no audit" case.
    assert "arbiter_audit_missing" in step["run"]
    for env_key in ("RAN", "PRIOR_ROUNDS", "LIMIT", "STATE", "SKIP_REASON", "ARBITER_AUDIT_PATH"):
        assert env_key in step["env"], env_key
