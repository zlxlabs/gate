"""Real-pytest junit verdicts and behavior receipt accept/reject (gate#404 G3).

JUnit XML is produced by subprocess `python -m pytest --junitxml` in tmp_path.
Hand-written XML is forbidden here; @type strings are locked from pytest output.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from test_gate_disposition_issue_receipt import PRODUCER, REPOSITORY, ROOT, SCOPE, _audit

MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "convergence.py"
BEHAVIOR_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "behavior_counterevidence.py"
VALID_HEAD = "a" * 40
LOCK_NODEID = "tests/test_lock.py::test_locked_behavior"
PARAM_NODEID = "tests/test_lock.py::test_t[case]"


def _load(path, name):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONV = _load(MODULE_PATH, "gate_convergence_from_aggregate")
BEHAVIOR = _load(BEHAVIOR_PATH, "gate_behavior_verdict_tests")

SOURCES = {
    "passed": "def test_locked_behavior():\n    assert True\n",
    "assert": "def test_locked_behavior():\n    assert 1 == 2\n",
    "fail": "import pytest\n\ndef test_locked_behavior():\n    pytest.fail('nope')\n",
    "error": (
        "import pytest\n\n@pytest.fixture\ndef boom():\n    raise RuntimeError('fixture')\n\n"
        "def test_locked_behavior(boom):\n    pass\n"
    ),
    "skip": "import pytest\n\ndef test_locked_behavior():\n    pytest.skip('no')\n",
    "timeout": "def test_locked_behavior():\n    raise AssertionError('TimeoutError while waiting')\n",
    "other": "def test_other():\n    assert True\n",
    "param_pass": (
        "import pytest\n\n@pytest.mark.parametrize('case', ['case'])\n"
        "def test_t(case):\n    assert True\n"
    ),
    "param_fail": (
        "import pytest\n\n@pytest.mark.parametrize('case', ['case'])\n"
        "def test_t(case):\n    assert False\n"
    ),
}


def _patch():
    unified = (
        "--- a/src/guard.py\n+++ b/src/guard.py\n@@ -12,1 +12,1 @@\n"
        "-    return True\n+    return False\n"
    )
    raw = unified.encode("utf-8")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "unified_diff": unified}


def _payload(*, nodeid=LOCK_NODEID, **changes):
    body = {
        "kind": "behavior_replay", "result": "refuted",
        "pointer": f"{nodeid} asserts the predicate at src/guard.py:12",
        "finding_id": "finding-one", "primary_run_id": "7", "primary_run_attempt": "1",
        "head_sha": VALID_HEAD, "runner": "pytest", "nodeid": nodeid, "patch": _patch(),
    }
    body.update(changes)
    return body


def _run_pytest_junit(tmp_path: Path, source: str, filename: str, nodeid: str) -> Path:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / filename).write_text(source, encoding="utf-8")
    xml_path = tmp_path / "junit.xml"
    env = {k: v for k, v in os.environ.items() if k not in {"PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST"}}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
         "-o", "addopts=", f"--junitxml={xml_path}", "--", nodeid],
        cwd=tmp_path, capture_output=True, text=True, check=False, env=env,
    )
    assert xml_path.is_file(), "pytest did not write junit xml"
    return xml_path


def _child(xml_path: Path, tag: str):
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        found = case.find(tag)
        if found is not None:
            return found
    return None


def _type_attr(element) -> str:
    return (element.get("type") if element is not None else None) or ""


def _body_suffix_type(element) -> str:
    text = (element.text or "").strip() if element is not None else ""
    return text.rsplit(":", 1)[-1].strip() if text else ""


def _failure_type(xml_path: Path) -> str:
    for tag in ("failure", "error"):
        element = _child(xml_path, tag)
        if element is not None:
            return _type_attr(element) or _body_suffix_type(element)
    return "skipped" if _child(xml_path, "skipped") is not None else "passed"


def _issue_behavior(tmp_path: Path, *, counterevidence, replay_dir, canonical_dir):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "audit.json").write_text(json.dumps(_audit(head_sha=VALID_HEAD)))
    output_dir = tmp_path / "output"
    command = [
        sys.executable, str(PRODUCER), "issue",
        "--output-dir", str(output_dir), "--audit-path", str(tmp_path / "audit.json"),
        "--repository", REPOSITORY, "--repository-id", "123", "--pr-number", "42",
        "--head-sha", VALID_HEAD, "--finding-id", "finding-one",
        "--reason", "canonical evidence reviewed",
        "--scope-json", json.dumps({**SCOPE, "head_sha": VALID_HEAD}),
        "--approver", "owner", "--approver-id", "10",
        "--approved-at", "2026-09-25T09:00:00Z", "--triggering-actor", "owner",
        "--disposition", "false-positive", "--counterevidence-json", json.dumps(counterevidence),
    ]
    env = {
        **os.environ, "PRIMARY_RUN_ID": "7", "PRIMARY_RUN_ATTEMPT": "1",
        "BEHAVIOR_REPLAY_DIR": str(replay_dir), "BEHAVIOR_CANONICAL_JUNIT_DIR": str(canonical_dir),
        "GITHUB_RUN_ID": "99", "GITHUB_RUN_ATTEMPT": "1",
    }
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env), output_dir


def _dirs_with_xmls(tmp_path: Path, *, canonical_xml, baseline_xml, red_xml, exit_baseline=0, apply_ok=True):
    replay, canon = tmp_path / "replay", tmp_path / "canonical"
    replay.mkdir(parents=True)
    canon.mkdir(parents=True)
    (replay / "baseline.xml").write_bytes(baseline_xml.read_bytes())
    (replay / "red.xml").write_bytes(red_xml.read_bytes())
    (canon / "junit.xml").write_bytes(canonical_xml.read_bytes())
    (replay / "exit_codes.json").write_text(
        json.dumps({"baseline": exit_baseline, "red": 1, "apply_ok": apply_ok}),
    )
    return replay, canon


@pytest.fixture(scope="module")
def xmls(tmp_path_factory):
    root = tmp_path_factory.mktemp("junit")
    lock = LOCK_NODEID

    def make(name, source, nodeid=lock, filename="test_lock.py"):
        return _run_pytest_junit(root / name, source, filename, nodeid)

    return {
        "passed": make("passed", SOURCES["passed"]),
        "assert": make("assert", SOURCES["assert"]),
        "fail": make("fail", SOURCES["fail"]),
        "error": make("error", SOURCES["error"]),
        "skip": make("skip", SOURCES["skip"]),
        "timeout": make("timeout", SOURCES["timeout"]),
        "other": make("other", SOURCES["other"], "tests/test_other.py::test_other", "test_other.py"),
        "param_pass": make("param_pass", SOURCES["param_pass"], PARAM_NODEID),
        "param_fail": make("param_fail", SOURCES["param_fail"], PARAM_NODEID),
    }


def _issue(tmp_path, xmls, red, *, nodeid=LOCK_NODEID, payload=None, canonical="passed", **dir_kw):
    replay, canon = _dirs_with_xmls(
        tmp_path / "evidence",
        canonical_xml=xmls[canonical], baseline_xml=xmls[canonical], red_xml=xmls[red],
        **dir_kw,
    )
    return _issue_behavior(
        tmp_path / "issue", counterevidence=payload or _payload(nodeid=nodeid),
        replay_dir=replay, canonical_dir=canon,
    )


@pytest.mark.parametrize(
    "key, suffix",
    [("assert", "AssertionError"), ("fail", "Failed")],
    ids=["assert", "pytest.fail"],
)
def test_real_pytest_junit_type(xmls, key, suffix):
    xml_path = xmls[key]
    failure = _child(xml_path, "failure")
    # pytest 9.2 junit: no @type; class is the last "path:line: Type" token.
    assert _type_attr(failure) == ""
    assert _body_suffix_type(failure) == suffix
    assert _failure_type(xml_path) == suffix


def test_issuer_accepts_assert_failure_red(tmp_path, xmls):
    result, output_dir = _issue(tmp_path, xmls, "assert")
    assert result.returncode == 0, result.stderr
    replay_obj = json.loads((output_dir / json.loads(result.stdout)["artifact"]).read_text())
    replay_obj = replay_obj["counterevidence"]["gate_replay"]
    assert replay_obj["canonical_outcome"] == replay_obj["baseline_outcome"] == "passed"
    assert replay_obj["red_outcome"] == "failure"
    assert replay_obj["red_failure_type"] == "AssertionError"
    assert replay_obj["contract_version"] == 1
    assert replay_obj["replay_run_id"] == "99"


def test_issuer_accepts_pytest_fail_red(tmp_path, xmls):
    result, output_dir = _issue(tmp_path, xmls, "fail")
    assert result.returncode == 0, result.stderr
    body = json.loads((output_dir / json.loads(result.stdout)["artifact"]).read_text())
    assert body["counterevidence"]["gate_replay"]["red_failure_type"] == "Failed"


def test_issuer_rejects_fixture_error(tmp_path, xmls):
    assert "Error" in _failure_type(xmls["error"]) or _failure_type(xmls["error"]) not in BEHAVIOR.ALLOWED_FAILURE_TYPES
    result, output_dir = _issue(tmp_path, xmls, "error")
    assert result.returncode != 0
    assert "behavior_red_is_error" in result.stderr
    assert not output_dir.exists() or not list(output_dir.iterdir())


def test_issuer_rejects_skip(tmp_path, xmls):
    assert _failure_type(xmls["skip"]) == "skipped"
    result, _ = _issue(tmp_path, xmls, "skip")
    assert result.returncode != 0 and "behavior_red_skipped" in result.stderr


def test_issuer_rejects_missing_nodeid(tmp_path, xmls):
    result, _ = _issue(tmp_path, xmls, "other")
    assert result.returncode != 0 and "behavior_nodeid_not_collected" in result.stderr


def test_issuer_accepts_parametrized_nodeid(tmp_path, xmls):
    result, output_dir = _issue(tmp_path, xmls, "param_fail", nodeid=PARAM_NODEID, canonical="param_pass")
    assert result.returncode == 0, result.stderr
    body = json.loads((output_dir / json.loads(result.stdout)["artifact"]).read_text())
    assert body["counterevidence"]["nodeid"] == PARAM_NODEID
    assert body["counterevidence"]["gate_replay"]["red_failure_type"] == "AssertionError"


def test_issuer_rejects_timeout_marker_in_message(tmp_path, xmls):
    result, _ = _issue(tmp_path, xmls, "timeout")
    assert result.returncode != 0 and "behavior_red_not_assertion" in result.stderr


def test_issuer_rejects_missing_canonical_dir(tmp_path, xmls):
    replay, _ = _dirs_with_xmls(
        tmp_path / "evidence", canonical_xml=xmls["passed"], baseline_xml=xmls["passed"],
        red_xml=xmls["assert"],
    )
    result, _ = _issue_behavior(
        tmp_path / "issue", counterevidence=_payload(),
        replay_dir=replay, canonical_dir=tmp_path / "missing-canonical",
    )
    assert result.returncode != 0 and "behavior_canonical_junit_missing" in result.stderr


def test_issuer_rejects_author_forged_gate_replay(tmp_path, xmls):
    payload = _payload()
    payload["gate_replay"] = {"contract_version": 1}
    result, _ = _issue(tmp_path, xmls, "assert", payload=payload)
    assert result.returncode != 0 and "behavior_replay_author_forged_rerun" in result.stderr


def test_issuer_rejects_patch_apply_failed(tmp_path, xmls):
    result, _ = _issue(tmp_path, xmls, "assert", apply_ok=False)
    assert result.returncode != 0 and "behavior_patch_apply_failed" in result.stderr


def test_issuer_rejects_missing_red_xml(tmp_path, xmls):
    replay, canon = _dirs_with_xmls(
        tmp_path / "evidence", canonical_xml=xmls["passed"], baseline_xml=xmls["passed"],
        red_xml=xmls["passed"],
    )
    (replay / "red.xml").unlink()
    result, _ = _issue_behavior(
        tmp_path / "issue", counterevidence=_payload(), replay_dir=replay, canonical_dir=canon,
    )
    assert result.returncode != 0 and "behavior_replay_no_junit" in result.stderr


def test_issuer_rejects_missing_exit_codes(tmp_path, xmls):
    replay, canon = _dirs_with_xmls(
        tmp_path / "evidence", canonical_xml=xmls["passed"], baseline_xml=xmls["passed"],
        red_xml=xmls["assert"],
    )
    (replay / "exit_codes.json").unlink()
    result, _ = _issue_behavior(
        tmp_path / "issue", counterevidence=_payload(), replay_dir=replay, canonical_dir=canon,
    )
    assert result.returncode != 0 and "behavior_replay_timeout" in result.stderr


def test_issuer_rejects_baseline_not_green(tmp_path, xmls):
    result, _ = _issue(tmp_path, xmls, "assert", canonical="assert", exit_baseline=1)
    assert result.returncode != 0
    assert "behavior_baseline_not_green" in result.stderr or "behavior_canonical_not_passed" in result.stderr


def _gate_replay(**changes):
    body = {
        "contract_version": 1, "replay_run_id": "99", "replay_run_attempt": "1",
        "canonical_outcome": "passed", "baseline_outcome": "passed", "red_outcome": "failure",
        "red_failure_type": "AssertionError",
        "canonical_xml_sha256": "a" * 64, "baseline_xml_sha256": "b" * 64, "red_xml_sha256": "c" * 64,
        "exit_codes": {"baseline": 0, "red": 1, "apply_ok": True},
    }
    body.update(changes)
    return body


def _behavior_receipt(scope=None, *, primary=None):
    scope = scope or CONV.Scope(
        repository_id=123, pr_number=42, base_sha="b" * 40, head_sha="h" * 40,
        diff_digest="d" * 64, policy_version="policy-v1", policy_digest="p" * 64,
        tier="personal", caller_sha="c" * 40, reusable_workflow_sha="w" * 40,
    )
    primary = primary or CONV.CanonicalPrimary(
        schema_version=1, repository_id=scope.repository_id, pr_number=scope.pr_number,
        head_sha=scope.head_sha, run_id=7, run_attempt=2, verdict="fail",
        p1_ids=("p1",),
        p1_findings=(("p1", "major", "inferred", "src/lock.py", 12, "correctness"),),
    )
    finding = primary.p1_findings[0]
    key = CONV.canonical_finding_key(
        {"file": finding[3], "line": finding[4], "category": finding[5], "severity": finding[1]}
    )
    evidence = _payload(finding_id=finding[0], head_sha=scope.head_sha)
    evidence["gate_replay"] = _gate_replay()
    receipt = CONV.DispositionReceipt(
        schema_version=CONV.DISPOSITION_RECEIPT_SCHEMA_VERSION, disposition="false-positive",
        repository_id=str(scope.repository_id), pr_number=scope.pr_number,
        epoch=CONV.derive_epoch(scope), head_sha=scope.head_sha, audit_digest="a" * 64,
        finding_id=finding[0], finding_key=key, reason="locked upstream behavior",
        counterevidence=evidence, approver="octocat", approver_id=1,
        approved_at="2026-08-30T12:00:00Z",
    )
    return receipt, scope, primary


def test_convergence_accepts_behavior_gate_replay():
    receipt, scope, primary = _behavior_receipt()
    status = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest="a" * 64,
    )
    assert (status.valid, status.active, status.reason_code) == (True, True, "active_false_positive")


@pytest.mark.parametrize(
    "replay_changes, reason",
    [
        ({"canonical_outcome": "failure"}, "behavior_canonical_not_passed"),
        ({"baseline_outcome": "failure"}, "behavior_baseline_not_passed"),
        ({"red_outcome": "passed"}, "behavior_red_not_failure"),
        ({"red_failure_type": "TimeoutError"}, "behavior_red_not_assertion"),
        ({"contract_version": 2}, "behavior_contract_version_invalid"),
    ],
    ids=["canonical", "baseline", "red-passed", "timeout-type", "version"],
)
def test_convergence_behavior_gate_replay_reasons(replay_changes, reason):
    receipt, scope, primary = _behavior_receipt()
    evidence = dict(receipt.counterevidence)
    evidence["gate_replay"] = _gate_replay(**replay_changes)
    receipt = replace(receipt, counterevidence=evidence)
    status = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest="a" * 64,
    )
    assert status.reason_code == reason


def test_convergence_rejects_behavior_receipt_without_gate_replay():
    receipt, scope, primary = _behavior_receipt()
    evidence = dict(receipt.counterevidence)
    evidence.pop("gate_replay")
    receipt = replace(receipt, counterevidence=evidence)
    status = CONV.validate_disposition_receipt(
        receipt, scope=scope, primary=primary, audit_digest="a" * 64,
    )
    assert status.reason_code == "behavior_replay_missing"


def test_behavior_receipt_closes_only_one_same_key_finding_id():
    scope = CONV.Scope(
        repository_id=123, pr_number=42, base_sha="b" * 40, head_sha="h" * 40,
        diff_digest="d" * 64, policy_version="policy-v1", policy_digest="p" * 64,
        tier="personal", caller_sha="c" * 40, reusable_workflow_sha="w" * 40,
    )
    primary = CONV.CanonicalPrimary(
        schema_version=1, repository_id=scope.repository_id, pr_number=scope.pr_number,
        head_sha=scope.head_sha, run_id=7, run_attempt=2, verdict="fail",
        p1_ids=("p1", "p2"),
        p1_findings=(
            ("p1", "major", "inferred", "src/lock.py", None, "correctness"),
            ("p2", "major", "inferred", "src/lock.py", None, "correctness"),
        ),
    )
    receipt, _, _ = _behavior_receipt(scope, primary=primary)
    recorded = CONV.record_dispositions(
        primary.p1_ids, (receipt,), scope=scope, primary=primary, audit_digest="a" * 64,
    )
    assert recorded.recorded_finding_ids == ("p1",)
    assert recorded.remaining_p1_ids == ("p2",)


def test_allowlist_constant_is_defined_once():
    assert BEHAVIOR.ALLOWED_FAILURE_TYPES == frozenset({"AssertionError", "Failed"})
