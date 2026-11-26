#!/usr/bin/env python3
"""gate-aggregator — decision core for the Required Gate v2 final `gate` job.

Implements the "gate:" node of the Required Gate state machine (see the private
gate-hub repo's ceo-plans/2026-07-24-shadow-review-independence.md, "Required
Gate" + "Fork, waiver and notification semantics" sections). Per that plan's
"Caller / reusable workflow boundary", this aggregator is a portable script
that depends ONLY on python3 stdlib + data the calling workflow hands it — no
gate-hub import, no hosted-image-specific tool. The one network call it may
make is the optional Stage 4 PR-comment receipt (one fail-open issue-comment
POST via stdlib urllib for the marker-located status panel); it never
feeds back into the verdict or the exit code. When the caller supplies
`--panel-delivery-path`, the result of that attempt is also persisted as a
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
semantics and add a durable `gate_v2_status_panel_delivery` JSON artifact at the
workflow boundary. The artifact records whether the comment was created and,
when it was not, a stable reason category plus HTTP status where available. A
transport failure after the POST was attempted is recorded as
`delivery=unknown`, never as a definite `not_created`, because the server may
already have created the comment before the response was lost.
The reusable workflow uploads it with `if: always()` and
`if-no-files-found: error`. A consumer must distinguish three receipt states:
missing artifact means the write failed and stale content was cleared;
an artifact that is not parseable means the write failed and old evidence was
poisoned; a parseable old JSON artifact means cleanup also failed and the
`receipt channel is untrusted` warning is the deciding signal. Successful
comment delivery adds no annotation or second notification; it only records
the quiet machine-readable receipt.

This module intentionally duplicates (does NOT import) two small pieces of
gate-hub's scripts/review/contracts.py: the primary-verdict domain and the
identity-quintuple field names. There is deliberately no shared package
between the public zlxlabs/gate repo and the private gate-hub repo (see the
plan's Code boundary / Caller boundary sections) — if contracts.py's
IDENTITY_FIELDS or PRIMARY_VERDICTS ever change shape, this file must be
updated by hand, and the two repos' contract tests are the thing that would
catch a silent drift, not a runtime import.

Finding summary (gate-hub#1294): `--render-findings-summary` is the one mode
that only ADDS readable evidence — it republishes id/severity/file:line/title
for every canonical-audit finding into the job log and the Step Summary, because
a caller repo cannot read the canonical audit itself (no silo credentials) and
used to be left with nothing but a verdict line. It never judges anything, never
exits non-zero, and a degraded render always leaves exactly one greppable
`FINDING_SUMMARY_UNAVAILABLE reason=<type>` line behind.

Judgement responsibilities (see gate-v2.yml's `gate` job and this repo's
tests/test_gate_aggregator.py for the full decision matrix):
  - `runner` must be a recognized value (`self`/`hosted`); anything else fails
    closed immediately rather than silently falling through whatever branch a
    typo happens to resemble.
  - quality must be `success`; anything else fails the gate.
  - primary's job `result` must itself be a recognized value (`success`,
    `failure`, `cancelled`, `skipped`); an unrecognized string fails closed
    rather than being treated as any particular case.
  - primary `skipped` is accepted only when a reason is identified in this
    order: draft, fork, hosted runner, review-exempt paths. An unexplained
    skip is never accepted as a passing primary review.
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
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

GATE_ROOT = Path(__file__).resolve().parents[3]
if str(GATE_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE_ROOT))

from scripts.scrub_outbound import runtime_values_from_environment, scrub_for_publish


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

# gate#297: collector for NeighborEvidence (subprocess/git/silo I/O lives
# there; this module and convergence.py stay out of that business).
_NEIGHBOR_EVIDENCE_SPEC = importlib.util.spec_from_file_location(
    "gate_neighbor_evidence_from_aggregate",
    Path(__file__).with_name("neighbor_evidence.py"),
)
assert _NEIGHBOR_EVIDENCE_SPEC and _NEIGHBOR_EVIDENCE_SPEC.loader
_NEIGHBOR_EVIDENCE = importlib.util.module_from_spec(_NEIGHBOR_EVIDENCE_SPEC)
sys.modules[_NEIGHBOR_EVIDENCE_SPEC.name] = _NEIGHBOR_EVIDENCE
_NEIGHBOR_EVIDENCE_SPEC.loader.exec_module(_NEIGHBOR_EVIDENCE)
_CARRY_SPEC = importlib.util.spec_from_file_location(
    "gate_carry_findings_from_aggregate", Path(__file__).with_name("carry_findings.py"))
_CARRY = importlib.util.module_from_spec(_CARRY_SPEC)
sys.modules[_CARRY_SPEC.name] = _CARRY
_CARRY_SPEC.loader.exec_module(_CARRY)
_INHERITED_REJECTION_SPEC = importlib.util.spec_from_file_location(
    "gate_inherited_rejection_from_aggregate",
    Path(__file__).with_name("inherited_rejection.py"),
)
assert _INHERITED_REJECTION_SPEC and _INHERITED_REJECTION_SPEC.loader
_INHERITED_REJECTION = importlib.util.module_from_spec(_INHERITED_REJECTION_SPEC)
sys.modules[_INHERITED_REJECTION_SPEC.name] = _INHERITED_REJECTION
_INHERITED_REJECTION_SPEC.loader.exec_module(_INHERITED_REJECTION)
_CARRY_BLOCKERS_SPEC = importlib.util.spec_from_file_location(
    "gate_carry_blockers_from_aggregate", Path(__file__).with_name("carry_blockers.py"))
assert _CARRY_BLOCKERS_SPEC and _CARRY_BLOCKERS_SPEC.loader
_CARRY_BLOCKERS = importlib.util.module_from_spec(_CARRY_BLOCKERS_SPEC)
_CARRY_BLOCKERS_SPEC.loader.exec_module(_CARRY_BLOCKERS)

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
_FINDING_RELATIONS_NAME = "gate_finding_relations"
_FINDING_RELATIONS = sys.modules.get(_FINDING_RELATIONS_NAME)
if _FINDING_RELATIONS is None:
    _FINDING_RELATIONS_SPEC = importlib.util.spec_from_file_location(
        _FINDING_RELATIONS_NAME, Path(__file__).with_name("finding_relations.py")
    )
    assert _FINDING_RELATIONS_SPEC and _FINDING_RELATIONS_SPEC.loader
    _FINDING_RELATIONS = importlib.util.module_from_spec(_FINDING_RELATIONS_SPEC)
    sys.modules[_FINDING_RELATIONS_NAME] = _FINDING_RELATIONS
    _FINDING_RELATIONS_SPEC.loader.exec_module(_FINDING_RELATIONS)
_DECISION_NAME = "gate_aggregate_decision"
_DECISION = sys.modules.get(_DECISION_NAME)
if _DECISION is None:
    _DECISION_SPEC = importlib.util.spec_from_file_location(
        _DECISION_NAME, Path(__file__).with_name("decision.py")
    )
    assert _DECISION_SPEC and _DECISION_SPEC.loader
    _DECISION = importlib.util.module_from_spec(_DECISION_SPEC)
    sys.modules[_DECISION_NAME] = _DECISION
    _DECISION_SPEC.loader.exec_module(_DECISION)

from gate_aggregate_models import (  # noqa: E402
    ACTIONS_BOT_ID, ACTIONS_BOT_LOGIN, CALLER_CHECKS_DOMAIN,
    CONVERGENCE_ENVELOPE_KIND, CONVERGENCE_ENVELOPE_SCHEMA_VERSION,
    DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS, DEFAULT_PUBLISH_BUDGET_SECONDS,
    DISPOSITION_ARTIFACT_PREFIX, FINDING_SUMMARY_MAX_FINDINGS,
    FINDING_SUMMARY_MAX_TITLE_CHARS, FINDING_SUMMARY_UNAVAILABLE,
    GATE_RESULT_DOMAIN, GITHUB_API_TIMEOUT_SECONDS,
    HISTORY_RECONSTRUCTION_BUDGET_ENV, IDENTITY_QUINTUPLE,
    MAX_HISTORY_WARNING_CHARS, MAX_REPO_WIDE_HISTORY_PAGES, MAX_TARGETED_HISTORY_RUNS,
    PANEL_BUCKET_BY_GATE_RESULT, PANEL_DELIVERY_KIND, PANEL_DELIVERY_SCHEMA_VERSION,
    PANEL_HISTORY_ROW_SCHEMA_VERSION, PANEL_MARKER, PREFLIGHT_RESULT_DOMAIN,
    PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE, PRIMARY_BUDGET_EXHAUSTED_REASON,
    PRIMARY_FINDINGS_PANEL_MARKER, PRIMARY_RESULT_DOMAIN,
    PRIMARY_SKIP_REASON_DOMAIN, PRIMARY_VERDICT_DOMAIN,
    PUBLISH_BUDGET_ENV, PUBLISH_OPERATION_ORDER, QUALITY_RESULT_DOMAIN,
    RUNNER_DOMAIN, SILO_CLI_CALL_TIMEOUT_SECONDS, SILO_DISPOSITION_TIER,
    SILO_TERMINAL_TIER, SYNTHETIC_STATUS_ARTIFACT_MISSING,
    SYNTHETIC_STATUS_TIMED_OUT, TERMINAL_CLASSIFICATION_DOMAIN,
    TERMINAL_HISTORY_CACHE_SCHEMA_VERSION, TERMINAL_PRIMARY_VERDICT_DOMAIN,
    TERMINAL_REASON_DOMAIN, _ACTIVE_PUBLISH_BUDGET, _COLON_RUN,
    _FINDING_SUMMARY_COLUMNS, _IDENTITY_INT_FIELDS, BoolParseError, HistoryLoad,
    Identity, Outcome, _PublishBudget, _PublishBudgetExhausted, as_bool,
    _is_strict_int,
)
from gate_disposition_projection import (  # noqa: E402
    empty_disposition_receipt_audit, project_disposition_receipt_audit,
)
from gate_finding_relations import (  # noqa: E402
    FINDING_TEXT_MAX, PREVIOUS_FINDING_LIMIT, RELATION_DEGRADED,
    RELATION_UNKNOWN, RELATION_VALUES, FindingRelationError,
    _LATEST_LEDGER_DEGRADE, _LEDGER_LATEST_OBJECT, _LEDGER_LATEST_PREFIX,
    _P1_RELATION_SEVERITIES, _close_consumed_false_positive_carry,
    _clip_text, _consume_explicit_withdrawals, _degrade_previous_round,
    _is_location_conflict, _norm_text, _require_relation, apply_finding_relation,
    build_finding_relation, project_finding, relate_findings,
    validate_finding_relation_block,
)
_GATE_IO_NAME = "gate_aggregate_io"
_GATE_IO = sys.modules.get(_GATE_IO_NAME)
if _GATE_IO is None:
    _GATE_IO_SPEC = importlib.util.spec_from_file_location(
        _GATE_IO_NAME, Path(__file__).with_name("gate_io.py")
    )
    assert _GATE_IO_SPEC and _GATE_IO_SPEC.loader
    _GATE_IO = importlib.util.module_from_spec(_GATE_IO_SPEC)
    sys.modules[_GATE_IO_NAME] = _GATE_IO
    _GATE_IO_SPEC.loader.exec_module(_GATE_IO)

_TERMINAL_HISTORY_NAME = "gate_terminal_history"
_TERMINAL_HISTORY = sys.modules.get(_TERMINAL_HISTORY_NAME)
if _TERMINAL_HISTORY is None:
    _TERMINAL_HISTORY_SPEC = importlib.util.spec_from_file_location(
        _TERMINAL_HISTORY_NAME, Path(__file__).with_name("terminal_history.py")
    )
    assert _TERMINAL_HISTORY_SPEC and _TERMINAL_HISTORY_SPEC.loader
    _TERMINAL_HISTORY = importlib.util.module_from_spec(_TERMINAL_HISTORY_SPEC)
    sys.modules[_TERMINAL_HISTORY_NAME] = _TERMINAL_HISTORY
    _TERMINAL_HISTORY_SPEC.loader.exec_module(_TERMINAL_HISTORY)

_PREVIOUS_CONTEXT_NAME = "gate_previous_context"
_PREVIOUS_CONTEXT = sys.modules.get(_PREVIOUS_CONTEXT_NAME)
if _PREVIOUS_CONTEXT is None:
    _PREVIOUS_CONTEXT_SPEC = importlib.util.spec_from_file_location(
        _PREVIOUS_CONTEXT_NAME, Path(__file__).with_name("previous_context.py")
    )
    assert _PREVIOUS_CONTEXT_SPEC and _PREVIOUS_CONTEXT_SPEC.loader
    _PREVIOUS_CONTEXT = importlib.util.module_from_spec(_PREVIOUS_CONTEXT_SPEC)
    sys.modules[_PREVIOUS_CONTEXT_NAME] = _PREVIOUS_CONTEXT
    _PREVIOUS_CONTEXT_SPEC.loader.exec_module(_PREVIOUS_CONTEXT)

from gate_aggregate_decision import (  # noqa: E402
    _CONVERGENCE_SCOPE_FIELDS, _canonical_p1_findings,
    _convergence_scope_from_audit, build_convergence_envelope,
    build_synthetic_audit, build_terminal_envelope, evaluate,
    validate_audit_identity,
)



from types import SimpleNamespace

def _gate_io_ports() -> SimpleNamespace:
    return SimpleNamespace(
        _download_terminal_zip=_download_terminal_zip,
        _github_json=_github_json,
        _github_identity=_github_identity,
        _fetch_panel_comments=_fetch_panel_comments,
        _post_issue_comment=_post_issue_comment,
        _patch_issue_comment=_patch_issue_comment,
        _delete_issue_comment=_delete_issue_comment,
        _read_audit_file=_read_audit_file,
        _write_convergence_receipt=_write_convergence_receipt,
        _read_disposition_zip=_read_disposition_zip,
        _fetch_github_disposition_receipts=_fetch_github_disposition_receipts,
        _silo_configured=_silo_configured,
        _silo_cli=_silo_cli,
        _silo_objects_under=_silo_objects_under,
        _fetch_silo_disposition_receipts=_fetch_silo_disposition_receipts,
        _receipt_merge_key=_receipt_merge_key,
        _merge_disposition_receipts=_merge_disposition_receipts,
        _fetch_disposition_receipts=_fetch_disposition_receipts,
        _NoRedirectHandler=_NoRedirectHandler,
        _github_request=_github_request,
    )

def _terminal_history_ports() -> SimpleNamespace:
    return SimpleNamespace(
        _terminal_row=_terminal_row,
        _compute_pr_primary_convergence=_compute_pr_primary_convergence,
        _read_terminal_zip=_read_terminal_zip,
        _consume_terminal_artifact=_consume_terminal_artifact,
        _fetch_github_terminal_history=_fetch_github_terminal_history,
        _terminal_artifact_run_id=_terminal_artifact_run_id,
        _silo_artifact_name=_silo_artifact_name,
        _consume_silo_terminal_json=_consume_silo_terminal_json,
        _fetch_silo_terminal_history=_fetch_silo_terminal_history,
        _pr_target_run_ids=_pr_target_run_ids,
        _load_pr_convergence_history=_load_pr_convergence_history,
        _terminal_history_cache_path=_terminal_history_cache_path,
        _write_terminal_history_cache=_write_terminal_history_cache,
        _read_terminal_history_cache=_read_terminal_history_cache,
        _merge_history_loads=_merge_history_loads,
        _fetch_terminal_history=_fetch_terminal_history,
        _download_terminal_zip=_download_terminal_zip,
        _github_json=_github_json,
        _github_identity=_github_identity,
        _fetch_panel_comments=_fetch_panel_comments,
        _post_issue_comment=_post_issue_comment,
        _patch_issue_comment=_patch_issue_comment,
        _delete_issue_comment=_delete_issue_comment,
        _read_audit_file=_read_audit_file,
        _write_convergence_receipt=_write_convergence_receipt,
        _read_disposition_zip=_read_disposition_zip,
        _fetch_github_disposition_receipts=_fetch_github_disposition_receipts,
        _silo_configured=_silo_configured,
        _silo_cli=_silo_cli,
        _silo_objects_under=_silo_objects_under,
        _fetch_silo_disposition_receipts=_fetch_silo_disposition_receipts,
        _receipt_merge_key=_receipt_merge_key,
        _merge_disposition_receipts=_merge_disposition_receipts,
        _fetch_disposition_receipts=_fetch_disposition_receipts,
        _FINDING_RELATIONS=_FINDING_RELATIONS,
    )

def _previous_context_ports() -> SimpleNamespace:
    return SimpleNamespace(
        _ledger_latest_key=_ledger_latest_key,
        _previous_findings_from_silo=_previous_findings_from_silo,
        load_previous_round_findings=load_previous_round_findings,
        _project_previous_dispositions=_project_previous_dispositions,
        _render_previous_context_cli=_render_previous_context_cli,
        _annotate_primary_audit_cli=_annotate_primary_audit_cli,
        _silo_configured=_silo_configured,
        _silo_cli=_silo_cli,
        _fetch_silo_disposition_receipts=_fetch_silo_disposition_receipts,
        _GATE_ACTION=_GATE_ACTION,
        _INHERITED_REJECTION=_INHERITED_REJECTION,
        _CARRY=_CARRY,
        _NEIGHBOR_EVIDENCE=_NEIGHBOR_EVIDENCE,
        _CARRY_BLOCKERS=_CARRY_BLOCKERS,
        _FINDING_RELATIONS=_FINDING_RELATIONS,
        validate_audit_identity=validate_audit_identity,
    )


def _ledger_latest_key(repository_id: int, pr_number: int) -> str:
    return _PREVIOUS_CONTEXT._ledger_latest_key(repository_id=repository_id, pr_number=pr_number, _ports=_previous_context_ports())


def _previous_findings_from_silo(
    *, repository_id: int, pr_number: int, run_id: int, run_attempt: int,
) -> tuple[Optional[str], list[Any]]:
    return _PREVIOUS_CONTEXT._previous_findings_from_silo(repository_id=repository_id, pr_number=pr_number, run_id=run_id, run_attempt=run_attempt, _ports=_previous_context_ports())


def load_previous_round_findings(
    *, path: Optional[str], repository_id: int, pr_number: int, run_id: int, run_attempt: int,
) -> dict[str, Any]:
    return _PREVIOUS_CONTEXT.load_previous_round_findings(path=path, repository_id=repository_id, pr_number=pr_number, run_id=run_id, run_attempt=run_attempt, _ports=_previous_context_ports())


def _project_previous_dispositions(
    *, repository_id: int, pr_number: int,
) -> list[dict[str, Any]]:
    return _PREVIOUS_CONTEXT._project_previous_dispositions(repository_id=repository_id, pr_number=pr_number, _ports=_previous_context_ports())


def _render_previous_context_cli(argv: list[str]) -> int:
    return _PREVIOUS_CONTEXT._render_previous_context_cli(argv=argv, _ports=_previous_context_ports())


def _annotate_primary_audit_cli(argv: list[str]) -> int:
    return _PREVIOUS_CONTEXT._annotate_primary_audit_cli(argv=argv, _ports=_previous_context_ports())


class _FindingsSummaryUnavailable(Exception):
    """One degraded finding-summary render; the message is the reason token
    printed after `FINDING_SUMMARY_UNAVAILABLE reason=`."""


def _summary_cell(value: Any) -> str:
    """Flatten one finding field into a single line both surfaces can carry.

    Every whitespace run (newline, tab, CR) collapses to one space, so a title
    can never occupy a line of its own; a run of colons collapses to one, so
    the `::` workflow-command prefix cannot survive at all; and the text is
    length-capped so one finding cannot flood the log.
    """
    flat = "" if value is None else " ".join(str(value).split())
    flat = _COLON_RUN.sub(":", flat)
    if len(flat) > FINDING_SUMMARY_MAX_TITLE_CHARS:
        flat = flat[: FINDING_SUMMARY_MAX_TITLE_CHARS - 1] + "…"
    return flat


def _markdown_cell(value: str) -> str:
    """Escape the one character that would break a markdown table row."""
    return value.replace("|", "\\|")


def _append_step_summary(summary_path: Optional[str], text: str) -> None:
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(text)


def _degrade_findings_summary(reason: str, summary_path: Optional[str]) -> None:
    """Announce one degraded render on both surfaces.

    The reason vocabulary is code-owned (`audit-missing`, `audit-unparseable`,
    `findings-field-missing`, `render-failed`), never audit content, so this
    line needs no scrub. An absent audit is NOT the same claim as "the
    reviewer found nothing" and must never be rendered as an empty table.
    """
    line = f"{FINDING_SUMMARY_UNAVAILABLE} reason={reason}"
    print(line)
    _append_step_summary(summary_path, f"## primary review findings\n\n{line}\n")


def _load_audit_findings(audit_path: Path) -> tuple[list[Any], str]:
    """(findings, verdict) from a canonical primary audit, or one degrade reason.

    `title` is read because gate-hub's contracts._project_finding_text_aliases
    copies it onto every finding whose `issue` is non-empty, so a real audit
    always carries it alongside the schema-required `issue`.
    """
    if not audit_path.is_file():
        raise _FindingsSummaryUnavailable("audit-missing")
    try:
        text = audit_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _FindingsSummaryUnavailable(f"audit-unreadable {type(exc).__name__}") from exc
    try:
        audit = json.loads(text)
    except ValueError as exc:
        # json.JSONDecodeError and UnicodeDecodeError are both ValueErrors:
        # bytes the producer never wrote, not a file this job cannot open.
        raise _FindingsSummaryUnavailable(f"audit-unparseable {type(exc).__name__}") from exc
    if not isinstance(audit, dict):
        raise _FindingsSummaryUnavailable("audit-unparseable root-not-object")
    result = audit.get("result")
    findings = result.get("findings") if isinstance(result, dict) else None
    if not isinstance(findings, list):
        raise _FindingsSummaryUnavailable("findings-field-missing")
    return findings, str(audit.get("verdict"))


def _finding_summary_rows(findings: list[Any]) -> list[dict[str, str]]:
    rows = []
    for finding in findings:
        line = finding.get("line")
        file_name = _summary_cell(finding.get("file"))
        # `line` is model output exactly like `title` is, so it gets the same
        # treatment before it is joined: a raw one would split the markdown row
        # on `|`, start a fresh log line on a newline, and a line-leading `::`
        # would be parsed as a workflow command — the exact shape this renderer
        # exists to keep out of the log.
        location = file_name if line is None else f"{file_name}:{_summary_cell(line)}"
        rows.append({
            "severity": _summary_cell(finding.get("severity")),
            "id": _summary_cell(finding.get("id")),
            "location": location,
            "title": _summary_cell(finding.get("title")),
        })
    return rows


def _primary_findings_panel_lines(primary_audit: Any) -> list[str]:
    """Project only bounded finding labels from the validated primary audit."""
    if not isinstance(primary_audit, dict):
        raise ValueError("primary findings panel audit is not an object")
    verdict = primary_audit.get("verdict")
    if verdict not in PRIMARY_VERDICT_DOMAIN:
        raise ValueError(f"primary findings panel audit verdict is invalid: {verdict!r}")
    if verdict != "fail":
        return []
    result = primary_audit.get("result")
    if not isinstance(result, dict):
        raise ValueError("primary findings panel audit result is not an object")
    findings = result.get("findings")
    if not isinstance(findings, list):
        raise ValueError("primary findings panel audit findings is not a list")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"primary findings panel finding {index} is not an object")
        for field in ("id", "severity", "file"):
            if not isinstance(finding.get(field), str) or not finding[field]:
                raise ValueError(f"primary findings panel finding {index} has invalid {field}")
        if "line" not in finding or (
            finding["line"] is not None and not _is_strict_int(finding["line"])
        ):
            raise ValueError(f"primary findings panel finding {index} has invalid line")
        if "title" in finding and not isinstance(finding["title"], str):
            raise ValueError(f"primary findings panel finding {index} has invalid title")
    if not findings:
        return []

    shown_rows = _finding_summary_rows(findings[:FINDING_SUMMARY_MAX_FINDINGS])
    overflow = len(findings) - len(shown_rows)
    lines = [
        "",
        f"#### {PRIMARY_FINDINGS_PANEL_MARKER} — 主审发现（{len(shown_rows)} / {len(findings)} 条）",
        "",
        "| severity | id | location | title |",
        "| --- | --- | --- | --- |",
    ]
    for row in shown_rows:
        lines.append("| " + " | ".join(_markdown_cell(row[key]) for key in _FINDING_SUMMARY_COLUMNS) + " |")
    if overflow:
        lines.extend(["", f"+{overflow} more"])
    return lines


def _render_findings_summary(audit_path: Path, summary_path: Optional[str]) -> None:
    """Print the finding summary to stdout and append it to the Step Summary."""
    try:
        findings, verdict = _load_audit_findings(audit_path)
        shown = _finding_summary_rows(findings[:FINDING_SUMMARY_MAX_FINDINGS])
        overflow = len(findings) - len(shown)
        lines = [
            f"{PRIMARY_FINDINGS_PANEL_MARKER}-SUMMARY verdict={verdict} findings={len(findings)} shown={len(shown)}"
        ]
        for row in shown:
            lines.append(
                "PRIMARY-FINDING severity={severity} id={id} at={location} title={title}".format(**row)
            )
        if overflow:
            lines.append(f"{PRIMARY_FINDINGS_PANEL_MARKER}-SUMMARY-OVERFLOW ... and {overflow} more")
        table = [
            f"## primary review findings (verdict {verdict}, {len(shown)} of {len(findings)} shown)",
            "",
            "| severity | id | location | title |",
            "| --- | --- | --- | --- |",
        ]
        for row in shown:
            table.append("| " + " | ".join(_markdown_cell(row[key]) for key in _FINDING_SUMMARY_COLUMNS) + " |")
        if overflow:
            table += ["", f"... and {overflow} more"]
        runtime_values = runtime_values_from_environment()
        print(scrub_for_publish("\n".join(lines) + "\n", runtime_values=runtime_values), end="")
        _append_step_summary(
            summary_path,
            scrub_for_publish("\n".join(table) + "\n", runtime_values=runtime_values),
        )
    except _FindingsSummaryUnavailable as exc:
        _degrade_findings_summary(str(exc), summary_path)


def _render_findings_summary_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-path", required=True)
    parser.add_argument("--summary-path", default=None, help="$GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)
    try:
        _render_findings_summary(Path(args.audit_path), args.summary_path)
    except Exception as exc:
        # Fail-open observability boundary (gate-hub#1294): this mode exists to
        # ADD readable evidence and must never change the primary job's result,
        # so anything the renderer did not already classify leaves one greppable
        # line behind and exits 0. The workflow step additionally guards its own
        # invocation with `|| echo ... reason=render-invocation-failed`.
        print(f"{FINDING_SUMMARY_UNAVAILABLE} reason=render-failed {type(exc).__name__}")
    return 0


def _read_audit_file(audit_dir: Optional[Path]) -> tuple[Any, Optional[str], Optional[bytes]]:
    return _GATE_IO._read_audit_file(audit_dir=audit_dir, _ports=_gate_io_ports())


def _write_convergence_receipt(path: str, receipt: Any) -> None:
    return _GATE_IO._write_convergence_receipt(path=path, receipt=receipt, _ports=_gate_io_ports())


# Human-readable, per-reason_code explanations rendered into the Step Summary
# (issues #32/#43): the caller must be able to tell "the primary reviewer
# rejected the change" apart from "the aggregator could not read the primary
# reviewer's conclusion". Phrasing is load-bearing — the "could not be read"
# entries must state that the cause is unknown, and unexpected_primary_skip
# must never read as the normal draft/fork skip.
REASON_CODE_EXPLANATIONS = {
    "disposition_resolved": (
        "The primary audit reported findings, but every current P1 is covered by an active, "
        "evidence-bound gate-disposition receipt for this exact head and audit."
    ),
    "primary_findings": (
        "The primary reviewer REJECTED this change — the specific findings are in the primary "
        "review result for this run (linked above); the Problems list below only mirrors the verdict."
    ),
    "audit_missing": (
        "The primary reviewer's conclusion could NOT be read for this run (no audit artifact was "
        "produced); the reason for the primary job result is unknown. This is not a reviewer rejection."
    ),
    "audit_invalid": (
        "The primary reviewer's conclusion could NOT be read for this run (the audit artifact failed "
        "validation); the reason for the primary job result is unknown. This is not a reviewer rejection."
    ),
    "audit_source_mismatch": (
        "The primary reviewer's conclusion could NOT be read for this run (the audit's source attempt "
        "did not match the selected artifact); the reason for the primary job result is unknown. "
        "This is not a reviewer rejection."
    ),
    "job_audit_mismatch": (
        "The primary reviewer's recorded conclusion contradicts the primary job's own status — "
        "the gate fails closed. Check the primary job logs."
    ),
    "unexpected_primary_skip": (
        "The primary review should have run but was skipped — this is NOT the normal draft/fork "
        "skip. Check the primary job configuration."
    ),
    "quality_infra": (
        "The quality job failed but its business checks show no failure (or the evidence was "
        "never recorded) — this is an infrastructure problem, not a code problem. Check the "
        "quality job's control-plane steps (checkout, cache, MagicDNS, ledger upload)."
    ),
}


def _action_sentence(
    outcome: Outcome, *, repository: Optional[str], identity: Optional[Identity],
    is_draft: Optional[bool], runner: Optional[str], primary_audit: Any = None,
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
        if outcome.reason_code == "disposition_resolved":
            return "All current P1 findings are covered by active evidence-bound disposition receipts; the gate is green."
        if outcome.recorded_disposition_claims:
            return (
                "A disposition receipt claim was recorded; the gate conclusion is unchanged and green."
            )
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
        if outcome.classification == "review_unavailable" and outcome.reason_code == "primary_unavailable":
            budget_action = _primary_budget_exhausted_action(primary_audit)
            if budget_action:
                return budget_action
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
    is_draft: Optional[bool] = None, runner: Optional[str] = None, primary_audit: Any = None,
    primary_result: Optional[str] = None,
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
        action = _action_sentence(
            outcome, repository=repository, identity=identity, is_draft=is_draft,
            runner=runner, primary_audit=primary_audit,
        )
        if action:
            lines.append(action)
        lines.append(
            f"Terminal state: classification=`{outcome.classification}`, "
            f"reason_code=`{outcome.reason_code}`, gate_result=`{outcome.gate_result}`"
        )
    if outcome.convergence_shadow is not None:
        shadow = outcome.convergence_shadow
        if shadow["history_status"] == "history_unavailable":
            lines.append("PR 累计主审轮（影子）：`history_unavailable`；未输出累计数，不改变 gate 结论。")
        else:
            lines.append(
                "PR 累计主审轮（影子）："
                f"`{shadow['eligible_rounds']}/{shadow['limit']}`，"
                f"decision=`{shadow['terminal_decision']}`；不改变 gate 结论。"
            )
    explanation = REASON_CODE_EXPLANATIONS.get(outcome.reason_code or "")
    if explanation:
        lines.append("")
        lines.append(explanation)
    if outcome.reason_code == "disposition_resolved":
        lines.extend([
            "",
            "当前主审 P1 已由绑定本轮审计的有效处置回执逐条覆盖；回执只覆盖列出的 finding。",
        ])
    lines.append("")
    if outcome.notes:
        lines.append("Accepted:")
        for note in outcome.notes:
            lines.append(f"- {note}")
        lines.append("")
    if outcome.recorded_disposition_claims:
        lines.append("Disposition receipts consumed:")
        for line in outcome.recorded_disposition_claims:
            lines.append(f"- {line}")
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
            '`"verdict": null` below means no canonical primary review conclusion was available, '
            "and the cause cannot be determined from the available audit data. "
            "This is NOT a reviewer rejection."
        )
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(outcome.synthetic_audit, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def _primary_budget_exhausted_action(primary_audit: Any) -> Optional[str]:
    """Return the split-PR action only for an entirely budget-exhausted chain."""
    if not isinstance(primary_audit, dict) or primary_audit.get("verdict") != "unavailable":
        return None
    attempts = primary_audit.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return None
    if not all(
        isinstance(attempt, dict)
        and _is_strict_int(attempt.get("exit_code"))
        and attempt["exit_code"] == PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE
        and attempt.get("reason") == PRIMARY_BUDGET_EXHAUSTED_REASON
        for attempt in attempts
    ):
        return None

    return "本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。"


def _panel_action(row: dict[str, Any]) -> str:
    """Return the recipient-facing action for one validated panel row."""
    gate_result = row["gate_result"]
    if gate_result == "unavailable":
        if row.get("classification") == "review_unavailable" and row.get("reason_code") == "primary_unavailable":
            budget_action = _primary_budget_exhausted_action(
                row.get("primary_audit")
            )
            if budget_action:
                return budget_action
    action = PANEL_BUCKET_BY_GATE_RESULT[gate_result]
    if gate_result == "skipped":
        return f"{action}（主审未跑，绿≠过审）"
    return action


def _history_reason_category(reason: str) -> str:
    http_match = re.search(r"\bHTTP\s+Error\s+(\d{3})\b", reason)
    if http_match:
        return f"制品下载失败：HTTP {http_match.group(1)}"
    if reason.startswith("expired artifact "):
        return "过期制品"
    if reason.startswith("artifact ") and " has no archive URL" in reason:
        return "制品缺少 archive URL"
    if reason.startswith("artifact history does not contain cached rows:"):
        return "制品历史缺少面板缓存行"
    if reason.startswith("artifact ") and ": " in reason:
        detail = reason.split(": ", 1)[1]
        return f"制品处理失败：{detail.split(':', 1)[0]}"
    if reason.startswith("bounded_scan:"):
        return "有界历史扫描达到上限"
    if reason.startswith("history budget exhausted"):
        return "历史重建预算耗尽"
    if reason.startswith("no terminal artifact matched"):
        return "未找到终态制品"
    return reason


def _summarize_history_reasons(reasons: list[str]) -> str:
    counts: dict[str, int] = {}
    for reason in reasons:
        category = _history_reason_category(reason)
        counts[category] = counts.get(category, 0) + 1
    return "；".join(f"{count} 个{category}" for category, count in counts.items())


def _bounded_history_warning(*, history_warning: Optional[str], history_reasons: Optional[list[str]]) -> Optional[str]:
    warning = _summarize_history_reasons(history_reasons) if history_reasons is not None else history_warning
    if not warning:
        return None
    prefix = "> 历史可能不完整："
    suffix = "…完整明细见 delivery 诊断制品"
    line = prefix + warning
    if len(line) <= MAX_HISTORY_WARNING_CHARS:
        return line
    available = MAX_HISTORY_WARNING_CHARS - len(prefix) - len(suffix)
    return prefix + warning[:available].rstrip("；") + suffix


def render_status_panel(
    rows: list[dict[str, Any]], *, history_warning: Optional[str] = None,
    history_reasons: Optional[list[str]] = None,
) -> str:
    """Render the public sticky panel from rows only.

    The renderer is deliberately a pure projection: it does not read a prior
    comment, infer history from rendered Markdown, or mutate its input. Rows
    are sorted by their durable run identity so a rerun produces the same
    body for the same input set.
    """
    ordered = sorted(rows, key=lambda row: (row["run_id"], row["run_attempt"]))
    if not ordered:
        raise ValueError("status panel requires at least one terminal row")
    current = ordered[-1]
    current_result = current["gate_result"]
    lines = [
        PANEL_MARKER,
        "",
        "### Required Gate v2 — 状态面板",
        "",
        f"当前状态：**{current_result}** · **{_panel_action(current)}**",
    ]
    if current_result == "skipped":
        lines.extend(["", "> 主审未跑，绿≠过审。draft / fork / hosted 的跳过不代表真实通过。"])
    lines.extend([
        "",
        f"当前裁决：`{current['classification']}` / `{current['reason_code']}`",
    ])
    if current_result == "fail" and "primary_audit" in current:
        lines.extend(_primary_findings_panel_lines(current["primary_audit"]))
    current_shadow = current.get("convergence_shadow")
    if isinstance(current_shadow, dict):
        if current_shadow["history_status"] == "history_unavailable":
            shadow_text = "history_unavailable（未估算累计轮数）"
        else:
            shadow_text = (
                f"{current_shadow['eligible_rounds']}/{current_shadow['limit']} · "
                f"{current_shadow['terminal_decision']}"
            )
        lines.extend(["", f"PR 累计主审轮（影子）：`{shadow_text}`"])
    warning_line = _bounded_history_warning(history_warning=history_warning, history_reasons=history_reasons)
    if warning_line:
        lines.extend(["", warning_line])
    recorded_claims = [
        line for line in (current.get("recorded_disposition_claims") or [])
        if isinstance(line, str) and line
    ]
    if recorded_claims:
        lines.extend(["", "Disposition receipts consumed:"])
        for line in recorded_claims:
            lines.append(f"- {line}")
    resolved = [
        line for line in (current.get("resolved_findings") or [])
        if isinstance(line, str) and line and "resolved by receipt" not in line
    ]
    if resolved:
        lines.extend(["", "Resolved:"])
        for line in resolved:
            lines.append(f"- {line}")
    relation = current.get("finding_relation")
    conflict_items = []
    if isinstance(relation, dict):
        conflict_items = [
            item for item in (relation.get("items") or [])
            if isinstance(item, dict) and item.get("relation_to_previous") == "conflict"
        ]
    if conflict_items:
        lines.extend(["", "#### 跨轮冲突（人工裁决 manual_required）", ""])
        for item in conflict_items:
            lines.append(
                f"- `{item.get('id')}` 与上一轮 `{item.get('previous_finding_id')}` "
                f"在 `{item.get('file')}:{item.get('line')}` 冲突"
            )
    carried = (relation.get("carried_findings") or []) if isinstance(relation, dict) else []
    if current_result == "fail" and carried:
        lines.extend(["", "#### 上一轮未处置的 sol 问题（已并入本轮主审）", ""])
        for item in carried:
            lines.append(f"- `{item.get('id')}` ← `{item.get('carried_from_head_sha')}`（{item.get('evidence_reason')}）")
        lines.append("出口：改代码，或对本轮该条签发 rejected。")
    lines.extend([
        "",
        "#### Gate 历史（v1；来源为持久化 `gate_terminal` 制品）",
        "",
        "| Run | Attempt | Head | 状态 | PR 累计主审轮（影子） | 收件人动作 |",
        "| ---: | ---: | :--- | :--- | :--- | :--- |",
    ])
    for row in ordered:
        short_sha = row["head_sha"][:7]
        run_link = f"[{row['run_id']}](https://github.com/{row['repository']}/actions/runs/{row['run_id']})"
        shadow = row.get("convergence_shadow")
        if not isinstance(shadow, dict):
            shadow_text = "—"
        elif shadow["history_status"] == "history_unavailable":
            shadow_text = "history_unavailable"
        else:
            shadow_text = f"{shadow['eligible_rounds']}/{shadow['limit']} · {shadow['terminal_decision']}"
        lines.append(
            f"| {run_link} | {row['run_attempt']} | `{short_sha}` | "
            f"`{row['gate_result']}` | `{shadow_text}` | {_panel_action(row)} |"
        )
    lines.extend([
        "",
        "历史行按 `run_id` + `run_attempt` 去重并只增不删；删除本评论后可由 `gate_terminal` 制品重建。",
        "",
    ])
    return "\n".join(lines)


# The status panel is the only aggregate PR comment. It is a marker-located
# projection of durable gate-terminal artifacts; its body is never treated as
# the history database. PATCH updates do not notify GitHub subscribers.


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _github_request(*, token: str, url: str, method: str = "GET", payload: Optional[dict[str, Any]] = None) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "gate-aggregator",
        },
        method=method,
    )
    budget = _ACTIVE_PUBLISH_BUDGET.get()
    timeout = budget.timeout() if budget is not None else GITHUB_API_TIMEOUT_SECONDS
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (socket.timeout, TimeoutError) as exc:
        if budget is not None:
            try:
                budget.timeout()
            except _PublishBudgetExhausted as budget_exc:
                raise budget_exc from exc
        raise
    if budget is not None:
        budget.timeout()
    return raw


def _download_terminal_zip(*, token: str, url: str) -> bytes:
    return _GATE_IO._download_terminal_zip(token=token, url=url, _ports=_gate_io_ports())


def _github_json(*, token: str, url: str) -> Any:
    return _GATE_IO._github_json(token=token, url=url, _ports=_gate_io_ports())


def _github_identity(token: str) -> dict[str, Any]:
    return _GATE_IO._github_identity(token=token, _ports=_gate_io_ports())


def _fetch_panel_comments(*, token: str, repository: str, pr_number: int) -> list[dict[str, Any]]:
    return _GATE_IO._fetch_panel_comments(token=token, repository=repository, pr_number=pr_number, _ports=_gate_io_ports())


def _post_issue_comment(*, repository: str, pr_number: int, body: str, token: str) -> None:
    return _GATE_IO._post_issue_comment(repository=repository, pr_number=pr_number, body=body, token=token, _ports=_gate_io_ports())


def _patch_issue_comment(*, repository: str, comment_id: int, body: str, token: str) -> None:
    return _GATE_IO._patch_issue_comment(repository=repository, comment_id=comment_id, body=body, token=token, _ports=_gate_io_ports())


def _delete_issue_comment(*, repository: str, comment_id: int, token: str) -> None:
    return _GATE_IO._delete_issue_comment(repository=repository, comment_id=comment_id, token=token, _ports=_gate_io_ports())


def _terminal_row(record: Any, *, repository: str, repository_id: int, pr_number: int) -> dict[str, Any]:
    return _TERMINAL_HISTORY._terminal_row(record=record, repository=repository, repository_id=repository_id, pr_number=pr_number, _ports=_terminal_history_ports())


def _compute_pr_primary_convergence(
    history: HistoryLoad, *, identity: Identity, primary_result: str, outcome: Outcome,
) -> tuple[Optional[int], Optional[bool]]:
    return _TERMINAL_HISTORY._compute_pr_primary_convergence(history=history, identity=identity, primary_result=primary_result, outcome=outcome, _ports=_terminal_history_ports())


def _read_terminal_zip(raw: bytes) -> dict[str, Any]:
    return _TERMINAL_HISTORY._read_terminal_zip(raw=raw, _ports=_terminal_history_ports())


def _consume_terminal_artifact(
    result: HistoryLoad, *, artifact: dict[str, Any], token: str, repository: str,
    repository_id: int, pr_number: int,
) -> bool:
    return _TERMINAL_HISTORY._consume_terminal_artifact(result=result, artifact=artifact, token=token, repository=repository, repository_id=repository_id, pr_number=pr_number, _ports=_terminal_history_ports())


def _fetch_github_terminal_history(
    *, token: str, repository: str, repository_id: int, pr_number: int,
    target_run_ids: Optional[list[int]] = None,
) -> HistoryLoad:
    return _TERMINAL_HISTORY._fetch_github_terminal_history(token=token, repository=repository, repository_id=repository_id, pr_number=pr_number, target_run_ids=target_run_ids, _ports=_terminal_history_ports())


def _read_disposition_zip(raw: bytes) -> dict[str, Any]:
    return _GATE_IO._read_disposition_zip(raw=raw, _ports=_gate_io_ports())


def _fetch_github_disposition_receipts(
    *, token: str, repository: str, repository_id: int, pr_number: int,
) -> tuple[Any, ...]:
    return _GATE_IO._fetch_github_disposition_receipts(token=token, repository=repository, repository_id=repository_id, pr_number=pr_number, _ports=_gate_io_ports())


def _silo_configured() -> bool:
    return _GATE_IO._silo_configured(_ports=_gate_io_ports())


def _silo_cli(argv: list[str], timeout_s: float = SILO_CLI_CALL_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    # Silo 客户端是纯标准库（scripts/silo_store.py 自签 SigV4）：直接用当前
    # 解释器执行，禁止经由 uv/pip 在运行期取包。SILO_STORE 允许覆盖是为了
    # 让测试打桩与 job 稀疏检出能指向同一份仓内脚本，不接受仓外路径以外的
    # 任何包管理器入口。仓内调用点必须显式传 timeout_s（deadline 剩余或
    # SILO_CLI_CALL_TIMEOUT_SECONDS）；默认值只服务于仓外无超时语义的探针
    # （tests/test_no_runtime_install.py），且本身就是有界的。
    return _GATE_IO._silo_cli(argv=argv, timeout_s=timeout_s, _ports=_gate_io_ports())


def _silo_objects_under(prefix: str, timeout_s: float) -> list[tuple[str, bytes]]:
    return _GATE_IO._silo_objects_under(prefix=prefix, timeout_s=timeout_s, _ports=_gate_io_ports())


def _terminal_artifact_run_id(name: str, repository_id: int) -> Optional[int]:
    return _TERMINAL_HISTORY._terminal_artifact_run_id(name=name, repository_id=repository_id, _ports=_terminal_history_ports())


def _silo_artifact_name(key: str) -> str:
    return _TERMINAL_HISTORY._silo_artifact_name(key=key, _ports=_terminal_history_ports())


def _consume_silo_terminal_json(
    result: HistoryLoad, *, name: str, raw: bytes, repository: str,
    repository_id: int, pr_number: int,
) -> None:
    return _TERMINAL_HISTORY._consume_silo_terminal_json(result=result, name=name, raw=raw, repository=repository, repository_id=repository_id, pr_number=pr_number, _ports=_terminal_history_ports())


def _fetch_silo_terminal_history(
    *, repository: str, repository_id: int, pr_number: int,
    target_run_ids: Optional[list[int]] = None,
) -> HistoryLoad:
    return _TERMINAL_HISTORY._fetch_silo_terminal_history(repository=repository, repository_id=repository_id, pr_number=pr_number, target_run_ids=target_run_ids, _ports=_terminal_history_ports())


def _pr_target_run_ids(*, token: str, repository: str, pr_number: int) -> Optional[list[int]]:
    return _TERMINAL_HISTORY._pr_target_run_ids(token=token, repository=repository, pr_number=pr_number, _ports=_terminal_history_ports())


def _load_pr_convergence_history(
    *, repository: str, repository_id: int, pr_number: int,
) -> tuple[str, tuple[int, ...], HistoryLoad]:
    return _TERMINAL_HISTORY._load_pr_convergence_history(repository=repository, repository_id=repository_id, pr_number=pr_number, _ports=_terminal_history_ports())


def _terminal_history_cache_path(terminal_path: str | Path) -> Path:
    return _TERMINAL_HISTORY._terminal_history_cache_path(terminal_path=terminal_path, _ports=_terminal_history_ports())


def _write_terminal_history_cache(terminal_path: str | Path, history: HistoryLoad) -> None:
    return _TERMINAL_HISTORY._write_terminal_history_cache(terminal_path=terminal_path, history=history, _ports=_terminal_history_ports())


def _read_terminal_history_cache(path: str | Path) -> HistoryLoad:
    return _TERMINAL_HISTORY._read_terminal_history_cache(path=path, _ports=_terminal_history_ports())


def _merge_history_loads(github: HistoryLoad, silo: HistoryLoad) -> HistoryLoad:
    return _TERMINAL_HISTORY._merge_history_loads(github=github, silo=silo, _ports=_terminal_history_ports())


def _fetch_terminal_history(
    *, token: str, repository: str, repository_id: int, pr_number: int,
    target_run_ids: Optional[list[int]] = None,
    silo_history: Optional[HistoryLoad] = None,
) -> HistoryLoad:
    return _TERMINAL_HISTORY._fetch_terminal_history(token=token, repository=repository, repository_id=repository_id, pr_number=pr_number, target_run_ids=target_run_ids, silo_history=silo_history, _ports=_terminal_history_ports())


def _fetch_silo_disposition_receipts(*, repository_id: int, pr_number: int) -> tuple[Any, ...]:
    return _GATE_IO._fetch_silo_disposition_receipts(repository_id=repository_id, pr_number=pr_number, _ports=_gate_io_ports())


def _receipt_merge_key(receipt: Any) -> str:
    return _GATE_IO._receipt_merge_key(receipt=receipt, _ports=_gate_io_ports())


def _merge_disposition_receipts(github: tuple[Any, ...], silo: tuple[Any, ...]) -> tuple[Any, ...]:
    return _GATE_IO._merge_disposition_receipts(github=github, silo=silo, _ports=_gate_io_ports())


def _fetch_disposition_receipts(
    *, token: str, repository: str, repository_id: int, pr_number: int,
) -> tuple[Any, ...]:
    return _GATE_IO._fetch_disposition_receipts(token=token, repository=repository, repository_id=repository_id, pr_number=pr_number, _ports=_gate_io_ports())


def _merge_panel_rows(current: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_identity = {(row["run_id"], row["run_attempt"]): row for row in history}
    by_identity[(current["run_id"], current["run_attempt"])] = current
    return list(by_identity.values())


def _find_panel_comments(comments: Any, owner: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(comments, list):
        raise ValueError("PR comments response has an invalid shape")
    owner_id = owner["id"]
    owner_login = owner["login"]
    selected = []
    for comment in comments:
        if not isinstance(comment, dict) or PANEL_MARKER not in comment.get("body", ""):
            continue
        user = comment.get("user")
        if not isinstance(user, dict):
            continue
        if user.get("id") is not None:
            is_owner = user["id"] == owner_id
        else:
            is_owner = user.get("login") == owner_login
        if is_owner:
            selected.append(comment)
    return sorted(selected, key=lambda comment: (str(comment.get("created_at", "")), int(comment.get("id", 0))))


def _find_panel_comment(comments: Any) -> Optional[dict[str, Any]]:
    """Compatibility wrapper retained for callers that only need a marker check."""
    if not isinstance(comments, list):
        raise ValueError("PR comments response has an invalid shape")
    return next((comment for comment in comments if isinstance(comment, dict) and PANEL_MARKER in comment.get("body", "")), None)


def _parse_panel_history(body: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("| ["):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 5:
            continue
        run_match = re.match(r"\[(\d+)\]\(https://github\.com/([^/]+/[^/]+)/actions/runs/\d+\)", parts[0])
        attempt_match = re.fullmatch(r"(\d+)", parts[1])
        head_match = re.fullmatch(r"`([^`]+)`", parts[2])
        result_match = re.fullmatch(r"`([^`]+)`", parts[3])
        if not (run_match and attempt_match and head_match and result_match):
            continue
        gate_result = result_match.group(1)
        if gate_result not in GATE_RESULT_DOMAIN:
            continue
        rows.append({
            "schema_version": PANEL_HISTORY_ROW_SCHEMA_VERSION,
            "repository": run_match.group(2),
            "run_id": int(run_match.group(1)),
            "run_attempt": int(attempt_match.group(1)),
            "head_sha": head_match.group(1),
            "gate_result": gate_result,
            "classification": "panel_cache",
            "reason_code": "panel_cache",
        })
    return rows


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


def _build_panel_delivery(
    *, body: str, repository: Optional[str], pr_number: Optional[int], identity: Optional[Identity],
    delivery: str, reason_code: str, error_category: Optional[str] = None,
    http_status: Optional[int] = None, history_error: Optional[str] = None,
    operation: Optional[str] = None, attempts: Optional[int] = None,
    exception_type: Optional[str] = None, history_skipped_records: Optional[list[dict[str, str]]] = None,
    history_incomplete_reasons: Optional[list[str]] = None, self_heal_errors: Optional[list[str]] = None,
    identity_source: Optional[str] = None, completed_operations: Optional[list[str]] = None,
    pending_operations: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build durable evidence for panel delivery and reconstruction failures."""
    return {
        "schema_version": PANEL_DELIVERY_SCHEMA_VERSION,
        "kind": PANEL_DELIVERY_KIND,
        "repository": repository or "",
        "repository_id": identity.repository_id if identity else None,
        "pr_number": pr_number,
        "run_id": identity.run_id if identity else None,
        "run_attempt": identity.run_attempt if identity else None,
        "head_sha": identity.head_sha if identity else None,
        "comment_expected": delivery != "not_enabled",
        "comment_created": True if delivery == "created" else False if delivery in ("updated", "not_created") else None,
        "delivery": delivery,
        "reason_code": reason_code,
        "error_category": error_category,
        "http_status": http_status,
        "history_error": history_error,
        "history_skipped_records": history_skipped_records or [],
        "history_skipped_count": len(history_skipped_records or []),
        "history_incomplete_reasons": history_incomplete_reasons or [],
        "history_incomplete": bool(history_incomplete_reasons),
        "self_heal_errors": self_heal_errors or [],
        "operation": operation,
        "attempts": attempts,
        "exception_type": exception_type,
        "identity_source": identity_source,
        "completed_operations": completed_operations or [],
        "pending_operations": pending_operations or [],
        "comment_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def _panel_failure(exc: BaseException) -> tuple[str, str, Optional[int], str]:
    raw_reason = getattr(exc, "reason", "")
    if isinstance(raw_reason, BaseException):
        raw_reason = type(raw_reason).__name__
    if not isinstance(raw_reason, str):
        raw_reason = ""
    raw_reason = re.sub(r"https?://[^\s?#]+\?[^\s#]*", "<URL>", raw_reason)
    raw_reason = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+", r"\1[REDACTED]", raw_reason)
    raw_reason = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", raw_reason)
    raw_reason = re.sub(r"(?i)([?&][^=\s#]+)=([^&\s#]*)", r"\1=[REDACTED]", raw_reason)
    safe_reason = scrub_for_publish(raw_reason, runtime_values=runtime_values_from_environment())[:200]
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403 or exc.code == 429:
            return f"http_{exc.code}", "permission_or_rate_limit", exc.code, safe_reason
        if exc.code >= 500:
            return "http_5xx", "server_error", exc.code, safe_reason
        return "http_error", "http_error", exc.code, safe_reason
    return "network_indeterminate", "network_error", None, safe_reason


def _panel_warning(
    *, phase: str, exc: BaseException, reason_code: str, category: str,
    http_status: Optional[int], operation: Optional[str] = None,
    attempts: int = 1, safe_reason: str = "",
) -> None:
    status = str(http_status) if http_status is not None else "unavailable"
    _warn(
        f"::warning::gate status panel {phase} failed — HTTP status={status}; "
        f"permission category={category}; reason={reason_code}; "
        "gate verdict is unchanged and Step Summary remains authoritative"
    )
    if operation:
        _warn(
            f"::warning::PANEL_PUBLISH_FAILED operation={operation} attempts={attempts} "
            f"exc_type={type(exc).__name__} reason={safe_reason[:200]}"
        )


def _bounded_panel_request(
    request: Any, *, before_retry: Optional[Any] = None,
) -> tuple[Any, int, bool, Optional[Exception]]:
    """Retry only the idempotent panel GET/PATCH operations, at most three times."""
    attempts = 0
    last_error: Optional[Exception] = None
    while attempts < 3:
        attempts += 1
        try:
            return request(), attempts, False, None
        except Exception as exc:
            last_error = exc
            retryable = (
                isinstance(exc, urllib.error.HTTPError) and exc.code >= 500
            ) or (
                not isinstance(exc, urllib.error.HTTPError)
                and isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError))
            )
            if not retryable or attempts >= 3:
                return None, attempts, False, exc
        if before_retry is not None:
            try:
                if before_retry():
                    return None, attempts, True, None
            except Exception as exc:
                last_error = exc
                attempts += 1
                retryable = (
                    isinstance(exc, urllib.error.HTTPError) and exc.code >= 500
                ) or (
                    not isinstance(exc, urllib.error.HTTPError)
                    and isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError))
                )
                if not retryable or attempts >= 3:
                    return None, attempts, False, exc
        time.sleep(2)
    return None, attempts, False, last_error


def _publish_panel_patch(
    *, repository: str, comment_id: int, body: str, token: str, run_id: int, run_attempt: int,
) -> tuple[int, bool, Optional[Exception]]:
    def already_written() -> bool:
        comment = _github_json(
            token=token,
            url=f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}",
        )
        if not isinstance(comment, dict) or not isinstance(comment.get("body"), str):
            raise ValueError("issue comment readback has an invalid shape")
        return any(
            row["run_id"] == run_id and row["run_attempt"] == run_attempt
            for row in _parse_panel_history(comment["body"])
        )

    _, attempts, recovered, error = _bounded_panel_request(
        lambda: _patch_issue_comment(
            repository=repository, comment_id=comment_id, body=body, token=token,
        ),
        before_retry=already_written,
    )
    return attempts, recovered, error


def _post_status_panel_fail_open_with_budget(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity], budget: _PublishBudget,
    silo_history: Optional[HistoryLoad] = None,
) -> tuple[str, dict[str, Any]]:
    """Publish the one marker-located status panel without affecting verdict."""
    body = scrub_for_publish(
        render_status_panel([current]),
        runtime_values=runtime_values_from_environment(),
    )
    identity_source: Optional[str] = None
    failure_operation: Optional[str] = None
    failure_attempts = 1
    comment_was_created = False
    try:
        if not repository or pr_number is None:
            reason_code, category, status = "missing_target", "configuration", None
            _panel_warning(phase="target resolution", exc=ValueError("missing target"), reason_code=reason_code, category=category, http_status=status)
            return body, _build_panel_delivery(body=body, repository=repository, pr_number=pr_number, identity=identity, delivery="not_created", reason_code=reason_code, error_category=category, http_status=status)
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            reason_code, category, status = "missing_token", "configuration", None
            _panel_warning(phase="authentication", exc=ValueError("missing token"), reason_code=reason_code, category=category, http_status=status)
            return body, _build_panel_delivery(body=body, repository=repository, pr_number=pr_number, identity=identity, delivery="not_created", reason_code=reason_code, error_category=category, http_status=status)

        try:
            budget.begin("IDENTITY")
            owner = _github_identity(token)
            budget.complete("IDENTITY")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            reason_code, category, status, _ = _panel_failure(exc)
            _panel_warning(phase="identity", exc=exc, reason_code=reason_code, category=category, http_status=status)
            return body, _build_panel_delivery(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code=reason_code, error_category=category,
                http_status=status, operation="IDENTITY",
                completed_operations=budget.completed_operations, pending_operations=budget.pending_operations,
            )
        identity_source = owner.get("identity_source", "user_api")
        budget.begin("COMMENT_LOOKUP")
        failure_operation = "comment_lookup"
        comments, failure_attempts, _, lookup_error = _bounded_panel_request(
            lambda: _fetch_panel_comments(token=token, repository=repository, pr_number=pr_number),
        )
        if lookup_error is not None:
            raise lookup_error
        budget.complete("COMMENT_LOOKUP")
        failure_operation = None
        failure_attempts = 1
        own_panels = _find_panel_comments(comments, owner)
        existing = own_panels[0] if own_panels else None
        cached_rows = _parse_panel_history(existing.get("body", "")) if existing else []
        target_run_ids = [row["run_id"] for row in cached_rows] if cached_rows else None
        try:
            budget.begin("HISTORY_RECONSTRUCTION")
            history = _fetch_terminal_history(
                token=token, repository=repository, repository_id=repository_id or 0,
                pr_number=pr_number, target_run_ids=target_run_ids,
                silo_history=silo_history,
            )
            budget.complete("HISTORY_RECONSTRUCTION")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            reason_code, category, status, safe_reason = _panel_failure(exc)
            _panel_warning(
                phase="history reconstruction", exc=exc, reason_code=reason_code,
                category=category, http_status=status, operation="history_load",
                attempts=1, safe_reason=safe_reason,
            )
            cached_body = existing.get("body", body) if existing else body
            return cached_body, _build_panel_delivery(
                body=cached_body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="not_created", reason_code="history_unavailable", error_category=category,
                http_status=status, history_error=f"{type(exc).__name__}: {safe_reason}",
                operation="history_load", attempts=1, exception_type=type(exc).__name__,
                identity_source=identity_source, completed_operations=budget.completed_operations,
                pending_operations=budget.pending_operations,
            )
        if isinstance(history, list):
            history = HistoryLoad(rows=history)
        incomplete_reasons = list(history.incomplete_reasons)
        artifact_keys = {(row["run_id"], row["run_attempt"]) for row in history.rows}
        cache_only = [row for row in cached_rows if (row["run_id"], row["run_attempt"]) not in artifact_keys]
        if cache_only:
            incomplete_reasons.append(
                "artifact history does not contain cached rows: "
                + ", ".join(f"{row['run_id']}/{row['run_attempt']}" for row in cache_only)
            )
        body = scrub_for_publish(
            render_status_panel(
                _merge_panel_rows(current, [*history.rows, *cached_rows]),
                history_reasons=incomplete_reasons if incomplete_reasons else None,
            ),
            runtime_values=runtime_values_from_environment(),
        )
        self_heal_errors: list[str] = []
        if existing:
            budget.discard("POST_VERIFY")
            budget.begin("COMMENT_PUBLISH")
            failure_operation = "comment_patch"
            failure_attempts, _, patch_error = _publish_panel_patch(
                repository=repository, comment_id=int(existing["id"]), body=body, token=token,
                run_id=current["run_id"], run_attempt=current["run_attempt"],
            )
            if patch_error is not None:
                raise patch_error
            budget.complete("COMMENT_PUBLISH")
            failure_operation = None
            for duplicate in own_panels[1:]:
                budget.add("SELF_HEAL")
                budget.begin("SELF_HEAL")
                try:
                    _delete_issue_comment(repository=repository, comment_id=int(duplicate["id"]), token=token)
                    budget.complete("SELF_HEAL")
                except _PublishBudgetExhausted:
                    raise
                except Exception as exc:
                    self_heal_errors.append(f"comment {duplicate.get('id')}: {type(exc).__name__}: {exc}")
            if not own_panels[1:]:
                budget.discard("SELF_HEAL")
            return body, _build_panel_delivery(
                body=body, repository=repository, pr_number=pr_number, identity=identity,
                delivery="updated", reason_code="history_incomplete" if incomplete_reasons else "patched",
                operation="PATCH", history_skipped_records=history.skipped_records,
                history_incomplete_reasons=incomplete_reasons, self_heal_errors=self_heal_errors,
                identity_source=identity_source, completed_operations=budget.completed_operations,
                pending_operations=budget.pending_operations,
            )
        budget.begin("COMMENT_PUBLISH")
        failure_operation = "comment_create"
        _post_issue_comment(repository=repository, pr_number=pr_number, body=body, token=token)
        budget.complete("COMMENT_PUBLISH")
        comment_was_created = True
        failure_operation = None
        budget.begin("POST_VERIFY")
        try:
            after_post = _find_panel_comments(
                _fetch_panel_comments(token=token, repository=repository, pr_number=pr_number), owner,
            )
            budget.complete("POST_VERIFY")
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            self_heal_errors.append(f"post verification: {type(exc).__name__}: {exc}")
            after_post = []
            budget.discard("POST_VERIFY")
        if after_post:
            winner = after_post[0]
            if winner.get("body") != body or len(after_post) > 1:
                budget.add("SELF_HEAL")
                budget.begin("SELF_HEAL")
                try:
                    failure_operation = "comment_patch"
                    failure_attempts, _, patch_error = _publish_panel_patch(
                        repository=repository, comment_id=int(winner["id"]), body=body, token=token,
                        run_id=current["run_id"], run_attempt=current["run_attempt"],
                    )
                    if patch_error is not None:
                        raise patch_error
                    failure_operation = None
                    for duplicate in after_post[1:]:
                        _delete_issue_comment(repository=repository, comment_id=int(duplicate["id"]), token=token)
                    budget.complete("SELF_HEAL")
                except _PublishBudgetExhausted:
                    raise
                except Exception as exc:
                    if failure_operation == "comment_patch":
                        raise
                    self_heal_errors.append(f"post self-heal: {type(exc).__name__}: {exc}")
            else:
                budget.discard("SELF_HEAL")
        else:
            budget.discard("SELF_HEAL")
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="created", reason_code="post_self_heal_partial" if self_heal_errors else "posted",
            operation="POST", history_skipped_records=history.skipped_records,
            history_incomplete_reasons=incomplete_reasons, self_heal_errors=self_heal_errors,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )
    except _PublishBudgetExhausted as exc:
        delivery = "unknown" if failure_operation in ("comment_patch", "comment_create") else "not_created"
        _, _, _, safe_reason = _panel_failure(exc)
        _panel_warning(
            phase="publish budget", exc=exc, reason_code="publish_budget_exhausted",
            category="network_error", http_status=None, operation=failure_operation,
            attempts=failure_attempts, safe_reason=safe_reason,
        )
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery=delivery, reason_code="publish_budget_exhausted", error_category="network_error",
            operation=failure_operation or exc.operation, attempts=failure_attempts,
            exception_type=type(exc).__name__, identity_source=identity_source,
            completed_operations=budget.completed_operations, pending_operations=budget.pending_operations,
        )
    except urllib.error.HTTPError as exc:
        reason_code, category, status, safe_reason = _panel_failure(exc)
        _panel_warning(
            phase="comment publish", exc=exc, reason_code=reason_code,
            category=category, http_status=status, operation=failure_operation,
            attempts=failure_attempts, safe_reason=safe_reason,
        )
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery=(
                "unknown" if comment_was_created and failure_operation == "comment_patch"
                else "not_created" if status is not None and status < 500 else "unknown"
            ),
            reason_code=reason_code, error_category=category, http_status=status,
            operation=failure_operation, attempts=failure_attempts,
            exception_type=type(exc).__name__,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )
    except Exception as exc:
        reason_code, category, status, safe_reason = _panel_failure(exc)
        _panel_warning(
            phase="comment publish", exc=exc, reason_code=reason_code,
            category=category, http_status=status, operation=failure_operation,
            attempts=failure_attempts, safe_reason=safe_reason,
        )
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="unknown", reason_code=reason_code, error_category=category, http_status=status,
            operation=failure_operation, attempts=failure_attempts,
            exception_type=type(exc).__name__,
            identity_source=identity_source, completed_operations=budget.completed_operations,
            pending_operations=budget.pending_operations,
        )


def _post_status_panel_fail_open(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity],
    silo_history: Optional[HistoryLoad] = None,
) -> tuple[str, dict[str, Any]]:
    body = render_status_panel([current])
    try:
        budget = _PublishBudget.from_environment()
    except ValueError as exc:
        _panel_warning(
            phase="publish budget", exc=exc, reason_code="invalid_publish_budget",
            category="configuration", http_status=None,
        )
        return body, _build_panel_delivery(
            body=body, repository=repository, pr_number=pr_number, identity=identity,
            delivery="not_created", reason_code="invalid_publish_budget", error_category="configuration",
        )
    token = _ACTIVE_PUBLISH_BUDGET.set(budget)
    try:
        return _post_status_panel_fail_open_with_budget(
            current=current, repository=repository, repository_id=repository_id,
            pr_number=pr_number, identity=identity, budget=budget,
            silo_history=silo_history,
        )
    finally:
        _ACTIVE_PUBLISH_BUDGET.reset(token)


def _write_panel_delivery(path: str, receipt: dict[str, Any]) -> None:
    """Publish the receipt atomically so artifact upload never sees partial JSON."""
    target = Path(path)
    temporary_path = target.with_name(f".{target.name}.tmp")
    temporary_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(target)


def _panel_current_row(
    *, outcome: Outcome, repository: Optional[str], identity: Optional[Identity],
) -> dict[str, Any]:
    gate_result = outcome.gate_result if outcome.gate_result in GATE_RESULT_DOMAIN else "unavailable"
    classification = outcome.classification if outcome.classification in TERMINAL_CLASSIFICATION_DOMAIN else "integration_error"
    reason_code = outcome.reason_code if outcome.reason_code in TERMINAL_REASON_DOMAIN else "audit_invalid"
    row = {
        "schema_version": PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": repository or "",
        "run_id": identity.run_id if identity else 0,
        "run_attempt": identity.run_attempt if identity else 0,
        "head_sha": identity.head_sha if identity else "unknown",
        "gate_result": gate_result,
        "classification": classification,
        "reason_code": reason_code,
    }
    if outcome.recorded_disposition_claims:
        row["recorded_disposition_claims"] = list(outcome.recorded_disposition_claims)
    return row


def _persist_panel_delivery(path: str, receipt: dict[str, Any]) -> None:
    """Persist the second-exit receipt while clearing stale evidence safely."""
    receipt_path = Path(path)
    receipt_unlink_failed = False
    try:
        receipt_path.unlink(missing_ok=True)
    except OSError as exc:
        receipt_unlink_failed = True
        _warn(f"::warning::could not remove the previous gate PR-comment receipt ({type(exc).__name__}: {exc})")
    try:
        _write_panel_delivery(path, receipt)
    except OSError as exc:
        if receipt_unlink_failed and receipt_path.exists():
            try:
                receipt_path.unlink(missing_ok=True)
            except OSError:
                pass
            if receipt_path.exists():
                try:
                    receipt_path.write_bytes(b"invalid gate PR-comment receipt\n")
                except OSError as invalidate_exc:
                    _warn(
                        "::warning::gate PR-comment receipt write failed and the stale receipt "
                        f"could not be cleared ({type(invalidate_exc).__name__}: {invalidate_exc}); "
                        "receipt channel is untrusted"
                    )
                else:
                    _warn("::warning::gate PR-comment receipt write failed and the stale receipt was destroyed; an invalid marker was written, upload will pass but consumers' json.loads will fail-loud")
            else:
                _warn("::warning::gate PR-comment receipt write failed and the stale receipt was cleared; upload will red")
        elif receipt_path.exists():
            _warn(f"::warning::gate PR-comment receipt write failed ({type(exc).__name__}: {exc}); receipt channel is untrusted")
        else:
            _warn(f"::warning::gate PR-comment receipt write failed ({type(exc).__name__}: {exc}); file is missing and upload will red")


def _publish_only(args: argparse.Namespace) -> int:
    """Publish only after the caller has uploaded the terminal envelope."""
    identity = Identity(
        repository_id=args.repository_id,
        head_sha=args.head_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        pr=args.pr_number,
    )
    current: dict[str, Any]
    silo_history: Optional[HistoryLoad] = None
    try:
        if not args.terminal_path:
            raise ValueError("terminal path is missing")
        record = json.loads(Path(args.terminal_path).read_text(encoding="utf-8"))
        current = _terminal_row(record, repository=args.repository, repository_id=args.repository_id, pr_number=args.pr_number)
        history_cache_path = _terminal_history_cache_path(args.terminal_path)
        if history_cache_path.is_file():
            try:
                silo_history = _read_terminal_history_cache(history_cache_path)
            except (OSError, ValueError) as exc:
                print(f"GATE-CONVERGENCE-HISTORY-CACHE-UNAVAILABLE reason={type(exc).__name__}")
                silo_history = HistoryLoad(incomplete_reasons=["terminal history cache unavailable"])
        elif record.get("convergence_shadow") is not None:
            print("GATE-CONVERGENCE-HISTORY-CACHE-UNAVAILABLE reason=missing")
            silo_history = HistoryLoad(incomplete_reasons=["terminal history cache missing"])
    except Exception as exc:
        current = _panel_current_row(
            outcome=Outcome(ok=False, classification="integration_error", reason_code="audit_invalid", gate_result="unavailable"),
            repository=args.repository,
            identity=identity,
        )
        body = scrub_for_publish(
            render_status_panel(
                [current],
                history_warning=f"terminal artifact unavailable: {type(exc).__name__}: {exc}",
            ),
            runtime_values=runtime_values_from_environment(),
        )
        receipt = _build_panel_delivery(
            body=body, repository=args.repository, pr_number=args.pr_number, identity=identity,
            delivery="not_created", reason_code="terminal_unavailable", error_category="configuration",
            history_error=f"{type(exc).__name__}: {exc}", operation="PUBLISH_ONLY",
        )
        _panel_warning(phase="terminal artifact validation", exc=exc, reason_code="terminal_unavailable", category="configuration", http_status=None)
    else:
        audit_dir = args.audit_dir
        runner_temp = os.environ.get("RUNNER_TEMP")
        if audit_dir is None and runner_temp:
            audit_dir = Path(runner_temp) / "primary-audit"
        primary_audit, _, _ = _read_audit_file(audit_dir)
        if primary_audit is not None and not validate_audit_identity(primary_audit, identity):
            current["primary_audit"] = primary_audit
        body, receipt = _post_status_panel_fail_open(
            current=current, repository=args.repository, repository_id=args.repository_id,
            pr_number=args.pr_number, identity=identity, silo_history=silo_history,
        )
    _append_panel_diagnostic(args.summary_path, receipt)
    if args.panel_delivery_path:
        _persist_panel_delivery(args.panel_delivery_path, receipt)
    return 0


def _append_panel_diagnostic(summary_path: Optional[str], receipt: dict[str, Any]) -> None:
    if (
        receipt.get("delivery") in ("created", "updated")
        and not receipt.get("history_error")
        and not receipt.get("history_incomplete")
        and not receipt.get("history_skipped_count")
        and not receipt.get("self_heal_errors")
    ):
        return
    status = receipt.get("http_status") if receipt.get("http_status") is not None else "unavailable"
    diagnostic = (
        "### Gate v2 status panel delivery diagnostic\n\n"
        f"- Delivery: `{receipt.get('delivery')}`\n"
        f"- HTTP status: `{status}`\n"
        f"- Permission category: `{receipt.get('error_category')}`\n"
        f"- Reason: `{receipt.get('reason_code')}`\n"
    )
    if receipt.get("exception_type"):
        diagnostic += "- 面板发布失败，面板可能显示旧状态\n"
    if receipt.get("history_error"):
        diagnostic += f"- History reconstruction: `{receipt['history_error']}`\n"
    if receipt.get("history_skipped_count"):
        diagnostic += f"- Skipped history records: `{receipt['history_skipped_count']}`\n"
        for record in receipt.get("history_skipped_records", []):
            diagnostic += f"  - `{record.get('name')}`: `{record.get('reason')}`\n"
    if receipt.get("history_incomplete_reasons"):
        diagnostic += "- History completeness: `incomplete`\n"
        for reason in receipt["history_incomplete_reasons"]:
            diagnostic += f"  - `{reason}`\n"
    if receipt.get("self_heal_errors"):
        diagnostic += "- Comment self-heal: `partial`\n"
        for error in receipt["self_heal_errors"]:
            diagnostic += f"  - `{error}`\n"
    diagnostic = scrub_for_publish(
        diagnostic,
        runtime_values=runtime_values_from_environment(),
    )
    try:
        print(diagnostic)
    except Exception:
        pass
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write("\n" + diagnostic)
        except OSError as exc:
            _warn(f"::warning::could not append the status panel diagnostic to Step Summary ({type(exc).__name__}: {exc})")


def _finish(
    outcome: Outcome, summary_path: Optional[str], *, terminal_path: Optional[str] = None, repository: Optional[str] = None,
    identity: Optional[Identity] = None, quality_result: Optional[str] = None, primary_result: Optional[str] = None,
    review_expected: Optional[bool] = None, is_draft: Optional[bool] = None, runner: Optional[str] = None,
    primary_audit: Any = None,
    pr_number: Optional[int] = None, panel_delivery_path: Optional[str] = None,
    convergence_receipt_path: Optional[str] = None,
    convergence_missing_scope_fields: tuple[str, ...] = (),
) -> int:
    """Shared tail for both the normal and the malformed-input paths through
    `main()`: render + print + (optionally) persist the Step Summary, emit
    ::notice::/::error:: annotations, and map ok -> exit code."""
    convergence_note = ""
    if convergence_receipt_path:
        if outcome.convergence_receipt is not None:
            _write_convergence_receipt(convergence_receipt_path, outcome.convergence_receipt)
            convergence_note = "\nConvergence receipt: produced (`convergence-receipt.json`).\n"
        elif convergence_missing_scope_fields:
            fields = ", ".join(convergence_missing_scope_fields)
            convergence_note = (
                "\nConvergence receipt: not produced (reason: canonical primary audit is missing "
                f"scope field(s): `{fields}`).\n"
            )
        else:
            reason = outcome.reason_code or outcome.classification or "canonical primary round was unavailable"
            convergence_note = f"\nConvergence receipt: not produced (reason: `{reason}`).\n"
    summary = scrub_for_publish(
        render_summary(
            outcome, repository=repository, identity=identity, is_draft=is_draft,
            runner=runner, primary_audit=primary_audit, primary_result=primary_result,
        ),
        runtime_values=runtime_values_from_environment(),
    )
    summary += convergence_note
    classification = outcome.classification if outcome.classification in TERMINAL_CLASSIFICATION_DOMAIN else "integration_error"
    reason_code = outcome.reason_code if outcome.reason_code in TERMINAL_REASON_DOMAIN else "audit_invalid"
    gate_result = outcome.gate_result if outcome.gate_result in GATE_RESULT_DOMAIN else ("pass" if outcome.ok else "fail")
    verdict = {
        "v": 1,
        "gate_result": gate_result,
        "classification": classification,
        "reason_code": reason_code,
        "primary": "skipped" if primary_result == "skipped" else "executed",
        "skip_reason": outcome.skip_reason,
        "draft": is_draft is True,
    }
    summary += "AGENT-GATE-VERDICT-V1 " + json.dumps(verdict, sort_keys=True, separators=(",", ":")) + "\n"
    print(summary)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary)
    for note in outcome.notes:
        print(f"::notice::{note}")
    for recorded_claim in outcome.recorded_disposition_claims:
        print(f"::notice::{recorded_claim}")
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
    return 0 if outcome.ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    argv_list = sys.argv[1:] if argv is None else list(argv)
    if argv_list[:1] == ["--render-previous-context"]:
        return _render_previous_context_cli(argv_list[1:])
    if argv_list[:1] == ["--annotate-primary-audit"]:
        return _annotate_primary_audit_cli(argv_list[1:])
    if argv_list[:1] == ["--render-findings-summary"]:
        return _render_findings_summary_cli(argv_list[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality-result", required=True, help="needs.quality.result")
    parser.add_argument("--caller-checks", default="", help="needs.quality.outputs.caller_checks (not_started|passed|failed|signal_terminated; empty when the evidence step never ran)")
    parser.add_argument("--caller-signal", default="", help="needs.quality.outputs.caller_signal (signal number when caller_checks=signal_terminated)")
    parser.add_argument("--preflight-result", default="", help="structured PR size preflight result (blocked means the measured PR exceeds the single-review budget; unavailable means measurement failed)")
    parser.add_argument("--primary-result", required=True, help="needs.primary.result")
    parser.add_argument("--runner", required=True, help="inputs.runner ('self'/'hosted') — validated strictly")
    parser.add_argument("--is-draft", required=True, help="github.event.pull_request.draft ('true'/'false')")
    parser.add_argument("--pr-author-json", default=None, help="JSON-serialized github.event.pull_request.user.login; no actor fallback")
    parser.add_argument("--is-fork", default="false", help="whether pull_request.head.repo.full_name differs from github.repository")
    parser.add_argument("--classify-review-expected", default="", help="needs.classify_pr_paths.outputs.review_expected; absent means no review-exempt reason can be identified")
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
    parser.add_argument(
        "--convergence-receipt-path",
        default=None,
        help="immutable convergence-receipt.json output path",
    )
    parser.add_argument("--panel-delivery-path", default=None, help="durable status-panel delivery diagnostic JSON output path")
    parser.add_argument("--summary-path", default=None, help="$GITHUB_STEP_SUMMARY")
    parser.add_argument("--publish-only", action="store_true", help="publish the panel after terminal artifact upload")
    args = parser.parse_args(argv_list)

    if args.publish_only:
        if args.pr_number is None:
            _warn("::warning::status panel publish skipped because PR number is missing")
            return 0
        return _publish_only(args)

    if args.pr_number is None:
        return _finish(
            Outcome(ok=False, problems=["missing PR number — fail-closed"]),
            args.summary_path,
            repository=args.repository,
            pr_number=None,
            panel_delivery_path=args.panel_delivery_path,
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
                repository=args.repository,
                pr_number=args.pr_number,
                panel_delivery_path=args.panel_delivery_path,
            )

    if not args.repository or not args.head_sha:
        return _finish(
            Outcome(ok=False, problems=["malformed repository/head_sha identity input — fail-closed"]),
            args.summary_path,
            repository=args.repository,
            pr_number=args.pr_number,
            panel_delivery_path=args.panel_delivery_path,
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
        is_fork = as_bool(args.is_fork)
        review_expected = as_bool(args.review_expected)
    except BoolParseError as exc:
        return _finish(
            Outcome(ok=False, problems=[f"malformed boolean input — fail-closed: {exc}"]),
            args.summary_path,
            repository=args.repository,
            pr_number=args.pr_number,
            panel_delivery_path=args.panel_delivery_path,
        )

    audit, audit_error, audit_bytes = _read_audit_file(args.audit_dir)
    try:
        pr_author = json.loads(args.pr_author_json) if args.pr_author_json is not None else None
    except json.JSONDecodeError:
        pr_author = None
    scope, missing_scope_fields = _convergence_scope_from_audit(audit, identity)
    legacy_raw_audit_digest = hashlib.sha256(audit_bytes).hexdigest() if audit_bytes is not None else None
    if isinstance(audit, dict):
        audit_digest = _CONVERGENCE.canonical_audit_digest(audit)
    else:
        audit_digest = legacy_raw_audit_digest

    waiver_receipts: tuple[Any, ...] = ()
    prior_eligible_run_ids: tuple[int, ...] = ()
    convergence_history_status = "history_unavailable"
    silo_terminal_history: Optional[HistoryLoad] = None
    if scope is not None:
        convergence_history_status, prior_eligible_run_ids, silo_terminal_history = _load_pr_convergence_history(
            repository=args.repository,
            repository_id=identity.repository_id,
            pr_number=identity.pr,
        )
        if args.terminal_path:
            try:
                _write_terminal_history_cache(args.terminal_path, silo_terminal_history)
            except OSError as exc:
                print(f"GATE-CONVERGENCE-HISTORY-CACHE-UNAVAILABLE reason={type(exc).__name__}")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and scope is not None and not (is_draft and args.primary_result == "skipped"):
        try:
            waiver_receipts = _fetch_disposition_receipts(
                token=token,
                repository=args.repository,
                repository_id=identity.repository_id,
                pr_number=identity.pr,
            )
        except Exception:
            waiver_receipts = ()

    # gate#297 wiring: collect NeighborEvidence for drifted candidate receipts
    # BEFORE record_dispositions runs inside evaluate().  The collector itself
    # returns {} (with zero network/git/silo calls) when no receipt is a
    # neighbor candidate; any per-receipt failure prints one greppable
    # GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE line and stays fail-closed.
    neighbor_evidence: Optional[dict[str, Any]] = {}
    if token and scope is not None and waiver_receipts:
        neighbor_evidence = _NEIGHBOR_EVIDENCE.collect_neighbor_evidence(
            receipts=waiver_receipts,
            repository=args.repository,
            repository_id=identity.repository_id,
            pr_number=identity.pr,
            current_head_sha=identity.head_sha,
            audit_digest=audit_digest if audit_digest is not None else "",
            legacy_raw_audit_digest=legacy_raw_audit_digest,
            terminal_history_rows=(
                silo_terminal_history.rows if silo_terminal_history is not None else ()
            ),
            token=token,
        )

    findings: list[Any] = []
    if isinstance(audit, dict):
        result = audit.get("result")
        if isinstance(result, dict) and isinstance(result.get("findings"), list):
            findings = result["findings"]
    inherited_rejection_matches: dict[tuple[str, str, str], str] = {}
    inherited_candidates = _INHERITED_REJECTION.receipt_blob_candidates(
        receipts=waiver_receipts,
        findings=findings,
        current_head_sha=identity.head_sha,
        canonical_finding_key=_GATE_ACTION.canonical_finding_key,
        is_rejected_eligible=_CARRY._rejected_releases,
    )
    if inherited_candidates and token:
        blob_oids = _NEIGHBOR_EVIDENCE.collect_file_blob_oids(
            repository=args.repository,
            current_head_sha=identity.head_sha,
            head_paths=sorted(_INHERITED_REJECTION.blob_fetch_requests(inherited_candidates)),
            token=token,
        )
        inherited_rejection_matches = _INHERITED_REJECTION.matching_finding_ids(
            inherited_candidates, blob_oids,
        )
    elif inherited_candidates:
        print("GATE-INHERITED-REJECTION-EVIDENCE-UNAVAILABLE reason=environment_missing")
    outcome = evaluate(
        quality_result=args.quality_result,
        caller_checks=args.caller_checks,
        caller_signal=args.caller_signal,
        preflight_result=args.preflight_result,
        primary_result=args.primary_result,
        runner=args.runner,
        is_draft=is_draft,
        is_fork=is_fork,
        classify_review_expected=args.classify_review_expected,
        review_expected=review_expected,
        audit=audit,
        audit_error=audit_error,
        identity=identity,
        pr_author=pr_author,
        audit_source_attempt=audit_source_attempt,
        audit_artifact_name=args.audit_artifact_name or None,
        scope=scope,
        audit_digest=audit_digest,
        legacy_raw_audit_digest=legacy_raw_audit_digest,
        waiver_receipts=waiver_receipts,
        repository=args.repository,
        prior_eligible_run_ids=prior_eligible_run_ids,
        convergence_history_status=convergence_history_status,
        neighbor_evidence=neighbor_evidence,
        inherited_rejection_matches=inherited_rejection_matches,
    )
    if isinstance(audit, dict):
        if "finding_relation" in audit:
            relation = validate_finding_relation_block(audit["finding_relation"])
        else:
            print("GATE-FINDING-RELATION-DEGRADED: source=primary-audit detail=unknown")
            current_findings = [
                {key: value for key, value in item.items()
                 if key not in {"relation_to_previous", "previous_finding_id"}}
                if isinstance(item, dict) else item
                for item in findings
            ]
            relation = build_finding_relation(current_findings, {"available": False})
            relation["history_status"] = "unknown"
            relation = validate_finding_relation_block(relation)
        relation.pop("previous_head_sha", None)
        outcome.finding_relation = _close_consumed_false_positive_carry(
            relation, findings, outcome.disposition_audit,
        )
        if relation["counts"]["conflict"] and outcome.gate_result == "pass":
            (outcome.ok, outcome.gate_result, outcome.classification, outcome.reason_code) = (
                False, "fail", "code_fail", "primary_findings",
            )
            outcome.problems.append("cross-round finding conflict requires manual review (manual_required)")
    fail_count, reconsider = _compute_pr_primary_convergence(
        silo_terminal_history if silo_terminal_history is not None else HistoryLoad(),
        identity=identity, primary_result=args.primary_result, outcome=outcome,
    )
    outcome.convergence = {"fail_count": fail_count, "reconsider": reconsider}

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
        primary_audit=audit,
        pr_number=identity.pr,
        panel_delivery_path=args.panel_delivery_path,
        convergence_receipt_path=args.convergence_receipt_path,
        convergence_missing_scope_fields=missing_scope_fields,
    )


if __name__ == "__main__":
    sys.exit(main())
