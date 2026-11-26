"""Pure cross-round finding relation and carry decisions."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

_MODELS_NAME = "gate_aggregate_models"
_MODELS = sys.modules.get(_MODELS_NAME)
if _MODELS is None:
    _MODELS_SPEC = importlib.util.spec_from_file_location(
        _MODELS_NAME, Path(__file__).with_name("aggregate_models.py")
    )
    assert _MODELS_SPEC and _MODELS_SPEC.loader
    _MODELS = importlib.util.module_from_spec(_MODELS_SPEC)
    sys.modules[_MODELS_NAME] = _MODELS
    _MODELS_SPEC.loader.exec_module(_MODELS)
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
_GATE_ACTION_NAME = "gate_action_from_aggregate"
_GATE_ACTION = sys.modules.get(_GATE_ACTION_NAME)
if _GATE_ACTION is None:
    _GATE_ACTION_SPEC = importlib.util.spec_from_file_location(
        _GATE_ACTION_NAME, Path(__file__).with_name("gate_action.py")
    )
    assert _GATE_ACTION_SPEC and _GATE_ACTION_SPEC.loader
    _GATE_ACTION = importlib.util.module_from_spec(_GATE_ACTION_SPEC)
    sys.modules[_GATE_ACTION_NAME] = _GATE_ACTION
    _GATE_ACTION_SPEC.loader.exec_module(_GATE_ACTION)

from gate_aggregate_models import Outcome, _is_strict_int  # noqa: E402

RELATION_VALUES = frozenset({"new", "repeat", "conflict"})

RELATION_DEGRADED = "GATE-FINDING-RELATION-DEGRADED"

RELATION_UNKNOWN = "GATE-FINDING-RELATION-UNKNOWN"

PREVIOUS_FINDING_LIMIT = 30

FINDING_TEXT_MAX = 240

_P1_RELATION_SEVERITIES = frozenset({"major", "blocker"})

_LEDGER_LATEST_PREFIX = "codex-review-ledger-latest-v1-"

_LEDGER_LATEST_OBJECT = "ledger.jsonl"

_LATEST_LEDGER_DEGRADE = frozenset({
    "silo-ledger-latest-absent",
    "silo-ledger-latest-not-prior",
    "silo-ledger-latest-unreadable",
})

class FindingRelationError(ValueError):
    pass

def _degrade_previous_round(detail: str) -> dict[str, Any]:
    print(f"{RELATION_DEGRADED}: source=previous-ledger detail={detail}")
    return {"available": False, "detail": detail, "head_sha": None, "findings": []}

def _norm_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()

def _clip_text(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    if len(text) <= FINDING_TEXT_MAX:
        return text
    return text[: FINDING_TEXT_MAX - 1] + "…"

def _require_relation(value: Any) -> str:
    if value not in RELATION_VALUES:
        raise FindingRelationError(f"{RELATION_UNKNOWN}: {value!r}")
    return str(value)

def project_finding(finding: dict[str, Any]) -> dict[str, Any]:
    line = finding.get("line")
    return {
        "id": finding.get("id") if isinstance(finding.get("id"), str) else "",
        "file": finding.get("file") if isinstance(finding.get("file"), str) else "",
        "line": line if type(line) is int else None,
        "severity": finding.get("severity") if isinstance(finding.get("severity"), str) else "",
        "category": finding.get("category") if isinstance(finding.get("category"), str) else "",
        "issue": _clip_text(finding.get("issue")),
        "acceptance": _clip_text(finding.get("acceptance")),
    }

def _is_location_conflict(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    if current["id"] and current["id"] == previous.get("id"):
        return False
    if not current["file"] or current["file"] != previous.get("file"):
        return False
    if type(current["line"]) is not int or current["line"] != previous.get("line"):
        return False
    if current["severity"] not in _P1_RELATION_SEVERITIES or previous.get("severity") not in _P1_RELATION_SEVERITIES:
        return False
    issue_current, issue_previous = _norm_text(current.get("issue")), _norm_text(previous.get("issue"))
    acceptance_current = _norm_text(current.get("acceptance"))
    acceptance_previous = _norm_text(previous.get("acceptance"))
    if not issue_current or not issue_previous or not acceptance_current or not acceptance_previous:
        return False
    return issue_current != issue_previous and acceptance_current != acceptance_previous

def relate_findings(current: list[Any], previous: list[Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in previous:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            by_id.setdefault(item["id"], item)
    preset = [isinstance(item, dict) and "relation_to_previous" in item for item in current if isinstance(item, dict)]
    if any(preset) and not all(preset):
        raise FindingRelationError(f"{RELATION_UNKNOWN}: partial relation_to_previous")
    labeled: list[dict[str, Any]] = []
    for finding in current:
        if not isinstance(finding, dict):
            continue
        projected = project_finding(finding)
        if "relation_to_previous" in finding:
            relation = _require_relation(finding.get("relation_to_previous"))
            pointer = finding.get("previous_finding_id")
            if relation != "new" and (not isinstance(pointer, str) or not pointer):
                raise FindingRelationError(f"{RELATION_UNKNOWN}: {relation} missing previous_finding_id")
            if relation == "new" and pointer not in (None, ""):
                raise FindingRelationError(f"{RELATION_UNKNOWN}: new must not carry previous_finding_id")
            projected["relation_to_previous"] = relation
            if relation != "new":
                projected["previous_finding_id"] = pointer
            labeled.append(projected)
            continue
        if projected["id"] and projected["id"] in by_id:
            projected["relation_to_previous"] = "repeat"
            projected["previous_finding_id"] = projected["id"]
            labeled.append(projected)
            continue
        sol_key_matches = [
            item for item in previous
            if isinstance(item, dict)
            and item.get("origin") == "sol_verify"
            and finding.get("origin") == "sol_verify"
            and _GATE_ACTION.canonical_finding_key(item) == _GATE_ACTION.canonical_finding_key(finding)
        ]
        if len(sol_key_matches) == 1 and isinstance(sol_key_matches[0].get("id"), str) and sol_key_matches[0]["id"]:
            projected["relation_to_previous"] = "repeat"
            projected["previous_finding_id"] = sol_key_matches[0]["id"]
            labeled.append(projected)
            continue
        matches = [item for item in previous if isinstance(item, dict) and _is_location_conflict(projected, project_finding(item))]
        pointer = matches[0].get("id") if len(matches) == 1 else ""
        if isinstance(pointer, str) and pointer:
            projected["relation_to_previous"] = "conflict"
            projected["previous_finding_id"] = pointer
        else:
            projected["relation_to_previous"] = "new"
        labeled.append(projected)
    return labeled

def build_finding_relation(findings: list[Any], previous_round: dict[str, Any]) -> dict[str, Any]:
    previous: list[Any] = []
    open_findings: list[dict[str, Any]] = []
    if previous_round.get("available") is True:
        raw = previous_round.get("findings")
        if not isinstance(raw, list):
            raise FindingRelationError(f"{RELATION_UNKNOWN}: previous findings are not a list")
        previous = raw
        positions: dict[str, int] = {}
        for finding in previous:
            if not isinstance(finding, dict):
                continue
            projected = dict(finding)
            finding_id = projected.get("id")
            if isinstance(finding_id, str) and finding_id:
                if finding_id in positions:
                    open_findings[positions[finding_id]].update(projected)
                    continue
                positions[finding_id] = len(open_findings)
            open_findings.append(projected)
    else:
        positions = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        projected = dict(finding)
        finding_id = projected.get("id")
        if isinstance(finding_id, str) and finding_id in positions:
            open_findings[positions[finding_id]].update(projected)
            continue
        if isinstance(finding_id, str) and finding_id:
            positions[finding_id] = len(open_findings)
        open_findings.append(projected)
    items = relate_findings(findings, previous)
    counts = {name: sum(item["relation_to_previous"] == name for item in items) for name in ("new", "repeat", "conflict")}
    return {
        "schema_version": 1,
        "counts": counts,
        "review_terminal": "manual_required" if counts["conflict"] else "unchanged",
        "items": items,
        "open_findings": open_findings,
    }

def validate_finding_relation_block(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict) or block.get("schema_version") != 1:
        raise FindingRelationError(f"{RELATION_UNKNOWN}: block schema")
    counts = block.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(RELATION_VALUES):
        raise FindingRelationError(f"{RELATION_UNKNOWN}: counts")
    for name in RELATION_VALUES:
        if not _is_strict_int(counts[name]) or counts[name] < 0:
            raise FindingRelationError(f"{RELATION_UNKNOWN}: count {name}")
    items = block.get("items")
    if not isinstance(items, list) or sum(counts[name] for name in RELATION_VALUES) != len(items):
        raise FindingRelationError(f"{RELATION_UNKNOWN}: counts do not match items")
    for item in items:
        if not isinstance(item, dict):
            raise FindingRelationError(f"{RELATION_UNKNOWN}: item")
        relation = _require_relation(item.get("relation_to_previous"))
        pointer = item.get("previous_finding_id")
        if relation != "new" and (not isinstance(pointer, str) or not pointer):
            raise FindingRelationError(f"{RELATION_UNKNOWN}: {relation} missing previous_finding_id")
    if "open_findings" in block and (
        not isinstance(block["open_findings"], list)
        or any(not isinstance(item, dict) for item in block["open_findings"])
    ):
        raise FindingRelationError(f"{RELATION_UNKNOWN}: open_findings")
    history_status = block.get("history_status")
    if history_status is not None and history_status not in {"available", "unknown"}:
        raise FindingRelationError(f"{RELATION_UNKNOWN}: history_status")
    terminal = block.get("review_terminal")
    if terminal not in {"unchanged", "manual_required"}:
        raise FindingRelationError(f"{RELATION_UNKNOWN}: review_terminal {terminal!r}")
    if (counts["conflict"] > 0) != (terminal == "manual_required"):
        raise FindingRelationError(f"{RELATION_UNKNOWN}: review_terminal does not match conflict count")
    return block

def apply_finding_relation(outcome: Outcome, findings: list[Any], previous_round: dict[str, Any]) -> None:
    block = validate_finding_relation_block(build_finding_relation(findings, previous_round))
    outcome.finding_relation = block
    if len(findings) == len(block["items"]):
        for finding, item in zip(findings, block["items"]):
            if not isinstance(finding, dict):
                continue
            finding["relation_to_previous"] = item["relation_to_previous"]
            finding.pop("previous_finding_id", None)
            if item["relation_to_previous"] != "new":
                finding["previous_finding_id"] = item["previous_finding_id"]
    if block["counts"]["conflict"] and outcome.gate_result == "pass":
        outcome.ok = False
        outcome.gate_result = "fail"
        outcome.classification = "code_fail"
        outcome.reason_code = "primary_findings"
        outcome.problems.append("cross-round finding conflict requires manual review (manual_required)")

def _consume_explicit_withdrawals(
    relation: dict[str, Any], previous_round: dict[str, Any], withdrawals: Any,
) -> dict[str, Any]:
    """Close prior open findings only for valid, reasoned audit withdrawals."""
    if withdrawals is None or withdrawals == []:
        return relation
    if not isinstance(withdrawals, list):
        print("GATE-WITHDRAWAL-IGNORED: invalid explicit withdrawal record")
        return relation
    valid_ids: set[str] = set()
    for item in withdrawals:
        if (
            not isinstance(item, dict)
            or set(item) != {"finding_id", "reason"}
            or not isinstance(item.get("finding_id"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", item["finding_id"])
            or not isinstance(item.get("reason"), str)
            or not item["reason"].strip()
        ):
            print("GATE-WITHDRAWAL-IGNORED: invalid explicit withdrawal record")
            return relation
        valid_ids.add(item["finding_id"])
    if previous_round.get("available") is not True:
        return relation
    previous_findings = previous_round.get("findings")
    if not isinstance(previous_findings, list):
        return relation
    prior_ids = {
        item["id"] for item in previous_findings
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    reported_prior_ids = {
        pointer
        for item in relation.get("items", [])
        if isinstance(item, dict)
        for pointer in (item.get("id"), item.get("previous_finding_id"))
        if isinstance(pointer, str)
    }
    closable_ids = (valid_ids & prior_ids) - reported_prior_ids
    open_findings = relation.get("open_findings")
    if not closable_ids or not isinstance(open_findings, list):
        return relation
    return {
        **relation,
        "open_findings": [
            item for item in open_findings
            if not isinstance(item, dict) or item.get("id") not in closable_ids
        ],
    }

def _close_consumed_false_positive_carry(
    relation: dict[str, Any], findings: list[Any], disposition_audit: Any,
) -> dict[str, Any]:
    """Close only current open carry entries consumed by a valid refuted receipt."""
    open_findings = relation.get("open_findings")
    if not isinstance(open_findings, list):
        return relation
    if not isinstance(disposition_audit, _CONVERGENCE.DispositionAudit):
        return relation
    recorded_receipts = disposition_audit.recorded_receipts
    recorded_finding_ids = disposition_audit.recorded_finding_ids
    if len(recorded_receipts) != len(recorded_finding_ids):
        return relation

    current_keys_by_id: dict[str, list[str]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            continue
        if finding.get("severity") not in _CONVERGENCE.P1_SEVERITIES:
            continue
        current_keys_by_id.setdefault(finding_id, []).append(_CONVERGENCE.canonical_finding_key(finding))

    closed: set[tuple[str, str]] = set()
    for receipt, finding_id in zip(recorded_receipts, recorded_finding_ids):
        if receipt.disposition != "false-positive" or _CONVERGENCE._counterevidence_reason(receipt):
            continue
        current_keys = current_keys_by_id.get(finding_id, [])
        if len(current_keys) == 1:
            closed.add((finding_id, current_keys[0]))
    if not closed:
        return relation
    return {
        **relation,
        "open_findings": [finding for finding in open_findings if (
            not isinstance(finding, dict)
            or (finding.get("id"), _CONVERGENCE.canonical_finding_key(finding)) not in closed
        )],
    }
