#!/usr/bin/env python3
"""Exact-key and same-file-blob evidence for inherited rejected receipts.

The owner-protected/origin/action issuance predicate is supplied by the
existing carry producer; this module owns the stricter cross-head matching and
blob evidence contract shared by receipt consumption and carry release.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


_INFRA_FINDING_PREFIX = "sol_verify_infra."


@dataclass(frozen=True)
class BlobCandidate:
    """One uniquely qualified finding requiring two commit-tree blob OIDs."""

    identity: tuple[str, str, str]
    source_head_sha: str
    target_head_sha: str
    path: str
    finding_id: str


def _candidate(
    *, identity: tuple[str, str, str], source_head_sha: Any,
    target_head_sha: Any, finding: Any, is_rejected_eligible: Callable[[dict[str, Any]], bool],
) -> BlobCandidate | None:
    if not isinstance(source_head_sha, str) or not source_head_sha:
        return None
    if not isinstance(target_head_sha, str) or not target_head_sha:
        return None
    if not isinstance(finding, dict):
        return None
    finding_id = finding.get("id")
    path = finding.get("file")
    if (
        not isinstance(finding_id, str) or not finding_id
        or finding_id.startswith(_INFRA_FINDING_PREFIX)
        or not isinstance(path, str) or not path
        or not is_rejected_eligible(finding)
    ):
        return None
    return BlobCandidate(identity, source_head_sha, target_head_sha, path, finding_id)


def receipt_blob_candidates(
    *, receipts: Iterable[Any], findings: list[Any], current_head_sha: str,
    canonical_finding_key: Callable[[dict[str, Any]], str],
    is_rejected_eligible: Callable[[dict[str, Any]], bool],
) -> list[BlobCandidate]:
    """Return only rejected receipts with exactly one qualified current key."""
    candidates: list[BlobCandidate] = []
    for receipt in receipts:
        if getattr(receipt, "disposition", None) != "rejected":
            continue
        key = getattr(receipt, "finding_key", "")
        source_head_sha = getattr(receipt, "head_sha", "")
        if not isinstance(key, str) or not key:
            continue
        matches = [
            finding for finding in findings
            if isinstance(finding, dict) and canonical_finding_key(finding) == key
        ]
        if len(matches) != 1:
            continue
        candidate = _candidate(
            identity=(str(source_head_sha), key, str(getattr(receipt, "finding_id", ""))),
            source_head_sha=source_head_sha,
            target_head_sha=current_head_sha,
            finding=matches[0],
            is_rejected_eligible=is_rejected_eligible,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def disposition_blob_candidates(
    *, findings: list[Any], dispositions: list[Any], target_head_sha: str,
    canonical_finding_key: Callable[[dict[str, Any]], str],
    is_rejected_eligible: Callable[[dict[str, Any]], bool],
) -> list[BlobCandidate]:
    """Return uniquely matched previous findings eligible for rejected release."""
    candidates: list[BlobCandidate] = []
    for disposition in dispositions:
        if not isinstance(disposition, dict) or disposition.get("disposition") != "rejected":
            continue
        key = disposition.get("finding_key")
        source_head_sha = disposition.get("head_sha")
        if not isinstance(key, str) or not key:
            continue
        matches = [
            finding for finding in findings
            if isinstance(finding, dict) and canonical_finding_key(finding) == key
        ]
        if len(matches) != 1:
            continue
        candidate = _candidate(
            identity=(str(source_head_sha), key, str(disposition.get("finding_id", ""))),
            source_head_sha=source_head_sha,
            target_head_sha=target_head_sha,
            finding=matches[0],
            is_rejected_eligible=is_rejected_eligible,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def blob_fetch_requests(candidates: Iterable[BlobCandidate]) -> set[tuple[str, str]]:
    """Commit/path pairs required by candidates; the consumer head is implicit."""
    return {
        (sha, candidate.path)
        for candidate in candidates
        for sha in (candidate.source_head_sha, candidate.target_head_sha)
    }


def _matching_candidates(
    candidates: Iterable[BlobCandidate], blob_oids: Mapping[tuple[str, str], str],
) -> list[BlobCandidate]:
    return [
        candidate for candidate in candidates
        if isinstance(blob_oids.get((candidate.source_head_sha, candidate.path)), str)
        and bool(blob_oids[(candidate.source_head_sha, candidate.path)])
        and blob_oids[(candidate.source_head_sha, candidate.path)]
        == blob_oids.get((candidate.target_head_sha, candidate.path))
    ]


def matching_finding_ids(
    candidates: Iterable[BlobCandidate], blob_oids: Mapping[tuple[str, str], str],
) -> dict[tuple[str, str, str], str]:
    """Return candidates whose source/target file blobs are both present and equal."""
    return {
        candidate.identity: candidate.finding_id
        for candidate in _matching_candidates(candidates, blob_oids)
    }


def matching_finding_keys(
    candidates: Iterable[BlobCandidate], blob_oids: Mapping[tuple[str, str], str],
) -> set[tuple[str, str]]:
    """Carry release targets retain both finding id and canonical four-tuple."""
    return {
        (candidate.finding_id, candidate.identity[1])
        for candidate in _matching_candidates(candidates, blob_oids)
    }
