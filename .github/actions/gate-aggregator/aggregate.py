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



_RENDERING_NAME = "gate_aggregate_rendering"
_RENDERING = sys.modules.get(_RENDERING_NAME)
if _RENDERING is None:
    _RENDERING_SPEC = importlib.util.spec_from_file_location(
        _RENDERING_NAME, Path(__file__).with_name("rendering.py")
    )
    assert _RENDERING_SPEC and _RENDERING_SPEC.loader
    _RENDERING = importlib.util.module_from_spec(_RENDERING_SPEC)
    sys.modules[_RENDERING_NAME] = _RENDERING
    _RENDERING_SPEC.loader.exec_module(_RENDERING)

_FindingsSummaryUnavailable = _RENDERING._FindingsSummaryUnavailable
_summary_cell = _RENDERING._summary_cell
_markdown_cell = _RENDERING._markdown_cell
_append_step_summary = _RENDERING._append_step_summary
_degrade_findings_summary = _RENDERING._degrade_findings_summary
_load_audit_findings = _RENDERING._load_audit_findings
_finding_summary_rows = _RENDERING._finding_summary_rows
_primary_findings_panel_lines = _RENDERING._primary_findings_panel_lines
def _render_findings_summary(audit_path: Path, summary_path: Optional[str]) -> None:
    return _RENDERING._render_findings_summary(audit_path, summary_path, _ports=_rendering_ports())
def _render_findings_summary_cli(argv: list[str]) -> int:
    return _RENDERING._render_findings_summary_cli(argv, _ports=_rendering_ports())
_action_sentence = _RENDERING._action_sentence
render_summary = _RENDERING.render_summary
_primary_budget_exhausted_action = _RENDERING._primary_budget_exhausted_action
_panel_action = _RENDERING._panel_action
_history_reason_category = _RENDERING._history_reason_category
_summarize_history_reasons = _RENDERING._summarize_history_reasons
_bounded_history_warning = _RENDERING._bounded_history_warning
render_status_panel = _RENDERING.render_status_panel

def _rendering_ports() -> SimpleNamespace:
    return SimpleNamespace(
        scrub_for_publish=scrub_for_publish,
        runtime_values_from_environment=runtime_values_from_environment,
    )

def _read_audit_file(audit_dir: Optional[Path]) -> tuple[Any, Optional[str], Optional[bytes]]:
    return _GATE_IO._read_audit_file(audit_dir=audit_dir, _ports=_gate_io_ports())


def _write_convergence_receipt(path: str, receipt: Any) -> None:
    return _GATE_IO._write_convergence_receipt(path=path, receipt=receipt, _ports=_gate_io_ports())


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



_PANEL_PUBLISH_NAME = "gate_panel_publish"
_PANEL_PUBLISH = sys.modules.get(_PANEL_PUBLISH_NAME)
if _PANEL_PUBLISH is None:
    _PANEL_PUBLISH_SPEC = importlib.util.spec_from_file_location(
        _PANEL_PUBLISH_NAME, Path(__file__).with_name("panel_publish.py")
    )
    assert _PANEL_PUBLISH_SPEC and _PANEL_PUBLISH_SPEC.loader
    _PANEL_PUBLISH = importlib.util.module_from_spec(_PANEL_PUBLISH_SPEC)
    sys.modules[_PANEL_PUBLISH_NAME] = _PANEL_PUBLISH
    _PANEL_PUBLISH_SPEC.loader.exec_module(_PANEL_PUBLISH)

_merge_panel_rows = _PANEL_PUBLISH._merge_panel_rows
_find_panel_comments = _PANEL_PUBLISH._find_panel_comments
_find_panel_comment = _PANEL_PUBLISH._find_panel_comment
_parse_panel_history = _PANEL_PUBLISH._parse_panel_history
_warn = _PANEL_PUBLISH._warn
_build_panel_delivery = _PANEL_PUBLISH._build_panel_delivery
def _panel_failure(exc: BaseException) -> tuple[str, str, Optional[int], str]:
    return _PANEL_PUBLISH._panel_failure(exc, _ports=_panel_publish_ports())
def _panel_warning(
    *, phase: str, exc: BaseException, reason_code: str, category: str,
    http_status: Optional[int], operation: Optional[str] = None,
    attempts: int = 1, safe_reason: str = "",
) -> None:
    return _PANEL_PUBLISH._panel_warning(phase=phase, exc=exc, reason_code=reason_code, category=category, http_status=http_status, operation=operation, attempts=attempts, safe_reason=safe_reason, _ports=_panel_publish_ports())
def _bounded_panel_request(
    request: Any, *, before_retry: Optional[Any] = None,
) -> tuple[Any, int, bool, Optional[Exception]]:
    return _PANEL_PUBLISH._bounded_panel_request(request, before_retry=before_retry, _ports=_panel_publish_ports())
def _publish_panel_patch(
    *, repository: str, comment_id: int, body: str, token: str, run_id: int, run_attempt: int,
) -> tuple[int, bool, Optional[Exception]]:
    return _PANEL_PUBLISH._publish_panel_patch(repository=repository, comment_id=comment_id, body=body, token=token, run_id=run_id, run_attempt=run_attempt, _ports=_panel_publish_ports())
def _post_status_panel_fail_open_with_budget(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity], budget: _PublishBudget,
    silo_history: Optional[HistoryLoad] = None,
) -> tuple[str, dict[str, Any]]:
    return _PANEL_PUBLISH._post_status_panel_fail_open_with_budget(current=current, repository=repository, repository_id=repository_id, pr_number=pr_number, identity=identity, budget=budget, silo_history=silo_history, _ports=_panel_publish_ports())
def _post_status_panel_fail_open(
    *, current: dict[str, Any], repository: Optional[str], repository_id: Optional[int],
    pr_number: Optional[int], identity: Optional[Identity],
    silo_history: Optional[HistoryLoad] = None,
) -> tuple[str, dict[str, Any]]:
    return _PANEL_PUBLISH._post_status_panel_fail_open(current=current, repository=repository, repository_id=repository_id, pr_number=pr_number, identity=identity, silo_history=silo_history, _ports=_panel_publish_ports())
_write_panel_delivery = _PANEL_PUBLISH._write_panel_delivery
_panel_current_row = _PANEL_PUBLISH._panel_current_row
def _persist_panel_delivery(path: str, receipt: dict[str, Any]) -> None:
    return _PANEL_PUBLISH._persist_panel_delivery(path, receipt, _ports=_panel_publish_ports())
def _publish_only(args: argparse.Namespace) -> int:
    return _PANEL_PUBLISH._publish_only(args, _ports=_panel_publish_ports())
def _append_panel_diagnostic(summary_path: Optional[str], receipt: dict[str, Any]) -> None:
    return _PANEL_PUBLISH._append_panel_diagnostic(summary_path, receipt, _ports=_panel_publish_ports())

def _panel_publish_ports() -> SimpleNamespace:
    return SimpleNamespace(
        scrub_for_publish=scrub_for_publish,
        runtime_values_from_environment=runtime_values_from_environment,
        render_status_panel=render_status_panel,
        _warn=_warn, _github_json=_github_json, _bounded_panel_request=_bounded_panel_request,
        _patch_issue_comment=_patch_issue_comment, _parse_panel_history=_parse_panel_history,
        _panel_warning=_panel_warning, _build_panel_delivery=_build_panel_delivery,
        _github_identity=_github_identity, _fetch_panel_comments=_fetch_panel_comments,
        _find_panel_comments=_find_panel_comments, _fetch_terminal_history=_fetch_terminal_history,
        _merge_panel_rows=_merge_panel_rows, _publish_panel_patch=_publish_panel_patch,
        _delete_issue_comment=_delete_issue_comment, _panel_failure=_panel_failure,
        _post_status_panel_fail_open_with_budget=_post_status_panel_fail_open_with_budget,
        _write_panel_delivery=_write_panel_delivery, _terminal_row=_terminal_row,
        _terminal_history_cache_path=_terminal_history_cache_path,
        _read_terminal_history_cache=_read_terminal_history_cache, _panel_current_row=_panel_current_row,
        _read_audit_file=_read_audit_file, validate_audit_identity=validate_audit_identity,
        _post_status_panel_fail_open=_post_status_panel_fail_open,
        _append_panel_diagnostic=_append_panel_diagnostic,
        _persist_panel_delivery=_persist_panel_delivery, _post_issue_comment=_post_issue_comment,
    )

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
