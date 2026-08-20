"""Pure canonical clean-streak convergence evaluator.

This module deliberately has no filesystem, network, subprocess, environment,
or GitHub dependencies.  The caller owns I/O and supplies immutable canonical
primary evidence and receipts.  All state returned here is derived from those
inputs and can therefore be replayed in a different process.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
P1_SEVERITIES = frozenset({"major", "blocker"})
KNOWN_SEVERITIES = frozenset({"major", "blocker", "minor", "nit"})
SEVERITY_P1 = P1_SEVERITIES
SUPPORTED_TIERS = ("personal", "internal", "saas")
PRIMARY_VERDICTS = frozenset({"pass", "fail", "unavailable"})
TERMINAL_DECISIONS = frozenset(
    {"collecting", "converged", "manual_required", "fail_closed"}
)
RECEIPT_KIND = "canonical_primary"
DISPOSITION_KINDS = frozenset({"false-positive", "accepted", "wont-fix", "fixed"})
DISPOSITION_RECEIPT_SCHEMA_VERSION = 1
DISPOSITION_REVOCATION_SCHEMA_VERSION = 1

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
    """Immutable, exact-current-round protected disposition receipt.

    ``valid`` remains a compatibility-only input marker for the increment-1
    fixture shape.  It is never included in the canonical payload and a true
    value without the complete protected binding is rejected.
    """

    schema_version: int = RECEIPT_SCHEMA_VERSION
    disposition: str = "none"
    repository_id: str = ""
    pr_number: int = 0
    epoch: str = ""
    head_sha: str = ""
    diff_digest: str = ""
    primary_run_id: str = ""
    primary_run_attempt: int = 0
    audit_digest: str = ""
    finding_id: str = ""
    issuer_login: str = ""
    issuer_user_id: str = ""
    control_run_id: str = ""
    approval_ref: str = ""
    issued_at: str = ""
    expires_at: str = ""
    nonce: str = ""
    evidence_manifest_digest: str = ""
    receipt_digest: str = ""
    scope: Mapping[str, Any] | None = None
    valid: bool = False

    def as_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "disposition": self.disposition,
            "repository_id": self.repository_id,
            "pr_number": self.pr_number,
            "epoch": self.epoch,
            "head_sha": self.head_sha,
            "diff_digest": self.diff_digest,
            "primary_run_id": self.primary_run_id,
            "primary_run_attempt": self.primary_run_attempt,
            "audit_digest": self.audit_digest,
            "finding_id": self.finding_id,
            "issuer_login": self.issuer_login,
            "issuer_user_id": self.issuer_user_id,
            "control_run_id": self.control_run_id,
            "approval_ref": self.approval_ref,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "evidence_manifest_digest": self.evidence_manifest_digest,
            "scope": self.scope.as_dict() if isinstance(self.scope, Scope) else self.scope,
        }
        if include_digest:
            payload["receipt_digest"] = self.receipt_digest
        return payload


@dataclass(frozen=True)
class DispositionRevocation:
    """Append-only protected revocation event."""

    schema_version: int
    nonce: str
    reason: str
    actor: str
    revoked_at: str
    evidence_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "nonce": self.nonce,
            "reason": self.reason,
            "actor": self.actor,
            "revoked_at": self.revoked_at,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class DispositionStatus:
    """Read-only disposition diagnostic; it is not convergence state."""

    receipt: DispositionReceipt
    valid: bool
    active: bool
    consumable: bool
    reason: str

    @property
    def accepted(self) -> bool:
        return self.consumable

    @property
    def finding_id(self) -> str:
        return self.receipt.finding_id

    @property
    def evidence_manifest_digest(self) -> str:
        return self.receipt.evidence_manifest_digest


@dataclass(frozen=True)
class DispositionConsumption:
    """Pure result of applying protected dispositions to one primary audit."""

    remaining_p1_ids: tuple[str, ...]
    consumed_receipts: tuple[DispositionReceipt, ...]
    rejected_receipts: tuple[tuple[DispositionReceipt, str], ...]
    fail_closed: bool
    statuses: tuple[DispositionStatus, ...] = ()
    malformed_inputs: tuple[Any, ...] = ()

    @property
    def p1_ids(self) -> tuple[str, ...]:
        return self.remaining_p1_ids

    @property
    def consumed(self) -> tuple[DispositionReceipt, ...]:
        return self.consumed_receipts

    @property
    def rejected(self) -> tuple[tuple[DispositionReceipt, str], ...]:
        return self.rejected_receipts


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
    event_records: tuple[tuple[tuple[Any, ...], str, tuple[Any, ...]], ...] = ()
    reason: str = ""

    @property
    def decision(self) -> str:
        return self.terminal_decision

    @property
    def consumed_processing_keys_digest(self) -> str:
        return self.processing_keys_digest

    @property
    def consumed_round_keys_digest(self) -> str:
        return self.round_keys_digest

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


def disposition_receipt_digest(receipt: DispositionReceipt) -> str:
    """Return the digest over the receipt's binding fields, excluding itself."""

    if not isinstance(receipt, DispositionReceipt):
        raise TypeError("receipt must be DispositionReceipt")
    return _sha256(receipt.as_dict(include_digest=False))


def _disposition_status(
    receipt: DispositionReceipt,
    *,
    valid: bool,
    active: bool,
    consumable: bool,
    reason: str,
) -> DispositionStatus:
    return DispositionStatus(
        receipt=receipt,
        valid=valid,
        active=active,
        consumable=consumable,
        reason=reason,
    )


def _legacy_disposition_stub(receipt: DispositionReceipt) -> bool:
    return (
        isinstance(receipt, DispositionReceipt)
        and not receipt.valid
        and receipt.schema_version == RECEIPT_SCHEMA_VERSION
        and receipt.disposition == "none"
        and receipt.repository_id == ""
        and receipt.pr_number == 0
        and receipt.epoch == ""
        and receipt.head_sha == ""
        and receipt.diff_digest == ""
        and receipt.primary_run_id == ""
        and receipt.primary_run_attempt == 0
        and receipt.audit_digest == ""
        and receipt.finding_id == ""
        and receipt.issuer_login == ""
        and receipt.issuer_user_id == ""
        and receipt.control_run_id == ""
        and receipt.approval_ref == ""
        and receipt.issued_at == ""
        and receipt.expires_at == ""
        and receipt.nonce == ""
        and receipt.evidence_manifest_digest == ""
        and receipt.receipt_digest == ""
        and receipt.scope is None
    )


def _utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _sha256_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _revocation_error(revocations: Sequence[DispositionRevocation], nonce: str) -> str | None:
    if not isinstance(revocations, Sequence):
        return "malformed_revocation_index"
    for revocation in revocations:
        if not isinstance(revocation, DispositionRevocation):
            return "malformed_revocation"
        if revocation.schema_version != DISPOSITION_REVOCATION_SCHEMA_VERSION:
            return "malformed_revocation"
        if not all(
            _nonempty_text(getattr(revocation, field))
            for field in ("nonce", "reason", "actor", "revoked_at", "evidence_ref")
        ):
            return "malformed_revocation"
        if revocation.nonce != nonce:
            continue
        if _utc_timestamp(revocation.revoked_at) is None:
            return "malformed_revocation"
        return "revoked"
    return None


def validate_disposition_receipt(
    receipt: DispositionReceipt,
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    now: str,
    revocations: Sequence[DispositionRevocation],
) -> DispositionStatus:
    """Validate one protected receipt without using exceptions as control flow."""

    if not isinstance(receipt, DispositionReceipt):
        return _disposition_status(
            DispositionReceipt(), valid=False, active=False, consumable=False,
            reason="malformed_receipt",
        )
    if _legacy_disposition_stub(receipt):
        return _disposition_status(
            receipt, valid=False, active=False, consumable=False,
            reason="absent_legacy_stub",
        )
    if not isinstance(scope, Scope) or _scope_errors(scope):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_scope")
    if not isinstance(primary, CanonicalPrimary):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_primary")
    if not isinstance(now, str) or _utc_timestamp(now) is None:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_now")
    if not isinstance(revocations, Sequence):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_revocation_index")
    if receipt.schema_version != DISPOSITION_RECEIPT_SCHEMA_VERSION:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="schema_version_mismatch")
    if receipt.disposition not in DISPOSITION_KINDS:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="unknown_disposition")
    required_text = (
        "repository_id", "epoch", "head_sha", "diff_digest", "primary_run_id",
        "audit_digest", "finding_id", "control_run_id", "issued_at", "expires_at", "nonce",
        "evidence_manifest_digest", "receipt_digest",
    )
    if any(not _nonempty_text(getattr(receipt, field)) for field in required_text):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_receipt")
    if type(receipt.pr_number) is not int or receipt.pr_number <= 0:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_pr_number")
    if type(receipt.primary_run_attempt) is not int or receipt.primary_run_attempt <= 0:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_primary_attempt")
    if receipt.repository_id != str(scope.repository_id):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="repository_mismatch")
    if receipt.pr_number != scope.pr_number:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="pr_mismatch")
    receipt_scope = receipt.scope.as_dict() if isinstance(receipt.scope, Scope) else receipt.scope
    if not isinstance(receipt_scope, Mapping) or dict(receipt_scope) != scope.as_dict():
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="scope_mismatch")
    expected_epoch = derive_epoch(scope)
    if receipt.epoch != expected_epoch:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="epoch_mismatch_stale")
    if receipt.head_sha != scope.head_sha:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="head_sha_mismatch")
    if receipt.diff_digest != scope.diff_digest:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="diff_digest_mismatch")
    if receipt.primary_run_id != str(primary.run_id):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="primary_run_id_mismatch")
    if receipt.primary_run_attempt != primary.run_attempt:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="primary_run_attempt_mismatch")
    if not _sha256_text(audit_digest) or receipt.audit_digest != audit_digest:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="audit_digest_mismatch")
    if receipt.finding_id in {"*", "all"} or any(character in receipt.finding_id for character in "?[]"):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="finding_target_not_exact")
    if receipt.finding_id not in primary.p1_ids:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="finding_not_current_p1")
    if not _sha256_text(receipt.diff_digest) or not _sha256_text(receipt.evidence_manifest_digest):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="evidence_manifest_malformed")
    if not _nonempty_text(receipt.issuer_login) or not _nonempty_text(receipt.issuer_user_id) or not _nonempty_text(receipt.approval_ref):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="issuer_unprotected")
    issued = _utc_timestamp(receipt.issued_at)
    expires = _utc_timestamp(receipt.expires_at)
    current = _utc_timestamp(now)
    if issued is None or expires is None or current is None or expires <= issued:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="malformed_timestamps")
    if issued > current:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="issued_in_future")
    if current >= expires:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="expired")
    revocation = _revocation_error(revocations, receipt.nonce)
    if revocation == "revoked":
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="revoked")
    if revocation is not None:
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason=revocation)
    if receipt.receipt_digest != disposition_receipt_digest(receipt):
        return _disposition_status(receipt, valid=False, active=False, consumable=False, reason="receipt_digest_mismatch")
    if receipt.disposition != "false-positive":
        return _disposition_status(receipt, valid=True, active=True, consumable=False, reason="disposition_not_gate_resolving")
    return _disposition_status(receipt, valid=True, active=True, consumable=True, reason="active_false_positive")


def disposition_status(
    receipt: DispositionReceipt,
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    now: str,
    revocations: Sequence[DispositionRevocation],
) -> DispositionStatus:
    """Return the observational status view used by ledger/human summaries."""

    return validate_disposition_receipt(
        receipt,
        scope=scope,
        primary=primary,
        audit_digest=audit_digest,
        now=now,
        revocations=revocations,
    )


_DISPOSITION_FAIL_CLOSED_REASONS = frozenset(
    {
        "malformed_receipt", "schema_version_mismatch", "unknown_disposition",
        "malformed_pr_number", "malformed_primary_attempt", "repository_mismatch",
        "pr_mismatch", "epoch_mismatch_stale", "head_sha_mismatch",
        "diff_digest_mismatch", "primary_run_id_mismatch",
        "primary_run_attempt_mismatch", "audit_digest_mismatch",
        "finding_target_not_exact", "finding_not_current_p1",
        "evidence_manifest_malformed", "issuer_unprotected", "malformed_timestamps",
        "issued_in_future", "receipt_digest_mismatch", "malformed_scope",
        "malformed_primary", "malformed_now", "malformed_revocation_index",
        "malformed_revocation", "nonce_conflict",
        "scope_mismatch",
    }
)


def _current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def consume_dispositions(
    p1_ids: Sequence[str],
    receipts: Sequence[DispositionReceipt],
    *,
    scope: Scope,
    primary: CanonicalPrimary,
    audit_digest: str,
    now: str,
    revocations: Sequence[DispositionRevocation],
) -> DispositionConsumption:
    """Apply only active exact false-positive receipts to this P1 projection."""

    if not isinstance(p1_ids, Sequence) or isinstance(p1_ids, (str, bytes)):
        return DispositionConsumption((), (), (), True, ())
    remaining = list(p1_ids)
    if any(not _nonempty_text(finding_id) for finding_id in remaining):
        return DispositionConsumption(tuple(remaining), (), (), True, ())
    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)):
        return DispositionConsumption(tuple(remaining), (), (), True, ())
    statuses: list[DispositionStatus] = []
    consumed: list[DispositionReceipt] = []
    rejected: list[tuple[DispositionReceipt, str]] = []
    malformed_inputs: list[Any] = []
    fail_closed = False
    by_nonce: dict[str, DispositionReceipt] = {}
    seen_payloads: set[str] = set()
    for receipt in receipts:
        if not isinstance(receipt, DispositionReceipt):
            status = validate_disposition_receipt(
                receipt, scope=scope, primary=primary, audit_digest=audit_digest,
                now=now, revocations=revocations,
            )
            statuses.append(status)
            rejected.append((status.receipt, status.reason))
            malformed_inputs.append(receipt)
            fail_closed = True
            continue
        if _legacy_disposition_stub(receipt):
            statuses.append(_disposition_status(receipt, valid=False, active=False, consumable=False, reason="absent_legacy_stub"))
            continue
        payload_signature = json.dumps(receipt.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if receipt.nonce in by_nonce:
            prior = by_nonce[receipt.nonce]
            prior_signature = json.dumps(prior.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if payload_signature != prior_signature:
                rejected.append((receipt, "nonce_conflict"))
                fail_closed = True
                statuses.append(_disposition_status(receipt, valid=False, active=False, consumable=False, reason="nonce_conflict"))
            else:
                statuses.append(_disposition_status(receipt, valid=True, active=True, consumable=False, reason="duplicate_nonce_noop"))
            continue
        if receipt.nonce:
            by_nonce[receipt.nonce] = receipt
        if payload_signature in seen_payloads:
            statuses.append(_disposition_status(receipt, valid=True, active=True, consumable=False, reason="duplicate_receipt_noop"))
            continue
        seen_payloads.add(payload_signature)
        status = validate_disposition_receipt(
            receipt, scope=scope, primary=primary, audit_digest=audit_digest,
            now=now, revocations=revocations,
        )
        statuses.append(status)
        if status.consumable:
            if receipt.finding_id in remaining:
                remaining.remove(receipt.finding_id)
                consumed.append(receipt)
            else:
                rejected.append((receipt, "finding_already_consumed"))
        elif status.reason != "absent_legacy_stub":
            rejected.append((receipt, status.reason))
            if status.reason in _DISPOSITION_FAIL_CLOSED_REASONS:
                fail_closed = True
    return DispositionConsumption(
        remaining_p1_ids=tuple(remaining),
        consumed_receipts=tuple(consumed),
        rejected_receipts=tuple(rejected),
        fail_closed=fail_closed,
        statuses=tuple(statuses),
        malformed_inputs=tuple(malformed_inputs),
    )


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


def policy_for(scope: Scope) -> Policy:
    """Resolve the frozen tier matrix for a validated Scope."""

    validate_scope(scope)
    clean, maximum = _POLICY_BY_TIER[scope.effective_tier]
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
    now: str | None = None,
    revocations: Sequence[DispositionRevocation] = (),
) -> RoundDecision:
    """Consume exactly one canonical primary observation.

    A clean round is defined solely by an eligible canonical primary whose
    current P1 projection is empty after protected disposition consumption.
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
    if not isinstance(waiver_receipts, Sequence) or any(not isinstance(receipt, DispositionReceipt) for receipt in waiver_receipts):
        failed = _fail_closed_state(state, "waiver receipts have an invalid shape")
        return _decision(failed, processing_key=processing_key, round_key=RoundKey(epoch, primary.run_id, audit_digest), event_id="", reason=failed.reason, accepted=False, no_op=False)
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

    disposition_result = consume_dispositions(
        primary.p1_ids,
        waiver_receipts,
        scope=scope,
        primary=primary,
        audit_digest=audit_digest,
        now=now or _current_utc_iso(),
        revocations=revocations,
    )
    if disposition_result.fail_closed:
        reasons = ", ".join(reason for _, reason in disposition_result.rejected_receipts)
        failed = _fail_closed_state(working, f"invalid protected disposition: {reasons or 'malformed_receipt'}")
        return _decision(
            failed,
            processing_key=processing_key,
            round_key=round_key,
            event_id=event_id,
            reason=failed.reason,
            accepted=False,
            no_op=False,
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
            verdict=primary.verdict, p1_ids=disposition_result.remaining_p1_ids, effect="terminal",
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
        verdict=primary.verdict, p1_ids=disposition_result.remaining_p1_ids,
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
    streak = recorded.clean_streak + 1 if not disposition_result.remaining_p1_ids else 0
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
