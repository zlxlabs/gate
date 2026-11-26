"""Domain values and contracts shared by the gate aggregator's pure modules."""
from __future__ import annotations

import contextvars
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

PRIMARY_VERDICT_DOMAIN = ("pass", "fail", "unavailable", "not_expected", "waived")

TERMINAL_PRIMARY_VERDICT_DOMAIN = ("pass", "fail", "unavailable")

IDENTITY_QUINTUPLE = ("repository_id", "head_sha", "run_id", "run_attempt", "pr")

_IDENTITY_INT_FIELDS = ("repository_id", "run_id", "run_attempt", "pr")

PRIMARY_RESULT_DOMAIN = ("success", "failure", "cancelled", "skipped")

QUALITY_RESULT_DOMAIN = PRIMARY_RESULT_DOMAIN

CALLER_CHECKS_DOMAIN = ("not_started", "passed", "failed", "signal_terminated")

PREFLIGHT_RESULT_DOMAIN = ("", "success", "blocked", "unavailable", "skipped", "cancelled")

TERMINAL_CLASSIFICATION_DOMAIN = ("code_pass", "code_fail", "expected_skip", "review_unavailable", "ci_failure", "integration_error")

TERMINAL_REASON_DOMAIN = ("primary_pass", "primary_findings", "disposition_resolved", "review_not_expected", "primary_unavailable", "primary_cancelled", "quality_failure", "quality_infra", "quality_cancelled", "quality_skipped", "audit_missing", "audit_invalid", "audit_source_mismatch", "job_audit_mismatch", "unexpected_primary_skip")

GATE_RESULT_DOMAIN = ("pass", "fail", "skipped", "unavailable")

PRIMARY_SKIP_REASON_DOMAIN = ("draft", "review_exempt", "fork", "hosted_runner")

PANEL_DELIVERY_SCHEMA_VERSION = 1

PANEL_DELIVERY_KIND = "gate_v2_status_panel_delivery"

CONVERGENCE_ENVELOPE_SCHEMA_VERSION = 1

CONVERGENCE_ENVELOPE_KIND = "gate_convergence_round"

TERMINAL_HISTORY_CACHE_SCHEMA_VERSION = 1

GITHUB_API_TIMEOUT_SECONDS = 15

DEFAULT_PUBLISH_BUDGET_SECONDS = 120

PUBLISH_BUDGET_ENV = "GATE_PUBLISH_BUDGET_SECONDS"

DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS = 45

HISTORY_RECONSTRUCTION_BUDGET_ENV = "GATE_HISTORY_RECONSTRUCTION_BUDGET_SECONDS"

MAX_REPO_WIDE_HISTORY_PAGES = 5

MAX_TARGETED_HISTORY_RUNS = 50

SILO_CLI_CALL_TIMEOUT_SECONDS = 120

MAX_HISTORY_WARNING_CHARS = 500

DISPOSITION_ARTIFACT_PREFIX = "gate-disposition-receipt-v3-"

SILO_TERMINAL_TIER = "d30"

SILO_DISPOSITION_TIER = "d30"

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

PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE = 22

PRIMARY_BUDGET_EXHAUSTED_REASON = "评审总预算已耗尽，保留收尾空间"

RUNNER_DOMAIN = ("self", "hosted")

SYNTHETIC_STATUS_TIMED_OUT = "job_timed_out"

SYNTHETIC_STATUS_ARTIFACT_MISSING = "artifact_missing"

FINDING_SUMMARY_MAX_FINDINGS = 50

FINDING_SUMMARY_MAX_TITLE_CHARS = 200

PRIMARY_FINDINGS_PANEL_MARKER = "PRIMARY-FINDINGS"

FINDING_SUMMARY_UNAVAILABLE = f"{PRIMARY_FINDINGS_PANEL_MARKER}-SUMMARY-UNAVAILABLE"

_FINDING_SUMMARY_COLUMNS = ("severity", "id", "location", "title")

_COLON_RUN = re.compile(r":{2,}")

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
    skip_reason: Optional[str] = None
    audit_available: bool = False
    primary_verdict: Optional[str] = None
    convergence: Optional[dict[str, int | bool | None]] = None
    audit_source_attempt: Optional[int] = None
    audit_artifact_name: Optional[str] = None
    convergence_envelope: Optional[dict[str, Any]] = None
    convergence_shadow: Optional[dict[str, Any]] = None
    convergence_receipt: Optional[Any] = None
    recorded_disposition_claims: list[str] = field(default_factory=list)
    # Typed receipt audit is the only source for the terminal receipt record.
    disposition_audit: Optional[Any] = None
    finding_relation: Optional[dict[str, Any]] = None

@dataclass
class HistoryLoad:
    """Best-effort history projection with per-record loss diagnostics."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped_records: list[dict[str, str]] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)
    target_run_ids: Optional[tuple[int, ...]] = None

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
