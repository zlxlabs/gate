#!/usr/bin/env python3
"""Pure carry-blocker selection for unresolved sol must_respond findings.

Git I/O stays in neighbor_evidence.py; this module consumes its verified
``(commit SHA, path) -> blob OID`` evidence and fails closed when evidence is
missing. It has no second evidence implementation.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Mapping

_INFRA_PREFIX = "sol_verify_infra."
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def inherited_rejection_keys(disposition_audit: Any) -> set[str]:
    """Keys actually consumed through G2a, not merely candidate receipts."""
    keys: set[str] = set()
    for status in getattr(disposition_audit, "statuses", ()):
        receipt = getattr(status, "receipt", None)
        key = getattr(receipt, "finding_key", None)
        if (
            getattr(status, "active", False) is True
            and getattr(status, "valid", False) is True
            and getattr(status, "matched_by", "") == "inherited_rejected"
            and isinstance(key, str) and key
        ):
            keys.add(key)
    return keys


def candidates(
    *, open_findings: Iterable[Any], current_findings: Iterable[Any],
    source_head_sha: Any, current_head_sha: str,
    canonical_finding_key: Callable[[dict[str, Any]], str],
    inherited_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Select only prior open sol/must_respond entries absent from this audit."""
    current_keys = {
        canonical_finding_key(item) for item in current_findings
        if isinstance(item, dict)
    }
    inherited_keys = inherited_keys or set()
    selected: list[dict[str, Any]] = []
    for finding in open_findings:
        if not isinstance(finding, dict):
            continue
        finding_id = finding.get("id")
        path = finding.get("file")
        key = canonical_finding_key(finding)
        if (
            finding.get("origin") != "sol_verify"
            or finding.get("gate_action") != "must_respond"
            or not isinstance(finding_id, str) or not finding_id
            or finding_id.startswith(_INFRA_PREFIX)
            or key in current_keys or key in inherited_keys
        ):
            continue
        safe_path = (
            isinstance(path, str) and bool(path)
            and not path.startswith("/") and "\\" not in path
            and ".." not in path.split("/")
        )
        selected.append({
            "id": finding_id,
            "finding_key": key,
            "source_head_sha": source_head_sha,
            "current_head_sha": current_head_sha,
            "file": path if isinstance(path, str) else "",
            "line": finding.get("line"),
            "title": finding.get("title") or finding.get("issue") or "",
            "_safe_path": safe_path,
            "_finding": dict(finding),
        })
    return selected


def audit_additions(
    items: Iterable[dict[str, Any]], decisions: Iterable[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair original finding payloads with audit-side carry provenance."""
    by_key = {(item["id"], item["finding_key"]): item for item in items}
    additions = []
    for decision in decisions:
        item = by_key[(decision["id"], decision["finding_key"])]
        additions.append((
            dict(item["_finding"]),
            {
                "id": item["id"],
                "carried_from_head_sha": item["source_head_sha"],
                "evidence_reason": decision["evidence_reason"],
            },
        ))
    return additions


def blob_fetch_requests(items: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    """Commit/path requests passed directly to neighbor_evidence.py."""
    requests: set[tuple[str, str]] = set()
    for item in items:
        source = item["source_head_sha"]
        target = item["current_head_sha"]
        path = item["file"]
        if (
            item["_safe_path"] and isinstance(source, str) and _SHA_RE.fullmatch(source)
            and isinstance(target, str) and _SHA_RE.fullmatch(target)
        ):
            requests.add((source, path))
            requests.add((target, path))
    return requests


def blockers(
    items: Iterable[dict[str, Any]], blob_oids: Mapping[tuple[str, str], str],
) -> list[dict[str, Any]]:
    """Return unchanged or unverifiable carry entries; changed files release."""
    result: list[dict[str, Any]] = []
    for item in items:
        source = item["source_head_sha"]
        target = item["current_head_sha"]
        path = item["file"]
        if not item["_safe_path"]:
            reason = "file_path_unavailable"
        elif not isinstance(source, str) or not _SHA_RE.fullmatch(source):
            reason = "source_head_unavailable"
        elif not isinstance(target, str) or not _SHA_RE.fullmatch(target):
            reason = "current_head_unavailable"
        else:
            old_oid = blob_oids.get((source, path))
            new_oid = blob_oids.get((target, path))
            if not isinstance(old_oid, str) or not old_oid or not isinstance(new_oid, str) or not new_oid:
                reason = "blob_evidence_unavailable"
            elif old_oid != new_oid:
                continue
            else:
                reason = "blob_unchanged"
        result.append({
            key: item[key] for key in (
                "id", "finding_key", "source_head_sha", "file", "line", "title",
            )
        } | {"evidence_reason": reason})
    return result
