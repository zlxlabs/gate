"""Pure canonical clean-streak convergence evaluator. Caller owns I/O; returned state is derived from immutable evidence."""
from __future__ import annotations

import hashlib
import importlib.util
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

_DISPOSITIONS_NAME = "gate_dispositions"
_DISPOSITIONS = sys.modules.get(_DISPOSITIONS_NAME)
if _DISPOSITIONS is None:
    _DISPOSITIONS_SPEC = importlib.util.spec_from_file_location(
        _DISPOSITIONS_NAME, Path(__file__).with_name("dispositions.py")
    )
    if _DISPOSITIONS_SPEC is None or _DISPOSITIONS_SPEC.loader is None:
        raise ImportError("disposition decision module is unavailable")
    _DISPOSITIONS = importlib.util.module_from_spec(_DISPOSITIONS_SPEC)
    sys.modules[_DISPOSITIONS_NAME] = _DISPOSITIONS
    _DISPOSITIONS_SPEC.loader.exec_module(_DISPOSITIONS)

from gate_convergence_contract import (
    CANONICAL_AUDIT_DIGEST_FINDING_FIELDS, CANONICAL_AUDIT_DIGEST_SCOPE_FIELDS,
    COUNTEREVIDENCE_ARGV_CONTRACT_VERSION, COUNTEREVIDENCE_ARGV_TEMPLATE,
    COUNTEREVIDENCE_RERUN_TIMEOUT_S, DEFERRED_RECEIPT_REJECT_REASON,
    DISPOSITION_KINDS, DISPOSITION_REASON_DISPLAY_MAX, DISPOSITION_RECEIPT_KIND,
    DISPOSITION_RECEIPT_SCHEMA_VERSION, GATE_ACTION_ISSUANCE_ERROR,
    KNOWN_SEVERITIES, NEIGHBOR_LINE_TOLERANCE, NEIGHBOR_POLICY_SCOPE_FIELDS,
    P1_SEVERITIES, PRIMARY_VERDICTS, RECEIPT_KIND, RECEIPT_SCHEMA_VERSION,
    SCHEMA_VERSION, SUPPORTED_TIERS, TERMINAL_DECISIONS,
    CanonicalPrimary, ConvergenceError, ConvergenceState, DispositionAudit,
    DispositionReceipt, DispositionStatus, NeighborEvidence, Policy, ProcessingKey,
    Receipt, ReceiptConflictError, ReceiptValidationError, RoundDecision, RoundKey,
    Scope, ScopeValidationError, _GATE_ACTION, _approved_at_has_time,
    _audit_digest_errors, _bounded_disposition_text, _canonical_finding_sort_key,
    _canonical_json, _current_p1_keys, _decode_canonical_finding_key,
    _nonempty_text, _primary_finding_record, _receipt_audit_digest_matches,
    _sha256, _strict_int, _stable_primary_matches,
    canonical_audit_binding, canonical_audit_digest, canonical_finding_key,
    coverable_matches, counterevidence_argv_reason, deferred_gate_rejected,
    gate_coverable, gate_coverable_error, narrow_stable_key_matches_by_id,
    neighbor_finding_key,
)
from gate_disposition_receipts import (
    _behavior_gate_replay_reason, _counterevidence_reason, _tracking_issue_reason,
    parse_disposition_receipt,
)
from gate_dispositions import (
    _actionable_disposition_reason, _ambiguous_finding_key_status,
    _argv_matches_except_head, _consume_neighbor_disposition,
    _counterevidence_expired, _disposition_status, _legacy_disposition_stub,
    _neighbor_epoch_mismatch, _neighbor_evidence_for, _neighbor_primary_matches,
    _scope_errors, disposition_status, record_dispositions, validate_disposition_receipt,
)


def disposition_receipt_artifact_name(receipt: DispositionReceipt) -> str:
    """Reconstruct the producer artifact name bound to one receipt payload.

    The target component hashes finding_key AND finding_id: same-key
    receipts for distinct same-key P1s must not share an artifact name, or
    the dual-read merge (keyed by name) would drop one of them.
    """

    digest_prefix = receipt.audit_digest[:12]
    if receipt.finding_key:
        target_component = hashlib.sha256(
            _canonical_json([receipt.finding_key, receipt.finding_id])
        ).hexdigest()[:12]
    else:
        target_component = receipt.finding_id
    return (
        f"gate-disposition-receipt-v{DISPOSITION_RECEIPT_SCHEMA_VERSION}-"
        f"{receipt.epoch}-{digest_prefix}-{target_component}"
    )


def recorded_disposition_lines(audit: DispositionAudit) -> tuple[str, ...]:
    """Render bounded, evidence-linked audit lines for consumed receipts."""

    lines: list[str] = []
    for receipt in audit.recorded_receipts:
        target = receipt.finding_key or receipt.finding_id
        if receipt.disposition == "false-positive":
            evidence = receipt.counterevidence or {}
            pointer = _bounded_disposition_text(evidence.get("pointer", ""))
            command = _bounded_disposition_text(evidence.get("command", ""))
            rerun = evidence.get("gate_rerun")
            excerpt = rerun.get("excerpt") if isinstance(rerun, dict) else None
            output_value = excerpt if isinstance(excerpt, str) and excerpt.strip() else evidence.get("output", "")
            output = _bounded_disposition_text(output_value)
            evidence_text = f"pointer={pointer}; command={command}; output={output}"
        else:
            evidence_text = f"pointer={_bounded_disposition_text(receipt.tracking_issue or '')}"
        lines.append(
            f"disposition={receipt.disposition} finding={_bounded_disposition_text(target)} "
            f"{evidence_text} receipt={disposition_receipt_artifact_name(receipt)} "
            f"reason={_bounded_disposition_text(receipt.reason)}"
        )
    return tuple(lines)


_POLICY_BY_TIER = {
    "personal": (1, 3),
    "internal": (2, 5),
    "saas": (2, 8),
}
_PR_ROUND_LIMIT_BY_TIER = {"personal": 5, "internal": 8, "saas": 12}


def validate_scope(scope: Scope) -> None:
    """Validate every scope guard and raise on any unknown or malformed one."""

    if not isinstance(scope, Scope):
        raise ScopeValidationError(f"scope must be Scope, got {type(scope).__name__}")
    errors = _scope_errors(scope)
    if errors:
        raise ScopeValidationError("invalid scope: " + "; ".join(errors))


def derive_epoch(scope: Scope) -> str:
    """Return the generation guard: SHA-256 of canonical Scope JSON."""

    validate_scope(scope)
    return _sha256(scope.as_dict())


def policy_for(scope: Scope) -> Policy:
    """Resolve the frozen tier matrix for a validated Scope."""

    validate_scope(scope)
    clean, maximum = _POLICY_BY_TIER[scope.tier]
    if not _strict_int(clean) or not _strict_int(maximum) or not 1 <= clean <= maximum:
        raise ConvergenceError("invalid policy cap: require 1 <= N <= max_rounds")
    return Policy(
        tier=scope.tier,
        clean_rounds=clean,
        max_rounds=maximum,
        unavailable_budget=maximum,
    )


def pr_round_budget(
    *,
    prior_run_ids: Sequence[int],
    current_run_id: int,
    current_eligible: bool,
    tier: str,
    history_status: str = "available",
) -> dict[str, Any]:
    """Derive shadow-only eligible primary count across all PR heads.

    A workflow rerun shares its run id with the original attempt, so run ids
    are the counting unit. History unavailability is a third state, never a
    zero-count alias. This projection does not alter the per-epoch reducer.
    """
    if tier not in _PR_ROUND_LIMIT_BY_TIER:
        raise ConvergenceError(f"unsupported tier for PR round budget {tier!r}")
    if history_status not in {"available", "history_unavailable"}:
        raise ConvergenceError(f"invalid PR round history status {history_status!r}")
    if type(current_run_id) is not int or current_run_id <= 0:
        raise ConvergenceError("current_run_id must be a positive integer")
    if type(current_eligible) is not bool:
        raise ConvergenceError("current_eligible must be a boolean")
    limit = _PR_ROUND_LIMIT_BY_TIER[tier]
    if history_status == "history_unavailable":
        return {
            "history_status": "history_unavailable",
            "limit": limit,
            "terminal_decision": "history_unavailable",
        }
    run_ids: set[int] = set()
    for run_id in prior_run_ids:
        if type(run_id) is not int or run_id <= 0:
            raise ConvergenceError("prior run ids must be positive integers")
        run_ids.add(run_id)
    if current_eligible:
        run_ids.add(current_run_id)
    eligible_rounds = len(run_ids)
    return {
        "history_status": "available",
        "eligible_rounds": eligible_rounds,
        "limit": limit,
        "terminal_decision": "arbitration_required" if eligible_rounds >= limit else "collecting",
    }


def _empty_key_digest() -> str:
    return _sha256([])


def _keys_digest(keys: Sequence[tuple[Any, ...]]) -> str:
    return _sha256(sorted(keys))


def _valid_processing_key_tuple(value: Any) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 4
        and all(_strict_int(item) and item > 0 for item in value)
    )


def _valid_round_key_tuple(value: Any) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and _nonempty_text(value[0])
        and _strict_int(value[1])
        and value[1] > 0
        and _nonempty_text(value[2])
    )


def _state_index_errors(state: ConvergenceState) -> list[str]:
    errors: list[str] = []
    processing_valid = isinstance(state.processing_keys, tuple) and all(
        _valid_processing_key_tuple(key) for key in state.processing_keys
    )
    if not processing_valid:
        errors.append("processing key index has invalid element shape")
    elif state.processing_keys_digest != _keys_digest(state.processing_keys):
        errors.append("processing key digest does not match its consumed set")
    round_valid = isinstance(state.round_keys, tuple) and all(
        _valid_round_key_tuple(key) for key in state.round_keys
    )
    if not round_valid:
        errors.append("round key index has invalid element shape")
    elif state.round_keys_digest != _keys_digest(state.round_keys):
        errors.append("round key digest does not match its consumed set")
    return errors


def initial_state(scope: Scope) -> ConvergenceState:
    """Create an explicit zero-based state for a validated scope."""

    epoch = derive_epoch(scope)
    return ConvergenceState(
        schema_version=SCHEMA_VERSION,
        epoch=epoch,
        clean_streak=0,
        eligible_rounds=0,
        unavailable_streak=0,
        processing_keys_digest=_empty_key_digest(),
        round_keys_digest=_empty_key_digest(),
        terminal_decision="collecting",
    )


def _state_errors(state: ConvergenceState) -> list[str]:
    errors: list[str] = []
    if not isinstance(state, ConvergenceState):
        return [f"state must be ConvergenceState, got {type(state).__name__}"]
    if state.schema_version != SCHEMA_VERSION:
        errors.append(f"unknown state schema_version {state.schema_version!r}")
    if not _nonempty_text(state.epoch):
        errors.append("state epoch must be a non-empty string")
    for name in ("clean_streak", "eligible_rounds", "unavailable_streak"):
        value = getattr(state, name)
        if not _strict_int(value) or value < 0:
            errors.append(f"state {name} must be a non-negative int")
    if state.terminal_decision not in TERMINAL_DECISIONS:
        errors.append(f"unknown terminal decision {state.terminal_decision!r}")
    errors.extend(_state_index_errors(state))
    recorded_processing: list[tuple[int, int, int, int]] = []
    recorded_rounds: list[tuple[str, int, str]] = []
    for record in state.event_records:
        if not isinstance(record, tuple) or len(record) != 3:
            errors.append("event record has invalid shape")
            continue
        keys, event_id, evidence = record
        if (
            not isinstance(keys, tuple)
            or len(keys) != 2
            or not _valid_processing_key_tuple(keys[0])
            or not _valid_round_key_tuple(keys[1])
        ):
            errors.append("event record key pair has invalid shape")
        else:
            recorded_processing.append(keys[0])
            recorded_rounds.append(keys[1])
        if not _nonempty_text(event_id) or not isinstance(evidence, tuple) or len(evidence) != 4:
            errors.append("event record identity/fingerprint is empty")
            continue
        fingerprint, verdict, p1_ids, effect = evidence
        if not _nonempty_text(fingerprint) or not isinstance(verdict, str) or verdict not in PRIMARY_VERDICTS or not isinstance(p1_ids, tuple) or not isinstance(effect, str) or effect not in {"eligible", "unavailable", "terminal"}:
            errors.append("event record evidence is not canonical")
    if isinstance(state.processing_keys, tuple) and all(
        _valid_processing_key_tuple(key) for key in state.processing_keys
    ):
        if tuple(sorted(recorded_processing)) != tuple(sorted(state.processing_keys)):
            errors.append("processing key index does not match event evidence")
    if isinstance(state.round_keys, tuple) and all(
        _valid_round_key_tuple(key) for key in state.round_keys
    ):
        if tuple(sorted(recorded_rounds)) != tuple(sorted(state.round_keys)):
            errors.append("round key index does not match event evidence")
    return errors


def _state_evidence_errors(state: ConvergenceState, policy: Policy) -> list[str]:
    clean = eligible = unavailable = 0
    terminal = "collecting"
    for _, _, evidence in state.event_records:
        _, verdict, p1_ids, effect = evidence
        if effect == "terminal":
            if terminal != "converged":
                return ["terminal event does not follow a converged state"]
            terminal = "manual_required"
        elif effect == "unavailable":
            unavailable += 1
            terminal = "manual_required" if unavailable >= policy.unavailable_budget else "collecting"
        else:
            eligible += 1
            clean = clean + 1 if not p1_ids else 0
            unavailable = 0
            terminal = "converged" if clean >= policy.clean_rounds else "manual_required" if eligible >= policy.max_rounds else "collecting"
    errors = []
    if (clean, eligible, unavailable) != (state.clean_streak, state.eligible_rounds, state.unavailable_streak):
        errors.append("state counters do not match event evidence")
    if state.terminal_decision != "fail_closed" and terminal != state.terminal_decision:
        errors.append("state terminal decision does not match event evidence")
    return errors


def _state_with(
    state: ConvergenceState,
    *,
    epoch: str | None = None,
    clean_streak: int | None = None,
    eligible_rounds: int | None = None,
    unavailable_streak: int | None = None,
    terminal_decision: str | None = None,
    processing_keys: tuple[tuple[int, int, int, int], ...] | None = None,
    round_keys: tuple[tuple[str, int, str], ...] | None = None,
    event_records: tuple[tuple[tuple[Any, ...], str, tuple[Any, ...]], ...] | None = None,
    reason: str | None = None,
) -> ConvergenceState:
    new_processing = state.processing_keys if processing_keys is None else processing_keys
    new_round = state.round_keys if round_keys is None else round_keys
    return ConvergenceState(
        schema_version=state.schema_version,
        epoch=state.epoch if epoch is None else epoch,
        clean_streak=state.clean_streak if clean_streak is None else clean_streak,
        eligible_rounds=state.eligible_rounds if eligible_rounds is None else eligible_rounds,
        unavailable_streak=state.unavailable_streak if unavailable_streak is None else unavailable_streak,
        processing_keys_digest=_keys_digest(new_processing),
        round_keys_digest=_keys_digest(new_round),
        terminal_decision=state.terminal_decision if terminal_decision is None else terminal_decision,
        processing_keys=new_processing,
        round_keys=new_round,
        event_records=state.event_records if event_records is None else event_records,
        reason=state.reason if reason is None else reason,
    )


def _fail_closed_state(state: ConvergenceState, reason: str) -> ConvergenceState:
    if not isinstance(state, ConvergenceState):
        return ConvergenceState(
            schema_version=SCHEMA_VERSION, epoch="", clean_streak=0, eligible_rounds=0,
            unavailable_streak=0, processing_keys_digest=_empty_key_digest(),
            round_keys_digest=_empty_key_digest(), terminal_decision="fail_closed", reason=reason,
        )
    if _state_index_errors(state):
        return ConvergenceState(
            schema_version=SCHEMA_VERSION,
            epoch=state.epoch if _nonempty_text(state.epoch) else "",
            clean_streak=state.clean_streak if _strict_int(state.clean_streak) and state.clean_streak >= 0 else 0,
            eligible_rounds=state.eligible_rounds if _strict_int(state.eligible_rounds) and state.eligible_rounds >= 0 else 0,
            unavailable_streak=state.unavailable_streak if _strict_int(state.unavailable_streak) and state.unavailable_streak >= 0 else 0,
            processing_keys_digest=_empty_key_digest(),
            round_keys_digest=_empty_key_digest(),
            terminal_decision="fail_closed",
            reason=reason,
        )
    return _state_with(state, terminal_decision="fail_closed", reason=reason)


def _primary_errors(*, scope: Scope, primary: CanonicalPrimary) -> list[str]:
    errors: list[str] = []
    if not isinstance(primary, CanonicalPrimary):
        return [f"primary must be CanonicalPrimary, got {type(primary).__name__}"]
    if primary.schema_version != SCHEMA_VERSION:
        errors.append(f"unknown primary schema_version {primary.schema_version!r}")
    for name in ("repository_id", "pr_number", "run_id", "run_attempt"):
        value = getattr(primary, name)
        if not _strict_int(value) or value <= 0:
            errors.append(f"primary {name} must be a positive int")
    if not _nonempty_text(primary.head_sha):
        errors.append("primary head_sha must be a non-empty string")
    if primary.repository_id != scope.repository_id:
        errors.append("primary repository_id does not match scope")
    if primary.pr_number != scope.pr_number:
        errors.append("primary pr_number does not match scope")
    if primary.head_sha != scope.head_sha:
        errors.append("primary head_sha does not match scope")
    if primary.verdict not in PRIMARY_VERDICTS:
        errors.append(f"primary verdict {primary.verdict!r} is not canonical")
    if not isinstance(primary.p1_ids, tuple):
        errors.append("primary p1_ids must be an immutable tuple")
    else:
        seen: set[str] = set()
        for finding_id in primary.p1_ids:
            if not isinstance(finding_id, str) or not finding_id:
                errors.append("primary p1 finding ids must be non-empty strings")
                continue
            if finding_id in seen:
                errors.append(f"primary p1 finding id repeated: {finding_id!r}")
            seen.add(finding_id)
    if not isinstance(primary.p1_findings, tuple):
        errors.append("primary p1_findings must be an immutable tuple")
    elif primary.p1_findings:
        finding_ids: list[str] = []
        for finding in primary.p1_findings:
            record = _primary_finding_record(finding)
            if (
                record is None
                or not isinstance(record.get("id"), str)
                or not record["id"]
                or not isinstance(record.get("severity"), str)
                or record["severity"] not in P1_SEVERITIES
                or (record.get("trigger_kind") is not None and not isinstance(record["trigger_kind"], str))
                or (
                    len(finding) == 6
                    and (
                        not isinstance(record.get("file"), str)
                        or (record.get("line") is not None and type(record.get("line")) is not int)
                        or not isinstance(record.get("category"), str)
                    )
                )
            ):
                errors.append(
                    "primary p1_findings must contain either legacy v2 "
                    "(id, P1 severity, trigger_kind) or stable-key "
                    "(id, P1 severity, trigger_kind, file, line, category)"
                )
                continue
            finding_ids.append(record["id"])
        if tuple(finding_ids) != primary.p1_ids:
            errors.append("primary p1_findings ids must match p1_ids")
    return errors


def _processing_errors(*, scope: Scope, primary: CanonicalPrimary, key: ProcessingKey) -> list[str]:
    errors: list[str] = []
    if not isinstance(key, ProcessingKey):
        return [f"processing_key must be ProcessingKey, got {type(key).__name__}"]
    for name in ("repository_id", "pr_number", "run_id", "run_attempt"):
        value = getattr(key, name)
        if not _strict_int(value) or value <= 0:
            errors.append(f"processing_key {name} must be a positive int")
    if key.repository_id != scope.repository_id or key.pr_number != scope.pr_number:
        errors.append("processing_key repository/PR does not match scope")
    if isinstance(primary, CanonicalPrimary):
        if key.run_id != primary.run_id or key.run_attempt != primary.run_attempt:
            errors.append("processing_key does not match primary run identity")
    return errors


def _event_id(*, epoch: str, run_id: int, run_attempt: int, audit_digest: str, receipt_kind: str = RECEIPT_KIND) -> str:
    return _sha256([epoch, run_id, run_attempt, audit_digest, receipt_kind])


def _round_key(*, epoch: str, run_id: int, audit_digest: str) -> RoundKey:
    return RoundKey(epoch=epoch, run_id=run_id, audit_digest=audit_digest)


def _round_fingerprint(*, scope: Scope, primary: CanonicalPrimary, audit_digest: str, processing_key: ProcessingKey) -> str:
    return _sha256(
        {
            "scope": scope.as_dict(),
            "primary": primary.as_dict(),
            "audit_digest": audit_digest,
            "processing_key": list(processing_key.as_tuple()),
        }
    )


def _record_event(
    state: ConvergenceState,
    *,
    processing_key: ProcessingKey,
    round_key: RoundKey,
    event_id: str,
    fingerprint: str,
    verdict: str,
    p1_ids: tuple[str, ...],
    effect: str,
) -> ConvergenceState:
    processing = (*state.processing_keys, processing_key.as_tuple())
    rounds = (*state.round_keys, round_key.as_tuple())
    evidence = (fingerprint, verdict, p1_ids, effect)
    records = (*state.event_records, ((processing_key.as_tuple(), round_key.as_tuple()), event_id, evidence))
    return _state_with(state, processing_keys=processing, round_keys=rounds, event_records=records)


def _duplicate_status(
    state: ConvergenceState,
    *,
    processing_key: ProcessingKey,
    round_key: RoundKey,
    event_id: str,
    fingerprint: str,
) -> tuple[bool, bool]:
    """Return (is_duplicate, is_conflict) for a previously consumed round."""

    if not isinstance(state, ConvergenceState) or not isinstance(processing_key, ProcessingKey) or not isinstance(round_key, RoundKey) or not _nonempty_text(event_id) or not _nonempty_text(fingerprint) or _state_errors(state):
        return (False, True)

    processing_tuple = processing_key.as_tuple()
    round_tuple = round_key.as_tuple()
    matches = [
        record
        for record in state.event_records
        if processing_tuple in record[0] or round_tuple in record[0] or event_id == record[1]
    ]
    if not matches:
        key_seen = processing_tuple in state.processing_keys or round_tuple in state.round_keys
        return (key_seen, key_seen)
    if any(record[1] == event_id and record[2][0] != fingerprint for record in matches):
        return (False, True)
    if any(record[0][0] == processing_tuple and record[2][0] != fingerprint for record in matches):
        return (False, True)
    if any(record[0] == (processing_tuple, round_tuple) and record[2][0] != fingerprint for record in matches):
        return (False, True)
    return (True, False)


def _decision(
    state: ConvergenceState,
    *,
    processing_key: ProcessingKey,
    round_key: RoundKey,
    event_id: str,
    reason: str,
    accepted: bool,
    no_op: bool,
    remaining_p1_ids: tuple[str, ...] = (),
) -> RoundDecision:
    return RoundDecision(
        state=state,
        decision=state.terminal_decision,
        reason=reason,
        accepted=accepted,
        no_op=no_op,
        processing_key=processing_key,
        round_key=round_key,
        event_id=event_id,
        remaining_p1_ids=remaining_p1_ids,
    )


def evaluate_round(
    *,
    state: ConvergenceState,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    waiver_receipts: Sequence[DispositionReceipt] = (),
    disposition_audit: DispositionAudit | None = None,
    processing_key: ProcessingKey,
    repository: str | None = None,
    coverage_result: Mapping[str, Any] | None = None,
) -> RoundDecision:
    """Consume exactly one canonical primary observation.

    A clean round has no remaining P1 findings after active receipts are
    validated against this exact primary audit, and coverage is complete.
    """

    scope_errors = _scope_errors(scope) if isinstance(scope, Scope) else ["scope must be Scope"]
    if scope_errors:
        fallback = state if isinstance(state, ConvergenceState) else ConvergenceState(
            schema_version=SCHEMA_VERSION,
            epoch="",
            clean_streak=0,
            eligible_rounds=0,
            unavailable_streak=0,
            processing_keys_digest=_empty_key_digest(),
            round_keys_digest=_empty_key_digest(),
            terminal_decision="fail_closed",
            reason="invalid scope",
        )
        return _decision(
            _fail_closed_state(fallback, "invalid scope: " + "; ".join(scope_errors)),
            processing_key=processing_key,
            round_key=RoundKey(epoch="", run_id=0, audit_digest=""),
            event_id="",
            reason="invalid scope",
            accepted=False,
            no_op=False,
        )
    epoch = derive_epoch(scope)
    state_errors = _state_errors(state)
    if state_errors:
        failed = _fail_closed_state(state, "invalid state: " + "; ".join(state_errors))
        return _decision(
            failed,
            processing_key=processing_key,
            round_key=RoundKey(epoch=epoch, run_id=0, audit_digest=""),
            event_id="",
            reason=failed.reason,
            accepted=False,
            no_op=False,
        )
    policy_errors: list[str] = []
    try:
        policy = policy_for(scope)
    except ConvergenceError as exc:
        policy_errors.append(str(exc))
    if policy_errors:
        failed = _fail_closed_state(state, "invalid policy: " + "; ".join(policy_errors))
        return _decision(
            failed,
            processing_key=processing_key,
            round_key=RoundKey(epoch=epoch, run_id=0, audit_digest=""),
            event_id="",
            reason=failed.reason,
            accepted=False,
            no_op=False,
        )
    if state.epoch == epoch:
        evidence_errors = _state_evidence_errors(state, policy)
        if evidence_errors:
            failed = _fail_closed_state(state, "; ".join(evidence_errors))
            return _decision(failed, processing_key=processing_key, round_key=RoundKey(epoch, 0, ""), event_id="", reason=failed.reason, accepted=False, no_op=False)
    audit_errors = _audit_digest_errors(audit_digest)
    primary_errors = _primary_errors(scope=scope, primary=primary)
    processing_errors = _processing_errors(scope=scope, primary=primary, key=processing_key)
    if audit_errors or primary_errors or processing_errors:
        failed = _fail_closed_state(
            state,
            "; ".join(audit_errors + primary_errors + processing_errors),
        )
        safe_run_id = processing_key.run_id if isinstance(processing_key, ProcessingKey) else 0
        safe_run_attempt = processing_key.run_attempt if isinstance(processing_key, ProcessingKey) else 0
        safe_digest = audit_digest if isinstance(audit_digest, str) else ""
        round_key = RoundKey(epoch=epoch, run_id=safe_run_id, audit_digest=safe_digest)
        return _decision(
            failed,
            processing_key=processing_key,
            round_key=round_key,
            event_id=_event_id(
                epoch=epoch,
                run_id=safe_run_id,
                run_attempt=safe_run_attempt,
                audit_digest=safe_digest,
            ),
            reason=failed.reason,
            accepted=False,
            no_op=False,
        )
    if disposition_audit is None:
        disposition_audit = record_dispositions(
            primary.p1_ids,
            waiver_receipts,
            scope=scope,
            primary=primary,
            audit_digest=audit_digest,
            repository=repository,
        )
    remaining_p1_ids = disposition_audit.remaining_p1_ids
    # Epoch boundaries precede all idempotency checks. Old indexes cannot
    # consume a round in the new generation.
    if state.epoch != epoch:
        if state.terminal_decision in {"fail_closed", "manual_required"}:
            failed = _fail_closed_state(state, "untrusted state cannot auto-reset on scope change")
            return _decision(failed, processing_key=processing_key, round_key=RoundKey(epoch, primary.run_id, audit_digest), event_id="", reason=failed.reason, accepted=False, no_op=False)
        working = initial_state(scope)
    else:
        working = state
    round_key = _round_key(epoch=epoch, run_id=primary.run_id, audit_digest=audit_digest)
    event_id = _event_id(
        epoch=epoch,
        run_id=primary.run_id,
        run_attempt=primary.run_attempt,
        audit_digest=audit_digest,
    )
    fingerprint = _round_fingerprint(
        scope=scope,
        primary=primary,
        audit_digest=audit_digest,
        processing_key=processing_key,
    )
    duplicate, conflict = _duplicate_status(
        working,
        processing_key=processing_key,
        round_key=round_key,
        event_id=event_id,
        fingerprint=fingerprint,
    )
    if conflict:
        failed = _fail_closed_state(working, "conflicting payload for a consumed processing/round/event key")
        return _decision(
            failed,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason=failed.reason,
            accepted=False,
            no_op=False,
        )
    if duplicate:
        return _decision(
            working,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason="duplicate processing or round key",
            accepted=True,
            no_op=True,
        )

    if working.terminal_decision == "fail_closed":
        return _decision(
            working,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason="fail_closed state is sticky",
            accepted=False,
            no_op=True,
        )
    if working.terminal_decision == "manual_required":
        return _decision(
            working,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason="manual_required is terminal for this epoch",
            accepted=False,
            no_op=True,
        )
    if working.terminal_decision == "converged":
        recorded = _record_event(
            working,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            fingerprint=fingerprint,
            verdict=primary.verdict, p1_ids=primary.p1_ids, effect="terminal",
        )
        terminal = _state_with(recorded, terminal_decision="manual_required", reason="new round after convergence requires manual review")
        return _decision(
            terminal,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason=terminal.reason,
            accepted=True,
            no_op=False,
        )

    recorded = _record_event(
        working,
        processing_key=processing_key,
        round_key=round_key,
        event_id=event_id,
        fingerprint=fingerprint,
        verdict=primary.verdict, p1_ids=primary.p1_ids,
        effect="unavailable" if primary.verdict == "unavailable" else "eligible",
    )
    if primary.verdict == "unavailable":
        unavailable = recorded.unavailable_streak + 1
        terminal = "manual_required" if unavailable >= policy.unavailable_budget else "collecting"
        next_state = _state_with(
            recorded,
            unavailable_streak=unavailable,
            terminal_decision=terminal,
            reason=("unavailable budget exhausted" if terminal == "manual_required" else "canonical primary unavailable"),
        )
        return _decision(
            next_state,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason=next_state.reason,
            accepted=True,
            no_op=False,
        )

    eligible = recorded.eligible_rounds + 1
    streak = recorded.clean_streak + 1 if not remaining_p1_ids and ((coverage_result or {}).get("coverage") or {}).get("status") != "incomplete" else 0
    # The threshold is deliberately checked before the eligible cap.
    if streak >= policy.clean_rounds:
        terminal = "converged"
        reason = "clean streak reached policy threshold"
    elif eligible >= policy.max_rounds:
        terminal = "manual_required"
        reason = "eligible round budget exhausted before convergence"
    else:
        terminal = "collecting"
        reason = "eligible round consumed"
    next_state = _state_with(
        recorded,
        clean_streak=streak,
        eligible_rounds=eligible,
        unavailable_streak=0,
        terminal_decision=terminal,
        reason=reason,
    )
    return _decision(
        next_state,
        processing_key=processing_key,
        round_key=round_key,
        event_id=event_id,
        reason=reason,
        accepted=True,
        no_op=False,
        remaining_p1_ids=remaining_p1_ids,
    )


def receipt_for_round(
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    decision: RoundDecision,
    source_attempt: int | None = None,
    artifact_id: str | None = None,
    artifact_name: str | None = None,
) -> Receipt:
    """Build the immutable replay receipt for exactly one round.

    The evaluator is the sole owner of round identity.  In particular, the
    three idempotency guards are copied verbatim from ``decision`` rather than
    re-derived here; a producer and a future replay consumer must observe the
    same keys even if their surrounding metadata differs.
    """
    return Receipt(
        schema_version=RECEIPT_SCHEMA_VERSION,
        scope=scope,
        epoch=decision.state.epoch,
        processing_key=decision.processing_key,
        round_key=decision.round_key,
        event_id=decision.event_id,
        run_id=primary.run_id,
        run_attempt=primary.run_attempt,
        audit_digest=audit_digest,
        verdict=primary.verdict,
        p1_ids=primary.p1_ids,
        p1_findings=primary.p1_findings,
        source_attempt=primary.run_attempt if source_attempt is None else source_attempt,
        artifact_id=artifact_id,
        artifact_name=artifact_name,
        receipt_kind=RECEIPT_KIND,
        reported_decision=decision.decision,
        reported_clean_streak=decision.clean_streak,
        reported_eligible_rounds=decision.eligible_rounds,
    )


def _receipt_round_fingerprint(receipt: Receipt) -> str:
    return _sha256(
        {
            "scope": receipt.scope.as_dict(),
            "epoch": receipt.epoch,
            "round_key": list(receipt.round_key.as_tuple()),
            "audit_digest": receipt.audit_digest,
            "verdict": receipt.verdict,
            "p1_ids": list(receipt.p1_ids),
            "p1_findings": [list(item) for item in receipt.p1_findings],
            "source_attempt": receipt.source_attempt,
            "artifact_id": receipt.artifact_id,
            "artifact_name": receipt.artifact_name,
            "receipt_kind": receipt.receipt_kind,
        }
    )


def _receipt_event_fingerprint(receipt: Receipt) -> str:
    return _sha256(receipt.as_dict(include_reported=False))


def validate_receipt(receipt: Receipt, scope: Scope) -> None:
    """Validate the producer/schema/identity guard tuple of one receipt."""

    if not isinstance(receipt, Receipt):
        raise ReceiptValidationError(f"receipt must be Receipt, got {type(receipt).__name__}")
    try:
        validate_scope(scope)
    except ScopeValidationError as exc:
        raise ReceiptValidationError(f"evaluator scope is invalid: {exc}") from exc
    try:
        validate_scope(receipt.scope)
    except ScopeValidationError as exc:
        raise ReceiptValidationError(f"receipt scope is invalid: {exc}") from exc
    expected_epoch = derive_epoch(scope)
    errors: list[str] = []
    if receipt.scope.as_dict() != scope.as_dict():
        errors.append("receipt scope does not match evaluator scope")
    if receipt.schema_version != RECEIPT_SCHEMA_VERSION:
        errors.append(f"unknown receipt schema_version {receipt.schema_version!r}")
    if receipt.epoch != expected_epoch:
        errors.append("receipt epoch does not match scope")
    if receipt.receipt_kind != RECEIPT_KIND:
        errors.append(f"unknown receipt kind {receipt.receipt_kind!r}")
    errors.extend(_audit_digest_errors(receipt.audit_digest))
    if not _strict_int(receipt.run_id) or receipt.run_id <= 0:
        errors.append("receipt run_id must be a positive int")
    if not _strict_int(receipt.run_attempt) or receipt.run_attempt <= 0:
        errors.append("receipt run_attempt must be a positive int")
    if receipt.verdict not in PRIMARY_VERDICTS:
        errors.append(f"receipt verdict {receipt.verdict!r} is not canonical")
    if not isinstance(receipt.p1_ids, tuple) or any(not _nonempty_text(item) for item in receipt.p1_ids):
        errors.append("receipt p1_ids must be an immutable tuple of non-empty strings")
    if not isinstance(receipt.p1_findings, tuple):
        errors.append("receipt p1_findings must be an immutable tuple")
    elif receipt.p1_findings:
        finding_ids: list[str] = []
        for finding in receipt.p1_findings:
            record = _primary_finding_record(finding)
            if (
                record is None
                or not _nonempty_text(record.get("id"))
                or not isinstance(record.get("severity"), str)
                or record["severity"] not in P1_SEVERITIES
                or (record.get("trigger_kind") is not None and not isinstance(record["trigger_kind"], str))
                or (
                    len(finding) == 6
                    and (
                        not isinstance(record.get("file"), str)
                        or (record.get("line") is not None and type(record.get("line")) is not int)
                        or not isinstance(record.get("category"), str)
                    )
                )
            ):
                errors.append(
                    "receipt p1_findings must contain either legacy v2 "
                    "(id, P1 severity, trigger_kind) or stable-key "
                    "(id, P1 severity, trigger_kind, file, line, category)"
                )
                continue
            finding_ids.append(record["id"])
        if tuple(finding_ids) != receipt.p1_ids:
            errors.append("receipt p1_findings ids must match p1_ids")
    if not isinstance(receipt.processing_key, ProcessingKey):
        errors.append("receipt processing_key has invalid type")
    else:
        if receipt.processing_key.as_tuple() != (
            scope.repository_id,
            scope.pr_number,
            receipt.run_id,
            receipt.run_attempt,
        ):
            errors.append("receipt processing_key is not bound to receipt identity")
    expected_round = RoundKey(expected_epoch, receipt.run_id, receipt.audit_digest)
    if receipt.round_key != expected_round:
        errors.append("receipt round_key is not bound to epoch/run/audit digest")
    expected_event = _event_id(
        epoch=expected_epoch,
        run_id=receipt.run_id,
        run_attempt=receipt.run_attempt,
        audit_digest=receipt.audit_digest,
        receipt_kind=receipt.receipt_kind,
    )
    if receipt.event_id != expected_event:
        errors.append("receipt event_id is not derived from its immutable identity")
    if receipt.source_attempt is None or not _strict_int(receipt.source_attempt) or receipt.source_attempt <= 0:
        errors.append("receipt source_attempt must be a positive int")
    elif receipt.source_attempt > receipt.run_attempt:
        errors.append("receipt source_attempt cannot exceed receipt run_attempt")
    artifact_values = [receipt.artifact_id, receipt.artifact_name]
    if not any(_nonempty_text(value) for value in artifact_values):
        errors.append("receipt must preserve a non-empty artifact id/name")
    if all(_nonempty_text(value) for value in artifact_values) and receipt.artifact_id != receipt.artifact_name:
        errors.append("receipt artifact_id and artifact_name disagree")
    if errors:
        raise ReceiptValidationError("invalid receipt: " + "; ".join(errors))


def dedupe_receipts(receipts: Sequence[Receipt]) -> tuple[Receipt, ...]:
    """Stable-sort receipts and remove only exact same-key replays.

    A processing key, round key, or event id may be repeated only when the
    corresponding immutable payload is identical.  The function raises on a
    conflict; replay_receipts converts that explicit conflict into a sticky
    fail-closed state.
    """

    if not isinstance(receipts, Sequence):
        raise ReceiptValidationError("receipts must be a Sequence")
    for receipt in receipts:
        if not isinstance(receipt, Receipt):
            raise ReceiptValidationError(f"receipt must be Receipt, got {type(receipt).__name__}")
        validate_receipt(receipt, receipt.scope)
    ordered = sorted(
        receipts,
        key=lambda receipt: (receipt.run_id, receipt.run_attempt, receipt.event_id),
    )
    by_processing: dict[tuple[int, int, int, int], str] = {}
    by_round: dict[tuple[str, int, str], str] = {}
    by_event: dict[str, str] = {}
    unique: list[Receipt] = []
    for receipt in ordered:
        event_fingerprint = _receipt_event_fingerprint(receipt)
        round_fingerprint = _receipt_round_fingerprint(receipt)
        processing_key = receipt.processing_key.as_tuple()
        round_key = receipt.round_key.as_tuple()
        prior_event = by_event.get(receipt.event_id)
        prior_processing = by_processing.get(processing_key)
        prior_round = by_round.get(round_key)
        if (
            (prior_event is not None and prior_event != event_fingerprint)
            or (prior_processing is not None and prior_processing != event_fingerprint)
            or (prior_round is not None and prior_round != round_fingerprint)
        ):
            raise ReceiptConflictError(
                "conflicting immutable payload for event, processing, or round key"
            )
        if prior_event is not None or prior_processing is not None or prior_round is not None:
            continue
        by_event[receipt.event_id] = event_fingerprint
        by_processing[processing_key] = event_fingerprint
        by_round[round_key] = round_fingerprint
        unique.append(receipt)
    return tuple(unique)


def _replay_failure(scope: Scope, reason: str) -> ConvergenceState:
    return _fail_closed_state(initial_state(scope), reason)


def replay_receipts(*, scope: Scope, receipts: Sequence[Receipt]) -> ConvergenceState:
    """Recompute state from immutable receipts in deterministic producer order."""

    validate_scope(scope)
    if not isinstance(receipts, Sequence):
        raise ReceiptValidationError("receipts must be a Sequence")
    epoch = derive_epoch(scope)
    current: list[Receipt] = []
    for receipt in receipts:
        if not isinstance(receipt, Receipt):
            return _replay_failure(scope, f"invalid receipt type: {type(receipt).__name__}")
        # Validate the receipt against its own scope first.  A valid receipt
        # from an older generation is history, not input for this generation.
        try:
            validate_receipt(receipt, receipt.scope)
        except ReceiptValidationError as exc:
            return _replay_failure(scope, str(exc))
        if receipt.epoch != epoch or receipt.scope.as_dict() != scope.as_dict():
            continue
        try:
            validate_receipt(receipt, scope)
        except ReceiptValidationError as exc:
            return _replay_failure(scope, str(exc))
        current.append(receipt)
    try:
        ordered = dedupe_receipts(current)
    except ReceiptConflictError as exc:
        return _replay_failure(scope, str(exc))
    state = initial_state(scope)
    for receipt in ordered:
        primary = CanonicalPrimary(
            schema_version=SCHEMA_VERSION,
            repository_id=scope.repository_id,
            pr_number=scope.pr_number,
            head_sha=scope.head_sha,
            run_id=receipt.run_id,
            run_attempt=receipt.run_attempt,
            verdict=receipt.verdict,
            p1_ids=receipt.p1_ids,
            p1_findings=receipt.p1_findings,
        )
        result = evaluate_round(
            state=state,
            scope=scope,
            primary=primary,
            audit_digest=receipt.audit_digest,
            waiver_receipts=(),
            processing_key=receipt.processing_key,
        )
        state = result.state
        if state.terminal_decision == "fail_closed":
            return state
    return state
