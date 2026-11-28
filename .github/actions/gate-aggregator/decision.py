"""Pure gate decision, audit validation, and terminal envelope construction."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

_MODELS_NAME = "gate_aggregate_models"
_MODELS = sys.modules.get(_MODELS_NAME)
if _MODELS is None:
    _MODELS_SPEC = importlib.util.spec_from_file_location(
        _MODELS_NAME, Path(__file__).with_name("aggregate_models.py")
    )
    assert _MODELS_SPEC and _MODELS_SPEC.loader
    _MODELS = importlib.util.module_from_spec(_MODELS_SPEC)
    sys.modules[_MODELS_NAME] = _MODELS
    _MODELS_SPEC.loader.exec_module(_MODELS)
_PROJECTION_NAME = "gate_disposition_projection"
_PROJECTION = sys.modules.get(_PROJECTION_NAME)
if _PROJECTION is None:
    _PROJECTION_SPEC = importlib.util.spec_from_file_location(
        _PROJECTION_NAME, Path(__file__).with_name("disposition_projection.py")
    )
    assert _PROJECTION_SPEC and _PROJECTION_SPEC.loader
    _PROJECTION = importlib.util.module_from_spec(_PROJECTION_SPEC)
    sys.modules[_PROJECTION_NAME] = _PROJECTION
    _PROJECTION_SPEC.loader.exec_module(_PROJECTION)
_CONVERGENCE_NAME = "gate_convergence_from_aggregate"
_CONVERGENCE = sys.modules.get(_CONVERGENCE_NAME)
if _CONVERGENCE is None:
    _CONVERGENCE_SPEC = importlib.util.spec_from_file_location(
        _CONVERGENCE_NAME, Path(__file__).with_name("convergence.py")
    )
    assert _CONVERGENCE_SPEC and _CONVERGENCE_SPEC.loader
    _CONVERGENCE = importlib.util.module_from_spec(_CONVERGENCE_SPEC)
    sys.modules[_CONVERGENCE_NAME] = _CONVERGENCE
    _CONVERGENCE_SPEC.loader.exec_module(_CONVERGENCE)
_GATE_ACTION_NAME = "gate_action_from_aggregate"
_GATE_ACTION = sys.modules.get(_GATE_ACTION_NAME)
if _GATE_ACTION is None:
    _GATE_ACTION_SPEC = importlib.util.spec_from_file_location(
        _GATE_ACTION_NAME, Path(__file__).with_name("gate_action.py")
    )
    assert _GATE_ACTION_SPEC and _GATE_ACTION_SPEC.loader
    _GATE_ACTION = importlib.util.module_from_spec(_GATE_ACTION_SPEC)
    sys.modules[_GATE_ACTION_NAME] = _GATE_ACTION
    _GATE_ACTION_SPEC.loader.exec_module(_GATE_ACTION)

from gate_aggregate_models import (  # noqa: E402
    CALLER_CHECKS_DOMAIN, CONVERGENCE_ENVELOPE_KIND,
    CONVERGENCE_ENVELOPE_SCHEMA_VERSION, GATE_RESULT_DOMAIN, IDENTITY_QUINTUPLE,
    PRIMARY_RESULT_DOMAIN, PRIMARY_SKIP_REASON_DOMAIN, PRIMARY_VERDICT_DOMAIN,
    PREFLIGHT_RESULT_DOMAIN, QUALITY_RESULT_DOMAIN, RUNNER_DOMAIN,
    SYNTHETIC_STATUS_ARTIFACT_MISSING, SYNTHETIC_STATUS_TIMED_OUT,
    TERMINAL_CLASSIFICATION_DOMAIN, TERMINAL_PRIMARY_VERDICT_DOMAIN,
    TERMINAL_REASON_DOMAIN, Identity, Outcome, _IDENTITY_INT_FIELDS,
    _is_strict_int,
)
from gate_disposition_projection import (  # noqa: E402
    empty_disposition_receipt_audit, project_disposition_receipt_audit,
)

def build_terminal_envelope(
    *, repository: str, identity: Identity, quality_result: str, primary_result: str, review_expected: bool,
    is_draft: bool, runner: str, outcome: Outcome,
) -> dict[str, Any]:
    """Build the versioned machine-readable gate terminal envelope."""
    if outcome.classification not in TERMINAL_CLASSIFICATION_DOMAIN or outcome.reason_code not in TERMINAL_REASON_DOMAIN or outcome.gate_result not in GATE_RESULT_DOMAIN:
        raise ValueError("terminal field is outside the finite domain")
    if outcome.skip_reason not in (None,) + PRIMARY_SKIP_REASON_DOMAIN:
        raise ValueError("primary skip reason is outside the finite domain")
    if outcome.primary_verdict is not None and outcome.primary_verdict not in TERMINAL_PRIMARY_VERDICT_DOMAIN:
        raise ValueError("terminal primary_verdict is outside the finite domain")
    envelope = {
        "schema_version": 1, "kind": "gate_terminal", "repository": repository,
        "repository_id": identity.repository_id, "pr_number": identity.pr, "run_id": identity.run_id,
        "run_attempt": identity.run_attempt, "head_sha": identity.head_sha,
        "quality_result": quality_result,
        "primary_result": primary_result,
        "skip_reason": outcome.skip_reason,
        "review_expected": review_expected,
        "is_draft": is_draft,
        "runner": runner,
        "gate_result": outcome.gate_result,
        "classification": outcome.classification,
        "reason_code": outcome.reason_code,
        "audit": {"available": outcome.audit_available, "source_attempt": outcome.audit_source_attempt if outcome.audit_available else None, "artifact_name": outcome.audit_artifact_name if outcome.audit_available else None},
        "disposition_receipt_consumption": project_disposition_receipt_audit(
            outcome.disposition_audit,
        ),
    }
    if outcome.primary_verdict is not None:
        envelope["primary_verdict"] = outcome.primary_verdict
    if outcome.convergence is not None:
        envelope["convergence"] = dict(outcome.convergence)
    if outcome.recorded_disposition_claims:
        envelope["recorded_disposition_claims"] = list(outcome.recorded_disposition_claims)
    if outcome.convergence_shadow is not None:
        envelope["convergence_shadow"] = dict(outcome.convergence_shadow)
    if outcome.finding_relation is not None:
        envelope["finding_relation"] = outcome.finding_relation
    return envelope

_CONVERGENCE_SCOPE_FIELDS = (
    "base_sha", "diff_digest", "policy_version", "policy_digest",
    "caller_sha", "reusable_workflow_sha",
)

_canonical_p1_findings = _GATE_ACTION.p1_findings

def _convergence_scope_from_audit(
    audit: Any, identity: Identity,
) -> tuple[Optional[Any], tuple[str, ...]]:
    """Project the complete immutable Scope carried by a canonical audit."""
    if not isinstance(audit, dict):
        return None, ()
    missing_fields = tuple(sorted(field for field in _CONVERGENCE_SCOPE_FIELDS if field not in audit))
    if missing_fields:
        return None, missing_fields
    return _CONVERGENCE.Scope(
        repository_id=identity.repository_id,
        pr_number=identity.pr,
        base_sha=audit["base_sha"],
        head_sha=identity.head_sha,
        diff_digest=audit["diff_digest"],
        policy_version=audit["policy_version"],
        policy_digest=audit["policy_digest"],
        tier=audit.get("tier") if audit.get("tier") in _CONVERGENCE.SUPPORTED_TIERS else "internal",
        caller_sha=audit["caller_sha"],
        reusable_workflow_sha=audit["reusable_workflow_sha"],
    ), ()

def build_convergence_envelope(
    *, scope: Any, decision: Any, audit_digest: str,
    source_attempt: Optional[int], artifact_name: Optional[str],
) -> dict[str, Any]:
    """Build the additive, explicitly versioned one-round convergence envelope."""
    if not isinstance(scope, _CONVERGENCE.Scope):
        raise ValueError("convergence envelope requires a convergence Scope")
    if not isinstance(decision, _CONVERGENCE.RoundDecision):
        raise ValueError("convergence envelope requires a RoundDecision")
    if not isinstance(audit_digest, str) or len(audit_digest) != 64:
        raise ValueError("convergence envelope requires a 64-character SHA-256 digest")
    envelope = {
        "schema_version": CONVERGENCE_ENVELOPE_SCHEMA_VERSION,
        "kind": CONVERGENCE_ENVELOPE_KIND,
        "scope": scope.as_dict(),
        "epoch": decision.state.epoch,
        "audit_digest": audit_digest,
        "source_attempt": source_attempt,
        "artifact_name": artifact_name,
        "decision": decision.decision,
        "state": decision.state.as_dict(),
    }
    return envelope

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
    pr_author: Any,
    is_fork: bool = False,
    classify_review_expected: str = "",
    audit_source_attempt: Optional[int] = None,
    audit_artifact_name: Optional[str] = None,
    scope: Optional[Any] = None,
    audit_digest: Optional[str] = None,
    legacy_raw_audit_digest: Optional[str] = None,
    convergence_state: Optional[Any] = None,
    waiver_receipts: Sequence[Any] = (),
    caller_checks: str = "",
    caller_signal: str = "",
    preflight_result: str = "",
    repository: str | None = None,
    prior_eligible_run_ids: Sequence[int] = (),
    convergence_history_status: str = "history_unavailable",
    neighbor_evidence: Optional[dict[str, Any]] = None,
    inherited_rejection_matches: Optional[dict[tuple[str, str, str], str]] = None,
) -> Outcome:
    """The pure decision core — no I/O, no GitHub API, fully unit-testable.

    `audit`/`audit_error` are pre-fetched by the thin CLI wrapper (`main`):
    `audit` is either the parsed JSON value found on disk (which may be any
    JSON type, not necessarily an object — see `validate_audit_identity`), or
    None; when None, `audit_error` explains why (download failed, no file,
    bad JSON, ...). Draft skip decisions use only the event payload: this run
    represents checks from the draft stage, while a non-draft run with no
    trusted primary result still fails closed. See tests/test_gate_aggregator.py
    for the judgement matrix this function must satisfy.
    """
    notes: list[str] = []
    problems: list[str] = []
    synthetic: Optional[dict[str, Any]] = None
    if not isinstance(pr_author, str) or not pr_author:
        return Outcome(ok=False, problems=["PR author missing or is not a non-empty string — fail-closed"], classification="review_unavailable", reason_code="primary_unavailable", gate_result="unavailable")
    if pr_author == "dependabot[bot]" and not (is_draft is True and primary_result == "skipped"):
        return Outcome(ok=False, problems=["Dependabot PR author has no trusted primary review — fail-closed"], classification="review_unavailable", reason_code="primary_unavailable", gate_result="unavailable")
    invalid_inputs = []
    if runner not in RUNNER_DOMAIN:
        invalid_inputs.append(f"runner input {runner!r} is not a recognized value (expected one of {RUNNER_DOMAIN!r}) — fail-closed")
    if quality_result not in QUALITY_RESULT_DOMAIN:
        invalid_inputs.append(f"quality job result {quality_result!r} is not a recognized value — fail-closed")
    if primary_result not in PRIMARY_RESULT_DOMAIN:
        invalid_inputs.append(f"primary job result {primary_result!r} is not a recognized value (expected one of {PRIMARY_RESULT_DOMAIN!r}) — fail-closed")
    if type(is_draft) is not bool or type(review_expected) is not bool:
        invalid_inputs.append("draft/review_expected must be genuine booleans — fail-closed")
    if type(is_fork) is not bool:
        invalid_inputs.append("is_fork must be a genuine boolean — fail-closed")
    if not isinstance(classify_review_expected, str):
        invalid_inputs.append("classify_review_expected must be a string — fail-closed")
    caller_checks = (caller_checks or "").strip()
    if caller_checks not in ("",) + CALLER_CHECKS_DOMAIN:
        invalid_inputs.append(f"caller_checks input {caller_checks!r} is not a recognized value (expected one of {CALLER_CHECKS_DOMAIN!r} or empty when the evidence step never ran) — fail-closed")
    preflight_result = (preflight_result or "").strip()
    if preflight_result not in PREFLIGHT_RESULT_DOMAIN:
        invalid_inputs.append(f"preflight_result input {preflight_result!r} is not a recognized value — fail-closed")
    if invalid_inputs:
        return Outcome(ok=False, problems=invalid_inputs)

    # gate#105 方案 A: the workflow skips quality by design when primary
    # already failed (quality: needs [primary], if != 'failure'). That skip is
    # the short-circuit working as intended, NOT a quality problem — it must
    # not claim the quality_skipped reason; primary's own terminal state
    # decides the classification. Every other skipped combination (primary
    # not failed) keeps the quality_skipped semantics.
    quality_short_circuited = quality_result == "skipped" and primary_result == "failure"
    quality_reason = None if quality_result == "success" or quality_short_circuited else {"failure": "quality_failure", "cancelled": "quality_cancelled", "skipped": "quality_skipped"}[quality_result]
    primary_classification = primary_reason = None
    audit_available = False
    primary_verdict: Optional[str] = None
    convergence_eligible = False
    audit_source = artifact_name = None
    skip_reason = None

    if quality_result == "success":
        notes.append("quality: success")
    elif quality_short_circuited:
        problems.append("quality job was skipped because primary already failed (short-circuit; the gate result is decided by primary)")
    else:
        problems.append(f"quality job result is {quality_result!r} (required: success)")

    if primary_result == "skipped":
        if is_draft:
            skip_reason = "draft"
            notes.append(f"primary: skipped and accepted (draft={is_draft}, review_expected={review_expected})")
            notes.append("draft event check records quality for the draft stage; primary review is not expected")
            primary_classification, primary_reason = "expected_skip", "review_not_expected"
        elif is_fork:
            skip_reason = "fork"
            notes.append(f"primary: skipped and accepted (draft={is_draft}, review_expected={review_expected})")
            primary_classification, primary_reason = "expected_skip", "review_not_expected"
        elif runner != "self":
            skip_reason = "hosted_runner"
            notes.append(f"primary: skipped and accepted (draft={is_draft}, review_expected={review_expected})")
            primary_classification, primary_reason = "expected_skip", "review_not_expected"
        elif classify_review_expected == "false":
            skip_reason = "review_exempt"
            notes.append(f"primary: skipped and accepted (draft={is_draft}, review_expected={review_expected})")
            primary_classification, primary_reason = "expected_skip", "review_not_expected"
        else:
            problems.append(
                "primary job was skipped but no skip reason could be derived from draft, fork, runner, "
                "or classify output — an unexplained skip is never accepted as a passing primary review"
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
                convergence_eligible = audit_available
                artifact_name = audit_artifact_name if isinstance(audit_artifact_name, str) and audit_artifact_name else None
                notes.append(f"primary audit source run_attempt={audit_source} (current run_attempt={identity.run_attempt})")
                if verdict == "pass":
                    if primary_result != "success":
                        audit_available = False
                        convergence_eligible = False
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
                    convergence_eligible = False
                    audit_source = artifact_name = None
                    problems.append(
                        f"primary audit verdict {verdict!r} is not accepted: canary-stage primary never legitimately writes "
                        "not_expected/waived; companion-field validation is not wired yet"
                    )
                    primary_classification, primary_reason = "integration_error", "audit_invalid"
                if primary_result == "success" and verdict in ("fail", "unavailable"):
                    audit_available = False
                    convergence_eligible = False
                    audit_source = artifact_name = None
                    primary_classification, primary_reason = "integration_error", "job_audit_mismatch"
                if audit_available:
                    primary_verdict = verdict

    # gate#199: a primary that was expected but produced no conclusion
    # (cancelled job, missing/invalid audit) means there is no reviewer
    # verdict to trust — the gate cannot attribute the run to a code
    # problem, so the primary leg outranks the quality leg. An expected
    # skip (draft/fork/hosted) is a verdict in itself and stays below
    # quality: a genuinely red quality run on a draft is still a code
    # problem. code_pass/code_fail keep the old quality-first order (the
    # primary=success + quality=infra-failure residual is left for the
    # follow-up card, not flipped here).
    if primary_classification in ("integration_error", "review_unavailable"):
        classification, reason_code = primary_classification, primary_reason
    elif quality_reason is not None:
        if quality_reason == "quality_failure":
            # gate#199 根治：quality=failure 是 job 级标量，混同了门禁控制面
            # 步骤失败与业务检查真失败。只有结构化证据明确表明预算
            # blocked，才把 preflight 红归因于代码改动；测量 unavailable
            # 必须优先留在基础设施桶，即使其他业务步骤也失败；不能
            # 冒充超预算，也不能放行。
            if preflight_result == "blocked":
                problems.append("PR size preflight blocked (PR exceeds the single-review budget) — split the PR and retry")
                classification, reason_code = "ci_failure", "quality_failure"
            elif preflight_result == "unavailable":
                problems.append(
                    "PR size preflight was unavailable (git measurement failed) — "
                    "treated as an infrastructure problem, not a size decision"
                )
                classification, reason_code = "review_unavailable", "quality_infra"
            elif caller_checks == "signal_terminated":
                sig = (caller_signal or "").strip() or "unknown"
                problems.append(
                    f"quality tests were terminated by signal {sig} — "
                    "infrastructure (host memory/eviction), not a code problem"
                )
                classification, reason_code = "review_unavailable", "quality_infra"
            elif caller_checks == "failed":
                problems.append("quality business checks reported failure (caller_checks=failed) — a code problem")
                classification, reason_code = "ci_failure", "quality_failure"
            else:
                evidence = caller_checks if caller_checks else "missing (the evidence record step never ran)"
                problems.append(
                    f"quality job failed but the business checks show no failure (caller_checks={evidence}) — "
                    "treated as an infrastructure problem, not a code problem"
                )
                classification, reason_code = "review_unavailable", "quality_infra"
        else:
            classification, reason_code = "ci_failure", quality_reason
    else:
        classification, reason_code = primary_classification, primary_reason
    # A structured unavailable result is a measurement failure, not a size
    # verdict. Keep the gate closed even if a malformed caller reports a
    # successful quality job; a skipped quality job remains the primary
    # short-circuit path and is judged by its primary result above.
    if (
        preflight_result == "unavailable"
        and quality_result in ("success", "failure")
        and primary_classification not in ("code_fail", "integration_error")
        and (classification, reason_code) != ("review_unavailable", "quality_infra")
    ):
        problems.append(
            "PR size preflight was unavailable (git measurement failed) — "
            "treated as an infrastructure problem, not a size decision"
        )
        classification, reason_code = "review_unavailable", "quality_infra"
    elif (
        preflight_result == "blocked"
        and quality_result == "success"
        and primary_classification not in ("code_fail", "integration_error")
    ):
        problems.append(
            "PR size preflight reported a budget block despite a successful quality job — "
            "fail-closed as a quality failure"
        )
        classification, reason_code = "ci_failure", "quality_failure"
    elif (
        preflight_result in ("", "skipped", "cancelled")
        and quality_result == "success"
        and primary_classification == "code_pass"
    ):
        problems.append(
            "PR size preflight evidence was missing or non-terminal — "
            "treated as an infrastructure problem, not a size decision"
        )
        classification, reason_code = "review_unavailable", "quality_infra"
    outcome = Outcome(
        ok=False, notes=notes, problems=problems, synthetic_audit=synthetic,
        classification=classification, reason_code=reason_code,
        skip_reason=skip_reason,
        audit_available=audit_available, primary_verdict=primary_verdict,
        audit_source_attempt=audit_source, audit_artifact_name=artifact_name,
    )
    # This is deliberately the only convergence hand-off in the single-round
    # evaluator.  It runs only after the existing audit identity/job verdict
    # checks have succeeded; the old Outcome fields remain single-round facts.
    if scope is not None and convergence_eligible and audit_digest is not None and isinstance(audit, dict):
        p1_findings = () if audit.get("verdict") == "unavailable" and audit.get("result") is None else _canonical_p1_findings(audit)
        if p1_findings is None:
            outcome.primary_verdict = None
            outcome.ok = False
            outcome.classification = "integration_error"
            outcome.reason_code = "audit_invalid"
            outcome.gate_result = "unavailable"
            outcome.problems.append("canonical finding severity is missing, unknown, or malformed — fail-closed")
            return outcome
        p1_ids = tuple(item[0] for item in p1_findings)
        primary = _CONVERGENCE.CanonicalPrimary(
            schema_version=1,
            repository_id=identity.repository_id,
            pr_number=identity.pr,
            head_sha=identity.head_sha,
            run_id=identity.run_id,
            run_attempt=identity.run_attempt,
            verdict=audit["verdict"],
            p1_ids=p1_ids,
            p1_findings=p1_findings,
        )
        # Blocking-matrix-v1 audits widen the receipt-coverable set to every
        # finding whose gate_action is block/must_respond (including a saas
        # minor). The round-convergence primary above stays P1-only; only the
        # disposition projection below uses the widened set.
        projection = _GATE_ACTION.disposition_projection(audit, primary)
        disposition_ids, disposition_primary, gate_actions, invalid_note = (
            projection if projection is not None else (p1_ids, primary, None, None)
        )
        if invalid_note:
            outcome.notes.append(invalid_note)
        processing_key = _CONVERGENCE.ProcessingKey(
            identity.repository_id, identity.pr, identity.run_id, identity.run_attempt,
        )
        disposition_audit = _CONVERGENCE.record_dispositions(
            disposition_ids,
            waiver_receipts,
            scope=scope,
            primary=disposition_primary,
            audit_digest=audit_digest,
            legacy_raw_audit_digest=legacy_raw_audit_digest,
            repository=repository,
            neighbor_evidence=neighbor_evidence,
            gate_actions=gate_actions,
            inherited_rejection_matches=inherited_rejection_matches,
        )
        outcome.disposition_audit = disposition_audit
        state = convergence_state or _CONVERGENCE.initial_state(scope)
        round_decision = _CONVERGENCE.evaluate_round(
            state=state,
            scope=scope,
            primary=primary,
            audit_digest=audit_digest,
            waiver_receipts=waiver_receipts,
            disposition_audit=disposition_audit,
            processing_key=processing_key,
            repository=repository, coverage_result=audit.get("result"),
        )
        outcome.convergence_envelope = build_convergence_envelope(
            scope=scope,
            decision=round_decision,
            audit_digest=audit_digest,
            source_attempt=audit_source,
            artifact_name=artifact_name,
        )
        if round_decision.accepted and not round_decision.no_op and round_decision.event_id:
            outcome.convergence_receipt = _CONVERGENCE.receipt_for_round(
                scope=scope,
                primary=primary,
                audit_digest=audit_digest,
                decision=round_decision,
                source_attempt=audit_source,
                artifact_name=artifact_name,
            )
        recorded_claim_lines = list(_CONVERGENCE.recorded_disposition_lines(disposition_audit))
        if recorded_claim_lines:
            outcome.recorded_disposition_claims = recorded_claim_lines
        if (
            outcome.classification == "code_fail"
            and outcome.reason_code == "primary_findings"
            and audit["verdict"] == "fail"
            and disposition_audit.recorded_receipts
            and not disposition_audit.remaining_p1_ids
        ):
            outcome.classification = "code_pass"
            outcome.reason_code = "disposition_resolved"
            outcome.problems = [problem for problem in outcome.problems if problem != "primary review verdict is 'fail'"]
            outcome.notes.append("all current P1 findings are covered by active evidence-bound gate-disposition receipts")
    if scope is not None:
        eligible_this_round = (
            audit_available
            and audit_source is not None
            and isinstance(audit, dict)
            and audit.get("verdict") in ("pass", "fail")
        )
        outcome.convergence_shadow = {
            **_CONVERGENCE.pr_round_budget(
                prior_run_ids=prior_eligible_run_ids,
                current_run_id=identity.run_id,
                current_eligible=eligible_this_round,
                tier=scope.tier,
                history_status=convergence_history_status,
            ),
            "eligible_this_round": eligible_this_round,
        }
    # No classification path can publish pass while a blocking finding remains.
    gate_result = {"code_pass": "pass", "code_fail": "fail", "expected_skip": "skipped", "ci_failure": "fail", "review_unavailable": "unavailable", "integration_error": "unavailable"}[outcome.classification]
    if gate_result == "pass" and outcome.disposition_audit and outcome.disposition_audit.remaining_p1_ids:
        gate_result = "fail"
        outcome.classification, outcome.reason_code = "code_fail", "primary_findings"
        outcome.problems.append("primary review still has unresolved blocking findings")
    outcome.gate_result = gate_result
    outcome.ok = gate_result in ("pass", "skipped")
    return outcome
