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
  - quality must be `success`; anything else fails the gate.
  - primary `skipped` is only accepted when the PR is a draft, or when review
    was not expected at all (fork PR / `runner: hosted` / any future
    non-review policy) — an unexplained skip on a non-draft, same-repo,
    `runner: self` PR is never treated as a pass.
  - primary `cancelled` always fails closed and produces a synthetic audit
    (status `job_timed_out`).
  - primary `success`/`failure` must have a valid, identity-matched canonical
    audit artifact; a missing/corrupt/mismatched artifact fails closed and
    produces a synthetic audit (status `artifact_missing`).
  - a canonical audit's verdict `pass`/`fail`/`unavailable` maps directly;
    `not_expected`/`waived` are accepted defensively (canary-stage primary
    jobs never actually write these two yet — see gate-v2.yml's primary job —
    so this path is currently unreached in production, but must not silently
    accept a malformed record once a future PR wires up real writers; full
    policy re-verification is T6 TODO, see the plan's waiver section).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

# Mirrors gate-hub's scripts/review/contracts.py PRIMARY_VERDICTS / IDENTITY_FIELDS
# (see module docstring for why this is a hand-kept mirror, not an import).
PRIMARY_VERDICT_DOMAIN = ("pass", "fail", "unavailable", "not_expected", "waived")
IDENTITY_QUINTUPLE = ("repository_id", "head_sha", "run_id", "run_attempt", "pr")

# The two statuses gate-hub's contracts.build_synthetic_primary accepts
# (SYNTHETIC_STATUSES) — this aggregator never invents a third one.
SYNTHETIC_STATUS_TIMED_OUT = "job_timed_out"
SYNTHETIC_STATUS_ARTIFACT_MISSING = "artifact_missing"


def as_bool(value: Any) -> bool:
    """Parse a GitHub Actions boolean-expression string ('true'/'false') the
    same permissive way everywhere in this script — never Python's truthy
    `bool("false") == True` trap."""
    return str(value).strip().lower() == "true"


@dataclass(frozen=True)
class Identity:
    """The identity quintuple the plan requires the aggregator to cross-check
    a downloaded primary audit against, before trusting its verdict."""

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


def validate_audit_identity(record: Any, identity: Identity) -> list[str]:
    """Independent, minimal structural check — NOT a re-implementation of
    gate-hub's validate_primary_record (that full schema validation already
    ran once, inside review-primary, before this record was ever written).
    This aggregator only needs to confirm what it must itself trust before
    basing a merge decision on the downloaded file: it parses as an object,
    it is a primary_review record (this aggregator is the only writer of
    synthetic_primary records — a downloaded synthetic_primary would mean
    something upstream is badly confused), the verdict is a known value, and
    the identity quintuple matches the current run/PR/head — guarding against
    a stale or cross-run artifact silently being adopted.
    """
    if not isinstance(record, dict):
        return ["primary audit is not a JSON object"]
    errors: list[str] = []
    if record.get("kind") != "primary_review":
        errors.append(f"unexpected audit kind {record.get('kind')!r} (expected 'primary_review')")
    verdict = record.get("verdict")
    if verdict not in PRIMARY_VERDICT_DOMAIN:
        errors.append(f"verdict {verdict!r} is not in the accepted domain {PRIMARY_VERDICT_DOMAIN!r}")
    expected = identity.as_dict()
    for key in IDENTITY_QUINTUPLE:
        if record.get(key) != expected[key]:
            errors.append(f"identity mismatch on {key!r}: audit={record.get(key)!r} expected={expected[key]!r}")
    return errors


def validate_companion_fields(record: Mapping[str, Any]) -> list[str]:
    """not_expected/waived acceptance path. T6 TODO: full policy
    re-verification (e.g. confirming a `not_expected_reason` actually matches
    the run's real fork/hosted/no-policy condition, or that a waiver's
    approver/head_sha satisfy the human-account rules in the plan's "Fork,
    waiver and notification semantics") is NOT implemented here — this only
    checks that the required companion field(s) are structurally present, so
    a malformed record can never be silently accepted. Canary-stage primary
    jobs never actually write either verdict yet (fork/hosted skip the whole
    job instead of running review-primary with a not_expected/waived branch —
    see gate-v2.yml's primary job comment), so in the current rollout this
    function's non-empty-error branches are dead code in production and only
    exercised by tests/test_gate_aggregator.py — kept ready for the follow-up
    PR that wires up real not_expected/waived writers.
    """
    verdict = record.get("verdict")
    errors: list[str] = []
    if verdict == "not_expected":
        reason = record.get("not_expected_reason")
        if not isinstance(reason, str) or not reason:
            errors.append("not_expected audit missing a non-empty not_expected_reason")
    elif verdict == "waived":
        waiver = record.get("waiver")
        if not isinstance(waiver, dict) or not all(
            isinstance(waiver.get(k), str) and waiver.get(k) for k in ("approver", "approved_at", "reason")
        ):
            errors.append("waived audit missing a waiver object with approver/approved_at/reason")
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
    is_draft: bool,
    review_expected: bool,
    audit: Any,
    audit_error: Optional[str],
    identity: Identity,
) -> Outcome:
    """The pure decision core — no I/O, no GitHub API, fully unit-testable.

    `audit`/`audit_error` are pre-fetched by the thin CLI wrapper (`main`):
    `audit` is either the parsed JSON object found on disk, or None; when
    None, `audit_error` explains why (download failed, no file, bad JSON,
    ...). See tests/test_gate_aggregator.py for the judgement matrix this
    function must satisfy.
    """
    notes: list[str] = []
    problems: list[str] = []
    synthetic: Optional[dict[str, Any]] = None
    ok = True

    if quality_result == "success":
        notes.append("quality: success")
    else:
        ok = False
        problems.append(f"quality job result is {quality_result!r} (required: success)")

    if primary_result == "skipped":
        if is_draft or not review_expected:
            notes.append(f"primary: skipped and accepted (draft={is_draft}, review_expected={review_expected})")
        else:
            ok = False
            problems.append(
                "primary job was skipped but review was expected (non-draft PR, same-repo head, "
                "runner: self) — an unexplained skip is never accepted as a passing primary review"
            )
    elif primary_result == "cancelled":
        ok = False
        synthetic = build_synthetic_audit(
            identity=identity,
            status=SYNTHETIC_STATUS_TIMED_OUT,
            reason="primary job concluded 'cancelled' before a canonical audit could be finalized",
        )
        problems.append("primary job was cancelled before completion — fail-closed, synthetic audit generated")
    else:
        # success or failure: review-primary writes a canonical audit on exit codes 0
        # (pass) and 1 (fail/unavailable — a legitimate audited outcome); exit codes 2
        # (audit write failed) and 3 (setup error) may leave no audit file at all — see
        # review-primary's exit-code contract docstring.
        if audit is None:
            ok = False
            synthetic = build_synthetic_audit(
                identity=identity,
                status=SYNTHETIC_STATUS_ARTIFACT_MISSING,
                reason=audit_error or "primary audit artifact was not found",
            )
            problems.append(f"primary audit artifact missing ({audit_error or 'not found'}) — fail-closed")
        else:
            errors = validate_audit_identity(audit, identity)
            if errors:
                ok = False
                synthetic = build_synthetic_audit(
                    identity=identity,
                    status=SYNTHETIC_STATUS_ARTIFACT_MISSING,
                    reason="downloaded primary audit failed validation: " + "; ".join(errors),
                )
                problems.append("primary audit failed validation: " + "; ".join(errors))
            else:
                verdict = audit["verdict"]
                if verdict == "pass":
                    if primary_result != "success":
                        ok = False
                        problems.append(
                            f"primary audit verdict is 'pass' but the job result is {primary_result!r} — "
                            "inconsistent, fail-closed"
                        )
                    else:
                        notes.append("primary: pass")
                elif verdict in ("fail", "unavailable"):
                    ok = False
                    problems.append(f"primary review verdict is {verdict!r}")
                else:  # not_expected / waived — defensive acceptance, see validate_companion_fields
                    companion_errors = validate_companion_fields(audit)
                    if companion_errors:
                        ok = False
                        problems.append(
                            f"primary audit verdict {verdict!r} missing required companion field(s): "
                            + "; ".join(companion_errors)
                        )
                    else:
                        notes.append(
                            f"primary: accepted {verdict!r} audit "
                            "(T6 TODO: full policy re-verification not yet implemented — "
                            "see CEO plan 'Fork, waiver and notification semantics')"
                        )

    return Outcome(ok=ok, notes=notes, problems=problems, synthetic_audit=synthetic)


def find_audit_file(audit_dir: Optional[Path]) -> tuple[Any, Optional[str]]:
    """Locate and parse the single downloaded canonical-audit JSON file.

    Returns (record_or_None, error_or_None). Never raises — every failure mode
    (missing directory, empty directory, more than one file, unparsable JSON)
    becomes a descriptive error string for `evaluate()`/the Step Summary,
    because a broken download must fail the gate closed, not crash the job
    with an unhandled exception and leave the check pending.
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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality-result", required=True, help="needs.quality.result")
    parser.add_argument("--primary-result", required=True, help="needs.primary.result")
    parser.add_argument("--is-draft", required=True, help="github.event.pull_request.draft ('true'/'false')")
    parser.add_argument(
        "--review-expected",
        required=True,
        help="the same non-draft && same-repo-head && runner==self expression the primary job's own "
        "`if:` uses — see gate-v2.yml and tests/test_gate_v2_contract.py",
    )
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--audit-dir", type=Path, default=None)
    parser.add_argument("--summary-path", default=None, help="$GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)

    identity = Identity(
        repository_id=args.repository_id,
        head_sha=args.head_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        pr=args.pr_number,
    )

    audit, audit_error = find_audit_file(args.audit_dir)

    outcome = evaluate(
        quality_result=args.quality_result,
        primary_result=args.primary_result,
        is_draft=as_bool(args.is_draft),
        review_expected=as_bool(args.review_expected),
        audit=audit,
        audit_error=audit_error,
        identity=identity,
    )

    summary = render_summary(outcome)
    print(summary)
    if args.summary_path:
        with open(args.summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary)

    for note in outcome.notes:
        print(f"::notice::{note}")
    for problem in outcome.problems:
        print(f"::error::{problem}")

    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
