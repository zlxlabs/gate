"""Focused previous context implementation for gate aggregate."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from gate_aggregate_models import Identity, Outcome, SILO_CLI_CALL_TIMEOUT_SECONDS, SILO_TERMINAL_TIER
from gate_finding_relations import (
    PREVIOUS_FINDING_LIMIT, _LATEST_LEDGER_DEGRADE, _LEDGER_LATEST_OBJECT,
    _LEDGER_LATEST_PREFIX, _clip_text, _consume_explicit_withdrawals,
    _degrade_previous_round, apply_finding_relation,
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

_CARRY_FINDINGS_NAME = "gate_carry_findings_from_aggregate"
_CARRY_FINDINGS = sys.modules.get(_CARRY_FINDINGS_NAME)
if _CARRY_FINDINGS is None:
    _CARRY_FINDINGS_SPEC = importlib.util.spec_from_file_location(
        _CARRY_FINDINGS_NAME, Path(__file__).with_name("carry_findings.py")
    )
    assert _CARRY_FINDINGS_SPEC and _CARRY_FINDINGS_SPEC.loader
    _CARRY_FINDINGS = importlib.util.module_from_spec(_CARRY_FINDINGS_SPEC)
    sys.modules[_CARRY_FINDINGS_NAME] = _CARRY_FINDINGS
    _CARRY_FINDINGS_SPEC.loader.exec_module(_CARRY_FINDINGS)

_NEIGHBOR_EVIDENCE_NAME = "gate_neighbor_evidence_from_aggregate"
_NEIGHBOR_EVIDENCE = sys.modules.get(_NEIGHBOR_EVIDENCE_NAME)
if _NEIGHBOR_EVIDENCE is None:
    _NEIGHBOR_EVIDENCE_SPEC = importlib.util.spec_from_file_location(
        _NEIGHBOR_EVIDENCE_NAME, Path(__file__).with_name("neighbor_evidence.py")
    )
    assert _NEIGHBOR_EVIDENCE_SPEC and _NEIGHBOR_EVIDENCE_SPEC.loader
    _NEIGHBOR_EVIDENCE = importlib.util.module_from_spec(_NEIGHBOR_EVIDENCE_SPEC)
    sys.modules[_NEIGHBOR_EVIDENCE_NAME] = _NEIGHBOR_EVIDENCE
    _NEIGHBOR_EVIDENCE_SPEC.loader.exec_module(_NEIGHBOR_EVIDENCE)

def _ledger_latest_key(repository_id: int, pr_number: int, _ports: Any = None) -> str:
    return (
        f"{SILO_TERMINAL_TIER}/{repository_id}/"
        f"{_LEDGER_LATEST_PREFIX}{repository_id}-pr{pr_number}/"
        f"{_LEDGER_LATEST_OBJECT}"
    )

def _previous_findings_from_silo(
    *, repository_id: int, pr_number: int, run_id: int, run_attempt: int,
    _ports: Any = None) -> tuple[Optional[str], list[Any]]:
    """Return carried open facts (legacy latest findings when absent); a missing head stays unknown."""
    import tempfile

    key = _ports._ledger_latest_key(repository_id, pr_number)
    with tempfile.TemporaryDirectory(prefix="silo-ledger-latest-") as dest:
        target = Path(dest) / _LEDGER_LATEST_OBJECT
        proc = _ports._silo_cli(["get", "--key", key, "--dest", str(target)], SILO_CLI_CALL_TIMEOUT_SECONDS)
        if proc.returncode == 2: raise RuntimeError("silo-ledger-latest-absent")
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or f"silo get failed: {key}").strip())
        raw = target.read_text(encoding="utf-8")
    try:
        entry = json.loads(raw.splitlines()[0])
    except (IndexError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("silo-ledger-latest-unreadable") from exc
    if not isinstance(entry, dict) or entry.get("pr_number") != pr_number:
        raise RuntimeError("silo-ledger-latest-unreadable")
    try:
        parsed = (int(entry["run_id"]), int(entry["run_attempt"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("silo-ledger-latest-unreadable") from exc
    if parsed >= (run_id, run_attempt): raise RuntimeError("silo-ledger-latest-not-prior")
    review = entry.get("review") if isinstance(entry.get("review"), dict) else {}
    result = review.get("result") if isinstance(review.get("result"), dict) else {}
    findings = result.get("findings") if isinstance(result.get("findings"), list) else []
    finding_relation = entry.get("finding_relation")
    if finding_relation is not None and not isinstance(finding_relation, dict):
        raise RuntimeError("silo-ledger-latest-unreadable")
    if isinstance(finding_relation, dict) and "open_findings" in finding_relation:
        carried = finding_relation["open_findings"]
        if not isinstance(carried, list) or any(not isinstance(item, dict) for item in carried):
            raise RuntimeError("silo-ledger-latest-unreadable")
    else:
        carried = findings
    head_sha = entry.get("head_sha")
    return head_sha if isinstance(head_sha, str) and head_sha else None, [
        item for item in carried if isinstance(item, dict)
    ]

def load_previous_round_findings(
    *, path: Optional[str], repository_id: int, pr_number: int, run_id: int, run_attempt: int,
    _ports: Any = None) -> dict[str, Any]:
    if path:
        file = Path(path)
        if not file.is_file() or file.stat().st_size == 0:
            return _degrade_previous_round("previous-findings-file-missing")
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return _degrade_previous_round("previous-findings-file-invalid")
        if isinstance(payload, dict) and payload.get("available") is False:
            detail = payload.get("detail")
            return _degrade_previous_round(detail if isinstance(detail, str) and detail else "previous-findings-unavailable")
        if isinstance(payload, dict) and payload.get("available") is True and isinstance(payload.get("findings"), list):
            open_findings = payload.get("open_findings", payload["findings"])
            if not isinstance(open_findings, list) or any(not isinstance(item, dict) for item in open_findings):
                return _degrade_previous_round("previous-findings-file-invalid")
            head_sha = payload.get("head_sha")
            return {
                "available": True,
                "detail": "",
                "head_sha": head_sha if isinstance(head_sha, str) and head_sha else None,
                "findings": payload["findings"],
                "open_findings": open_findings,
            }
        return _degrade_previous_round("previous-findings-file-invalid")
    if not _ports._silo_configured(): return _degrade_previous_round("silo-not-configured")
    try:
        head_sha, findings = _ports._previous_findings_from_silo(
            repository_id=repository_id, pr_number=pr_number, run_id=run_id, run_attempt=run_attempt,
        )
    except Exception as exc:
        detail = str(exc)
        if detail in _LATEST_LEDGER_DEGRADE: return _degrade_previous_round(detail)
        return _degrade_previous_round(f"silo-ledger-unreadable:{type(exc).__name__}")
    return {"available": True, "detail": "", "head_sha": head_sha, "findings": findings}

def _project_previous_dispositions(
    *, repository_id: int, pr_number: int,
    _ports: Any = None) -> list[dict[str, Any]]:
    projected: list[tuple[str, dict[str, Any]]] = []
    dropped = 0
    for receipt in _ports._fetch_silo_disposition_receipts(repository_id=repository_id, pr_number=pr_number):
        counterevidence = None
        tracking_issue = None
        if receipt.disposition == "false-positive":
            if _CONVERGENCE._counterevidence_reason(receipt) is not None:
                dropped += 1
                continue
            evidence = receipt.counterevidence
            counterevidence = {
                field: _clip_text(evidence[field])
                for field in ("command", "output", "result", "pointer")
            }
            if evidence.get("match_expectation") == "absent":
                counterevidence["match_expectation"] = "absent"
        elif receipt.disposition == "deferred":
            if not isinstance(receipt.tracking_issue, str) or not receipt.tracking_issue.strip():
                dropped += 1
                continue
            tracking_issue = _clip_text(receipt.tracking_issue)
        elif receipt.disposition == "rejected":
            # gate-hub#2644: rejected projects evidence-free; blank reason stays dropped.
            if not receipt.reason.strip():
                dropped += 1
                continue
        else:
            dropped += 1
            continue

        approved_at = receipt.approved_at if isinstance(receipt.approved_at, str) else ""
        projected.append((approved_at, {
            "finding_id": _clip_text(receipt.finding_id),
            "finding_key": receipt.finding_key,
            "disposition": _clip_text(receipt.disposition),
            "head_sha": receipt.head_sha,
            "approved_at": _clip_text(approved_at),
            "reason": _clip_text(receipt.reason),
            "counterevidence": counterevidence,
            "tracking_issue": tracking_issue,
        }))

    projected.sort(key=lambda item: item[0], reverse=True)
    if len(projected) > PREVIOUS_FINDING_LIMIT:
        dropped += len(projected) - PREVIOUS_FINDING_LIMIT
        projected = projected[:PREVIOUS_FINDING_LIMIT]
    result = [item for _approved_at, item in projected]
    print(f"GATE-PREVIOUS-DISPOSITIONS: kept={len(result)} dropped={dropped}")
    return result

def _render_previous_context_cli(argv: list[str], _ports: Any = None) -> int:
    parser = argparse.ArgumentParser()
    for name in ("--repository-id", "--pr-number", "--run-id", "--run-attempt"):
        parser.add_argument(name, required=True, type=int)
    parser.add_argument("--previous-json", required=True)
    args = parser.parse_args(argv)
    previous = _ports.load_previous_round_findings(
        path=None, repository_id=args.repository_id, pr_number=args.pr_number,
        run_id=args.run_id, run_attempt=args.run_attempt,
    )
    dispositions = _ports._project_previous_dispositions(
        repository_id=args.repository_id, pr_number=args.pr_number,
    ) if previous["available"] else []
    rejected_release_keys: set[tuple[str, str]] = set()
    target_head_sha = previous.get("head_sha")
    candidates = _ports._INHERITED_REJECTION.disposition_blob_candidates(
        findings=previous.get("findings", []), dispositions=dispositions,
        target_head_sha=target_head_sha or "",
        canonical_finding_key=_ports._GATE_ACTION.canonical_finding_key,
        is_rejected_eligible=_ports._CARRY._rejected_releases,
    ) if previous["available"] else []
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("REPOSITORY", "")
    if candidates and token and repository and target_head_sha:
        blob_oids = _ports._NEIGHBOR_EVIDENCE.collect_file_blob_oids(
            repository=repository,
            current_head_sha=target_head_sha,
            head_paths=sorted(_ports._INHERITED_REJECTION.blob_fetch_requests(candidates)),
            token=token,
        )
        rejected_release_keys = _ports._INHERITED_REJECTION.matching_finding_keys(
            candidates, blob_oids,
        )
    elif candidates:
        print("GATE-INHERITED-REJECTION-EVIDENCE-UNAVAILABLE reason=environment_missing")
    _ports._CARRY.write_previous_context(
        previous, dispositions, args.previous_json,
        rejected_release_keys=rejected_release_keys,
        canonical_finding_key=_ports._GATE_ACTION.canonical_finding_key,
    )
    return 0

def _annotate_primary_audit_cli(argv: list[str], _ports: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-path", required=True)
    parser.add_argument("--previous-findings-path", required=True)
    args = parser.parse_args(argv)
    audit_path = Path(args.audit_path)
    if not audit_path.is_file(): return 0
    previous = _ports.load_previous_round_findings(
        path=args.previous_findings_path, repository_id=0, pr_number=0, run_id=0, run_attempt=0,
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    result = audit.get("result") if isinstance(audit, dict) else None
    findings = result.get("findings") if isinstance(result, dict) else None
    if isinstance(findings, list):
        relation_previous = {
            **previous,
            "findings": previous.get("open_findings", previous.get("findings", [])),
        }
        annotation = Outcome(ok=True)
        apply_finding_relation(annotation, findings, relation_previous)
        relation = annotation.finding_relation
        assert relation is not None
        relation = _consume_explicit_withdrawals(
            relation, relation_previous,
            result.get("withdrawals") if isinstance(result, dict) else None,
        )
        if (
            previous.get("available") is True
            and audit.get("verdict") in {"pass", "fail"}
            and result.get("verdict") == audit.get("verdict")
            and all(isinstance(item, dict) for item in findings)
        ):
            try:
                identity = Identity(
                    repository_id=int(os.environ["REPOSITORY_ID"]), head_sha=os.environ["HEAD_SHA"],
                    run_id=int(os.environ["RUN_ID"]), run_attempt=int(os.environ["RUN_ATTEMPT"]),
                    pr=int(os.environ["PR_NUMBER"]),
                )
            except (KeyError, ValueError):
                print("GATE-ANCHOR-CLOSURE-UNAVAILABLE reason=current_identity_missing")
            else:
                if _ports.validate_audit_identity(audit, identity):
                    print("GATE-ANCHOR-CLOSURE-UNAVAILABLE reason=current_audit_invalid")
                else:
                    anchor_updates = _ports._NEIGHBOR_EVIDENCE.collect_anchor_updates(
                        findings=relation_previous["findings"], repository=os.environ.get("GITHUB_REPOSITORY", ""),
                        previous_head_sha=previous.get("head_sha") or "",
                        current_head_sha=identity.head_sha,
                        token=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""),
                    )
                    reported = {
                        pointer for item in relation.get("items", []) if isinstance(item, dict)
                        for pointer in (item.get("id"), item.get("previous_finding_id"))
                        if isinstance(pointer, str)
                    }
                    if anchor_updates is not None:
                        open_findings = []
                        for item in relation.get("open_findings", []):
                            finding_id = item.get("id") if isinstance(item, dict) else None
                            if finding_id not in reported and finding_id in anchor_updates:
                                current_line = anchor_updates[finding_id]
                                if current_line is None:
                                    continue
                                item["line"] = current_line
                            open_findings.append(item)
                        relation["open_findings"] = open_findings
                    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
                    repository = os.environ.get("GITHUB_REPOSITORY", "")
                    carry_candidates = _ports._CARRY_BLOCKERS.candidates(
                        open_findings=relation.get("open_findings", []), current_findings=findings,
                        source_head_sha=previous.get("head_sha"), current_head_sha=identity.head_sha,
                        canonical_finding_key=_ports._GATE_ACTION.canonical_finding_key,
                    )
                    blob_oids = _ports._NEIGHBOR_EVIDENCE.collect_file_blob_oids(
                        repository=repository, current_head_sha=identity.head_sha,
                        head_paths=sorted(_ports._CARRY_BLOCKERS.blob_fetch_requests(carry_candidates)), token=token,
                    ) if carry_candidates and token and repository else {}
                    if carry_candidates and not (token and repository):
                        print("GATE-CARRY-BLOCKER-EVIDENCE-UNAVAILABLE reason=environment_missing")
                    carried = _ports._CARRY_BLOCKERS.audit_additions(
                        carry_candidates, _ports._CARRY_BLOCKERS.blockers(carry_candidates, blob_oids),
                    )
                    if carried:
                        findings.extend(finding for finding, _ in carried)
                        relation["carried_findings"] = [metadata for _, metadata in carried]
                        audit["verdict"] = result["verdict"] = "fail"
                        for metadata in relation["carried_findings"]:
                            print(f"GATE-CARRIED-FINDING id={metadata['id']} reason={metadata['evidence_reason']}")
        relation["history_status"] = "available" if previous["available"] else "unknown"
        relation["previous_head_sha"] = previous.get("head_sha")
        audit["finding_relation"] = relation
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0
