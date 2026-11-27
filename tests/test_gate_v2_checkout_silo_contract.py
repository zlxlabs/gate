"""Domain-specific Required Gate v2 contract tests."""
from test_gate_v2_contract import (
    CHECKOUT_HELPER_RUN,
    CLASSIFY_GUARD,
    CLASSIFY_JOB_ID,
    CLASSIFY_SCRIPT,
    DISPOSITION_CHECKOUT_SUBDIR,
    GATE_CHECKOUT_MIRROR_CALL,
    PRIMARY_RESULT_DOMAIN,
    PRIMARY_RESULT_EXPR,
    REPO_ROOT,
    WORKFLOW,
    _GATE_WRITER_CONCURRENCY,
    _assert_expensive_job_cancel_lock,
    _evaluate_primary_result_expr,
    _fromjson_label_sets,
    _load_caller,
    _load_disposition_workflow,
    _load_workflow,
    _quality_job_blob,
    _quality_result,
    _quality_steps,
    _repo,
    _run_quality_result_render,
    _self_hosted_label_set,
    _sparse_env_paths,
    assert_workflow_sha_checkout,
    json,
    os,
    pytest,
    re,
    retry,
    run_preflight_with_compare_stub,
    shlex,
    subprocess,
    sys,
    yaml,

)

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
def test_required_v2_has_no_workflow_level_concurrency():
    raw, _ = _load_workflow()
    assert "concurrency" not in raw
def test_gate_panel_writer_lock_remains_cancel_false():
    raw, _ = _load_workflow()
    # Byte-exact contract with base: the full mapping (complete group literal
    # + cancel-in-progress) must equal these constants, not just pass per-field
    # shape probes.
    assert raw["jobs"]["gate"].get("concurrency") == _GATE_WRITER_CONCURRENCY
def test_quality_and_primary_have_independent_cancel_true_pr_locks():
    raw, _ = _load_workflow()
    quality = raw["jobs"]["quality"].get("concurrency", {})
    primary = raw["jobs"]["primary"].get("concurrency", {})
    _assert_expensive_job_cancel_lock("quality", quality)
    _assert_expensive_job_cancel_lock("primary", primary)
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

def test_all_required_jobs_present():
    raw, _ = _load_workflow()
    assert set(raw["jobs"].keys()) == {
        "classify_pr_paths", "quality", "primary", "advisor_classify", "advisor", "gate", "ledger",
    }

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

def test_is_workflow_call_named_gate():
    raw, trigger = _load_workflow()
    assert "workflow_call" in trigger
    assert raw["name"] == "gate"

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
