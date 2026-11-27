"""Domain-focused tests migrated without changing assertions."""
from test_gate_aggregator import (
    AGG,
    CONV,
    CONVERGENCE_HISTORY_FIXTURE,
    IDENTITY,
    MODULE_PATH,
    Path,
    ROOT,
    _CANARY_ATTEMPT,
    _CANARY_REPO_ID,
    _CANARY_RUN_ID,
    _CANARY_SHA,
    _DIGEST_A,
    _base_kwargs,
    _cli_args,
    _valid_primary_record,
    _valid_scoped_primary_record,
    hashlib,
    io,
    json,
    os,
    pytest,
    subprocess,
    sys,
    zipfile,
)

@pytest.mark.parametrize(
    "case,expected_primary,expected_reason,expected_draft,expected_gate_result,expected_classification,expected_reason_code",
    [
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "false", "review_expected": "false"}, "skipped", "review_exempt", False, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "skipped", "is_draft": "true", "is_fork": "true", "runner": "hosted", "classify_review_expected": "false", "review_expected": "false"}, "skipped", "draft", True, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "true", "runner": "self", "classify_review_expected": "true", "review_expected": "false"}, "skipped", "fork", False, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "false", "runner": "hosted", "classify_review_expected": "true", "review_expected": "false"}, "skipped", "hosted_runner", False, "skipped", "expected_skip", "review_not_expected"),
        ({"primary_result": "success", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "true", "review_expected": "true"}, "executed", None, False, "pass", "code_pass", "primary_pass"),
        ({"primary_result": "failure", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "true", "review_expected": "true"}, "executed", None, False, "fail", "code_fail", "primary_findings"),
        ({"primary_result": "skipped", "is_draft": "false", "is_fork": "false", "runner": "self", "classify_review_expected": "true", "review_expected": "false"}, "skipped", None, False, "unavailable", "integration_error", "unexpected_primary_skip"),
    ],
)
def test_real_aggregator_subprocess_publishes_one_verdict_line(
    tmp_path, case, expected_primary, expected_reason, expected_draft, expected_gate_result,
    expected_classification, expected_reason_code,
):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    if case["primary_result"] in ("success", "failure"):
        (audit_dir / "primary-review-audit.json").write_text(
            json.dumps(_valid_primary_record(verdict="fail" if case["primary_result"] == "failure" else "pass")),
            encoding="utf-8",
        )
    summary_path = tmp_path / "summary.md"
    args = _cli_args(audit_dir, summary_path, **case)
    env = {**os.environ, "GH_TOKEN": "", "GITHUB_TOKEN": ""}
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), *args],
        cwd=ROOT, env=env, capture_output=True, check=False,
    )
    assert result.returncode == (0 if expected_gate_result in ("pass", "skipped") else 1)
    prefix = b"AGENT-GATE-VERDICT-V1 "
    verdict_lines = [line for line in result.stdout.splitlines() if line.startswith(prefix)]
    assert len(verdict_lines) == 1
    verdict = json.loads(verdict_lines[0][len(prefix):])
    assert verdict_lines[0] == prefix + json.dumps(
        verdict, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert verdict == {
        "classification": expected_classification,
        "draft": expected_draft,
        "gate_result": expected_gate_result,
        "primary": expected_primary,
        "reason_code": expected_reason_code,
        "skip_reason": expected_reason,
        "v": 1,
    }
    assert verdict_lines[0].decode("utf-8") in summary_path.read_text(encoding="utf-8")
    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_text(encoding="utf-8"))
    assert terminal["skip_reason"] == expected_reason

def test_quality_skipped_by_primary_failure_reports_short_circuit_not_quality_problem():
    # gate#105 方案 A: when the workflow short-circuits quality because primary
    # already failed, the problems text must say WHY quality did not run (a
    # short-circuit, not a quality defect). The other skipped combination —
    # (quality=skipped, primary=success) — is unchanged and still fails as
    # quality_skipped (matrix cell above).
    outcome = AGG.evaluate(**_base_kwargs(
        quality_result="skipped", primary_result="failure",
        audit=_valid_primary_record(verdict="fail"),
    ))
    assert outcome.ok is False
    assert any("short-circuit" in p for p in outcome.problems)
    assert not any("quality job result is 'skipped'" in p for p in outcome.problems)

@pytest.mark.parametrize(
    "runner,is_fork,classify_review_expected",
    [
        ("self", False, "true"),
        ("hosted", False, "true"),
        ("self", True, "false"),
        ("hosted", True, "false"),
    ],
)
def test_draft_skipped_primary_uses_payload_for_all_caller_modes(
    runner, is_fork, classify_review_expected,
):
    outcome = AGG.evaluate(**_base_kwargs(
        primary_result="skipped", is_draft=True, review_expected=False, audit=None,
        runner=runner, is_fork=is_fork,
        classify_review_expected=classify_review_expected,
    ))
    assert outcome.ok is True
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "expected_skip", "review_not_expected", "skipped",
    )
    assert outcome.skip_reason == "draft"
    assert any("draft stage" in note for note in outcome.notes)

@pytest.mark.parametrize(
    "quality_result,primary_result,verdict,is_draft,review_expected,caller_checks,expected_gate,expected_exit",
    [
        ("failure", "success", "pass", "false", "true", "failed", "fail", 1),
        ("skipped", "failure", "fail", "false", "true", None, "fail", 1),
        ("skipped", "failure", "unavailable", "false", "true", None, "unavailable", 1),
        ("success", "skipped", None, "true", "false", None, "skipped", 0),
    ],
    ids=["quality-failure-with-primary-pass", "primary-fail", "primary-unavailable", "draft-skip"],
)
def test_primary_verdict_is_transported_from_validated_audit_to_terminal_history(
    tmp_path, quality_result, primary_result, verdict, is_draft, review_expected,
    caller_checks, expected_gate, expected_exit,
):
    audit_dir = tmp_path / "audit"
    if verdict is not None:
        audit_dir.mkdir()
        (audit_dir / "primary-review-audit.json").write_text(
            json.dumps(_valid_primary_record(verdict=verdict)), encoding="utf-8",
        )
    summary_path = tmp_path / "summary.md"
    exit_code = AGG.main(_cli_args(
        audit_dir, summary_path,
        quality_result=quality_result, primary_result=primary_result,
        is_draft=is_draft, review_expected=review_expected, caller_checks=caller_checks,
    ))

    terminal = json.loads(summary_path.with_name("gate-terminal.json").read_bytes())
    row = AGG._terminal_row(
        terminal, repository="zlxlabs/gate", repository_id=IDENTITY.repository_id,
        pr_number=IDENTITY.pr,
    )
    assert (exit_code, terminal["gate_result"]) == (expected_exit, expected_gate)
    if verdict is None:
        assert "primary_verdict" not in terminal
    else:
        assert terminal["primary_verdict"] == verdict
    assert row["primary_verdict"] == verdict

@pytest.mark.parametrize("verdict", ["not-a-primary-verdict", "not_expected", "waived"])
def test_invalid_primary_verdict_marks_terminal_history_incomplete(monkeypatch, verdict):
    record = json.loads(_canary_terminal_bytes())
    record["primary_verdict"] = verdict
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("gate-terminal.json", json.dumps(record))
    monkeypatch.setattr(AGG, "_download_terminal_zip", lambda **kwargs: archive.getvalue())
    history = AGG.HistoryLoad()

    AGG._consume_terminal_artifact(
        history,
        artifact={"name": "gate-terminal-v1-1327629472-test", "expired": False,
                  "archive_download_url": "https://api.github.com/artifacts/1/zip"},
        token="test-token", repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID,
        pr_number=42,
    )

    assert history.rows == []
    assert len(history.skipped_records) == 1
    assert "primary_verdict" in history.incomplete_reasons[0]

def _panel_row(run_id, run_attempt, gate_result, *, head_sha=None):
    return {
        "schema_version": AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": "zlxlabs/gate",
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha or (chr(96 + run_id) * 40),
        "gate_result": gate_result,
        "classification": {
            "pass": "code_pass",
            "fail": "code_fail",
            "skipped": "expected_skip",
            "unavailable": "integration_error",
        }[gate_result],
        "reason_code": {
            "pass": "primary_pass",
            "fail": "primary_findings",
            "skipped": "review_not_expected",
            "unavailable": "audit_missing",
        }[gate_result],
    }

def _budget_exhausted_attempt(*, reason=None, exit_code=22, reviewer="reviewer"):
    return {
        "reviewer": reviewer,
        "model": "model",
        "exit_code": AGG.PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE if reason is None else exit_code,
        "reason": AGG.PRIMARY_BUDGET_EXHAUSTED_REASON if reason is None else reason,
        "cost_usd": None,
        "tokens": None,
        "duration_s": 1.0,
    }

def _budget_exhausted_audit(*, attempts=None):
    audit = _valid_primary_record(
        verdict="unavailable",
        attempts=attempts if attempts is not None else [
            _budget_exhausted_attempt(reviewer="reviewer-a"),
            _budget_exhausted_attempt(reviewer="reviewer-b"),
        ],
    )
    return audit

def _failing_scoped_audit(*, finding_id="p1", severity="major"):
    return _valid_scoped_primary_record(
        verdict="fail",
        result={"findings": [{
            "id": finding_id, "severity": severity, "trigger_kind": "inferred",
            "file": "src/lock.py", "line": 12, "category": "correctness",
        }]},
    )

def _scope_for(audit):
    scope, missing = AGG._convergence_scope_from_audit(audit, IDENTITY)
    assert not missing and scope is not None
    return scope

def _gate_rerun(head_sha):
    literal = "locked-behavior"
    pathspec = "src/lock.py"
    argv = ["git", "grep", "-n", "-F", "-e", literal, head_sha, "--", pathspec]
    excerpt = f"{pathspec}:1:{literal}"
    return {
        "argv": argv,
        "exit_code": 0,
        "match_count": 1,
        "stdout_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "excerpt": excerpt,
    }

def _false_positive_receipt(scope, *, audit_digest=_DIGEST_A, finding_id="p1", **changes):
    head_sha = changes.get("head_sha", IDENTITY.head_sha)
    fields = dict(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="false-positive",
        repository_id=str(IDENTITY.repository_id),
        pr_number=IDENTITY.pr,
        epoch=CONV.derive_epoch(scope),
        head_sha=head_sha,
        audit_digest=audit_digest,
        finding_id=finding_id,
        reason="locked upstream behavior",
        counterevidence={
            "command": "pytest -q tests/test_regression.py",
            "output": "1 passed",
            "result": "refuted",
            "pointer": "tests/test_regression.py::test_regression",
            "gate_rerun": _gate_rerun(head_sha),
        },
        approver="octocat",
        approver_id=1,
        approved_at="2026-08-30T12:00:00Z",
    )
    fields.update(changes)
    return CONV.DispositionReceipt(**fields)

def _deferred_receipt(scope, *, audit_digest=_DIGEST_A, finding_id="p1", tracking_issue="#12", **changes):
    fields = dict(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="deferred",
        repository_id=str(IDENTITY.repository_id),
        pr_number=IDENTITY.pr,
        epoch=CONV.derive_epoch(scope),
        head_sha=IDENTITY.head_sha,
        audit_digest=audit_digest,
        finding_id=finding_id,
        reason="tracked below the gate redline",
        tracking_issue=tracking_issue,
        approver="octocat",
        approver_id=1,
        approved_at="2026-08-30T12:00:00Z",
    )
    fields.update(changes)
    return CONV.DispositionReceipt(**fields)

def _resolved_line(receipt):
    return CONV.recorded_disposition_lines(
        CONV.DispositionAudit(
            primary_p1_ids=(),
            recorded_receipts=(receipt,),
            rejected_receipts=(),
            recorded_finding_ids=(receipt.finding_id,),
        )
    )[0]

def _evaluate_failing_primary(audit, *, audit_digest=_DIGEST_A, waiver_receipts=()):
    return AGG.evaluate(
        **_base_kwargs(
            primary_result="failure",
            audit=audit,
            scope=_scope_for(audit),
            audit_digest=audit_digest,
            waiver_receipts=waiver_receipts,
            repository="zlxlabs/gate",
        )
    )

def _zip_receipt_bytes(payload):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr("receipt.json", json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return buf.getvalue()

def _terminal_for(outcome, *, primary_result="failure"):
    return AGG.build_terminal_envelope(
        repository="zlxlabs/gate", identity=IDENTITY, quality_result="success",
        primary_result=primary_result, review_expected=True, is_draft=False, runner="self",
        outcome=outcome,
    )

def _canary_terminal_bytes(gate_result="pass", *, run_id=_CANARY_RUN_ID, run_attempt=_CANARY_ATTEMPT, head_sha=_CANARY_SHA):
    record = {
        "schema_version": 1,
        "kind": "gate_terminal",
        "repository": "zlxlabs/gate",
        "repository_id": _CANARY_REPO_ID,
        "pr_number": 42,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
        "gate_result": gate_result,
        "primary_result": "success" if gate_result == "pass" else "failure",
        "audit": {"available": True, "source_attempt": 1, "artifact_name": "primary-audit-v2-canary"},
        "classification": "code_pass" if gate_result == "pass" else "code_fail",
        "reason_code": "primary_pass" if gate_result == "pass" else "primary_findings",
    }
    return json.dumps(record).encode()

def _canary_terminal_zip(gate_result="pass", **kwargs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr("gate-terminal.json", _canary_terminal_bytes(gate_result, **kwargs))
    return buf.getvalue()

def _primary_convergence_row(
    run_id, attempt, verdict, *, primary_result=None, skip_reason=None, audit_available=None,
    head_sha=IDENTITY.head_sha,
):
    record = json.loads(_canary_terminal_bytes(
        "fail" if verdict == "fail" or primary_result == "failure" else "pass",
        run_id=run_id, run_attempt=attempt, head_sha=head_sha,
    ))
    record.update({"primary_result": primary_result or ("failure" if verdict == "fail" else "success"),
                   "skip_reason": skip_reason, "audit": {"available": verdict is not None if audit_available is None else audit_available}})
    if verdict is not None:
        record["primary_verdict"] = verdict
    return AGG._terminal_row(record, repository="zlxlabs/gate", repository_id=_CANARY_REPO_ID, pr_number=42)

def _primary_convergence_history(target_run_ids, rows):
    history = AGG.HistoryLoad(rows=rows)
    history.target_run_ids = target_run_ids
    return history

def _run_pr_convergence_cli(tmp_path, monkeypatch, *, audit_verdict, primary_result, quality_result="success", caller_checks="", history=None):
    audit_dir = tmp_path / "audit"
    if audit_verdict is not None:
        audit_dir.mkdir()
        (audit_dir / "primary-review-audit.json").write_text(
            json.dumps(_valid_scoped_primary_record(verdict=audit_verdict, result={"findings": []})), encoding="utf-8",
        )
        if history is not None:
            monkeypatch.setattr(AGG, "_load_pr_convergence_history", lambda **kwargs: ("available", (), history))
    monkeypatch.setattr(AGG, "_fetch_disposition_receipts", lambda **kwargs: ())
    monkeypatch.setattr(AGG, "load_previous_round_findings", lambda **kwargs: {"available": False, "detail": "fixture", "findings": []})
    summary_path = tmp_path / "summary.md"
    exit_code = AGG.main(_cli_args(
        audit_dir, summary_path, quality_result=quality_result, caller_checks=caller_checks,
        primary_result=primary_result,
    ))
    return exit_code, json.loads(summary_path.with_name("gate-terminal.json").read_bytes())

def _install_dual_read(monkeypatch, *, github_artifacts, github_blobs, silo_objects, github_error=None, silo_error=None):
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")

    def fake_github_json(**kwargs):
        if github_error is not None:
            raise github_error
        return {"artifacts": github_artifacts}

    def fake_download(**kwargs):
        return github_blobs[kwargs["url"]]

    def fake_silo(prefix, timeout_s):
        if silo_error is not None:
            raise silo_error
        return [(key, body) for key, body in silo_objects if key.startswith(prefix)]

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            prefix = argv[2]
            keys = [key for key, _body in silo_objects if key.startswith(prefix)]
            if silo_error is not None:
                raise silo_error
            return subprocess.CompletedProcess(
                argv, 0, stdout="".join(f"{key}\n" for key in keys), stderr="",
            )
        key = argv[argv.index("--key") + 1]
        body = dict(silo_objects)[key]
        dest = argv[argv.index("--dest") + 1]
        Path(dest).write_bytes(body)
        return subprocess.CompletedProcess(argv, 0, stdout=dest, stderr="")

    monkeypatch.setattr(AGG, "_github_json", fake_github_json)
    monkeypatch.setattr(AGG, "_download_terminal_zip", fake_download)
    monkeypatch.setattr(AGG, "_silo_objects_under", fake_silo)
    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)

def _real_convergence_history_silo_objects():
    fixture = json.loads(CONVERGENCE_HISTORY_FIXTURE.read_text(encoding="utf-8"))
    objects = [
        (key, json.dumps(record, sort_keys=True).encode("utf-8"))
        for key, record in zip(fixture["source_keys"], fixture["terminal_envelopes"], strict=True)
    ]
    return fixture, objects

def _canary_receipt(reason="locked upstream behavior", finding_id="p1"):
    scope = _scope_for(_failing_scoped_audit())
    return _false_positive_receipt(
        scope, repository_id=str(_CANARY_REPO_ID), reason=reason, finding_id=finding_id,
    )

def _receipt_payload(receipt):
    return {**receipt.as_dict(), "kind": CONV.DISPOSITION_RECEIPT_KIND}

def _receipt_key(receipt):
    name = CONV.disposition_receipt_artifact_name(receipt)
    return f"d30/{_CANARY_REPO_ID}/{name}/{name}"

def test_issue199_quality_failure_with_cancelled_primary_is_unavailable():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure", primary_result="cancelled", audit=None, audit_error=None))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "primary_cancelled", "unavailable")
    terminal = AGG.build_terminal_envelope(repository="zlxlabs/gate", identity=IDENTITY, quality_result="failure", primary_result="cancelled", review_expected=True, is_draft=False, runner="self", outcome=outcome)
    assert terminal["quality_result"] == "failure"
    assert any("quality" in p for p in outcome.problems)
    assert any("cancelled" in p for p in outcome.problems)

def test_issue199_quality_cancelled_with_cancelled_primary_is_unavailable():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="cancelled", primary_result="cancelled", audit=None, audit_error=None))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "primary_cancelled", "unavailable")

def test_issue199_quality_skipped_with_cancelled_primary_is_unavailable():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="skipped", primary_result="cancelled", audit=None, audit_error=None))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "primary_cancelled", "unavailable")

def test_issue199_draft_skipped_primary_with_quality_failure_stays_code_problem():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", primary_result="skipped", is_draft=True, review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")

def test_issue199_hosted_skipped_primary_with_quality_failure_stays_code_problem():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", primary_result="skipped", is_draft=False, runner="hosted", review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")

def test_issue199_quality_failure_with_passing_primary_is_unavailable_without_failed_evidence():
    # 根治翻转（#199）：primary=success + quality=failure 时，没有
    # caller_checks=failed 证据就不能定罪为代码问题，一律基础设施不可用。
    # ok 仍为 False（合并照样被挡），只是桶从「要修代码」换到「修基础设施」。
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure"))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("review_unavailable", "quality_infra", "unavailable")

def test_issue199_quality_failure_with_failing_primary_stays_fail():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", primary_result="failure", audit=_valid_primary_record(verdict="fail"))
    )
    assert outcome.ok is False
    assert outcome.gate_result == "fail"

def test_issue199_unavailable_panel_renders_infra_action():
    body = AGG.render_status_panel([{
        "schema_version": AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": "zlxlabs/gate",
        "run_id": 1,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "gate_result": "unavailable",
        "classification": "review_unavailable",
        "reason_code": "primary_cancelled",
    }])
    assert "修基础设施" in body
    assert "要修代码" not in body

@pytest.mark.parametrize("caller_checks,expected", [
    ("failed", ("ci_failure", "quality_failure", "fail")),
    ("signal_terminated", ("review_unavailable", "quality_infra", "unavailable")),
    ("passed", ("review_unavailable", "quality_infra", "unavailable")),
    ("not_started", ("review_unavailable", "quality_infra", "unavailable")),
    ("", ("review_unavailable", "quality_infra", "unavailable")),  # 证据缺席：旧调用方/记录步前死亡
])
def test_caller_checks_evidence_matrix(caller_checks, expected):
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure", caller_checks=caller_checks))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == expected

def test_preflight_blocked_is_legitimate_fail_despite_passing_checks():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="passed", preflight_result="blocked")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")
    assert any("exceeds the single-review budget" in problem for problem in outcome.problems)

def test_preflight_blocked_outranks_missing_evidence():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="", preflight_result="blocked")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_failure", "fail")

def test_preflight_unavailable_precedes_business_failure_without_size_claim():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="failed", preflight_result="unavailable")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable"
    )
    assert not any("split the PR" in problem for problem in outcome.problems)

@pytest.mark.parametrize("quality_result", ["success", "failure"])
def test_preflight_unavailable_never_passes_even_when_quality_result_is_not_red(quality_result):
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result=quality_result, caller_checks="passed", preflight_result="unavailable")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable"
    )

@pytest.mark.parametrize("preflight_result", ["", "skipped", "cancelled"])
def test_quality_success_requires_explicit_preflight_success(preflight_result):
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="success", caller_checks="passed", preflight_result=preflight_result)
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable"
    )
    assert not any("split the PR" in problem for problem in outcome.problems)

def test_signal_terminated_problem_names_the_signal_number():
    outcome = AGG.evaluate(
        **_base_kwargs(quality_result="failure", caller_checks="signal_terminated", caller_signal="15")
    )
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "quality_infra", "unavailable",
    )
    assert any(
        "signal 15" in problem and "not a code problem" in problem
        for problem in outcome.problems
    )

def test_unknown_caller_checks_value_fails_closed():
    outcome = AGG.evaluate(**_base_kwargs(quality_result="failure", caller_checks="banana"))
    assert outcome.ok is False
    assert outcome.classification is None
    assert any("caller_checks" in p for p in outcome.problems)

def test_quality_cancelled_ignores_caller_checks_evidence():
    # 只有 failure 腿读证据；cancelled/skipped 保持原语义。
    outcome = AGG.evaluate(**_base_kwargs(quality_result="cancelled", caller_checks="passed"))
    assert outcome.ok is False
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == ("ci_failure", "quality_cancelled", "fail")

def test_quality_infra_panel_renders_infra_action():
    body = AGG.render_status_panel([{
        "schema_version": AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": "zlxlabs/gate",
        "run_id": 1,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "gate_result": "unavailable",
        "classification": "review_unavailable",
        "reason_code": "quality_infra",
    }])
    assert "修基础设施" in body
    assert "要修代码" not in body

@pytest.mark.parametrize("author", [None, "", 17, True, [], {}])
def test_missing_or_invalid_pr_author_is_unavailable_before_skip_acceptance(author):
    outcome = AGG.evaluate(**_base_kwargs(
        pr_author=author, primary_result="skipped", is_draft=False, is_fork=True,
        runner="hosted", classify_review_expected="false", review_expected=False, audit=None,
    ))
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "review_unavailable", "primary_unavailable", "unavailable",
    )
    assert outcome.skip_reason is None
    assert any("PR author" in problem for problem in outcome.problems)

def test_dependabot_draft_skip_is_expected_from_payload():
    outcome = AGG.evaluate(**_base_kwargs(
        pr_author="dependabot[bot]", primary_result="skipped", is_draft=True,
        review_expected=False, audit=None,
    ))
    assert (outcome.classification, outcome.reason_code, outcome.gate_result) == (
        "expected_skip", "review_not_expected", "skipped",
    )

def test_non_draft_non_fork_self_runner_skipped_primary_fails():
    # review_expected True + skipped == an unexplained skip, never accepted.
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="skipped", is_draft=False, review_expected=True, audit=None, audit_error=None)
    )
    assert outcome.ok is False
    assert outcome.synthetic_audit is None  # skip is not a missing-audit situation

def test_non_draft_fork_or_hosted_skipped_primary_is_accepted():
    # review_expected False due to fork/hosted (not draft) — still accepted.
    outcome = AGG.evaluate(
        **_base_kwargs(primary_result="skipped", is_draft=False, is_fork=True, review_expected=False, audit=None, audit_error=None)
    )
    assert outcome.ok is True
