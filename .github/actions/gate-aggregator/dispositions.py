"""Neighbor matching, disposition receipt decisions, and receipt consumption."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

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

_RECEIPTS_NAME = "gate_disposition_receipts"
_RECEIPTS = sys.modules.get(_RECEIPTS_NAME)
if _RECEIPTS is None:
    _RECEIPTS_SPEC = importlib.util.spec_from_file_location(
        _RECEIPTS_NAME, Path(__file__).with_name("disposition_receipts.py")
    )
    if _RECEIPTS_SPEC is None or _RECEIPTS_SPEC.loader is None:
        raise ImportError("disposition receipt contract is unavailable")
    _RECEIPTS = importlib.util.module_from_spec(_RECEIPTS_SPEC)
    sys.modules[_RECEIPTS_NAME] = _RECEIPTS
    _RECEIPTS_SPEC.loader.exec_module(_RECEIPTS)

from gate_convergence_contract import (
    DEFERRED_RECEIPT_REJECT_REASON, DISPOSITION_KINDS, DISPOSITION_RECEIPT_SCHEMA_VERSION,
    NEIGHBOR_LINE_TOLERANCE, NEIGHBOR_POLICY_SCOPE_FIELDS, P1_SEVERITIES, SUPPORTED_TIERS,
    CanonicalPrimary, DispositionAudit, DispositionReceipt, DispositionStatus,
    NeighborEvidence, Scope, _GATE_ACTION, _approved_at_has_time,
    _current_p1_keys, _decode_canonical_finding_key, _nonempty_text, _strict_int,
    _primary_finding_record, _receipt_audit_digest_matches,
    _sha256, _stable_primary_matches, canonical_finding_key,
    counterevidence_argv_reason, narrow_stable_key_matches_by_id, neighbor_finding_key,
)
from gate_disposition_receipts import _counterevidence_reason, _tracking_issue_reason


def _scope_errors(scope: Scope) -> list[str]:
    errors: list[str] = []
    for name in ("repository_id", "pr_number"):
        value = getattr(scope, name)
        if not _strict_int(value) or value <= 0:
            errors.append(f"{name} must be a positive int")
    for name in (
        "base_sha",
        "head_sha",
        "diff_digest",
        "policy_version",
        "policy_digest",
        "caller_sha",
        "reusable_workflow_sha",
    ):
        if not _nonempty_text(getattr(scope, name)):
            errors.append(f"{name} must be a non-empty string")
    if scope.tier not in SUPPORTED_TIERS:
        errors.append(f"unknown tier {scope.tier!r}")
    return errors


def _neighbor_primary_matches(
    anchor_file: str,
    anchor_line: int,
    primary: CanonicalPrimary,
) -> list[tuple[str, dict[str, Any]]]:
    matches: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(primary.p1_findings, tuple):
        return matches
    for finding in primary.p1_findings:
        record = _primary_finding_record(finding)
        if record is None or len(finding) != 6:
            continue
        key = neighbor_finding_key(record)
        if key is None:
            continue
        if key[0] == anchor_file and abs(key[1] - anchor_line) <= NEIGHBOR_LINE_TOLERANCE:
            matches.append((record["id"], record))
    return matches


def _actionable_disposition_reason(
    code: str,
    receipt: DispositionReceipt,
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
) -> str:
    """Explain a stale target with the values needed for the next action."""

    target = receipt.finding_key or receipt.finding_id
    if code == "head_sha_mismatch":
        return (
            f"{code}: finding key {target!r} was registered for head_sha {receipt.head_sha!r}, "
            f"but the current head_sha is {scope.head_sha!r}; register this key again for the new head."
        )
    current_keys = json.dumps(list(_current_p1_keys(primary)), ensure_ascii=False, separators=(",", ":"))
    if code == "epoch_mismatch_stale":
        return (
            f"{code}: finding key {target!r} was registered for head_sha {receipt.head_sha!r}, "
            f"but the current head_sha is {scope.head_sha!r}; the code changed, so register this "
            f"key again for the new head. Current P1 keys: {current_keys}"
        )
    if code == "audit_digest_mismatch":
        return (
            f"{code}: finding set (or its verdict/scope) differs from the registered audit; "
            f"finding key {target!r}, registered audit_digest {receipt.audit_digest!r}, current "
            f"audit_digest {audit_digest!r}, current head_sha {scope.head_sha!r}. Re-register after "
            f"checking the changed finding set. Current P1 keys: {current_keys}"
        )
    if code == "finding_not_current_p1":
        return (
            f"{code}: finding key {target!r} is absent from the current P1 set or is no longer P1; "
            f"current head_sha {scope.head_sha!r}. Register only a current P1 key. Current P1 keys: "
            f"{current_keys}"
        )
    return code


def _disposition_status(
    receipt: DispositionReceipt,
    *,
    valid: bool,
    active: bool,
    reason: str,
    message: str | None = None,
    matched_by: str = "",
    audit_digest_drift: bool = False,
    matched_finding_id: str = "",
) -> DispositionStatus:
    return DispositionStatus(
        receipt=receipt,
        valid=valid,
        active=active,
        reason=reason if message is None else message,
        reason_code=reason,
        matched_by=matched_by,
        audit_digest_drift=audit_digest_drift,
        matched_finding_id=matched_finding_id,
    )


def _ambiguous_finding_key_status(
    receipt: DispositionReceipt,
    *,
    finding_key: str,
    matches: list[tuple[str, dict[str, Any]]],
    scope: Scope,
    primary: CanonicalPrimary,
) -> DispositionStatus | None:
    if len(matches) <= 1:
        return None
    return _disposition_status(
        receipt, valid=False, active=False,
        reason="finding_key_ambiguous",
        message=(
            f"finding_key_ambiguous: finding key {finding_key!r} matches "
            f"{len(matches)} current P1 findings and the receipt finding_id does not "
            f"resolve the collision, so the disposition target cannot be determined; "
            f"current head_sha {scope.head_sha!r}. Register only after the "
            f"duplicate key is resolved. Current P1 keys: "
            f"{json.dumps(list(_current_p1_keys(primary)), ensure_ascii=False, separators=(",", ":"))}"
        ),
    )


def _argv_matches_except_head(left: Any, right: Any) -> bool:
    """True when allowlisted git-grep argv pairs differ only at head (index 6)."""

    if not isinstance(left, list) or not isinstance(right, list):
        return False
    if len(left) != 9 or len(right) != 9:
        return False
    return left[:6] == right[:6] and left[7:] == right[7:]


def _counterevidence_expired(receipt: DispositionReceipt) -> DispositionStatus:
    return _disposition_status(
        receipt, valid=False, active=False, reason="counterevidence_expired",
    )


def _neighbor_epoch_mismatch(receipt: DispositionReceipt, evidence: NeighborEvidence, scope: Scope) -> bool:
    receipt_scope = evidence.receipt_scope
    if not isinstance(receipt_scope, Scope) or _scope_errors(receipt_scope):
        return True
    if _sha256(receipt_scope.as_dict()) != receipt.epoch:
        return True
    if receipt_scope.head_sha != receipt.head_sha:
        return True
    return any(
        getattr(receipt_scope, field) != getattr(scope, field)
        for field in NEIGHBOR_POLICY_SCOPE_FIELDS
    )


def _neighbor_evidence_for(
    receipt: DispositionReceipt,
    neighbor_evidence: Mapping[str, NeighborEvidence] | None,
) -> NeighborEvidence | DispositionStatus | None:
    if (
        receipt.disposition != "false-positive"
        or not receipt.finding_key
        or neighbor_evidence is None
        or receipt.finding_key not in neighbor_evidence
    ):
        return None
    evidence = neighbor_evidence[receipt.finding_key]
    if not isinstance(evidence, NeighborEvidence):
        return _counterevidence_expired(receipt)
    return evidence


def _consume_neighbor_disposition(
    receipt: DispositionReceipt,
    *,
    evidence: NeighborEvidence,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    digest_ok: bool,
) -> DispositionStatus:
    if isinstance(receipt.counterevidence, dict):
        if receipt.counterevidence.get("match_expectation", "present") != "present":
            return _counterevidence_expired(receipt)
    if evidence.current_head_sha != scope.head_sha:
        return _counterevidence_expired(receipt)
    if evidence.anchor_window_touched:
        return _counterevidence_expired(receipt)
    rerun_argv = evidence.rerun_argv if isinstance(evidence.rerun_argv, list) else []
    if (
        counterevidence_argv_reason(rerun_argv, scope.head_sha) is not None
        or type(evidence.rerun_exit_code) is not int
        or evidence.rerun_exit_code != 0
        or type(evidence.rerun_match_count) is not int
        or evidence.rerun_match_count < 1
    ):
        return _counterevidence_expired(receipt)
    receipt_argv: Any = None
    if isinstance(receipt.counterevidence, dict):
        rerun = receipt.counterevidence.get("gate_rerun")
        if isinstance(rerun, dict):
            receipt_argv = rerun.get("argv")
    if not _argv_matches_except_head(rerun_argv, receipt_argv):
        return _counterevidence_expired(receipt)
    decoded = _decode_canonical_finding_key(receipt.finding_key)
    if decoded is None or neighbor_finding_key(decoded) is None:
        matches: list[tuple[str, dict[str, Any]]] = []
    else:
        matches = _neighbor_primary_matches(
            decoded["file"], evidence.anchor_line_current, primary,
        )
    if len(matches) == 0:
        return _disposition_status(
            receipt, valid=False, active=False,
            reason="finding_not_current_p1",
            message=_actionable_disposition_reason(
                "finding_not_current_p1", receipt, scope=scope, primary=primary,
                audit_digest=audit_digest,
            ),
        )
    if len(matches) >= 2:
        return _disposition_status(
            receipt, valid=False, active=False,
            reason="finding_key_neighbor_ambiguous",
            message=(
                f"finding_key_neighbor_ambiguous: finding key {receipt.finding_key!r} "
                f"matches {len(matches)} current P1 findings within neighbor tolerance, "
                f"so the disposition target cannot be determined; current head_sha "
                f"{scope.head_sha!r}."
            ),
        )
    match_id, finding = matches[0]
    if finding.get("trigger_kind") != "inferred":
        return _disposition_status(
            receipt, valid=False, active=False,
            reason="finding_trigger_not_inferred",
        )
    return _disposition_status(
        receipt, valid=True, active=True,
        reason=f"active_{receipt.disposition.replace('-', '_')}",
        matched_by="neighbor",
        audit_digest_drift=not digest_ok,
        matched_finding_id=match_id,
    )


def _legacy_disposition_stub(receipt: DispositionReceipt) -> bool:
    return (
        isinstance(receipt, DispositionReceipt)
        and receipt.schema_version == DISPOSITION_RECEIPT_SCHEMA_VERSION
        and receipt.disposition == "none"
        and receipt.repository_id == ""
        and receipt.pr_number == 0
        and receipt.epoch == ""
        and receipt.head_sha == ""
        and receipt.audit_digest == ""
        and receipt.finding_id == ""
        and receipt.reason == ""
        and receipt.approver == ""
        and receipt.approver_id == 0
        and receipt.approved_at == ""
    )


def validate_disposition_receipt(
    receipt: DispositionReceipt,
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    legacy_raw_audit_digest: str | None = None,
    repository: str | None = None,
    neighbor_evidence: Mapping[str, NeighborEvidence] | None = None,
    gate_actions: Mapping[str, str] | None = None,
    inherited_rejection_matches: Mapping[tuple[str, str, str], str] | None = None,
) -> DispositionStatus:
    """Validate one receipt against the current canonical audit round.

    ``gate_actions`` carries the blocking-matrix-v1 projection (finding_id →
    gate_action) for audits that have ``blocking_matrix``; it is None for
    legacy audits, where deferred receipts stay rejected unconditionally.
    """

    if not isinstance(receipt, DispositionReceipt):
        return _disposition_status(
            DispositionReceipt(), valid=False, active=False,
            reason="malformed_receipt",
        )
    if _legacy_disposition_stub(receipt):
        return _disposition_status(
            receipt, valid=False, active=False,
            reason="absent_legacy_stub",
        )
    if not isinstance(scope, Scope) or _scope_errors(scope):
        return _disposition_status(receipt, valid=False, active=False, reason="malformed_scope")
    if not isinstance(primary, CanonicalPrimary):
        return _disposition_status(receipt, valid=False, active=False, reason="malformed_primary")
    if receipt.schema_version != DISPOSITION_RECEIPT_SCHEMA_VERSION:
        return _disposition_status(receipt, valid=False, active=False, reason="schema_version_mismatch")
    if receipt.disposition not in DISPOSITION_KINDS:
        return _disposition_status(receipt, valid=False, active=False, reason="unknown_disposition")
    if receipt.disposition == "false-positive":
        evidence_reason = _counterevidence_reason(receipt)
        if evidence_reason is not None:
            message = None
            if evidence_reason == "counterevidence_command_not_allowlisted":
                rerun = (
                    receipt.counterevidence.get("gate_rerun")
                    if isinstance(receipt.counterevidence, dict) else None
                )
                argv = rerun.get("argv") if isinstance(rerun, dict) else []
                message = counterevidence_argv_reason(
                    argv if isinstance(argv, list) else [], receipt.head_sha,
                )
            return _disposition_status(
                receipt, valid=False, active=False,
                reason=evidence_reason, message=message,
            )
    elif receipt.disposition == "deferred":
        tracking_reason = _tracking_issue_reason(receipt.tracking_issue, repository)
        if tracking_reason is not None:
            return _disposition_status(receipt, valid=False, active=False, reason=tracking_reason)
        if not _GATE_ACTION.deferred_release_allowed(receipt, primary=primary, gate_actions=gate_actions):
            return _disposition_status(
                receipt, valid=False, active=False,
                reason=DEFERRED_RECEIPT_REJECT_REASON,
            )
    target_field = "finding_key" if receipt.finding_key else "finding_id"
    required_text = ("repository_id", "epoch", "head_sha", "audit_digest", target_field, "reason")
    if any(not _nonempty_text(getattr(receipt, field)) for field in required_text):
        return _disposition_status(receipt, valid=False, active=False, reason="malformed_receipt")
    if not isinstance(receipt.approver, str) or not receipt.approver.strip():
        return _disposition_status(receipt, valid=False, active=False, reason="malformed_receipt")
    if type(receipt.approver_id) is not int or receipt.approver_id <= 0:
        return _disposition_status(receipt, valid=False, active=False, reason="malformed_receipt")
    if not _approved_at_has_time(receipt.approved_at):
        return _disposition_status(receipt, valid=False, active=False, reason="malformed_receipt")
    if type(receipt.pr_number) is not int or receipt.pr_number <= 0:
        return _disposition_status(receipt, valid=False, active=False, reason="malformed_pr_number")
    if receipt.repository_id != str(scope.repository_id):
        return _disposition_status(receipt, valid=False, active=False, reason="repository_mismatch")
    if receipt.pr_number != scope.pr_number:
        return _disposition_status(receipt, valid=False, active=False, reason="pr_mismatch")
    head_ok = receipt.head_sha == scope.head_sha
    expected_epoch = _sha256(scope.as_dict())
    epoch_ok = receipt.epoch == expected_epoch
    digest_ok = _receipt_audit_digest_matches(
        receipt.audit_digest, audit_digest, legacy_raw_audit_digest,
    )
    evidence = _neighbor_evidence_for(receipt, neighbor_evidence)
    if isinstance(evidence, DispositionStatus):
        return evidence
    inherited_finding_id = ""
    if head_ok and epoch_ok and digest_ok:
        pass
    elif (
        receipt.disposition == "rejected"
        and isinstance(receipt.finding_key, str)
        and inherited_rejection_matches is not None
    ):
        inherited_finding_id = inherited_rejection_matches.get(
            (receipt.head_sha, receipt.finding_key, receipt.finding_id), "",
        )
        if not inherited_finding_id:
            if not head_ok:
                return _disposition_status(
                    receipt, valid=False, active=False, reason="head_sha_mismatch",
                    message=_actionable_disposition_reason(
                        "head_sha_mismatch", receipt, scope=scope, primary=primary,
                        audit_digest=audit_digest,
                    ),
                )
            if not epoch_ok:
                return _disposition_status(
                    receipt, valid=False, active=False, reason="epoch_mismatch_stale",
                    message=_actionable_disposition_reason(
                        "epoch_mismatch_stale", receipt, scope=scope, primary=primary,
                        audit_digest=audit_digest,
                    ),
                )
            return _disposition_status(
                receipt, valid=False, active=False, reason="audit_digest_mismatch",
                message=_actionable_disposition_reason(
                    "audit_digest_mismatch", receipt, scope=scope, primary=primary,
                    audit_digest=audit_digest,
                ),
            )
    elif evidence is not None:
        if _neighbor_epoch_mismatch(receipt, evidence, scope):
            return _disposition_status(
                receipt, valid=False, active=False, reason="epoch_mismatch_stale",
                message=_actionable_disposition_reason(
                    "epoch_mismatch_stale", receipt, scope=scope, primary=primary,
                    audit_digest=audit_digest,
                ),
            )
        return _consume_neighbor_disposition(
            receipt, evidence=evidence, scope=scope, primary=primary,
            audit_digest=audit_digest, digest_ok=digest_ok,
        )
    elif not head_ok:
        return _disposition_status(
            receipt, valid=False, active=False, reason="head_sha_mismatch",
            message=_actionable_disposition_reason(
                "head_sha_mismatch", receipt, scope=scope, primary=primary,
                audit_digest=audit_digest,
            ),
        )
    elif not epoch_ok:
        return _disposition_status(
            receipt, valid=False, active=False, reason="epoch_mismatch_stale",
            message=_actionable_disposition_reason(
                "epoch_mismatch_stale", receipt, scope=scope, primary=primary,
                audit_digest=audit_digest,
            ),
        )
    else:
        return _disposition_status(
            receipt, valid=False, active=False, reason="audit_digest_mismatch",
            message=_actionable_disposition_reason(
                "audit_digest_mismatch", receipt, scope=scope, primary=primary,
                audit_digest=audit_digest,
            ),
        )

    if receipt.finding_key:
        matches = narrow_stable_key_matches_by_id(
            _stable_primary_matches(receipt.finding_key, primary, gate_actions), receipt.finding_id,
        )
        ambiguous = _ambiguous_finding_key_status(
            receipt, finding_key=receipt.finding_key, matches=matches,
            scope=scope, primary=primary,
        )
        if ambiguous is not None:
            return ambiguous
        if len(matches) == 0:
            return _disposition_status(
                receipt, valid=False, active=False,
                reason="finding_not_current_p1",
                message=_actionable_disposition_reason(
                    "finding_not_current_p1", receipt, scope=scope, primary=primary,
                    audit_digest=audit_digest,
                ),
            )
        _, finding = matches[0]
    else:
        # Expand-then-contract for gate#150: an in-flight v2 receipt has no
        # finding_key and remains bound to its legacy finding_id.  Remove this
        # branch after the v2 artifact TTL has elapsed and all v2 receipts have
        # been reissued with finding_key.
        if receipt.finding_id in {"*", "all"} or any(character in receipt.finding_id for character in "?[]"):
            return _disposition_status(receipt, valid=False, active=False, reason="finding_target_not_exact")
        if receipt.finding_id not in primary.p1_ids:
            return _disposition_status(
                receipt, valid=False, active=False,
                reason="finding_not_current_p1",
                message=_actionable_disposition_reason(
                    "finding_not_current_p1", receipt, scope=scope, primary=primary,
                    audit_digest=audit_digest,
                ),
            )
        finding = next(
            (
                item for item in primary.p1_findings
                if isinstance(item, tuple) and len(item) >= 3 and item[0] == receipt.finding_id
            ),
            None,
        ) if isinstance(primary.p1_findings, tuple) else None
        if isinstance(finding, tuple) and len(finding) == 6:
            stable_record = _primary_finding_record(finding)
            stable_key = canonical_finding_key(stable_record)
            ambiguous = _ambiguous_finding_key_status(
                receipt, finding_key=stable_key,
                matches=_stable_primary_matches(stable_key, primary, gate_actions),
                scope=scope, primary=primary,
            )
            if ambiguous is not None:
                return ambiguous

    if isinstance(finding, dict):
        finding_severity = finding.get("severity")
        finding_trigger_kind = finding.get("trigger_kind")
        resolved_id = finding.get("id")
    else:
        finding_severity = finding[1] if isinstance(finding, tuple) and len(finding) >= 2 else None
        finding_trigger_kind = finding[2] if isinstance(finding, tuple) and len(finding) >= 3 else None
        resolved_id = finding[0] if isinstance(finding, tuple) and finding else None
    if finding is None or (
        finding_severity not in P1_SEVERITIES
        and not (
            _GATE_ACTION.gate_covered(receipt.finding_id, gate_actions)
            or _GATE_ACTION.gate_covered(resolved_id, gate_actions)
        )
    ):
        return _disposition_status(
            receipt, valid=False, active=False,
            reason="finding_not_current_p1",
        )
    if receipt.disposition == "false-positive" and finding_trigger_kind != "inferred":
        return _disposition_status(
            receipt, valid=False, active=False,
            reason="finding_trigger_not_inferred",
        )
    return _disposition_status(
        receipt, valid=True, active=True,
        reason=f"active_{receipt.disposition.replace('-', '_')}",
        matched_by="inherited_rejected" if inherited_finding_id else "",
        matched_finding_id=inherited_finding_id,
    )


def disposition_status(
    receipt: DispositionReceipt,
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    legacy_raw_audit_digest: str | None = None,
    repository: str | None = None,
    neighbor_evidence: Mapping[str, NeighborEvidence] | None = None,
    gate_actions: Mapping[str, str] | None = None,
    inherited_rejection_matches: Mapping[tuple[str, str, str], str] | None = None,
) -> DispositionStatus:
    """Return the observational status view used by ledger/human summaries."""

    return validate_disposition_receipt(
        receipt,
        scope=scope,
        primary=primary,
        audit_digest=audit_digest,
        legacy_raw_audit_digest=legacy_raw_audit_digest,
        repository=repository,
        neighbor_evidence=neighbor_evidence,
        gate_actions=gate_actions,
        inherited_rejection_matches=inherited_rejection_matches,
    )


def record_dispositions(
    p1_ids: Sequence[str],
    receipts: Sequence[DispositionReceipt],
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    legacy_raw_audit_digest: str | None = None,
    repository: str | None = None,
    neighbor_evidence: Mapping[str, NeighborEvidence] | None = None,
    gate_actions: Mapping[str, str] | None = None,
    inherited_rejection_matches: Mapping[tuple[str, str, str], str] | None = None,
) -> DispositionAudit:
    """Validate receipts and return the current P1s they cover exactly."""

    primary_ids = tuple(p1_ids) if isinstance(p1_ids, Sequence) and not isinstance(p1_ids, (str, bytes)) else ()
    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)):
        return DispositionAudit(
            primary_p1_ids=primary_ids, recorded_receipts=(), rejected_receipts=(),
            remaining_p1_ids=primary_ids,
        )
    statuses: list[DispositionStatus] = []
    recorded: list[DispositionReceipt] = []
    recorded_finding_ids: list[str] = []
    rejected: list[tuple[DispositionReceipt, str]] = []
    seen_payloads: set[str] = set()
    for receipt in receipts:
        if not isinstance(receipt, DispositionReceipt):
            status = validate_disposition_receipt(
                receipt, scope=scope, primary=primary, audit_digest=audit_digest,
                legacy_raw_audit_digest=legacy_raw_audit_digest,
                repository=repository,
                neighbor_evidence=neighbor_evidence,
                gate_actions=gate_actions,
                inherited_rejection_matches=inherited_rejection_matches,
            )
            statuses.append(status)
            rejected.append((status.receipt, status.reason_code))
            continue
        if _legacy_disposition_stub(receipt):
            statuses.append(_disposition_status(receipt, valid=False, active=False, reason="absent_legacy_stub"))
            continue
        status = validate_disposition_receipt(
            receipt, scope=scope, primary=primary, audit_digest=audit_digest,
            legacy_raw_audit_digest=legacy_raw_audit_digest,
            repository=repository,
            neighbor_evidence=neighbor_evidence,
            gate_actions=gate_actions,
            inherited_rejection_matches=inherited_rejection_matches,
        )
        if status.active and status.valid:
            payload_signature = json.dumps(receipt.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if payload_signature in seen_payloads:
                statuses.append(_disposition_status(receipt, valid=True, active=True, reason="duplicate_receipt_noop"))
                continue
            seen_payloads.add(payload_signature)
        statuses.append(status)
        if status.active and status.valid:
            target_id = receipt.finding_id
            if status.matched_finding_id:
                target_id = status.matched_finding_id
            elif receipt.finding_key:
                stable_matches = narrow_stable_key_matches_by_id(
                    _stable_primary_matches(receipt.finding_key, primary, gate_actions), receipt.finding_id,
                )
                if len(stable_matches) == 1:
                    target_id = stable_matches[0][0]
            recorded.append(receipt)
            recorded_finding_ids.append(target_id)
        elif status.reason_code != "absent_legacy_stub":
            rejected.append((receipt, status.reason_code))
    covered = set(recorded_finding_ids)
    remaining = tuple(finding_id for finding_id in primary_ids if finding_id not in covered)
    return DispositionAudit(
        primary_p1_ids=primary_ids,
        recorded_receipts=tuple(recorded),
        rejected_receipts=tuple(rejected),
        statuses=tuple(statuses),
        recorded_finding_ids=tuple(recorded_finding_ids),
        remaining_p1_ids=remaining,
    )
