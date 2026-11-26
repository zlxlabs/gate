"""Shared immutable contracts and canonicalization for gate convergence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

def _load_sibling(canonical_name: str, filename: str) -> Any:
    module = sys.modules.get(canonical_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            canonical_name, Path(__file__).with_name(filename)
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"{filename} contract is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[canonical_name] = module
        spec.loader.exec_module(module)
    return module


_GATE_ACTION = _load_sibling("gate_action_from_convergence", "gate_action.py")
_COUNTEREVIDENCE_ARGV = _load_sibling(
    "gate_counterevidence_argv_contract", "counterevidence_argv.py"
)

SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
P1_SEVERITIES = _GATE_ACTION.P1_SEVERITIES
KNOWN_SEVERITIES = _GATE_ACTION.KNOWN_SEVERITIES
# Replay anchors (design §4.2): 187↔190 / 116↔118 / 37↔40. |Δline|<=4 covers
# those pairs; |Δline|==5 must not match. Category is not part of neighbor identity.
NEIGHBOR_LINE_TOLERANCE = 4
# Strategy/identity fields that still bind a receipt across heads. Code-changing
# Scope fields (base_sha, head_sha, diff_digest, caller_sha, reusable_workflow_sha)
# are excluded: a new commit is gated by the anchor window and current-head
# rerun, not by epoch (§8 revision 1: policy change voids a receipt, code change
# does not).
NEIGHBOR_POLICY_SCOPE_FIELDS = (
    "repository_id",
    "pr_number",
    "policy_version",
    "policy_digest",
    "tier",
)
SUPPORTED_TIERS = _GATE_ACTION.SUPPORTED_TIERS
PRIMARY_VERDICTS = frozenset({"pass", "fail", "unavailable"})
TERMINAL_DECISIONS = frozenset(
    {"collecting", "converged", "manual_required", "fail_closed"}
)
RECEIPT_KIND = "canonical_primary"
DISPOSITION_KINDS = frozenset({"false-positive", "deferred", "rejected"})  # + "rejected" per gate-hub design 261122 §8.9: sol_verify origin only, owner-protected categories refused at issuance
DISPOSITION_RECEIPT_SCHEMA_VERSION = 3
DISPOSITION_RECEIPT_KIND = f"gate-disposition-receipt-v{DISPOSITION_RECEIPT_SCHEMA_VERSION}"
# gate-hub#1072 ruling 4(a): a deferred receipt may not release a P1 at any
# tier — issuance and consumption share this single reason-code constant.
DEFERRED_RECEIPT_REJECT_REASON = "deferred_not_allowed_for_tier"
DISPOSITION_REASON_DISPLAY_MAX = 500
COUNTEREVIDENCE_RERUN_TIMEOUT_S: int = 30
# Locked contract for disposition audit_digest (gate#90). Adding or removing a
# field must update issuer, consumer, and tests together.
CANONICAL_AUDIT_DIGEST_SCOPE_FIELDS = (
    "base_sha",
    "caller_sha",
    "diff_digest",
    "head_sha",
    "policy_digest",
    "policy_version",
    "pr_number",
    "repository_id",
    "reusable_workflow_sha",
    "tier",
)
CANONICAL_AUDIT_DIGEST_FINDING_FIELDS = ("file", "line", "category", "severity")
# Finding-level blocking-matrix contract v1 (gate-hub producer ⇄ gate
# aggregator): gate_action values a receipt may release. "comment" findings
# never need a receipt; "block" accepts only false-positive; "must_respond"
# accepts false-positive, deferred or rejected (rejected: sol_verify origin only).
_canonical_json = _GATE_ACTION.canonical_json
canonical_finding_key = _GATE_ACTION.canonical_finding_key
_primary_finding_record = _GATE_ACTION.primary_finding_record
_stable_primary_matches = _GATE_ACTION.stable_primary_matches
gate_coverable = _GATE_ACTION.gate_coverable
coverable_matches = _GATE_ACTION.coverable_matches
gate_coverable_error = _GATE_ACTION.gate_coverable_error
deferred_gate_rejected = _GATE_ACTION.deferred_gate_rejected
GATE_ACTION_ISSUANCE_ERROR = _GATE_ACTION.GATE_ACTION_ISSUANCE_ERROR
COUNTEREVIDENCE_ARGV_CONTRACT_VERSION = _COUNTEREVIDENCE_ARGV.COUNTEREVIDENCE_ARGV_CONTRACT_VERSION
COUNTEREVIDENCE_ARGV_TEMPLATE = _COUNTEREVIDENCE_ARGV.COUNTEREVIDENCE_ARGV_TEMPLATE
counterevidence_argv_reason = _COUNTEREVIDENCE_ARGV.counterevidence_argv_reason

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
            "caller_sha": self.caller_sha,
            "reusable_workflow_sha": self.reusable_workflow_sha,
        }


@dataclass(frozen=True)
class Policy:
    tier: str
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

    ``p1_findings`` carries either the legacy v2
    ``(id, severity, trigger_kind)`` tuple or the stable-key projection
    ``(id, severity, trigger_kind, file, line, category)`` for each P1.  A
    disposition can consume only an ``inferred`` tuple; missing or unknown
    trigger kinds remain blocking.  The six-field form is required when a
    stable-key disposition is validated.
    """

    schema_version: int
    repository_id: int
    pr_number: int
    head_sha: str
    run_id: int
    run_attempt: int
    verdict: str
    p1_ids: tuple[str, ...] = ()
    p1_findings: tuple[tuple[Any, ...], ...] = ()

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
            "p1_findings": [list(item) for item in self.p1_findings],
        }
@dataclass(frozen=True)
class DispositionReceipt:
    """Immutable disposition bound to one current canonical audit round.

    ``finding_key`` is populated by the stable-key format and is the binding
    target. ``finding_id`` remains the human audit id in new receipts. An
    empty ``finding_key`` identifies the in-flight v2 format, whose
    ``finding_id`` remains the legacy target until its artifact window expires.
    """

    schema_version: int = DISPOSITION_RECEIPT_SCHEMA_VERSION
    disposition: str = "none"
    repository_id: str = ""
    pr_number: int = 0
    epoch: str = ""
    head_sha: str = ""
    audit_digest: str = ""
    finding_id: str = ""
    finding_key: str = ""
    reason: str = ""
    approver: str = ""
    approver_id: int = 0
    approved_at: str = ""
    triggering_actor: str = ""
    triggering_actor_source: str = ""
    counterevidence: dict[str, Any] | None = None
    tracking_issue: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "disposition": self.disposition,
            "repository_id": self.repository_id,
            "pr_number": self.pr_number,
            "epoch": self.epoch,
            "head_sha": self.head_sha,
            "audit_digest": self.audit_digest,
            "finding_id": self.finding_id,
            "finding_key": self.finding_key,
            "reason": self.reason,
            "approver": self.approver,
            "approver_id": self.approver_id,
            "approved_at": self.approved_at,
            "triggering_actor": self.triggering_actor,
            "triggering_actor_source": self.triggering_actor_source,
        }
        if self.counterevidence is not None:
            payload["counterevidence"] = self.counterevidence
        if self.tracking_issue is not None:
            payload["tracking_issue"] = self.tracking_issue
        return payload
@dataclass(frozen=True)
class NeighborEvidence:
    """Caller-injected facts for neighbor consumption after head/digest drift.

    Field names are a locked contract with the wiring card. This module never
    collects these values; omitted evidence is a no-op (existing reasons).
    """

    current_head_sha: str
    anchor_line_current: int
    anchor_window_touched: bool
    rerun_argv: list[str]
    rerun_exit_code: int
    rerun_match_count: int
    receipt_scope: Scope
@dataclass(frozen=True)
class DispositionStatus:
    """Receipt claim diagnostics; ``active`` means current-target match, never authorization."""

    receipt: DispositionReceipt
    valid: bool
    active: bool
    reason: str
    reason_code: str = ""
    matched_by: str = ""
    audit_digest_drift: bool = False
    matched_finding_id: str = ""

    @property
    def finding_id(self) -> str:
        return self.receipt.finding_id

    @property
    def message(self) -> str:
        """Return the actionable diagnostic text (same value as ``reason``)."""

        return self.reason
@dataclass(frozen=True)
class DispositionAudit:
    """Read-only audit of receipt claims; it never changes primary findings."""

    primary_p1_ids: tuple[str, ...]
    recorded_receipts: tuple[DispositionReceipt, ...]
    rejected_receipts: tuple[tuple[DispositionReceipt, str], ...]
    statuses: tuple[DispositionStatus, ...] = ()
    recorded_finding_ids: tuple[str, ...] = ()
    remaining_p1_ids: tuple[str, ...] = ()
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
    p1_findings: tuple[tuple[Any, ...], ...] = ()
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
            "p1_findings": [list(item) for item in self.p1_findings],
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
    remaining_p1_ids: tuple[str, ...] = ()

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
def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()
def neighbor_finding_key(finding: Any) -> tuple[str, int] | None:
    """Return ``(file, line)`` when ``finding`` may join neighbor identity.

    Two findings are neighbor-identical iff both yield a key, the files are
    equal, and ``abs(Δline) <= NEIGHBOR_LINE_TOLERANCE``. Category is not
    consulted. Non-int / missing / null ``line``, empty ``file``, or a
    non-P1 severity make the finding ineligible (returns ``None``).
    """

    if not isinstance(finding, dict):
        return None
    file = finding.get("file")
    line = finding.get("line")
    if not isinstance(file, str) or not file:
        return None
    if type(line) is not int:
        return None
    if finding.get("severity") not in P1_SEVERITIES:
        return None
    return (file, line)
def _decode_canonical_finding_key(finding_key: str) -> dict[str, Any] | None:
    """Invert ``canonical_finding_key`` tagged JSON into a finding dict."""

    try:
        payload = json.loads(finding_key)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    decoded: dict[str, Any] = {}
    for field in _GATE_ACTION.CANONICAL_FINDING_KEY_FIELDS:
        tagged = payload.get(field)
        if not isinstance(tagged, list) or not tagged:
            continue
        tag = tagged[0]
        if tag == "missing" and len(tagged) == 1:
            continue
        if tag == "null" and len(tagged) == 1:
            decoded[field] = None
        elif tag == "value" and len(tagged) == 2:
            decoded[field] = tagged[1]
        else:
            return None
    return decoded
def _canonical_finding_sort_key(item: dict[str, Any]) -> str:
    return canonical_finding_key(item)
def canonical_audit_binding(audit: Any) -> dict[str, Any]:
    """Project the stable audit subset hashed into disposition audit_digest.

    Runtime noise (duration, tokens, timestamps, run/attempt, reviewer) is
    excluded so the same finding set yields the same digest across reruns.
    """

    if not isinstance(audit, dict):
        raise ValueError("canonical audit digest requires an object")
    findings_raw: list[Any] = []
    result = audit.get("result")
    if isinstance(result, dict) and isinstance(result.get("findings"), list):
        findings_raw = result["findings"]
    findings: list[dict[str, Any]] = []
    for finding in findings_raw:
        if not isinstance(finding, dict):
            continue
        findings.append({field: finding.get(field) for field in CANONICAL_AUDIT_DIGEST_FINDING_FIELDS})
    findings.sort(key=_canonical_finding_sort_key)
    pr_number = audit["pr_number"] if "pr_number" in audit else audit.get("pr")
    scope = {field: audit.get(field) for field in CANONICAL_AUDIT_DIGEST_SCOPE_FIELDS}
    scope["pr_number"] = pr_number
    return {
        "findings": findings,
        "scope": scope,
        "verdict": audit.get("verdict"),
    }
def canonical_audit_digest(audit: Any) -> str:
    """SHA-256 of canonical_audit_binding via _canonical_json. Single definition."""

    return _sha256(canonical_audit_binding(audit))
def _receipt_audit_digest_matches(
    receipt_digest: str,
    audit_digest: str,
    legacy_raw_audit_digest: str | None,
) -> bool:
    if _audit_digest_errors(audit_digest) or _audit_digest_errors(receipt_digest):
        return False
    if receipt_digest == audit_digest:
        return True
    # Expand-then-contract: also accept the pre-gate#90 raw-bytes digest of the
    # current audit file so in-flight receipts are not voided on deploy.
    # TODO(gate#90): drop this legacy match after no remaining
    # gate-disposition-receipt-v1 artifact still carries sha256(file bytes)
    # (reissue after this SHA is pinned, or the 30-day artifact TTL elapses).
    return (
        legacy_raw_audit_digest is not None
        and not _audit_digest_errors(legacy_raw_audit_digest)
        and receipt_digest == legacy_raw_audit_digest
    )
def _current_p1_keys(primary: CanonicalPrimary) -> tuple[str, ...]:
    """List current P1 stable keys, or legacy ids for a v2 projection."""

    if isinstance(primary.p1_findings, tuple):
        keys = []
        for finding in primary.p1_findings:
            record = _primary_finding_record(finding)
            if record is not None and record.get("severity") in P1_SEVERITIES and len(finding) == 6:
                keys.append(canonical_finding_key(record))
        if keys:
            return tuple(keys)
    return tuple(primary.p1_ids)
def narrow_stable_key_matches_by_id(
    matches: list[tuple[str, Any]],
    finding_id: str,
) -> list[tuple[str, Any]]:
    """Narrow same-key P1 matches with the receipt's exact finding id (gate#240).

    The stable finding key stays the disposition binding target.  Only when
    the key matches several current P1s does the caller's exact finding_id
    take part: hitting exactly one of them disambiguates the target, and any
    other outcome keeps the full match set so callers fail closed with
    finding_key_ambiguous.  A single match is returned untouched so a receipt
    stays consumable when reruns drift the finding id.
    """

    if len(matches) <= 1:
        return matches
    id_matches = [match for match in matches if match[0] == finding_id]
    if len(id_matches) == 1:
        return id_matches
    return matches
def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)
def _approved_at_has_time(value: Any) -> bool:
    """ISO-8601 approval instant; a bare date is not a timestamp (hub waiver rule)."""

    if not isinstance(value, str) or not value:
        return False
    if "T" not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return False
    return True
def _bounded_disposition_text(value: Any) -> str:
    collapsed = " ".join(str(value).split())
    if len(collapsed) > DISPOSITION_REASON_DISPLAY_MAX:
        return collapsed[:DISPOSITION_REASON_DISPLAY_MAX]
    return collapsed
def _audit_digest_errors(audit_digest: str) -> list[str]:
    if not isinstance(audit_digest, str) or len(audit_digest) != 64:
        return ["audit_digest must be a 64-character SHA-256 hex digest"]
    try:
        int(audit_digest, 16)
    except ValueError:
        return ["audit_digest must contain only hexadecimal characters"]
    return []
