"""Pure canonical clean-streak convergence evaluator.

This module deliberately has no filesystem, network, subprocess, environment,
or GitHub dependencies.  The caller owns I/O and supplies immutable canonical
primary evidence and receipts.  All state returned here is derived from those
inputs and can therefore be replayed in a different process.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
P1_SEVERITIES = frozenset({"major", "blocker"})
SUPPORTED_TIERS = ("personal", "internal", "saas")
PRIMARY_VERDICTS = frozenset({"pass", "fail", "unavailable"})
TERMINAL_DECISIONS = frozenset(
    {"collecting", "converged", "manual_required", "fail_closed"}
)
RECEIPT_KIND = "canonical_primary"

# This is intentionally local to the public gate repository.  The private
# policy source is represented only by Scope.policy_version/policy_digest in
# this increment; no policy file is parsed here.
_POLICY_BY_TIER = {
    "personal": (1, 3),
    "internal": (2, 5),
    "saas": (2, 8),
}
_TIER_UPGRADE = {"personal": "internal", "internal": "saas", "saas": "saas"}


class ConvergenceError(ValueError):
    """Base class for invalid immutable convergence inputs."""


class ScopeValidationError(ConvergenceError):
    """Raised when a Scope is not a complete, known identity."""


class ReceiptValidationError(ConvergenceError):
    """Raised when a receipt is not bound to its producer guards."""


class ReceiptConflictError(ConvergenceError):
    """Raised when one idempotency key is claimed by different payloads."""


@dataclass(frozen=True)
class Scope:
    repository_id: int
    pr_number: int
    base_sha: str
    head_sha: str
    diff_digest: str
    policy_version: str
    policy_digest: str
    tier: str
    effective_tier: str
    infra_classifier_version: str
    infra_diff: bool
    caller_sha: str
    reusable_workflow_sha: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "diff_digest": self.diff_digest,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "tier": self.tier,
            "effective_tier": self.effective_tier,
            "infra_classifier_version": self.infra_classifier_version,
            "infra_diff": self.infra_diff,
            "caller_sha": self.caller_sha,
            "reusable_workflow_sha": self.reusable_workflow_sha,
        }


@dataclass(frozen=True)
class Policy:
    tier: str
    effective_tier: str
    clean_rounds: int
    max_rounds: int
    unavailable_budget: int

    @property
    def required_clean_rounds(self) -> int:
        return self.clean_rounds

    @property
    def n(self) -> int:
        return self.clean_rounds


@dataclass(frozen=True)
class ProcessingKey:
    repository_id: int
    pr_number: int
    run_id: int
    run_attempt: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.repository_id, self.pr_number, self.run_id, self.run_attempt)


@dataclass(frozen=True)
class RoundKey:
    epoch: str
    run_id: int
    audit_digest: str

    def as_tuple(self) -> tuple[str, int, str]:
        return (self.epoch, self.run_id, self.audit_digest)


@dataclass(frozen=True)
class CanonicalPrimary:
    """The minimal canonical primary projection consumed by the reducer.

    ``p1_ids`` is evidence from the current canonical audit.  The evaluator
    never infers severity from finding text; a producer must have projected
    only the frozen P1 severity set into this field.
    """

    schema_version: int
    repository_id: int
    pr_number: int
    head_sha: str
    run_id: int
    run_attempt: int
    verdict: str
    p1_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repository_id": self.repository_id,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "verdict": self.verdict,
            "p1_ids": list(self.p1_ids),
        }


@dataclass(frozen=True)
class DispositionReceipt:
    """Increment-1 stub for the protected disposition contract.

    No disposition can make a round clean in this increment.  The fields are
    intentionally a small immutable shape so increment 2 can extend the
    producer without changing evaluate_round's signature.
    """

    schema_version: int = RECEIPT_SCHEMA_VERSION
    disposition: str = "none"
    epoch: str = ""
    audit_digest: str = ""
    finding_id: str = ""
    valid: bool = False


@dataclass(frozen=True)
class Receipt:
    """Immutable producer receipt used as replay input.

    ``reported_*`` members are audit-only fields.  Replay intentionally never
    uses them to derive counters.
    """

    schema_version: int
    scope: Scope
    epoch: str
    processing_key: ProcessingKey
    round_key: RoundKey
    event_id: str
    run_id: int
    run_attempt: int
    audit_digest: str
    verdict: str
    p1_ids: tuple[str, ...] = ()
    source_attempt: int | None = None
    artifact_id: str | None = None
    artifact_name: str | None = None
    receipt_kind: str = RECEIPT_KIND
    reported_decision: str | None = None
    reported_clean_streak: int | None = None
    reported_eligible_rounds: int | None = None

    def as_dict(self, *, include_reported: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "scope": self.scope.as_dict(),
            "epoch": self.epoch,
            "processing_key": list(self.processing_key.as_tuple()),
            "round_key": list(self.round_key.as_tuple()),
            "event_id": self.event_id,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "audit_digest": self.audit_digest,
            "verdict": self.verdict,
            "p1_ids": list(self.p1_ids),
            "source_attempt": self.source_attempt,
            "artifact_id": self.artifact_id,
            "artifact_name": self.artifact_name,
            "receipt_kind": self.receipt_kind,
        }
        if include_reported:
            payload.update(
                {
                    "decision": self.reported_decision,
                    "clean_streak": self.reported_clean_streak,
                    "eligible_rounds": self.reported_eligible_rounds,
                }
            )
        return payload


@dataclass(frozen=True)
class ConvergenceState:
    """Derived reducer state.

    The key tuples and event records are retained as replay indexes.  Their
    digests are the compact contract fields that can be published in a
    versioned envelope; the tuples themselves never come from a comment or a
    mutable external cursor.
    """

    schema_version: int
    epoch: str
    clean_streak: int
    eligible_rounds: int
    unavailable_streak: int
    processing_keys_digest: str
    round_keys_digest: str
    terminal_decision: str
    processing_keys: tuple[tuple[int, int, int, int], ...] = ()
    round_keys: tuple[tuple[str, int, str], ...] = ()
    event_records: tuple[tuple[tuple[Any, ...], str, str], ...] = ()
    reason: str = ""

    @property
    def decision(self) -> str:
        return self.terminal_decision

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "epoch": self.epoch,
            "clean_streak": self.clean_streak,
            "eligible_rounds": self.eligible_rounds,
            "unavailable_streak": self.unavailable_streak,
            "processing_keys_digest": self.processing_keys_digest,
            "round_keys_digest": self.round_keys_digest,
            "terminal_decision": self.terminal_decision,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RoundDecision:
    """Result of consuming one canonical round, including derived state."""

    state: ConvergenceState
    decision: str
    reason: str
    accepted: bool
    no_op: bool
    processing_key: ProcessingKey
    round_key: RoundKey
    event_id: str

    @property
    def clean_streak(self) -> int:
        return self.state.clean_streak

    @property
    def eligible_rounds(self) -> int:
        return self.state.eligible_rounds

    @property
    def unavailable_streak(self) -> int:
        return self.state.unavailable_streak

    @property
    def terminal_decision(self) -> str:
        return self.state.terminal_decision


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


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
        "infra_classifier_version",
        "caller_sha",
        "reusable_workflow_sha",
    ):
        if not _nonempty_text(getattr(scope, name)):
            errors.append(f"{name} must be a non-empty string")
    if type(scope.infra_diff) is not bool:
        errors.append("infra_diff must be a genuine bool")
    if scope.tier not in SUPPORTED_TIERS:
        errors.append(f"unknown tier {scope.tier!r}")
    if scope.effective_tier not in SUPPORTED_TIERS:
        errors.append(f"unknown effective_tier {scope.effective_tier!r}")
    elif scope.tier in SUPPORTED_TIERS:
        expected = _TIER_UPGRADE[scope.tier] if scope.infra_diff else scope.tier
        if scope.effective_tier != expected:
            errors.append(
                f"effective_tier {scope.effective_tier!r} does not match "
                f"tier={scope.tier!r}, infra_diff={scope.infra_diff!r}"
            )
    return errors


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


def policy_for(scope: Scope, *, clean_rounds: int | None = None, max_rounds: int | None = None) -> Policy:
    """Resolve the frozen tier matrix for a validated Scope.

    Optional overrides exist only as a validation seam for contract tests; a
    caller cannot use them to change the policy selected by Scope.
    """

    validate_scope(scope)
    clean, maximum = _POLICY_BY_TIER[scope.effective_tier]
    if clean_rounds is not None and clean_rounds != clean:
        raise ConvergenceError("policy clean-round cap does not match frozen policy")
    if max_rounds is not None and max_rounds != maximum:
        raise ConvergenceError("policy max-round cap does not match frozen policy")
    if not _strict_int(clean) or not _strict_int(maximum) or not 1 <= clean <= maximum:
        raise ConvergenceError("invalid policy cap: require 1 <= N <= max_rounds")
    return Policy(
        tier=scope.tier,
        effective_tier=scope.effective_tier,
        clean_rounds=clean,
        max_rounds=maximum,
        unavailable_budget=maximum,
    )


def _empty_key_digest() -> str:
    return _sha256([])


def _keys_digest(keys: Sequence[tuple[Any, ...]]) -> str:
    return _sha256(sorted(keys))


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
    if state.processing_keys_digest != _keys_digest(state.processing_keys):
        errors.append("processing key digest does not match its consumed set")
    if state.round_keys_digest != _keys_digest(state.round_keys):
        errors.append("round key digest does not match its consumed set")
    for record in state.event_records:
        if not isinstance(record, tuple) or len(record) != 3:
            errors.append("event record has invalid shape")
            continue
        keys, event_id, fingerprint = record
        if not isinstance(keys, tuple) or len(keys) != 2:
            errors.append("event record key pair has invalid shape")
        if not _nonempty_text(event_id) or not _nonempty_text(fingerprint):
            errors.append("event record identity/fingerprint is empty")
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
    event_records: tuple[tuple[tuple[Any, ...], str, str], ...] | None = None,
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
            if not _nonempty_text(finding_id):
                errors.append("primary p1 finding ids must be non-empty strings")
            elif finding_id in seen:
                errors.append(f"primary p1 finding id repeated: {finding_id!r}")
            seen.add(finding_id)
    return errors


def _audit_digest_errors(audit_digest: str) -> list[str]:
    if not isinstance(audit_digest, str) or len(audit_digest) != 64:
        return ["audit_digest must be a 64-character SHA-256 hex digest"]
    try:
        int(audit_digest, 16)
    except ValueError:
        return ["audit_digest must contain only hexadecimal characters"]
    return []


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
) -> ConvergenceState:
    processing = (*state.processing_keys, processing_key.as_tuple())
    rounds = (*state.round_keys, round_key.as_tuple())
    records = (*state.event_records, ((processing_key.as_tuple(), round_key.as_tuple()), event_id, fingerprint))
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
    if any(record[1] == event_id and record[2] != fingerprint for record in matches):
        return (False, True)
    if any(record[0] == (processing_tuple, round_tuple) and record[2] != fingerprint for record in matches):
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
    )


def evaluate_round(
    *,
    state: ConvergenceState,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    waiver_receipts: Sequence[DispositionReceipt],
    processing_key: ProcessingKey,
) -> RoundDecision:
    """Consume exactly one canonical primary observation.

    A clean round is defined solely by an eligible canonical primary whose
    current P1 projection is empty.  The disposition argument is present now
    for the increment-2 contract, but no receipt can resolve a finding here.
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
    audit_errors = _audit_digest_errors(audit_digest)
    primary_errors = _primary_errors(scope=scope, primary=primary)
    processing_errors = _processing_errors(scope=scope, primary=primary, key=processing_key)
    if audit_errors or primary_errors or processing_errors:
        failed = _fail_closed_state(
            state,
            "; ".join(audit_errors + primary_errors + processing_errors),
        )
        round_key = RoundKey(epoch=epoch, run_id=processing_key.run_id, audit_digest=audit_digest)
        return _decision(
            failed,
            processing_key=processing_key,
            round_key=round_key,
            event_id=_event_id(
                epoch=epoch,
                run_id=processing_key.run_id,
                run_attempt=processing_key.run_attempt,
                audit_digest=audit_digest,
            ),
            reason=failed.reason,
            accepted=False,
            no_op=False,
        )
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
        state,
        processing_key=processing_key,
        round_key=round_key,
        event_id=event_id,
        fingerprint=fingerprint,
    )
    if conflict:
        failed = _fail_closed_state(state, "conflicting payload for a consumed processing/round/event key")
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
            state,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason="duplicate processing or round key",
            accepted=True,
            no_op=True,
        )

    if any(getattr(receipt, "valid", False) for receipt in waiver_receipts):
        failed = _fail_closed_state(state, "disposition semantics are not enabled in increment 1")
        return _decision(
            failed,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason=failed.reason,
            accepted=False,
            no_op=False,
        )

    # A scope change is a new generation.  A damaged/manual old generation is
    # not silently reinitialized; only a trusted collecting/converged state can
    # be evaluated from a zero-based new epoch in this increment.
    if state.epoch != epoch:
        if state.terminal_decision in {"fail_closed", "manual_required"}:
            failed = _fail_closed_state(state, "untrusted state cannot auto-reset on scope change")
            return _decision(
                failed,
                processing_key=processing_key,
                round_key=round_key,
                event_id=event_id,
                reason=failed.reason,
                accepted=False,
                no_op=False,
            )
        working = initial_state(scope)
    else:
        working = state

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
    streak = recorded.clean_streak + 1 if not primary.p1_ids else 0
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
    validate_scope(scope)
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

    ordered = sorted(
        receipts,
        key=lambda receipt: (receipt.run_id, receipt.run_attempt, receipt.event_id),
    )
    by_processing: dict[tuple[int, int, int, int], str] = {}
    by_round: dict[tuple[str, int, str], str] = {}
    by_event: dict[str, str] = {}
    unique: list[Receipt] = []
    for receipt in ordered:
        if not isinstance(receipt, Receipt):
            raise ReceiptValidationError(f"receipt must be Receipt, got {type(receipt).__name__}")
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
