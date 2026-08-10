#!/usr/bin/env python3
"""Build a cumulative, artifact-backed Codex review effectiveness ledger."""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DISPOSITION_RE = re.compile(
    r"^Codex finding disposition:\s*([a-z0-9][a-z0-9._-]*)\s*=\s*"
    r"(false-positive|accepted|fixed|wont-fix)\s*(?:[-—:]\s*(.+))?$",
    re.IGNORECASE | re.MULTILINE,
)


class CrossHostAuthStripRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not leak the GitHub bearer token to signed artifact storage URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            for header_map in (redirected.headers, redirected.unredirected_hdrs):
                for key in list(header_map):
                    if key.lower() == "authorization":
                        del header_map[key]
        return redirected


URL_OPENER = urllib.request.build_opener(CrossHostAuthStripRedirectHandler())
STATE_MARKER = "<!-- codex-review-ledger-state:v2 -->"
STATE_RE = re.compile(r"<!-- codex-review-ledger-state:v2:([A-Za-z0-9_-]+={0,2}) -->")
PRIMARY_STATUS_BY_VERDICT = {
    "pass": "pass", "fail": "fail", "unavailable": "unavailable",
    "not_expected": "not_expected", "waived": "waived",
}
PRIMARY_IDENTITY_FIELDS = (
    "repository_id", "repository", "pr", "base_sha", "head_sha", "diff_digest",
    "policy_version", "policy_digest", "registry_commit", "caller_sha",
    "reusable_workflow_sha", "run_id", "run_attempt", "job_id", "reviewer",
    "merge_base_sha", "candidate_commit_sha", "candidate_tree_sha", "run_mode",
    "spec_source", "pr_body_digest",
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


def parse_dispositions(comments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for comment in comments:
        for match in DISPOSITION_RE.finditer(comment.get("body", "")):
            finding_id, disposition, reason = match.groups()
            result[finding_id.lower()] = {
                "disposition": disposition.lower(),
                "reason": (reason or "").strip(),
                "author": comment.get("user", {}).get("login", "unknown"),
                "recorded_at": comment.get("created_at"),
                "url": comment.get("html_url"),
            }
    return result


def parse_state_entries(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for comment in comments:
        user = comment.get("user", {})
        if user.get("type") != "Bot" or user.get("login") != "github-actions[bot]":
            continue
        match = STATE_RE.search(comment.get("body", ""))
        if not match:
            continue
        try:
            payload = base64.urlsafe_b64decode(match.group(1).encode())
            entries = json.loads(payload)
            if isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries):
                return entries
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return []


def render_state_comment(entries: list[dict[str, Any]], current: dict[str, Any]) -> str:
    relevant = [
        entry for entry in entries
        if entry.get("repository") == current.get("repository")
        and entry.get("pr_number") == current.get("pr_number")
    ][-20:]
    encoded = base64.urlsafe_b64encode(
        json.dumps(relevant, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()
    review = current["review"]
    comparison = current["comparison"]
    comparison_line = comparison["kind"]
    if comparison["kind"] == "new_head":
        comparison_line += (
            f"; persistent/resolved/new = {len(comparison['persistent_finding_ids'])}/"
            f"{len(comparison['resolved_finding_ids'])}/{len(comparison['new_finding_ids'])}"
        )
    elif comparison["kind"] == "same_head_rerun":
        comparison_line += (
            f"; stable/missing/appeared = {len(comparison['persistent_finding_ids'])}/"
            f"{len(comparison['missing_finding_ids'])}/{len(comparison['appeared_finding_ids'])}"
        )
    reviewer = review.get("reviewer") or "none"
    failover = bool(review.get("failover"))
    reviewer_line = f"{reviewer}" + (" (failover)" if failover else "")
    return (
        f"{STATE_MARKER}\n\n"
        "### ⚙️ Review ledger state（机器状态记录，非评审结论）\n\n"
        "> 这是 review ledger 的**机器状态记录**，不代表评审结论，通常无需任何操作。\n"
        f"> 要看当前 PR 能否合并，请看 required check `gate` 的结果："
        f" https://github.com/{current['repository']}/pull/{current['pr_number']}/checks\n\n"
        "<details><summary>机器状态明细</summary>\n\n"
        f"- Commit: `{current['head_sha']}`\n"
        f"- Round: **{current['review_round']}**\n"
        f"- Status / findings: **{review['status']} / {review['finding_count']}**\n"
        f"- Reviewer: **{reviewer_line}**\n"
        f"- Comparison: `{comparison_line}`\n\n"
        "完整数据保存在 `codex-review-ledger-v2` artifact；此 sticky comment 仅保存 v2 epoch 的跨 rerun 连续游标。\n\n"
        "</details>\n\n"
        f"<!-- codex-review-ledger-state:v2:{encoded} -->\n"
    )


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
        cost_usd, tokens, shadows = audit.get("cost"), audit.get("tokens"), {}
    else:
        result = result or {}
        findings = result.get("findings") or []
        coverage = audit.get("coverage")
        cost_usd, tokens, shadows = audit.get("cost_usd"), audit.get("tokens"), audit.get("shadows", {})
    attempts = _compact_attempts(audit)
    status = PRIMARY_STATUS_BY_VERDICT[audit["verdict"]] if is_primary_v2 else audit.get("status", "unknown")
    # Failover = more than one hop was tried (a discarded hop precedes the adopted one).
    failover = len(attempts) > 1
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
    if audit["expected_shadows"]:
        raise ValueError("canonical primary expected_shadows outcomes are unavailable")
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


def build_entry(
    *,
    repository: str,
    pr_number: int,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    preflight: dict[str, Any],
    audit: dict[str, Any] | None,
    prior_entries: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    expected_identity: dict[str, Any] | None = None,
    install: dict[str, Any] | None = None,
    fallback_status: str = "not_run",
) -> dict[str, Any]:
    relevant = [
        entry for entry in prior_entries
        if entry.get("repository") == repository and entry.get("pr_number") == pr_number
    ]
    previous = relevant[-1] if relevant else None
    prior_conflict = bool(previous and previous.get("ledger_conflict"))
    previous = None if prior_conflict else previous
    primary_identity = _primary_identity(
        audit, repository=repository, pr_number=pr_number, run_id=run_id,
        run_attempt=run_attempt, head_sha=head_sha, expected_identity=expected_identity,
    )
    review = _review_summary(audit, fallback_status, preflight)
    current_ids = set(review["finding_ids"])
    comparison: dict[str, Any] = {"kind": "prior_conflict" if prior_conflict else "first_review"}
    if previous:
        previous_ids = set(previous.get("review", {}).get("finding_ids", []))
        same_head = previous.get("head_sha") == head_sha
        comparison = {
            "kind": "same_head_rerun" if same_head else "new_head",
            "previous_head_sha": previous.get("head_sha"),
            "previous_run_id": previous.get("run_id"),
            "persistent_finding_ids": sorted(previous_ids & current_ids),
        }
        if same_head:
            comparison.update({
                "missing_finding_ids": sorted(previous_ids - current_ids),
                "appeared_finding_ids": sorted(current_ids - previous_ids),
            })
        else:
            comparison.update({
                "resolved_finding_ids": sorted(previous_ids - current_ids),
                "new_finding_ids": sorted(current_ids - previous_ids),
            })
    relevant_dispositions = {
        finding_id: value for finding_id, value in dispositions.items()
        if finding_id in current_ids or any(finding_id in entry.get("review", {}).get("finding_ids", []) for entry in relevant)
    }
    return {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "pr_number": pr_number,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
        "review_round": len({(entry.get("run_id"), entry.get("run_attempt")) for entry in relevant}) + 1,
        "preflight": preflight or None,
        # D5(ci-cache-strategy.md 阶段 A):Install dependencies 步骤的度量信号 —
        # {ecosystem, status, duration_s, cache_hit}(见 gate.yml Install 步骤),
        # 缺失时为 None。纯新增字段,不影响任何读取 "review"/"preflight"/
        # "comparison" 等既有 key 的消费者。
        "install": install,
        "primary_identity": primary_identity,
        "review": review,
        "comparison": comparison,
        "finding_dispositions": relevant_dispositions,
        "false_positive_count": sum(
            item.get("disposition") == "false-positive" for item in relevant_dispositions.values()
        ),
    }


def write_ledger(path: Path, entries: list[dict[str, Any]], *, max_entries: int) -> None:
    if type(max_entries) is not int or max_entries <= 0:
        raise ValueError("max_entries must be a positive integer")
    ordered = dedupe_entries(entries)
    if len(ordered) > max_entries:
        raise ValueError(f"max_entries exceeded: {len(ordered)} entries > {max_entries}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in ordered))


def dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    known_conflict_counts: dict[tuple[Any, ...], int] = {}
    for entry in entries:
        key = (entry.get("repository"), entry.get("run_id"), entry.get("run_attempt"))
        conflict = entry.get("ledger_conflict")
        if isinstance(conflict, dict) and type(conflict.get("variant_count")) is int:
            known_conflict_counts[key] = max(known_conflict_counts.get(key, 0), conflict["variant_count"])
        canonical = {field: value for field, value in entry.items() if field != "ledger_conflict"}
        signature = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        grouped.setdefault(key, {})[signature] = canonical
    unique: list[dict[str, Any]] = []
    for key, variants in grouped.items():
        variant_count = max(len(variants), known_conflict_counts.get(key, 0))
        marker = {"key": list(key), "variant_count": variant_count, "present_variant_count": len(variants)}
        for entry in variants.values():
            if variant_count > 1:
                entry = {**entry, "ledger_conflict": marker}
            unique.append(entry)
    return sorted(unique, key=lambda entry: (
        entry.get("recorded_at", ""), entry.get("run_id", 0), entry.get("run_attempt", 0),
        json.dumps(entry, ensure_ascii=False, sort_keys=True),
    ))


def _api_request(token: str, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> bytes:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "zlxlabs-gate-review-ledger",
            "Content-Type": "application/json",
        },
    )
    with URL_OPENER.open(request, timeout=30) as response:
        return response.read()


def _api_json(token: str, url: str) -> Any:
    return json.loads(_api_request(token, url))


def fetch_prior_entries(token: str, repository: str, *, artifact_limit: int = 10) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"name": "codex-review-ledger-v2", "per_page": artifact_limit})
    payload = _api_json(token, f"https://api.github.com/repos/{repository}/actions/artifacts?{query}")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise ValueError("prior ledger artifact list has invalid JSON shape")
    entries: list[dict[str, Any]] = []
    for artifact in payload.get("artifacts", [])[:artifact_limit]:
        if artifact.get("expired"):
            continue
        archive = _api_request(token, artifact["archive_download_url"])
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            name = next((name for name in bundle.namelist() if name.endswith("ledger.jsonl")), None)
            if not name:
                raise ValueError(f"prior ledger artifact {artifact.get('id')} has no ledger.jsonl")
            for line in bundle.read(name).decode("utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    if not isinstance(entry, dict):
                        raise ValueError("prior ledger entry must be a JSON object")
                    entries.append(entry)
    return dedupe_entries(entries)


def fetch_comments(token: str, repository: str, pr_number: int) -> list[dict[str, Any]]:
    return _api_json(token, f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments?per_page=100")


def post_state_comment(
    token: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    entries: list[dict[str, Any]],
    current: dict[str, Any],
    comments: list[dict[str, Any]],
) -> None:
    api = f"https://api.github.com/repos/{repository}"
    pull = _api_json(token, f"{api}/pulls/{pr_number}")
    if pull.get("head", {}).get("sha") != head_sha:
        print("::notice::skip stale review ledger state; PR head advanced")
        return
    existing = next((comment for comment in comments if STATE_MARKER in comment.get("body", "")), None)
    body = render_state_comment(entries, current)
    if existing:
        _api_request(token, f"{api}/issues/comments/{existing['id']}", method="PATCH", payload={"body": body})
    else:
        _api_request(token, f"{api}/issues/{pr_number}/comments", method="POST", payload={"body": body})


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return json.loads(path.read_text())


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes"}


def _append_summary(entry: dict[str, Any], path: str) -> None:
    review = entry["review"]
    comparison = entry["comparison"]
    with open(path, "a", encoding="utf-8") as summary:
        reviewer = review.get("reviewer") or "none"
        failover = "yes" if review.get("failover") else "no"
        summary.write(
            "### Review effectiveness ledger\n\n"
            f"- Round: {entry['review_round']} (`{comparison['kind']}`)\n"
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
            summary.write(f"- Chain: `{chain}`\n")
        if comparison["kind"] == "new_head":
            summary.write(
                f"- Persistent / resolved / new: {len(comparison['persistent_finding_ids'])} / "
                f"{len(comparison['resolved_finding_ids'])} / {len(comparison['new_finding_ids'])}\n"
            )
        elif comparison["kind"] == "same_head_rerun":
            summary.write(
                f"- Same-head stable / missing / appeared: {len(comparison['persistent_finding_ids'])} / "
                f"{len(comparison['missing_finding_ids'])} / {len(comparison['appeared_finding_ids'])}\n"
            )
        install = entry.get("install")
        if install:
            summary.write(
                f"- Install: `{install.get('ecosystem')}` status={install.get('status')} "
                f"duration={install.get('duration_s')}s cache_hit={install.get('cache_hit')}\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-path", required=True, type=Path)
    parser.add_argument("--preflight-path", required=True, type=Path)
    parser.add_argument("--install-path", required=True, type=Path)
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
    parser.add_argument("--max-entries", default=2000, type=int)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("GH_TOKEN is required to publish review ledger")

    preflight = _load_json(args.preflight_path) or {}
    audit = _load_json(args.audit_path)
    install = _load_json(args.install_path)
    if not preflight.get("reviewable", True):
        fallback = "blocked_by_size"
    elif _truthy(args.codex_waived):
        fallback = "waived"
    elif not _truthy(args.codex_expected):
        fallback = "not_applicable"
    else:
        fallback = "not_run"

    prior_entries: list[dict[str, Any]] = []
    dispositions: dict[str, dict[str, Any]] = {}
    comments: list[dict[str, Any]] = []
    prior_entries = fetch_prior_entries(token, args.repository)
    if token:
        try:
            comments = fetch_comments(token, args.repository, args.pr_number)
            dispositions = parse_dispositions(comments)
            prior_entries = dedupe_entries([*prior_entries, *parse_state_entries(comments)])
        except Exception as error:
            print(f"::warning::could not load finding dispositions or PR ledger state: {error}")

    entry = build_entry(
        repository=args.repository,
        pr_number=args.pr_number,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        head_sha=args.head_sha,
        preflight=preflight,
        audit=audit,
        prior_entries=prior_entries,
        dispositions=dispositions,
        expected_identity={
            "repository_id": args.expected_repository_id,
            "base_sha": args.expected_base_sha,
            "caller_sha": args.expected_caller_sha,
            "reusable_workflow_sha": args.expected_reusable_workflow_sha,
        },
        install=install,
        fallback_status=fallback,
    )
    all_entries = dedupe_entries([*prior_entries, entry])
    write_ledger(args.output, all_entries, max_entries=args.max_entries)
    if token:
        try:
            post_state_comment(
                token, args.repository, args.pr_number, args.head_sha,
                all_entries, entry, comments,
            )
        except Exception as error:
            print(f"::warning::could not update PR review ledger state: {error}")
    print(json.dumps(entry, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        _append_summary(entry, os.environ["GITHUB_STEP_SUMMARY"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
