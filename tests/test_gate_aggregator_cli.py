from _gate_aggregator_support import _assert_terminal_classification
from test_gate_aggregator import _cli_args
from test_gate_aggregator import (
    AGG,
    CONV,
    IDENTITY,
    MODULE_PATH,
    PR_AUTHOR_REST_FIXTURE,
    Path,
    ROOT,
    _base_kwargs,
    _failing_scoped_audit,
    _false_positive_receipt,
    _primary_convergence_history,
    _primary_convergence_row,
    _resolved_line,
    _run_pr_convergence_cli,
    _scope_for,
    _valid_primary_record,
    _valid_scoped_primary_record,
    _visible_scenario,
    _write_relation_audit,
    _zip_receipt_bytes,
    hashlib,
    json,
    os,
    pytest,
    subprocess,
    sys,
)

def test_main_exit_code_zero_on_pass(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary_path))
    assert rc == 0
    assert "pass" in summary_path.read_text() and json.loads(summary_path.with_name("gate-terminal.json").read_text())["kind"] == "gate_terminal"
@pytest.mark.parametrize("author_json,expected", [
    (json.dumps(PR_AUTHOR_REST_FIXTURE["user"]["login"]), "skipped"),
    (json.dumps("dependabot[bot]"), "unavailable"), (None, "unavailable"),
    ("not-json", "unavailable"),
])
def test_cli_consumes_pr_author_json_instead_of_human_rerun_actor(tmp_path, monkeypatch, author_json, expected):
    monkeypatch.setenv("GITHUB_ACTOR", "zj1123581321")
    summary = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(
        tmp_path / "missing-audit", summary, primary_result="skipped", runner="hosted",
        review_expected="false", classify_review_expected="false", pr_author_json=author_json,
    ))
    terminal = json.loads(summary.with_name("gate-terminal.json").read_text())
    assert terminal["gate_result"] == expected
    assert rc == (0 if expected == "skipped" else 1)
def test_main_writes_one_canonical_receipt_for_scoped_primary(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit = _valid_scoped_primary_record()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    summary_path = tmp_path / "summary.md"
    receipt_path = tmp_path / "convergence-receipt" / "convergence-receipt.json"

    rc = AGG.main(
        _cli_args(
            audit_dir,
            summary_path,
            convergence_receipt_path=str(receipt_path),
        )
    )

    assert rc == 0
    assert receipt_path.is_file()
    assert list(receipt_path.parent.iterdir()) == [receipt_path]
    payload = json.loads(receipt_path.read_text())
    assert receipt_path.read_bytes() == json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    assert "Convergence receipt: produced (`convergence-receipt.json`).\n" in summary_path.read_text()
def test_cli_missing_one_scope_field_names_it_without_receipt(tmp_path):
    audit = _valid_scoped_primary_record()
    del audit["caller_sha"]

    rc, receipt_path = _run_receipt_cli_case(tmp_path, audit=audit, primary_result="success")

    assert rc == 0
    assert not receipt_path.exists()
    summary = receipt_path.parent.parent.joinpath("summary.md").read_text()
    assert (
        "Convergence receipt: not produced (reason: canonical primary audit is missing "
        "scope field(s): `caller_sha`)."
    ) in summary
def test_cli_missing_multiple_scope_fields_names_all_in_sorted_order(tmp_path):
    audit = _valid_scoped_primary_record()
    for field in ("caller_sha", "reusable_workflow_sha"):
        del audit[field]

    rc, receipt_path = _run_receipt_cli_case(tmp_path, audit=audit, primary_result="success")

    assert rc == 0
    assert not receipt_path.exists()
    summary = receipt_path.parent.parent.joinpath("summary.md").read_text()
    assert (
        "Convergence receipt: not produced (reason: canonical primary audit is missing "
        "scope field(s): `caller_sha, reusable_workflow_sha`)."
    ) in summary
@pytest.mark.parametrize("tier", [None, "unrecognized"])
def test_cli_missing_or_invalid_tier_defaults_to_internal(tmp_path, tier):
    audit = _valid_scoped_primary_record()
    if tier is None:
        del audit["tier"]
    else:
        audit["tier"] = tier

    rc, receipt_path = _run_receipt_cli_case(tmp_path, audit=audit, primary_result="success")

    assert rc == 0
    payload = json.loads(receipt_path.read_bytes())
    assert payload["scope"]["tier"] == "internal"
@pytest.mark.parametrize(
    ("verdict", "primary_result", "expected_rc"),
    [("pass", "success", 0), ("fail", "failure", 1)],
)
def test_cli_missing_scope_field_preserves_exit_and_receipt_semantics(
    tmp_path, verdict, primary_result, expected_rc,
):
    audit = _valid_scoped_primary_record(
        verdict=verdict,
        result={"findings": []} if verdict == "pass" else {"findings": [{"id": "p1", "severity": "major"}]},
    )
    del audit["caller_sha"]

    rc, receipt_path = _run_receipt_cli_case(tmp_path, audit=audit, primary_result=primary_result)

    assert rc == expected_rc
    assert not receipt_path.exists()
def test_main_skipped_round_does_not_write_receipt_and_explains_reason(tmp_path):
    summary_path = tmp_path / "summary.md"
    receipt_path = tmp_path / "convergence-receipt" / "convergence-receipt.json"

    rc = AGG.main(
        _cli_args(
            tmp_path / "missing-audit",
            summary_path,
            primary_result="skipped",
            is_draft="true",
            review_expected="false",
            convergence_receipt_path=str(receipt_path),
        )
    )

    assert rc == 0
    assert not receipt_path.exists()
    summary = summary_path.read_text()
    assert "Convergence receipt: not produced" in summary
    assert "review_not_expected" in summary
def _run_receipt_cli_case(tmp_path, *, audit, primary_result, is_draft="false", review_expected="true"):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    if audit is not None:
        (audit_dir / "primary-review-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    summary_path = tmp_path / "summary.md"
    receipt_path = tmp_path / "convergence-receipt" / "convergence-receipt.json"
    overrides = {
        "primary_result": primary_result,
        "is_draft": is_draft,
        "review_expected": review_expected,
        "convergence_receipt_path": str(receipt_path),
    }
    return AGG.main(_cli_args(audit_dir, summary_path, **overrides)), receipt_path
def test_cli_exit_zero_with_receipt_for_clean_round(tmp_path):
    audit = _valid_scoped_primary_record()
    rc, receipt_path = _run_receipt_cli_case(tmp_path, audit=audit, primary_result="success")

    assert rc == 0
    assert receipt_path.is_file()
    assert json.loads(receipt_path.read_text())["clean_streak"] == 1
def test_cli_exit_zero_without_receipt_for_expected_skip(tmp_path):
    rc, receipt_path = _run_receipt_cli_case(
        tmp_path, audit=None, primary_result="skipped", is_draft="true", review_expected="false",
    )

    assert rc == 0
    assert not receipt_path.exists()
def test_cli_draft_payload_skip_uses_event_and_makes_no_github_request(tmp_path, monkeypatch):
    # A draft event has its own check name, so its verdict describes the draft
    # stage even when the PR has since been marked ready.
    requests = []
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        AGG, "_github_request",
        lambda **kwargs: (requests.append(kwargs["url"]), b'{"draft": false}')[1],
    )
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_scoped_primary_record()))
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(
        _cli_args(
            audit_dir, summary_path,
            primary_result="skipped", is_draft="true", review_expected="false",
            runner="hosted", is_fork="true", classify_review_expected="false",
        )
    )

    assert rc == 0
    assert requests == []
    text = summary_path.read_text()
    assert "draft event check records quality for the draft stage" in text
    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_text())
    assert terminal["classification"] == "expected_skip"
    assert terminal["reason_code"] == "review_not_expected"
    assert terminal["gate_result"] == "skipped"
@pytest.mark.parametrize(
    "primary_result,expected_classification,expected_reason",
    [
        ("skipped", "integration_error", "unexpected_primary_skip"),
        ("cancelled", "review_unavailable", "primary_cancelled"),
    ],
)
def test_cli_non_draft_missing_primary_remains_fail_closed(
    tmp_path, primary_result, expected_classification, expected_reason,
):
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(
        _cli_args(
            tmp_path / "missing-audit", summary_path,
            primary_result=primary_result, is_draft="false", review_expected="true",
        )
    )

    assert rc == 1
    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_text())
    assert terminal["classification"] == expected_classification
    assert terminal["reason_code"] == expected_reason
    assert terminal["gate_result"] == "unavailable"
@pytest.mark.parametrize("verdict", ["fail", "unavailable"])
def test_cli_exit_nonzero_with_zero_streak_receipt_for_red_or_unavailable_round(tmp_path, verdict):
    audit = _valid_scoped_primary_record(
        verdict=verdict,
        result={"findings": [{
            "id": "p1", "severity": "major", "file": "src/lock.py", "line": 12,
            "category": "correctness",
        }]},
    )
    rc, receipt_path = _run_receipt_cli_case(tmp_path, audit=audit, primary_result="failure")

    assert rc != 0
    assert receipt_path.is_file()
    assert json.loads(receipt_path.read_text())["clean_streak"] == 0
def test_cli_exit_nonzero_without_receipt_for_fail_closed_audit(tmp_path):
    rc, receipt_path = _run_receipt_cli_case(tmp_path, audit=None, primary_result="failure")

    assert rc != 0
    assert not receipt_path.exists()
def test_main_summary_and_notice_include_cross_attempt_source(capsys, tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit = _valid_primary_record(run_attempt=1)
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(audit))
    summary_path = tmp_path / "summary.md"
    args = _cli_args(audit_dir, summary_path, run_attempt="2", audit_source_attempt="1")
    rc = AGG.main(args)
    assert rc == 0
    assert "source run_attempt=1" in summary_path.read_text()
    assert "source run_attempt=1" in capsys.readouterr().out
def test_main_exit_code_nonzero_on_missing_audit(tmp_path):
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(tmp_path / "nope", summary_path))
    assert rc == 1
    assert "Synthetic audit generated" in summary_path.read_text()
    assert json.loads(summary_path.with_name("gate-terminal.json").read_text())["audit"] == {"available": False, "source_attempt": None, "artifact_name": None}
def test_main_exit_code_nonzero_on_malformed_boolean_input(tmp_path):
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(tmp_path / "nope", summary_path, is_draft="banana"))
    assert rc == 1
    text = summary_path.read_text()
    assert "malformed boolean input" in text
def test_main_exit_code_nonzero_on_unknown_runner(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary_path, runner="slef"))
    assert rc == 1
    assert "runner input" in summary_path.read_text()
@pytest.mark.parametrize(
    "facts,expected_reason,expected_draft",
    [
        ({"is_draft": True, "is_fork": True, "runner": "hosted", "classify_review_expected": "false"}, "draft", True),
        ({"is_draft": False, "is_fork": True, "runner": "hosted", "classify_review_expected": "false"}, "fork", False),
        ({"is_draft": False, "is_fork": False, "runner": "hosted", "classify_review_expected": "false"}, "hosted_runner", False),
        ({"is_draft": False, "is_fork": False, "runner": "self", "classify_review_expected": "false"}, "review_exempt", False),
    ],
)
def test_primary_skip_reason_uses_locked_precedence(facts, expected_reason, expected_draft):
    outcome = AGG.evaluate(**_base_kwargs(
        primary_result="skipped", review_expected=False, audit=None, **facts,
    ))
    assert outcome.skip_reason == expected_reason
    assert facts["is_draft"] is expected_draft
    if expected_reason == "review_exempt":
        assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
            "expected_skip", "review_not_expected", "skipped",
        )
def test_primary_skip_without_a_matching_reason_fails_closed():
    outcome = AGG.evaluate(**_base_kwargs(
        primary_result="skipped", is_draft=False, is_fork=False, runner="self",
        classify_review_expected="true", review_expected=False, audit=None,
    ))
    assert outcome.ok is False
    assert outcome.skip_reason is None
    assert (outcome.classification, outcome.reason_code) == (
        "integration_error", "unexpected_primary_skip",
    )
    assert outcome.gate_result != "skipped"
def test_primary_skip_with_missing_classify_fact_fails_closed():
    kwargs = _base_kwargs(
        primary_result="skipped", is_draft=False, review_expected=False, audit=None,
    )
    outcome = AGG.evaluate(**kwargs)
    assert outcome.ok is False
    assert outcome.skip_reason is None
    assert outcome.gate_result != "skipped"
def test_primary_skip_reason_domain_lock():
    assert AGG.PRIMARY_SKIP_REASON_DOMAIN == ("draft", "review_exempt", "fork", "hosted_runner")
def test_cli_draft_quality_failure_stays_red_without_github_request(tmp_path, monkeypatch):
    requests = []
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        AGG, "_github_request",
        lambda **kwargs: (requests.append(kwargs["url"]), b'{"draft": false}')[1],
    )
    summary_path = tmp_path / "summary.md"
    rc = AGG.main(
        _cli_args(
            tmp_path / "missing-audit", summary_path,
            quality_result="failure", caller_checks="failed",
            primary_result="skipped", is_draft="true", review_expected="false",
        )
    )
    assert rc == 1
    assert requests == []
    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_text())
    assert terminal["classification"] == "ci_failure"
    assert terminal["reason_code"] == "quality_failure"
    assert terminal["gate_result"] == "fail"
def test_main_terminal_omits_primary_verdict_when_findings_are_malformed(tmp_path):
    audit = _valid_scoped_primary_record()
    audit["result"]["findings"] = [{"severity": "unknown"}]
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(audit))
    summary_path = tmp_path / "summary.md"

    assert AGG.main(_cli_args(audit_dir, summary_path)) == 1
    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_bytes())

    assert (terminal["gate_result"], terminal["reason_code"]) == ("unavailable", "audit_invalid")
    assert "primary_verdict" not in terminal
def test_main_fail_primary_plus_matching_receipt_resolves_p1(monkeypatch, tmp_path):
    audit = _failing_scoped_audit()
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    raw = json.dumps(audit, indent=2).encode("utf-8") + b"\n"
    (audit_dir / "primary-review-audit.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    scope = _scope_for(audit)
    receipt = _false_positive_receipt(scope, audit_digest=digest)
    payload = {**receipt.as_dict(), "kind": CONV.DISPOSITION_RECEIPT_KIND}
    artifact_name = CONV.disposition_receipt_artifact_name(receipt)
    zip_bytes = _zip_receipt_bytes(payload)

    def fake_github_json(*, token, url, **kwargs):
        assert "actions/artifacts" in url
        return {
            "artifacts": [
                {
                    "name": artifact_name,
                    "expired": False,
                    "archive_download_url": "https://api.github.com/artifacts/1/zip",
                }
            ]
        }

    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    monkeypatch.setattr(AGG, "_download_terminal_zip", lambda **kwargs: zip_bytes)

    summary_path = tmp_path / "summary.md"
    rc = AGG.main(
        _cli_args(audit_dir, summary_path, primary_result="failure")
    )
    text = summary_path.read_text()
    resolved = _resolved_line(receipt)
    assert rc == 0
    assert "**Result: pass**" in text
    assert "Disposition receipts consumed:" in text
    assert resolved in text
    assert "All current P1 findings are covered" in text
    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_text())
    assert terminal["gate_result"] == "pass"
    assert terminal["reason_code"] == "disposition_resolved"
@pytest.mark.parametrize(
    "run_501_head,run_504_head",
    [("c" * 40, "b" * 40), (IDENTITY.head_sha, IDENTITY.head_sha)],
    ids=["three-distinct-pr-heads", "same-pr-head"],
)
def test_cli_convergence_counts_latest_actual_attempt_per_run_and_known_skips(
    monkeypatch, tmp_path, run_501_head, run_504_head,
):
    history = _primary_convergence_history((501, 502, 503, 504, IDENTITY.run_id), [
        _primary_convergence_row(501, 1, "fail", head_sha=run_501_head),
        _primary_convergence_row(501, 2, "unavailable", head_sha=run_501_head),
        _primary_convergence_row(502, 1, "fail"), _primary_convergence_row(502, 2, "pass"),
        _primary_convergence_row(503, 1, None, primary_result="skipped", skip_reason="draft"),
        _primary_convergence_row(504, 1, "fail", head_sha=run_504_head),
    ])
    exit_code, terminal = _run_pr_convergence_cli(
        tmp_path, monkeypatch, history=history, audit_verdict="fail", primary_result="failure",
    )
    assert exit_code == 1
    assert terminal.get("convergence") == {"fail_count": 3, "reconsider": True}
@pytest.mark.parametrize(
    "history,audit_verdict,primary_result,expected",
    [
        (_primary_convergence_history((501, IDENTITY.run_id), [
            _primary_convergence_row(501, 1, None, primary_result="failure", audit_available=True),
        ]), "fail", "failure", {"fail_count": None, "reconsider": None}),
        (_primary_convergence_history((501, 502, 503, IDENTITY.run_id), [
            _primary_convergence_row(501, 1, "fail"), _primary_convergence_row(502, 1, "fail"),
        ]), "fail", "failure", {"fail_count": None, "reconsider": True}),
        (None, None, "failure", {"fail_count": None, "reconsider": None}),
    ],
    ids=["legacy-reviewed-verdict-missing-never-use-gate-result", "target-gap-three-failure-lower-bound", "current-reviewed-verdict-missing"],
)
def test_cli_convergence_keeps_unknown_history_and_verified_lower_bound(
    history, audit_verdict, primary_result, expected, monkeypatch, tmp_path,
):
    exit_code, terminal = _run_pr_convergence_cli(
        tmp_path, monkeypatch, history=history, audit_verdict=audit_verdict, primary_result=primary_result,
    )
    assert exit_code == 1
    assert terminal.get("convergence") == expected
def test_main_terminal_convergence_reads_target_run_gap_and_quality_fail_primary_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_pr_target_run_ids", lambda **kwargs: [700, IDENTITY.run_id])
    monkeypatch.setattr(AGG, "_fetch_silo_terminal_history", lambda **kwargs: AGG.HistoryLoad())
    monkeypatch.setattr(AGG, "_fetch_github_terminal_history", lambda **kwargs: AGG.HistoryLoad(
        incomplete_reasons=[f"no terminal artifact matched run {run_id}" for run_id in (700, IDENTITY.run_id)],
    ))
    observed = []
    load_history = AGG._load_pr_convergence_history
    def record_loaded_history(**kwargs):
        result = load_history(**kwargs)
        observed.append(getattr(result[2], "target_run_ids", None))
        return result
    monkeypatch.setattr(AGG, "_load_pr_convergence_history", record_loaded_history)

    exit_code, terminal = _run_pr_convergence_cli(
        tmp_path, monkeypatch, audit_verdict="pass", primary_result="success",
        quality_result="failure", caller_checks="failed",
    )
    assert (exit_code, terminal["gate_result"], terminal["primary_verdict"]) == (1, "fail", "pass")
    assert observed == [(700, IDENTITY.run_id)]
    assert terminal.get("convergence") == {"fail_count": None, "reconsider": False}
def test_cli_evidence_reaches_the_verdict(tmp_path):
    # E2E-Assertion 的消费侧一半：--caller-checks/--preflight-result 经 CLI
    # 到达判据（生产侧一半由契约测试锁 YAML→env→argv 接线）。
    _, summary_path, args = _visible_scenario(
        tmp_path, {"quality_result": "failure", "caller_checks": "", "preflight_result": ""}, "__default__",
    )
    rc = AGG.main(args)
    assert rc == 1
    summary = summary_path.read_text()
    assert "quality_infra" in summary
    assert "review_unavailable" in summary
def test_cli_rejects_present_null_finding_relation(tmp_path):
    audit_dir = _write_relation_audit(tmp_path, {"id": "a"})
    audit_path = audit_dir / "primary-review-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["finding_relation"] = None
    audit_path.write_text(json.dumps(audit))
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), *_cli_args(audit_dir, tmp_path / "summary.md")],
        cwd=ROOT, env={**os.environ, "SILO_ENDPOINT": "", "SILO_STORE": "", "GITHUB_TOKEN": "", "GH_TOKEN": ""},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "GATE-FINDING-RELATION-UNKNOWN: block schema" in result.stderr
