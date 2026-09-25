#!/usr/bin/env python3
"""Build the current run's artifact-backed Codex review effectiveness ledger row."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GATE_ROOT = Path(__file__).resolve().parents[3]
if str(GATE_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE_ROOT))

from scripts.scrub_outbound import runtime_values_from_environment, scrub_for_publish


DISPOSITION_STATUSES = frozenset({"success", "failure"})
PRIMARY_STATUS_BY_VERDICT = {
    "pass": "pass", "fail": "fail", "unavailable": "unavailable",
    "not_expected": "not_expected", "waived": "waived",
}
PRIMARY_IDENTITY_FIELDS = (
    "repository_id", "repository", "pr", "base_sha", "head_sha", "diff_digest",
    "policy_version", "policy_digest", "registry_commit", "caller_sha",
    "reusable_workflow_sha", "run_id", "run_attempt", "job_id", "reviewer",
    "merge_base_sha", "candidate_commit_sha", "candidate_tree_sha", "run_mode",
    "spec_source", "pr_body_digest", "tier",
)
PRIMARY_REQUIRED_IDENTITY_FIELDS = (
    "repository_id", "repository", "pr", "base_sha", "head_sha", "diff_digest",
    "policy_version", "policy_digest", "registry_commit", "caller_sha",
    "reusable_workflow_sha", "run_id", "run_attempt", "job_id", "reviewer",
)
PRIMARY_SCOPE_FIELDS = ("merge_base_sha", "candidate_commit_sha", "candidate_tree_sha", "run_mode")
PRIMARY_SPEC_FIELDS = ("spec_source", "pr_body_digest")
PRIMARY_ALLOWED_FIELDS = set(PRIMARY_IDENTITY_FIELDS) | {
    "kind", "schema_version", "verdict", "attempts", "shadow_mode", "expected_shadows",
    "result", "cost", "tokens", "runtime", "not_expected_reason", "waiver",
}
PRIMARY_VERDICTS = {"pass", "fail", "unavailable", "not_expected", "waived"}
PRIMARY_REVIEWER_VERDICTS = {"pass", "fail", "unavailable"}
PRIMARY_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

LEDGER_ENTRY_REQUIRED_FIELDS = (
    "schema_version", "recorded_at", "repository", "pr_number", "run_id",
    "run_attempt", "head_sha", "disposition_status", "preflight", "install",
    "primary_identity", "review", "finding_dispositions", "false_positive_count",
)
LEDGER_ENTRY_OPTIONAL_FIELDS = (
    "disposition_receipt_consumption", "terminal_source_attempt", "finding_relation",
)
_RELATION_VALUES = frozenset({"new", "repeat", "conflict"})
LEDGER_ENTRY_FIELDS = LEDGER_ENTRY_REQUIRED_FIELDS + LEDGER_ENTRY_OPTIONAL_FIELDS


def _compact_attempts(audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Keep a stable, ledger-friendly hop summary (no tokens blobs)."""
    if not audit:
        return []
    raw = audit.get("attempts") or []
    if not isinstance(raw, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        compact.append({
            "reviewer": item.get("reviewer"),
            "exit_code": item.get("exit_code"),
            "reason": item.get("reason") or "",
            "duration_s": item.get("duration_s"),
            "cost_usd": item.get("cost_usd"),
            # Optional short redacted adapter diagnostic (e.g. "api_error_status=529 …访问量过大").
            "diag_snippet": item.get("diag_snippet"),
        })
    return compact


def _require_finite_nonnegative(value: Any, field: str) -> None:
    if value is None:
        return
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float))
        valid = valid and math.isfinite(float(value)) and value >= 0
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"canonical primary {field} must be finite and non-negative or null")


def _review_summary(
    audit: dict[str, Any] | None, fallback_status: str, preflight: dict[str, Any] | None = None,
    *,
    input_short_circuited: bool = False,
) -> dict[str, Any]:
    if not audit:
        return {
            "status": fallback_status,
            "verdict": None,
            "result": None,
            "cost_usd": None,
            "tokens": None,
            "finding_count": 0,
            "finding_ids": [],
            "severity_counts": {},
            "category_counts": {},
            "trigger_kind_counts": {},
            "inferred_p1_count": 0,
            "coverage": None,
            "runtime": None,
            "shadows": {},
            # P0 observability: who adjudicated + hop path (additive; old readers ignore).
            "reviewer": None,
            "attempts": [],
            "failover": False,
        }
    result = audit.get("result")
    is_primary_v2 = audit.get("kind") == "primary_review"
    if is_primary_v2:
        if input_short_circuited and (preflight is None or preflight == {}):
            coverage = None
        else:
            if not isinstance(preflight, dict):
                raise ValueError("canonical primary preflight must be an object")
            thresholds = preflight.get("thresholds")
            diff_lines = preflight.get("diff_lines")
            if (
                isinstance(diff_lines, bool) or not isinstance(diff_lines, int) or diff_lines < 0
                or not isinstance(preflight.get("classification"), str)
                or not isinstance(preflight.get("review_plan"), str)
                or not isinstance(thresholds, dict)
                or isinstance(thresholds.get("single_turn_lines"), bool)
                or not isinstance(thresholds.get("single_turn_lines"), int)
                or thresholds["single_turn_lines"] <= 0
            ):
                raise ValueError("canonical primary preflight has invalid coverage shape")
            coverage_complete = diff_lines <= thresholds["single_turn_lines"]
            coverage = {
                "mode": "single" if coverage_complete else "sharded+cross-module integration",
                "complete": coverage_complete,
                "diff_lines": diff_lines,
                "shards": 1 if coverage_complete else None,
            }
        findings = result["findings"] if isinstance(result, dict) else []
        cost_usd, tokens = audit.get("cost"), audit.get("tokens")
        expected_shadows = audit["expected_shadows"]
        shadows = {} if not expected_shadows else {
            "shadow_mode": "detached",
            "status": "detached_unavailable",
            "expected_shadows": list(expected_shadows),
            "outcomes": None,
        }
    else:
        result = result or {}
        findings = result.get("findings") or []
        coverage = audit.get("coverage")
        cost_usd, tokens, shadows = audit.get("cost_usd"), audit.get("tokens"), audit.get("shadows", {})
    attempts = _compact_attempts(audit)
    status = PRIMARY_STATUS_BY_VERDICT[audit["verdict"]] if is_primary_v2 else audit.get("status", "unknown")
    # Failover = more than one hop was tried (a discarded hop precedes the adopted one).
    failover = len(attempts) > 1
    trigger_kind_counts: Counter[str] = Counter()
    inferred_p1_count = 0
    for finding in findings:
        kind = finding.get("trigger_kind")
        if not isinstance(kind, str) or kind not in {"measured", "inferred", "unmeasurable"}:
            kind = "unspecified"
        trigger_kind_counts[kind] += 1
        if kind == "inferred" and finding.get("severity") in {"blocker", "major"}:
            inferred_p1_count += 1
    return {
        "status": status,
        "verdict": status if is_primary_v2 else result.get("verdict"),
        "result": audit.get("result"),
        "cost_usd": cost_usd,
        "tokens": tokens,
        "finding_count": len(findings),
        "finding_ids": sorted({finding.get("id", "") for finding in findings if finding.get("id")}),
        "severity_counts": dict(sorted(Counter(finding.get("severity", "unknown") for finding in findings).items())),
        "category_counts": dict(sorted(Counter(finding.get("category", "unknown") for finding in findings).items())),
        "trigger_kind_counts": dict(sorted(trigger_kind_counts.items())),
        "inferred_p1_count": inferred_p1_count,
        "coverage": coverage,
        "runtime": audit.get("runtime"),
        "shadows": shadows,
        "reviewer": audit.get("reviewer"),
        "attempts": attempts,
        "failover": failover,
    }


def _primary_identity(
    audit: dict[str, Any] | None, *, repository: str, pr_number: int,
    run_id: int, run_attempt: int, head_sha: str,
    expected_identity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if audit is None:
        return None
    if not isinstance(audit, dict):
        raise ValueError("review audit must be a JSON object")
    if audit.get("kind") != "primary_review":
        return None
    verdict = audit.get("verdict")
    schema_version = audit.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2} or verdict not in PRIMARY_VERDICTS:
        raise ValueError("invalid canonical primary_review schema/version or verdict")
    extra = set(audit) - PRIMARY_ALLOWED_FIELDS
    missing = set(PRIMARY_REQUIRED_IDENTITY_FIELDS) - set(audit)
    if extra or missing:
        raise ValueError(
            f"invalid canonical primary envelope: extra={sorted(extra)}, missing={sorted(missing)}"
        )
    if "tier" in audit:
        tier = audit["tier"]
        if not (isinstance(tier, str) and tier in {"personal", "internal", "saas"}):
            raise ValueError("canonical primary tier must be personal, internal, or saas")
    if (
        isinstance(audit["repository_id"], bool) or not isinstance(audit["repository_id"], int)
        or audit["repository_id"] <= 0
    ):
        raise ValueError("canonical primary repository_id must be a positive integer")
    for field in ("repository", "base_sha", "head_sha", "policy_version", "registry_commit",
                  "caller_sha", "reusable_workflow_sha"):
        if not isinstance(audit[field], str) or not audit[field]:
            raise ValueError(f"canonical primary {field} must be a non-empty string")
    for field in ("pr", "run_id", "run_attempt", "job_id"):
        if isinstance(audit[field], bool) or not isinstance(audit[field], int) or audit[field] <= 0:
            raise ValueError(f"canonical primary {field} must be a positive integer")
    for field in ("diff_digest", "policy_digest"):
        if not isinstance(audit[field], str) or not PRIMARY_SHA256_RE.fullmatch(audit[field]):
            raise ValueError(f"canonical primary {field} must be a lowercase SHA-256 digest")
    present_scope = [field for field in PRIMARY_SCOPE_FIELDS if field in audit]
    if present_scope and set(present_scope) != set(PRIMARY_SCOPE_FIELDS):
        raise ValueError("canonical primary scope provenance must be complete")
    if present_scope:
        for field in PRIMARY_SCOPE_FIELDS[:3]:
            if not isinstance(audit[field], str) or not audit[field]:
                raise ValueError(f"canonical primary {field} must be a non-empty string")
        if not isinstance(audit["run_mode"], str) or audit["run_mode"] not in {"PAYLOAD_ONLY", "FULL_SOURCE"}:
            raise ValueError("canonical primary run_mode is invalid")
    present_spec = [field for field in PRIMARY_SPEC_FIELDS if field in audit]
    if present_spec and set(present_spec) != set(PRIMARY_SPEC_FIELDS):
        raise ValueError("canonical primary spec provenance must be complete")
    if present_spec:
        if not isinstance(audit["spec_source"], str) or audit["spec_source"] not in {"live", "event_payload"}:
            raise ValueError("canonical primary spec_source is invalid")
        digest = audit["pr_body_digest"]
        if digest != "empty" and (
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{12}", digest)
        ):
            raise ValueError("canonical primary pr_body_digest is invalid")
    expected = {"repository": repository, "pr": pr_number, "run_id": run_id, "head_sha": head_sha}
    expected.update(expected_identity or {})
    mismatches = [field for field, value in expected.items() if audit.get(field) != value]
    source_attempt = audit.get("run_attempt")
    current_attempt = expected.get("run_attempt", run_attempt)
    if not isinstance(source_attempt, int) or isinstance(source_attempt, bool) or not 1 <= source_attempt <= current_attempt:
        mismatches.append("run_attempt")
    if mismatches:
        raise ValueError(f"primary audit identity mismatch: {sorted(set(mismatches))}")
    if verdict != "not_expected" and "not_expected_reason" in audit:
        raise ValueError("canonical primary not_expected_reason is only valid for not_expected")
    if verdict != "waived" and "waiver" in audit:
        raise ValueError("canonical primary waiver is only valid for waived")
    reviewer = audit["reviewer"]
    if verdict in PRIMARY_REVIEWER_VERDICTS and (not isinstance(reviewer, str) or not reviewer):
        raise ValueError("canonical primary reviewer must be a non-empty string")
    if verdict in {"not_expected", "waived"} and reviewer is not None:
        raise ValueError("canonical primary reviewer must be None when no reviewer ran")
    if audit["shadow_mode"] != "detached":
        raise ValueError("canonical primary shadow_mode must be 'detached'")
    if not isinstance(audit["expected_shadows"], list) or not all(
        isinstance(name, str) and name for name in audit["expected_shadows"]
    ):
        raise ValueError("canonical primary expected_shadows must be an array of names")
    attempts = audit["attempts"]
    if not isinstance(attempts, list):
        raise ValueError("canonical primary attempts must be an array of objects")
    required_attempt_fields = ("reviewer", "exit_code", "reason", "duration_s", "cost_usd")
    attempt_durations = []
    for attempt in attempts:
        if not isinstance(attempt, dict) or any(field not in attempt for field in required_attempt_fields):
            raise ValueError("canonical primary attempts have an invalid consumed shape")
        if not isinstance(attempt["reviewer"], str) or not attempt["reviewer"]:
            raise ValueError("canonical primary attempt reviewer must be a non-empty string")
        if isinstance(attempt["exit_code"], bool) or not isinstance(attempt["exit_code"], int):
            raise ValueError("canonical primary attempt exit_code must be an integer")
        if not isinstance(attempt["reason"], str):
            raise ValueError("canonical primary attempt reason must be a string")
        if attempt["duration_s"] is None: raise ValueError("canonical primary attempt duration_s must be a number")
        _require_finite_nonnegative(attempt["duration_s"], "attempt duration_s")
        _require_finite_nonnegative(attempt["cost_usd"], "attempt cost_usd")
        attempt_durations.append(attempt["duration_s"])
        if "diag_snippet" in attempt and attempt["diag_snippet"] is not None and not isinstance(attempt["diag_snippet"], str):
            raise ValueError("canonical primary attempt diag_snippet must be a string or null")
    if schema_version == 2 and "runtime" not in audit:
        raise ValueError("canonical primary telemetry fields are missing: ['runtime']")
    if verdict in PRIMARY_REVIEWER_VERDICTS:
        missing = {field for field in ("result", "cost", "tokens") if field not in audit}
        if missing:
            raise ValueError(f"canonical primary telemetry fields are missing: {sorted(missing)}")
    _require_finite_nonnegative(audit.get("cost"), "cost")
    tokens = audit.get("tokens")
    if tokens is not None and not isinstance(tokens, list):
        raise ValueError("canonical primary tokens must be an array or null")
    runtime_present = "runtime" in audit
    runtime = audit.get("runtime")
    if runtime is not None:
        if not isinstance(runtime, dict) or set(runtime) != {"duration_s"}:
            raise ValueError("canonical primary runtime must be null or {duration_s}")
        if runtime["duration_s"] is None: raise ValueError("canonical primary runtime duration_s must be a number")
        _require_finite_nonnegative(runtime["duration_s"], "runtime duration_s")
    if not attempts and runtime is not None:
        raise ValueError("canonical primary runtime must be null when attempts are empty")
    if attempts and (schema_version == 2 or runtime_present) and (
        runtime is None or runtime["duration_s"] != sum(attempt_durations)
    ):
        raise ValueError("canonical primary runtime must equal attempt duration sum")
    result = audit.get("result")
    if result is None:
        if verdict in {"pass", "fail"}:
            raise ValueError("canonical primary result is required for pass/fail")
    elif not isinstance(result, dict) or result.get("verdict") != verdict:
        raise ValueError("canonical primary result.verdict must match terminal verdict")
    elif not isinstance(result.get("summary"), str) or not result["summary"]:
        raise ValueError("canonical primary result.summary must be a non-empty string")
    if isinstance(result, dict):
        findings = result.get("findings")
        if not isinstance(findings, list):
            raise ValueError("canonical primary result.findings must be an array")
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("canonical primary finding must be an object")
            for field in ("id", "severity", "category"):
                if not isinstance(finding.get(field), str) or not finding[field]:
                    raise ValueError(f"canonical primary finding {field} must be a non-empty string")
    if verdict in {"not_expected", "waived"}:
        if (attempts != [] or result is not None or audit.get("cost") is not None or audit.get("tokens") is not None
                or audit.get("runtime") is not None
                or (verdict == "not_expected" and "waiver" in audit)
                or (verdict == "waived" and "not_expected_reason" in audit)):
            raise ValueError("canonical primary no-review audit cannot carry review content")
        if verdict == "not_expected" and (not isinstance(audit.get("not_expected_reason"), str) or audit.get("not_expected_reason") not in {
            "fork", "hosted_runner", "no_review_policy"
        }):
            raise ValueError("canonical primary not_expected_reason is invalid")
        if verdict == "waived":
            waiver = audit.get("waiver")
            if (
                not isinstance(waiver, dict) or set(waiver) != {"approver", "approved_at", "reason"}
                or any(not isinstance(waiver[field], str) or not waiver[field] for field in waiver)
                or "T" not in waiver["approved_at"]
            ):
                raise ValueError("canonical primary waiver has invalid shape")
    return {field: audit[field] for field in PRIMARY_IDENTITY_FIELDS if field in audit}


def empty_disposition_receipt_audit() -> dict[str, Any]:
    """Empty schema-2 receipt block for a run without recorded claims."""
    return {
        "recorded": [],
        "resolved": [],
        "consumed_count": 0,
        "rejected_count": 0,
        "rejected_reasons": {},
        "fail_closed": False,
    }


def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_gate_terminal_envelope(path: Path) -> dict[str, Any]:
    """Read the same-run gate-terminal artifact; missing or corrupt is fail-loud."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("gate terminal artifact is missing or empty")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("gate terminal artifact is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("gate terminal artifact is not a JSON object")
    if payload.get("schema_version") != 1 or payload.get("kind") != "gate_terminal":
        raise ValueError("gate terminal artifact has an unsupported schema")
    return payload


def validate_disposition_receipt_audit(block: Any) -> dict[str, Any]:
    """Validate submitter claims and retain legacy schema-2 fields."""
    if not isinstance(block, dict):
        raise ValueError("disposition_receipt_consumption must be an object")
    for key in ("resolved", "consumed_count", "rejected_count", "rejected_reasons", "fail_closed"):
        if key not in block:
            raise ValueError(f"disposition_receipt_consumption is missing {key}")
    resolved = block["resolved"]
    if not isinstance(resolved, list):
        raise ValueError("disposition_receipt_consumption.resolved must be an array")
    projected: list[dict[str, Any]] = []
    display_fields = (
        "disposition", "disposition_claim", "evidence_pointer",
        "counterevidence_command", "counterevidence_output",
    )
    for item in resolved:
        if not isinstance(item, dict):
            raise ValueError("disposition_receipt_consumption.resolved item must be an object")
        for key in ("finding_id", "receipt", "approver", "approver_id", "approved_at", "reason"):
            if key not in item:
                raise ValueError(f"disposition_receipt_consumption.resolved item is missing {key}")
        for key in ("finding_id", "receipt", "approver", "approved_at", "reason"):
            if not isinstance(item[key], str) or not item[key]:
                raise ValueError("disposition_receipt_consumption.resolved item has an invalid text field")
        if not _strict_int(item["approver_id"]) or item["approver_id"] <= 0:
            raise ValueError("disposition_receipt_consumption.resolved item approver_id must be a positive integer")
        projected_item = {
            "finding_id": item["finding_id"],
            "receipt": item["receipt"],
            "approver": item["approver"],
            "approver_id": item["approver_id"],
            "approved_at": item["approved_at"],
            "reason": item["reason"],
        }
        if "finding_key" in item:
            if not isinstance(item["finding_key"], str) or not item["finding_key"]:
                raise ValueError("disposition_receipt_consumption.resolved item has an invalid finding_key")
            projected_item["finding_key"] = item["finding_key"]
        for key in display_fields:
            if key not in item:
                continue
            value = item[key]
            if not isinstance(value, str) or not value or len(value) > 500:
                raise ValueError(f"disposition_receipt_consumption.resolved item has invalid {key}")
            if key in {"disposition", "disposition_claim"} and value not in {"false-positive", "deferred"}:
                raise ValueError(f"disposition_receipt_consumption.resolved item has invalid {key}")
            projected_item[key] = value
        projected.append(projected_item)
    consumed_count = block["consumed_count"]
    rejected_count = block["rejected_count"]
    if not _strict_int(consumed_count) or consumed_count < 0:
        raise ValueError("disposition_receipt_consumption.consumed_count must be a non-negative integer")
    if not _strict_int(rejected_count) or rejected_count < 0:
        raise ValueError("disposition_receipt_consumption.rejected_count must be a non-negative integer")
    if consumed_count != len(projected):
        raise ValueError("disposition_receipt_consumption.consumed_count does not match resolved")
    reasons = block["rejected_reasons"]
    if not isinstance(reasons, dict) or any(
        not isinstance(key, str) or not key or not _strict_int(value) or value <= 0
        for key, value in reasons.items()
    ):
        raise ValueError("disposition_receipt_consumption.rejected_reasons must map reasons to positive counts")
    if sum(reasons.values()) != rejected_count:
        raise ValueError("disposition_receipt_consumption.rejected_count does not match rejected_reasons")
    fail_closed = block["fail_closed"]
    if type(fail_closed) is not bool:
        raise ValueError("disposition_receipt_consumption.fail_closed must be a boolean")
    recorded = block.get("recorded", [])
    if not isinstance(recorded, list):
        raise ValueError("disposition_receipt_consumption.recorded must be an array")
    projected_claims: list[dict[str, Any]] = []
    for item in recorded:
        if not isinstance(item, dict):
            raise ValueError("disposition_receipt_consumption.recorded item must be an object")
        for key in ("finding_id", "receipt", "disposition_claim", "triggering_actor", "recorded_at", "reason"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ValueError(f"disposition_receipt_consumption.recorded item has invalid {key}")
        if not _strict_int(item.get("triggering_actor_id")) or item["triggering_actor_id"] <= 0:
            raise ValueError("disposition_receipt_consumption.recorded triggering_actor_id must be a positive integer")
        projected_claim = {key: item[key] for key in (
            "finding_id", "receipt", "disposition_claim", "triggering_actor",
            "triggering_actor_id", "recorded_at", "reason",
        )}
        if "finding_key" in item:
            if not isinstance(item["finding_key"], str) or not item["finding_key"]:
                raise ValueError("disposition_receipt_consumption.recorded has invalid finding_key")
            projected_claim["finding_key"] = item["finding_key"]
        for key in display_fields:
            if key not in item:
                continue
            value = item[key]
            if not isinstance(value, str) or not value or len(value) > 500:
                raise ValueError(f"disposition_receipt_consumption.recorded item has invalid {key}")
            if key in {"disposition", "disposition_claim"} and value not in {"false-positive", "deferred"}:
                raise ValueError(f"disposition_receipt_consumption.recorded item has invalid {key}")
            projected_claim[key] = value
        projected_claims.append(projected_claim)
    result = {
        "recorded": projected_claims,
        "resolved": projected,
        "consumed_count": consumed_count,
        "rejected_count": rejected_count,
        "rejected_reasons": dict(sorted(reasons.items())),
        "fail_closed": fail_closed,
    }
    if "remaining_p1_ids" in block:
        remaining = block["remaining_p1_ids"]
        if not isinstance(remaining, list) or any(not isinstance(item, str) or not item for item in remaining):
            raise ValueError("disposition_receipt_consumption.remaining_p1_ids must be non-empty text values")
        result["remaining_p1_ids"] = list(remaining)
    return result


def _validate_finding_relation(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict) or block.get("schema_version") != 1:
        raise ValueError("GATE-FINDING-RELATION-UNKNOWN: block schema")
    counts = block.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(_RELATION_VALUES):
        raise ValueError("GATE-FINDING-RELATION-UNKNOWN: counts")
    for name in _RELATION_VALUES:
        if not _strict_int(counts.get(name)) or counts[name] < 0:
            raise ValueError(f"GATE-FINDING-RELATION-UNKNOWN: count {name}")
    items = block.get("items")
    if not isinstance(items, list) or sum(counts[name] for name in _RELATION_VALUES) != len(items):
        raise ValueError("GATE-FINDING-RELATION-UNKNOWN: counts do not match items")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("GATE-FINDING-RELATION-UNKNOWN: item")
        relation = item.get("relation_to_previous")
        if relation not in _RELATION_VALUES:
            raise ValueError(f"GATE-FINDING-RELATION-UNKNOWN: {relation!r}")
        if relation != "new" and (not isinstance(item.get("previous_finding_id"), str) or not item["previous_finding_id"]):
            raise ValueError(f"GATE-FINDING-RELATION-UNKNOWN: {relation} missing previous_finding_id")
    terminal = block.get("review_terminal")
    if terminal not in {"unchanged", "manual_required"} or (counts["conflict"] > 0) != (terminal == "manual_required"):
        raise ValueError(f"GATE-FINDING-RELATION-UNKNOWN: review_terminal {terminal!r}")
    return block


def _disposition_receipt_consumption_from_terminal(
    envelope: dict[str, Any], *, repository: str, pr_number: int,
    run_id: int, run_attempt: int, head_sha: str,
) -> dict[str, Any]:
    if envelope.get("schema_version") != 1 or envelope.get("kind") != "gate_terminal":
        raise ValueError("gate terminal artifact has an unsupported schema")
    expected = {
        "repository": repository,
        "pr_number": pr_number,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
    }
    mismatches = []
    for field, value in expected.items():
        observed = envelope.get(field)
        if field == "run_attempt":
            if not _strict_int(observed) or not 1 <= observed <= value:
                mismatches.append(field)
        elif observed != value:
            mismatches.append(field)
    if mismatches:
        raise ValueError(f"gate terminal identity mismatch: {sorted(mismatches)}")
    if "disposition_receipt_consumption" not in envelope:
        raise ValueError("gate terminal artifact is missing disposition_receipt_consumption")
    return validate_disposition_receipt_audit(envelope["disposition_receipt_consumption"])


def build_entry(
    *,
    repository: str,
    pr_number: int,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    preflight: dict[str, Any],
    audit: dict[str, Any] | None,
    expected_identity: dict[str, Any] | None = None,
    install: dict[str, Any] | None = None,
    fallback_status: str = "not_run",
    terminal_envelope: dict[str, Any] | None = None,
    input_short_circuited: bool = False,
    disposition_status: str = "success",
) -> dict[str, Any]:
    if disposition_status not in DISPOSITION_STATUSES:
        raise ValueError("disposition status is invalid")
    primary_identity = _primary_identity(
        audit, repository=repository, pr_number=pr_number, run_id=run_id,
        run_attempt=run_attempt, head_sha=head_sha, expected_identity=expected_identity,
    )
    review = _review_summary(
        audit, fallback_status, preflight, input_short_circuited=input_short_circuited,
    )
    entry = {
        "schema_version": 2,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "pr_number": pr_number,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
        "disposition_status": disposition_status,
        "preflight": preflight or None,
        # D5(ci-cache-strategy.md 阶段 A):Install dependencies 步骤的度量信号 —
        # {ecosystem, status, duration_s, cache_hit}(见 gate.yml Install 步骤),
        # 缺失时为 None。纯新增字段,不影响任何读取 "review"/"preflight"/
        # "comparison" 等既有 key 的消费者。
        "install": install,
        "primary_identity": primary_identity,
        "review": review,
        "finding_dispositions": {},
        "false_positive_count": 0,
    }
    if terminal_envelope is not None:
        entry["disposition_receipt_consumption"] = _disposition_receipt_consumption_from_terminal(
            terminal_envelope,
            repository=repository,
            pr_number=pr_number,
            run_id=run_id,
            run_attempt=run_attempt,
            head_sha=head_sha,
        )
        source_attempt = terminal_envelope.get("run_attempt")
        if source_attempt != run_attempt:
            entry["terminal_source_attempt"] = source_attempt
        if "finding_relation" in terminal_envelope:
            entry["finding_relation"] = _validate_finding_relation(terminal_envelope["finding_relation"])
    return entry


def write_ledger(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return json.loads(path.read_text())


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes"}


def _append_summary(entry: dict[str, Any], path: str) -> None:
    review = entry["review"]
    reviewer = review.get("reviewer") or "none"
    failover = "yes" if review.get("failover") else "no"
    summary_text = (
        "### Review effectiveness ledger\n\n"
        f"- Run: {entry['run_id']} attempt {entry['run_attempt']}\n"
        f"- Review status: `{review['status']}`\n"
        f"- Reviewer: `{reviewer}` (failover={failover})\n"
        f"- Findings: {review['finding_count']}\n"
        f"- False positives recorded: {entry['false_positive_count']}\n"
    )
    attempts = review.get("attempts") or []
    if attempts:
        chain = " -> ".join(
            f"{a.get('reviewer')}(exit {a.get('exit_code')}"
            + (f", {a.get('reason')}" if a.get("reason") else "")
            + (f", {a.get('duration_s')}s" if isinstance(a.get("duration_s"), int) else "")
            + ")"
            for a in attempts
        )
        summary_text += f"- Chain: `{chain}`\n"
    install = entry.get("install")
    if install:
        summary_text += (
            f"- Install: `{install.get('ecosystem')}` status={install.get('status')} "
            f"duration={install.get('duration_s')}s cache_hit={install.get('cache_hit')}\n"
        )
    with open(path, "a", encoding="utf-8") as summary:
        summary.write(scrub_for_publish(summary_text, runtime_values=runtime_values_from_environment()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-path", required=True, type=Path)
    parser.add_argument("--preflight-path", required=True, type=Path)
    parser.add_argument("--install-path", required=True, type=Path)
    parser.add_argument("--terminal-path", default="")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--expected-repository-id", required=True, type=int)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--expected-caller-sha", required=True)
    parser.add_argument("--expected-reusable-workflow-sha", required=True)
    parser.add_argument("--codex-expected", default="false")
    parser.add_argument("--codex-waived", default="false")
    # Omitted means false. Do not use argparse default="false": the explicit
    # None branch below is the documented default, and any other string fails.
    parser.add_argument("--input-short-circuited", default=None)
    args = parser.parse_args()
    if args.input_short_circuited is None:
        input_short_circuited = False
    elif args.input_short_circuited not in {"true", "false"}:
        raise SystemExit("--input-short-circuited must be one of true, false")
    else:
        input_short_circuited = args.input_short_circuited == "true"
    preflight = _load_json(args.preflight_path) or {}
    audit = _load_json(args.audit_path)
    install = _load_json(args.install_path)
    terminal_raw = args.terminal_path.strip() if isinstance(args.terminal_path, str) else str(args.terminal_path or "").strip()
    terminal = load_gate_terminal_envelope(Path(terminal_raw)) if terminal_raw else None
    if not preflight.get("reviewable", True):
        fallback = "blocked_by_size"
    elif _truthy(args.codex_waived):
        fallback = "waived"
    elif not _truthy(args.codex_expected):
        fallback = "not_applicable"
    else:
        fallback = "not_run"

    entry = build_entry(
        repository=args.repository,
        pr_number=args.pr_number,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        head_sha=args.head_sha,
        preflight=preflight,
        audit=audit,
        expected_identity={
            "repository_id": args.expected_repository_id,
            "base_sha": args.expected_base_sha,
            "caller_sha": args.expected_caller_sha,
            "reusable_workflow_sha": args.expected_reusable_workflow_sha,
        },
        install=install,
        fallback_status=fallback,
        terminal_envelope=terminal,
        input_short_circuited=input_short_circuited,
    )
    write_ledger(args.output, entry)
    print(json.dumps(entry, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        _append_summary(entry, os.environ["GITHUB_STEP_SUMMARY"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
