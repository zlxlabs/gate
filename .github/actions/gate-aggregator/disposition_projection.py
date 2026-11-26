"""Pure projection of the disposition-receipt audit into terminal data."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_CONVERGENCE_NAME = "gate_convergence_from_aggregate"
_CONVERGENCE = sys.modules.get(_CONVERGENCE_NAME)
if _CONVERGENCE is None:
    _CONVERGENCE_SPEC = importlib.util.spec_from_file_location(
        _CONVERGENCE_NAME, Path(__file__).with_name("convergence.py")
    )
    assert _CONVERGENCE_SPEC and _CONVERGENCE_SPEC.loader
    _CONVERGENCE = importlib.util.module_from_spec(_CONVERGENCE_SPEC)
    sys.modules[_CONVERGENCE_NAME] = _CONVERGENCE
    _CONVERGENCE_SPEC.loader.exec_module(_CONVERGENCE)

def empty_disposition_receipt_audit() -> dict[str, Any]:
    """Default receipt-consumption block for a run without current receipts."""
    return {
        "recorded": [],
        "resolved": [],
        "consumed_count": 0,
        "rejected_count": 0,
        "rejected_reasons": {},
        "fail_closed": False,
        "remaining_p1_ids": [],
    }

def project_disposition_receipt_audit(audit: Any) -> dict[str, Any]:
    """Project active evidence-bound receipts and rejected receipt reasons."""
    if audit is None:
        return empty_disposition_receipt_audit()
    recorded_finding_ids = audit.recorded_finding_ids
    if len(recorded_finding_ids) != len(audit.recorded_receipts):
        raise ValueError("disposition audit has mismatched recorded finding ids")
    recorded = []
    resolved = []
    for index, receipt in enumerate(audit.recorded_receipts):
        target = recorded_finding_ids[index]
        if receipt.disposition == "false-positive":
            evidence = receipt.counterevidence or {}
            evidence_pointer = _CONVERGENCE._bounded_disposition_text(evidence.get("pointer", ""))
            command = _CONVERGENCE._bounded_disposition_text(evidence.get("command", ""))
            output = _CONVERGENCE._bounded_disposition_text(evidence.get("output", ""))
        else:
            evidence_pointer = _CONVERGENCE._bounded_disposition_text(receipt.tracking_issue or "")
            command = output = ""
        item = {
            "finding_id": target,
            "receipt": _CONVERGENCE.disposition_receipt_artifact_name(receipt),
            "disposition_claim": receipt.disposition,
            "triggering_actor": _CONVERGENCE._bounded_disposition_text(receipt.approver),
            "triggering_actor_id": receipt.approver_id,
            "recorded_at": receipt.approved_at,
            "reason": _CONVERGENCE._bounded_disposition_text(receipt.reason),
            "evidence_pointer": evidence_pointer,
        }
        if command:
            item["counterevidence_command"] = command
        if output:
            item["counterevidence_output"] = output
        if receipt.finding_key:
            item["finding_key"] = receipt.finding_key
        recorded.append(item)
        resolved_item = {
            "finding_id": target,
            "receipt": item["receipt"],
            "approver": _CONVERGENCE._bounded_disposition_text(receipt.approver),
            "approver_id": receipt.approver_id,
            "approved_at": receipt.approved_at,
            "reason": item["reason"],
            "disposition": receipt.disposition,
            "evidence_pointer": evidence_pointer,
            **({"counterevidence_command": command} if command else {}),
            **({"counterevidence_output": output} if output else {}),
            **({"finding_key": receipt.finding_key} if receipt.finding_key else {}),
        }
        inherited_status = next((
            status for status in getattr(audit, "statuses", ())
            if status.receipt is receipt and status.matched_by == "inherited_rejected"
        ), None)
        if inherited_status is not None:
            resolved_item.update({
                "matched_by": "inherited_rejected",
                "receipt_head_sha": receipt.head_sha,
                "receipt_finding_id": receipt.finding_id,
            })
        resolved.append(resolved_item)
    rejected_reasons: dict[str, int] = {}
    for _receipt, reason in audit.rejected_receipts:
        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
    return {
        "recorded": recorded,
        "resolved": resolved,
        "consumed_count": len(resolved),
        "rejected_count": len(audit.rejected_receipts),
        "rejected_reasons": dict(sorted(rejected_reasons.items())),
        "fail_closed": False,
        "remaining_p1_ids": list(audit.remaining_p1_ids),
    }
