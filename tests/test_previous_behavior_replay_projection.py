"""Lock the serialized previous-findings projection for behavior replay receipts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from test_review_ledger import _live_record_audit

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
REPOSITORY_ID = 123
PR_NUMBER = 42


def _aggregate():
    spec = importlib.util.spec_from_file_location("gate_aggregate_behavior_projection", AGGREGATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGG = _aggregate()
CONV = AGG._CONVERGENCE


def _behavior_evidence(finding_id: str, *, include_gate_replay: bool = True) -> dict[str, Any]:
    unified_diff = (
        "--- a/src/guard.py\n+++ b/src/guard.py\n@@ -12,1 +12,1 @@\n"
        "-    return True\n+    return False\n"
    )
    evidence = {
        "kind": "behavior_replay",
        "result": "refuted",
        "pointer": "tests/test_guard.py::test_guard asserts src/guard.py:12",
        "finding_id": finding_id,
        "primary_run_id": "7",
        "primary_run_attempt": "1",
        "head_sha": HEAD_SHA,
        "runner": "pytest",
        "nodeid": "tests/test_guard.py::test_guard",
        "patch": {
            "sha256": hashlib.sha256(unified_diff.encode("utf-8")).hexdigest(),
            "byte_len": len(unified_diff.encode("utf-8")),
            "unified_diff": unified_diff,
        },
    }
    if include_gate_replay:
        evidence["gate_replay"] = {
            "contract_version": 2,
            "replay_run_id": "99",
            "replay_run_attempt": "1",
            "canonical_outcome": "passed",
            "baseline_outcome": "passed",
            "red_outcome": "failure",
            "red_failure_type": "AssertionError",
            "canonical_xml_sha256": "c" * 64,
            "baseline_xml_sha256": "d" * 64,
            "red_xml_sha256": "e" * 64,
            "exit_codes": {"baseline": 0, "red": 1, "apply_ok": True},
        }
    return evidence


def _literal_evidence(*, absent: bool) -> dict[str, Any]:
    literal = "locked-behavior"
    argv = ["git", "grep", "-n", "-F", "-e", literal, HEAD_SHA, "--", "src/guard.py"]
    rerun: dict[str, Any] = {
        "argv": argv,
        "exit_code": 1 if absent else 0,
        "match_count": 0 if absent else 1,
        "excerpt": "no matching lines" if absent else "src/guard.py:12:locked-behavior",
    }
    evidence = {
        "kind": "literal_grep",
        "command": "git grep -n -F -e locked-behavior",
        "output": "no match after fix" if absent else "src/guard.py:12:locked-behavior",
        "result": "refuted",
        "pointer": "src/guard.py:12",
        "gate_rerun": rerun,
    }
    if absent:
        base_argv = list(argv)
        base_argv[6] = BASE_SHA
        rerun["base_rerun"] = {
            "argv": base_argv,
            "exit_code": 0,
            "match_count": 1,
            "excerpt": "src/guard.py:12:locked-behavior",
        }
        evidence["match_expectation"] = "absent"
    return evidence


def _receipt(
    finding_id: str,
    evidence: dict[str, Any],
    *,
    approved_at: str,
    expected_reason: str | None = None,
):
    receipt = CONV.DispositionReceipt(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION,
        disposition="false-positive",
        repository_id=str(REPOSITORY_ID),
        pr_number=PR_NUMBER,
        epoch="f" * 64,
        head_sha=HEAD_SHA,
        audit_digest="d" * 64,
        finding_id=finding_id,
        reason="verified counterevidence",
        approver="owner",
        approver_id=10,
        approved_at=approved_at,
        triggering_actor="owner",
        triggering_actor_source="session",
        counterevidence=evidence,
    )
    assert CONV._counterevidence_reason(receipt) == expected_reason
    payload = receipt.as_dict()
    payload["kind"] = CONV.DISPOSITION_RECEIPT_KIND
    return receipt, payload


def _render(tmp_path: Path, monkeypatch, findings: list[dict[str, Any]], receipts: list[dict[str, Any]]):
    ledger = {
        "pr_number": PR_NUMBER,
        "run_id": 100,
        "run_attempt": 1,
        "head_sha": HEAD_SHA,
        "primary_identity": {**_live_record_audit(), "run_id": 100},
        "review": {"verdict": "fail", "result": {"findings": findings}},
    }
    identity = AGG.Identity(REPOSITORY_ID, HEAD_SHA, 100, 1, PR_NUMBER)
    audit = {**ledger["primary_identity"], **ledger["review"]}
    scope, _ = AGG._convergence_scope_from_audit(audit, identity)
    objects = [(AGG._ledger_latest_key(REPOSITORY_ID, PR_NUMBER), json.dumps(ledger).encode("utf-8"))]
    for payload in receipts:
        payload.update(epoch=CONV.derive_epoch(scope), audit_digest=CONV.canonical_audit_digest(audit))
        receipt = CONV.parse_disposition_receipt(payload)
        name = CONV.disposition_receipt_artifact_name(receipt)
        objects.append((f"{AGG.SILO_DISPOSITION_TIER}/{REPOSITORY_ID}/{name}/{name}.json", json.dumps(payload).encode("utf-8")))
    by_key = dict(objects)

    def fake_silo_cli(argv, timeout_s):
        key = argv[argv.index("--key") + 1]
        dest = Path(argv[argv.index("--dest") + 1])
        raw = by_key.get(key)
        if raw is None:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="absent")
        dest.write_bytes(raw)
        return subprocess.CompletedProcess(argv, 0, stdout=str(dest), stderr="")

    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    monkeypatch.setattr(
        AGG, "_silo_objects_under",
        lambda prefix, timeout_s: [(key, raw) for key, raw in objects if key.startswith(prefix)],
    )
    output = tmp_path / "previous-findings.json"
    rc = AGG.main([
        "--render-previous-context",
        "--repository-id", str(REPOSITORY_ID),
        "--pr-number", str(PR_NUMBER),
        "--run-id", "101",
        "--run-attempt", "1",
        "--previous-json", str(output),
    ])
    raw = output.read_bytes()
    return rc, raw, json.loads(raw)


def test_previous_context_projects_behavior_replay_and_preserves_literal_contract(
    tmp_path, monkeypatch, capsys,
):
    behavior_finding = {
        "id": "behavior-finding", "severity": "major", "file": "src/guard.py", "line": 12,
        "category": "correctness", "issue": "behavior replay finding", "acceptance": "verify behavior",
    }
    literal_finding = {
        "id": "literal-finding", "severity": "minor", "file": "src/guard.py", "line": 13,
        "category": "correctness", "issue": "literal grep finding", "acceptance": "verify literal",
    }
    absent_finding = {
        "id": "absent-finding", "severity": "minor", "file": "src/guard.py", "line": 14,
        "category": "correctness", "issue": "absent grep finding", "acceptance": "verify absence",
    }
    unrelated_finding = {
        "id": "unrelated-finding", "severity": "minor", "file": "src/other.py", "line": 23,
        "category": "reliability", "issue": "keep this unrelated fact", "acceptance": "do not lose it",
        "quote": "unicode stays: 反证\nand a second line",
    }
    findings = [behavior_finding, literal_finding, absent_finding, unrelated_finding]
    for finding in findings[:3]:
        finding.update(trigger_kind="inferred", gate_action="must_respond")
    unrelated_finding["gate_action"] = "comment"

    behavior_receipt, behavior_payload = _receipt(
        behavior_finding["id"], _behavior_evidence(behavior_finding["id"]),
        approved_at="2026-12-01T00:00:00Z",
    )
    assert CONV._counterevidence_reason(behavior_receipt) is None
    assert CONV._behavior_gate_replay_reason(behavior_receipt.counterevidence) is None
    _, literal_payload = _receipt(
        literal_finding["id"], _literal_evidence(absent=False),
        approved_at="2026-11-30T00:00:00Z",
    )
    _, absent_payload = _receipt(
        absent_finding["id"], _literal_evidence(absent=True),
        approved_at="2026-11-29T00:00:00Z",
    )
    _, invalid_payload = _receipt(
        "invalid-behavior-finding",
        _behavior_evidence("invalid-behavior-finding", include_gate_replay=False),
        approved_at="2026-11-28T00:00:00Z",
        expected_reason="behavior_replay_missing",
    )

    rc, raw, payload = _render(
        tmp_path,
        monkeypatch,
        findings,
        [behavior_payload, literal_payload, absent_payload, invalid_payload],
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert raw.endswith(b"\n")
    assert payload["findings"] == findings
    assert b'"findings": ' + json.dumps(findings, ensure_ascii=False).encode("utf-8") in raw
    assert "GATE-PREVIOUS-DISPOSITIONS: kept=3 dropped=1" in out

    dispositions = {item["finding_id"]: item for item in payload["dispositions"]}
    assert payload["dispositions"][0]["finding_id"] == behavior_finding["id"]
    assert dispositions[behavior_finding["id"]]["counterevidence"] == {
        "kind": "behavior_replay",
        "result": "refuted",
        "pointer": "tests/test_guard.py::test_guard asserts src/guard.py:12",
        "runner": "pytest",
        "nodeid": "tests/test_guard.py::test_guard",
    }
    assert set(dispositions[behavior_finding["id"]]["counterevidence"]) == {
        "kind", "result", "pointer", "runner", "nodeid",
    }
    assert "patch" not in dispositions[behavior_finding["id"]]["counterevidence"]
    assert "gate_replay" not in dispositions[behavior_finding["id"]]["counterevidence"]
    assert dispositions[literal_finding["id"]]["counterevidence"] == {
        "command": "git grep -n -F -e locked-behavior",
        "output": "src/guard.py:12:locked-behavior",
        "result": "refuted",
        "pointer": "src/guard.py:12",
    }
    assert dispositions[absent_finding["id"]]["counterevidence"] == {
        "command": "git grep -n -F -e locked-behavior",
        "output": "no match after fix",
        "result": "refuted",
        "pointer": "src/guard.py:12",
        "match_expectation": "absent",
    }
    assert "invalid-behavior-finding" not in dispositions
