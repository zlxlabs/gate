"""Domain-specific Required Gate v2 contract tests."""
from test_gate_v2_contract import BOUNDED_RETRY_HELPER, DISPOSITION_CALLER_PIN, DISPOSITION_CALLER_TEMPLATE, DISPOSITION_CHECKOUT_SUBDIR, DISPOSITION_PREFLIGHT_ERROR, DISPOSITION_PRODUCER_SPARSE, DISPOSITION_WORKFLOW, EVIDENCE_INPUT_ENV, LEGACY_FIVE_INPUTS, MAGICDNS_HELPER_RUN, PREFLIGHT_STEP_NAME, REPO_ROOT, VALID_COUNTEREVIDENCE, _checkout_shaped_workspace, _disposition_checkout_step, _disposition_scope_python, _disposition_step, _input_env, _load_disposition_caller, _load_disposition_workflow, _load_workflow, _preflight_step, _run_preflight, _run_receipt_step, _seed_caller_head, _sparse_env_paths, assert_workflow_sha_checkout, json, os, pytest, re, subprocess, sys

def test_disposition_workflow_is_protected_and_cannot_publish_gate_result():
    raw, trigger = _load_disposition_workflow()
    assert raw["name"] == "gate-v2 disposition control"
    assert "workflow_dispatch" in trigger
    assert "workflow_call" in trigger
    assert raw["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    control = raw["jobs"]["control"]
    assert control["environment"] == {"name": "gate-disposition"}
    expected_inputs = {
        "pr_number", "primary_run_id", "primary_run_attempt", "finding_id", "reason",
        "disposition", "counterevidence_json", "tracking_issue", "gate_ref",
    }
    assert "operation" not in trigger["workflow_dispatch"]["inputs"]
    assert "repository_id" not in trigger["workflow_dispatch"]["inputs"]
    assert "epoch" not in trigger["workflow_dispatch"]["inputs"]
    assert set(trigger["workflow_dispatch"]["inputs"]) == expected_inputs
    assert set(trigger["workflow_call"]["inputs"]) == expected_inputs
    for kind in ("workflow_dispatch", "workflow_call"):
        gate_ref = trigger[kind]["inputs"]["gate_ref"]
        assert gate_ref["required"] is False
        assert gate_ref["type"] == "string"
        for name in ("disposition", "counterevidence_json", "tracking_issue"):
            assert trigger[kind]["inputs"][name]["required"] is False
            assert trigger[kind]["inputs"][name]["type"] == "string"
    text = DISPOSITION_WORKFLOW.read_text()
    assert "issue_receipt.py issue" in text
    assert "issue_receipt.py revoke" not in text
    assert "counterevidence_json" in text
    assert "tracking_issue" in text
    assert "GITHUB_ACTOR" not in text
    assert "pull-requests: write" not in text
    assert "checks: write" not in text
    assert "statuses: write" not in text
    assert "gate/gate" not in text
    assert "check-runs" not in text
    upload = next(step for step in control["steps"] if step.get("name") == "Upload immutable disposition artifact")
    assert "uses" not in upload
    assert "--tier d30" in upload["run"]
    assert "put-dir" in upload["run"]
    resolve = next(step for step in control["steps"] if step.get("name") == "Resolve current PR head and canonical primary audit")
    assert resolve["env"]["GH_REPO"] == "${{ github.repository }}"
    assert 'audit_name="primary-audit-v2-${GITHUB_REPOSITORY_ID}-${head_sha}-${PRIMARY_RUN_ID}-${audit_attempt}"' in resolve["run"]
    assert 'silo_key="d14/${GITHUB_REPOSITORY_ID}/${audit_name}/primary-review-audit.json"' in resolve["run"]
    assert '"$SILO_EXEC" get' in resolve["run"]
    assert resolve["run"].index("$SILO_EXEC") < resolve["run"].index("gh run download")
    assert 'if [ "$silo_rc" -eq 2 ]; then' in resolve["run"]
    assert 'gh run download -R "$GITHUB_REPOSITORY" "$PRIMARY_RUN_ID" --name "$audit_name"' in resolve["run"]
    issue = next(step for step in control["steps"] if step.get("name") == "Issue immutable disposition artifact")
    assert '--scope-json "$CURRENT_SCOPE_JSON"' in issue["run"]
    assert issue["env"]["DISPOSITION_APPROVER"] == "${{ github.actor }}"
    assert issue["env"]["DISPOSITION_APPROVER_ID"] == "${{ github.actor_id }}"
    assert "github.actor" in issue["env"]["DISPOSITION_APPROVER"]
    assert "github.triggering_actor" not in issue["env"]["DISPOSITION_APPROVER"]
    assert "--approver \"$DISPOSITION_APPROVER\"" in issue["run"]
    assert "--approver-id \"$DISPOSITION_APPROVER_ID\"" in issue["run"]
    assert "--approved-at \"$approved_at\"" in issue["run"]
    assert "--repo-dir \"$CALLER_HEAD_DIR\"" in issue["run"]
    assert 'test -n "$CALLER_HEAD_DIR"' in issue["run"]
    assert "inputs.approver" not in text
    assert "${{ github.actor }}" in text
    assert "${{ github.triggering_actor }}" not in text
    assert "${{ github.actor_id }}" in text
    assert "GITHUB_TRIGGERING_ACTOR" not in issue.get("env", {})
    assert "--triggering-actor" not in issue["run"]
    assert "env -i" not in issue["run"]
    assert (
        f"python3 {DISPOSITION_CHECKOUT_SUBDIR}/.github/actions/gate-disposition/issue_receipt.py issue"
        in issue["run"]
    )

def test_disposition_workflow_fetches_caller_head_for_counterevidence_rerun():
    raw, _ = _load_disposition_workflow()
    control = raw["jobs"]["control"]
    names = [step.get("name") for step in control["steps"]]
    fetch = next(
        step for step in control["steps"]
        if step.get("name") == "Fetch caller head for counterevidence rerun"
    )
    assert "uses" not in fetch
    assert fetch["env"]["GH_TOKEN"] == "${{ github.token }}"
    run = fetch["run"]
    assert "eval \"$GATE_SOURCE_DECIDE_SCRIPT\"" in run
    assert 'if [ "$mode" = service ]' in run
    assert run.index('if [ "$mode" = service ]') < run.index("git-source-prepare")
    assert "flock" not in run
    assert "COUNTEREVIDENCE-${mapped}" in run
    assert "SOURCE-SERVICE-UNAVAILABLE" in run
    assert "COUNTEREVIDENCE-SOURCE-TIMEOUT" in run
    assert "COUNTEREVIDENCE-SOURCE-FETCH-FAILED" in run
    assert "git -C \"$repo_dir\" init --quiet" in run
    # gate#302: the caller head must arrive complete. A blob-filtered clone
    # defers the pathspec blobs to per-object promisor fetches and the grep
    # times out without saying why.
    assert 'timeout 300 git -C "$repo_dir" fetch --depth 1 origin "${fetch_shas[@]}"' in run
    assert not [line for line in run.splitlines() if "fetch" in line and "--filter" in line]
    assert "--missing=print" in run
    assert "_gate-caller-head" in run
    assert raw["permissions"]["contents"] == "read"
    issue = next(
        step for step in control["steps"]
        if step.get("name") == "Issue immutable disposition artifact"
    )
    issue_run = issue["run"]
    assert "GIT_NO_REPLACE_OBJECTS=1" in issue_run
    assert "consume.lock" in issue_run
    assert "flock --shared" in issue_run
    assert issue_run.index("flock --shared") < issue_run.index("issue_receipt.py issue")
    assert issue_run.index("issue_receipt.py issue") < issue_run.index("flock --unlock")
    assert "GIT_CONFIG_KEY_0=safe.directory" in issue_run
    assert "COUNTEREVIDENCE-SOURCE-PIN-MISMATCH" in issue_run
    assert names.index("Resolve current PR head and canonical primary audit") < names.index(
        "Fetch caller head for counterevidence rerun"
    )
    assert names.index("Fetch caller head for counterevidence rerun") < names.index(
        "Issue immutable disposition artifact"
    )



def test_gate_disposition_receipt_names_include_epoch_and_audit_digest():
    text = DISPOSITION_WORKFLOW.read_text()
    producer = (REPO_ROOT / ".github" / "actions" / "gate-disposition" / "issue_receipt.py").read_text()
    consumer = (REPO_ROOT / ".github" / "actions" / "gate-aggregator" / "convergence.py").read_text()
    assert "disposition_receipt_artifact_name" in producer
    assert "DISPOSITION_APPROVER" in producer
    assert "steps.disposition.outputs.artifact_name" in text
    assert "CURRENT_AUDIT_DIGEST" in text
    assert "receipt.audit_digest[:12]" in consumer

def test_recorded_disposition_lines_is_the_only_g4_line_builder():
    hits = []
    line_builder = 'f"disposition={receipt.disposition} finding='
    for path in (REPO_ROOT / ".github").rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".yml"}:
            continue
        if line_builder in path.read_text(encoding="utf-8"):
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
    # The module is loaded from the workflow's own checkout subdirectory, so the
    # only copy in existence has to be the one a sparse checkout leaves there.
    workspace = _checkout_shaped_workspace(
        tmp_path, DISPOSITION_CHECKOUT_SUBDIR, DISPOSITION_PRODUCER_SPARSE)
    result = subprocess.run(
        [sys.executable, "-", str(audit_path), str(pr_path), "123", "42"],
        input=source,
        cwd=workspace,
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
    assert set(_sparse_env_paths(checkout)) == set(DISPOSITION_PRODUCER_SPARSE)
    helper = BOUNDED_RETRY_HELPER.read_text(encoding="utf-8")
    assert '["sparse-checkout", "init", "--no-cone"]' in helper
    assert '"--no-cone"' in helper

def test_disposition_checkout_uses_reusable_workflow_identity():
    raw, _ = _load_disposition_workflow()
    checkout = next(
        step
        for step in raw["jobs"]["control"]["steps"]
        if step.get("name") == "Checkout disposition producer"
    )
    assert_workflow_sha_checkout(checkout, path=DISPOSITION_CHECKOUT_SUBDIR, sparse=DISPOSITION_PRODUCER_SPARSE)
    assert not any(step.get("name") == "Require 40-hex gate_ref" for step in raw["jobs"]["control"]["steps"])
    assert "inputs." + "gate_ref" not in DISPOSITION_WORKFLOW.read_text()

def test_disposition_workflow_call_accepts_legacy_gate_ref_both_ways():
    _, callee_trigger = _load_disposition_workflow()
    callee_declared = callee_trigger["workflow_call"]["inputs"]
    callee_required = {name for name, spec in callee_declared.items() if spec.get("required")}

    caller_raw, _ = _load_disposition_caller()
    caller_inputs = set(caller_raw["jobs"]["disposition"]["with"].keys())
    assert "gate_ref" not in caller_inputs

    # New caller: caller omits gate_ref; callee required inputs must be satisfied
    # and caller must stay within declared callee inputs.
    assert callee_required <= caller_inputs
    assert caller_inputs <= set(callee_declared)

    # Legacy caller: caller still supplies gate_ref; callee required inputs are
    # satisfied and callee must still accept gate_ref without schema rejection.
    legacy_caller_inputs = caller_inputs | {"gate_ref"}
    assert callee_required <= legacy_caller_inputs
    assert legacy_caller_inputs <= set(callee_declared)

def test_disposition_workflow_call_declares_optional_silo_secret_pair():
    # Public callers map the Silo pair by name; legacy callers keep
    # secrets: inherit. The callee must declare exactly the pair its control
    # job consumes, both optional, so both call shapes pass schema validation.
    raw, trigger = _load_disposition_workflow()
    control_env = raw["jobs"]["control"]["env"]
    assert control_env["AWS_ACCESS_KEY_ID"] == "${{ secrets.SILO_ACCESS_KEY }}"
    assert control_env["AWS_SECRET_ACCESS_KEY"] == "${{ secrets.SILO_SECRET_KEY }}"
    consumed = set()
    for value in control_env.values():
        match = re.fullmatch(r"\$\{\{ secrets\.([A-Z0-9_]+) }}", str(value))
        if match:
            consumed.add(match.group(1))
    assert consumed == {"SILO_ACCESS_KEY", "SILO_SECRET_KEY"}
    declared = trigger["workflow_call"].get("secrets", {})
    assert set(declared) == consumed
    for name in consumed:
        assert declared[name]["required"] is False
    assert "secrets" not in trigger["workflow_dispatch"]

def test_disposition_silo_and_script_consumers_read_from_the_checkout_subdirectory(tmp_path):
    """The four root-relative consumers of the disposition job.

    `SILO_STORE` / `SILO_EXEC` are the Silo helpers the MagicDNS and audit steps
    invoke; the other two are the inline script invocations.  Each is resolved
    against a workspace that contains the tools ONLY below the declared
    subdirectory, so a consumer left addressing the workspace root resolves to
    nothing instead of quietly working in the source tree.
    """
    raw, _ = _load_disposition_workflow()
    job = raw["jobs"]["control"]
    checkout = _disposition_checkout_step()
    subdir = checkout["env"]["GATE_CHECKOUT_PATH"]
    sparse = _sparse_env_paths(checkout)
    workspace = _checkout_shaped_workspace(tmp_path, subdir, sparse)

    assert job["env"]["SILO_STORE"] == (
        f"${{{{ github.workspace }}}}/{subdir}/scripts/silo_store.py"
    )
    assert job["env"]["SILO_EXEC"] == (
        f"${{{{ github.workspace }}}}/{subdir}/scripts/silo_exec.sh"
    )
    module_ref = re.search(
        r'module_path = Path\("([^"]+)"\)', _disposition_scope_python()
    ).group(1)
    producer_ref = re.search(
        r'python3 (\S+) issue', _disposition_step("Issue immutable disposition artifact")["run"]
    ).group(1)
    assert module_ref == f"{subdir}/.github/actions/gate-aggregator/convergence.py"
    assert producer_ref == f"{subdir}/.github/actions/gate-disposition/issue_receipt.py"
    for relative in (
        f"{subdir}/scripts/silo_store.py",
        f"{subdir}/scripts/silo_exec.sh",
        module_ref,
        producer_ref,
    ):
        assert (workspace / relative).is_file(), relative
        assert (workspace / relative).read_bytes() == (REPO_ROOT / relative[len(subdir) + 1:]).read_bytes()
    # Nothing resolves from the workspace root any more.
    for relative in ("scripts/silo_store.py", "scripts/silo_exec.sh", ".github"):
        assert not (workspace / relative).exists()

def test_disposition_receipt_step_runs_the_producer_from_the_checkout_subdirectory(tmp_path):
    """Run the receipt step's own `run` body, from a checkout-shaped workspace,
    and check that it signs a real receipt from the producer the checkout left
    below the subdirectory."""
    raw, _ = _load_disposition_workflow()
    step = _disposition_step("Issue immutable disposition artifact")
    subdir = _disposition_checkout_step()["env"]["GATE_CHECKOUT_PATH"]
    sparse = _sparse_env_paths(_disposition_checkout_step())
    workspace = _checkout_shaped_workspace(tmp_path, subdir, sparse)
    caller_head = tmp_path / "caller-head"
    head_sha, counterevidence = _seed_caller_head(caller_head)

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
    output_dir.mkdir()
    github_env = tmp_path / "github-env"
    github_env.write_text("")
    github_output = tmp_path / "github-output"
    github_output.write_text("")

    env = {
        **os.environ,
        "AUDIT_PATH": str(audit_path),
        "CURRENT_HEAD_SHA": scope["head_sha"],
        "CURRENT_AUDIT_DIGEST": "d" * 64,
        "CURRENT_EPOCH": "e" * 64,
        "CURRENT_SCOPE_JSON": json.dumps(scope),
        "REPOSITORY_ID": "123",
        "PR_NUMBER": "42",
        "FINDING_ID": "finding-one",
        "DISPOSITION_REASON": "canonical evidence reviewed",
        "DISPOSITION_KIND": "false-positive",
        "DISPOSITION_COUNTEREVIDENCE_JSON": json.dumps(counterevidence),
        "CALLER_HEAD_DIR": str(caller_head),
        "DISPOSITION_TRACKING_ISSUE": "",
        "DISPOSITION_APPROVER": "owner",
        "DISPOSITION_APPROVER_ID": "10",
        "DISPOSITION_REPOSITORY": "zlxlabs/gate",
        "OUTPUT_DIR": str(output_dir),
        "GITHUB_TRIGGERING_ACTOR": "owner",
        "GITHUB_ENV": str(github_env),
        "GITHUB_OUTPUT": str(github_output),
        "GATE_SOURCE_DECIDE_SCRIPT": raw["env"]["GATE_SOURCE_DECIDE_SCRIPT"],
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, f"{result.stderr}\n{result.stdout}"
    artifact_name = next(
        line.split("=", 1)[1] for line in github_output.read_text().splitlines()
        if line.startswith("artifact_name=")
    )
    receipt = json.loads((output_dir / artifact_name).read_text(encoding="utf-8"))
    assert receipt["kind"] == "gate-disposition-receipt-v3"
    assert receipt["disposition"] == "false-positive"
    assert receipt["finding_id"] == "finding-one"
    assert receipt["approver"] == "owner"

def test_disposition_preflight_reads_the_same_inputs_as_the_receipt_step():
    """The preflight may only pre-judge what the receipt already rejects, so its
    view of the three optional inputs must be the receipt step's view."""
    raw, _ = _load_disposition_workflow()
    steps = raw["jobs"]["control"]["steps"]
    preflight = _preflight_step()
    receipt = _disposition_step("Issue immutable disposition artifact")
    assert set(preflight["env"]) == set(EVIDENCE_INPUT_ENV), preflight["env"]
    assert {name: preflight["env"][name] for name in EVIDENCE_INPUT_ENV} == EVIDENCE_INPUT_ENV
    assert {name: receipt["env"][name] for name in EVIDENCE_INPUT_ENV} == EVIDENCE_INPUT_ENV
    # An absent input renders as "", and issue_receipt.py turns exactly that into
    # its documented false-positive default (`... or "false-positive"`).
    producer = (REPO_ROOT / ".github" / "actions" / "gate-disposition" / "issue_receipt.py").read_text()
    assert '_value(args, envelope, "disposition", "DISPOSITION_KIND") or "false-positive"' in producer
    assert 'if counterevidence_raw is None or counterevidence_raw == "":' in producer
    assert DISPOSITION_PREFLIGHT_ERROR in preflight["run"]
    # Nothing outside the three inputs: no evidence channel the preflight could
    # invent, and no Silo/audit/network token for it to touch.
    run = preflight["run"]
    for token in ("SILO_", "AWS_", "RUNNER_TEMP", "gh ", "curl", "audit", "github.token"):
        assert token not in run, token
    # Before every step that can reach Silo or the canonical audit: the job stops
    # at the first failing step, so an early non-zero keeps them untouched.
    assert steps[0]["name"] == PREFLIGHT_STEP_NAME
    later = [
        index for index, step in enumerate(steps)
        if index and any(token in str(step.get("run", "")) for token in ("SILO_EXEC", "SILO_STORE", "gh api"))
    ]
    assert later, "no Silo/audit step found to order the preflight against"

@pytest.mark.parametrize(
    "values, expect_preflight_rc, expect_receipt_failure",
    [
        # Legacy five-input caller: nothing to forward, rejected for any audit.
        (LEGACY_FIVE_INPUTS, 1, "counterevidence_required"),
        ({**LEGACY_FIVE_INPUTS, "disposition": "false-positive"}, 1, "counterevidence_required"),
        ({**LEGACY_FIVE_INPUTS, "disposition": "yes-please"}, 1, "disposition must be false-positive, deferred or rejected"),
        # The default empty disposition is the false-positive default, so it is
        # checked with counterevidence exactly as the receipt checks it.
        ({**LEGACY_FIVE_INPUTS, "counterevidence_json": VALID_COUNTEREVIDENCE}, 0, None),
        ({**LEGACY_FIVE_INPUTS, "disposition": "false-positive",
          "counterevidence_json": VALID_COUNTEREVIDENCE}, 0, None),
        # Deferred keeps its existing rejection boundary, untouched.
        ({**LEGACY_FIVE_INPUTS, "disposition": "deferred", "tracking_issue": "#12"}, 0,
         "deferred_not_allowed_for_tier"),
        # Malformed evidence stays with the authoritative validator.
        ({**LEGACY_FIVE_INPUTS, "counterevidence_json": "not json"}, 0, "counterevidence"),
    ],
)
def test_disposition_preflight_never_changes_the_authoritative_outcome(
    tmp_path, values, expect_preflight_rc, expect_receipt_failure,
):
    preflight = _run_preflight(values)
    assert preflight.returncode == expect_preflight_rc, preflight.stderr
    if expect_preflight_rc:
        assert DISPOSITION_PREFLIGHT_ERROR in preflight.stderr
        # Readable, actionable, and not a verdict on the calling repository: name
        # the evidence channel and say what the caller has to expose and forward.
        for name in EVIDENCE_INPUT_ENV.values():
            assert name.split(".")[-1].rstrip(" }") in preflight.stderr
        assert (
            "expose and forward disposition, counterevidence_json and tracking_issue"
            in preflight.stderr
        )
        assert "caller" in preflight.stderr
        assert "counterevidence_required" not in preflight.stderr
    # Every early rejection is one the producer makes anyway; every shape the
    # preflight waves through keeps the outcome it had before.
    receipt = _run_receipt_step(tmp_path, values)
    if expect_receipt_failure is None:
        assert receipt.returncode == 0, receipt.stderr
        assert len(list(receipt.receipt_dir.iterdir())) == 1
    else:
        assert receipt.returncode != 0
        assert expect_receipt_failure in receipt.stderr

def test_disposition_preflight_failure_never_reaches_silo_or_the_audit(tmp_path):
    """Recorded-access fixture: every network/Silo entry point of this job is
    wired to a recorder, so an early rejection that touched one would leave a
    mark instead of silently passing."""
    record = tmp_path / "touched.log"
    recorder = tmp_path / "touched.sh"
    recorder.write_text(f'#!/bin/sh\necho "$0 $*" >> "{record}"\nexit 0\n')
    recorder.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("gh", "curl", "aws", "jq", "git"):
        (bin_dir / tool).symlink_to(recorder)
    env = {
        **_input_env(_preflight_step(), LEGACY_FIVE_INPUTS),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SILO_EXEC": str(recorder), "SILO_STORE": str(recorder),
        "AWS_ACCESS_KEY_ID": "would-not-be-here", "AWS_SECRET_ACCESS_KEY": "x",
    }
    result = _run_preflight(LEGACY_FIVE_INPUTS, env=env)
    assert result.returncode != 0
    assert DISPOSITION_PREFLIGHT_ERROR in result.stderr
    assert not record.exists(), "preflight reached Silo or a network client"

def test_disposition_caller_forwards_business_inputs_without_legacy_gate_ref():
    raw, trigger = _load_disposition_caller()
    assert raw["name"] == "gate-disposition"
    assert set(trigger["workflow_dispatch"]["inputs"]) == {
        "pr_number", "primary_run_id", "primary_run_attempt", "finding_id", "reason",
        "disposition", "counterevidence_json", "tracking_issue",
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
    assert job["secrets"] == {
        "SILO_ACCESS_KEY": "${{ secrets.SILO_ACCESS_KEY }}",
        "SILO_SECRET_KEY": "${{ secrets.SILO_SECRET_KEY }}",
    }
    assert "environment" not in job
    uses = job["uses"]
    assert uses == (
        "zlxlabs/gate/.github/workflows/gate-v2-disposition.yml@" + DISPOSITION_CALLER_PIN
    )
    forwarded = (
        "pr_number", "primary_run_id", "primary_run_attempt", "finding_id", "reason",
        "disposition", "counterevidence_json", "tracking_issue",
    )
    for key in forwarded:
        assert job["with"][key] == "${{ inputs." + key + " }}"
    assert set(job["with"]) == set(forwarded)
    text = DISPOSITION_CALLER_TEMPLATE.read_text()
    assert "pull-requests: write" not in text
    assert "environment:" not in text
