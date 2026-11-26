from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONVERGENCE_PATH = ROOT / ".github/actions/gate-aggregator/convergence.py"
NEW_MODULES = {
    ".github/actions/gate-aggregator/convergence_contract.py",
    ".github/actions/gate-aggregator/disposition_receipts.py",
    ".github/actions/gate-aggregator/dispositions.py",
}


def _load_convergence(name: str):
    spec = importlib.util.spec_from_file_location(name, CONVERGENCE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_split_modules_share_single_dataclass_identity():
    convergence = _load_convergence("gate_convergence_split_identity_test")
    contract = sys.modules["gate_convergence_contract"]
    receipts = sys.modules["gate_disposition_receipts"]
    dispositions = sys.modules["gate_dispositions"]

    assert convergence.Scope is contract.Scope
    assert convergence.DispositionReceipt is contract.DispositionReceipt
    assert receipts.DispositionReceipt is contract.DispositionReceipt
    assert dispositions.DispositionReceipt is contract.DispositionReceipt
    assert convergence.ConvergenceState is contract.ConvergenceState


def test_evaluate_round_uses_patchable_compatibility_export(monkeypatch):
    convergence = _load_convergence("gate_convergence_split_monkeypatch_test")
    dispositions = sys.modules["gate_dispositions"]
    assert convergence.record_dispositions is dispositions.record_dispositions

    scope = convergence.Scope(
        repository_id=123,
        pr_number=42,
        base_sha="b" * 40,
        head_sha="a" * 40,
        diff_digest="c" * 64,
        policy_version="v1",
        policy_digest="d" * 64,
        tier="personal",
        caller_sha="e" * 40,
        reusable_workflow_sha="f" * 40,
    )
    primary = convergence.CanonicalPrimary(
        schema_version=convergence.SCHEMA_VERSION,
        repository_id=scope.repository_id,
        pr_number=scope.pr_number,
        head_sha=scope.head_sha,
        run_id=900,
        run_attempt=1,
        verdict="pass",
    )
    state = convergence.initial_state(scope)
    processing_key = convergence.ProcessingKey(
        repository_id=scope.repository_id,
        pr_number=scope.pr_number,
        run_id=primary.run_id,
        run_attempt=primary.run_attempt,
    )
    calls = []

    def fake_record_dispositions(p1_ids, receipts, **kwargs):
        calls.append((p1_ids, receipts, kwargs))
        return convergence.DispositionAudit(
            primary_p1_ids=(),
            recorded_receipts=(),
            rejected_receipts=(),
            remaining_p1_ids=(),
        )

    monkeypatch.setattr(convergence, "record_dispositions", fake_record_dispositions)
    decision = convergence.evaluate_round(
        state=state,
        scope=scope,
        primary=primary,
        audit_digest="a" * 64,
        processing_key=processing_key,
    )

    assert len(calls) == 1
    assert decision.state.terminal_decision == "converged"


def test_disposition_sparse_manifest_materializes_importable_receipt_stack(tmp_path):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/gate-v2-disposition.yml").read_text(encoding="utf-8")
    )
    checkout_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Checkout disposition producer"
    ]
    assert len(checkout_steps) == 1
    sparse_value = checkout_steps[0]["env"]["GATE_CHECKOUT_SPARSE"]
    sparse_paths = [line.strip() for line in sparse_value.splitlines() if line.strip()]
    assert NEW_MODULES <= set(sparse_paths)

    for relative in sparse_paths:
        source = ROOT / relative
        assert source.is_file(), f"sparse manifest source is absent: {relative}"
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    script = r'''import importlib.util, json, sys
from pathlib import Path
root = Path.cwd()
issue_path = root / ".github/actions/gate-disposition/issue_receipt.py"
spec = importlib.util.spec_from_file_location("sparse_issue_receipt", issue_path)
issue = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = issue
spec.loader.exec_module(issue)
convergence = issue._CONVERGENCE
second_spec = importlib.util.spec_from_file_location(
    "sparse_convergence_second_load",
    root / ".github/actions/gate-aggregator/convergence.py",
)
second = importlib.util.module_from_spec(second_spec)
sys.modules[second_spec.name] = second
second_spec.loader.exec_module(second)
receipt = convergence.DispositionReceipt(
    disposition="deferred",
    repository_id="123",
    pr_number=42,
    epoch="epoch",
    head_sha="a" * 40,
    audit_digest="b" * 64,
    finding_id="finding-1",
    reason="tracked for follow-up",
    approver="owner",
    approver_id=7,
    approved_at="2026-11-26T13:00:00Z",
    tracking_issue="#453",
)
parsed = convergence.parse_disposition_receipt(receipt.as_dict())
contract = sys.modules["gate_convergence_contract"]
assert isinstance(parsed, contract.DispositionReceipt)
assert second.DispositionReceipt is contract.DispositionReceipt
print(json.dumps({"receipt": parsed.as_dict(), "single_class_identity": True}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["single_class_identity"] is True
    assert result["receipt"]["tracking_issue"] == "#453"
    assert result["receipt"]["disposition"] == "deferred"
