"""Disposition receipt parsing and evidence syntax validation."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

_CONTRACT_NAME = "gate_convergence_contract"
_CONTRACT = sys.modules.get(_CONTRACT_NAME)
if _CONTRACT is None:
    _CONTRACT_SPEC = importlib.util.spec_from_file_location(
        _CONTRACT_NAME, Path(__file__).with_name("convergence_contract.py")
    )
    if _CONTRACT_SPEC is None or _CONTRACT_SPEC.loader is None:
        raise ImportError("convergence contract is unavailable")
    _CONTRACT = importlib.util.module_from_spec(_CONTRACT_SPEC)
    sys.modules[_CONTRACT_NAME] = _CONTRACT
    _CONTRACT_SPEC.loader.exec_module(_CONTRACT)

from gate_convergence_contract import (  # noqa: E402
    DISPOSITION_RECEIPT_KIND, DispositionReceipt, ReceiptValidationError,
    _nonempty_text, counterevidence_argv_reason,
)


def parse_disposition_receipt(payload: Any) -> DispositionReceipt:
    """Project one producer JSON object into a typed disposition receipt."""

    if not isinstance(payload, dict):
        raise ReceiptValidationError("disposition receipt payload must be an object")
    kind = payload.get("kind")
    if kind is not None and kind != DISPOSITION_RECEIPT_KIND:
        raise ReceiptValidationError("unexpected disposition receipt kind")
    try:
        return DispositionReceipt(
            schema_version=payload["schema_version"],
            disposition=payload["disposition"],
            repository_id=str(payload["repository_id"]),
            pr_number=payload["pr_number"],
            epoch=str(payload["epoch"]),
            head_sha=str(payload["head_sha"]),
            audit_digest=str(payload["audit_digest"]),
            finding_id=str(payload["finding_id"]),
            finding_key=str(payload["finding_key"]) if "finding_key" in payload else "",
            reason=str(payload["reason"]),
            approver=payload["approver"] if "approver" in payload else "",
            approver_id=payload["approver_id"] if "approver_id" in payload else 0,
            approved_at=payload["approved_at"] if "approved_at" in payload else "",
            triggering_actor=str(payload["triggering_actor"]) if "triggering_actor" in payload else "",
            triggering_actor_source=(
                str(payload["triggering_actor_source"]) if "triggering_actor_source" in payload else ""
            ),
            counterevidence=payload.get("counterevidence"),
            tracking_issue=payload.get("tracking_issue"),
        )
    except (KeyError, TypeError) as exc:
        raise ReceiptValidationError("malformed disposition receipt") from exc


def _behavior_gate_replay_reason(evidence: dict[str, Any]) -> str | None:
    name = "gate_behavior_counterevidence"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("behavior_counterevidence.py"))
        if spec is None or spec.loader is None:
            raise ImportError("behavior counterevidence contract is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.behavior_gate_replay_reason(evidence)


def _counterevidence_reason(receipt: DispositionReceipt) -> str | None:
    evidence = receipt.counterevidence
    if not isinstance(evidence, dict):
        return "counterevidence_required"
    if evidence.get("kind") == "behavior_replay":
        return _behavior_gate_replay_reason(evidence)
    fields = ("command", "output", "result", "pointer")
    if any(not isinstance(evidence.get(field), str) or not evidence[field].strip() for field in fields):
        return "counterevidence_required"
    if evidence["result"] != "refuted":
        return "counterevidence_result_not_refuted"
    rerun = evidence.get("gate_rerun")
    if not isinstance(rerun, dict):
        return "counterevidence_not_rerun"
    argv = rerun.get("argv")
    argv_reason = counterevidence_argv_reason(argv if isinstance(argv, list) else [], receipt.head_sha)
    if argv_reason is not None:
        return "counterevidence_command_not_allowlisted"
    expectation = evidence.get("match_expectation", "present")
    match_count = rerun.get("match_count")
    if expectation == "present":
        if type(rerun.get("exit_code")) is not int or rerun.get("exit_code") != 0:
            return "counterevidence_not_rerun"
        if type(match_count) is not int or match_count < 1:
            return "counterevidence_not_rerun"
        return None
    if expectation != "absent":
        return "counterevidence_not_rerun"
    base = rerun.get("base_rerun")
    base_argv = base.get("argv") if isinstance(base, dict) else None
    if (
        not isinstance(base, dict)
        or not isinstance(base_argv, list) or len(base_argv) != len(argv)
        or base_argv[:6] != argv[:6] or base_argv[7:] != argv[7:]
        or not isinstance(base_argv[6], str)
        or re.fullmatch(r"[0-9a-f]{40}", base_argv[6]) is None
    ):
        return "counterevidence_not_rerun"
    base_sha = base_argv[6]
    if counterevidence_argv_reason(base_argv, base_sha) is not None:
        return "counterevidence_command_not_allowlisted"
    if (
        type(base.get("exit_code")) is not int or base.get("exit_code") != 0
        or type(base.get("match_count")) is not int or base["match_count"] < 1
        or not _nonempty_text(base.get("excerpt"))
    ):
        return "counterevidence_not_rerun"
    if (
        type(rerun.get("exit_code")) is not int or rerun.get("exit_code") != 1
        or type(match_count) is not int or match_count != 0
        or not _nonempty_text(rerun.get("excerpt"))
    ):
        return "counterevidence_not_rerun"
    return None


def _tracking_issue_reason(value: Any, repository: str | None) -> str | None:
    if not isinstance(value, str):
        return "tracking_issue_invalid"
    if re.fullmatch(r"#[1-9][0-9]*", value):
        return None
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+)/issues/([1-9][0-9]*)", value)
    if match is None:
        return "tracking_issue_invalid"
    if repository is None or f"{match.group(1)}/{match.group(2)}".casefold() != repository.casefold():
        return "tracking_issue_repository_mismatch"
    return None
