"""Publish the actual gate-terminal and previous-context bytes for gate#481."""
import json
import subprocess
from pathlib import Path

import pytest

from test_gate_aggregator import AGG, _cli_args
from test_gate_aggregator_caller_checks import _false_positive_receipt, _scope_for
from test_review_ledger import _live_record_audit, _run_live_record_cli

CONV = AGG._CONVERGENCE


def _audit(verdict, severity):
    audit = _live_record_audit()
    audit["verdict"] = audit["result"]["verdict"] = verdict
    audit["result"]["findings"][0].update(
        severity=severity, trigger_kind="inferred", file="src/lock.py", line=12,
    )
    return audit


def _receipt(audit, **changes):
    return _false_positive_receipt(
        _scope_for(audit), audit_digest=CONV.canonical_audit_digest(audit),
        finding_id=audit["result"]["findings"][0]["id"], **changes,
    )


@pytest.mark.parametrize("severity", ["major", "blocker"])
def test_terminal_pass_requires_all_p1_resolved(tmp_path, monkeypatch, severity):
    audit = _audit("pass", severity)
    finding_id = audit["result"]["findings"][0]["id"]
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(audit), encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setattr(AGG, "_load_pr_convergence_history", lambda **_: ("available", (), AGG.HistoryLoad()))

    for receipts, expected_rc, expected_result, expected_reason, remaining in (
        ((_receipt(audit),), 0, "pass", "primary_pass", []),
        ((), 1, "fail", "primary_findings", [finding_id]),
    ):
        monkeypatch.setattr(AGG, "_fetch_disposition_receipts", lambda **_: receipts)
        rc = AGG.main(_cli_args(audit_dir, summary))
        terminal = json.loads(summary.with_name("gate-terminal.json").read_bytes())
        assert terminal["primary_verdict"] == "pass"
        assert terminal["disposition_receipt_consumption"]["remaining_p1_ids"] == remaining
        assert (terminal["gate_result"], terminal["reason_code"], rc) == (
            expected_result, expected_reason, expected_rc,
        )


@pytest.mark.parametrize(("changes", "reason"), [
    ({"head_sha": "f" * 40}, "head_sha_mismatch"),
    ({"approver_id": 0}, "malformed_receipt"),
    ({"epoch": "f" * 64}, "epoch_mismatch_stale"),
])
def test_previous_only_releases_with_active_receipt(tmp_path, monkeypatch, capsys, changes, reason):
    audit = _audit("fail", "blocker")
    finding = audit["result"]["findings"][0]
    proc, ledger_path, _ = _run_live_record_cli(tmp_path / "ledger", audit=audit)
    assert proc.returncode == 0, proc.stderr
    ledger_bytes = ledger_path.read_bytes()
    ledger_key = AGG._ledger_latest_key(123, 42)

    def silo_get(argv, timeout):
        assert argv[:3] == ["get", "--key", ledger_key]
        Path(argv[argv.index("--dest") + 1]).write_bytes(ledger_bytes)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setattr(AGG, "_silo_cli", silo_get)
    output = tmp_path / "previous.json"
    for receipt_changes, active in (({}, True), (changes, False)):
        receipt = _receipt(audit, **receipt_changes)
        name = CONV.disposition_receipt_artifact_name(receipt)
        key = f"d30/123/{name}/{name}.json"
        raw = json.dumps({**receipt.as_dict(), "kind": CONV.DISPOSITION_RECEIPT_KIND}).encode()
        monkeypatch.setattr(AGG, "_silo_objects_under", lambda prefix, _: [(key, raw)] if key.startswith(prefix) else [])
        assert AGG.main([
            "--render-previous-context", "--repository-id", "123", "--pr-number", "42",
            "--run-id", "1000", "--run-attempt", "1", "--previous-json", str(output),
        ]) == 0
        payload = json.loads(output.read_bytes())
        assert payload["head_sha"] == audit["head_sha"]
        assert payload["findings"] == [finding]
        assert payload["open_findings"] == ([] if active else [finding])
        assert [item["finding_id"] for item in payload["dispositions"]] == ([finding["id"]] if active else [])
        if not active:
            assert f"reason={reason}" in capsys.readouterr().err
