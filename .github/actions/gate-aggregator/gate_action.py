#!/usr/bin/env python3
"""Finding-level blocking-matrix contract v1 (gate-hub producer ⇄ gate
aggregator): severity→gate_action matrix, receipt coverage rules, and the
canonical finding-key helpers they share with convergence.py.

stdlib-only, no intra-repo imports: convergence.py and aggregate.py load
this file via importlib and may alias names back into their own namespaces
for compatibility. Split out of aggregate.py/convergence.py per the
file-size ratchet (card dlg-20261120-180704-d7a06b scope-request seq=2/3).
"""
from __future__ import annotations

import json
from dataclasses import replace as _dataclass_replace
from typing import Any, Mapping

P1_SEVERITIES = frozenset({"major", "blocker"})
KNOWN_SEVERITIES = frozenset({"major", "blocker", "minor", "nit"})
SUPPORTED_TIERS = ("personal", "internal", "saas")
# gate_action values a receipt may release. "comment" findings never need a
# receipt; "block" accepts only false-positive; "must_respond" accepts
# false-positive or deferred (with a same-repo tracking issue).
COVERING_GATE_ACTIONS = frozenset({"block", "must_respond"})
GATE_ACTION_VALUES = frozenset({"block", "must_respond", "comment"})
GATE_ACTION_ISSUANCE_ERROR = (
    "finding gate_action must be block or must_respond under blocking_matrix v1"
)

CANONICAL_JSON_SEPARATORS = (",", ":")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=CANONICAL_JSON_SEPARATORS,
    ).encode("utf-8")


CANONICAL_FINDING_KEY_FIELDS = ("file", "line", "category", "severity")


def canonical_finding_key(finding: Any) -> str:
    """Return the sole canonical key for a finding disposition target.

    The key contains exactly ``file``, ``line``, ``category``, and
    ``severity`` in that order.  A missing field is encoded as the tagged
    value ``["missing"]``; an explicit JSON ``null`` is ``["null"]``; every
    other value, including the empty string, is encoded as ``["value", V]``.
    Consequently missing, ``None``, and ``""`` never collapse to one key.
    The compact JSON representation is deliberate: file paths may contain
    slashes and delimiter characters, so artifact names must hash this full
    key instead of embedding or reparsing it.
    """

    if not isinstance(finding, dict):
        raise ValueError("canonical finding key requires an object")
    components: dict[str, list[Any]] = {}
    for field in CANONICAL_FINDING_KEY_FIELDS:
        if field not in finding:
            components[field] = ["missing"]
        elif finding[field] is None:
            components[field] = ["null"]
        else:
            components[field] = ["value", finding[field]]
    return canonical_json(components).decode("utf-8")


def primary_finding_record(finding: Any) -> dict[str, Any] | None:
    """Normalize one primary tuple without reimplementing key serialization."""

    if not isinstance(finding, tuple):
        return None
    # Expand-then-contract: canonical_primary receipts already published in
    # the v2 artifact window may still carry the legacy three-field shape.
    # TODO(gate#150): remove this branch after no retained canonical_primary
    # receipt can be read back with a three-field p1_findings projection.
    if len(finding) == 3:
        return {
            "id": finding[0],
            "severity": finding[1],
            "trigger_kind": finding[2],
        }
    if len(finding) == 6:
        return {
            "id": finding[0],
            "severity": finding[1],
            "trigger_kind": finding[2],
            "file": finding[3],
            "line": finding[4],
            "category": finding[5],
        }
    return None


def stable_primary_matches(
    finding_key: str,
    primary: Any,
    gate_actions: Mapping[str, str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Current findings whose canonical key equals ``finding_key``.

    ``gate_actions`` (blocking-matrix-v1 projection, finding_id →
    gate_action) widens the P1-only severity filter: a coverable finding is
    matched even when its severity is not a P1 severity.
    """
    matches: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(primary.p1_findings, tuple):
        return matches
    for finding in primary.p1_findings:
        record = primary_finding_record(finding)
        if record is None or len(finding) != 6:
            continue
        if record.get("severity") not in P1_SEVERITIES and not gate_covered(record.get("id"), gate_actions):
            continue
        if canonical_finding_key(record) == finding_key:
            matches.append((record["id"], record))
    return matches


def gate_covered(finding_id: Any, gate_actions: Mapping[str, str] | None) -> bool:
    """True when a blocking-matrix-v1 audit marks this finding coverable.

    ``gate_actions`` is None for legacy audits (no blocking_matrix): every
    caller then keeps the strict P1-only semantics — this helper never
    widens coverage on its own.
    """
    return bool(gate_actions) and gate_actions.get(finding_id) in COVERING_GATE_ACTIONS


def deferred_release_allowed(
    receipt: Any,
    *,
    primary: Any,
    gate_actions: Mapping[str, str] | None,
) -> bool:
    """v1 blocking matrix: deferred releases only a must_respond finding.

    Stable-key receipts bind finding_id to the canonical stable key, so the
    id is first resolved back to the audit finding id via the stable-key
    matches. Legacy audits (gate_actions=None) stay rejected.
    """
    if not gate_actions:
        return False
    action = gate_actions.get(receipt.finding_id)
    if action is None and receipt.finding_id:
        for match_id, _record in stable_primary_matches(receipt.finding_id, primary, gate_actions):
            action = gate_actions.get(match_id)
            if action is not None:
                break
    return action == "must_respond"


def gate_coverable(audit: dict[str, Any], finding: Any) -> bool:
    """True when a receipt may be issued/consumed against this finding.

    Legacy audits (no blocking_matrix) keep the P1-only rule.
    blocking-matrix-v1 audits accept any finding whose gate_action is
    coverable; unknown matrix versions/tiers fail closed.
    """
    matrix = audit.get("blocking_matrix")
    if not isinstance(matrix, dict):
        return finding.get("severity") in P1_SEVERITIES
    return (
        matrix.get("version") == 1
        and matrix.get("tier") in SUPPORTED_TIERS
        and finding.get("gate_action") in COVERING_GATE_ACTIONS
    )


def gate_coverable_error(audit: dict[str, Any], finding: Any) -> str | None:
    """Issuance-side error message, or None when the finding is issuable."""

    if gate_coverable(audit, finding):
        return None
    if isinstance(audit.get("blocking_matrix"), dict):
        return GATE_ACTION_ISSUANCE_ERROR
    return "finding_id must identify a P1 finding"


def coverable_matches(audit: dict[str, Any], stable_key: str) -> list[tuple[str, dict[str, Any]]]:
    """Audit findings issuable against ``stable_key`` (gate_coverable filter)."""
    return [
        (finding.get("id"), finding)
        for finding in (audit.get("result") or {}).get("findings", [])
        if isinstance(finding, dict)
        and gate_coverable(audit, finding)
        and canonical_finding_key(finding) == stable_key
    ]


def deferred_gate_rejected(audit: dict[str, Any], finding: Any) -> bool:
    """True when a deferred receipt must stay rejected for this finding
    (gate-hub#1072 ruling 4(a)): at every tier on legacy audits; a
    blocking-matrix-v1 audit allows deferred only for must_respond."""
    action = None
    matrix = audit.get("blocking_matrix")
    if isinstance(matrix, dict) and matrix.get("version") == 1 and matrix.get("tier") in SUPPORTED_TIERS:
        action = finding.get("gate_action")
    return action != "must_respond"


def validated_canonical_findings(
    audit: dict[str, Any], *, full: bool = False,
) -> list[tuple[str, str, str | None, str, int | None, str]] | None:
    """Shared structural validation for finding projections; None = malformed.

    ``full=False`` keeps the legacy P1-projection behavior of skipping
    non-P1 findings after the severity check (no id/file/line validation);
    ``full=True`` (blocking-matrix projection) validates every finding.
    """
    result = audit.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
        return None
    findings: list[dict[str, Any]] = []
    for finding in result["findings"]:
        if not isinstance(finding, dict):
            return None
        severity = finding.get("severity")
        if not isinstance(severity, str) or severity not in KNOWN_SEVERITIES:
            return None
        if not full and severity not in P1_SEVERITIES:
            continue
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            return None
        file = finding.get("file")
        if "line" not in finding:
            return None
        line = finding["line"]
        category = finding.get("category")
        if not isinstance(file, str) or (line is not None and type(line) is not int) or not isinstance(category, str):
            return None
        trigger_kind = finding.get("trigger_kind")
        if trigger_kind is not None and not isinstance(trigger_kind, str):
            trigger_kind = None
        findings.append((finding_id, severity, trigger_kind, file, line, category))
    return findings


def p1_findings(audit: dict[str, Any]) -> Any:
    """Project canonical P1 stable-key evidence (legacy aggregator rule)."""
    validated = validated_canonical_findings(audit)
    if validated is None:
        return None
    return tuple(sorted(
        (item for item in validated if item[1] in P1_SEVERITIES),
        key=lambda item: item[0],
    ))


def blocking_projection(
    audit: dict[str, Any],
) -> tuple[tuple[tuple[str, str, str | None, str, int | None, str], ...], dict[str, str], int] | None:
    """Project blocking-matrix-v1 findings that need receipt coverage.

    Returns (coverable_findings, gate_action_by_id, invalid_action_count), or
    None when the audit carries no blocking_matrix (legacy semantics apply
    unchanged). A matrix with an unsupported version/tier, or a finding whose
    gate_action is missing or outside GATE_ACTION_VALUES, is fail-closed: the
    finding is treated as "block" and surfaced via the GATE-ACTION-INVALID
    marker.
    """
    matrix = audit.get("blocking_matrix")
    if not isinstance(matrix, dict):
        return None
    validated = validated_canonical_findings(audit, full=True)
    if validated is None:
        return None
    matrix_valid = matrix.get("version") == 1 and matrix.get("tier") in SUPPORTED_TIERS
    coverable: list[tuple[str, str, str | None, str, int | None, str]] = []
    gate_actions: dict[str, str] = {}
    invalid = 0 if matrix_valid else len(validated)
    raw_findings = audit["result"]["findings"]
    for item, raw in zip(validated, raw_findings):
        finding_id = item[0]
        action = raw.get("gate_action") if matrix_valid else "block"
        if action not in GATE_ACTION_VALUES:
            invalid += 1
            action = "block"
        if action in COVERING_GATE_ACTIONS:
            gate_actions[finding_id] = action
            coverable.append(item)
    return tuple(sorted(coverable, key=lambda item: item[0])), gate_actions, invalid


def disposition_projection(audit: dict[str, Any], primary: Any) -> Any:
    """Blocking-matrix-v1 disposition projection for the aggregator hand-off.

    Returns (disposition_ids, disposition_primary, gate_actions,
    invalid_note), or None when the audit has no blocking_matrix or carries
    no findings (verdict=unavailable) — legacy semantics apply unchanged.
    The returned primary is a copy of ``primary`` widened to the coverable
    set; invalid matrix/gate_action findings fail closed to block with a
    grep-able GATE-ACTION-INVALID note.
    """
    if audit.get("verdict") == "unavailable" and audit.get("result") is None:
        return None
    projection = blocking_projection(audit)
    if projection is None:
        return None
    coverable, gate_actions, invalid = projection
    disposition_ids = tuple(item[0] for item in coverable)
    invalid_note = (
        f"GATE-ACTION-INVALID: {invalid} finding(s) had a missing or unsupported "
        "gate_action/blocking_matrix and were fail-closed to block"
    ) if invalid else None
    disposition_primary = _dataclass_replace(
        primary,
        p1_ids=disposition_ids,
        p1_findings=coverable,
    )
    return disposition_ids, disposition_primary, gate_actions, invalid_note
