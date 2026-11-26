"""Status-panel publication side effects for the gate aggregator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import time
import urllib.error
from pathlib import Path
from typing import Any, Optional

from scripts.scrub_outbound import runtime_values_from_environment, scrub_for_publish
from gate_aggregate_rendering import _primary_budget_exhausted_action, render_status_panel

from gate_aggregate_models import (  # noqa: E402
    ACTIONS_BOT_ID, ACTIONS_BOT_LOGIN, CALLER_CHECKS_DOMAIN,
    CONVERGENCE_ENVELOPE_KIND, CONVERGENCE_ENVELOPE_SCHEMA_VERSION,
    DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS, DEFAULT_PUBLISH_BUDGET_SECONDS,
    DISPOSITION_ARTIFACT_PREFIX, FINDING_SUMMARY_MAX_FINDINGS,
    FINDING_SUMMARY_MAX_TITLE_CHARS, FINDING_SUMMARY_UNAVAILABLE,
    GATE_RESULT_DOMAIN, GITHUB_API_TIMEOUT_SECONDS,
    HISTORY_RECONSTRUCTION_BUDGET_ENV, IDENTITY_QUINTUPLE,
    MAX_HISTORY_WARNING_CHARS, MAX_REPO_WIDE_HISTORY_PAGES, MAX_TARGETED_HISTORY_RUNS,
    PANEL_BUCKET_BY_GATE_RESULT, PANEL_DELIVERY_KIND, PANEL_DELIVERY_SCHEMA_VERSION,
    PANEL_HISTORY_ROW_SCHEMA_VERSION, PANEL_MARKER, PREFLIGHT_RESULT_DOMAIN,
    PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE, PRIMARY_BUDGET_EXHAUSTED_REASON,
    PRIMARY_FINDINGS_PANEL_MARKER, PRIMARY_RESULT_DOMAIN,
    PRIMARY_SKIP_REASON_DOMAIN, PRIMARY_VERDICT_DOMAIN,
    PUBLISH_BUDGET_ENV, PUBLISH_OPERATION_ORDER, QUALITY_RESULT_DOMAIN,
    RUNNER_DOMAIN, SILO_CLI_CALL_TIMEOUT_SECONDS, SILO_DISPOSITION_TIER,
    SILO_TERMINAL_TIER, SYNTHETIC_STATUS_ARTIFACT_MISSING,
    SYNTHETIC_STATUS_TIMED_OUT, TERMINAL_CLASSIFICATION_DOMAIN,
    TERMINAL_HISTORY_CACHE_SCHEMA_VERSION, TERMINAL_PRIMARY_VERDICT_DOMAIN,
    TERMINAL_REASON_DOMAIN, _ACTIVE_PUBLISH_BUDGET, _COLON_RUN,
    _FINDING_SUMMARY_COLUMNS, _IDENTITY_INT_FIELDS, BoolParseError, HistoryLoad,
    Identity, Outcome, _PublishBudget, _PublishBudgetExhausted, as_bool,
    _is_strict_int,
)

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
    operation: Optional[str] = None, attempts: Optional[int] = None,
    exception_type: Optional[str] = None, history_skipped_records: Optional[list[dict[str, str]]] = None,
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
        "attempts": attempts,
        "exception_type": exception_type,
        "identity_source": identity_source,
        "completed_operations": completed_operations or [],
        "pending_operations": pending_operations or [],
        "comment_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }

def _panel_failure(exc: BaseException, *, _ports: Any = None) -> tuple[str, str, Optional[int], str]:
    raw_reason = getattr(exc, "reason", "")
    if isinstance(raw_reason, BaseException):
        raw_reason = type(raw_reason).__name__
    if not isinstance(raw_reason, str):
        raw_reason = ""
    raw_reason = re.sub(r"https?://[^\s?#]+\?[^\s#]*", "<URL>", raw_reason)
    raw_reason = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+", r"\1[REDACTED]", raw_reason)
    raw_reason = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", raw_reason)
    raw_reason = re.sub(r"(?i)([?&][^=\s#]+)=([^&\s#]*)", r"\1=[REDACTED]", raw_reason)
    safe_reason = _ports.scrub_for_publish(raw_reason, runtime_values=_ports.runtime_values_from_environment())[:200]
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403 or exc.code == 429:
            return f"http_{exc.code}", "permission_or_rate_limit", exc.code, safe_reason
        if exc.code >= 500:
            return "http_5xx", "server_error", exc.code, safe_reason
        return "http_error", "http_error", exc.code, safe_reason
    return "network_indeterminate", "network_error", None, safe_reason

def _panel_warning(
    *, phase: str, exc: BaseException, reason_code: str, category: str,
    http_status: Optional[int], operation: Optional[str] = None,
    attempts: int = 1, safe_reason: str = "", _ports: Any = None) -> None:
    status = str(http_status) if http_status is not None else "unavailable"
    _ports._warn(
        f"::warning::gate status panel {phase} failed — HTTP status={status}; "
        f"permission category={category}; reason={reason_code}; "
        "gate verdict is unchanged and Step Summary remains authoritative"
    )
    if operation:
        _ports._warn(
            f"::warning::PANEL_PUBLISH_FAILED operation={operation} attempts={attempts} "
            f"exc_type={type(exc).__name__} reason={safe_reason[:200]}"
        )

def _bounded_panel_request(
    request: Any, *, before_retry: Optional[Any] = None, _ports: Any = None) -> tuple[Any, int, bool, Optional[Exception]]:
    """Retry only the idempotent panel GET/PATCH operations, at most three times."""
    attempts = 0
    last_error: Optional[Exception] = None
    while attempts < 3:
        attempts += 1
        try:
            return request(), attempts, False, None
        except Exception as exc:
            last_error = exc
            retryable = (
                isinstance(exc, urllib.error.HTTPError) and exc.code >= 500
            ) or (
                not isinstance(exc, urllib.error.HTTPError)
                and isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError))
            )
            if not retryable or attempts >= 3:
                return None, attempts, False, exc
        if before_retry is not None:
            try:
                if before_retry():
                    return None, attempts, True, None
            except Exception as exc:
                last_error = exc
                attempts += 1
                retryable = (
                    isinstance(exc, urllib.error.HTTPError) and exc.code >= 500
                ) or (
                    not isinstance(exc, urllib.error.HTTPError)
                    and isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError))
                )
                if not retryable or attempts >= 3:
                    return None, attempts, False, exc
        time.sleep(2)
    return None, attempts, False, last_error

def _publish_panel_patch(
    *, repository: str, comment_id: int, body: str, token: str, run_id: int, run_attempt: int, _ports: Any = None) -> tuple[int, bool, Optional[Exception]]:
    def already_written() -> bool:
        comment = _ports._github_json(
            token=token,
            url=f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}",
        )
        if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
            raise ValueError("issue comment readback has an invalid shape")
        return any(
            row["run_id"] == run_id and row["run_attempt"] == run_attempt
            for row in _ports._parse_panel_history(comment["body"])
        )

    _, attempts, recovered, error = _ports._bounded_panel_request(
        lambda: _ports._patch_issue_comment(
            repository=repository, comment_id=comment_id, body=body, token=token,
        ),
        before_retry=already_written,
    )
    return attempts, recovered, error

def _post_status_panel_fail_open_with_budget(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity], budget: _PublishBudget,
    silo_history: Optional[HistoryLoad] = None, _ports: Any = None) -> tuple[str, dict[str, Any]]:
    """Publish the one marker-located status panel without affecting verdict."""
    body = _ports.scrub_for_publish(
        _ports.render_status_panel([current]),
        runtime_values=_ports.runtime_values_from_environment(),
    )
    identity_source: Optional[str] = None
    failure_operation: Optional[str] = None
    failure_attempts = 1
    comment_was_created = False
    try:
        if not repository or pr_number is None:
            reason_code, category, status = "missing_target", "configuration", None
            _ports._panel_warning(phase="target resolution", exc=ValueError("missing target"), reason_code=reason_code, category=category, http_status=status)
            return body, _ports._build_panel_delivery(body=body, repository=repository, pr_number=pr_number, identity=identity, delivery="not_created", reason_code=reason_code, error_category=category, http_status=status)
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            reason_code, category, status = "missing_token", "configuration", None
            _ports._panel_warning(phase="authentication", exc=ValueError("missing token"), reason_code=reason_code, category=category, http_status=status)
            return body, _ports._build_panel_delivery(body=body, repository=repository, pr_number=pr_number, identity=identity, delivery="not_created", reason_code=reason_code, error_category=category, http_status=status)

        try:
            budget.begin("IDENTITY")
            owner = _ports._github_identity(token)
            budget.complete("IDENTITY")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            reason_code, category, status, _ = _ports._panel_failure(exc)
            _ports._panel_warning(phase="identity", exc=exc, reason_code=reason_code, category=category, http_status=status)
            return body, _ports._build_panel_delivery(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code=reason_code, error_category=category,
                http_status=status, operation="IDENTITY",
                completed_operations=budget.completed_operations, pending_operations=budget.pending_operations,
            )
        identity_source = owner.get("identity_source", "user_api")
        budget.begin("COMMENT_LOOKUP")
        failure_operation = "comment_lookup"
        comments, failure_attempts, _, lookup_error = _ports._bounded_panel_request(
            lambda: _ports._fetch_panel_comments(token=token, repository=repository, pr_number=pr_number),
        )
        if lookup_error is not None:
            raise lookup_error
        budget.complete("COMMENT_LOOKUP")
        failure_operation = None
        failure_attempts = 1
        own_panels = _ports._find_panel_comments(comments, owner)
        existing = own_panels[0] if own_panels else None
        cached_rows = _ports._parse_panel_history(existing.get("body", "")) if existing else []
        target_run_ids = [row["run_id"] for row in cached_rows] if cached_rows else None
        try:
            budget.begin("HISTORY_RECONSTRUCTION")
            history = _ports._fetch_terminal_history(
                token=token, repository=repository, repository_id=repository_id or 0,
                pr_number=pr_number, target_run_ids=target_run_ids,
                silo_history=silo_history,
            )
            budget.complete("HISTORY_RECONSTRUCTION")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            reason_code, category, status, safe_reason = _ports._panel_failure(exc)
            _ports._panel_warning(
                phase="history reconstruction", exc=exc, reason_code=reason_code,
                category=category, http_status=status, operation="history_load",
                attempts=1, safe_reason=safe_reason,
            )
            cached_body = existing.get("body", body) if existing else body
            return cached_body, _ports._build_panel_delivery(
                body=cached_body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code="history_unavailable", error_category=category,
                http_status=status, history_error=f"{type(exc).__name__}: {safe_reason}",
                operation="history_load", attempts=1, exception_type=type(exc).__name__,
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
        body = _ports.scrub_for_publish(
            _ports.render_status_panel(
                _ports._merge_panel_rows(current, [*history.rows, *cached_rows]),
                history_reasons=incomplete_reasons if incomplete_reasons else None,
            ),
            runtime_values=_ports.runtime_values_from_environment(),
        )
        self_heal_errors: list[str] = []
        if existing:
            budget.discard("POST_VERIFY")
            budget.begin("COMMENT_PUBLISH")
            failure_operation = "comment_patch"
            failure_attempts, _, patch_error = _ports._publish_panel_patch(
                repository=repository, comment_id=int(existing["id"]), body=body, token=token,
                run_id=current["run_id"], run_attempt=current["run_attempt"],
            )
            if patch_error is not None:
                raise patch_error
            budget.complete("COMMENT_PUBLISH")
            failure_operation = None
            for duplicate in own_panels[1:]:
                budget.add("SELF_HEAL")
                budget.begin("SELF_HEAL")
                try:
                    _ports._delete_issue_comment(repository=repository, comment_id=int(duplicate["id"]), token=token)
                    budget.complete("SELF_HEAL")
                except _PublishBudgetExhausted:
                    raise
                except Exception as exc:
                    self_heal_errors.append(f"comment {duplicate.get('id')}: {type(exc).__name__}: {exc}")
            if not own_panels[1:]:
                budget.discard("SELF_HEAL")
            return body, _ports._build_panel_delivery(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="updated", reason_code="history_incomplete" if incomplete_reasons else "patched",
                operation="PATCH", history_skipped_records=history.skipped_records,
                history_incomplete_reasons=incomplete_reasons, self_heal_errors=self_heal_errors,
                identity_source=identity_source, completed_operations=budget.completed_operations,
                pending_operations=budget.pending_operations,
            )
        budget.begin("COMMENT_PUBLISH")
        failure_operation = "comment_create"
        _ports._post_issue_comment(repository=repository, pr_number=pr_number, body=body, token=token)
        budget.complete("COMMENT_PUBLISH")
        comment_was_created = True
        failure_operation = None
        budget.begin("POST_VERIFY")
        try:
            after_post = _ports._find_panel_comments(
                _ports._fetch_panel_comments(token=token, repository=repository, pr_number=pr_number), owner,
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
                    failure_operation = "comment_patch"
                    failure_attempts, _, patch_error = _ports._publish_panel_patch(
                        repository=repository, comment_id=int(winner["id"]), body=body, token=token,
                        run_id=current["run_id"], run_attempt=current["run_attempt"],
                    )
                    if patch_error is not None:
                        raise patch_error
                    failure_operation = None
                    for duplicate in after_post[1:]:
                        _ports._delete_issue_comment(repository=repository, comment_id=int(duplicate["id"]), token=token)
                    budget.complete("SELF_HEAL")
                except _PublishBudgetExhausted:
                    raise
                except Exception as exc:
                    if failure_operation == "comment_patch":
                        raise
                    self_heal_errors.append(f"post self-heal: {type(exc).__name__}: {exc}")
            else:
                budget.discard("SELF_HEAL")
        else:
            budget.discard("SELF_HEAL")
        return body, _ports._build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="created", reason_code="post_self_heal_partial" if self_heal_errors else "posted",
            operation="POST", history_skipped_records=history.skipped_records,
            history_incomplete_reasons=incomplete_reasons, self_heal_errors=self_heal_errors,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )
    except _PublishBudgetExhausted as exc:
        delivery = "unknown" if failure_operation in ("comment_patch", "comment_create") else "not_created"
        _, _, _, safe_reason = _ports._panel_failure(exc)
        _ports._panel_warning(
            phase="publish budget", exc=exc, reason_code="publish_budget_exhausted",
            category="network_error", http_status=None, operation=failure_operation,
            attempts=failure_attempts, safe_reason=safe_reason,
        )
        return body, _ports._build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery=delivery, reason_code="publish_budget_exhausted", error_category="network_error",
            operation=failure_operation or exc.operation, attempts=failure_attempts,
            exception_type=type(exc).__name__, identity_source=identity_source,
            completed_operations=budget.completed_operations, pending_operations=budget.pending_operations,
        )
    except urllib.error.HTTPError as exc:
        reason_code, category, status, safe_reason = _ports._panel_failure(exc)
        _ports._panel_warning(
            phase="comment publish", exc=exc, reason_code=reason_code,
            category=category, http_status=status, operation=failure_operation,
            attempts=failure_attempts, safe_reason=safe_reason,
        )
        return body, _ports._build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery=(
                "unknown" if comment_was_created and failure_operation == "comment_patch"
                else "not_created" if status is not None and status < 500 else "unknown"
            ),
            reason_code=reason_code, error_category=category, http_status=status,
            operation=failure_operation, attempts=failure_attempts,
            exception_type=type(exc).__name__,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )
    except Exception as exc:
        reason_code, category, status, safe_reason = _ports._panel_failure(exc)
        _ports._panel_warning(
            phase="comment publish", exc=exc, reason_code=reason_code,
            category=category, http_status=status, operation=failure_operation,
            attempts=failure_attempts, safe_reason=safe_reason,
        )
        return body, _ports._build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="unknown", reason_code=reason_code, error_category=category, http_status=status,
            operation=failure_operation, attempts=failure_attempts,
            exception_type=type(exc).__name__,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )

def _post_status_panel_fail_open(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity],
    silo_history: Optional[HistoryLoad] = None, _ports: Any = None) -> tuple[str, dict[str, Any]]:
    body = _ports.render_status_panel([current])
    try:
        budget = _PublishBudget.from_environment()
    except ValueError as exc:
        _ports._panel_warning(
            phase="publish budget", exc=exc, reason_code="invalid_publish_budget",
            category="configuration", http_status=None,
        )
        return body, _ports._build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="not_created", reason_code="invalid_publish_budget", error_category="configuration",
        )
    token = _ACTIVE_PUBLISH_BUDGET.set(budget)
    try:
        return _ports._post_status_panel_fail_open_with_budget(
            current=current, repository=repository, repository_id=repository_id,
            pr_number=pr_number, identity=identity, budget=budget,
            silo_history=silo_history,
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
    row = {
        "schema_version": PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": repository or "",
        "run_id": identity.run_id if identity else 0,
        "run_attempt": identity.run_attempt if identity else 0,
        "head_sha": identity.head_sha if identity else "unknown",
        "gate_result": gate_result,
        "classification": classification,
        "reason_code": reason_code,
    }
    if outcome.recorded_disposition_claims:
        row["recorded_disposition_claims"] = list(outcome.recorded_disposition_claims)
    return row

def _persist_panel_delivery(path: str, receipt: dict[str, Any], *, _ports: Any = None) -> None:
    """Persist the second-exit receipt while clearing stale evidence safely."""
    receipt_path = Path(path)
    receipt_unlink_failed = False
    try:
        receipt_path.unlink(missing_ok=True)
    except OSError as exc:
        receipt_unlink_failed = True
        _ports._warn(f"::warning::could not remove the previous gate PR-comment receipt ({type(exc).__name__}: {exc})")
    try:
        _ports._write_panel_delivery(path, receipt)
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
                    _ports._warn(
                        "::warning::gate PR-comment receipt write failed and the stale receipt "
                        f"could not be cleared ({type(invalidate_exc).__name__}: {invalidate_exc}); "
                        "receipt channel is untrusted"
                    )
                else:
                    _ports._warn("::warning::gate PR-comment receipt write failed and the stale receipt was destroyed; an invalid marker was written, upload will pass but consumers' json.loads will fail-loud")
            else:
                _ports._warn("::warning::gate PR-comment receipt write failed and the stale receipt was cleared; upload will red")
        elif receipt_path.exists():
            _ports._warn(f"::warning::gate PR-comment receipt write failed ({type(exc).__name__}: {exc}); receipt channel is untrusted")
        else:
            _ports._warn(f"::warning::gate PR-comment receipt write failed ({type(exc).__name__}: {exc}); file is missing and upload will red")

def _publish_only(args: argparse.Namespace, *, _ports: Any = None) -> int:
    """Publish only after the caller has uploaded the terminal envelope."""
    identity = Identity(
        repository_id=args.repository_id,
        head_sha=args.head_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        pr=args.pr_number,
    )
    current: dict[str, Any]
    silo_history: Optional[HistoryLoad] = None
    try:
        if not args.terminal_path:
            raise ValueError("terminal path is missing")
        record = json.loads(Path(args.terminal_path).read_text(encoding="utf-8"))
        current = _ports._terminal_row(record, repository=args.repository, repository_id=args.repository_id, pr_number=args.pr_number)
        history_cache_path = _ports._terminal_history_cache_path(args.terminal_path)
        if history_cache_path.is_file():
            try:
                silo_history = _ports._read_terminal_history_cache(history_cache_path)
            except (OSError, ValueError) as exc:
                print(f"GATE-CONVERGENCE-HISTORY-CACHE-UNAVAILABLE reason={type(exc).__name__}")
                silo_history = HistoryLoad(incomplete_reasons=["terminal history cache unavailable"])
        elif record.get("convergence_shadow") is not None:
            print("GATE-CONVERGENCE-HISTORY-CACHE-UNAVAILABLE reason=missing")
            silo_history = HistoryLoad(incomplete_reasons=["terminal history cache missing"])
    except Exception as exc:
        current = _ports._panel_current_row(
            outcome=Outcome(ok=False, classification="integration_error", reason_code="audit_invalid", gate_result="unavailable"),
            repository=args.repository,
            identity=identity,
        )
        body = _ports.scrub_for_publish(
            _ports.render_status_panel(
                [current],
                history_warning=f"terminal artifact unavailable: {type(exc).__name__}: {exc}",
            ),
            runtime_values=_ports.runtime_values_from_environment(),
        )
        receipt = _ports._build_panel_delivery(
            body=body, repository=args.repository, pr_number=args.pr_number, identity=identity,
            delivery="not_created", reason_code="terminal_unavailable", error_category="configuration",
            history_error=f"{type(exc).__name__}: {exc}", operation="PUBLISH_ONLY",
        )
        _ports._panel_warning(phase="terminal artifact validation", exc=exc, reason_code="terminal_unavailable", category="configuration", http_status=None)
    else:
        audit_dir = args.audit_dir
        runner_temp = os.environ.get("RUNNER_TEMP")
        if audit_dir is None and runner_temp:
            audit_dir = Path(runner_temp) / "primary-audit"
        primary_audit, _, _ = _ports._read_audit_file(audit_dir)
        if primary_audit is not None and not _ports.validate_audit_identity(primary_audit, identity):
            current["primary_audit"] = primary_audit
        body, receipt = _ports._post_status_panel_fail_open(
            current=current, repository=args.repository, repository_id=args.repository_id,
            pr_number=args.pr_number, identity=identity, silo_history=silo_history,
        )
    _ports._append_panel_diagnostic(args.summary_path, receipt)
    if args.panel_delivery_path:
        _ports._persist_panel_delivery(args.panel_delivery_path, receipt)
    return 0

def _append_panel_diagnostic(summary_path: Optional[str], receipt: dict[str, Any], *, _ports: Any = None) -> None:
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
    if receipt.get("exception_type"):
        diagnostic += "- 面板发布失败，面板可能显示旧状态\n"
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
    diagnostic = _ports.scrub_for_publish(
        diagnostic,
        runtime_values=_ports.runtime_values_from_environment(),
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
            _ports._warn(f"::warning::could not append the status panel diagnostic to Step Summary ({type(exc).__name__}: {exc})")
