from test_gate_aggregator import (
    AGG,
    IDENTITY,
    _RUN_URL,
    _base_kwargs,
    _budget_exhausted_attempt,
    _budget_exhausted_audit,
    _panel_row,
    _panel_terminal_row,
    _publish_only_panel_body,
    _valid_primary_record,
    _valid_scoped_primary_record,
    _visible_scenario,
    pytest,
)

@pytest.mark.parametrize(
    "overrides,audit_record,classification,reason_code,gate_result,exit_code,annotation",
    [
        ({}, "__default__", "code_pass", "primary_pass", "pass", 0, "::notice::"),
        (
            {"primary_result": "failure"}, _valid_primary_record(verdict="fail"),
            "code_fail", "primary_findings", "fail", 1, "::error::",
        ),
        (
            {"primary_result": "skipped", "is_draft": "true", "review_expected": "false"}, None,
            "expected_skip", "review_not_expected", "skipped", 0, "::notice::",
        ),
        (
            {"primary_result": "failure"}, _valid_primary_record(verdict="unavailable"),
            "review_unavailable", "primary_unavailable", "unavailable", 1, "::error::",
        ),
        (
            {"quality_result": "failure", "caller_checks": "failed"}, "__default__",
            "ci_failure", "quality_failure", "fail", 1, "::error::",
        ),
        ({}, None, "integration_error", "audit_missing", "unavailable", 1, "::error::"),
    ],
)
def test_visible_terminal_state_axis(
    capsys, tmp_path, overrides, audit_record, classification, reason_code, gate_result, exit_code, annotation,
):
    _, summary_path, args = _visible_scenario(tmp_path, overrides, audit_record)
    rc = AGG.main(args)
    out = capsys.readouterr().out
    text = summary_path.read_text()

    # Exit code is exactly the pre-existing pass/fail semantic (skipped -> 0).
    assert rc == exit_code

    # Output slot 1 — Summary top line shows the four-state gate_result, and
    # never one of the other three states (skipped must not read as pass,
    # unavailable must not read as fail).
    assert f"**Result: {gate_result}**" in text
    for other in AGG.GATE_RESULT_DOMAIN:
        if other != gate_result:
            assert f"**Result: {other}**" not in text

    # Output slot 2 — Summary body exposes all three terminal fields.
    assert (
        f"classification=`{classification}`, reason_code=`{reason_code}`, gate_result=`{gate_result}`"
    ) in text

    # Output slot 3 — annotation of the expected type carrying reason_code,
    # visible on the checks list page without expanding the run.
    assert f"{annotation}gate terminal state:" in out
    assert f"reason_code={reason_code}" in out
    opposite = "::error::" if annotation == "::notice::" else "::notice::"
    assert f"{opposite}gate terminal state:" not in out

def test_visible_expected_skip_summary_names_draft_as_the_skip_reason(capsys, tmp_path):
    _, summary_path, args = _visible_scenario(
        tmp_path, {"primary_result": "skipped", "is_draft": "true", "review_expected": "false"}, None,
    )
    assert AGG.main(args) == 0
    text = summary_path.read_text()
    assert "**Result: skipped**" in text
    assert "draft=True" in text

@pytest.mark.parametrize(
    "kwargs,must_say,must_not_say",
    [
        (
            {"primary_result": "failure", "audit": _valid_primary_record(verdict="fail")},
            ["REJECTED", "Problems"],
            ["could NOT be read"],
        ),
        (
            {"audit": None, "audit_error": "missing"},
            ["could NOT be read", "reason for the primary job result is unknown", "not a reviewer rejection"],
            ["REJECTED", "primary job logs"],
        ),
        (
            {"audit": _valid_primary_record(kind="synthetic_primary")},
            ["could NOT be read", "reason for the primary job result is unknown", "not a reviewer rejection"],
            ["REJECTED", "primary job logs"],
        ),
        (
            {"audit_source_attempt": 2},
            ["could NOT be read", "reason for the primary job result is unknown", "not a reviewer rejection"],
            ["REJECTED", "primary job logs"],
        ),
        (
            {"primary_result": "failure", "audit": _valid_primary_record(verdict="pass")},
            ["contradicts", "Check the primary job logs"],
            ["could NOT be read", "REJECTED"],
        ),
        (
            {"primary_result": "skipped", "audit": None},
            ["should have run but was skipped", "NOT the normal draft/fork"],
            ["could NOT be read"],
        ),
    ],
    ids=["primary_findings", "audit_missing", "audit_invalid", "audit_source_mismatch", "job_audit_mismatch", "unexpected_primary_skip"],
)
def test_reason_code_wording_cannot_be_misread(kwargs, must_say, must_not_say):
    outcome = AGG.evaluate(**_base_kwargs(**kwargs))
    text = AGG.render_summary(outcome)
    for phrase in must_say:
        assert phrase in text
    for phrase in must_not_say:
        assert phrase not in text

@pytest.mark.parametrize(
    "primary_result",
    [
        "failure",
        "success",
    ],
    ids=["primary_failure", "primary_success"],
)
def test_audit_missing_wording_does_not_infer_from_primary_job_result(tmp_path, primary_result):
    # The terminal classification and unknown-cause wording hold regardless of
    # the primary job conclusion; only canonical verdict=fail proves rejection.
    _, summary_path, args = _visible_scenario(
        tmp_path, {"primary_result": primary_result}, None,
    )
    assert AGG.main(args) == 1
    text = summary_path.read_text()
    assert "classification=`integration_error`, reason_code=`audit_missing`, gate_result=`unavailable`" in text
    assert "could NOT be read" in text
    assert "reason for the primary job result is unknown" in text
    assert "not a reviewer rejection" in text
    assert "primary job logs" not in text
    assert "usually because of a reviewer rejection" not in text

@pytest.mark.parametrize(
    "kwargs",
    [
        {"audit": None, "audit_error": "missing"},
        {"primary_result": "failure", "audit": None, "audit_error": "missing"},
        {"audit": _valid_primary_record(kind="synthetic_primary")},
        {"audit_source_attempt": 2},
        {"primary_result": "cancelled", "audit": None},
    ],
    ids=["audit_missing", "audit_missing_primary_failure", "audit_invalid", "audit_source_mismatch", "primary_cancelled"],
)
def test_synthetic_verdict_null_is_explained_before_the_json(kwargs):
    # A human sentence explaining `"verdict": null` must precede the synthetic
    # audit JSON, and it must say "not a reviewer rejection" (issue #43).
    outcome = AGG.evaluate(**_base_kwargs(**kwargs))
    assert outcome.synthetic_audit is not None
    text = AGG.render_summary(outcome)
    explanation = '"verdict": null` below means no canonical primary review conclusion was available'
    assert explanation in text
    assert "NOT a reviewer rejection" in text
    assert "cause cannot be determined from the available audit data" in text
    assert "primary job logs" not in text
    assert text.index(explanation) < text.index('  "verdict": null')

@pytest.mark.parametrize(
    "overrides,audit_record,gate_result,action_phrases,needs_run_url",
    [
        ({}, "__default__", "pass", ["No action needed"], False),
        (
            {"primary_result": "failure"}, _valid_primary_record(verdict="fail"),
            "fail", ["Action needed", "the primary reviewer rejected this change"], True,
        ),
        (
            {"primary_result": "skipped", "is_draft": "true", "review_expected": "false"}, None,
            "skipped", ["No action needed", "draft", "ready for review"], False,
        ),
        (
            {"primary_result": "skipped", "runner": "hosted", "review_expected": "false"}, None,
            "skipped", ["No action needed", "runner=self", "runner=hosted"], False,
        ),
        (
            {"primary_result": "cancelled"}, None,
            "unavailable", ["Action needed", "could not be determined"], True,
        ),
    ],
    ids=["pass", "fail", "skipped_draft", "skipped_hosted", "unavailable"],
)
def test_action_line_precedes_machine_codes_for_every_gate_result(tmp_path, overrides, audit_record, gate_result, action_phrases, needs_run_url):
    _, summary_path, args = _visible_scenario(tmp_path, overrides, audit_record)
    AGG.main(args)
    text = summary_path.read_text()

    # Axis 2: Result stays the first verdict line and the machine codes stay
    # behind it…
    assert text.index(f"**Result: {gate_result}**") < text.index("Terminal state: classification=")
    # …and EVERY action-sentence phrase lands before the machine codes (axis
    # 1: the human sentence first, classification= never ahead of it).
    terminal_pos = text.index("Terminal state: classification=")
    for phrase in action_phrases:
        assert phrase in text
        assert text.index(phrase) < terminal_pos
    if needs_run_url:
        # Fail/unavailable must hand the recipient a direct run entry point
        # (axis 1) — built from repository + run_id, before the machine codes.
        assert _RUN_URL in text
        assert text.index(_RUN_URL) < terminal_pos

def test_primary_fail_summary_no_longer_points_at_problems_for_findings(tmp_path):
    # P2-1: the Problems list only mirrors the verdict string; the summary
    # must not claim the findings live there.
    _, summary_path, args = _visible_scenario(tmp_path, {"primary_result": "failure"}, _valid_primary_record(verdict="fail"))
    AGG.main(args)
    text = summary_path.read_text()
    assert "see the findings listed under Problems" not in text
    assert _RUN_URL in text

@pytest.mark.parametrize(
    "verdict,findings",
    [("pass", None), ("fail", [{"id": "x", "severity": "major", "file": "a.py", "line": 1, "title": "t"}]),
     ("unavailable", None), ("not_expected", None), ("waived", None)],
)
def test_primary_findings_panel_accepts_every_audit_verdict_domain_value(verdict, findings):
    audit = _valid_primary_record(verdict=verdict)
    if findings is not None:
        audit["result"] = {"findings": findings}

    lines = AGG._primary_findings_panel_lines(audit)

    assert bool(lines) is (verdict == "fail")

def test_publish_only_status_panel_renders_bounded_scrubbed_primary_findings(monkeypatch, tmp_path):
    title = "reviewer label\n::error:: token=ghp_" + "A" * 16 + " limit " + "z" * 230
    findings = [{
        "id": "correctness.data-loss",
        "severity": "major",
        "category": "correctness",
        "file": "src/service.py",
        "line": 17,
        "trigger": "private reviewer trigger must not be published",
        "evidence": "private reviewer evidence must not be published",
        "issue": title,
        "acceptance": "private reviewer acceptance must not be published",
        "title": title,
    }]
    findings.extend({
        "id": f"quality.other-{index}",
        "severity": "minor",
        "category": "quality",
        "file": "src/other.py",
        "line": index,
        "trigger": "trigger",
        "evidence": "evidence",
        "issue": "Short title",
        "acceptance": "acceptance",
        "title": "Short title",
    } for index in range(1, 51))
    audit = _valid_primary_record(verdict="fail", result={"findings": findings})
    assert not AGG.validate_audit_identity(audit, IDENTITY)
    assert AGG._primary_findings_panel_lines(audit)

    body, _ = _publish_only_panel_body(monkeypatch, tmp_path, audit)

    assert "PRIMARY-FINDINGS" in body
    assert "| major | correctness.data-loss | src/service.py:17 |" in body
    assert "reviewer label :error: [REDACTED:TOKEN] limit" in body
    assert "+1 more" in body
    assert "::error::" not in body
    assert "ghp_" not in body
    assert "private reviewer trigger must not be published" not in body
    assert "private reviewer evidence must not be published" not in body
    assert "private reviewer acceptance must not be published" not in body
    long_title_row = next(
        line for line in body.splitlines()
        if line.startswith("| major | correctness.data-loss | src/service.py:17 |")
    )
    title_cell = long_title_row.rsplit("|", 2)[1].strip()
    assert len(title_cell) <= AGG.FINDING_SUMMARY_MAX_TITLE_CHARS

def test_publish_only_status_panel_does_not_render_pass_audit_findings(monkeypatch, tmp_path):
    audit = _valid_scoped_primary_record(
        verdict="pass",
        result={"findings": [{
            "id": "quality.nonblocking",
            "severity": "minor",
            "category": "quality",
            "file": "src/example.py",
            "line": 4,
            "trigger": "nonblocking trigger",
            "evidence": "nonblocking evidence",
            "issue": "nonblocking title",
            "acceptance": "nonblocking acceptance",
            "title": "nonblocking title",
        }]},
    )
    body, row = _publish_only_panel_body(monkeypatch, tmp_path, audit)

    assert "PRIMARY-FINDINGS" not in body
    assert body == AGG.render_status_panel([row])

def test_publish_only_status_panel_without_audit_keeps_existing_body(monkeypatch, tmp_path):
    audit = _valid_scoped_primary_record(
        verdict="fail",
        result={"findings": [{
            "id": "correctness.data-loss",
            "severity": "major",
            "category": "correctness",
            "file": "src/example.py",
            "line": 8,
            "trigger": "trigger",
            "evidence": "evidence",
            "issue": "title",
            "acceptance": "acceptance",
            "title": "title",
        }]},
    )
    body, row = _publish_only_panel_body(
        monkeypatch, tmp_path, audit, remove_audit_before_publish=True,
    )

    assert "PRIMARY-FINDINGS" not in body
    assert body == AGG.render_status_panel([row])

def test_status_panel_malformed_primary_findings_fail_loud():
    row = _panel_terminal_row(1, 1, "fail", "a" * 40)
    row["primary_audit"] = {"verdict": "fail", "result": {"findings": {}}}

    with pytest.raises(ValueError, match="findings is not a list"):
        AGG.render_status_panel([row])

def test_panel_bucket_mapping_exhaustively_covers_current_gate_result_domain():
    assert set(AGG.PANEL_BUCKET_BY_GATE_RESULT) == set(AGG.GATE_RESULT_DOMAIN)

@pytest.mark.parametrize("gate_result", AGG.GATE_RESULT_DOMAIN)
def test_status_panel_renders_every_gate_result_and_action_bucket(gate_result):
    body = AGG.render_status_panel([_panel_row(1, 1, gate_result)])
    assert AGG.PANEL_MARKER in body
    assert f"当前状态：**{gate_result}**" in body
    assert AGG.PANEL_BUCKET_BY_GATE_RESULT[gate_result] in body

def test_status_panel_is_pure_and_history_is_sorted_by_durable_run_identity():
    rows = [_panel_row(3, 1, "pass"), _panel_row(2, 2, "fail"), _panel_row(2, 1, "skipped")]
    original = [dict(row) for row in rows]
    body = AGG.render_status_panel(rows)
    assert rows == original
    assert body.index("| [2]") < body.index("| [3]")
    assert body.index("| [2]") < body.index("| [2]") + 1
    assert "主审未跑，绿≠过审" in body

@pytest.mark.parametrize(
    "audit,expected",
    [
        (
            _budget_exhausted_audit(),
            "本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。",
        ),
        (
            _budget_exhausted_audit(
                attempts=[
                    _budget_exhausted_attempt(reviewer="reviewer-a"),
                    _budget_exhausted_attempt(reason="认证或网络暂不可用", exit_code=21, reviewer="reviewer-b"),
                ],
            ),
            "修基础设施",
        ),
        (
            _budget_exhausted_audit(attempts=[]),
            "修基础设施",
        ),
        (
            {
                **_budget_exhausted_audit(),
                "attempts": [
                    {**_budget_exhausted_attempt(reviewer="reviewer-a"), "exit_code": True},
                    _budget_exhausted_attempt(reviewer="reviewer-b"),
                ],
            },
            "修基础设施",
        ),
        (
            {
                **_budget_exhausted_audit(),
                "attempts": [
                    {**_budget_exhausted_attempt(reviewer="reviewer-a"), "exit_code": "22"},
                    _budget_exhausted_attempt(reviewer="reviewer-b"),
                ],
            },
            "修基础设施",
        ),
        (
            {
                **_budget_exhausted_audit(),
                "attempts": [
                    {
                        **_budget_exhausted_attempt(reviewer="reviewer-a"),
                        "reason": AGG.PRIMARY_BUDGET_EXHAUSTED_REASON + "x",
                    },
                    _budget_exhausted_attempt(reviewer="reviewer-b"),
                ],
            },
            "修基础设施",
        ),
    ],
    ids=[
        "all-legs-exhausted", "mixed-failure", "no-attempts",
        "exit-code-bool", "exit-code-string", "reason-extra-character",
    ],
)
def test_budget_exhaustion_panel_action_is_rendered_from_primary_audit(audit, expected):
    outcome = AGG.evaluate(**_base_kwargs(primary_result="failure", audit=audit))
    assert (outcome.gate_result, outcome.classification, outcome.reason_code) == (
        "unavailable", "review_unavailable", "primary_unavailable",
    )
    row = _panel_row(1, 1, "unavailable")
    row["classification"] = "review_unavailable"
    row["reason_code"] = "primary_unavailable"
    row["primary_audit"] = audit
    body = AGG.render_status_panel([row])
    assert f"当前状态：**unavailable** · **{expected}**" in body
    assert "0 个审查分片" not in body

def test_budget_exhaustion_panel_action_rejects_non_unavailable_verdict():
    audit = {**_budget_exhausted_audit(), "verdict": "pass"}
    row = _panel_row(1, 1, "unavailable")
    row["classification"] = "review_unavailable"
    row["reason_code"] = "primary_unavailable"
    row["primary_audit"] = audit
    body = AGG.render_status_panel([row])
    assert "当前状态：**unavailable** · **修基础设施**" in body

def test_budget_exhaustion_action_keeps_terminal_decision_fields_unchanged():
    audit = _budget_exhausted_audit()
    outcome = AGG.evaluate(**_base_kwargs(primary_result="failure", audit=audit))
    assert (outcome.gate_result, outcome.classification, outcome.reason_code) == (
        "unavailable", "review_unavailable", "primary_unavailable",
    )
    summary = AGG.render_summary(outcome, primary_audit=audit)
    assert "classification=`review_unavailable`, reason_code=`primary_unavailable`, gate_result=`unavailable`" in summary
    assert "本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。" in summary
