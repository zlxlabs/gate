"""Vector contract tests for behavior_replay counterevidence syntax.

The public module beside the aggregator is the contract. These tests walk the
versioned JSON vectors and pin the argv v1 module so a parallel contract cannot
bump the literal-grep version by accident.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "behavior_counterevidence.py"
VECTORS_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "behavior_counterevidence_v1_vectors.json"
ARGV_MODULE_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "counterevidence_argv.py"
ARGV_VECTORS_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "counterevidence_argv_v1_vectors.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


MOD = _load(MODULE_PATH, "gate_public_behavior_counterevidence")
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def test_contract_version_is_1():
    assert MOD.BEHAVIOR_COUNTEREVIDENCE_CONTRACT_VERSION == 1
    assert VECTORS["contract_version"] == 1


def test_vectors_cover_legal_and_boundary_cases():
    legal = [case for case in VECTORS["cases"] if case["reason_code"] is None]
    assert len(legal) >= 2
    assert any(case["id"] == "allowed_line_window_boundary" for case in legal)
    assert any(case["id"] == "hunk_outside_window_plus5" for case in VECTORS["cases"])
    assert any(case["id"] == "patch_too_large_8193" for case in VECTORS["cases"])


def _bindings(case: dict) -> tuple[object, str, str, str, str, object]:
    payload = case["payload"]
    finding_id = case.get("finding_id", VECTORS["finding_id"])
    primary_run_id = case.get("primary_run_id", VECTORS["primary_run_id"])
    primary_run_attempt = case.get("primary_run_attempt", VECTORS["primary_run_attempt"])
    head_sha = case.get("head_sha", VECTORS["head_sha"])
    finding = case.get("finding", VECTORS["finding"])
    return payload, finding_id, primary_run_id, primary_run_attempt, head_sha, finding


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["id"])
def test_versioned_behavior_vectors(case):
    payload, finding_id, primary_run_id, primary_run_attempt, head_sha, finding = _bindings(case)
    got = MOD.behavior_counterevidence_reason(
        payload,
        finding_id,
        primary_run_id,
        primary_run_attempt,
        head_sha,
        finding,
    )
    assert got == case["reason_code"]


def test_argv_contract_version_and_vectors_unaffected():
    argv_mod = _load(ARGV_MODULE_PATH, "gate_public_counterevidence_argv")
    assert argv_mod.COUNTEREVIDENCE_ARGV_CONTRACT_VERSION == 1
    payload = json.loads(ARGV_VECTORS_PATH.read_text(encoding="utf-8"))
    assert payload["contract_version"] == 1
    for case in payload["cases"]:
        head_sha = case.get("head_sha", payload["head_sha"])
        got = argv_mod.counterevidence_argv_reason(case["argv"], head_sha)
        if case["reason_detail"] is None:
            assert got is None
        else:
            assert got is not None
            assert got.startswith(payload["reason_code"] + ":")
            assert case["reason_detail"] in got
