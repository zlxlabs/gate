"""Pure incremental blocking-shadow classifier (candidate gate rule, round>=2).

Implements the classification algorithm of the gate-hub design
``docs/designs/convergence-incremental-blocking-shadow.md`` (PR #1535),
sections 1 (locked rule), 4 (algorithm) and 5.1 (payload shape).  A P1
finding (severity blocker/major) of canonical round r>=2 still blocks only
if it is a duplicate of an earlier-round finding (same file, |delta line|
<= 15) or its anchor sits on a line added by the inter-round diff
(``git diff -U0 -M prev..head``, new side, +/-3).  Everything else would
drop to advisory under the candidate rule; this module only labels, it
never changes any gate conclusion.

This module deliberately has no filesystem, network, subprocess,
environment, or GitHub dependencies.  The caller (the ledger job, card G2)
owns all I/O and passes ledger rows and the diff text in; every function
here is replayable offline.

Invalid input fails fast: functions raise ``ValueError`` whose message
starts with ``incremental_shadow_invalid_input`` and names the offending
field.  There is deliberately no fallback, retry, or default-on-error
path; the caller decides how to degrade (status/degrade fields of the
``incremental_blocking_shadow`` ledger field are filled by the caller).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

ELIGIBLE_REVIEW_STATUSES = frozenset({"pass", "fail"})
P1_SEVERITIES = frozenset({"blocker", "major"})
LOOSE_LINE_DELTA = 15
DIFF_LINE_SLOP = 3
MAX_SHADOW_FINDINGS = 50
FINDING_CLASSES = ("no_anchor", "duplicate", "diff_unavailable", "on_diff", "neither")
COUNTERFACTUAL_VALUES = ("block", "pass", "unclassified", "not_applicable")
BLOCKING_TERMINAL_REASON = "primary_findings"
DIFF_STATUSES = ("ok", "unavailable")

_HUNK_RE = re.compile(r"\+(\d+)(?:,(\d+))?")


def _invalid(field: str, detail: str) -> ValueError:
    return ValueError(f"incremental_shadow_invalid_input: {field} {detail}")


def _require_str(value: Any, field: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise _invalid(field, "must be a non-empty string" if not allow_empty else "must be a string")


def normalize_path(path: Any) -> str | None:
    """Normalize a finding's file path; non-string or blank becomes None.

    Mirrors the offline classifier: backslashes become slashes, surrounding
    whitespace and leading ``./`` segments are stripped.  ``a/``/``b/``
    prefixes are NOT stripped here; the diff parser strips ``b/`` from
    ``+++`` lines, which is the only prefix handling the locked口径 uses.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    value = path.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def finding_line(finding: dict[str, Any]) -> int | None:
    """Return the finding's anchor line; missing/non-int (bool included) is None."""
    line = finding.get("line")
    if isinstance(line, bool) or not isinstance(line, int):
        return None
    return line


def normalize_text(text: Any) -> str:
    """Collapse whitespace runs to single spaces and strip; None becomes ''."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise _invalid("issue", "must be a string or None")
    return re.sub(r"\s+", " ", text).strip()


def text_digest(category: Any, issue: Any) -> str:
    """First 16 hex chars of sha256(category + \\x1f + normalize_text(issue)).

    Identity key for no-anchor recurrence tracking (design §6.4).  category
    None/missing becomes the empty string; a non-string category or issue
    raises the invalid-input error.
    """
    if category is None:
        category = ""
    _require_str(category, "category", allow_empty=True)
    return hashlib.sha256(f"{category}\x1f{normalize_text(issue)}".encode("utf-8")).hexdigest()[:16]


def parse_diff_added_lines(diff_text: Any) -> dict[str, set[int]]:
    """Parse ``git diff -U0 -M`` output into {new-side path: set of added lines}.

    Mirrors the offline classifier line-for-line, including the ``b/`` strip
    on ``+++`` lines and ``/dev/null`` handling, so replay matches the prior
    study exactly.  A rename without hunks yields no entry (pure renames are
    never on_diff).
    """
    if not isinstance(diff_text, str):
        raise _invalid("diff_text", "must be a string")
    current_file: str | None = None
    new_line = 0
    result: dict[str, set[int]] = {}
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            if path.startswith("b/"):
                path = path[2:]
            current_file = None if path == "/dev/null" else path
            continue
        if line.startswith("@@"):
            match = _HUNK_RE.search(line)
            if match:
                new_line = int(match.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if current_file:
                result.setdefault(current_file, set()).add(new_line)
            new_line += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            continue
        if line.startswith("\\"):
            continue
        new_line += 1
    return {path: lines for path, lines in result.items() if lines}


def _validated_findings_list(findings: Any, where: str) -> list[dict[str, Any]]:
    if findings is None:
        return []
    if not isinstance(findings, list):
        raise _invalid(f"{where}", "must be a list")
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            raise _invalid(f"{where}[{index}]", "must be a dict")
    return findings


def _validated_findings(review: Any, where: str) -> list[dict[str, Any]]:
    if review is None:
        return []
    if not isinstance(review, dict):
        raise _invalid(f"{where}.review", "must be a dict")
    result = review.get("result")
    if result is None:
        return []
    if not isinstance(result, dict):
        raise _invalid(f"{where}.review.result", "must be a dict")
    return _validated_findings_list(result.get("findings"), f"{where}.review.result.findings")


def fold_canonical_rounds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold ledger rows into canonical rounds (design §1 / §4.1).

    Eligible rows are those whose ``review.status`` is pass/fail and which
    carry a non-empty ``head_sha``.  Same ``run_id`` collapses to the max
    ``run_attempt`` first (earlier attempts of a rerun never become rounds,
    so their findings never enter anchor sets), remaining rows sort by
    ``(recorded_at, run_id)`` (ISO strings compare chronologically), and
    consecutive rows with the same head collapse into one round keeping the
    latest row.  Returns one dict per canonical round::

        {"round": 1-based number, "head_sha", "recorded_at", "run_id",
         "run_attempt", "findings": [full finding dicts of that row]}

    Non-eligible rows are skipped (a non-dict ``review`` is simply not
    eligible); structurally broken eligible rows raise the invalid-input
    error instead of being silently dropped.
    """
    if not isinstance(rows, list):
        raise _invalid("rows", "must be a list")
    eligible: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise _invalid(f"rows[{index}]", "must be a dict")
        review = row.get("review")
        status = review.get("status") if isinstance(review, dict) else None
        if status not in ELIGIBLE_REVIEW_STATUSES:
            continue
        head_sha = row.get("head_sha")
        if head_sha is not None and not isinstance(head_sha, str):
            raise _invalid(f"rows[{index}].head_sha", "must be a string")
        if not head_sha:
            continue
        run_id = row.get("run_id")
        if isinstance(run_id, bool) or not isinstance(run_id, int):
            raise _invalid(f"rows[{index}].run_id", "must be an int")
        run_attempt = row.get("run_attempt")
        if run_attempt is not None and (isinstance(run_attempt, bool) or not isinstance(run_attempt, int)):
            raise _invalid(f"rows[{index}].run_attempt", "must be an int or None")
        recorded_at = row.get("recorded_at")
        if recorded_at is not None and not isinstance(recorded_at, str):
            raise _invalid(f"rows[{index}].recorded_at", "must be a string or None")
        _validated_findings(review, f"rows[{index}]")
        eligible.append(row)
    by_run: dict[int, dict[str, Any]] = {}
    for row in eligible:
        run_id = row["run_id"]
        kept = by_run.get(run_id)
        if kept is None or (row.get("run_attempt") or 0) > (kept.get("run_attempt") or 0):
            by_run[run_id] = row
    ordered = sorted(by_run.values(), key=lambda row: (row.get("recorded_at") or "", row["run_id"]))
    rounds: list[dict[str, Any]] = []
    for row in ordered:
        if rounds and rounds[-1]["head_sha"] == row["head_sha"]:
            rounds[-1] = row
            continue
        rounds.append(row)
    return [
        {
            "round": number,
            "head_sha": row["head_sha"],
            "recorded_at": row.get("recorded_at"),
            "run_id": row["run_id"],
            "run_attempt": row.get("run_attempt"),
            "findings": _validated_findings(row.get("review"), f"round {number}"),
        }
        for number, row in enumerate(rounds, start=1)
    ]


def _validate_round(round_: Any, field: str) -> None:
    if not isinstance(round_, dict):
        raise _invalid(field, "must be a dict")
    _require_str(round_.get("head_sha"), f"{field}.head_sha")
    round_no = round_.get("round")
    if isinstance(round_no, bool) or not isinstance(round_no, int):
        raise _invalid(f"{field}.round", "must be an int")
    _validated_findings_list(round_.get("findings"), f"{field}.findings")


def _anchors(prior_rounds: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    anchors: list[tuple[str, int, int]] = []
    for round_ in prior_rounds:
        for finding in round_["findings"]:
            file_path = normalize_path(finding.get("file"))
            line = finding_line(finding)
            if file_path is not None and line is not None:
                anchors.append((file_path, line, round_["round"]))
    return anchors


def _on_changed_line(file_path: str | None, line: int | None, changed: dict[str, set[int]]) -> bool:
    if not file_path or line is None:
        return False
    lines = changed.get(file_path)
    if not lines:
        # basename fallback only when exactly one changed path matches
        matches = [path for path in changed if path == file_path or path.endswith("/" + file_path)]
        if len(matches) != 1:
            return False
        lines = changed[matches[0]]
    return any(abs(line - changed_line) <= DIFF_LINE_SLOP for changed_line in lines)


def compute_counterfactual(gate_result: Any, reason_code: Any, classes: Any, diff_status: Any) -> str:
    """Design §4.1 counterfactual: would the candidate rule still block?

    ``not_applicable`` when the round was not failed by primary findings
    (rule has nothing to flip); ``block`` when any finding is duplicate or
    on_diff; otherwise ``pass`` if the diff was available, else
    ``unclassified`` (findings may be hiding on an unavailable diff).
    """
    _require_str(gate_result, "gate_result", allow_empty=True)
    _require_str(reason_code, "reason_code", allow_empty=True)
    if diff_status not in DIFF_STATUSES:
        raise _invalid("diff_status", "must be 'ok' or 'unavailable'")
    labels = list(classes) if classes is not None else []
    for item in labels:
        if item not in FINDING_CLASSES:
            raise _invalid("classes", f"unknown class {item!r}")
    if gate_result != "fail" or reason_code != BLOCKING_TERMINAL_REASON:
        return "not_applicable"
    if any(item in ("duplicate", "on_diff") for item in labels):
        return "block"
    return "pass" if diff_status == "ok" else "unclassified"


def classify_p1_findings(
    prior_rounds: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    diff_lines: dict[str, set[int]] | None,
    *,
    head_sha: str,
    gate_result: str,
    reason_code: str,
) -> dict[str, Any]:
    """Classify the current round's P1 findings and return the §5.1 payload.

    ``prior_rounds`` is the ``fold_canonical_rounds`` output for every
    canonical round strictly before the current run; ALL of their findings
    (P1 or not) form the duplicate anchor set, except that a same-head
    rerun (``head_sha`` equals the last prior round's head) excludes that
    last round from the anchors: it is the current round's own earlier
    attempt (design §4.2), so it cannot make the rerun a duplicate of
    itself.  ``findings`` is the current
    round's full finding list; only blocker/major entries are classified.
    ``diff_lines`` is ``parse_diff_added_lines`` output for the inter-round
    diff, or None when the diff is unavailable.  Classification priority:
    no_anchor > duplicate > diff_unavailable > on_diff > neither — in
    particular a duplicate stays duplicate even when the diff is
    unavailable.  ``prev_head_sha`` and ``canonical_round`` are derived from
    ``prior_rounds`` + ``head_sha`` (a same-head rerun keeps the last round
    number); ``prior_rounds_used`` is ``len(prior_rounds)`` (a same-head
    last round still counts as fetched; it is only excluded from the
    anchor set).  The findings
    list is capped at MAX_SHADOW_FINDINGS (``truncated`` flags the cap;
    ``class_counts`` always counts all P1 findings).  The caller adds
    schema_version/status/skip_reason/degrade_reason.
    """
    if not isinstance(prior_rounds, list) or not prior_rounds:
        raise _invalid("prior_rounds", "must be a non-empty list")
    for index, round_ in enumerate(prior_rounds):
        _validate_round(round_, f"prior_rounds[{index}]")
    if not isinstance(findings, list):
        raise _invalid("findings", "must be a list")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise _invalid(f"findings[{index}]", "must be a dict")
    changed: dict[str, set[int]] | None = None
    if diff_lines is not None:
        if not isinstance(diff_lines, dict):
            raise _invalid("diff_lines", "must be a dict or None")
        changed = {}
        for path, lines in diff_lines.items():
            if not isinstance(path, str):
                raise _invalid("diff_lines key", "must be a string")
            values: set[int] = set()
            for line in lines:
                if isinstance(line, bool) or not isinstance(line, int):
                    raise _invalid(f"diff_lines[{path}]", "line numbers must be ints")
                values.add(line)
            changed[path] = values
    _require_str(head_sha, "head_sha")
    _require_str(gate_result, "gate_result", allow_empty=True)
    _require_str(reason_code, "reason_code", allow_empty=True)

    anchor_rounds = prior_rounds[:-1] if head_sha == prior_rounds[-1]["head_sha"] else prior_rounds
    anchors = _anchors(anchor_rounds)
    diff_status = "unavailable" if diff_lines is None else "ok"
    p1 = [finding for finding in findings if finding.get("severity") in P1_SEVERITIES]
    entries: list[dict[str, Any]] = []
    counts = {name: 0 for name in FINDING_CLASSES}
    for finding in p1:
        file_path = normalize_path(finding.get("file"))
        line = finding_line(finding)
        match_round: int | None = None
        match_line: int | None = None
        if line is None:
            label = "no_anchor"
        else:
            hit = next(
                (
                    (anchor_file, anchor_line, anchor_round)
                    for anchor_file, anchor_line, anchor_round in anchors
                    if anchor_file == file_path and abs(anchor_line - line) <= LOOSE_LINE_DELTA
                ),
                None,
            )
            if hit is not None:
                label = "duplicate"
                match_line, match_round = hit[1], hit[2]
            elif diff_status != "ok":
                label = "diff_unavailable"
            elif _on_changed_line(file_path, line, changed or {}):
                label = "on_diff"
            else:
                label = "neither"
        counts[label] += 1
        entries.append(
            {
                "id": finding.get("id"),
                "severity": finding.get("severity"),
                "file": file_path,
                "line": line,
                "class": label,
                "match_round": match_round,
                "match_line": match_line,
                "text_digest": text_digest(finding.get("category"), finding.get("issue")),
            }
        )
    prev_head = prior_rounds[-1]["head_sha"]
    return {
        "canonical_round": len(prior_rounds) if head_sha == prev_head else len(prior_rounds) + 1,
        "prev_head_sha": prev_head,
        "head_sha": head_sha,
        "prior_rounds_used": len(prior_rounds),
        "diff_status": diff_status,
        "findings": entries[:MAX_SHADOW_FINDINGS],
        "truncated": len(entries) > MAX_SHADOW_FINDINGS,
        "class_counts": counts,
        "counterfactual": compute_counterfactual(gate_result, reason_code, [e["class"] for e in entries], diff_status),
    }
