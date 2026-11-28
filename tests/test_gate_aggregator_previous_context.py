"""Domain-focused tests migrated without changing assertions."""
from test_gate_aggregator import (
    AGG,
    CONV,
    MODULE_PATH,
    Path,
    ROOT,
    _cli_args,
    _gate_rerun,
    _string_values,
    _valid_scoped_primary_record,
    hashlib,
    json,
    os,
    pytest,
    subprocess,
    sys,
)

def _relation_audit(**finding):
    record = _valid_scoped_primary_record(verdict="pass")
    record["result"] = {"verdict": "pass", "findings": [finding]}
    return record

def _write_relation_audit(tmp_path, finding):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_relation_audit(**finding)))
    return audit_dir

def test_same_finding_id_is_repeat_with_previous_pointer(tmp_path, capsys):
    finding = {
        "id": "same-id", "severity": "minor", "file": "a.py", "line": 3,
        "category": "correctness", "issue": "kept", "acceptance": "kept",
    }
    audit_dir = _write_relation_audit(tmp_path, finding)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"available": True, "detail": "", "findings": [finding]}))
    assert AGG.main([
        "--annotate-primary-audit", "--audit-path",
        str(audit_dir / "primary-review-audit.json"), "--previous-findings-path", str(previous),
    ]) == 0
    summary = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary))
    assert rc == 0
    terminal = json.loads(summary.with_name("gate-terminal.json").read_text())
    item = terminal["finding_relation"]["items"][0]
    assert item["relation_to_previous"] == "repeat"
    assert item["previous_finding_id"] == "same-id"
    assert terminal["finding_relation"]["counts"] == {"new": 0, "repeat": 1, "conflict": 0}
    assert "GATE-FINDING-RELATION-DEGRADED" not in capsys.readouterr().out

def test_opposite_same_location_requirement_is_conflict_and_not_pass(tmp_path):
    current = {
        "id": "round-b", "severity": "major", "file": "scripts/retro/log_acceptance.py", "line": 40,
        "category": "correctness", "issue": "must not advance the lease",
        "acceptance": "leave the lease at evidence",
    }
    previous_finding = {
        "id": "round-a", "severity": "major", "file": "scripts/retro/log_acceptance.py", "line": 40,
        "category": "correctness", "issue": "must advance the lease",
        "acceptance": "advance the lease to closed",
    }
    audit_dir = _write_relation_audit(tmp_path, current)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"available": True, "detail": "", "findings": [previous_finding]}))
    assert AGG.main([
        "--annotate-primary-audit", "--audit-path",
        str(audit_dir / "primary-review-audit.json"), "--previous-findings-path", str(previous),
    ]) == 0
    summary = tmp_path / "summary.md"
    receipt = tmp_path / "convergence-receipt.json"
    rc = AGG.main(_cli_args(audit_dir, summary, convergence_receipt_path=str(receipt)))
    terminal = json.loads(summary.with_name("gate-terminal.json").read_text())
    relation = terminal["finding_relation"]
    assert rc == 1
    assert terminal["gate_result"] != "pass"
    assert relation["review_terminal"] == "manual_required"
    assert relation["counts"]["conflict"] == 1
    assert relation["items"][0]["relation_to_previous"] == "conflict"
    assert relation["items"][0]["previous_finding_id"] == "round-a"
    body = AGG.render_status_panel([AGG._terminal_row(
        terminal, repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )])
    assert "manual_required" in body and "round-a" in body
    assert json.loads(receipt.read_text())["decision"] != "converged"
    same = dict(previous_finding, id="other", acceptance=current["acceptance"], issue=current["issue"])
    assert AGG.relate_findings([current], [same])[0]["relation_to_previous"] == "new"

def test_missing_previous_ledger_marks_new_and_job_succeeds(tmp_path, capsys):
    finding = {
        "id": "only-now", "severity": "minor", "file": "a.py", "line": 1,
        "category": "testing", "issue": "nit", "acceptance": "none",
    }
    audit_dir = _write_relation_audit(tmp_path, finding)
    summary = tmp_path / "summary.md"
    rc = AGG.main(_cli_args(audit_dir, summary))
    out = capsys.readouterr().out
    terminal = json.loads(summary.with_name("gate-terminal.json").read_text())
    item = terminal["finding_relation"]["items"][0]
    assert rc == 0
    assert terminal["gate_result"] == "pass"
    assert terminal["finding_relation"]["history_status"] == "unknown"
    assert terminal["finding_relation"]["open_findings"] == [finding]
    assert item["relation_to_previous"] == "new"
    assert "previous_finding_id" not in item
    assert "GATE-FINDING-RELATION-DEGRADED: source=primary-audit detail=unknown" in out

def _previous_round_fixture_objects():
    ledger_path = ROOT / "tests/fixtures/previous-round-pr1063-ledger.json"
    receipt_path = ROOT / "tests/fixtures/previous-round-pr1063-disposition.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = dict(receipt.get("counterevidence") or {})
    if evidence and "gate_rerun" not in evidence:
        evidence["gate_rerun"] = _gate_rerun(receipt["head_sha"])
        receipt["counterevidence"] = evidence
    receipt_bytes = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    repository_id = ledger["primary_identity"]["repository_id"]
    ledger_name = (
        f"codex-review-ledger-v2-{repository_id}-{ledger['head_sha']}-"
        f"{ledger['run_id']}-{ledger['run_attempt']}"
    )
    ledger_key = f"d30/{repository_id}/{ledger_name}/ledger.json"
    latest_key = AGG._ledger_latest_key(repository_id, ledger["pr_number"])
    receipt_name = CONV.disposition_receipt_artifact_name(CONV.parse_disposition_receipt(receipt))
    receipt_key = f"d30/{repository_id}/{receipt_name}/{receipt_name}.json"
    ledger_bytes = ledger_path.read_bytes()
    return ledger, receipt, [
        (ledger_key, ledger_bytes),
        (latest_key, ledger_bytes),
        (receipt_key, receipt_bytes),
    ]

def _render_previous_round_fixture(
    tmp_path, monkeypatch, silo_objects, *, silo_configured=True,
    run_id=35989919390, run_attempt=1,
):
    ledger, _, _ = _previous_round_fixture_objects()
    by_key = dict(silo_objects)

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            raise AssertionError("previous-round findings must not list Silo objects")
        key = argv[argv.index("--key") + 1]
        dest = argv[argv.index("--dest") + 1]
        body = by_key.get(key)
        if body is None:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="Silo object not found")
        Path(dest).write_bytes(body)
        return subprocess.CompletedProcess(argv, 0, stdout=dest, stderr="")

    monkeypatch.setattr(AGG, "_silo_configured", lambda: silo_configured)
    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    monkeypatch.setattr(
        AGG, "_silo_objects_under",
        lambda prefix, timeout_s: [(key, raw) for key, raw in silo_objects if key.startswith(prefix)],
    )
    output = tmp_path / "previous-findings.json"
    rc = AGG.main([
        "--render-previous-context",
        "--repository-id", str(ledger["primary_identity"]["repository_id"]),
        "--pr-number", str(ledger["pr_number"]),
        "--run-id", str(run_id),
        "--run-attempt", str(run_attempt),
        "--previous-json", str(output),
    ])
    return rc, json.loads(output.read_text(encoding="utf-8"))

def test_render_previous_context_keeps_legacy_digest_finding_open(tmp_path, monkeypatch, capsys):
    ledger, _, objects = _previous_round_fixture_objects()
    assert "open_findings" not in ledger["finding_relation"]
    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, objects)
    out = capsys.readouterr().out

    assert rc == 0
    assert set(payload) == {"available", "detail", "head_sha", "findings", "dispositions", "open_findings"}
    assert payload["available"] is True
    assert payload["head_sha"] == ledger["head_sha"]
    assert payload["findings"] == ledger["review"]["result"]["findings"]
    assert {finding["id"] for finding in payload["open_findings"]} <= {finding["id"] for finding in payload["findings"]}
    assert payload["findings"] and any(
        finding.get("id") == "reliability_alternates_survive_hard_kill"
        for finding in payload["findings"]
    )
    assert payload["dispositions"] == []  # cannot verify a raw-audit digest from ledger bytes
    assert "reliability_alternates_survive_hard_kill" in {finding["id"] for finding in payload["open_findings"]}
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=0 dropped=1" in out

def test_render_previous_context_preserves_full_findings_and_prior_head(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    expected_findings = list(ledger["review"]["result"]["findings"])
    template = expected_findings[-1]
    for index in range(31 - len(expected_findings)):
        finding = dict(template)
        finding.update(
            id=f"prior-finding-{index:02}",
            severity="minor",
            file=f"src/prior-{index}.py",
            line=index + 1,
            issue=f"prior issue {index}",
            acceptance=f"prior acceptance {index}",
        )
        expected_findings.append(finding)
    expected_findings[-1].update(
        id="prior-major-after-limit",
        severity="major",
        file="src/last-major.py",
        line=91,
        issue="last major finding",
        acceptance="keep the complete old machine fact",
        quote='raw quote with "quotes" and a newline\nthat must survive',
        anchor={"path": "src/last-major.py", "line": 91, "range": [91, 92]},
    )

    latest_key = AGG._ledger_latest_key(
        ledger["primary_identity"]["repository_id"], ledger["pr_number"],
    )
    expanded_objects = []
    for key, raw in objects:
        if key == latest_key:
            entry = json.loads(raw)
            entry["review"]["result"]["findings"] = expected_findings
            raw = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        expanded_objects.append((key, raw))

    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, expanded_objects)
    output_path = tmp_path / "previous-findings.json"
    written_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert rc == 0
    assert written_payload["findings"] == expected_findings
    assert written_payload.get("head_sha") == ledger["head_sha"]
    assert payload["findings"][-1]["severity"] == "major"
    assert payload["findings"][-1]["quote"] == expected_findings[-1]["quote"]
    assert payload["findings"][-1]["anchor"] == expected_findings[-1]["anchor"]

    loaded = AGG.load_previous_round_findings(
        path=str(output_path), repository_id=ledger["primary_identity"]["repository_id"],
        pr_number=ledger["pr_number"], run_id=35989919390, run_attempt=1,
    )
    assert loaded["head_sha"] == ledger["head_sha"]
    assert loaded["findings"] == expected_findings
    tail = expected_findings[-1]
    current = {
        field: tail[field]
        for field in ("id", "severity", "file", "line", "category", "issue", "acceptance")
    }
    relation = AGG.build_finding_relation([current], loaded)
    assert relation["items"][0]["relation_to_previous"] == "repeat"
    assert relation["items"][0]["previous_finding_id"] == "prior-major-after-limit"

def _anchor_git_heads(root: Path, mode: str) -> tuple[Path, str, str, str | None]:
    remote, work = root / "anchor.git", root / "anchor-work"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    def git(*args):
        return subprocess.run(["git", "-C", str(work), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("config", "user.name", "Gate Test")
    git("config", "user.email", "gate@example.invalid")
    path = work / "src" / "check.py"
    path.parent.mkdir()
    old_lines = [*(f"source line {line}" for line in range(1, 17)), "exact source bytes", "tail"]
    path.write_text("\n".join(old_lines) + "\n")
    git("add", "src/check.py")
    git("commit", "-m", "old anchor")
    old_sha = git("rev-parse", "HEAD")
    git("push", "origin", "HEAD:main")
    if mode == "adjacent":
        new_lines = old_lines[:17] + ["inserted nearby"] + old_lines[17:]
    elif mode in {"shift-delete", "unavailable"}:
        new_lines = old_lines[:16] + ["inserted before quote"] + old_lines[16:]
    else:
        new_lines = [*old_lines[:16], "replacement source bytes", *old_lines[17:]]
    path.write_text("\n".join(new_lines) + "\n")
    git("add", "src/check.py")
    git("commit", "-m", "next source")
    new_sha = git("rev-parse", "HEAD")
    third_sha = None
    if mode == "shift-delete":
        path.write_text("\n".join([*new_lines[:17], "replacement source bytes", *new_lines[18:]]) + "\n")
        git("add", "src/check.py")
        git("commit", "-m", "delete old anchor")
        third_sha = git("rev-parse", "HEAD")
    if mode != "unavailable":
        git("push", "origin", "HEAD:main")
    return remote, old_sha, new_sha, third_sha

@pytest.mark.parametrize(
    "case",
    [
        ("primary_review_v2_with_tier.json", "none", "new", ["open-with-evidence", "new-current"], "fail"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "actual", "silent", [], "pass"),
        ("primary_review_v2_with_withdrawal.json", "actual", "same", ["reliability_alternates_survive_hard_kill"], "fail"),
        ("primary_review_v2_with_withdrawal.json", "actual", "mapped", ["reliability_alternates_survive_hard_kill", "mapped-current"], "fail"),
        ("primary_review_v2_with_withdrawal.json", "blank", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "unknown", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "missing", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_withdrawal.json", "empty", "silent", ["reliability_alternates_survive_hard_kill"], "pass"),
        ("primary_review_v2_with_tier.json", "none", "new", ["new-current"], "fail", "changed"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "adjacent"),
        ("primary_review_v2_with_tier.json", "none", "new", ["open-with-evidence", "new-current"], "fail", "shift-delete"),
        ("primary_review_v2_with_tier.json", "none", "same", ["open-with-evidence"], "fail", "changed"),
        ("primary_review_v2_with_tier.json", "none", "mapped", ["open-with-evidence", "mapped-current"], "fail", "changed"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "unavailable"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "missing-quote"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "wrong-quote"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "pass", "line-null"),
        ("primary_review_v2_with_tier.json", "none", "silent", ["open-with-evidence"], "unavailable", "review-unavailable"),
    ],
    ids=["old-plus-new", "old-plus-empty", "withdrawal-closes", "same-id-wins", "stable-key-wins",
         "blank-reason-preserves", "unknown-id-preserves", "missing-withdrawal-preserves", "empty-withdrawals-preserves",
         "anchor-deleted", "adjacent-insertion-preserves", "shift-before-delete-three-rounds",
         "re-report-preserves", "mapped-report-preserves",
         "fetch-unavailable-preserves", "missing-quote-preserves", "wrong-quote-preserves",
         "line-null-preserves", "unavailable-review-preserves"],
)
def test_silent_round_carries_open_finding_through_ledger_jsonl(
    tmp_path, case,
):
    audit_fixture, withdrawal_case, report_case, expected_ids, expected_gate = case[:5]
    anchor_case = case[5] if len(case) > 5 else "none"
    ledger_script = ROOT / ".github" / "actions" / "review-ledger" / "build_ledger.py"
    prior_id = (
        "reliability_alternates_survive_hard_kill"
        if audit_fixture.endswith("with_withdrawal.json") else "open-with-evidence"
    )
    finding = {
        "id": prior_id, "severity": "major", "file": "src/check.py", "line": 17,
        "category": "correctness", "issue": "the quote must remain open",
        "acceptance": "retain the original anchor", "quote": "exact source bytes",
        "anchor": {"path": "src/check.py", "line": 17, "range": [17, 18]},
    }
    remote = None
    previous_head, current_head = "a" * 40, "b" * 40
    third_head = None
    if anchor_case in {"changed", "adjacent", "shift-delete", "same", "mapped", "unavailable", "missing-quote", "wrong-quote"}:
        remote, previous_head, current_head, third_head = _anchor_git_heads(tmp_path, anchor_case)
    if anchor_case == "line-null":
        finding.update(line=None)
        finding.pop("quote"); finding.pop("anchor")
    elif anchor_case == "missing-quote":
        finding.pop("quote")
    elif anchor_case == "wrong-quote":
        finding["quote"] = " exact source bytes "
    round_dir = tmp_path / "round-2"
    audit_dir = round_dir / "audit"
    audit_dir.mkdir(parents=True)
    identity = AGG.Identity(repository_id=123, head_sha=current_head, run_id=1000, run_attempt=1, pr=42)
    audit_bytes = (ROOT / "tests" / "data" / audit_fixture).read_bytes()
    audit = json.loads(audit_bytes)
    if audit_fixture.endswith("with_withdrawal.json"):
        # This is the captured producer result; adapter metadata contains local paths.
        canonical_producer_bytes = json.dumps(audit, ensure_ascii=False, separators=(",", ":")).encode()
        assert hashlib.sha256(canonical_producer_bytes).hexdigest() == "7a5df2c44f5664436ceb93ada76cc3723ce0b74424b72e56ad842918633913e7"
        audit = json.loads((ROOT / "tests" / "data" / "primary_review_v2_with_tier.json").read_bytes())
        audit["result"].update(json.loads(audit_bytes)["result"])
    if withdrawal_case == "actual":
        assert audit["result"]["withdrawals"] == [{
            "finding_id": prior_id,
            "reason": "The current review confirmed each job uses a disposable container, so this state cannot survive the job.",
        }]
    audit.update(repository="zlxlabs/gate", repository_id=123, pr=42, run_id=identity.run_id,
                 run_attempt=1, head_sha=identity.head_sha)
    if anchor_case in {"changed", "shift-delete"} and report_case == "new":
        verdict = "fail"
    elif anchor_case == "review-unavailable":
        verdict = "unavailable"
    else:
        verdict = "pass"
    audit["verdict"] = audit["result"]["verdict"] = verdict
    if withdrawal_case == "blank":
        audit["result"]["withdrawals"][0]["reason"] = "  "
    elif withdrawal_case == "unknown":
        audit["result"]["withdrawals"][0]["finding_id"] = "unknown-finding"
    elif withdrawal_case == "missing":
        audit["result"].pop("withdrawals")
    elif withdrawal_case == "empty":
        audit["result"]["withdrawals"] = []
    elif withdrawal_case == "none":
        audit["result"].pop("withdrawals", None)
    if report_case == "new":
        current_findings = [{
            "id": "new-current", "severity": "major", "file": "src/new-check.py", "line": 29,
            "category": "correctness", "issue": "a distinct current-round finding",
            "acceptance": "retain this finding with the historical open finding",
        }]
    elif report_case == "same":
        current_findings = [{**finding, "quote": "rechecked source bytes"}]
    elif report_case == "mapped":
        current_findings = [{
            **finding, "id": "mapped-current", "issue": "a newly reported claim at the same location",
            "acceptance": "review the changed claim at the same location",
        }]
    else:
        current_findings = []
    audit["result"]["findings"] = current_findings
    original_receipt_audit = {
        field: audit["result"].get(field)
        for field in ("finding_dispositions", "final_finding_dispositions")
    }
    audit_path = audit_dir / "primary-review-audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    previous_path = round_dir / "previous-findings.json"
    previous_path.write_text(json.dumps({
        "available": True, "detail": "", "head_sha": previous_head, "findings": [finding],
    }), encoding="utf-8")

    clean_env = os.environ.copy()
    for key in ("SILO_ENDPOINT", "SILO_STORE", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORY",
                "REPOSITORY_ID", "HEAD_SHA", "RUN_ID", "RUN_ATTEMPT", "PR_NUMBER"):
        clean_env.pop(key, None)
    if anchor_case != "none":
        clean_env.update({"GITHUB_REPOSITORY": "zlxlabs/gate", "REPOSITORY_ID": "123",
                          "HEAD_SHA": current_head, "RUN_ID": "1000", "RUN_ATTEMPT": "1",
                          "PR_NUMBER": "42", "GH_TOKEN": "fixture-token"})
    if remote is not None:
        git_home = tmp_path / "git-home"
        git_home.mkdir()
        git_config = git_home / ".gitconfig"
        git_config.write_text(
            f'[url "file://{remote}"]\n\tinsteadOf = https://github.com/zlxlabs/gate.git\n',
            encoding="utf-8",
        )
        clean_env.update(HOME=str(git_home), GIT_CONFIG_GLOBAL=str(git_config))
    annotation = subprocess.run([
        sys.executable, str(MODULE_PATH), "--annotate-primary-audit",
        "--audit-path", str(audit_path), "--previous-findings-path", str(previous_path),
    ], cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False)
    assert annotation.returncode == 0, annotation.stderr
    if withdrawal_case == "blank":
        assert "GATE-WITHDRAWAL-IGNORED: invalid explicit withdrawal record" in annotation.stdout
    audit_bytes = audit_path.read_bytes()
    annotated_audit = json.loads(audit_bytes)
    assert annotated_audit["finding_relation"]["history_status"] == "available"
    expected_carry = {**finding, "line": 18} if anchor_case == "shift-delete" else finding
    expected_open_findings = [expected_carry, *current_findings]
    if report_case == "same":
        expected_open_findings = [{**finding, **current_findings[0]}]
    if withdrawal_case == "actual" and report_case == "silent":
        expected_open_findings = []
    if anchor_case == "changed" and report_case == "new":
        expected_open_findings = current_findings
    assert annotated_audit["finding_relation"]["open_findings"] == expected_open_findings
    assert [item["id"] for item in annotated_audit["finding_relation"]["open_findings"]] == expected_ids
    assert [item["id"] for item in annotated_audit["result"]["findings"]] == [item["id"] for item in current_findings]
    expected_counts = {
        "new": int(report_case == "new"),
        "repeat": int(report_case == "same"),
        "conflict": int(report_case == "mapped"),
    }
    assert annotated_audit["finding_relation"]["counts"] == expected_counts
    assert [item["id"] for item in annotated_audit["finding_relation"]["items"]] == [
        item["id"] for item in current_findings
    ]
    if anchor_case == "shift-delete":
        assert annotated_audit["result"]["findings"] == [
            {**current_findings[0], "relation_to_previous": "new"}
        ]
        assert annotated_audit["finding_relation"]["items"][0]["line"] == current_findings[0]["line"]
    assert {field: annotated_audit["result"].get(field) for field in original_receipt_audit} == original_receipt_audit
    assert annotated_audit["result"].get("withdrawals") == audit["result"].get("withdrawals")

    summary = round_dir / "summary.md"
    primary_result = (
        "failure" if anchor_case == "review-unavailable"
        or (anchor_case in {"changed", "shift-delete"} and report_case == "new") else "success"
    )
    gate_args = _cli_args(audit_dir, summary, identity=identity, primary_result=primary_result)
    gate = subprocess.run(
        [sys.executable, str(MODULE_PATH), *gate_args], cwd=ROOT, env=clean_env,
        capture_output=True, text=True, check=False,
    )
    assert gate.returncode == (1 if expected_gate in {"fail", "unavailable"} else 0), gate.stderr
    terminal_path = summary.with_name("gate-terminal.json")
    terminal_bytes = terminal_path.read_bytes()
    terminal = json.loads(terminal_bytes)
    assert terminal["gate_result"] == expected_gate
    assert terminal["finding_relation"]["open_findings"] == expected_open_findings
    assert [item["id"] for item in terminal["finding_relation"]["open_findings"]] == expected_ids
    assert terminal["finding_relation"]["items"] == annotated_audit["finding_relation"]["items"]
    assert terminal["finding_relation"]["counts"] == expected_counts
    if current_findings:
        legacy_round = round_dir / "legacy"
        legacy_dir = legacy_round / "audit"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "primary-review-audit.json").write_text(
            json.dumps(audit), encoding="utf-8",
        )
        legacy_summary = legacy_round / "summary.md"
        legacy_gate = subprocess.run(
            [sys.executable, str(MODULE_PATH), *_cli_args(
                legacy_dir, legacy_summary, identity=identity,
                primary_result="failure" if anchor_case in {"changed", "shift-delete"} and report_case == "new" else "success")],
            cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False,
        )
        assert legacy_gate.returncode == 1, legacy_gate.stderr
        legacy_terminal = json.loads(legacy_summary.with_name("gate-terminal.json").read_bytes())
        assert legacy_terminal["gate_result"] == "fail"  # current P1 blocks without relation metadata too
        assert legacy_terminal["finding_relation"]["history_status"] == "unknown"
        assert [item["id"] for item in legacy_terminal["finding_relation"]["open_findings"]] == [
            item["id"] for item in current_findings
        ]

    preflight_path = round_dir / "preflight.json"
    preflight_path.write_text(json.dumps({
        "diff_lines": 100, "classification": "single", "review_plan": "single",
        "thresholds": {"single_turn_lines": 4000},
    }), encoding="utf-8")
    output_path = round_dir / "ledger.jsonl"
    ledger = subprocess.run([
        sys.executable, str(ledger_script), "--audit-path", str(audit_path),
        "--preflight-path", str(preflight_path), "--install-path", str(round_dir / "missing-install.json"),
        "--terminal-path", str(terminal_path), "--output", str(output_path),
        "--repository", "zlxlabs/gate", "--pr-number", "42", "--run-id", str(identity.run_id),
        "--run-attempt", "1", "--head-sha", identity.head_sha, "--expected-repository-id", "123",
        "--expected-base-sha", audit["base_sha"], "--expected-caller-sha", audit["caller_sha"],
        "--expected-reusable-workflow-sha", audit["reusable_workflow_sha"], "--codex-expected", "true",
    ], cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False)
    assert ledger.returncode == 0, ledger.stderr
    ledger_bytes = output_path.read_bytes()
    assert ledger_bytes.endswith(b"\n")
    ledger_entry = json.loads(ledger_bytes.splitlines()[0])
    assert ledger_entry["finding_relation"]["open_findings"] == expected_open_findings
    assert [item["id"] for item in ledger_entry["finding_relation"]["open_findings"]] == expected_ids
    assert ledger_entry["finding_relation"]["items"] == annotated_audit["finding_relation"]["items"]
    assert ledger_entry["finding_relation"]["counts"] == expected_counts

    silo_store = round_dir / "silo_store.py"
    silo_store.write_text(
        "import os, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'get':\n"
        "    shutil.copyfile(os.environ['C7_LEDGER_PATH'], args[args.index('--dest') + 1])\n"
        "elif args[0] != 'list':\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    loader_env = {**clean_env, "SILO_ENDPOINT": "local-test", "SILO_STORE": str(silo_store),
                  "C7_LEDGER_PATH": str(output_path)}
    previous_round_path = round_dir / "next-round-findings.json"
    loader = subprocess.run([
        sys.executable, str(MODULE_PATH), "--render-previous-context", "--repository-id", "123",
        "--pr-number", "42", "--run-id", "1001", "--run-attempt", "1",
        "--previous-json", str(previous_round_path),
    ], cwd=ROOT, env=loader_env, capture_output=True, text=True, check=False)
    assert loader.returncode == 0, loader.stderr
    previous_bytes = previous_round_path.read_bytes()
    assert json.loads(previous_bytes)["findings"] == expected_open_findings
    assert [item["id"] for item in json.loads(previous_bytes)["findings"]] == expected_ids
    if anchor_case == "shift-delete":
        third_audit = json.loads(audit_path.read_bytes())
        third_audit.pop("finding_relation")
        third_audit.update(run_id=1001, head_sha=third_head, verdict="pass")
        third_audit["result"].update(verdict="pass", findings=[])
        third_path = audit_dir / "round-3-primary-review-audit.json"
        third_path.write_text(json.dumps(third_audit), encoding="utf-8")
        third_env = {**clean_env, "HEAD_SHA": third_head, "RUN_ID": "1001"}
        third_annotation = subprocess.run([
            sys.executable, str(MODULE_PATH), "--annotate-primary-audit",
            "--audit-path", str(third_path), "--previous-findings-path", str(previous_round_path),
        ], cwd=ROOT, env=third_env, capture_output=True, text=True, check=False)
        assert third_annotation.returncode == 0, third_annotation.stderr
        closed_audit = json.loads(third_path.read_bytes())
        assert closed_audit["result"]["findings"] == []
        assert closed_audit["finding_relation"]["open_findings"] == [current_findings[0]]
        assert closed_audit["finding_relation"]["items"] == []
    if (withdrawal_case == "actual" and report_case == "silent") or (anchor_case == "changed" and report_case == "new"):
        reopened = json.loads(audit_path.read_bytes())
        reopened.pop("finding_relation")
        reopened.update(run_id=1001, head_sha="c" * 40)
        reopened["result"]["findings"] = [finding]
        reopened["result"]["withdrawals"] = []
        reopened_path = audit_dir / "reopened-primary-audit.json"
        reopened_path.write_text(json.dumps(reopened), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(MODULE_PATH), "--annotate-primary-audit",
            "--audit-path", str(reopened_path), "--previous-findings-path", str(previous_round_path),
        ], cwd=ROOT, env=clean_env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        reopened_relation = json.loads(reopened_path.read_bytes())["finding_relation"]
        expected_reopened_ids = [prior_id] if withdrawal_case == "actual" else ["new-current", prior_id]
        assert [item["id"] for item in reopened_relation["open_findings"]] == expected_reopened_ids
        assert reopened_relation["counts"] == {"new": 1, "repeat": 0, "conflict": 0}

def test_render_previous_context_keeps_same_head_older_attempt(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    rc, payload = _render_previous_round_fixture(
        tmp_path, monkeypatch, objects,
        run_id=ledger["run_id"], run_attempt=ledger["run_attempt"] + 1,
    )

    assert rc == 0
    assert payload["available"] is True
    assert payload.get("head_sha") == ledger["head_sha"]
    assert payload["findings"] == ledger["review"]["result"]["findings"]

@pytest.mark.parametrize("latest_state", ["same-attempt", "future-attempt"])
def test_render_previous_context_degrades_current_or_future_attempt(tmp_path, monkeypatch, capsys, latest_state):
    ledger, _, objects = _previous_round_fixture_objects()
    run_attempt = ledger["run_attempt"]
    if latest_state == "future-attempt":
        latest_key = AGG._ledger_latest_key(ledger["primary_identity"]["repository_id"], ledger["pr_number"])
        future_objects = []
        for key, raw in objects:
            if key == latest_key:
                entry = json.loads(raw)
                entry["run_attempt"] += 1
                entry["primary_identity"]["run_attempt"] += 1
                raw = json.dumps(entry, ensure_ascii=False).encode("utf-8")
            future_objects.append((key, raw))
        objects = future_objects
    rc, payload = _render_previous_round_fixture(
        tmp_path, monkeypatch, objects, run_id=ledger["run_id"], run_attempt=run_attempt,
    )

    assert rc == 0
    assert payload == {"available": False, "detail": "silo-ledger-latest-not-prior", "head_sha": None, "findings": [], "dispositions": [], "open_findings": []}
    assert (
        "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger "
        "detail=silo-ledger-latest-not-prior"
    ) in capsys.readouterr().out

def test_render_previous_context_keeps_missing_prior_head_unknown(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    latest_key = AGG._ledger_latest_key(
        ledger["primary_identity"]["repository_id"], ledger["pr_number"],
    )
    no_head_objects = []
    for key, raw in objects:
        if key == latest_key:
            entry = json.loads(raw)
            entry.pop("head_sha")
            raw = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        no_head_objects.append((key, raw))

    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, no_head_objects)
    output_path = tmp_path / "previous-findings.json"
    loaded = AGG.load_previous_round_findings(
        path=str(output_path), repository_id=ledger["primary_identity"]["repository_id"],
        pr_number=ledger["pr_number"], run_id=35989919390, run_attempt=1,
    )

    assert rc == 0
    assert ledger["primary_identity"]["head_sha"]
    assert "head_sha" in payload
    assert payload["head_sha"] is None
    assert "head_sha" in loaded
    assert loaded["head_sha"] is None
    assert loaded["findings"] == ledger["review"]["result"]["findings"]

def test_render_previous_context_drops_invalid_dispositions_and_reports_count(tmp_path, monkeypatch, capsys):
    ledger, valid, objects = _previous_round_fixture_objects()
    base_key, _ = objects[-1]
    audit = {**ledger["primary_identity"], **ledger["review"]}
    base = {**valid, "audit_digest": CONV.canonical_audit_digest(audit)}
    repo_objects = [*objects[:-1], (base_key, json.dumps(base).encode())]
    for suffix, mutate in (
        ("missing-pointer", lambda item: item["counterevidence"].pop("pointer")),
        ("confirmed", lambda item: item["counterevidence"].update(result="confirmed")),
    ):
        invalid = json.loads(json.dumps(base))
        mutate(invalid)
        repo_objects.append((f"{base_key}-{suffix}", json.dumps(invalid).encode("utf-8")))

    _, payload = _render_previous_round_fixture(tmp_path, monkeypatch, repo_objects)
    out = capsys.readouterr().out

    assert len(payload["dispositions"]) == 1
    assert payload["dispositions"][0]["finding_id"] == valid["finding_id"]
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=1 dropped=2" in out

def test_render_previous_context_writes_only_previous_findings_json(tmp_path, monkeypatch):
    _, _, objects = _previous_round_fixture_objects()
    before = {path.name for path in tmp_path.iterdir()}
    rc, payload = _render_previous_round_fixture(tmp_path, monkeypatch, objects)
    assert rc == 0
    assert set(payload) == {"available", "detail", "head_sha", "findings", "dispositions", "open_findings"}
    assert {path.name for path in tmp_path.iterdir()} - before == {"previous-findings.json"}

def test_render_previous_context_rejects_output_flag(tmp_path, monkeypatch):
    ledger, _, objects = _previous_round_fixture_objects()
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setattr(
        AGG, "_silo_objects_under",
        lambda prefix, timeout_s: [(key, raw) for key, raw in objects if key.startswith(prefix)],
    )
    target = tmp_path / "x"
    with pytest.raises(SystemExit):
        AGG.main([
            "--render-previous-context",
            "--repository-id", str(ledger["primary_identity"]["repository_id"]),
            "--pr-number", str(ledger["pr_number"]),
            "--run-id", "35989919390",
            "--run-attempt", "1",
            "--output", str(target),
            "--previous-json", str(tmp_path / "previous-findings.json"),
        ])
    assert not target.exists()

def test_previous_findings_context_renderer_removed():
    assert not hasattr(AGG, "render_previous_findings_context")
    assert not hasattr(AGG, "PREVIOUS_CONTEXT_MAX_CHARS")

def test_render_previous_context_limits_dispositions_and_projects_deferred(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone

    ledger, template, _ = _previous_round_fixture_objects()
    audit = {**ledger["primary_identity"], **ledger["review"]}
    finding = ledger["review"]["result"]["findings"][-1]
    ledger["review"]["result"]["findings"] = [
        {**finding, "id": f"finding-{i:02}", "line": i + 1, "gate_action": "must_respond"}
        for i in range(35)
    ]
    template.update(audit_digest=CONV.canonical_audit_digest(audit), finding_key="")
    repo_id = template["repository_id"]
    receipts = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(35):
        item = json.loads(json.dumps(template))
        item["finding_id"] = f"finding-{index:02}"
        item["approved_at"] = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        item["reason"] = "r" * 300
        item["counterevidence"]["command"] = "c" * 300
        item["counterevidence"]["output"] = "o" * 300
        item["counterevidence"]["pointer"] = "p" * 300
        if index == 34:
            item["disposition"] = "deferred"
            item.pop("counterevidence")
            item["tracking_issue"] = "#123"
        receipts.append((f"d30/{repo_id}/gate-disposition-receipt-v3-test-{index}/receipt.json", json.dumps(item).encode()))
    latest_key = AGG._ledger_latest_key(int(repo_id), ledger["pr_number"])
    _, payload = _render_previous_round_fixture(
        tmp_path, monkeypatch, [(latest_key, json.dumps(ledger).encode()), *receipts],
    )
    out = capsys.readouterr().out

    assert len(payload["dispositions"]) == 30
    assert [item["finding_id"] for item in payload["dispositions"]] == [f"finding-{i:02}" for i in range(34, 4, -1)]
    assert payload["dispositions"][0]["counterevidence"] is None
    assert payload["dispositions"][0]["tracking_issue"] == "#123"
    assert all(len(value) <= 240 for item in payload["dispositions"] for value in _string_values(item))
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=30 dropped=5" in out
