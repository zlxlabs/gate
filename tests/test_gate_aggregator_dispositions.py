from test_gate_aggregator import (
    AGG,
    CONV,
    IDENTITY,
    Path,
    ROOT,
    _CANARY_AUDIT_KEY,
    _CANARY_REPO_ID,
    _CANARY_TERMINAL_KEY,
    _DIGEST_A,
    _DIGEST_B,
    _base_kwargs,
    _canary_receipt,
    _cli_args,
    _deferred_receipt,
    _evaluate_failing_primary,
    _failing_scoped_audit,
    _false_positive_receipt,
    _install_dual_read,
    _real_convergence_history_silo_objects,
    _receipt_key,
    _receipt_payload,
    _resolved_line,
    _scope_for,
    _terminal_for,
    _valid_scoped_primary_record,
    _zip_receipt_bytes,
    hashlib,
    json,
    patch,
    pytest,
    subprocess,
)

def test_evaluate_records_dispositions_once_for_active_p1():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope, audit_digest=_DIGEST_A)
    original = CONV.record_dispositions

    with patch.object(CONV, "record_dispositions", wraps=original) as record_dispositions:
        outcome = AGG.evaluate(**_base_kwargs(
            primary_result="failure",
            audit=audit,
            scope=scope,
            audit_digest=_DIGEST_B,
            legacy_raw_audit_digest=_DIGEST_A,
            waiver_receipts=(receipt,),
            repository="zlxlabs/gate",
        ))

    assert record_dispositions.call_count == 1
    assert outcome.convergence_envelope is not None
    assert outcome.disposition_audit.primary_p1_ids == ("p1",)
    assert outcome.disposition_audit.remaining_p1_ids == ()
    assert outcome.convergence_envelope["state"]["clean_streak"] == 1

def test_evaluate_round_reuses_disposition_audit_with_legacy_digest():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    p1_findings = AGG._canonical_p1_findings(audit)
    primary = CONV.CanonicalPrimary(
        schema_version=1,
        repository_id=IDENTITY.repository_id,
        pr_number=IDENTITY.pr,
        head_sha=IDENTITY.head_sha,
        run_id=IDENTITY.run_id,
        run_attempt=IDENTITY.run_attempt,
        verdict=audit["verdict"],
        p1_ids=tuple(item[0] for item in p1_findings),
        p1_findings=p1_findings,
    )
    receipt = _false_positive_receipt(scope, audit_digest=_DIGEST_A)
    disposition_audit = CONV.record_dispositions(
        primary.p1_ids,
        (receipt,),
        scope=scope,
        primary=primary,
        audit_digest=_DIGEST_B,
        legacy_raw_audit_digest=_DIGEST_A,
        repository="zlxlabs/gate",
    )
    without_legacy_digest = CONV.record_dispositions(
        primary.p1_ids,
        (receipt,),
        scope=scope,
        primary=primary,
        audit_digest=_DIGEST_B,
        repository="zlxlabs/gate",
    )

    decision = CONV.evaluate_round(
        state=CONV.initial_state(scope),
        scope=scope,
        primary=primary,
        audit_digest=_DIGEST_B,
        waiver_receipts=(receipt,),
        disposition_audit=disposition_audit,
        processing_key=CONV.ProcessingKey(
            IDENTITY.repository_id, IDENTITY.pr, IDENTITY.run_id, IDENTITY.run_attempt,
        ),
        repository="zlxlabs/gate",
    )

    assert disposition_audit.remaining_p1_ids == ()
    assert without_legacy_digest.remaining_p1_ids == primary.p1_ids
    assert decision.remaining_p1_ids == disposition_audit.remaining_p1_ids
    assert decision.state.clean_streak == 1

@pytest.mark.parametrize("tier", ["personal", "internal", "unrecognized"])
def test_deferred_receipt_rejected_for_every_tier_or_defaulted_tier_at_consumption(tier):
    audit = _failing_scoped_audit()
    audit["tier"] = tier
    scope = _scope_for(audit)
    receipt = _deferred_receipt(
        scope, tracking_issue="https://github.com/zlxlabs/gate/issues/12",
    )

    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))

    block = _terminal_for(outcome)["disposition_receipt_consumption"]
    assert outcome.gate_result == "fail"
    assert block["remaining_p1_ids"] == ["p1"]
    assert block["rejected_reasons"] == {"deferred_not_allowed_for_tier": 1}

def test_deferred_receipt_is_rejected_for_saas_at_consumption():
    audit = _failing_scoped_audit()
    audit["tier"] = "saas"
    receipt = _deferred_receipt(_scope_for(audit))

    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))

    block = _terminal_for(outcome)["disposition_receipt_consumption"]
    assert outcome.gate_result == "fail"
    assert block["remaining_p1_ids"] == ["p1"]
    assert block["rejected_reasons"] == {"deferred_not_allowed_for_tier": 1}

def test_consumer_rejects_handcrafted_deferred_pull_or_cross_repository_url():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    for reference, reason in (
        ("https://github.com/zlxlabs/gate/pull/12", "tracking_issue_invalid"),
        ("https://github.com/other/repo/issues/12", "tracking_issue_repository_mismatch"),
    ):
        receipt = _deferred_receipt(scope, tracking_issue=reference)
        outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
        block = _terminal_for(outcome)["disposition_receipt_consumption"]
        assert outcome.gate_result == "fail"
        assert block["rejected_reasons"] == {reason: 1}

@pytest.mark.parametrize(
    "finding",
    [
        {"id": "p1", "severity": "major", "line": 12, "category": "correctness"},
        {"id": "p1", "severity": "major", "file": 12, "line": 12, "category": "correctness"},
        {"id": "p1", "severity": "major", "file": "src/lock.py", "line": "12", "category": "correctness"},
        {"id": "p1", "severity": "major", "file": "src/lock.py", "line": 12, "category": None},
    ],
)
def test_canonical_p1_projection_rejects_missing_or_malformed_stable_fields(finding):
    audit = _valid_scoped_primary_record(result={"findings": [finding]})
    assert AGG._canonical_p1_findings(audit) is None

def test_canonical_p1_projection_rejects_missing_line():
    audit = _valid_scoped_primary_record(result={"findings": [{
        "id": "p1", "severity": "major", "trigger_kind": "inferred",
        "file": "src/lock.py", "category": "correctness",
    }]})
    assert AGG._canonical_p1_findings(audit) is None

def test_canonical_p1_projection_accepts_null_line():
    audit = _valid_scoped_primary_record(result={"findings": [{
        "id": "p1", "severity": "major", "trigger_kind": "inferred",
        "file": "src/lock.py", "line": None, "category": "correctness",
    }]})
    assert AGG._canonical_p1_findings(audit) == (
        ("p1", "major", "inferred", "src/lock.py", None, "correctness"),
    )

def test_clean_round_at_cross_head_cap_marks_shadow_but_preserves_pass():
    audit = _valid_scoped_primary_record(
        verdict="pass", tier="personal", result={"findings": []},
    )
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="success", audit=audit, scope=_scope_for(audit), audit_digest=_DIGEST_A),
        prior_eligible_run_ids=(101, 102, 103, 104),
        convergence_history_status="available",
    )
    assert outcome.gate_result == "pass"
    assert outcome.convergence_shadow["eligible_rounds"] == 5
    assert outcome.convergence_shadow["terminal_decision"] == "arbitration_required"

def test_unavailable_primary_does_not_increment_cross_head_shadow_count():
    assert (audit_digest := hashlib.sha256(payload := (ROOT / "tests" / "fixtures" / "primary-audit-unavailable-malformed-v2.json").read_bytes()).hexdigest()) == "b8b521b776083e374ada7d803516adcfa29472210dba575516a36709956b7cfd"
    scope = AGG._convergence_scope_from_audit(audit := json.loads(payload), IDENTITY)[0]
    assert scope is not None and AGG.validate_audit_identity(audit, IDENTITY) == [] and audit["verdict"] == "unavailable" and audit["result"] is None
    outcome = AGG.evaluate(**_base_kwargs(primary_result="failure", audit=audit, scope=scope, audit_digest=audit_digest, prior_eligible_run_ids=(101, 102), convergence_history_status="available"))
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "primary_unavailable", "unavailable") and outcome.convergence_envelope is not None and outcome.convergence_envelope["state"]["unavailable_streak"] == 1 and not any("canonical finding severity" in p for p in outcome.problems)
    assert outcome.convergence_shadow["eligible_rounds"] == 2 and outcome.convergence_shadow["eligible_this_round"] is False
    assert AGG.evaluate(**_base_kwargs(primary_result="failure", audit={**audit, "result": {"findings": [{"severity": "unknown"}]}}, scope=scope, audit_digest=audit_digest)).reason_code == "audit_invalid"
    state = CONV.initial_state(scope)
    for run_id in range(1000, 1000 + CONV.policy_for(scope).unavailable_budget):
        state = CONV.evaluate_round(state=state, scope=scope, primary=CONV.CanonicalPrimary(1, IDENTITY.repository_id, IDENTITY.pr, IDENTITY.head_sha, run_id, 1, "unavailable"), audit_digest=audit_digest, processing_key=CONV.ProcessingKey(IDENTITY.repository_id, IDENTITY.pr, run_id, 1)).state
    assert (state.terminal_decision, state.reason) == ("manual_required", "unavailable budget exhausted")

def test_unavailable_cross_head_history_has_no_zero_count():
    audit = _valid_scoped_primary_record(
        verdict="fail", tier="personal",
        result={"findings": [{
            "id": "p1", "severity": "major", "trigger_kind": "inferred",
            "file": "src/lock.py", "line": 12, "category": "correctness",
        }]},
    )
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="failure", audit=audit, scope=_scope_for(audit), audit_digest=_DIGEST_A),
        prior_eligible_run_ids=(),
        convergence_history_status="history_unavailable",
    )
    assert outcome.gate_result == "fail"
    assert outcome.convergence_shadow["history_status"] == "history_unavailable"
    assert "eligible_rounds" not in outcome.convergence_shadow

def test_aggregate_projection_binds_stable_disposition_and_rejects_line_change():
    audit = _valid_scoped_primary_record(
        verdict="fail",
        result={"findings": [{
            "id": "p1", "severity": "major", "trigger_kind": "inferred",
            "file": "src/lock.py", "line": 12, "category": "correctness",
        }]},
    )
    projected = AGG._canonical_p1_findings(audit)
    assert projected is not None
    scope = _scope_for(audit)
    digest = CONV.canonical_audit_digest(audit)
    stable_key = CONV.canonical_finding_key(audit["result"]["findings"][0])
    receipt = _false_positive_receipt(
        scope, audit_digest=digest, finding_id=stable_key, finding_key=stable_key,
    )
    primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=IDENTITY.repository_id, pr_number=IDENTITY.pr,
        head_sha=IDENTITY.head_sha, run_id=IDENTITY.run_id, run_attempt=IDENTITY.run_attempt,
        verdict=audit["verdict"], p1_ids=tuple(item[0] for item in projected),
        p1_findings=projected,
    )
    active = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest=digest,
    )
    assert (active.valid, active.active, active.reason) == (True, True, "active_false_positive")

    moved_audit = {
        **audit,
        "result": {"findings": [{**audit["result"]["findings"][0], "line": 13}]},
    }
    moved_projected = AGG._canonical_p1_findings(moved_audit)
    assert moved_projected is not None
    moved_primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=IDENTITY.repository_id, pr_number=IDENTITY.pr,
        head_sha=IDENTITY.head_sha, run_id=IDENTITY.run_id, run_attempt=IDENTITY.run_attempt,
        verdict=moved_audit["verdict"], p1_ids=tuple(item[0] for item in moved_projected),
        p1_findings=moved_projected,
    )
    stale = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=moved_primary, audit_digest=digest,
    )
    assert (stale.valid, stale.active, stale.reason_code) == (
        False, False, "finding_not_current_p1",
    )

@pytest.mark.parametrize("severity", ["major", "blocker"])
def test_valid_disposition_receipt_releases_the_matching_required_gate_finding(severity):
    audit = _failing_scoped_audit(severity=severity)
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope)
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    resolved = _resolved_line(receipt)
    assert outcome.gate_result == "pass"
    assert outcome.ok is True
    assert outcome.classification == "code_pass"
    assert outcome.reason_code == "disposition_resolved"
    assert outcome.recorded_disposition_claims == [resolved]
    assert "resolved_findings" not in outcome.convergence_envelope
    summary = AGG.render_summary(outcome)
    assert resolved in summary
    assert "**Result: pass**" in summary
    assert "Disposition receipts consumed:" in summary
    terminal = AGG.build_terminal_envelope(
        repository="zlxlabs/gate", identity=IDENTITY, quality_result="success",
        primary_result="failure", review_expected=True, is_draft=False, runner="self",
        outcome=outcome,
    )
    panel_row = AGG._terminal_row(
        terminal, repository="zlxlabs/gate",
        repository_id=IDENTITY.repository_id, pr_number=IDENTITY.pr,
    )
    panel = AGG.render_status_panel([panel_row])
    pr_panel_has_required_line = resolved in panel
    assert pr_panel_has_required_line is True
    assert "Disposition receipts consumed:" in panel

def test_digest_mismatch_keeps_finding_active_and_gate_fail():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope, audit_digest=_DIGEST_B)
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    assert outcome.gate_result == "fail"
    assert outcome.reason_code == "primary_findings"
    assert outcome.recorded_disposition_claims == []
    assert "resolved_findings" not in (outcome.convergence_envelope or {})
    assert _terminal_for(outcome)["disposition_receipt_consumption"]["rejected_reasons"] == {
        "audit_digest_mismatch": 1,
    }

def test_old_head_receipt_keeps_finding_active_and_gate_fail():
    from dataclasses import replace

    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    old_scope = replace(scope, head_sha="f" * 40)
    receipt = _false_positive_receipt(
        old_scope, head_sha=old_scope.head_sha,
    )
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    assert outcome.gate_result == "fail"
    assert outcome.reason_code == "primary_findings"
    assert _terminal_for(outcome)["disposition_receipt_consumption"]["rejected_reasons"] == {
        "head_sha_mismatch": 1,
    }

def test_old_format_receipt_without_counterevidence_cannot_pass():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(
        scope, schema_version=2, counterevidence=None,
    )
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    assert outcome.gate_result == "fail"
    assert _terminal_for(outcome)["disposition_receipt_consumption"]["rejected_reasons"] == {
        "schema_version_mismatch": 1,
    }

def test_finding_id_mismatch_keeps_finding_active_and_gate_fail():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope, finding_id="other-finding")
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    assert outcome.gate_result == "fail"
    assert outcome.reason_code == "primary_findings"
    assert outcome.recorded_disposition_claims == []

def test_missing_reason_keeps_finding_active_and_gate_fail():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope, reason="")
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    assert outcome.gate_result == "fail"
    assert outcome.reason_code == "primary_findings"
    assert outcome.recorded_disposition_claims == []

def test_duplicate_disposition_receipt_is_idempotent_and_releases_once():
    audit = _failing_scoped_audit()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope)
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt, receipt))
    assert outcome.gate_result == "pass"
    assert _terminal_for(outcome)["disposition_receipt_consumption"]["consumed_count"] == 1
    assert outcome.recorded_disposition_claims == [
        _resolved_line(receipt)
    ]

def test_no_receipts_keeps_required_fail_byte_identical_to_baseline():
    audit = _failing_scoped_audit()
    without = _evaluate_failing_primary(audit, waiver_receipts=())
    explicit_empty = _evaluate_failing_primary(audit)
    assert without.gate_result == "fail"
    assert without.recorded_disposition_claims == []
    assert AGG.render_summary(without) == AGG.render_summary(explicit_empty)
    assert without.convergence_envelope == explicit_empty.convergence_envelope
    assert "resolved_findings" not in without.convergence_envelope

def test_measured_p1_receipt_cannot_resolve_required_gate():
    audit = _failing_scoped_audit()
    audit["result"]["findings"][0]["trigger_kind"] = "measured"
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope)
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    assert outcome.gate_result == "fail"
    assert outcome.recorded_disposition_claims == []

@pytest.mark.parametrize("trigger_kind", ["inferred", "measured", "unmeasurable", None, "unknown"])
def test_p1_receipt_trigger_kind_controls_gate_and_terminal_payload(trigger_kind):
    audit = _failing_scoped_audit()
    finding = audit["result"]["findings"][0]
    if trigger_kind is None:
        finding.pop("trigger_kind")
    else:
        finding["trigger_kind"] = trigger_kind
    scope = _scope_for(audit)
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(_false_positive_receipt(scope),))
    terminal = _terminal_for(outcome)
    block = terminal["disposition_receipt_consumption"]
    if trigger_kind == "inferred":
        assert outcome.gate_result == "pass"
        assert len(block["recorded"]) == 1
    else:
        assert outcome.gate_result == "fail"
        assert block["resolved"] == []
        assert block["rejected_reasons"] == {"finding_trigger_not_inferred": 1}

def test_malformed_receipt_stays_blocked_in_terminal_payload():
    audit = _failing_scoped_audit()
    outcome = _evaluate_failing_primary(audit, waiver_receipts=({"finding_id": "p1"},))
    block = _terminal_for(outcome)["disposition_receipt_consumption"]
    assert outcome.gate_result == "fail"
    assert block["rejected_reasons"] == {"malformed_receipt": 1}
    assert block["fail_closed"] is False

def test_partial_disposition_records_claim_and_keeps_only_uncovered_finding_blocking():
    audit = _valid_scoped_primary_record(
        verdict="fail",
        result={
            "findings": [
                {"id": "keep", "severity": "major", "file": "src/keep.py", "line": 10, "category": "correctness"},
                {"id": "drop", "severity": "blocker", "trigger_kind": "inferred", "file": "src/drop.py", "line": 12, "category": "correctness"},
            ]
        },
    )
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope, finding_id="drop")
    outcome = _evaluate_failing_primary(audit, waiver_receipts=(receipt,))
    assert outcome.gate_result == "fail"
    assert outcome.reason_code == "primary_findings"
    assert _terminal_for(outcome)["disposition_receipt_consumption"]["remaining_p1_ids"] == ["keep"]
    assert outcome.recorded_disposition_claims == [
        _resolved_line(receipt)
    ]

def test_fetch_disposition_receipts_skips_other_pr_and_expired(monkeypatch):
    scope = _scope_for(_failing_scoped_audit())
    mine = _false_positive_receipt(scope)
    other = _false_positive_receipt(scope, pr_number=99)
    payloads = {
        "https://api.github.com/artifacts/mine/zip": _zip_receipt_bytes(
            {**mine.as_dict(), "kind": CONV.DISPOSITION_RECEIPT_KIND}
        ),
        "https://api.github.com/artifacts/other/zip": _zip_receipt_bytes(
            {**other.as_dict(), "kind": CONV.DISPOSITION_RECEIPT_KIND}
        ),
    }

    def fake_github_json(*, token, url, **kwargs):
        return {
            "artifacts": [
                {
                    "name": CONV.disposition_receipt_artifact_name(mine),
                    "expired": False,
                    "archive_download_url": "https://api.github.com/artifacts/mine/zip",
                },
                {
                    "name": CONV.disposition_receipt_artifact_name(other),
                    "expired": False,
                    "archive_download_url": "https://api.github.com/artifacts/other/zip",
                },
                {
                    "name": "gate-disposition-receipt-v2-expired",
                    "expired": True,
                    "archive_download_url": "https://api.github.com/artifacts/expired/zip",
                },
            ]
        }

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    monkeypatch.setattr(
        AGG, "_download_terminal_zip",
        lambda **kwargs: payloads[kwargs["url"]],
    )
    receipts = AGG._fetch_disposition_receipts(
        token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )
    assert receipts == (mine,)

@pytest.mark.parametrize("has_p1", [True, False], ids=["primary-p1", "primary-clean"])
def test_disposition_receipt_read_error_is_visible_and_does_not_change_gate(monkeypatch, capsys, has_p1):
    audit = (
        _failing_scoped_audit()
        if has_p1
        else _valid_scoped_primary_record(verdict="pass", result={"findings": []})
    )

    def fail_listing(**kwargs):
        raise RuntimeError("private response body must not be logged")

    monkeypatch.setattr(AGG, "_github_json", fail_listing)
    receipts = AGG._fetch_disposition_receipts(
        token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )
    outcome = AGG.evaluate(
        **_base_kwargs(
            primary_result="failure" if has_p1 else "success",
            audit=audit,
            scope=_scope_for(audit),
            audit_digest=CONV.canonical_audit_digest(audit),
            waiver_receipts=receipts,
        )
    )

    assert receipts == ()
    assert outcome.gate_result == ("fail" if has_p1 else "pass")
    assert outcome.convergence_envelope["state"]["clean_streak"] == (0 if has_p1 else 1)
    warning = capsys.readouterr().out
    assert "Disposition receipt scan failed" in warning
    assert "RuntimeError" in warning
    assert "private response body" not in warning

def test_dual_read_fixtures_copy_b1_canary_key_layout():
    assert _CANARY_TERMINAL_KEY == (
        "d30/1327629472/gate-terminal-v1-1327629472-"
        "2cdbc1bd4719665b7ba7548ba04edea73ec65ffc-35082772768-1/gate-terminal.json"
    )
    assert _CANARY_AUDIT_KEY == (
        "d14/1327629472/primary-audit-v2-1327629472-"
        "2cdbc1bd4719665b7ba7548ba04edea73ec65ffc-35082772768-1/primary-review-audit.json"
    )

def test_mixed_legacy_and_new_terminal_history_prefers_explicit_eligibility(monkeypatch):
    fixture, objects = _real_convergence_history_silo_objects()
    new_terminal = dict(fixture["terminal_envelopes"][0])
    new_terminal.update({
        "run_id": 35429999999,
        "head_sha": "f" * 40,
        "convergence_shadow": {
            "history_status": "available",
            "eligible_rounds": 1,
            "limit": 8,
            "terminal_decision": "collecting",
            "eligible_this_round": False,
        },
    })
    new_name = (
        f"gate-terminal-v1-{fixture['repository_id']}-{new_terminal['head_sha']}-"
        f"{new_terminal['run_id']}-{new_terminal['run_attempt']}"
    )
    objects.append((
        f"d30/{fixture['repository_id']}/{new_name}/gate-terminal.json",
        json.dumps(new_terminal, sort_keys=True).encode("utf-8"),
    ))
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    targeted_ids = [record["run_id"] for record in fixture["terminal_envelopes"]] + [new_terminal["run_id"]]
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
    status, run_ids, _history = AGG._load_pr_convergence_history(
        repository=fixture["repository"], repository_id=fixture["repository_id"],
        pr_number=fixture["pr_number"],
    )
    assert (status, run_ids) == ("available", (35424466527, 35423752741))

def test_one_gate_run_lists_terminal_silo_prefix_once_and_panel_reuses_cache(monkeypatch, tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_scoped_primary_record()))
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_disposition_receipts", lambda **kwargs: ())
    monkeypatch.setattr(
        AGG, "load_previous_round_findings",
        lambda **kwargs: {"available": False, "detail": "fixture", "findings": []},
    )
    monkeypatch.setattr(
        AGG, "_fetch_github_terminal_history",
        lambda **kwargs: AGG.HistoryLoad(
            incomplete_reasons=["no terminal artifact matched gate-terminal-v1-123-"],
        ),
    )
    terminal_prefix_calls = []

    def fake_silo_cli(argv, timeout_s):
        prefix = argv[argv.index("--prefix") + 1]
        if prefix.startswith("d30/123/gate-terminal-v1-123-"):
            terminal_prefix_calls.append(prefix)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    assert AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))) == 0
    cache_path = AGG._terminal_history_cache_path(terminal_path)
    cache_bytes = cache_path.read_bytes()
    assert cache_bytes.endswith(b"\n")
    assert json.loads(cache_bytes) == {
        "schema_version": AGG.TERMINAL_HISTORY_CACHE_SCHEMA_VERSION,
        "rows": [],
        "skipped_records": [],
        "incomplete_reasons": [],
    }

    received = {}

    def fake_panel_publish(**kwargs):
        received["history"] = kwargs["silo_history"]
        return "", {"delivery": "not_created", "reason_code": "test", "history_error": None}

    monkeypatch.setattr(AGG, "_post_status_panel_fail_open", fake_panel_publish)
    publish_args = _cli_args(audit_dir, summary_path, terminal_path=str(terminal_path)) + ["--publish-only"]
    assert AGG.main(publish_args) == 0
    assert isinstance(received["history"], AGG.HistoryLoad)
    assert received["history"].rows == []
    assert terminal_prefix_calls == ["d30/123/gate-terminal-v1-123-"]

@pytest.mark.parametrize(
    "case",
    ["github_only", "silo_only", "both_same", "both_empty", "github_fail", "silo_fail"],
)
def test_disposition_receipts_dual_read_table(monkeypatch, capsys, case):
    github_receipt = _canary_receipt(reason="from-github")
    silo_receipt = _canary_receipt(reason="from-silo")
    other = _canary_receipt(reason="from-silo-only", finding_id="p2")
    github_zip = _zip_receipt_bytes(_receipt_payload(github_receipt))
    github_art = [{
        "name": CONV.disposition_receipt_artifact_name(github_receipt),
        "expired": False,
        "archive_download_url": "https://api.github.com/artifacts/receipt/zip",
    }]
    kwargs = dict(github_artifacts=[], github_blobs={}, silo_objects=[])
    if case == "github_only":
        kwargs = dict(
            github_artifacts=github_art,
            github_blobs={"https://api.github.com/artifacts/receipt/zip": github_zip},
            silo_objects=[],
        )
    elif case == "silo_only":
        kwargs = dict(
            github_artifacts=[], github_blobs={},
            silo_objects=[(_receipt_key(silo_receipt), json.dumps(_receipt_payload(silo_receipt)).encode())],
        )
    elif case == "both_same":
        kwargs = dict(
            github_artifacts=github_art,
            github_blobs={"https://api.github.com/artifacts/receipt/zip": github_zip},
            silo_objects=[(_receipt_key(silo_receipt), json.dumps(_receipt_payload(silo_receipt)).encode())],
        )
    elif case == "github_fail":
        kwargs = dict(
            github_artifacts=[], github_blobs={},
            silo_objects=[(_receipt_key(other), json.dumps(_receipt_payload(other)).encode())],
            github_error=RuntimeError("private response body must not be logged"),
        )
    elif case == "silo_fail":
        kwargs = dict(
            github_artifacts=github_art,
            github_blobs={"https://api.github.com/artifacts/receipt/zip": github_zip},
            silo_objects=[],
            silo_error=RuntimeError("silo listing down"),
        )
    _install_dual_read(monkeypatch, **kwargs)
    receipts = AGG._fetch_disposition_receipts(
        token="tok", repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    warning = capsys.readouterr().out
    reasons = [receipt.reason for receipt in receipts]
    names = [CONV.disposition_receipt_artifact_name(receipt) for receipt in receipts]
    if case == "github_only":
        assert reasons == ["from-github"]
        assert "Silo disposition receipt scan failed" not in warning
    elif case == "silo_only":
        assert reasons == ["from-silo"]
        assert "Silo disposition receipt scan failed" not in warning
    elif case == "both_same":
        assert reasons == ["from-silo"]
        assert names == [CONV.disposition_receipt_artifact_name(silo_receipt)]
    elif case == "both_empty":
        assert receipts == ()
        assert "scan failed" not in warning
    elif case == "github_fail":
        assert reasons == ["from-silo-only"]
        assert "Disposition receipt scan failed" in warning
        assert "RuntimeError" in warning
        assert "private response body" not in warning
    elif case == "silo_fail":
        assert reasons == ["from-github"]
        assert "Silo disposition receipt scan failed" in warning
        assert "RuntimeError" in warning

def test_dual_read_merge_keeps_same_key_receipts_with_different_finding_ids(monkeypatch):
    # gate#240: two same-key P1s (same file, line null) legitimately produce
    # two receipts that differ only by finding_id; the name-keyed merge must
    # keep both instead of letting one swallow the other.
    scope = _scope_for(_failing_scoped_audit())
    same_key = CONV.canonical_finding_key(
        {"file": "src/lock.py", "line": None, "category": "correctness", "severity": "major"}
    )
    first = _false_positive_receipt(
        scope, repository_id=str(_CANARY_REPO_ID), finding_id="p1", finding_key=same_key,
    )
    second = _false_positive_receipt(
        scope, repository_id=str(_CANARY_REPO_ID), finding_id="p2", finding_key=same_key,
    )
    assert CONV.disposition_receipt_artifact_name(first) != CONV.disposition_receipt_artifact_name(second)

    def github_artifact(receipt, index):
        return {
            "name": CONV.disposition_receipt_artifact_name(receipt),
            "expired": False,
            "archive_download_url": f"https://api.github.com/artifacts/{index}/zip",
        }

    _install_dual_read(
        monkeypatch,
        github_artifacts=[github_artifact(first, 1), github_artifact(second, 2)],
        github_blobs={
            "https://api.github.com/artifacts/1/zip": _zip_receipt_bytes(_receipt_payload(first)),
            "https://api.github.com/artifacts/2/zip": _zip_receipt_bytes(_receipt_payload(second)),
        },
        silo_objects=[
            (_receipt_key(first), json.dumps(_receipt_payload(first)).encode()),
            (_receipt_key(second), json.dumps(_receipt_payload(second)).encode()),
        ],
    )
    receipts = AGG._fetch_disposition_receipts(
        token="tok", repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42,
    )
    assert len(receipts) == 2
    assert {receipt.finding_id for receipt in receipts} == {"p1", "p2"}

def test_silo_objects_under_cli_path_contract(monkeypatch):
    import subprocess

    prefix = f"d30/{_CANARY_REPO_ID}/"
    canonical_key = f"d30/{_CANARY_REPO_ID}/artifact-abc/receipt.json"
    payload = b'{"receipt": true}'
    recorded_calls = []

    dest_existed = []
    recorded_timeouts = []

    def fake_cli(argv, timeout_s):
        recorded_calls.append(list(argv))
        recorded_timeouts.append(timeout_s)
        assert "--dest" in argv
        dest_idx = argv.index("--dest")
        dest_dir = Path(argv[dest_idx + 1])
        dest_existed.append(dest_dir.is_dir())
        parts = canonical_key.split("/", 3)
        file_path = dest_dir / parts[2] / parts[3]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(payload)
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=f"{canonical_key}\n",
            stderr="",
        )

    monkeypatch.setattr(AGG, "_silo_cli", fake_cli)
    objects = AGG._silo_objects_under(prefix, AGG.SILO_CLI_CALL_TIMEOUT_SECONDS)

    # 断言走 CLI 路径及 argv 形态
    assert len(recorded_calls) == 1
    argv = recorded_calls[0]
    assert argv[0:3] == ["list", "--prefix", prefix]
    assert argv[3] == "--dest"
    assert dest_existed == [True]
    assert recorded_timeouts == [AGG.SILO_CLI_CALL_TIMEOUT_SECONDS]

    # 断言 stdout 键解析与 (key, body) 返回
    assert objects == [(canonical_key, payload)]

    # 断言四段键校验
    def bad_key_cli(argv, timeout_s):
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout="invalid-two-segment/key\n",
            stderr="",
        )

    monkeypatch.setattr(AGG, "_silo_cli", bad_key_cli)
    with pytest.raises(ValueError) as exc:
        AGG._silo_objects_under(prefix, AGG.SILO_CLI_CALL_TIMEOUT_SECONDS)
    assert "silo key is not tier/repo_id/artifact_name/path" in str(exc.value)
