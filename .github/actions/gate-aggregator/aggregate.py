#!/usr/bin/env python3
"""gate-aggregator — decision core for the Required Gate v2 final `gate` job.

Implements the "gate:" node of the Required Gate state machine (see the private
gate-hub repo's ceo-plans/2026-07-24-shadow-review-independence.md, "Required
Gate" + "Fork, waiver and notification semantics" sections). Per that plan's
"Caller / reusable workflow boundary", this aggregator is a portable script
that depends ONLY on python3 stdlib + data the calling workflow hands it — no
gate-hub import, no hosted-image-specific tool, no network call of its own
beyond what the workflow already downloaded as an artifact. It is invoked as a
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
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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
    if type(schema_version) is not int or schema_version != 1:
        errors.append(f"schema_version must be exactly int 1, got {schema_version!r}")

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
                artifact_name = audit_artifact_name if isinstance(audit_artifact_name, str) and audit_artifact_name else None
                notes.append(f"primary audit source run_attempt={audit_source} (current run_attempt={identity.run_attempt})")
                if verdict == "pass":
                    if primary_result != "success":
                        audit_available = False
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
                    audit_source = artifact_name = None
                    problems.append(
                        f"primary audit verdict {verdict!r} is not accepted: canary-stage primary never legitimately writes "
                        "not_expected/waived; companion-field validation is not wired yet"
                    )
                    primary_classification, primary_reason = "integration_error", "audit_invalid"
                if primary_result == "success" and verdict in ("fail", "unavailable"):
                    audit_available = False
                    audit_source = artifact_name = None
                    primary_classification, primary_reason = "integration_error", "job_audit_mismatch"

    if primary_classification == "integration_error":
        classification, reason_code = primary_classification, primary_reason
    elif quality_reason is not None:
        classification, reason_code = "ci_failure", quality_reason
    else:
        classification, reason_code = primary_classification, primary_reason
    gate_result = {"code_pass": "pass", "code_fail": "fail", "expected_skip": "skipped", "ci_failure": "fail", "review_unavailable": "unavailable", "integration_error": "unavailable"}[classification]
    return Outcome(
        ok=gate_result in ("pass", "skipped"), notes=notes, problems=problems, synthetic_audit=synthetic,
        classification=classification, reason_code=reason_code, gate_result=gate_result,
        audit_available=audit_available, audit_source_attempt=audit_source, audit_artifact_name=artifact_name,
    )


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


def render_summary(outcome: Outcome) -> str:
    lines = ["### Required Gate v2 — aggregate verdict", ""]
    lines.append(f"**Result: {'pass' if outcome.ok else 'fail'}**")
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
        lines.append("```json")
        lines.append(json.dumps(outcome.synthetic_audit, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def _finish(
    outcome: Outcome, summary_path: Optional[str], *, terminal_path: Optional[str] = None, repository: Optional[str] = None,
    identity: Optional[Identity] = None, quality_result: Optional[str] = None, primary_result: Optional[str] = None,
    review_expected: Optional[bool] = None, is_draft: Optional[bool] = None, runner: Optional[str] = None,
) -> int:
    """Shared tail for both the normal and the malformed-input paths through
    `main()`: render + print + (optionally) persist the Step Summary, emit
    ::notice::/::error:: annotations, and map ok -> exit code."""
    if terminal_path and outcome.classification is not None:
        terminal = build_terminal_envelope(repository=repository or "", identity=identity, quality_result=quality_result, primary_result=primary_result, review_expected=review_expected, is_draft=is_draft, runner=runner, outcome=outcome)
        terminal_path = Path(terminal_path)
        temporary_path = terminal_path.with_name(f".{terminal_path.name}.tmp")
        temporary_path.write_text(json.dumps(terminal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(terminal_path)
    summary = render_summary(outcome)
    print(summary)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary)
    for note in outcome.notes:
        print(f"::notice::{note}")
    for problem in outcome.problems:
        print(f"::error::{problem}")
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
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument(
        "--audit-source-attempt",
        default=None,
        help="selected canonical artifact source attempt (the audit remains authoritative)",
    )
    parser.add_argument("--audit-dir", type=Path, default=None)
    parser.add_argument("--audit-artifact-name", default=None)
    parser.add_argument("--terminal-path", default=None, help="gate-terminal.json output path")
    parser.add_argument("--summary-path", default=None, help="$GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)

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
            )

    if not args.repository or not args.head_sha:
        return _finish(
            Outcome(ok=False, problems=["malformed repository/head_sha identity input — fail-closed"]),
            args.summary_path,
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
    )


if __name__ == "__main__":
    sys.exit(main())
