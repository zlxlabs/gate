#!/usr/bin/env python3
"""Producer-side carry projection for `--render-previous-context` (card
dlg-20261121-113103-7af1cd, gate-hub review-followup-incremental §2.1).

The top-level ``open_findings`` array is the merge-blocking subset of the
carried previous findings that no already-approved disposition receipt has
released. Blocking follows the previous audit's own semantics: a finding
carrying a ``gate_action`` (blocking-matrix-v1 projection) blocks when it
is ``block`` or ``must_respond`` — a ``comment`` finding never blocks; a
legacy finding without ``gate_action`` blocks on P1 severity only. A
false-positive receipt releases any covered finding; a deferred receipt
releases only a ``must_respond`` finding (legacy audits reject deferred
outright); a rejected receipt releases only a ``must_respond`` finding
with ``origin == "sol_verify"`` and a category outside the owner-protected
{test_integrity, security} (gate-hub#2579 / design 261122 §8.9, mirroring
issue_receipt.py's issuance guards; missing fields fail closed). This is
deliberately NOT ``build_finding_relation``'s union:
the source is the previous round's own carried findings, filtered.

stdlib-only; loaded by aggregate.py via importlib spec (sibling-module
pattern) — not a CLI entrypoint.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

_BLOCKING_GATE_ACTIONS = frozenset({"block", "must_respond"})
_KNOWN_GATE_ACTIONS = frozenset({"block", "must_respond", "comment"})
_P1_SEVERITIES = frozenset({"major", "blocker"})
_REJECTED_RELEASE_ORIGIN = "sol_verify"
_REJECTED_PROTECTED_CATEGORIES = frozenset({"test_integrity", "security"})


def _blocks_merge(finding: dict[str, Any]) -> bool:
    """Merge-blocking under the finding's own blocking semantics."""
    action = finding.get("gate_action")
    if isinstance(action, str) and action in _KNOWN_GATE_ACTIONS:
        return action in _BLOCKING_GATE_ACTIONS
    return finding.get("severity") in _P1_SEVERITIES


def _rejected_releases(finding: dict[str, Any]) -> bool:
    """True when a rejected receipt may release this carried finding:
    sol_verify origin only, must_respond only, owner-protected categories
    never; a missing/invalid field fails closed."""
    return (
        finding.get("origin") == _REJECTED_RELEASE_ORIGIN
        and finding.get("gate_action") == "must_respond"
        and isinstance(finding.get("category"), str)
        and finding["category"] not in _REJECTED_PROTECTED_CATEGORIES
    )


def _released_targets(
    findings: list[Any], dispositions: list[Any],
    rejected_release_keys: set[tuple[str, str]] | None = None,
    canonical_finding_key: Callable[[dict[str, Any]], str] | None = None,
    *, head_sha: str | None = None,
) -> set[tuple[str, str]]:
    """Release only exact targets already validated by the projection.

    False-positive and deferred targets are bound to the carry head. Rejected
    id/key pairs have separately verified same-blob evidence across heads.
    """
    if canonical_finding_key is None:
        return set()
    rejected_release_keys = rejected_release_keys or set()
    same_head = {
        (item["finding_id"], item["finding_key"], item["disposition"])
        for item in dispositions
        if isinstance(head_sha, str) and head_sha and isinstance(item, dict)
        and item.get("head_sha") == head_sha
        and item.get("disposition") in ("false-positive", "deferred")
        and isinstance(item.get("finding_id"), str) and isinstance(item.get("finding_key"), str)
    }
    released: set[tuple[str, str]] = set()
    for finding in findings:
        finding_id = finding.get("id") if isinstance(finding, dict) else None
        if not isinstance(finding_id, str):
            continue
        key = canonical_finding_key(finding)
        if (finding_id, key, "false-positive") in same_head or (
            (finding_id, key, "deferred") in same_head and finding.get("gate_action") == "must_respond"
        ) or (
            (finding_id, key) in rejected_release_keys and _rejected_releases(finding)
        ):
            released.add((finding_id, key))
    return released


def write_previous_context(
    previous: dict[str, Any], dispositions: list[Any], path: str,
    *, rejected_release_keys: set[tuple[str, str]] | None = None,
    canonical_finding_key: Callable[[dict[str, Any]], str] | None = None,
) -> None:
    """Attach ``open_findings`` and persist the producer bytes.

    ``findings`` stays verbatim for its existing consumers (the gate
    annotator and gate-hub's first-round path); ``open_findings`` is purely
    additive.
    """
    payload = {
        **previous,
        "dispositions": [
            {key: value for key, value in disposition.items() if key != "finding_key"}
            for disposition in dispositions
        ],
    }
    findings = payload.get("findings")
    if not isinstance(findings, list):
        findings = []
    released = _released_targets(
        findings, dispositions, rejected_release_keys, canonical_finding_key,
        head_sha=previous.get("head_sha"),
    )
    payload["open_findings"] = [
        finding for finding in findings
        if isinstance(finding, dict) and _blocks_merge(finding)
        and (not released or (finding.get("id"), canonical_finding_key(finding)) not in released)
    ]
    Path(path).write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
