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
