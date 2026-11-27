from test_gate_aggregator import _cli_args
from test_gate_aggregator import (
    AGG,
    CONV,
    IDENTITY,
    Path,
    _CANARY_ATTEMPT,
    _CANARY_REPO_ID,
    _CANARY_RUN_ID,
    _CANARY_TERMINAL_KEY,
    _CANARY_TERMINAL_NAME,
    _DIGEST_A,
    _DIGEST_B,
    _TERMINAL_GOLDEN,
    _base_kwargs,
    _canary_terminal_bytes,
    _canary_terminal_zip,
    _evaluate_failing_primary,
    _failing_scoped_audit,
    _false_positive_receipt,
    _install_dual_read,
    _real_convergence_history_silo_objects,
    _relation_audit,
    _resolved_line,
    _scope_for,
    _terminal_for,
    _valid_primary_record,
    _valid_scoped_primary_record,
    io,
    json,
    pytest,
    subprocess,
    zipfile,
)

def test_terminal_reason_domain_lock():
    assert AGG.TERMINAL_REASON_DOMAIN == (
        "primary_pass", "primary_findings", "disposition_resolved", "review_not_expected", "primary_unavailable", "primary_cancelled",
        "quality_failure", "quality_infra", "quality_cancelled", "quality_skipped", "audit_missing", "audit_invalid",
        "audit_source_mismatch", "job_audit_mismatch", "unexpected_primary_skip",
    )
def test_terminal_publish_barrier_failures(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    summary_path.mkdir()
    with pytest.raises(OSError):
        AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path)))
    assert not terminal_path.exists()
    original_write_text = Path.write_text
    def partial_write_then_fail(path, data, **kwargs):
        if path.name == ".gate-terminal.json.tmp":
            path.write_bytes(data.encode())
            raise OSError("simulated partial terminal write")
        return original_write_text(path, data, **kwargs)
    monkeypatch.setattr(Path, "write_text", partial_write_then_fail)
    with pytest.raises(OSError, match="simulated partial terminal write"):
        AGG.main(_cli_args(audit_dir, tmp_path / "summary.txt", terminal_path=str(terminal_path)))
    assert not terminal_path.exists()
def test_terminal_envelope_bytes_unchanged_by_rendering_work():
    # gate-terminal.json is a cross-job publish boundary. Receipt audit stays
    # present even when no receipt could be recorded.
    outcome = AGG.Outcome(
        ok=True, classification="code_pass", reason_code="primary_pass", gate_result="pass",
        audit_available=True, audit_source_attempt=1, audit_artifact_name="primary-audit-v2-1",
    )
    envelope = AGG.build_terminal_envelope(
        repository="zlxlabs/gate", identity=IDENTITY, quality_result="success",
        primary_result="success", review_expected=True, is_draft=False, runner="self", outcome=outcome,
    )
    assert json.dumps(envelope, ensure_ascii=False, indent=2) + "\n" == _TERMINAL_GOLDEN
@pytest.mark.parametrize("include_null", [False, True], ids=["legacy-missing", "legacy-null"])
def test_terminal_history_keeps_missing_or_null_primary_verdict_unknown(include_null):
    record = json.loads(_canary_terminal_bytes())
    if include_null:
        record["primary_verdict"] = None

    row = AGG._terminal_row(
        record, repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )

    assert row["primary_verdict"] is None
@pytest.mark.parametrize("verdict", ["not_expected", "waived"])
def test_terminal_rejects_nonreview_primary_verdicts(verdict):
    outcome = AGG.Outcome(
        ok=False, classification="integration_error", reason_code="audit_invalid",
        gate_result="unavailable", primary_verdict=verdict,
    )
    with pytest.raises(ValueError, match="terminal primary_verdict"):
        _terminal_for(outcome)
def test_terminal_envelope_publishes_shadow_budget_bytes_without_changing_gate_result(tmp_path):
    audit = _valid_scoped_primary_record()
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    outcome = AGG.evaluate(
        **_base_kwargs(
            primary_result="success", audit=audit, scope=_scope_for(audit), audit_digest=_DIGEST_A,
        ),
        prior_eligible_run_ids=(101, 102, 103, 104),
        convergence_history_status="available",
    )
    assert AGG._finish(
        outcome, str(summary_path), terminal_path=str(terminal_path),
        repository="zlxlabs/gate", identity=IDENTITY,
        quality_result="success", primary_result="success", review_expected=True,
        is_draft=False, runner="self", primary_audit=audit,
    ) == 0
    published = json.loads(terminal_path.read_bytes())
    assert published["gate_result"] == "pass"
    assert published["convergence_shadow"] == {
        "history_status": "available",
        "eligible_rounds": 5,
        "limit": 5,
        "terminal_decision": "arbitration_required",
        "eligible_this_round": True,
    }
    summary = summary_path.read_text()
    assert "5/5" in summary and "arbitration_required" in summary
    row = AGG._terminal_row(
        published, repository="zlxlabs/gate", repository_id=IDENTITY.repository_id,
        pr_number=IDENTITY.pr,
    )
    panel = AGG.render_status_panel([row])
    assert "5/5 · arbitration_required" in panel
def test_history_loader_skips_other_pr_and_bad_records_individually(monkeypatch):
    valid = {
        "schema_version": 1, "kind": "gate_terminal", "repository": "zlxlabs/gate",
        "repository_id": 123, "pr_number": 42, "run_id": 9, "run_attempt": 1,
        "head_sha": "a" * 40, "gate_result": "pass", "classification": "code_pass",
        "reason_code": "primary_pass",
    }
    other_pr = dict(valid, pr_number=77, run_id=8)
    bad_schema = dict(valid, schema_version=True, run_id=7)
    archives = {
        "valid": valid,
        "other": other_pr,
        "bad": bad_schema,
    }
    zipped = {}
    for name, record in archives.items():
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("gate-terminal.json", json.dumps(record))
        zipped[name] = archive.getvalue()
    monkeypatch.setattr(
        AGG, "_github_json",
        lambda **kwargs: {"artifacts": [
            {"name": "gate-terminal-v1-123-valid", "expired": False, "archive_download_url": "valid"},
            {"name": "gate-terminal-v1-123-other", "expired": False, "archive_download_url": "other"},
            {"name": "gate-terminal-v1-123-bad", "expired": False, "archive_download_url": "bad"},
            {"name": "gate-terminal-v1-123-expired", "expired": True, "archive_download_url": "expired"},
        ]},
    )
    monkeypatch.setattr(AGG, "_download_terminal_zip", lambda **kwargs: zipped[kwargs["url"]])
    result = AGG._fetch_terminal_history(token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42)
    assert [row["run_id"] for row in result.rows] == [9]
    assert {entry["name"] for entry in result.skipped_records} == {
        "gate-terminal-v1-123-other", "gate-terminal-v1-123-bad", "gate-terminal-v1-123-expired",
    }
    assert any("expired" in reason for reason in result.incomplete_reasons)
def test_cross_head_shadow_cap_records_arbitration_without_changing_gate_result():
    audit = _valid_scoped_primary_record(
        verdict="fail",
        tier="personal",
        result={"findings": [{
            "id": "p1", "severity": "major", "trigger_kind": "inferred",
            "file": "src/lock.py", "line": 12, "category": "correctness",
        }]},
    )
    kwargs = _base_kwargs(primary_result="failure", audit=audit, scope=_scope_for(audit), audit_digest=_DIGEST_A)
    before = AGG.evaluate(**kwargs)
    after = AGG.evaluate(
        **kwargs,
        prior_eligible_run_ids=(101, 102, 103),
        convergence_history_status="available",
    )
    assert (after.ok, after.classification, after.reason_code, after.gate_result) == (
        before.ok, before.classification, before.reason_code, before.gate_result,
    ) == (False, "code_fail", "primary_findings", "fail")
    assert after.convergence_shadow == {
        "history_status": "available",
        "eligible_rounds": 4,
        "limit": 5,
        "terminal_decision": "collecting",
        "eligible_this_round": True,
    }
def test_cross_head_shadow_cap_marks_arbitration_for_current_p1_round():
    audit = _valid_scoped_primary_record(
        verdict="fail",
        tier="personal",
        result={"findings": [{
            "id": "p1", "severity": "major", "trigger_kind": "inferred",
            "file": "src/lock.py", "line": 12, "category": "correctness",
        }]},
    )
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="failure", audit=audit, scope=_scope_for(audit), audit_digest=_DIGEST_A),
        prior_eligible_run_ids=(101, 102, 103, 104),
        convergence_history_status="available",
    )
    assert outcome.gate_result == "fail"
    assert outcome.convergence_shadow["eligible_rounds"] == 5
    assert outcome.convergence_shadow["terminal_decision"] == "arbitration_required"
def test_cross_head_shadow_rerun_same_run_attempt_is_idempotent():
    audit = _valid_scoped_primary_record(
        verdict="fail", tier="personal",
        result={"findings": [{
            "id": "p1", "severity": "major", "trigger_kind": "inferred",
            "file": "src/lock.py", "line": 12, "category": "correctness",
        }]},
    )
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="failure", audit=audit, scope=_scope_for(audit), audit_digest=_DIGEST_A),
        prior_eligible_run_ids=(101, IDENTITY.run_id),
        convergence_history_status="available",
    )
    assert outcome.convergence_shadow["eligible_rounds"] == 2
    assert outcome.convergence_shadow["eligible_this_round"] is True
def test_terminal_projects_structured_audit_from_record_dispositions_objects():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope)
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    block = _terminal_for(outcome)["disposition_receipt_consumption"]
    assert block["recorded"][0]["finding_id"] == "p1"
    assert block["recorded"][0]["disposition_claim"] == "false-positive"
    assert block["recorded"][0]["evidence_pointer"] == "tests/test_regression.py::test_regression"
    assert block["recorded"][0]["counterevidence_command"] == "pytest -q tests/test_regression.py"
    assert block["recorded"][0]["counterevidence_output"] == "1 passed"
    assert block["resolved"][0]["disposition"] == "false-positive"
    assert block["resolved"][0]["evidence_pointer"] == "tests/test_regression.py::test_regression"
    assert block["consumed_count"] == 1
    assert block["remaining_p1_ids"] == []
    assert outcome.recorded_disposition_claims == [_resolved_line(receipt)]
    assert "receipt claim" not in json.dumps(block)
def test_terminal_structured_block_does_not_parse_g4_display_strings():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope, finding_id="p1")
    g4 = _resolved_line(receipt)
    consumption = CONV.DispositionAudit(
        primary_p1_ids=(),
        recorded_receipts=(receipt,),
        rejected_receipts=(),
        recorded_finding_ids=("p1",),
    )
    outcome = AGG.Outcome(
        ok=False, classification="code_fail", reason_code="primary_findings", gate_result="fail",
        recorded_disposition_claims=[g4],
        disposition_audit=consumption,
    )
    block = _terminal_for(outcome, primary_result="success")["disposition_receipt_consumption"]
    assert block["resolved"][0]["evidence_pointer"] == "tests/test_regression.py::test_regression"
    assert block["recorded"][0]["finding_id"] == "p1"
    assert isinstance(block["recorded"][0], dict)
    assert block["recorded"][0]["triggering_actor"] == "octocat"
    assert g4 != block["recorded"][0]
def test_terminal_stable_audit_separates_human_id_and_finding_key():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    stable_key = CONV.canonical_finding_key(audit["result"]["findings"][0])
    receipt = _false_positive_receipt(
        scope, finding_id="p1", finding_key=stable_key,
    )
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    claims = _terminal_for(outcome)["disposition_receipt_consumption"]["recorded"]
    assert claims[0]["finding_id"] == "p1"
    assert claims[0]["finding_key"] == stable_key
def test_terminal_empty_audit_when_evaluate_sees_no_receipts():
    audit = _failing_scoped_audit()
    outcome = _evaluate_failing_primary(audit, waiver_receipts=())
    block = _terminal_for(outcome)["disposition_receipt_consumption"]
    assert {**block, "remaining_p1_ids": []} == AGG.empty_disposition_receipt_audit()
    assert block["remaining_p1_ids"] == ["p1"]
    assert outcome.disposition_audit is not None
    assert outcome.disposition_audit.recorded_receipts == ()
def test_terminal_rejected_receipts_project_reason_counts_without_fail_closed():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    mismatched = _false_positive_receipt(scope, audit_digest=_DIGEST_B)
    unknown = _false_positive_receipt(scope, finding_id="other-finding")
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(mismatched, unknown))
    block = _terminal_for(outcome)["disposition_receipt_consumption"]
    assert block["resolved"] == []
    assert block["consumed_count"] == 0
    assert block["rejected_count"] == 2
    assert block["rejected_reasons"] == {
        "audit_digest_mismatch": 1,
        "finding_not_current_p1": 1,
    }
    assert block["fail_closed"] is False
    assert outcome.recorded_disposition_claims == []
def test_convergence_history_without_silo_configuration_is_greppable(monkeypatch, capsys):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: False)
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert history.incomplete_reasons == ["Silo terminal history unavailable: not configured"]
    assert "GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=silo_not_configured" in capsys.readouterr().out
@pytest.mark.parametrize(
    "error",
    [RuntimeError("Silo list denied"), subprocess.TimeoutExpired(["silo_store.py", "list"], 15)],
    ids=["listing-error", "listing-timeout"],
)
def test_convergence_history_silo_failure_is_greppable_and_unavailable(monkeypatch, capsys, error):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [501])

    def fail_listing(**kwargs):
        raise error

    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", fail_listing)
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert history.incomplete_reasons == [f"Silo terminal history unavailable: {type(error).__name__}"]
    assert f"GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason={type(error).__name__}" in capsys.readouterr().out
def test_convergence_history_empty_silo_but_github_has_prior_terminal_is_unavailable(monkeypatch, capsys):
    prior = AGG._terminal_row(
        json.loads(_canary_terminal_bytes()),
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [_CANARY_RUN_ID])
    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", lambda **kwargs: AGG.HistoryLoad())
    monkeypatch.setattr(
        AGG, "_fetch_github_terminal_history",
        lambda **kwargs: AGG.HistoryLoad(rows=[prior]),
    )
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert history.incomplete_reasons == ["Silo empty but GitHub has terminal history"]
    assert "silo_empty_but_github_has_terminal_history" in capsys.readouterr().out
def test_convergence_history_malformed_silo_terminal_is_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [_CANARY_RUN_ID])

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            return subprocess.CompletedProcess(argv, 0, stdout=f"{_CANARY_TERMINAL_KEY}\n", stderr="")
        Path(argv[argv.index("--dest") + 1]).write_bytes(b"{")
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert history.incomplete_reasons
    assert "GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=incomplete_terminal_history" in capsys.readouterr().out
def test_convergence_history_empty_first_run_is_available_zero(monkeypatch):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [501])
    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", lambda **kwargs: AGG.HistoryLoad())
    monkeypatch.setattr(
        AGG, "_fetch_github_terminal_history",
        lambda **kwargs: AGG.HistoryLoad(
            incomplete_reasons=["no terminal artifact matched run 501"],
        ),
    )
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids, history.rows, history.incomplete_reasons) == ("available", (), [], [])
def test_history_loader_without_token_fails_fast_before_silo_scan(monkeypatch, capsys):
    # gate#290 教训：没有 token 就无法按 PR 定向，无定向的 Silo/GitHub 全仓
    # 下载正是拖垮汇总 job 的路径，必须 fail-fast 而不是退回全仓扫描。
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def must_not_run(**kwargs):
        raise AssertionError("untargeted silo scan must not run without a token")

    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", must_not_run)
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert history.incomplete_reasons == ["history loader needs GITHUB_TOKEN to target this PR's runs"]
    assert "GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=github_token_missing" in capsys.readouterr().out
def test_history_loader_run_targeting_failure_is_greppable(monkeypatch, capsys):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    def fail_targeting(**kwargs):
        raise ValueError("Actions runs response has an invalid shape")

    monkeypatch.setattr(AGG, "_pr_target_run_ids", fail_targeting)

    def must_not_run(**kwargs):
        raise AssertionError("silo scan must not run when targeting failed")

    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", must_not_run)
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert history.incomplete_reasons == ["GitHub run targeting unavailable: ValueError"]
    assert "GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=ValueError" in capsys.readouterr().out
def test_history_loader_silo_empty_with_no_targeted_runs_skips_github_fallback(monkeypatch):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", lambda **kwargs: AGG.HistoryLoad())

    def must_not_run(**kwargs):
        raise AssertionError("fallback must not run without targeted runs")

    monkeypatch.setattr(AGG, "_fetch_github_terminal_history", must_not_run)
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids, history.rows, history.incomplete_reasons) == ("available", (), [], [])
def test_history_loader_budget_exhaustion_in_github_fallback_is_greppable(monkeypatch, capsys):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [501])
    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", lambda **kwargs: AGG.HistoryLoad())
    monkeypatch.setattr(
        AGG, "_fetch_github_terminal_history",
        lambda **kwargs: AGG.HistoryLoad(
            incomplete_reasons=["history budget exhausted during targeted scan"],
        ),
    )
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert history.incomplete_reasons == ["history budget exhausted"]
    assert "GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=history_budget_exhausted" in capsys.readouterr().out
def test_pr_target_run_ids_narrows_to_pr_branch(monkeypatch):
    urls = []

    def fake_github_json(*, token, url):
        urls.append(url)
        if "/pulls/42" in url:
            return {"head": {"ref": "feature/分支"}}
        return {"total_count": 2, "workflow_runs": [{"id": 7}, {"id": 3}, {"id": 7}, {"id": "bad"}, {"id": 0}, {}]}

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    run_ids = AGG._pr_target_run_ids(token="tok", repository="zlxlabs/gate", pr_number=42)
    assert run_ids == [7, 3]
    assert sum("/pulls/42" in url for url in urls) == 1
    assert any("branch=feature%2F%E5%88%86%E6%94%AF" in url for url in urls)
    # terminal 记录只由 pull_request 触发的 gate run 产生（caller 模板 on: 仅
    # pull_request），过滤掉 push/dispatch 类 run 既降噪又降低截断概率。
    assert any("event=pull_request" in url for url in urls)
def test_pr_target_run_ids_returns_none_when_more_runs_than_one_page(monkeypatch):
    def fake_github_json(*, token, url):
        if "/pulls/42" in url:
            return {"head": {"ref": "main"}}
        return {
            "total_count": 150,
            "workflow_runs": [{"id": run_id} for run_id in range(2000, 2000 + 100)],
        }

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    assert AGG._pr_target_run_ids(token="tok", repository="zlxlabs/gate", pr_number=42) is None
def test_pr_target_run_ids_returns_none_when_page_full_without_total_count(monkeypatch):
    def fake_github_json(*, token, url):
        if "/pulls/42" in url:
            return {"head": {"ref": "main"}}
        return {"workflow_runs": [{"id": run_id} for run_id in range(2000, 2000 + 100)]}

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    assert AGG._pr_target_run_ids(token="tok", repository="zlxlabs/gate", pr_number=42) is None
def test_pr_target_run_ids_returns_none_when_targeted_cap_reached(monkeypatch):
    def fake_github_json(*, token, url):
        if "/pulls/42" in url:
            return {"head": {"ref": "main"}}
        return {
            "total_count": 60,
            "workflow_runs": [{"id": run_id} for run_id in range(1000, 1000 + 60)],
        }

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    assert AGG._pr_target_run_ids(token="tok", repository="zlxlabs/gate", pr_number=42) is None
def test_history_loader_run_list_truncation_is_history_unavailable(monkeypatch, capsys):
    """F1 / INV-A4 锁：runs 列表截断（total_count 超过本页，或超过定向 cap）时
    不得返回部分列表少算 eligible_rounds，必须整体判 history_unavailable 且打
    reason=history_truncated。红验：去掉 `_pr_target_run_ids` 的截断判定后，
    本测试必须以断言失败转红（打桩无 sleep，不存在挂起）。"""
    def fake_github_json(*, token, url):
        if "/pulls/42" in url:
            return {"head": {"ref": "card/long-lived"}}
        return {
            "total_count": 150,
            "workflow_runs": [{"id": run_id} for run_id in range(3000, 3000 + 100)],
        }

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    silo_calls = []

    def fake_silo(**kwargs):
        silo_calls.append(kwargs)
        return AGG.HistoryLoad()

    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", fake_silo)
    monkeypatch.setattr(
        AGG, "_fetch_github_terminal_history",
        lambda **kwargs: AGG.HistoryLoad(
            incomplete_reasons=["no terminal artifact matched run 3000"],
        ),
    )
    status, run_ids, history = AGG._load_pr_convergence_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert (status, run_ids) == ("history_unavailable", ())
    assert silo_calls == []
    assert history.incomplete_reasons == [
        "history truncated: this PR's branch has more pull_request runs than one page or the targeted cap"
    ]
    assert "GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=history_truncated" in capsys.readouterr().out
def test_fetch_silo_terminal_history_downloads_only_targeted_runs(monkeypatch):
    other_run_key = (
        f"d30/{_CANARY_REPO_ID}/gate-terminal-v1-{_CANARY_REPO_ID}-"
        f"{'9' * 40}-999-1/gate-terminal.json"
    )
    keys = [_CANARY_TERMINAL_KEY, other_run_key]
    gets = []

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            return subprocess.CompletedProcess(
                argv, 0, stdout="".join(f"{key}\n" for key in keys), stderr="",
            )
        gets.append(argv[argv.index("--key") + 1])
        run_id = _CANARY_RUN_ID if len(gets) == 1 else 999
        Path(argv[argv.index("--dest") + 1]).write_bytes(
            _canary_terminal_bytes("pass", run_id=run_id)
        )
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    result = AGG._fetch_silo_terminal_history(
        repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
        target_run_ids=[_CANARY_RUN_ID],
    )
    assert gets == [_CANARY_TERMINAL_KEY]
    assert [(row["run_id"], row["gate_result"]) for row in result.rows] == [(_CANARY_RUN_ID, "pass")]
    assert result.incomplete_reasons == []
def test_real_silo_d30_terminal_fixture_drives_terminal_row_and_history_loader(monkeypatch):
    fixture, objects = _real_convergence_history_silo_objects()
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    targeted_ids = [record["run_id"] for record in fixture["terminal_envelopes"]]
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: targeted_ids)

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            return subprocess.CompletedProcess(
                argv, 0, stdout="".join(f"{key}\n" for key, _body in objects), stderr="",
            )
        key = argv[argv.index("--key") + 1]
        Path(argv[argv.index("--dest") + 1]).write_bytes(dict(objects)[key])
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    rows = [
        AGG._terminal_row(
            record, repository=fixture["repository"], repository_id=fixture["repository_id"],
            pr_number=fixture["pr_number"],
        )
        for record in fixture["terminal_envelopes"]
    ]
    status, run_ids, _history = AGG._load_pr_convergence_history(
        repository=fixture["repository"], repository_id=fixture["repository_id"],
        pr_number=fixture["pr_number"],
    )
    assert len(fixture["source_keys"]) >= 2
    assert all("findings" not in record and "result" not in record for record in fixture["terminal_envelopes"])
    assert all(row["_eligible_primary_round"] is True for row in rows)
    assert (status, run_ids) == ("available", (35424466527, 35423752741))
@pytest.mark.parametrize(
    "case",
    ["github_only", "silo_only", "both_same", "both_empty", "github_fail", "silo_fail"],
)
def test_terminal_history_dual_read_table(monkeypatch, capsys, case):
    github_zip = _canary_terminal_zip("fail")
    silo_bytes = _canary_terminal_bytes("pass")
    github_art = [{
        "name": _CANARY_TERMINAL_NAME, "expired": False,
        "archive_download_url": "https://api.github.com/artifacts/canary/zip",
    }]
    kwargs = dict(github_artifacts=[], github_blobs={}, silo_objects=[])
    if case == "github_only":
        kwargs = dict(github_artifacts=github_art, github_blobs={"https://api.github.com/artifacts/canary/zip": github_zip}, silo_objects=[])
    elif case == "silo_only":
        kwargs = dict(github_artifacts=[], github_blobs={}, silo_objects=[(_CANARY_TERMINAL_KEY, silo_bytes)])
    elif case == "both_same":
        kwargs = dict(
            github_artifacts=github_art,
            github_blobs={"https://api.github.com/artifacts/canary/zip": github_zip},
            silo_objects=[(_CANARY_TERMINAL_KEY, silo_bytes)],
        )
    elif case == "github_fail":
        kwargs = dict(
            github_artifacts=[], github_blobs={}, silo_objects=[(_CANARY_TERMINAL_KEY, silo_bytes)],
            github_error=RuntimeError("github listing down"),
        )
    elif case == "silo_fail":
        kwargs = dict(
            github_artifacts=github_art,
            github_blobs={"https://api.github.com/artifacts/canary/zip": github_zip},
            silo_objects=[],
            silo_error=RuntimeError("silo listing down"),
        )
    _install_dual_read(monkeypatch, **kwargs)
    result = AGG._fetch_terminal_history(
        token="tok", repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    warning = capsys.readouterr().out
    rows_by_id = {(row["run_id"], row["run_attempt"]): row["gate_result"] for row in result.rows}
    if case == "github_only":
        assert rows_by_id == {(_CANARY_RUN_ID, _CANARY_ATTEMPT): "fail"}
        assert "Silo terminal history unavailable" not in warning
    elif case == "silo_only":
        assert rows_by_id == {(_CANARY_RUN_ID, _CANARY_ATTEMPT): "pass"}
        assert not any(reason.startswith("no terminal artifact matched gate-terminal-v1-") for reason in result.incomplete_reasons)
        assert "Silo terminal history unavailable" not in warning
    elif case == "both_same":
        assert rows_by_id == {(_CANARY_RUN_ID, _CANARY_ATTEMPT): "pass"}
        assert "Silo terminal history unavailable" not in warning
    elif case == "both_empty":
        assert result.rows == []
        assert any("no terminal artifact matched" in reason for reason in result.incomplete_reasons)
        assert "Silo terminal history unavailable" not in warning
    elif case == "github_fail":
        assert rows_by_id == {(_CANARY_RUN_ID, _CANARY_ATTEMPT): "pass"}
        assert any("GitHub terminal history unavailable: RuntimeError" in reason for reason in result.incomplete_reasons)
    elif case == "silo_fail":
        assert rows_by_id == {(_CANARY_RUN_ID, _CANARY_ATTEMPT): "fail"}
        assert "Silo terminal history unavailable: RuntimeError" in warning
        assert any("Silo terminal history unavailable: RuntimeError" in reason for reason in result.incomplete_reasons)
@pytest.mark.parametrize("case", ["pass", "fail", "manual_conflict"])
def test_shadow_budget_preserves_existing_verdict_contract(case):
    if case == "pass":
        audit = _valid_scoped_primary_record(verdict="pass", result={"findings": []})
        primary_result = "success"
        previous_round = {"available": True, "findings": []}
    elif case == "fail":
        audit = _valid_scoped_primary_record(
            verdict="fail",
            result={"findings": [{
                "id": "p1", "severity": "major", "trigger_kind": "inferred",
                "file": "src/lock.py", "line": 12, "category": "correctness",
            }]},
        )
        primary_result = "failure"
        previous_round = {"available": True, "findings": []}
    else:
        current = {
            "id": "round-b", "severity": "major", "file": "src/lock.py", "line": 12,
            "category": "correctness", "issue": "must not advance the lease",
            "acceptance": "leave the lease at evidence",
        }
        previous = {
            "id": "round-a", "severity": "major", "file": "src/lock.py", "line": 12,
            "category": "correctness", "issue": "must advance the lease",
            "acceptance": "advance the lease to closed",
        }
        audit = _relation_audit(**current)
        previous_round = {"available": True, "findings": [previous]}
        primary_result = "success"

    kwargs = _base_kwargs(
        primary_result=primary_result,
        audit=audit,
        scope=_scope_for(audit),
        audit_digest=_DIGEST_A,
    )
    baseline = AGG.evaluate(**kwargs)
    shadow = AGG.evaluate(
        **kwargs,
        prior_eligible_run_ids=(101, 102, 103, 104),
        convergence_history_status="available",
    )
    for outcome in (baseline, shadow):
        findings = json.loads(json.dumps(audit["result"]["findings"]))
        AGG.apply_finding_relation(outcome, findings, previous_round)
    assert (shadow.ok, shadow.classification, shadow.reason_code, shadow.gate_result) == (
        baseline.ok, baseline.classification, baseline.reason_code, baseline.gate_result,
    )
    assert shadow.convergence_shadow["terminal_decision"] == "arbitration_required"
    if case == "manual_conflict":
        assert shadow.finding_relation["review_terminal"] == "manual_required"
