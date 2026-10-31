"""Regression coverage for the compact ledger's current field set."""

from __future__ import annotations

import json

from test_review_ledger import (
    _live_record_audit,
    _module,
    _preflight,
    _producer_terminal,
    _run_live_record_cli,
)


def test_cli_build_from_real_aggregator_terminal_preserves_current_ledger_shape(tmp_path):
    module = _module()
    _agg, _conv, _identity, _receipts, _outcome, terminal = _producer_terminal(receipts=[{}])
    audit = _live_record_audit()
    completed, ledger_path, _record_path = _run_live_record_cli(
        tmp_path, audit=audit, terminal=terminal,
    )

    assert completed.returncode == 0, completed.stderr
    terminal_bytes = (tmp_path / "terminal.json").read_bytes()
    assert json.loads(terminal_bytes) == terminal
    ledger_bytes = ledger_path.read_bytes()
    assert ledger_bytes.endswith(b"\n")
    row = json.loads(ledger_bytes)
    assert json.loads(completed.stdout) == row
    expected = module.build_entry(
        repository="zlxlabs/gate", pr_number=42, run_id=999, run_attempt=1,
        head_sha="a" * 40, preflight=_preflight(), audit=audit,
        expected_identity={
            "repository_id": 123, "base_sha": "b" * 40,
            "caller_sha": "c" * 40, "reusable_workflow_sha": "w" * 40,
        },
        install=None, terminal_envelope=terminal,
    )
    assert set(row) == set(module.LEDGER_ENTRY_REQUIRED_FIELDS) | {
        "disposition_receipt_consumption", "risk",
    }
    row.pop("recorded_at")
    expected.pop("recorded_at")
    assert row == expected
