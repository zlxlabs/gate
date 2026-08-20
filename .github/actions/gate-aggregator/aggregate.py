#!/usr/bin/env python3
"""gate-aggregator — decision core for the Required Gate v2 final `gate` job.

Implements the "gate:" node of the Required Gate state machine (see the private
gate-hub repo's ceo-plans/2026-07-24-shadow-review-independence.md, "Required
Gate" + "Fork, waiver and notification semantics" sections). Per that plan's
"Caller / reusable workflow boundary", this aggregator is a portable script
that depends ONLY on python3 stdlib + data the calling workflow hands it — no
gate-hub import, no hosted-image-specific tool. The one network call it may
make is the optional Stage 4 PR-comment receipt (one fail-open issue-comment
POST via stdlib urllib for the marker-located status panel); it never
feeds back into the verdict or the exit code. When the caller supplies
`--panel-delivery-path`, the result of that attempt is also persisted as a
separate, versioned durable receipt artifact; this artifact is evidence for
consumers, not gate state. It is invoked as a
plain `python3 aggregate.py ...` step (see .github/workflows/gate-v2.yml's
`gate` job) rather than wrapped in an `action.yml` composite action: GitHub
Actions' `uses:` keyword cannot itself take an expression, so the ONLY way to
pin this script to the exact commit the reusable workflow file itself is
running from (the `job` context's `job.workflow_sha` — a separate context
from `github.*`; see GitHub's Contexts reference doc) is to check that ref
out explicitly and then invoke the checked-out file directly via `run:` — a `uses:
zlxlabs/gate/.github/actions/gate-aggregator@main` reference would instead
float independently of whatever SHA a canary caller has pinned for
gate-v2.yml, defeating the "pin to a reviewed SHA during canary" governance
model ("Caller / reusable workflow boundary" in the plan).

Issue #51 second-exit design: retain the existing fail-open PR-comment
semantics and add a durable `gate_v2_status_panel_delivery` JSON artifact at the
workflow boundary. The artifact records whether the comment was created and,
when it was not, a stable reason category plus HTTP status where available. A
transport failure after the POST was attempted is recorded as
`delivery=unknown`, never as a definite `not_created`, because the server may
already have created the comment before the response was lost.
The reusable workflow uploads it with `if: always()` and
`if-no-files-found: error`. A consumer must distinguish three receipt states:
missing artifact means the write failed and stale content was cleared;
an artifact that is not parseable means the write failed and old evidence was
poisoned; a parseable old JSON artifact means cleanup also failed and the
`receipt channel is untrusted` warning is the deciding signal. Successful
comment delivery adds no annotation or second notification; it only records
the quiet machine-readable receipt.

This module intentionally duplicates (does NOT import) two small pieces of
gate-hub's scripts/review/contracts.py: the primary-verdict domain and the
identity-quintuple field names. There is deliberately no shared package
between the public zlxlabs/gate repo and the private gate-hub repo (see the
plan's Code boundary / Caller boundary sections) — if contracts.py's
IDENTITY_FIELDS or PRIMARY_VERDICTS ever change shape, this file must be
updated by hand, and the two repos' contract tests are the thing that would
catch a silent drift, not a runtime import.

Judgement responsibilities (see gate-v2.yml's `gate` job and this repo's
tests/test_gate_aggregator.py for the full decision matrix):
  - `runner` must be a recognized value (`self`/`hosted`); anything else fails
    closed immediately rather than silently falling through whatever branch a
    typo happens to resemble.
  - quality must be `success`; anything else fails the gate.
  - primary's job `result` must itself be a recognized value (`success`,
    `failure`, `cancelled`, `skipped`); an unrecognized string fails closed
    rather than being treated as any particular case.
  - primary `skipped` is only accepted when the PR is a draft, or when review
    was not expected at all (fork PR / `runner: hosted` / any future
    non-review policy) — an unexplained skip on a non-draft, same-repo,
    `runner: self` PR is never treated as a pass.
  - primary `cancelled` always fails closed and produces a synthetic audit
    (status `job_timed_out`).
  - primary `success`/`failure` must have a valid, identity-matched canonical
    audit artifact (exact types — not just values — including rejecting a
    Python-bool-as-int in any identity integer field); a missing/corrupt/
    mismatched artifact fails closed and produces a synthetic audit (status
    `artifact_missing`).
  - a canonical audit's verdict `pass`/`fail`/`unavailable` maps directly, and
    `pass` is ONLY accepted when the primary job's own result is `success`
    (an audit claiming pass while the job result says otherwise is treated as
    an inconsistency and fails closed, never trusted over the job's own
    conclusion).
  - `not_expected`/`waived` are ALWAYS REJECTED, unconditionally, in this PR:
    canary-stage primary jobs never legitimately write either verdict yet
    (fork/hosted PRs skip the whole `primary` job instead — see gate-v2.yml's
    primary job comment), so seeing one at all is itself an anomaly. T6: a
    later PR that wires up a real not_expected/waived writer must ALSO
    implement gate-hub contracts.py-equivalent companion-field validation
    here (not_expected_reason checked against the real NOT_EXPECTED_REASONS
    enum domain, waiver.approved_at checked for an actual ISO-8601
    time-of-day component — not merely "is a non-empty string") before this
    aggregator may accept either verdict; until that lands, both are treated
    exactly like an invalid verdict.
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

GATE_ROOT = Path(__file__).resolve().parents[3]
if str(GATE_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE_ROOT))

from scripts.scrub_outbound import runtime_values_from_environment, scrub_for_publish


_CONVERGENCE_SPEC = importlib.util.spec_from_file_location(
    "gate_convergence_from_aggregate",
    Path(__file__).with_name("convergence.py"),
)
assert _CONVERGENCE_SPEC and _CONVERGENCE_SPEC.loader
_CONVERGENCE = importlib.util.module_from_spec(_CONVERGENCE_SPEC)
sys.modules[_CONVERGENCE_SPEC.name] = _CONVERGENCE
_CONVERGENCE_SPEC.loader.exec_module(_CONVERGENCE)


# Mirrors gate-hub's scripts/review/contracts.py PRIMARY_VERDICTS / IDENTITY_FIELDS
# (see module docstring for why this is a hand-kept mirror, not an import).
PRIMARY_VERDICT_DOMAIN = ("pass", "fail", "unavailable", "not_expected", "waived")
IDENTITY_QUINTUPLE = ("repository_id", "head_sha", "run_id", "run_attempt", "pr")
_IDENTITY_INT_FIELDS = ("repository_id", "run_id", "run_attempt", "pr")

# The exact `needs.primary.result` / `needs.quality.result` values GitHub
# Actions can produce for a job. Anything outside this set fails closed
# immediately rather than being funneled into whichever branch it happens to
# fall through to.
PRIMARY_RESULT_DOMAIN = ("success", "failure", "cancelled", "skipped")
QUALITY_RESULT_DOMAIN = PRIMARY_RESULT_DOMAIN
TERMINAL_CLASSIFICATION_DOMAIN = ("code_pass", "code_fail", "expected_skip", "review_unavailable", "ci_failure", "integration_error")
TERMINAL_REASON_DOMAIN = ("primary_pass", "primary_findings", "review_not_expected", "primary_unavailable", "primary_cancelled", "quality_failure", "quality_cancelled", "quality_skipped", "audit_missing", "audit_invalid", "audit_source_mismatch", "job_audit_mismatch", "unexpected_primary_skip")
GATE_RESULT_DOMAIN = ("pass", "fail", "skipped", "unavailable")
PANEL_DELIVERY_SCHEMA_VERSION = 1
PANEL_DELIVERY_KIND = "gate_v2_status_panel_delivery"
CONVERGENCE_ENVELOPE_SCHEMA_VERSION = 1
CONVERGENCE_ENVELOPE_KIND = "gate_convergence_round"
GITHUB_API_TIMEOUT_SECONDS = 15
DEFAULT_PUBLISH_BUDGET_SECONDS = 120
PUBLISH_BUDGET_ENV = "GATE_PUBLISH_BUDGET_SECONDS"
DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS = 45
HISTORY_RECONSTRUCTION_BUDGET_ENV = "GATE_HISTORY_RECONSTRUCTION_BUDGET_SECONDS"
MAX_REPO_WIDE_HISTORY_PAGES = 5
MAX_TARGETED_HISTORY_RUNS = 50
MAX_HISTORY_WARNING_CHARS = 500
PUBLISH_OPERATION_ORDER = (
    "IDENTITY",
    "COMMENT_LOOKUP",
    "HISTORY_RECONSTRUCTION",
    "COMMENT_PUBLISH",
    "POST_VERIFY",
    "SELF_HEAL",
)
PANEL_MARKER = "<!-- gate-v2-status-panel:v1 -->"
PANEL_HISTORY_ROW_SCHEMA_VERSION = 1
ACTIONS_BOT_ID = 41898282
ACTIONS_BOT_LOGIN = "github-actions[bot]"
PANEL_BUCKET_BY_GATE_RESULT = {
    "pass": "可合并",
    "fail": "要修代码",
    "skipped": "无需动作",
    "unavailable": "修基础设施",
}

# The `runner` reusable-workflow input's only two legal values (see
# gate-v2.yml's `inputs.runner`). A typo (e.g. "slef") must never be silently
# treated as either value.
RUNNER_DOMAIN = ("self", "hosted")

# The two statuses gate-hub's contracts.build_synthetic_primary accepts
# (SYNTHETIC_STATUSES) — this aggregator never invents a third one.
SYNTHETIC_STATUS_TIMED_OUT = "job_timed_out"
SYNTHETIC_STATUS_ARTIFACT_MISSING = "artifact_missing"


class BoolParseError(ValueError):
    """Raised by `as_bool` for anything other than a GitHub Actions boolean
    rendering ('true'/'false', case/whitespace normalized). GitHub Actions
    always renders a boolean expression as exactly one of those two strings;
    anything else means something upstream is already broken (a workflow YAML
    edit, a manual invocation, a runtime bug) and must fail closed rather than
    silently coerce to False — `"banana" == "true"` evaluating to False is
    exactly the silent-coercion trap this class exists to prevent callers from
    falling into.
    """


def as_bool(value: Any) -> bool:
    """Strictly parse a GitHub Actions boolean-expression string.

    Only 'true'/'false' (after `str().strip().lower()`) are accepted; any
    other input raises `BoolParseError` instead of silently defaulting to
    False. See `main()` for how the CLI turns that into a clean fail-closed
    Outcome instead of an unhandled traceback.
    """
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise BoolParseError(f"expected 'true' or 'false' (GitHub Actions boolean rendering), got {value!r}")


def _is_strict_int(value: Any) -> bool:
    """True only for a genuine int. Python's `bool` is an `int` subclass, so
    `isinstance(True, int)` is True — this rejects that trap explicitly: a
    downloaded audit's `run_attempt: true` must be rejected, never silently
    accepted as `1`."""
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class Identity:
    """The identity quintuple the plan requires the aggregator to cross-check
    a downloaded primary audit against, before trusting its verdict. The
    run_attempt member is the current attempt ceiling: an earlier source
    attempt is valid, while a future source attempt is rejected."""

    repository_id: int
    head_sha: str
    run_id: int
    run_attempt: int
    pr: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "head_sha": self.head_sha,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "pr": self.pr,
        }


@dataclass
class Outcome:
    ok: bool
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    synthetic_audit: Optional[dict[str, Any]] = None
    classification: Optional[str] = None
    reason_code: Optional[str] = None
    gate_result: Optional[str] = None
    audit_available: bool = False
    audit_source_attempt: Optional[int] = None
    audit_artifact_name: Optional[str] = None
    convergence_envelope: Optional[dict[str, Any]] = None


@dataclass
class HistoryLoad:
    """Best-effort history projection with per-record loss diagnostics."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped_records: list[dict[str, str]] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)


class _PublishBudgetExhausted(Exception):
    def __init__(self, operation: str):
        self.operation = operation
        super().__init__(f"publish budget exhausted during {operation}")


@dataclass
class _PublishBudget:
    seconds: float
    history_seconds: float = DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS
    started_at: float = field(default_factory=time.monotonic)
    completed_operations: list[str] = field(default_factory=list)
    pending_operations: list[str] = field(default_factory=lambda: list(PUBLISH_OPERATION_ORDER))
    current_operation: Optional[str] = None
    operation_deadline: Optional[float] = field(default=None, init=False)

    @classmethod
    def from_environment(cls) -> "_PublishBudget":
        raw = os.environ.get(PUBLISH_BUDGET_ENV)
        seconds = DEFAULT_PUBLISH_BUDGET_SECONDS if raw is None else float(raw)
        if seconds <= 0:
            raise ValueError(f"{PUBLISH_BUDGET_ENV} must be greater than zero")
        history_raw = os.environ.get(HISTORY_RECONSTRUCTION_BUDGET_ENV)
        history_seconds = (
            DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS
            if history_raw is None else float(history_raw)
        )
        if history_seconds <= 0 or history_seconds > DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS:
            raise ValueError(
                f"{HISTORY_RECONSTRUCTION_BUDGET_ENV} must be between zero and "
                f"{DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS} seconds"
            )
        return cls(seconds=seconds, history_seconds=history_seconds)

    def remaining(self) -> float:
        return self.seconds - (time.monotonic() - self.started_at)

    def timeout(self) -> float:
        remaining = self.remaining()
        if self.current_operation == "HISTORY_RECONSTRUCTION" and self.operation_deadline is not None:
            remaining = min(remaining, self.operation_deadline - time.monotonic())
        if remaining <= 0:
            raise _PublishBudgetExhausted(self.current_operation or "UNKNOWN")
        return min(GITHUB_API_TIMEOUT_SECONDS, remaining)

    def begin(self, operation: str) -> None:
        self.current_operation = operation
        self.operation_deadline = (
            time.monotonic() + self.history_seconds
            if operation == "HISTORY_RECONSTRUCTION" else None
        )
        self.timeout()

    def complete(self, operation: str) -> None:
        if operation not in self.completed_operations:
            self.completed_operations.append(operation)
        if operation in self.pending_operations:
            self.pending_operations.remove(operation)
        if operation == "HISTORY_RECONSTRUCTION":
            self.operation_deadline = None

    def add(self, operation: str) -> None:
        if operation not in self.completed_operations and operation not in self.pending_operations:
            self.pending_operations.append(operation)

    def discard(self, operation: str) -> None:
        if operation in self.pending_operations:
            self.pending_operations.remove(operation)


_ACTIVE_PUBLISH_BUDGET: contextvars.ContextVar[Optional[_PublishBudget]] = contextvars.ContextVar(
    "active_publish_budget", default=None,
)


def build_terminal_envelope(
    *, repository: str, identity: Identity, quality_result: str, primary_result: str, review_expected: bool,
    is_draft: bool, runner: str, outcome: Outcome,
) -> dict[str, Any]:
    """Build the versioned machine-readable gate terminal envelope."""
    if outcome.classification not in TERMINAL_CLASSIFICATION_DOMAIN or outcome.reason_code not in TERMINAL_REASON_DOMAIN or outcome.gate_result not in GATE_RESULT_DOMAIN:
        raise ValueError("terminal field is outside the finite domain")
    return {
        "schema_version": 1, "kind": "gate_terminal", "repository": repository,
        "repository_id": identity.repository_id, "pr_number": identity.pr, "run_id": identity.run_id,
        "run_attempt": identity.run_attempt, "head_sha": identity.head_sha,
        "quality_result": quality_result,
        "primary_result": primary_result,
        "review_expected": review_expected,
        "is_draft": is_draft,
        "runner": runner,
        "gate_result": outcome.gate_result,
        "classification": outcome.classification,
        "reason_code": outcome.reason_code,
        "audit": {"available": outcome.audit_available, "source_attempt": outcome.audit_source_attempt if outcome.audit_available else None, "artifact_name": outcome.audit_artifact_name if outcome.audit_available else None},
    }


def _canonical_p1_ids(audit: dict[str, Any]) -> Optional[tuple[str, ...]]:
    """Project only canonical P1 evidence; never infer severity from prose."""
    result = audit.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
        return None
    p1_ids: list[str] = []
    for finding in result["findings"]:
        if not isinstance(finding, dict):
            return None
        severity = finding.get("severity")
        if not isinstance(severity, str) or severity not in _CONVERGENCE.KNOWN_SEVERITIES:
            return None
        if severity not in _CONVERGENCE.P1_SEVERITIES:
            continue
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            return None
        p1_ids.append(finding_id)
    return tuple(sorted(p1_ids))


def build_convergence_envelope(
    *, scope: Any, decision: Any, audit_digest: str,
    source_attempt: Optional[int], artifact_name: Optional[str],
) -> dict[str, Any]:
    """Build the additive, explicitly versioned one-round convergence envelope."""
    if not isinstance(scope, _CONVERGENCE.Scope):
        raise ValueError("convergence envelope requires a convergence Scope")
    if not isinstance(decision, _CONVERGENCE.RoundDecision):
        raise ValueError("convergence envelope requires a RoundDecision")
    if not isinstance(audit_digest, str) or len(audit_digest) != 64:
        raise ValueError("convergence envelope requires the raw audit SHA-256 digest")
    return {
        "schema_version": CONVERGENCE_ENVELOPE_SCHEMA_VERSION,
        "kind": CONVERGENCE_ENVELOPE_KIND,
        "scope": scope.as_dict(),
        "epoch": decision.state.epoch,
        "audit_digest": audit_digest,
        "source_attempt": source_attempt,
        "artifact_name": artifact_name,
        "decision": decision.decision,
        "state": decision.state.as_dict(),
    }


def validate_audit_identity(record: Any, identity: Identity) -> list[str]:
    """Independent, minimal-but-strict structural check — NOT a
    re-implementation of gate-hub's validate_primary_record (that full schema
    validation already ran once, inside review-primary, before this record
    was ever written). This aggregator only needs to confirm what it must
    itself trust before basing a merge decision on the downloaded file: it
    parses as an object, it is a primary_review record at schema_version 1
    (this aggregator is the only writer of synthetic_primary records — a
    downloaded synthetic_primary would mean something upstream is badly
    confused), the verdict is a known value, the identity quintuple matches
    the current run/PR/head with EXACT types (rejecting e.g. a bool where an
    int is required; an earlier source run_attempt is valid but a future one
    is rejected), and — for verdicts that name a reviewer — that the
    reviewer field is actually populated.
    """
    if not isinstance(record, dict):
        return [f"primary audit is not a JSON object (top-level type: {type(record).__name__})"]
    errors: list[str] = []

    if record.get("kind") != "primary_review":
        errors.append(f"unexpected audit kind {record.get('kind')!r} (expected 'primary_review')")

    schema_version = record.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        errors.append(f"schema_version must be exactly int 1 or 2, got {schema_version!r}")

    verdict = record.get("verdict")
    if verdict not in PRIMARY_VERDICT_DOMAIN:
        errors.append(f"verdict {verdict!r} is not in the accepted domain {PRIMARY_VERDICT_DOMAIN!r}")

    expected = identity.as_dict()
    for key in IDENTITY_QUINTUPLE:
        value = record.get(key)
        if key in _IDENTITY_INT_FIELDS:
            if not _is_strict_int(value):
                errors.append(
                    f"{key} must be a genuine int (not bool/str/other), got {value!r} ({type(value).__name__})"
                )
                continue
        else:  # head_sha
            if not (isinstance(value, str) and value):
                errors.append(f"{key} must be a non-empty string, got {value!r} ({type(value).__name__})")
                continue
        if key == "run_attempt":
            if value > expected[key]:
                errors.append(
                    f"identity mismatch on 'run_attempt': audit={value!r} "
                    f"exceeds current run_attempt={expected[key]!r}"
                )
            continue
        if value != expected[key]:
            errors.append(f"identity mismatch on {key!r}: audit={value!r} expected={expected[key]!r}")

    if isinstance(verdict, str) and verdict in ("pass", "fail", "unavailable"):
        reviewer = record.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer:
            errors.append(f"reviewer must be a non-empty string when verdict is {verdict!r}, got {reviewer!r}")

    return errors


def build_synthetic_audit(*, identity: Identity, status: str, reason: str) -> dict[str, Any]:
    """Best-effort synthetic audit trail shaped like gate-hub's
    contracts.build_synthetic_primary (kind/schema_version/status/reason/
    verdict/attempts/shadow_mode/expected_shadows + the identity quintuple
    this aggregator actually knows). Fields build_synthetic_primary also
    carries but this workflow-side aggregator has no way to derive
    (base_sha, diff_digest, policy_version, policy_digest, registry_commit,
    caller_sha, reusable_workflow_sha, job_id, reviewer) are deliberately
    OMITTED rather than fabricated — per the plan's "Caller / reusable
    workflow boundary", policy resolution belongs to gate-hub, not this
    aggregator. This record is a workflow-side Step Summary audit trail, not
    a drop-in replacement for gate-hub's canonical schema or ledger input.
    """
    if status not in (SYNTHETIC_STATUS_TIMED_OUT, SYNTHETIC_STATUS_ARTIFACT_MISSING):
        raise ValueError(f"invalid synthetic status: {status!r}")
    payload = identity.as_dict()
    payload.update(
        {
            "kind": "synthetic_primary",
            "schema_version": 1,
            "status": status,
            "reason": reason,
            "verdict": None,
            "attempts": [],
            "shadow_mode": "detached",
            "expected_shadows": [],
        }
    )
    return payload


def evaluate(
    *,
    quality_result: str,
    primary_result: str,
    runner: str,
    is_draft: bool,
    review_expected: bool,
    audit: Any,
    audit_error: Optional[str],
    identity: Identity,
    audit_source_attempt: Optional[int] = None,
    audit_artifact_name: Optional[str] = None,
    scope: Optional[Any] = None,
    audit_digest: Optional[str] = None,
    convergence_state: Optional[Any] = None,
) -> Outcome:
    """The pure decision core — no I/O, no GitHub API, fully unit-testable.

    `audit`/`audit_error` are pre-fetched by the thin CLI wrapper (`main`):
    `audit` is either the parsed JSON value found on disk (which may be any
    JSON type, not necessarily an object — see `validate_audit_identity`), or
    None; when None, `audit_error` explains why (download failed, no file,
    bad JSON, ...). See tests/test_gate_aggregator.py for the judgement
    matrix this function must satisfy.
    """
    notes: list[str] = []
    problems: list[str] = []
    synthetic: Optional[dict[str, Any]] = None
    invalid_inputs = []
    if runner not in RUNNER_DOMAIN:
        invalid_inputs.append(f"runner input {runner!r} is not a recognized value (expected one of {RUNNER_DOMAIN!r}) — fail-closed")
    if quality_result not in QUALITY_RESULT_DOMAIN:
        invalid_inputs.append(f"quality job result {quality_result!r} is not a recognized value — fail-closed")
    if primary_result not in PRIMARY_RESULT_DOMAIN:
        invalid_inputs.append(f"primary job result {primary_result!r} is not a recognized value (expected one of {PRIMARY_RESULT_DOMAIN!r}) — fail-closed")
    if type(is_draft) is not bool or type(review_expected) is not bool:
        invalid_inputs.append("draft/review_expected must be genuine booleans — fail-closed")
    if invalid_inputs:
        return Outcome(ok=False, problems=invalid_inputs)

    quality_reason = None if quality_result == "success" else {"failure": "quality_failure", "cancelled": "quality_cancelled", "skipped": "quality_skipped"}[quality_result]
    primary_classification = primary_reason = None
    audit_available = False
    convergence_eligible = False
    audit_source = artifact_name = None

    if quality_result == "success":
        notes.append("quality: success")
    else:
        problems.append(f"quality job result is {quality_result!r} (required: success)")

    if primary_result == "skipped":
        if is_draft or not review_expected:
            notes.append(f"primary: skipped and accepted (draft={is_draft}, review_expected={review_expected})")
            primary_classification, primary_reason = "expected_skip", "review_not_expected"
        else:
            problems.append(
                "primary job was skipped but review was expected (non-draft PR, same-repo head, "
                "runner: self) — an unexplained skip is never accepted as a passing primary review"
            )
            primary_classification, primary_reason = "integration_error", "unexpected_primary_skip"
    elif primary_result == "cancelled":
        synthetic = build_synthetic_audit(identity=identity, status=SYNTHETIC_STATUS_TIMED_OUT, reason="primary job concluded 'cancelled' before a canonical audit could be finalized")
        problems.append("primary job was cancelled before completion — fail-closed, synthetic audit generated")
        primary_classification, primary_reason = "review_unavailable", "primary_cancelled"
    elif audit is None:
        synthetic = build_synthetic_audit(identity=identity, status=SYNTHETIC_STATUS_ARTIFACT_MISSING, reason=audit_error or "primary audit artifact was not found")
        problems.append(f"primary audit artifact missing ({audit_error or 'not found'}) — fail-closed")
        primary_classification, primary_reason = "integration_error", "audit_missing"
    else:
        errors = validate_audit_identity(audit, identity)
        if errors:
            synthetic = build_synthetic_audit(identity=identity, status=SYNTHETIC_STATUS_ARTIFACT_MISSING, reason="downloaded primary audit failed validation: " + "; ".join(errors))
            problems.append("primary audit failed validation: " + "; ".join(errors))
            primary_classification, primary_reason = "integration_error", "audit_invalid"
        else:
            verdict = audit["verdict"]
            audit_source = audit["run_attempt"]
            if audit_source_attempt != audit_source or not (isinstance(audit_artifact_name, str) and audit_artifact_name):
                synthetic = build_synthetic_audit(identity=identity, status=SYNTHETIC_STATUS_ARTIFACT_MISSING, reason=("downloaded primary audit source attempt does not match the selected artifact output: " f"audit={audit_source!r} selected={audit_source_attempt!r}"))
                problems.append(
                    "primary audit source run_attempt mismatch: "
                    f"audit={audit_source!r} selected={audit_source_attempt!r}"
                )
                audit_source = None
                primary_classification, primary_reason = "integration_error", "audit_source_mismatch"
            else:
                audit_available = verdict in ("pass", "fail", "unavailable")
                convergence_eligible = audit_available
                artifact_name = audit_artifact_name if isinstance(audit_artifact_name, str) and audit_artifact_name else None
                notes.append(f"primary audit source run_attempt={audit_source} (current run_attempt={identity.run_attempt})")
                if verdict == "pass":
                    if primary_result != "success":
                        audit_available = False
                        convergence_eligible = False
                        audit_source = artifact_name = None
                        problems.append(
                            f"primary audit verdict is 'pass' but the job result is {primary_result!r} — inconsistent, fail-closed"
                        )
                        primary_classification, primary_reason = "integration_error", "job_audit_mismatch"
                    else:
                        notes.append("primary: pass")
                        primary_classification, primary_reason = "code_pass", "primary_pass"
                elif verdict == "fail":
                    problems.append("primary review verdict is 'fail'")
                    primary_classification, primary_reason = "code_fail", "primary_findings"
                elif verdict == "unavailable":
                    problems.append("primary review verdict is 'unavailable'")
                    primary_classification, primary_reason = "review_unavailable", "primary_unavailable"
                else:
                    audit_available = False
                    convergence_eligible = False
                    audit_source = artifact_name = None
                    problems.append(
                        f"primary audit verdict {verdict!r} is not accepted: canary-stage primary never legitimately writes "
                        "not_expected/waived; companion-field validation is not wired yet"
                    )
                    primary_classification, primary_reason = "integration_error", "audit_invalid"
                if primary_result == "success" and verdict in ("fail", "unavailable"):
                    audit_available = False
                    convergence_eligible = False
                    audit_source = artifact_name = None
                    primary_classification, primary_reason = "integration_error", "job_audit_mismatch"

    if primary_classification == "integration_error":
        classification, reason_code = primary_classification, primary_reason
    elif quality_reason is not None:
        classification, reason_code = "ci_failure", quality_reason
    else:
        classification, reason_code = primary_classification, primary_reason
    gate_result = {"code_pass": "pass", "code_fail": "fail", "expected_skip": "skipped", "ci_failure": "fail", "review_unavailable": "unavailable", "integration_error": "unavailable"}[classification]
    outcome = Outcome(
        ok=gate_result in ("pass", "skipped"), notes=notes, problems=problems, synthetic_audit=synthetic,
        classification=classification, reason_code=reason_code, gate_result=gate_result,
        audit_available=audit_available, audit_source_attempt=audit_source, audit_artifact_name=artifact_name,
    )
    # This is deliberately the only convergence hand-off in the single-round
    # evaluator.  It runs only after the existing audit identity/job verdict
    # checks have succeeded; the old Outcome fields remain single-round facts.
    if scope is not None and convergence_eligible and audit_digest is not None and isinstance(audit, dict):
        p1_ids = _canonical_p1_ids(audit)
        if p1_ids is None:
            outcome.ok = False
            outcome.classification = "integration_error"
            outcome.reason_code = "audit_invalid"
            outcome.gate_result = "unavailable"
            outcome.problems.append("canonical finding severity is missing, unknown, or malformed — fail-closed")
            return outcome
        primary = _CONVERGENCE.CanonicalPrimary(
            schema_version=1,
            repository_id=identity.repository_id,
            pr_number=identity.pr,
            head_sha=identity.head_sha,
            run_id=identity.run_id,
            run_attempt=identity.run_attempt,
            verdict=audit["verdict"],
            p1_ids=p1_ids,
        )
        processing_key = _CONVERGENCE.ProcessingKey(
            identity.repository_id, identity.pr, identity.run_id, identity.run_attempt,
        )
        state = convergence_state or _CONVERGENCE.initial_state(scope)
        round_decision = _CONVERGENCE.evaluate_round(
            state=state,
            scope=scope,
            primary=primary,
            audit_digest=audit_digest,
            waiver_receipts=(),
            processing_key=processing_key,
        )
        outcome.convergence_envelope = build_convergence_envelope(
            scope=scope,
            decision=round_decision,
            audit_digest=audit_digest,
            source_attempt=audit_source,
            artifact_name=artifact_name,
        )
    return outcome


def find_audit_file(audit_dir: Optional[Path]) -> tuple[Any, Optional[str]]:
    """Locate and parse the single downloaded canonical-audit JSON file.

    Returns (value_or_None, error_or_None). Never raises — every failure mode
    (missing directory, empty directory, more than one file, unparsable JSON)
    becomes a descriptive error string for `evaluate()`/the Step Summary,
    because a broken download must fail the gate closed, not crash the job
    with an unhandled exception and leave the check pending. The returned
    value may be any JSON type (not necessarily an object) — `evaluate()` /
    `validate_audit_identity` are responsible for rejecting a non-object
    payload, not this function.
    """
    if audit_dir is None or not audit_dir.is_dir():
        return None, "audit directory not present (download step likely found no artifact)"
    candidates = sorted(p for p in audit_dir.iterdir() if p.suffix == ".json")
    if not candidates:
        return None, f"no *.json file found under {audit_dir}"
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        return None, f"expected exactly one audit file under {audit_dir}, found {len(candidates)}: {names}"
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not parse {candidates[0].name}: {exc}"


# Human-readable, per-reason_code explanations rendered into the Step Summary
# (issues #32/#43): the caller must be able to tell "the primary reviewer
# rejected the change" apart from "the aggregator could not read the primary
# reviewer's conclusion" without opening job logs. Phrasing is load-bearing —
# the "could not be read" entries must never read as a reviewer rejection, and
# unexpected_primary_skip must never read as the normal draft/fork skip.
REASON_CODE_EXPLANATIONS = {
    "primary_findings": (
        "The primary reviewer REJECTED this change — the specific findings are in the primary "
        "review result for this run (linked above); the Problems list below only mirrors the verdict."
    ),
    "audit_missing": (
        "The primary reviewer's conclusion could NOT be read for this run (no audit artifact was "
        "produced) — check the primary job logs. This is not a reviewer rejection."
    ),
    "audit_invalid": (
        "The primary reviewer's conclusion could NOT be read for this run (the audit artifact failed "
        "validation) — check the primary job logs. This is not a reviewer rejection."
    ),
    "audit_source_mismatch": (
        "The primary reviewer's conclusion could NOT be read for this run (the audit's source attempt "
        "did not match the selected artifact) — check the primary job logs. This is not a reviewer rejection."
    ),
    "job_audit_mismatch": (
        "The primary reviewer's recorded conclusion contradicts the primary job's own status — "
        "the gate fails closed. Check the primary job logs."
    ),
    "unexpected_primary_skip": (
        "The primary review should have run but was skipped — this is NOT the normal draft/fork "
        "skip. Check the primary job configuration."
    ),
}


def _action_sentence(
    outcome: Outcome, *, repository: Optional[str], identity: Optional[Identity],
    is_draft: Optional[bool], runner: Optional[str],
) -> Optional[str]:
    """The one-line "what do I do now" sentence rendered immediately after
    `**Result: …**` and BEFORE the machine codes — the aggregate mail/comment
    goes to people who were not watching the run, so the human action comes
    first and `classification=`/`reason_code=` follow it (never the other way
    round). Every gate_result gets one, including pass ("no action needed").
    The run URL is built from data the aggregator already has (repository +
    identity.run_id) — deliberately no new CLI flag or env var (locked
    decision: GHES server-URL configurability is out of scope)."""
    run_url = None
    if repository and identity is not None:
        run_url = f"https://github.com/{repository}/actions/runs/{identity.run_id}"
    gate_result = outcome.gate_result
    if gate_result == "pass":
        return "No action needed — quality passed and the primary reviewer approved this change; the gate is green."
    if gate_result == "skipped":
        if is_draft:
            return (
                "No action needed — the primary review is intentionally skipped while the PR is a draft; "
                "the full primary review will run once you mark the PR ready for review."
            )
        if runner == "hosted":
            return (
                "No action needed — the primary review only runs on runner=self and this run used "
                "runner=hosted, so the skip is expected; if you need the primary review on this PR, "
                "switch runner to self."
            )
        return "No action needed — the primary review was not expected for this PR, so the skip is accepted."
    if gate_result == "fail":
        if outcome.reason_code == "primary_findings":
            sentence = "Action needed — the primary reviewer rejected this change: read its findings, address them, and push again."
        else:
            sentence = "Action needed — the gate is red: fix the problem(s) listed under Problems below, then re-run."
        if run_url:
            sentence += f" Full details: {run_url}"
        return sentence
    if gate_result == "unavailable":
        sentence = (
            "Action needed — the primary review outcome could not be determined (this is neither an "
            "approval nor a rejection): investigate the run before merging."
        )
        if run_url:
            sentence += f" Run: {run_url}"
        return sentence
    return None


def render_summary(
    outcome: Outcome, *, repository: Optional[str] = None, identity: Optional[Identity] = None,
    is_draft: Optional[bool] = None, runner: Optional[str] = None,
) -> str:
    lines = ["### Required Gate v2 — aggregate verdict", ""]
    # Top line shows the four-state gate_result (pass/fail/skipped/unavailable)
    # instead of collapsing to ok -> pass|fail, so an accepted skip (draft/fork)
    # is visibly distinct from a real pass, and an unreadable primary review is
    # visibly distinct from a reviewer rejection (issues #32/#43). Outcomes from
    # the malformed-input paths carry no terminal fields — keep the legacy
    # binary rendering there.
    gate_result = outcome.gate_result or ("pass" if outcome.ok else "fail")
    lines.append(f"**Result: {gate_result}**")
    if outcome.classification is not None:
        # Human-first: the action sentence answers "what do I do now" BEFORE
        # the machine codes (never after), for every terminal gate_result.
        action = _action_sentence(outcome, repository=repository, identity=identity, is_draft=is_draft, runner=runner)
        if action:
            lines.append(action)
        lines.append(
            f"Terminal state: classification=`{outcome.classification}`, "
            f"reason_code=`{outcome.reason_code}`, gate_result=`{outcome.gate_result}`"
        )
    explanation = REASON_CODE_EXPLANATIONS.get(outcome.reason_code or "")
    if explanation:
        lines.append("")
        lines.append(explanation)
    lines.append("")
    if outcome.notes:
        lines.append("Accepted:")
        for note in outcome.notes:
            lines.append(f"- {note}")
        lines.append("")
    if outcome.problems:
        lines.append("Problems:")
        for problem in outcome.problems:
            lines.append(f"- {problem}")
        lines.append("")
    if outcome.synthetic_audit is not None:
        lines.append(
            "**Synthetic audit generated** (no valid canonical primary audit was available for this run):"
        )
        lines.append("")
        lines.append(
            '`"verdict": null` below means the primary conclusion could not be read — '
            "it is NOT a reviewer rejection. Check the primary job logs for the real cause."
        )
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(outcome.synthetic_audit, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def _panel_action(row: dict[str, Any]) -> str:
    """Return the recipient-facing action for one validated panel row."""
    gate_result = row["gate_result"]
    action = PANEL_BUCKET_BY_GATE_RESULT[gate_result]
    if gate_result == "skipped":
        return f"{action}（主审未跑，绿≠过审）"
    return action


def _history_reason_category(reason: str) -> str:
    http_match = re.search(r"\bHTTP\s+Error\s+(\d{3})\b", reason)
    if http_match:
        return f"制品下载失败：HTTP {http_match.group(1)}"
    if reason.startswith("expired artifact "):
        return "过期制品"
    if reason.startswith("artifact ") and " has no archive URL" in reason:
        return "制品缺少 archive URL"
    if reason.startswith("artifact history does not contain cached rows:"):
        return "制品历史缺少面板缓存行"
    if reason.startswith("artifact ") and ": " in reason:
        detail = reason.split(": ", 1)[1]
        return f"制品处理失败：{detail.split(':', 1)[0]}"
    if reason.startswith("bounded_scan:"):
        return "有界历史扫描达到上限"
    if reason.startswith("history budget exhausted"):
        return "历史重建预算耗尽"
    if reason.startswith("no terminal artifact matched"):
        return "未找到终态制品"
    return reason


def _summarize_history_reasons(reasons: list[str]) -> str:
    counts: dict[str, int] = {}
    for reason in reasons:
        category = _history_reason_category(reason)
        counts[category] = counts.get(category, 0) + 1
    return "；".join(f"{count} 个{category}" for category, count in counts.items())


def _bounded_history_warning(*, history_warning: Optional[str], history_reasons: Optional[list[str]]) -> Optional[str]:
    warning = _summarize_history_reasons(history_reasons) if history_reasons is not None else history_warning
    if not warning:
        return None
    prefix = "> 历史可能不完整："
    suffix = "…完整明细见 delivery 诊断制品"
    line = prefix + warning
    if len(line) <= MAX_HISTORY_WARNING_CHARS:
        return line
    available = MAX_HISTORY_WARNING_CHARS - len(prefix) - len(suffix)
    return prefix + warning[:available].rstrip("；") + suffix


def render_status_panel(
    rows: list[dict[str, Any]], *, history_warning: Optional[str] = None,
    history_reasons: Optional[list[str]] = None,
) -> str:
    """Render the public sticky panel from rows only.

    The renderer is deliberately a pure projection: it does not read a prior
    comment, infer history from rendered Markdown, or mutate its input. Rows
    are sorted by their durable run identity so a rerun produces the same
    body for the same input set.
    """
    ordered = sorted(rows, key=lambda row: (row["run_id"], row["run_attempt"]))
    if not ordered:
        raise ValueError("status panel requires at least one terminal row")
    current = ordered[-1]
    current_result = current["gate_result"]
    lines = [
        PANEL_MARKER,
        "",
        "### Required Gate v2 — 状态面板",
        "",
        f"当前状态：**{current_result}** · **{_panel_action(current)}**",
    ]
    if current_result == "skipped":
        lines.extend(["", "> 主审未跑，绿≠过审。draft / fork / hosted 的跳过不代表真实通过。"])
    lines.extend([
        "",
        f"当前裁决：`{current['classification']}` / `{current['reason_code']}`",
    ])
    warning_line = _bounded_history_warning(history_warning=history_warning, history_reasons=history_reasons)
    if warning_line:
        lines.extend(["", warning_line])
    lines.extend([
        "",
        "#### Gate 历史（v1；来源为持久化 `gate_terminal` 制品）",
        "",
        "| Run | Attempt | Head | 状态 | 收件人动作 |",
        "| ---: | ---: | :--- | :--- | :--- |",
    ])
    for row in ordered:
        short_sha = row["head_sha"][:7]
        run_link = f"[{row['run_id']}](https://github.com/{row['repository']}/actions/runs/{row['run_id']})"
        lines.append(
            f"| {run_link} | {row['run_attempt']} | `{short_sha}` | "
            f"`{row['gate_result']}` | {_panel_action(row)} |"
        )
    lines.extend([
        "",
        "历史行按 `run_id` + `run_attempt` 去重并只增不删；删除本评论后可由 `gate_terminal` 制品重建。",
        "",
    ])
    return "\n".join(lines)


# The status panel is the only aggregate PR comment. It is a marker-located
# projection of durable gate-terminal artifacts; its body is never treated as
# the history database. PATCH updates do not notify GitHub subscribers.


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _github_request(*, token: str, url: str, method: str = "GET", payload: Optional[dict[str, Any]] = None) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "gate-aggregator",
        },
        method=method,
    )
    budget = _ACTIVE_PUBLISH_BUDGET.get()
    timeout = budget.timeout() if budget is not None else GITHUB_API_TIMEOUT_SECONDS
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (socket.timeout, TimeoutError) as exc:
        if budget is not None:
            try:
                budget.timeout()
            except _PublishBudgetExhausted as budget_exc:
                raise budget_exc from exc
        raise
    if budget is not None:
        budget.timeout()
    return raw


def _download_terminal_zip(*, token: str, url: str) -> bytes:
    """Download one terminal zip while keeping GitHub auth off signed blobs."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    budget = _ACTIVE_PUBLISH_BUDGET.get()
    api_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gate-aggregator",
    }

    def open_once(request: urllib.request.Request) -> bytes:
        timeout = budget.timeout() if budget is not None else GITHUB_API_TIMEOUT_SECONDS
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except (socket.timeout, TimeoutError) as exc:
            if budget is not None:
                try:
                    budget.timeout()
                except _PublishBudgetExhausted as budget_exc:
                    raise budget_exc from exc
            raise
        if budget is not None:
            budget.timeout()
        return raw

    request = urllib.request.Request(url, headers=api_headers, method="GET")
    try:
        return open_once(request)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        location = exc.headers.get("Location") if exc.headers is not None else None
        if not isinstance(location, str) or not location:
            raise ValueError("artifact zip redirect did not include a Location header")
        if urllib.parse.urlsplit(location).hostname is None:
            raise ValueError("artifact zip redirect Location is not an absolute URL")
        redirect_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "gate-aggregator",
        }
        if urllib.parse.urlsplit(location).hostname.lower() == "api.github.com":
            redirect_headers.update(api_headers)
        return open_once(urllib.request.Request(location, headers=redirect_headers, method="GET"))


def _github_json(*, token: str, url: str) -> Any:
    raw = _github_request(token=token, url=url)
    return json.loads(raw) if raw else None


def _github_identity(token: str) -> dict[str, Any]:
    try:
        payload = _github_json(token=token, url="https://api.github.com/user")
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 404):
            raise
        return {"id": ACTIONS_BOT_ID, "login": ACTIONS_BOT_LOGIN, "identity_source": "actions_bot_fallback"}
    if not isinstance(payload, dict) or not _is_strict_int(payload.get("id")) or not isinstance(payload.get("login"), str) or not payload["login"]:
        raise ValueError("GitHub identity response is missing a strict numeric id or login")
    return {"id": payload["id"], "login": payload["login"], "identity_source": "user_api"}


def _fetch_panel_comments(*, token: str, repository: str, pr_number: int) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _github_json(
            token=token,
            url=f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments?per_page=100&page={page}",
        )
        if not isinstance(payload, list) or any(not isinstance(comment, dict) for comment in payload):
            raise ValueError("PR comments response has an invalid shape")
        comments.extend(payload)
        if len(payload) < 100:
            return comments
        page += 1


def _post_issue_comment(*, repository: str, pr_number: int, body: str, token: str) -> None:
    _github_request(
        token=token,
        url=f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments",
        method="POST",
        payload={"body": body},
    )


def _patch_issue_comment(*, repository: str, comment_id: int, body: str, token: str) -> None:
    _github_request(
        token=token,
        url=f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}",
        method="PATCH",
        payload={"body": body},
    )


def _delete_issue_comment(*, repository: str, comment_id: int, token: str) -> None:
    _github_request(
        token=token,
        url=f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}",
        method="DELETE",
    )


def _terminal_row(record: Any, *, repository: str, repository_id: int, pr_number: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("gate terminal artifact is not a JSON object")
    if type(record.get("schema_version")) is not int or record.get("schema_version") != 1 or record.get("kind") != "gate_terminal":
        raise ValueError("gate terminal artifact has an unsupported schema")
    if record.get("repository") != repository or record.get("repository_id") != repository_id or record.get("pr_number") != pr_number:
        raise ValueError("gate terminal artifact identity does not match this PR")
    for field_name in ("run_id", "run_attempt"):
        if not _is_strict_int(record.get(field_name)) or record[field_name] <= 0:
            raise ValueError(f"gate terminal {field_name} must be a positive integer")
    if not isinstance(record.get("head_sha"), str) or not record["head_sha"]:
        raise ValueError("gate terminal head_sha must be a non-empty string")
    if record.get("gate_result") not in GATE_RESULT_DOMAIN:
        raise ValueError("gate terminal gate_result is outside the finite domain")
    if record.get("classification") not in TERMINAL_CLASSIFICATION_DOMAIN or record.get("reason_code") not in TERMINAL_REASON_DOMAIN:
        raise ValueError("gate terminal classification/reason_code is outside the finite domain")
    return {
        "schema_version": PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": repository,
        "run_id": record["run_id"],
        "run_attempt": record["run_attempt"],
        "head_sha": record["head_sha"],
        "gate_result": record["gate_result"],
        "classification": record["classification"],
        "reason_code": record["reason_code"],
    }


def _read_terminal_zip(raw: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        candidates = [name for name in bundle.namelist() if Path(name).name == "gate-terminal.json"]
        if len(candidates) != 1:
            raise ValueError(f"terminal artifact must contain exactly one gate-terminal.json, found {len(candidates)}")
        return json.loads(bundle.read(candidates[0]))


def _consume_terminal_artifact(
    result: HistoryLoad, *, artifact: dict[str, Any], token: str, repository: str,
    repository_id: int, pr_number: int,
) -> bool:
    name = artifact["name"]
    if artifact.get("expired") is True:
        result.skipped_records.append({"name": name, "reason": "expired_artifact"})
        result.incomplete_reasons.append(f"expired artifact {name}")
        return True
    archive_url = artifact.get("archive_download_url")
    if not isinstance(archive_url, str) or not archive_url:
        result.skipped_records.append({"name": name, "reason": "missing_archive_url"})
        result.incomplete_reasons.append(f"artifact {name} has no archive URL")
        return True
    try:
        record = _read_terminal_zip(_download_terminal_zip(token=token, url=archive_url))
        result.rows.append(_terminal_row(record, repository=repository, repository_id=repository_id, pr_number=pr_number))
    except _PublishBudgetExhausted:
        result.incomplete_reasons.append(f"history budget exhausted while downloading {name}")
        return False
    except ValueError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        result.skipped_records.append({"name": name, "reason": reason})
        if "identity does not match" not in str(exc):
            result.incomplete_reasons.append(f"artifact {name}: {reason}")
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        result.skipped_records.append({"name": name, "reason": reason})
        result.incomplete_reasons.append(f"artifact {name}: {reason}")
    return True


def _fetch_terminal_history(
    *, token: str, repository: str, repository_id: int, pr_number: int,
    target_run_ids: Optional[list[int]] = None,
) -> HistoryLoad:
    prefix = f"gate-terminal-v1-{repository_id}-"
    result = HistoryLoad()

    if target_run_ids is not None:
        unique_run_ids = list(dict.fromkeys(target_run_ids))
        if len(unique_run_ids) > MAX_TARGETED_HISTORY_RUNS:
            result.incomplete_reasons.append(
                f"bounded_scan: targeted history run limit {MAX_TARGETED_HISTORY_RUNS} reached"
            )
            unique_run_ids = unique_run_ids[:MAX_TARGETED_HISTORY_RUNS]
        for run_id in unique_run_ids:
            try:
                payload = _github_json(
                    token=token,
                    url=f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts",
                )
            except _PublishBudgetExhausted:
                result.incomplete_reasons.append("history budget exhausted during targeted scan")
                break
            if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
                raise ValueError("Actions run artifacts response has an invalid shape")
            artifacts = [
                artifact for artifact in payload["artifacts"]
                if isinstance(artifact, dict)
                and isinstance(artifact.get("name"), str)
                and artifact["name"].startswith(prefix)
            ]
            if not artifacts:
                result.incomplete_reasons.append(f"no terminal artifact matched run {run_id}")
            for artifact in artifacts:
                if not _consume_terminal_artifact(
                    result, artifact=artifact, token=token, repository=repository,
                    repository_id=repository_id, pr_number=pr_number,
                ):
                    return result
        if not unique_run_ids:
            result.incomplete_reasons.append("no cached panel run ids were available for targeted history")
        return result

    artifacts: list[dict[str, Any]] = []
    page = 1
    while page <= MAX_REPO_WIDE_HISTORY_PAGES:
        try:
            payload = _github_json(
                token=token,
                url=f"https://api.github.com/repos/{repository}/actions/artifacts?per_page=100&page={page}",
            )
        except _PublishBudgetExhausted:
            result.incomplete_reasons.append("history budget exhausted during bounded_scan")
            break
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise ValueError("Actions artifacts response has an invalid shape")
        page_artifacts = payload["artifacts"]
        artifacts.extend(
            artifact for artifact in page_artifacts
            if isinstance(artifact, dict)
            and isinstance(artifact.get("name"), str)
            and artifact["name"].startswith(prefix)
        )
        if len(page_artifacts) < 100:
            break
        if page == MAX_REPO_WIDE_HISTORY_PAGES:
            result.incomplete_reasons.append(
                f"bounded_scan: repo-wide artifact page limit {MAX_REPO_WIDE_HISTORY_PAGES} reached"
            )
            break
        page += 1
    if not artifacts:
        if not result.incomplete_reasons:
            result.incomplete_reasons.append(f"no terminal artifact matched {prefix}")
    for artifact in artifacts:
        if not _consume_terminal_artifact(
            result, artifact=artifact, token=token, repository=repository,
            repository_id=repository_id, pr_number=pr_number,
        ):
            break
    return result


def _merge_panel_rows(current: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_identity = {(row["run_id"], row["run_attempt"]): row for row in history}
    by_identity[(current["run_id"], current["run_attempt"])] = current
    return list(by_identity.values())


def _find_panel_comments(comments: Any, owner: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(comments, list):
        raise ValueError("PR comments response has an invalid shape")
    owner_id = owner["id"]
    owner_login = owner["login"]
    selected = []
    for comment in comments:
        if not isinstance(comment, dict) or PANEL_MARKER not in comment.get("body", ""):
            continue
        user = comment.get("user")
        if not isinstance(user, dict):
            continue
        if user.get("id") is not None:
            is_owner = user["id"] == owner_id
        else:
            is_owner = user.get("login") == owner_login
        if is_owner:
            selected.append(comment)
    return sorted(selected, key=lambda comment: (str(comment.get("created_at", "")), int(comment.get("id", 0))))


def _find_panel_comment(comments: Any) -> Optional[dict[str, Any]]:
    """Compatibility wrapper retained for callers that only need a marker check."""
    if not isinstance(comments, list):
        raise ValueError("PR comments response has an invalid shape")
    return next((comment for comment in comments if isinstance(comment, dict) and PANEL_MARKER in comment.get("body", "")), None)


def _parse_panel_history(body: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("| ["):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 5:
            continue
        run_match = re.match(r"\[(\d+)\]\(https://github\.com/([^/]+/[^/]+)/actions/runs/\d+\)", parts[0])
        attempt_match = re.fullmatch(r"(\d+)", parts[1])
        head_match = re.fullmatch(r"`([^`]+)`", parts[2])
        result_match = re.fullmatch(r"`([^`]+)`", parts[3])
        if not (run_match and attempt_match and head_match and result_match):
            continue
        gate_result = result_match.group(1)
        if gate_result not in GATE_RESULT_DOMAIN:
            continue
        rows.append({
            "schema_version": PANEL_HISTORY_ROW_SCHEMA_VERSION,
            "repository": run_match.group(2),
            "run_id": int(run_match.group(1)),
            "run_attempt": int(attempt_match.group(1)),
            "head_sha": head_match.group(1),
            "gate_result": gate_result,
            "classification": "panel_cache",
            "reason_code": "panel_cache",
        })
    return rows


def _warn(message: str) -> None:
    """Print one ::warning:: annotation, itself fail-open: this only runs
    inside the fail-open boundary below, where stdout may already be a
    broken pipe (probe: BrokenPipeError). A failed warning print must never
    escape `_finish` and turn a green gate into a crash — so even THIS
    print is guarded. Guard-of-the-guard, deliberately no logging/raising.
    """
    try:
        print(message)
    except Exception:
        pass


def _build_panel_delivery(
    *, body: str, repository: Optional[str], pr_number: Optional[int], identity: Optional[Identity],
    delivery: str, reason_code: str, error_category: Optional[str] = None,
    http_status: Optional[int] = None, history_error: Optional[str] = None,
    operation: Optional[str] = None, history_skipped_records: Optional[list[dict[str, str]]] = None,
    history_incomplete_reasons: Optional[list[str]] = None, self_heal_errors: Optional[list[str]] = None,
    identity_source: Optional[str] = None, completed_operations: Optional[list[str]] = None,
    pending_operations: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build durable evidence for panel delivery and reconstruction failures."""
    return {
        "schema_version": PANEL_DELIVERY_SCHEMA_VERSION,
        "kind": PANEL_DELIVERY_KIND,
        "repository": repository or "",
        "repository_id": identity.repository_id if identity else None,
        "pr_number": pr_number,
        "run_id": identity.run_id if identity else None,
        "run_attempt": identity.run_attempt if identity else None,
        "head_sha": identity.head_sha if identity else None,
        "comment_expected": delivery != "not_enabled",
        "comment_created": True if delivery == "created" else False if delivery in ("updated", "not_created") else None,
        "delivery": delivery,
        "reason_code": reason_code,
        "error_category": error_category,
        "http_status": http_status,
        "history_error": history_error,
        "history_skipped_records": history_skipped_records or [],
        "history_skipped_count": len(history_skipped_records or []),
        "history_incomplete_reasons": history_incomplete_reasons or [],
        "history_incomplete": bool(history_incomplete_reasons),
        "self_heal_errors": self_heal_errors or [],
        "operation": operation,
        "identity_source": identity_source,
        "completed_operations": completed_operations or [],
        "pending_operations": pending_operations or [],
        "comment_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def _panel_failure(exc: BaseException) -> tuple[str, str, Optional[int]]:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403 or exc.code == 429:
            return f"http_{exc.code}", "permission_or_rate_limit", exc.code
        if exc.code >= 500:
            return "http_5xx", "server_error", exc.code
        return "http_error", "http_error", exc.code
    return "network_indeterminate", "network_error", None


def _panel_warning(*, phase: str, exc: BaseException, reason_code: str, category: str, http_status: Optional[int]) -> None:
    status = str(http_status) if http_status is not None else "unavailable"
    _warn(
        f"::warning::gate status panel {phase} failed — HTTP status={status}; "
        f"permission category={category}; reason={reason_code}; "
        "gate verdict is unchanged and Step Summary remains authoritative"
    )


def _post_status_panel_fail_open_with_budget(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity], budget: _PublishBudget,
) -> tuple[str, dict[str, Any]]:
    """Publish the one marker-located status panel without affecting verdict."""
    body = scrub_for_publish(
        render_status_panel([current]),
        runtime_values=runtime_values_from_environment(),
    )
    identity_source: Optional[str] = None
    try:
        if not repository or pr_number is None:
            reason_code, category, status = "missing_target", "configuration", None
            _panel_warning(phase="target resolution", exc=ValueError("missing target"), reason_code=reason_code, category=category, http_status=status)
            return body, _build_panel_delivery(body=body, repository=repository, pr_number=pr_number, identity=identity, delivery="not_created", reason_code=reason_code, error_category=category, http_status=status)
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            reason_code, category, status = "missing_token", "configuration", None
            _panel_warning(phase="authentication", exc=ValueError("missing token"), reason_code=reason_code, category=category, http_status=status)
            return body, _build_panel_delivery(body=body, repository=repository, pr_number=pr_number, identity=identity, delivery="not_created", reason_code=reason_code, error_category=category, http_status=status)

        try:
            budget.begin("IDENTITY")
            owner = _github_identity(token)
            budget.complete("IDENTITY")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            reason_code, category, status = _panel_failure(exc)
            _panel_warning(phase="identity", exc=exc, reason_code=reason_code, category=category, http_status=status)
            return body, _build_panel_delivery(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code=reason_code, error_category=category,
                http_status=status, operation="IDENTITY",
                completed_operations=budget.completed_operations, pending_operations=budget.pending_operations,
            )
        identity_source = owner.get("identity_source", "user_api")
        budget.begin("COMMENT_LOOKUP")
        comments = _fetch_panel_comments(token=token, repository=repository, pr_number=pr_number)
        budget.complete("COMMENT_LOOKUP")
        own_panels = _find_panel_comments(comments, owner)
        existing = own_panels[0] if own_panels else None
        cached_rows = _parse_panel_history(existing.get("body", "")) if existing else []
        target_run_ids = [row["run_id"] for row in cached_rows] if cached_rows else None
        try:
            budget.begin("HISTORY_RECONSTRUCTION")
            history = _fetch_terminal_history(
                token=token, repository=repository, repository_id=repository_id or 0,
                pr_number=pr_number, target_run_ids=target_run_ids,
            )
            budget.complete("HISTORY_RECONSTRUCTION")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            reason_code, category, status = _panel_failure(exc)
            _panel_warning(phase="history reconstruction", exc=exc, reason_code=reason_code, category=category, http_status=status)
            cached_body = existing.get("body", body) if existing else body
            return cached_body, _build_panel_delivery(
                body=cached_body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code="history_unavailable", error_category=category,
                http_status=status, history_error=f"{type(exc).__name__}: {exc}", operation="LOOKUP",
                identity_source=identity_source, completed_operations=budget.completed_operations,
                pending_operations=budget.pending_operations,
            )
        if isinstance(history, list):
            history = HistoryLoad(rows=history)
        incomplete_reasons = list(history.incomplete_reasons)
        artifact_keys = {(row["run_id"], row["run_attempt"]) for row in history.rows}
        cache_only = [row for row in cached_rows if (row["run_id"], row["run_attempt"]) not in artifact_keys]
        if cache_only:
            incomplete_reasons.append(
                "artifact history does not contain cached rows: "
                + ", ".join(f"{row['run_id']}/{row['run_attempt']}" for row in cache_only)
            )
        body = scrub_for_publish(
            render_status_panel(
                _merge_panel_rows(current, [*history.rows, *cached_rows]),
                history_reasons=incomplete_reasons if incomplete_reasons else None,
            ),
            runtime_values=runtime_values_from_environment(),
        )
        self_heal_errors: list[str] = []
        if existing:
            budget.discard("POST_VERIFY")
            budget.begin("COMMENT_PUBLISH")
            _patch_issue_comment(repository=repository, comment_id=int(existing["id"]), body=body, token=token)
            budget.complete("COMMENT_PUBLISH")
            for duplicate in own_panels[1:]:
                budget.add("SELF_HEAL")
                budget.begin("SELF_HEAL")
                try:
                    _delete_issue_comment(repository=repository, comment_id=int(duplicate["id"]), token=token)
                    budget.complete("SELF_HEAL")
                except _PublishBudgetExhausted:
                    raise
                except Exception as exc:
                    self_heal_errors.append(f"comment {duplicate.get('id')}: {type(exc).__name__}: {exc}")
            if not own_panels[1:]:
                budget.discard("SELF_HEAL")
            return body, _build_panel_delivery(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="updated", reason_code="history_incomplete" if incomplete_reasons else "patched",
                operation="PATCH", history_skipped_records=history.skipped_records,
                history_incomplete_reasons=incomplete_reasons, self_heal_errors=self_heal_errors,
                identity_source=identity_source, completed_operations=budget.completed_operations,
                pending_operations=budget.pending_operations,
            )
        budget.begin("COMMENT_PUBLISH")
        _post_issue_comment(repository=repository, pr_number=pr_number, body=body, token=token)
        budget.complete("COMMENT_PUBLISH")
        budget.begin("POST_VERIFY")
        try:
            after_post = _find_panel_comments(
                _fetch_panel_comments(token=token, repository=repository, pr_number=pr_number), owner,
            )
            budget.complete("POST_VERIFY")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            self_heal_errors.append(f"post verification: {type(exc).__name__}: {exc}")
            after_post = []
            budget.discard("POST_VERIFY")
        if after_post:
            winner = after_post[0]
            if winner.get("body") != body or len(after_post) > 1:
                budget.add("SELF_HEAL")
                budget.begin("SELF_HEAL")
                try:
                    _patch_issue_comment(repository=repository, comment_id=int(winner["id"]), body=body, token=token)
                    for duplicate in after_post[1:]:
                        _delete_issue_comment(repository=repository, comment_id=int(duplicate["id"]), token=token)
                    budget.complete("SELF_HEAL")
                except _PublishBudgetExhausted:
                    raise
                except Exception as exc:
                    self_heal_errors.append(f"post self-heal: {type(exc).__name__}: {exc}")
            else:
                budget.discard("SELF_HEAL")
        else:
            budget.discard("SELF_HEAL")
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="created", reason_code="post_self_heal_partial" if self_heal_errors else "posted",
            operation="POST", history_skipped_records=history.skipped_records,
            history_incomplete_reasons=incomplete_reasons, self_heal_errors=self_heal_errors,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )
    except _PublishBudgetExhausted as exc:
        delivery = "unknown" if "COMMENT_PUBLISH" in budget.completed_operations else "not_created"
        _panel_warning(
            phase="publish budget", exc=exc, reason_code="publish_budget_exhausted",
            category="network_error", http_status=None,
        )
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery=delivery, reason_code="publish_budget_exhausted", error_category="network_error",
            operation=exc.operation, identity_source=identity_source,
            completed_operations=budget.completed_operations, pending_operations=budget.pending_operations,
        )
    except urllib.error.HTTPError as exc:
        reason_code, category, status = _panel_failure(exc)
        _panel_warning(phase="comment publish", exc=exc, reason_code=reason_code, category=category, http_status=status)
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="not_created", reason_code=reason_code, error_category=category, http_status=status,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )
    except Exception as exc:
        reason_code, category, status = _panel_failure(exc)
        _panel_warning(phase="comment publish", exc=exc, reason_code=reason_code, category=category, http_status=status)
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="unknown", reason_code=reason_code, error_category=category, http_status=status,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )


def _post_status_panel_fail_open(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity],
) -> tuple[str, dict[str, Any]]:
    body = render_status_panel([current])
    try:
        budget = _PublishBudget.from_environment()
    except ValueError as exc:
        _panel_warning(
            phase="publish budget", exc=exc, reason_code="invalid_publish_budget",
            category="configuration", http_status=None,
        )
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="not_created", reason_code="invalid_publish_budget", error_category="configuration",
        )
    token = _ACTIVE_PUBLISH_BUDGET.set(budget)
    try:
        return _post_status_panel_fail_open_with_budget(
            current=current, repository=repository, repository_id=repository_id,
            pr_number=pr_number, identity=identity, budget=budget,
        )
    finally:
        _ACTIVE_PUBLISH_BUDGET.reset(token)


def _write_panel_delivery(path: str, receipt: dict[str, Any]) -> None:
    """Publish the receipt atomically so artifact upload never sees partial JSON."""
    target = Path(path)
    temporary_path = target.with_name(f".{target.name}.tmp")
    temporary_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(target)


def _panel_current_row(
    *, outcome: Outcome, repository: Optional[str], identity: Optional[Identity],
) -> dict[str, Any]:
    gate_result = outcome.gate_result if outcome.gate_result in GATE_RESULT_DOMAIN else "unavailable"
    classification = outcome.classification if outcome.classification in TERMINAL_CLASSIFICATION_DOMAIN else "integration_error"
    reason_code = outcome.reason_code if outcome.reason_code in TERMINAL_REASON_DOMAIN else "audit_invalid"
    return {
        "schema_version": PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": repository or "",
        "run_id": identity.run_id if identity else 0,
        "run_attempt": identity.run_attempt if identity else 0,
        "head_sha": identity.head_sha if identity else "unknown",
        "gate_result": gate_result,
        "classification": classification,
        "reason_code": reason_code,
    }


def _persist_panel_delivery(path: str, receipt: dict[str, Any]) -> None:
    """Persist the second-exit receipt while clearing stale evidence safely."""
    receipt_path = Path(path)
    receipt_unlink_failed = False
    try:
        receipt_path.unlink(missing_ok=True)
    except OSError as exc:
        receipt_unlink_failed = True
        _warn(f"::warning::could not remove the previous gate PR-comment receipt ({type(exc).__name__}: {exc})")
    try:
        _write_panel_delivery(path, receipt)
    except OSError as exc:
        if receipt_unlink_failed and receipt_path.exists():
            try:
                receipt_path.unlink(missing_ok=True)
            except OSError:
                pass
            if receipt_path.exists():
                try:
                    receipt_path.write_bytes(b"invalid gate PR-comment receipt\n")
                except OSError as invalidate_exc:
                    _warn(
                        "::warning::gate PR-comment receipt write failed and the stale receipt "
                        f"could not be cleared ({type(invalidate_exc).__name__}: {invalidate_exc}); "
                        "receipt channel is untrusted"
                    )
                else:
                    _warn("::warning::gate PR-comment receipt write failed and the stale receipt was destroyed; an invalid marker was written, upload will pass but consumers' json.loads will fail-loud")
            else:
                _warn("::warning::gate PR-comment receipt write failed and the stale receipt was cleared; upload will red")
        elif receipt_path.exists():
            _warn(f"::warning::gate PR-comment receipt write failed ({type(exc).__name__}: {exc}); receipt channel is untrusted")
        else:
            _warn(f"::warning::gate PR-comment receipt write failed ({type(exc).__name__}: {exc}); file is missing and upload will red")


def _publish_only(args: argparse.Namespace) -> int:
    """Publish only after the caller has uploaded the terminal envelope."""
    identity = Identity(
        repository_id=args.repository_id,
        head_sha=args.head_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        pr=args.pr_number,
    )
    current: dict[str, Any]
    try:
        if not args.terminal_path:
            raise ValueError("terminal path is missing")
        record = json.loads(Path(args.terminal_path).read_text(encoding="utf-8"))
        current = _terminal_row(record, repository=args.repository, repository_id=args.repository_id, pr_number=args.pr_number)
    except Exception as exc:
        current = _panel_current_row(
            outcome=Outcome(ok=False, classification="integration_error", reason_code="audit_invalid", gate_result="unavailable"),
            repository=args.repository,
            identity=identity,
        )
        body = scrub_for_publish(
            render_status_panel(
                [current],
                history_warning=f"terminal artifact unavailable: {type(exc).__name__}: {exc}",
            ),
            runtime_values=runtime_values_from_environment(),
        )
        receipt = _build_panel_delivery(
            body=body, repository=args.repository, pr_number=args.pr_number, identity=identity,
            delivery="not_created", reason_code="terminal_unavailable", error_category="configuration",
            history_error=f"{type(exc).__name__}: {exc}", operation="PUBLISH_ONLY",
        )
        _panel_warning(phase="terminal artifact validation", exc=exc, reason_code="terminal_unavailable", category="configuration", http_status=None)
    else:
        body, receipt = _post_status_panel_fail_open(
            current=current, repository=args.repository, repository_id=args.repository_id,
            pr_number=args.pr_number, identity=identity,
        )
    _append_panel_diagnostic(args.summary_path, receipt)
    if args.panel_delivery_path:
        _persist_panel_delivery(args.panel_delivery_path, receipt)
    return 0


def _append_panel_diagnostic(summary_path: Optional[str], receipt: dict[str, Any]) -> None:
    if (
        receipt.get("delivery") in ("created", "updated")
        and not receipt.get("history_error")
        and not receipt.get("history_incomplete")
        and not receipt.get("history_skipped_count")
        and not receipt.get("self_heal_errors")
    ):
        return
    status = receipt.get("http_status") if receipt.get("http_status") is not None else "unavailable"
    diagnostic = (
        "### Gate v2 status panel delivery diagnostic\n\n"
        f"- Delivery: `{receipt.get('delivery')}`\n"
        f"- HTTP status: `{status}`\n"
        f"- Permission category: `{receipt.get('error_category')}`\n"
        f"- Reason: `{receipt.get('reason_code')}`\n"
    )
    if receipt.get("history_error"):
        diagnostic += f"- History reconstruction: `{receipt['history_error']}`\n"
    if receipt.get("history_skipped_count"):
        diagnostic += f"- Skipped history records: `{receipt['history_skipped_count']}`\n"
        for record in receipt.get("history_skipped_records", []):
            diagnostic += f"  - `{record.get('name')}`: `{record.get('reason')}`\n"
    if receipt.get("history_incomplete_reasons"):
        diagnostic += "- History completeness: `incomplete`\n"
        for reason in receipt["history_incomplete_reasons"]:
            diagnostic += f"  - `{reason}`\n"
    if receipt.get("self_heal_errors"):
        diagnostic += "- Comment self-heal: `partial`\n"
        for error in receipt["self_heal_errors"]:
            diagnostic += f"  - `{error}`\n"
    diagnostic = scrub_for_publish(
        diagnostic,
        runtime_values=runtime_values_from_environment(),
    )
    try:
        print(diagnostic)
    except Exception:
        pass
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write("\n" + diagnostic)
        except OSError as exc:
            _warn(f"::warning::could not append the status panel diagnostic to Step Summary ({type(exc).__name__}: {exc})")


def _finish(
    outcome: Outcome, summary_path: Optional[str], *, terminal_path: Optional[str] = None, repository: Optional[str] = None,
    identity: Optional[Identity] = None, quality_result: Optional[str] = None, primary_result: Optional[str] = None,
    review_expected: Optional[bool] = None, is_draft: Optional[bool] = None, runner: Optional[str] = None,
    pr_number: Optional[int] = None, panel_delivery_path: Optional[str] = None,
) -> int:
    """Shared tail for both the normal and the malformed-input paths through
    `main()`: render + print + (optionally) persist the Step Summary, emit
    ::notice::/::error:: annotations, and map ok -> exit code."""
    summary = scrub_for_publish(
        render_summary(outcome, repository=repository, identity=identity, is_draft=is_draft, runner=runner),
        runtime_values=runtime_values_from_environment(),
    )
    print(summary)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary)
    for note in outcome.notes:
        print(f"::notice::{note}")
    for problem in outcome.problems:
        print(f"::error::{problem}")
    # Terminal-state annotation carrying the machine codes, so the checks list
    # page can tell skipped/unavailable/fail apart (and name the reason_code)
    # without expanding the run (issues #32/#43). notice for pass/skipped,
    # error otherwise — this mirrors the exit-code mapping, it does not feed
    # back into any decision.
    if outcome.classification is not None:
        tag = "notice" if outcome.ok else "error"
        print(
            f"::{tag}::gate terminal state: classification={outcome.classification}, "
            f"reason_code={outcome.reason_code}, gate_result={outcome.gate_result}"
        )
    if terminal_path and outcome.classification is not None:
        terminal = build_terminal_envelope(repository=repository or "", identity=identity, quality_result=quality_result, primary_result=primary_result, review_expected=review_expected, is_draft=is_draft, runner=runner, outcome=outcome)
        terminal_path = Path(terminal_path)
        temporary_path = terminal_path.with_name(f".{terminal_path.name}.tmp")
        temporary_path.write_text(json.dumps(terminal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(terminal_path)
    return 0 if outcome.ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality-result", required=True, help="needs.quality.result")
    parser.add_argument("--primary-result", required=True, help="needs.primary.result")
    parser.add_argument("--runner", required=True, help="inputs.runner ('self'/'hosted') — validated strictly")
    parser.add_argument("--is-draft", required=True, help="github.event.pull_request.draft ('true'/'false')")
    parser.add_argument(
        "--review-expected",
        required=True,
        help="the same non-draft && same-repo-head && runner==self expression the primary job's own "
        "`if:` uses — see gate-v2.yml and tests/test_gate_v2_contract.py",
    )
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--pr-number", default=None, type=int)
    parser.add_argument(
        "--audit-source-attempt",
        default=None,
        help="selected canonical artifact source attempt (the audit remains authoritative)",
    )
    parser.add_argument("--audit-dir", type=Path, default=None)
    parser.add_argument("--audit-artifact-name", default=None)
    parser.add_argument("--terminal-path", default=None, help="gate-terminal.json output path")
    parser.add_argument("--panel-delivery-path", default=None, help="durable status-panel delivery diagnostic JSON output path")
    parser.add_argument("--summary-path", default=None, help="$GITHUB_STEP_SUMMARY")
    parser.add_argument("--publish-only", action="store_true", help="publish the panel after terminal artifact upload")
    args = parser.parse_args(argv)

    if args.publish_only:
        if args.pr_number is None:
            _warn("::warning::status panel publish skipped because PR number is missing")
            return 0
        return _publish_only(args)

    if args.pr_number is None:
        return _finish(
            Outcome(ok=False, problems=["missing PR number — fail-closed"]),
            args.summary_path,
            repository=args.repository,
            pr_number=None,
            panel_delivery_path=args.panel_delivery_path,
        )

    audit_source_attempt: Optional[int] = None
    if args.audit_source_attempt:
        try:
            audit_source_attempt = int(args.audit_source_attempt)
            if audit_source_attempt <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return _finish(
                Outcome(
                    ok=False,
                    problems=[
                        "malformed audit source attempt — fail-closed: "
                        f"expected a decimal integer, got {args.audit_source_attempt!r}"
                    ],
                ),
                args.summary_path,
                repository=args.repository,
                pr_number=args.pr_number,
                panel_delivery_path=args.panel_delivery_path,
            )

    if not args.repository or not args.head_sha:
        return _finish(
            Outcome(ok=False, problems=["malformed repository/head_sha identity input — fail-closed"]),
            args.summary_path,
            repository=args.repository,
            pr_number=args.pr_number,
            panel_delivery_path=args.panel_delivery_path,
        )

    identity = Identity(
        repository_id=args.repository_id,
        head_sha=args.head_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        pr=args.pr_number,
    )

    try:
        is_draft = as_bool(args.is_draft)
        review_expected = as_bool(args.review_expected)
    except BoolParseError as exc:
        return _finish(
            Outcome(ok=False, problems=[f"malformed boolean input — fail-closed: {exc}"]),
            args.summary_path,
            repository=args.repository,
            pr_number=args.pr_number,
            panel_delivery_path=args.panel_delivery_path,
        )

    audit, audit_error = find_audit_file(args.audit_dir)

    outcome = evaluate(
        quality_result=args.quality_result,
        primary_result=args.primary_result,
        runner=args.runner,
        is_draft=is_draft,
        review_expected=review_expected,
        audit=audit,
        audit_error=audit_error,
        identity=identity,
        audit_source_attempt=audit_source_attempt,
        audit_artifact_name=args.audit_artifact_name or None,
    )

    return _finish(
        outcome,
        args.summary_path,
        terminal_path=args.terminal_path,
        repository=args.repository,
        identity=identity,
        quality_result=args.quality_result,
        primary_result=args.primary_result,
        review_expected=review_expected,
        is_draft=is_draft,
        runner=args.runner,
        pr_number=identity.pr,
        panel_delivery_path=args.panel_delivery_path,
    )


if __name__ == "__main__":
    sys.exit(main())
