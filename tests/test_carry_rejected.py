"""Rejected receipts must release eligible sol_verify carry entries.

Card dlg-20261125-070650-302b9e (gate-hub#2579 / design 261122 §8.9): a
rejected receipt previously fell into the projection's catch-all ``else``
and never released a carried finding. Per the gate-hub consumer contract
(scripts/review/previous_round_context.py, #2644) a rejected disposition
projects evidence-free (counterevidence and tracking_issue null, non-blank
reason) and — on the release side — may only free a finding whose own
fields say so: origin ``sol_verify``, ``gate_action`` ``must_respond``,
category outside {test_integrity, security}; any missing field fails
closed. Every scenario drives the real ``--render-previous-context`` CLI
(silo transport monkeypatched, matching test_gate_aggregator.py) and
asserts on the persisted previous-findings.json bytes.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
HEAD_SHA = "h" * 40


def _agg():
    spec = importlib.util.spec_from_file_location("gate_aggregate_carry_rejected", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGG = _agg()

OK_FINDING_ID = "sol-carry-ok"


def _receipt(finding_id, *, disposition="rejected", reason="sol verifier retracted after re-check",
             tracking_issue=None, counterevidence=None):
    return {
        "kind": "gate-disposition-receipt-v3",
        "schema_version": 3,
        "disposition": disposition,
        "repository_id": "123",
        "pr_number": 42,
        "epoch": "e" * 64,
        "head_sha": HEAD_SHA,
        "audit_digest": "d" * 64,
        "finding_id": finding_id,
        "reason": reason,
        "approver": "owner",
        "approver_id": 10,
        "approved_at": "2026-11-25T09:00:00Z",
        "triggering_actor": "owner",
        "triggering_actor_source": "cli",
        "counterevidence": counterevidence,
        "tracking_issue": tracking_issue,
    }


def _finding(finding_id, *, drop=(), **overrides):
    finding = {
        "id": finding_id,
        "severity": "major",
        "trigger_kind": "inferred",
        "file": "src/sol.py",
        "line": 12,
        "category": "correctness",
        "origin": "sol_verify",
        "gate_action": "must_respond",
        "issue": "sol verifier flagged X",
        "acceptance": "fix X",
    }
    finding.update(overrides)
    for key in drop:
        finding.pop(key)
    return finding


def _rerun():
    literal, pathspec = "locked-behavior", "src/lock.py"
    argv = ["git", "grep", "-n", "-F", "-e", literal, HEAD_SHA, "--", pathspec]
    excerpt = f"{pathspec}:1:{literal}"
    return {
        "argv": argv, "exit_code": 0, "match_count": 1,
        "stdout_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(), "excerpt": excerpt,
    }


def _fp_counterevidence():
    return {
        "command": "git grep -n -F -e locked-behavior",
        "output": "no match after fix",
        "result": "refuted",
        "pointer": "src/lock.py:1",
        "gate_rerun": _rerun(),
    }


def _render(tmp_path, monkeypatch, findings, receipts, *, capsys=None):
    """Run the real previous-context CLI against silo bytes and real Git blobs."""
    repo = tmp_path / "blob-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "init", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "src").mkdir()
    (repo / "src" / "sol.py").write_bytes(b"def check():\n    return True\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/sol.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "old"], check=True)
    old_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    (repo / "unrelated.txt").write_text("second commit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "unrelated.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "current"], check=True)
    current_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    for receipt in receipts:
        receipt["head_sha"] = old_sha
        receipt["finding_key"] = AGG._GATE_ACTION.canonical_finding_key(
            next(finding for finding in findings if finding["id"] == receipt["finding_id"])
        )
        if isinstance(receipt.get("counterevidence"), dict):
            receipt["counterevidence"]["gate_rerun"]["argv"][6] = old_sha
    objects = [(AGG._ledger_latest_key(123, 42), json.dumps({
        "pr_number": 42, "run_id": 100, "run_attempt": 1, "head_sha": current_sha,
        "review": {"result": {"findings": findings}},
    }).encode("utf-8"))]
    for index, receipt in enumerate(receipts):
        key = f"{AGG.SILO_DISPOSITION_TIER}/123/{AGG.DISPOSITION_ARTIFACT_PREFIX}case-{index}/r.json"
        objects.append((key, json.dumps(receipt).encode("utf-8")))
    by_key = dict(objects)

    def fake_silo_cli(argv, timeout_s):
        dest = argv[argv.index("--dest") + 1]
        body = by_key.get(argv[argv.index("--key") + 1])
        if body is None:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="absent")
        Path(dest).write_bytes(body)
        return subprocess.CompletedProcess(argv, 0, stdout=dest, stderr="")

    def local_blob_oids(*, repository, current_head_sha, head_paths, token):
        return {
            (sha, path): subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--verify", f"{sha}:{path}"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            for sha, path in head_paths
        }

    monkeypatch.setenv("GITHUB_REPOSITORY", "zlxlabs/gate")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(AGG._NEIGHBOR_EVIDENCE, "collect_file_blob_oids", local_blob_oids)
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    monkeypatch.setattr(
        AGG, "_silo_objects_under",
        lambda prefix, timeout_s: [obj for obj in objects if obj[0].startswith(prefix)],
    )
    output = tmp_path / "previous-findings.json"
    rc = AGG.main([
        "--render-previous-context", "--repository-id", "123", "--pr-number", "42",
        "--run-id", "101", "--run-attempt", "1", "--previous-json", str(output),
    ])
    assert rc == 0
    raw = output.read_bytes()
    assert raw.endswith(b"\n")
    if capsys is not None:
        return json.loads(raw), raw, capsys.readouterr().out
    return json.loads(raw), raw, ""


def _rejected_by_id(payload, finding_id):
    return next(item for item in payload["dispositions"]
                if item["disposition"] == "rejected" and item["finding_id"] == finding_id)


def _ok_pair():
    """Compliant rejected receipt for an eligible finding — released post-fix, blocked on base."""
    return [_finding(OK_FINDING_ID)], [_receipt(OK_FINDING_ID)]


def test_compliant_rejected_projects_and_releases(tmp_path, monkeypatch, capsys):
    payload, _, out = _render(tmp_path, monkeypatch, *_ok_pair(), capsys=capsys)

    assert out == "GATE-PREVIOUS-DISPOSITIONS: kept=1 dropped=0\n"
    assert payload["available"] is True and payload["head_sha"]
    assert payload["findings"] == [_finding(OK_FINDING_ID)]  # findings stay verbatim
    rejected = payload["dispositions"][0]
    assert set(rejected) == {
        "finding_id", "disposition", "head_sha", "approved_at", "reason",
        "counterevidence", "tracking_issue",
    }
    assert rejected == {
        "finding_id": OK_FINDING_ID,
        "disposition": "rejected", "head_sha": rejected["head_sha"],
        "approved_at": "2026-11-25T09:00:00Z", "reason": "sol verifier retracted after re-check",
        "counterevidence": None, "tracking_issue": None,
    }
    assert len(rejected["head_sha"]) == 40 and rejected["head_sha"] != payload["head_sha"]
    assert payload["open_findings"] == []  # eligible finding released from the carry


@pytest.mark.parametrize("origin", ["primary", None])
def test_rejected_requires_sol_verify_origin(tmp_path, monkeypatch, capsys, origin):
    guard = _finding("guard-primary", origin=origin, line=13)
    if origin is None:
        guard = _finding("guard-primary", drop=("origin",), line=13)
    findings, receipts = _ok_pair()
    findings.append(guard)
    receipts.append(_receipt("guard-primary"))

    payload, _, out = _render(tmp_path, monkeypatch, findings, receipts, capsys=capsys)

    assert out == "GATE-PREVIOUS-DISPOSITIONS: kept=2 dropped=0\n"  # projection is origin-agnostic
    assert {item["finding_id"] for item in payload["dispositions"]} == {OK_FINDING_ID, "guard-primary"}
    assert [item["id"] for item in payload["open_findings"]] == ["guard-primary"]


def test_rejected_requires_must_respond(tmp_path, monkeypatch, capsys):
    findings, receipts = _ok_pair()
    findings.append(_finding("guard-block", gate_action="block", line=13))
    receipts.append(_receipt("guard-block"))

    payload, _, out = _render(tmp_path, monkeypatch, findings, receipts, capsys=capsys)

    assert out == "GATE-PREVIOUS-DISPOSITIONS: kept=2 dropped=0\n"
    assert [item["id"] for item in payload["open_findings"]] == ["guard-block"]


@pytest.mark.parametrize("category", ["security", "test_integrity"])
def test_rejected_refuses_owner_protected_categories(tmp_path, monkeypatch, capsys, category):
    findings, receipts = _ok_pair()
    findings.append(_finding(f"guard-{category}", category=category, line=13))
    receipts.append(_receipt(f"guard-{category}"))

    payload, _, out = _render(tmp_path, monkeypatch, findings, receipts, capsys=capsys)

    assert out == "GATE-PREVIOUS-DISPOSITIONS: kept=2 dropped=0\n"
    assert [item["id"] for item in payload["open_findings"]] == [f"guard-{category}"]


@pytest.mark.parametrize("drop", [("origin",), ("gate_action",), ("category",)])
def test_rejected_missing_field_fails_closed(tmp_path, monkeypatch, capsys, drop):
    findings, receipts = _ok_pair()
    findings.append(_finding("guard-incomplete", drop=drop, line=13))
    receipts.append(_receipt("guard-incomplete"))

    payload, _, out = _render(tmp_path, monkeypatch, findings, receipts, capsys=capsys)

    assert out == "GATE-PREVIOUS-DISPOSITIONS: kept=2 dropped=0\n"
    assert [item["id"] for item in payload["open_findings"]] == ["guard-incomplete"]


def test_blank_reason_rejected_stays_dropped(tmp_path, monkeypatch, capsys):
    findings, receipts = _ok_pair()
    findings.append(_finding("sol-carry-blank", line=13))
    receipts.append(_receipt("sol-carry-blank", reason="   "))

    payload, _, out = _render(tmp_path, monkeypatch, findings, receipts, capsys=capsys)

    assert out == "GATE-PREVIOUS-DISPOSITIONS: kept=1 dropped=1\n"
    assert [item["finding_id"] for item in payload["dispositions"]] == [OK_FINDING_ID]
    assert [item["id"] for item in payload["open_findings"]] == ["sol-carry-blank"]


def test_write_previous_context_omits_finding_key_and_preserves_rejected_release(tmp_path):
    finding = _finding(OK_FINDING_ID)
    finding_key = AGG._GATE_ACTION.canonical_finding_key(finding)
    disposition = {
        "finding_id": OK_FINDING_ID,
        "finding_key": finding_key,
        "disposition": "rejected",
        "head_sha": "h" * 40,
        "approved_at": "2026-11-25T09:00:00Z",
        "reason": "sol verifier retracted after re-check",
        "counterevidence": None,
        "tracking_issue": None,
    }
    previous = {"findings": [finding], "available": True}
    path = tmp_path / "previous-findings.json"

    AGG._CARRY.write_previous_context(
        previous, [disposition], str(path),
        rejected_release_keys={(OK_FINDING_ID, finding_key)},
        canonical_finding_key=AGG._GATE_ACTION.canonical_finding_key,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "finding_id", "disposition", "head_sha", "approved_at", "reason",
        "counterevidence", "tracking_issue",
    }
    assert all(set(item) == expected_keys for item in payload["dispositions"])
    assert "finding_key" in disposition  # serialization does not mutate the in-memory input
    assert payload["open_findings"] == []  # rejected-release eligibility still uses the in-memory disposition


def test_false_positive_and_deferred_behavior_unchanged(tmp_path, monkeypatch, capsys):
    findings, receipts = _ok_pair()
    findings.append(_finding("fp-carry", origin="primary", category="security", line=13))
    receipts.append(_receipt("fp-carry", disposition="false-positive",
                             reason="locked upstream behavior", counterevidence=_fp_counterevidence()))
    findings.append(_finding("def-carry", origin="primary", line=14))
    receipts.append(_receipt("def-carry", disposition="deferred", reason="follow-up scheduled",
                             tracking_issue="#123"))

    payload, _, out = _render(tmp_path, monkeypatch, findings, receipts, capsys=capsys)

    assert out == "GATE-PREVIOUS-DISPOSITIONS: kept=3 dropped=0\n"
    fp = next(item for item in payload["dispositions"] if item["disposition"] == "false-positive")
    assert fp["counterevidence"]["result"] == "refuted" and fp["tracking_issue"] is None
    deferred = next(item for item in payload["dispositions"] if item["disposition"] == "deferred")
    assert deferred["tracking_issue"] == "#123" and deferred["counterevidence"] is None
    # false-positive releases the covered finding regardless of origin/category;
    # deferred releases the must_respond finding; rejected releases the sol one.
    assert payload["open_findings"] == []
