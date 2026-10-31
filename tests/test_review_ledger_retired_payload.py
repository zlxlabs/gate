"""Regression coverage for the compact ledger's current field set."""

from __future__ import annotations

import json

from test_review_ledger import (
    _live_record_audit,
    _preflight,
    _producer_terminal,
    _run_live_record_cli,
)


def test_cli_build_from_real_aggregator_terminal_preserves_current_ledger_shape(tmp_path):
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
    recorded_at = row.pop("recorded_at")
    assert recorded_at
    assert row == {
        "schema_version": 2,
        "repository": "zlxlabs/gate",
        "pr_number": 42,
        "run_id": 999,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "disposition_status": "success",
        "preflight": _preflight(),
        "install": None,
        "primary_identity": {
            "repository_id": 123,
            "repository": "zlxlabs/gate",
            "pr": 42,
            "base_sha": "b" * 40,
            "head_sha": "a" * 40,
            "diff_digest": "d" * 64,
            "policy_version": "policy-v1",
            "policy_digest": "e" * 64,
            "registry_commit": "0123456789abcdef0123456789abcdef01234567",
            "caller_sha": "c" * 40,
            "reusable_workflow_sha": "w" * 40,
            "run_id": 999,
            "run_attempt": 1,
            "job_id": 99,
            "reviewer": "codex-sub",
            "merge_base_sha": "9" * 40,
            "candidate_commit_sha": "f" * 40,
            "candidate_tree_sha": "1" * 40,
            "run_mode": "PAYLOAD_ONLY",
            "spec_source": "event_payload",
            "pr_body_digest": "empty",
        },
        "review": {
            "status": "fail",
            "verdict": "fail",
            "reviewer": "codex-sub",
            "failover": False,
            "attempts": [],
            "cost_usd": 0,
            "tokens": [],
            "runtime": None,
            "coverage": {
                "mode": "single", "complete": True, "diff_lines": 100, "shards": 1,
            },
            "finding_count": 1,
            "finding_ids": ["correctness.bad-state"],
            "category_counts": {"correctness": 1},
            "severity_counts": {"major": 1},
            "trigger_kind_counts": {"unspecified": 1},
            "inferred_p1_count": 0,
            "shadows": {},
            "result": audit["result"],
        },
        "finding_dispositions": {},
        "false_positive_count": 0,
        "disposition_receipt_consumption": terminal["disposition_receipt_consumption"],
        "risk": audit["risk"],
    }
