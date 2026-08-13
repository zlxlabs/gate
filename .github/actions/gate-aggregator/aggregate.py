#!/usr/bin/env python3
"""gate-aggregator — decision core for the Required Gate v2 final `gate` job.

Implements the "gate:" node of the Required Gate state machine (see the private
gate-hub repo's ceo-plans/2026-07-24-shadow-review-independence.md, "Required
Gate" + "Fork, waiver and notification semantics" sections). Per that plan's
"Caller / reusable workflow boundary", this aggregator is a portable script
that depends ONLY on python3 stdlib + data the calling workflow hands it — no
gate-hub import, no hosted-image-specific tool. The one network call it may
make is the optional Stage 4 PR-comment receipt (one fail-open issue-comment
POST via stdlib urllib, gated behind `--pr-comment`, off by default); it never
feeds back into the verdict or the exit code. When the caller supplies
`--comment-receipt-path`, the result of that attempt is also persisted as a
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
semantics and add a durable `gate_pr_comment_receipt` JSON artifact at the
workflow boundary. The artifact records whether the comment was created and,
when it was not, a stable reason category plus HTTP status where available. A
transport failure after the POST was attempted is recorded as
`delivery=unknown`, never as a definite `not_created`, because the server may
already have created the comment before the response was lost.
The reusable workflow uploads it with `if: always()` and
`if-no-files-found: error`, so a real consumer can distinguish "receipt was
created" from "receipt was expected but not created" without depending on the
same PR-comment API path. Successful comment delivery adds no annotation or
second notification; it only records the quiet machine-readable receipt.

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
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
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
COMMENT_RECEIPT_SCHEMA_VERSION = 1
COMMENT_RECEIPT_KIND = "gate_pr_comment_receipt"

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


# Stage 4 PR-comment receipt: post ONE NEW issue comment per run whose body is
# the exact render_summary() product that just went to the Step Summary —
# never a second, hand-maintained copy of the text (two copies would drift).
# Deliberate design choices (locked in the task card):
#   - fail-open: a notification outage must never turn a green gate red, so
#     every failure mode (missing token, fork-PR 403, 5xx, network error)
#     degrades to a ::warning:: annotation and nothing else — no ::error::,
#     no changed exit code, no exception escaping.
#   - no sticky/marker/PATCH de-dup: GitHub only emails on comment CREATION,
#     and the high-iteration stage wants one mail per gate run; not looking
#     up existing comments also removes the check-then-act race.
#   - token comes from the environment only (GITHUB_TOKEN/GH_TOKEN), never
#     argv (argv shows up in process lists and logs).
def _post_issue_comment(*, repository: str, pr_number: int, body: str, token: str) -> None:
    """POST one new comment on the PR via the issues-comments API (stdlib
    urllib only — this script must stay portable, no gh-CLI dependency).
    Raises on any transport or API failure; the fail-open boundary is
    `_post_pr_comment_fail_open`, not this function.
    """
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments",
        data=json.dumps({"body": body}, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "gate-aggregator",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15):
        pass  # urlopen raises HTTPError for any non-2xx; a 201 body needs no parsing


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


def _build_comment_receipt(
    *, body: str, repository: Optional[str], pr_number: Optional[int], identity: Optional[Identity],
    delivery: str, reason_code: str, error_category: Optional[str] = None,
    http_status: Optional[int] = None,
) -> dict[str, Any]:
    """Build the cross-job payload for the PR-comment delivery attempt.

    The body itself stays in the Step Summary/comment contract; a digest lets
    a consumer correlate this receipt to that exact body without copying the
    potentially large comment into a second durable artifact. `delivery` is
    one of `created`, `not_created`, `unknown`, or `not_enabled`; the created
    field is null whenever the outcome is not determinable or was disabled.
    """
    return {
        "schema_version": COMMENT_RECEIPT_SCHEMA_VERSION,
        "kind": COMMENT_RECEIPT_KIND,
        "repository": repository or "",
        "repository_id": identity.repository_id if identity else None,
        "pr_number": pr_number,
        "run_id": identity.run_id if identity else None,
        "run_attempt": identity.run_attempt if identity else None,
        "head_sha": identity.head_sha if identity else None,
        "comment_expected": delivery != "not_enabled",
        "comment_created": True if delivery == "created" else False if delivery == "not_created" else None,
        "delivery": delivery,
        "reason_code": reason_code,
        "error_category": error_category,
        "http_status": http_status,
        "comment_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def _post_pr_comment_fail_open(
    *, body: str, repository: Optional[str], pr_number: Optional[int], identity: Optional[Identity],
) -> dict[str, Any]:
    """Best-effort Stage 4 PR-comment receipt. NEVER raises and NEVER prints
    ::error::: the Step Summary plus the exit code stay the authoritative
    receipt; this is only the email-visible mirror of it. The try wraps the
    WHOLE comment attempt (token lookup, target check, POST) so no failure in
    any part of it can leak out and redden the gate; the warning annotations
    themselves go through `_warn`, so a broken-pipe stdout cannot escape
    either.
    """
    try:
        if not repository or pr_number is None:
            _warn("::warning::--pr-comment is enabled but repository/pr-number is unavailable — skipping the PR comment (Step Summary remains the authoritative receipt)")
            return _build_comment_receipt(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code="missing_target", error_category="configuration",
            )
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            _warn("::warning::--pr-comment is enabled but neither GITHUB_TOKEN nor GH_TOKEN is set — skipping the PR comment (Step Summary remains the authoritative receipt)")
            return _build_comment_receipt(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code="missing_token", error_category="configuration",
            )
        _post_issue_comment(repository=repository, pr_number=pr_number, body=body, token=token)
        return _build_comment_receipt(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="created", reason_code="posted",
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            # A 403 is NOT always the fork downgrade: on a same-repo PR the
            # same status means a secondary rate-limit (GitHub answers 403 or
            # 429) or a permission failure. The rendering layer cannot tell
            # fork from same-repo here, so name both instead of misattributing
            # every 403 to fork (P3-3).
            _warn(
                "::warning::could not post the gate PR comment (HTTP 403) — this may be the expected "
                "read-only token downgrade on fork PRs, or a rate-limit or permission problem on a "
                "same-repo PR. Step Summary remains the authoritative receipt."
            )
        else:
            _warn(f"::warning::could not post the gate PR comment (HTTP {exc.code}) — Step Summary remains the authoritative receipt")
        if exc.code == 403:
            reason_code, category = "http_403", "permission_or_rate_limit"
        elif exc.code == 429:
            reason_code, category = "http_429", "permission_or_rate_limit"
        elif exc.code >= 500:
            reason_code, category = "http_5xx", "server_error"
        else:
            reason_code, category = "http_error", "http_error"
        return _build_comment_receipt(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="not_created", reason_code=reason_code, error_category=category, http_status=exc.code,
        )
    except Exception as exc:
        _warn(f"::warning::could not post the gate PR comment ({type(exc).__name__}: {exc}) — Step Summary remains the authoritative receipt")
        return _build_comment_receipt(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="unknown", reason_code="network_indeterminate", error_category="network_error",
        )


def _write_comment_receipt(path: str, receipt: dict[str, Any]) -> None:
    """Publish the receipt atomically so artifact upload never sees partial JSON."""
    target = Path(path)
    temporary_path = target.with_name(f".{target.name}.tmp")
    temporary_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(target)


def _finish(
    outcome: Outcome, summary_path: Optional[str], *, terminal_path: Optional[str] = None, repository: Optional[str] = None,
    identity: Optional[Identity] = None, quality_result: Optional[str] = None, primary_result: Optional[str] = None,
    review_expected: Optional[bool] = None, is_draft: Optional[bool] = None, runner: Optional[str] = None,
    pr_comment: bool = False, pr_number: Optional[int] = None, comment_receipt_path: Optional[str] = None,
) -> int:
    """Shared tail for both the normal and the malformed-input paths through
    `main()`: render + print + (optionally) persist the Step Summary, emit
    ::notice::/::error:: annotations, and map ok -> exit code."""
    summary = render_summary(outcome, repository=repository, identity=identity, is_draft=is_draft, runner=runner)
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
    # Stage 4 PR-comment receipt comes LAST in the side-effect order — after
    # the Step Summary append and the terminal-envelope replace — so a killed
    # process can never leave a "comment says pass but terminal/summary was
    # never persisted" inconsistency; the reverse (terminal persisted, comment
    # never posted) is the safe direction. Fail-open by construction: it
    # cannot change the exit code on the next line.
    if comment_receipt_path:
        receipt_path = Path(comment_receipt_path)
        try:
            receipt_path.unlink(missing_ok=True)
        except OSError as exc:
            _warn(f"::warning::could not remove the previous gate PR-comment receipt ({type(exc).__name__}: {exc})")
        if pr_comment:
            receipt = _post_pr_comment_fail_open(
                body=summary, repository=repository, pr_number=pr_number, identity=identity,
            )
        else:
            receipt = _build_comment_receipt(
                body=summary, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_enabled", reason_code="not_enabled",
            )
        try:
            _write_comment_receipt(comment_receipt_path, receipt)
        except OSError as exc:
            _warn(f"::warning::could not persist the gate PR-comment receipt ({type(exc).__name__}: {exc})")
    elif pr_comment:
        _post_pr_comment_fail_open(
            body=summary, repository=repository, pr_number=pr_number, identity=identity,
        )
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
    parser.add_argument("--comment-receipt-path", default=None, help="durable PR-comment delivery receipt JSON output path")
    parser.add_argument("--summary-path", default=None, help="$GITHUB_STEP_SUMMARY")
    parser.add_argument(
        "--pr-comment",
        default="false",
        help="'true'/'false' (strictly parsed, default false) — Stage 4: also post the aggregate verdict "
        "as ONE new PR comment, reusing the Step Summary text verbatim. Fail-open; the token is read "
        "from the GITHUB_TOKEN/GH_TOKEN environment, never from argv.",
    )
    args = parser.parse_args(argv)

    try:
        pr_comment = as_bool(args.pr_comment)
    except BoolParseError as exc:
        return _finish(
            Outcome(ok=False, problems=[f"malformed boolean input — fail-closed: {exc}"]),
            args.summary_path,
            repository=args.repository,
            pr_number=args.pr_number,
            comment_receipt_path=args.comment_receipt_path,
        )

    if args.pr_number is None:
        return _finish(
            Outcome(ok=False, problems=["missing PR number — fail-closed"]),
            args.summary_path,
            pr_comment=pr_comment,
            repository=args.repository,
            pr_number=None,
            comment_receipt_path=args.comment_receipt_path,
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
                pr_comment=pr_comment,
                repository=args.repository,
                pr_number=args.pr_number,
                comment_receipt_path=args.comment_receipt_path,
            )

    if not args.repository or not args.head_sha:
        return _finish(
            Outcome(ok=False, problems=["malformed repository/head_sha identity input — fail-closed"]),
            args.summary_path,
            pr_comment=pr_comment,
            repository=args.repository,
            pr_number=args.pr_number,
            comment_receipt_path=args.comment_receipt_path,
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
            pr_comment=pr_comment,
            repository=args.repository,
            pr_number=args.pr_number,
            comment_receipt_path=args.comment_receipt_path,
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
        pr_comment=pr_comment,
        pr_number=identity.pr,
        comment_receipt_path=args.comment_receipt_path,
    )


if __name__ == "__main__":
    sys.exit(main())
