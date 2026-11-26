"""Focused terminal history implementation for gate aggregate."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Optional

from gate_aggregate_models import (
    GATE_RESULT_DOMAIN, HistoryLoad, Identity, MAX_REPO_WIDE_HISTORY_PAGES,
    MAX_TARGETED_HISTORY_RUNS, Outcome, PANEL_HISTORY_ROW_SCHEMA_VERSION,
    PRIMARY_SKIP_REASON_DOMAIN, SILO_CLI_CALL_TIMEOUT_SECONDS, SILO_TERMINAL_TIER,
    TERMINAL_CLASSIFICATION_DOMAIN, TERMINAL_HISTORY_CACHE_SCHEMA_VERSION,
    TERMINAL_PRIMARY_VERDICT_DOMAIN, TERMINAL_REASON_DOMAIN,
    _ACTIVE_PUBLISH_BUDGET, _PublishBudget, _PublishBudgetExhausted,
    _is_strict_int,
)

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

def _terminal_row(record: Any, *, repository: str, repository_id: int, pr_number: int, _ports: Any = None) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("gate terminal artifact is not a JSON object")
    if type(record.get("schema_version")) is not int or record.get("schema_version") != 1 or record.get("kind") != "gate_terminal":
        raise ValueError("gate terminal artifact has an unsupported schema")
    if record.get("repository") != repository or record.get("repository_id") != repository_id or record.get("pr_number") != pr_number:
        raise ValueError("gate terminal artifact identity does not match this PR")
    for field_name in ("run_id", "run_attempt"):
        if not _is_strict_int(record.get(field_name)) or record[field_name] <= 0:
            raise ValueError(f"gate terminal {field_name} must be a positive integer")
    if not isinstance(record.get("head_sha"), str) or not record["head_sha"]:
        raise ValueError("gate terminal head_sha must be a non-empty string")
    if record.get("gate_result") not in GATE_RESULT_DOMAIN:
        raise ValueError("gate terminal gate_result is outside the finite domain")
    primary_verdict = record.get("primary_verdict")
    if primary_verdict is not None and primary_verdict not in TERMINAL_PRIMARY_VERDICT_DOMAIN:
        raise ValueError("gate terminal primary_verdict is outside the finite domain")
    if record.get("classification") not in TERMINAL_CLASSIFICATION_DOMAIN or record.get("reason_code") not in TERMINAL_REASON_DOMAIN:
        raise ValueError("gate terminal classification/reason_code is outside the finite domain")
    audit = record.get("audit")
    known_primary_skip = (
        record.get("primary_result") == "skipped"
        and record.get("skip_reason") in PRIMARY_SKIP_REASON_DOMAIN
        and not (isinstance(audit, dict) and audit.get("available") is True)
    )
    shadow = record.get("convergence_shadow")
    eligible_primary_round = (
        isinstance(audit, dict)
        and audit.get("available") is True
        and record.get("primary_result") in ("success", "failure")
        and record["gate_result"] in ("pass", "fail")
    )
    if shadow is not None:
        if not isinstance(shadow, dict):
            raise ValueError("gate terminal convergence_shadow is not an object")
        if type(shadow.get("eligible_this_round")) is not bool:
            raise ValueError("gate terminal convergence eligibility is invalid")
        eligible_primary_round = shadow["eligible_this_round"]
    row = {
        "schema_version": PANEL_HISTORY_ROW_SCHEMA_VERSION,
        "repository": repository,
        "run_id": record["run_id"],
        "run_attempt": record["run_attempt"],
        "head_sha": record["head_sha"],
        "gate_result": record["gate_result"],
        "primary_verdict": primary_verdict,
        "_known_primary_skip": known_primary_skip,
        "classification": record["classification"],
        "reason_code": record["reason_code"],
        "_eligible_primary_round": eligible_primary_round,
    }
    if shadow is not None:
        history_status = shadow.get("history_status")
        decision = shadow.get("terminal_decision")
        if history_status not in ("available", "history_unavailable"):
            raise ValueError("gate terminal convergence history_status is invalid")
        if decision not in ("collecting", "arbitration_required", "history_unavailable"):
            raise ValueError("gate terminal convergence terminal_decision is invalid")
        if type(shadow.get("limit")) is not int or shadow["limit"] <= 0:
            raise ValueError("gate terminal convergence limit is invalid")
        projected_shadow = {
            "history_status": history_status,
            "terminal_decision": decision,
            "limit": shadow["limit"],
            "eligible_this_round": shadow["eligible_this_round"],
        }
        rounds = shadow.get("eligible_rounds")
        if history_status == "available":
            if type(rounds) is not int or rounds < 0:
                raise ValueError("gate terminal convergence eligible_rounds is invalid")
            projected_shadow["eligible_rounds"] = rounds
        elif "eligible_rounds" in shadow:
            raise ValueError("unavailable convergence history must not publish an eligible count")
        row["convergence_shadow"] = projected_shadow
    recorded_claims = record.get("recorded_disposition_claims")
    if isinstance(recorded_claims, list) and all(isinstance(item, str) for item in recorded_claims) and recorded_claims:
        row["recorded_disposition_claims"] = list(recorded_claims)
    resolved = record.get("resolved_findings")
    if isinstance(resolved, list) and all(isinstance(item, str) for item in resolved) and resolved:
        row["resolved_findings"] = [item for item in resolved if "resolved by receipt" not in item]
    if "finding_relation" in record:
        try:
            row["finding_relation"] = _ports._FINDING_RELATIONS.validate_finding_relation_block(record.get("finding_relation"))
        except _ports._FINDING_RELATIONS.FindingRelationError as exc:
            raise ValueError(str(exc)) from exc
    return row

def _compute_pr_primary_convergence(
    history: HistoryLoad, *, identity: Identity, primary_result: str, outcome: Outcome,
    _ports: Any = None) -> tuple[Optional[int], Optional[bool]]:
    """Count distinct primary failures across all heads in one PR.

    A known no-review skip contributes zero. The current verdict comes only
    from the validated audit in `Outcome`, even while its run is in progress.
    `run_id` deduplicates runs; `head_sha` identifies a row but does not partition
    PR-wide convergence history.
    """
    target_run_ids = history.target_run_ids
    target_ids = set(target_run_ids or ())
    complete = target_run_ids is not None and not history.incomplete_reasons
    rows_by_run: dict[int, list[dict[str, Any]]] = {}
    observed_run_ids: set[int] = set()
    for row in history.rows:
        run_id = row.get("run_id")
        if not _is_strict_int(run_id) or (
            target_run_ids is not None and run_id not in target_ids and run_id != identity.run_id
        ):
            complete = False
            continue
        observed_run_ids.add(run_id)
        if row.get("head_sha") != identity.head_sha and run_id == identity.run_id:
            complete = False
        rows_by_run.setdefault(run_id, []).append(row)
    if target_run_ids is not None:
        for run_id in target_ids:
            if run_id != identity.run_id and run_id not in observed_run_ids:
                complete = False
    rows_by_run.setdefault(identity.run_id, []).append({
        "run_attempt": identity.run_attempt,
        "primary_verdict": outcome.primary_verdict,
        "_known_primary_skip": (
            primary_result == "skipped"
            and outcome.skip_reason in PRIMARY_SKIP_REASON_DOMAIN
            and not outcome.audit_available
        ),
        "primary_result": primary_result,
        "audit_available": outcome.audit_available,
    })
    failed_run_ids: set[int] = set()
    for run_id, attempts in rows_by_run.items():
        actual_attempts = []
        for attempt in attempts:
            verdict = attempt.get("primary_verdict")
            if verdict in ("pass", "fail"):
                actual_attempts.append(attempt)
            elif verdict in ("unavailable", "not_expected", "waived") or attempt.get("_known_primary_skip") is True:
                continue
            else:
                complete = False
        if actual_attempts and max(actual_attempts, key=lambda row: row["run_attempt"])["primary_verdict"] == "fail":
            failed_run_ids.add(run_id)
    fail_count = len(failed_run_ids) if complete else None
    if outcome.primary_verdict == "pass" or (
        primary_result == "skipped" and outcome.skip_reason in PRIMARY_SKIP_REASON_DOMAIN
    ):
        reconsider: Optional[bool] = False
    elif outcome.primary_verdict == "fail":
        reconsider = True if len(failed_run_ids) >= 3 else (False if complete else None)
    else:
        reconsider = None
    return fail_count, reconsider

def _read_terminal_zip(raw: bytes, _ports: Any = None) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        candidates = [name for name in bundle.namelist() if Path(name).name == "gate-terminal.json"]
        if len(candidates) != 1:
            raise ValueError(f"terminal artifact must contain exactly one gate-terminal.json, found {len(candidates)}")
        return json.loads(bundle.read(candidates[0]))

def _consume_terminal_artifact(
    result: HistoryLoad, *, artifact: dict[str, Any], token: str, repository: str,
    repository_id: int, pr_number: int,
    _ports: Any = None) -> bool:
    name = artifact["name"]
    if artifact.get("expired") is True:
        result.skipped_records.append({"name": name, "reason": "expired_artifact"})
        result.incomplete_reasons.append(f"expired artifact {name}")
        return True
    archive_url = artifact.get("archive_download_url")
    if not isinstance(archive_url, str) or not archive_url:
        result.skipped_records.append({"name": name, "reason": "missing_archive_url"})
        result.incomplete_reasons.append(f"artifact {name} has no archive URL")
        return True
    try:
        record = _ports._read_terminal_zip(_ports._download_terminal_zip(token=token, url=archive_url))
        result.rows.append(_ports._terminal_row(record, repository=repository, repository_id=repository_id, pr_number=pr_number))
    except _PublishBudgetExhausted:
        result.incomplete_reasons.append(f"history budget exhausted while downloading {name}")
        return False
    except ValueError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        result.skipped_records.append({"name": name, "reason": reason})
        if "identity does not match" not in str(exc):
            result.incomplete_reasons.append(f"artifact {name}: {reason}")
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        result.skipped_records.append({"name": name, "reason": reason})
        result.incomplete_reasons.append(f"artifact {name}: {reason}")
    return True

def _fetch_github_terminal_history(
    *, token: str, repository: str, repository_id: int, pr_number: int,
    target_run_ids: Optional[list[int]] = None,
    _ports: Any = None) -> HistoryLoad:
    prefix = f"gate-terminal-v1-{repository_id}-"
    result = HistoryLoad()

    if target_run_ids is not None:
        unique_run_ids = list(dict.fromkeys(target_run_ids))
        if len(unique_run_ids) > MAX_TARGETED_HISTORY_RUNS:
            result.incomplete_reasons.append(
                f"bounded_scan: targeted history run limit {MAX_TARGETED_HISTORY_RUNS} reached"
            )
            unique_run_ids = unique_run_ids[:MAX_TARGETED_HISTORY_RUNS]
        for run_id in unique_run_ids:
            try:
                payload = _ports._github_json(
                    token=token,
                    url=f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts",
                )
            except _PublishBudgetExhausted:
                result.incomplete_reasons.append("history budget exhausted during targeted scan")
                break
            if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
                raise ValueError("Actions run artifacts response has an invalid shape")
            artifacts = [
                artifact for artifact in payload["artifacts"]
                if isinstance(artifact, dict)
                and isinstance(artifact.get("name"), str)
                and artifact["name"].startswith(prefix)
            ]
            if not artifacts:
                result.incomplete_reasons.append(f"no terminal artifact matched run {run_id}")
            for artifact in artifacts:
                if not _ports._consume_terminal_artifact(
                    result, artifact=artifact, token=token, repository=repository,
                    repository_id=repository_id, pr_number=pr_number,
                ):
                    return result
        if not unique_run_ids:
            result.incomplete_reasons.append("no cached panel run ids were available for targeted history")
        return result

    artifacts: list[dict[str, Any]] = []
    page = 1
    while page <= MAX_REPO_WIDE_HISTORY_PAGES:
        try:
            payload = _ports._github_json(
                token=token,
                url=f"https://api.github.com/repos/{repository}/actions/artifacts?per_page=100&page={page}",
            )
        except _PublishBudgetExhausted:
            result.incomplete_reasons.append("history budget exhausted during bounded_scan")
            break
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise ValueError("Actions artifacts response has an invalid shape")
        page_artifacts = payload["artifacts"]
        artifacts.extend(
            artifact for artifact in page_artifacts
            if isinstance(artifact, dict)
            and isinstance(artifact.get("name"), str)
            and artifact["name"].startswith(prefix)
        )
        if len(page_artifacts) < 100:
            break
        if page == MAX_REPO_WIDE_HISTORY_PAGES:
            result.incomplete_reasons.append(
                f"bounded_scan: repo-wide artifact page limit {MAX_REPO_WIDE_HISTORY_PAGES} reached"
            )
            break
        page += 1
    if not artifacts:
        if not result.incomplete_reasons:
            result.incomplete_reasons.append(f"no terminal artifact matched {prefix}")
    for artifact in artifacts:
        if not _ports._consume_terminal_artifact(
            result, artifact=artifact, token=token, repository=repository,
            repository_id=repository_id, pr_number=pr_number,
        ):
            break
    return result

def _terminal_artifact_run_id(name: str, repository_id: int, _ports: Any = None) -> Optional[int]:
    prefix = f"gate-terminal-v1-{repository_id}-"
    if not name.startswith(prefix):
        return None
    parts = name[len(prefix) :].rsplit("-", 2)
    if len(parts) != 3:
        return None
    sha, run_id_s, attempt_s = parts
    if len(sha) != 40 or not run_id_s.isdigit() or not attempt_s.isdigit():
        return None
    return int(run_id_s)

def _silo_artifact_name(key: str, _ports: Any = None) -> str:
    parts = key.split("/")
    if len(parts) >= 3 and parts[2]:
        return parts[2]
    return key

def _consume_silo_terminal_json(
    result: HistoryLoad, *, name: str, raw: bytes, repository: str,
    repository_id: int, pr_number: int,
    _ports: Any = None) -> None:
    try:
        record = json.loads(raw)
        result.rows.append(
            _ports._terminal_row(record, repository=repository, repository_id=repository_id, pr_number=pr_number)
        )
    except ValueError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        result.skipped_records.append({"name": name, "reason": reason})
        if "identity does not match" not in str(exc):
            result.incomplete_reasons.append(f"silo object {name}: {reason}")
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        result.skipped_records.append({"name": name, "reason": reason})
        result.incomplete_reasons.append(f"silo object {name}: {reason}")

def _fetch_silo_terminal_history(
    *, repository: str, repository_id: int, pr_number: int,
    target_run_ids: Optional[list[int]] = None,
    _ports: Any = None) -> HistoryLoad:
    """Fetch this PR's terminal records from the d30 Silo tier.

    Two hard bounds apply: only objects whose artifact name decodes to a
    targeted run id are ever downloaded (the artifact name does not carry the
    PR number, so the run-id filter is what keeps a busy repo from pulling
    every other PR's records), and each `_silo_cli` call runs against the
    active HISTORY_RECONSTRUCTION deadline — or SILO_CLI_CALL_TIMEOUT_SECONDS
    when no budget is active — so a slow Silo can never stall the job.
    """
    import tempfile

    prefix = f"{SILO_TERMINAL_TIER}/{repository_id}/gate-terminal-v1-{repository_id}-"
    result = HistoryLoad()
    budget = _ACTIVE_PUBLISH_BUDGET.get()
    deadline = (
        budget.operation_deadline
        if budget is not None and budget.current_operation == "HISTORY_RECONSTRUCTION"
        else None
    )

    def silo_timeout() -> float:
        if deadline is None:
            return SILO_CLI_CALL_TIMEOUT_SECONDS
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _PublishBudgetExhausted("HISTORY_RECONSTRUCTION")
        return remaining

    allowed: Optional[set[int]] = None
    if target_run_ids is not None:
        unique_run_ids = list(dict.fromkeys(target_run_ids))
        if len(unique_run_ids) > MAX_TARGETED_HISTORY_RUNS:
            unique_run_ids = unique_run_ids[:MAX_TARGETED_HISTORY_RUNS]
        allowed = set(unique_run_ids)
    with tempfile.TemporaryDirectory(prefix="silo-terminal-") as dest:
        proc = _ports._silo_cli(["list", "--prefix", prefix], silo_timeout())
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "silo list failed").strip())
        keys = [line for line in proc.stdout.splitlines() if line.strip()]
        for key in keys:
            if allowed is not None:
                run_id = _ports._terminal_artifact_run_id(_ports._silo_artifact_name(key), repository_id)
                if run_id is None or run_id not in allowed:
                    continue
            parts = key.split("/", 3)
            if len(parts) != 4:
                raise ValueError(f"silo key is not tier/repo_id/artifact_name/path: {key}")
            target = Path(dest) / parts[2]
            try:
                proc = _ports._silo_cli(["get", "--key", key, "--dest", str(target)], silo_timeout())
            except subprocess.TimeoutExpired as exc:
                if deadline is not None:
                    raise _PublishBudgetExhausted("HISTORY_RECONSTRUCTION") from exc
                raise
            if proc.returncode != 0:
                raise RuntimeError(
                    (proc.stderr or proc.stdout or f"silo get failed: {key}").strip()
                )
            _ports._consume_silo_terminal_json(
                result, name=_ports._silo_artifact_name(key), raw=target.read_bytes(),
                repository=repository, repository_id=repository_id, pr_number=pr_number,
            )
            target.unlink(missing_ok=True)
    return result

def _pr_target_run_ids(*, token: str, repository: str, pr_number: int, _ports: Any = None) -> Optional[list[int]]:
    """Cheaply enumerate this PR's prior Actions runs so history reads can be
    PR-targeted instead of repo-wide: the PR's head branch narrows the run
    list, and the silo/GitHub artifact filters drop everything that is not a
    gate terminal record. One page (newest first) is enough because
    MAX_TARGETED_HISTORY_RUNS already caps the consumed set at 50.

    Returns None instead of a partial list whenever the run list is known to
    be incomplete — the API reports more runs than this page holds
    (total_count, or a full page when total_count is absent), or the list
    exceeds MAX_TARGETED_HISTORY_RUNS. INV-A4: a history that cannot be fully
    listed must degrade to history_unavailable, never to a smaller count.
    `event=pull_request` matches the only trigger the Required Gate caller
    template declares (terminal records exist for pull_request runs only) and
    keeps unrelated branch runs from triggering the truncation path.
    """
    pull = _ports._github_json(token=token, url=f"https://api.github.com/repos/{repository}/pulls/{pr_number}")
    if not isinstance(pull, dict) or not isinstance(pull.get("head"), dict):
        raise ValueError("PR response has an invalid shape")
    head_ref = pull["head"].get("ref")
    if not isinstance(head_ref, str) or not head_ref:
        raise ValueError("PR head branch is missing")
    payload = _ports._github_json(
        token=token,
        url=(
            f"https://api.github.com/repos/{repository}/actions/runs"
            f"?event=pull_request&branch={urllib.parse.quote(head_ref, safe='')}&per_page=100"
        ),
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise ValueError("Actions runs response has an invalid shape")
    workflow_runs = payload["workflow_runs"]
    total_count = payload.get("total_count")
    if isinstance(total_count, int) and total_count > len(workflow_runs):
        return None
    if not isinstance(total_count, int) and len(workflow_runs) >= 100:
        return None
    run_ids = [
        run["id"] for run in workflow_runs
        if isinstance(run, dict) and _is_strict_int(run.get("id")) and run["id"] > 0
    ]
    unique_run_ids = list(dict.fromkeys(run_ids))
    if len(unique_run_ids) > MAX_TARGETED_HISTORY_RUNS:
        return None
    return unique_run_ids

def _load_pr_convergence_history(
    *, repository: str, repository_id: int, pr_number: int,
    _ports: Any = None) -> tuple[str, tuple[int, ...], HistoryLoad]:
    """Load d30 terminal history once for both shadow count and panel cache.

    The whole load — Silo reads plus the GitHub fallback — runs under one
    HISTORY_RECONSTRUCTION deadline from `_PublishBudget` (45s by default, the
    same knob the panel flow uses). gate#287 was reverted (gate#290) because an
    unbounded Silo read plus a repo-wide GitHub scan pushed the aggregate job
    past its `timeout-minutes`; the deadline turns any such stall into a fast
    `history_unavailable` with a greppable reason instead.
    """
    history = HistoryLoad()
    if not _ports._silo_configured():
        print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=silo_not_configured")
        history.incomplete_reasons.append("Silo terminal history unavailable: not configured")
        return "history_unavailable", (), history
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        # Without a token the load cannot be PR-targeted; an untargeted read
        # would download every terminal record on the repo (gate#290's revert
        # reason), so it fails fast instead.
        print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=github_token_missing")
        history.incomplete_reasons.append("history loader needs GITHUB_TOKEN to target this PR's runs")
        return "history_unavailable", (), history
    try:
        budget = _PublishBudget.from_environment()
    except ValueError as exc:
        print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=invalid_history_budget")
        history.incomplete_reasons.append(f"invalid history budget configuration: {exc}")
        return "history_unavailable", (), history
    budget_context = _ACTIVE_PUBLISH_BUDGET.set(budget)
    try:
        budget.begin("HISTORY_RECONSTRUCTION")
        try:
            target_run_ids = _ports._pr_target_run_ids(
                token=token, repository=repository, pr_number=pr_number,
            )
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            print(f"GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason={type(exc).__name__}")
            history.incomplete_reasons.append(f"GitHub run targeting unavailable: {type(exc).__name__}")
            return "history_unavailable", (), history
        if target_run_ids is None:
            # INV-A4: a truncated run list must never shrink to a partial count.
            print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=history_truncated")
            history.incomplete_reasons.append(
                "history truncated: this PR's branch has more pull_request runs than one page or the targeted cap"
            )
            return "history_unavailable", (), history
        history.target_run_ids = tuple(target_run_ids)
        try:
            history = _ports._fetch_silo_terminal_history(
                repository=repository, repository_id=repository_id, pr_number=pr_number,
                target_run_ids=target_run_ids,
            )
            history.target_run_ids = tuple(target_run_ids)
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            print(f"GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason={type(exc).__name__}")
            history.incomplete_reasons.append(f"Silo terminal history unavailable: {type(exc).__name__}")
            return "history_unavailable", (), history
        if history.incomplete_reasons:
            print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=incomplete_terminal_history")
            return "history_unavailable", (), history
        if not history.rows and target_run_ids:
            try:
                github_history = _ports._fetch_github_terminal_history(
                    token=token,
                    repository=repository,
                    repository_id=repository_id,
                    pr_number=pr_number,
                    target_run_ids=target_run_ids,
                )
            except _PublishBudgetExhausted:
                raise
            except Exception as exc:
                print(f"GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason={type(exc).__name__}")
                history.incomplete_reasons.append(f"GitHub terminal history unavailable: {type(exc).__name__}")
                return "history_unavailable", (), history
            if github_history.rows:
                print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=silo_empty_but_github_has_terminal_history")
                history.incomplete_reasons.append("Silo empty but GitHub has terminal history")
                return "history_unavailable", (), history
            budget_exhausted = [
                reason for reason in github_history.incomplete_reasons
                if "history budget exhausted" in reason
            ]
            unexpected_reasons = [
                reason for reason in github_history.incomplete_reasons
                if not reason.startswith("no terminal artifact matched run ")
                and "history budget exhausted" not in reason
            ]
            if budget_exhausted:
                raise _PublishBudgetExhausted("HISTORY_RECONSTRUCTION")
            if unexpected_reasons:
                print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=github_history_incomplete")
                history.incomplete_reasons.append("GitHub terminal history incomplete")
                return "history_unavailable", (), history
    except _PublishBudgetExhausted:
        print("GATE-CONVERGENCE-HISTORY-UNAVAILABLE reason=history_budget_exhausted")
        history.incomplete_reasons.append("history budget exhausted")
        return "history_unavailable", (), history
    finally:
        _ACTIVE_PUBLISH_BUDGET.reset(budget_context)
    return "available", tuple(
        row["run_id"] for row in history.rows
        if row.get("_eligible_primary_round") is True
    ), history

def _terminal_history_cache_path(terminal_path: str | Path, _ports: Any = None) -> Path:
    target = Path(terminal_path)
    return target.with_name(target.name + ".history.json")

def _write_terminal_history_cache(terminal_path: str | Path, history: HistoryLoad, _ports: Any = None) -> None:
    payload = {
        "schema_version": TERMINAL_HISTORY_CACHE_SCHEMA_VERSION,
        "rows": history.rows,
        "skipped_records": history.skipped_records,
        "incomplete_reasons": history.incomplete_reasons,
    }
    target = _ports._terminal_history_cache_path(terminal_path)
    temporary_path = target.with_name(f".{target.name}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(target)

def _read_terminal_history_cache(path: str | Path, _ports: Any = None) -> HistoryLoad:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != TERMINAL_HISTORY_CACHE_SCHEMA_VERSION:
        raise ValueError("terminal history cache has an unsupported schema")
    rows = payload.get("rows")
    skipped_records = payload.get("skipped_records")
    incomplete_reasons = payload.get("incomplete_reasons")
    if (
        not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
        or not isinstance(skipped_records, list) or any(not isinstance(row, dict) for row in skipped_records)
        or not isinstance(incomplete_reasons, list) or any(not isinstance(reason, str) for reason in incomplete_reasons)
    ):
        raise ValueError("terminal history cache has an invalid payload")
    return HistoryLoad(rows=rows, skipped_records=skipped_records, incomplete_reasons=incomplete_reasons)

def _merge_history_loads(github: HistoryLoad, silo: HistoryLoad, _ports: Any = None) -> HistoryLoad:
    by_identity: dict[tuple[int, int], dict[str, Any]] = {}
    for row in github.rows:
        by_identity[(row["run_id"], row["run_attempt"])] = row
    for row in silo.rows:
        by_identity[(row["run_id"], row["run_attempt"])] = row
    return HistoryLoad(
        rows=list(by_identity.values()),
        skipped_records=[*github.skipped_records, *silo.skipped_records],
        incomplete_reasons=[*github.incomplete_reasons, *silo.incomplete_reasons],
    )

def _fetch_terminal_history(
    *, token: str, repository: str, repository_id: int, pr_number: int,
    target_run_ids: Optional[list[int]] = None,
    silo_history: Optional[HistoryLoad] = None,
    _ports: Any = None) -> HistoryLoad:
    github = HistoryLoad()
    try:
        github = _ports._fetch_github_terminal_history(
            token=token, repository=repository, repository_id=repository_id,
            pr_number=pr_number, target_run_ids=target_run_ids,
        )
    except _PublishBudgetExhausted:
        raise
    except Exception as exc:
        github = HistoryLoad(
            incomplete_reasons=[f"GitHub terminal history unavailable: {type(exc).__name__}"],
        )
    silo = silo_history if silo_history is not None else HistoryLoad()
    if silo_history is None and _ports._silo_configured():
        try:
            silo = _ports._fetch_silo_terminal_history(
                repository=repository, repository_id=repository_id,
                pr_number=pr_number, target_run_ids=target_run_ids,
            )
        except _PublishBudgetExhausted:
            raise
        except Exception as exc:
            print(f"::warning::Silo terminal history unavailable: {type(exc).__name__}")
            silo = HistoryLoad(
                incomplete_reasons=[f"Silo terminal history unavailable: {type(exc).__name__}"],
            )
    merged = _ports._merge_history_loads(github, silo)
    present_runs = {row["run_id"] for row in merged.rows}
    filtered: list[str] = []
    for reason in merged.incomplete_reasons:
        if reason.startswith("no terminal artifact matched run "):
            run_token = reason.rsplit(" ", 1)[-1]
            if run_token.isdigit() and int(run_token) in present_runs:
                continue
        if reason.startswith("no terminal artifact matched gate-terminal-v1-") and merged.rows:
            continue
        filtered.append(reason)
    merged.incomplete_reasons = filtered
    return merged
