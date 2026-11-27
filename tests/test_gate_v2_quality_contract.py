"""Domain-specific Required Gate v2 contract tests."""
from test_gate_v2_contract import (
    AGGREGATOR_SCRIPT,
    CALLER_CHECKS_BUSINESS_STEPS,
    CALLER_CHECKS_PREFLIGHT_STEP,
    CALLER_TEMPLATE,
    CHECKOUT_ACTION,
    CHECKOUT_HELPER_RUN,
    LEDGER_INPUT_FIXTURE,
    QUALITY_ENTRY_MODE,
    QUALITY_ENTRY_PATH,
    QUALITY_RESULT_FIXTURE,
    REPO_ROOT,
    SHARED_ACTION_MODULES,
    _assert_expensive_job_cancel_lock,
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
