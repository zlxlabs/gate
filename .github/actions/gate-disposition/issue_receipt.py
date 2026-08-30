#!/usr/bin/env python3
"""Issue immutable disposition artifacts from a canonical primary audit.

audit_digest is SHA-256 of the stable audit subset (scope fields + sorted
finding id/severity/file/line + verdict) via convergence.canonical_audit_digest.
The audit file's raw bytes are not stable across reruns (duration/tokens/
timestamps), so they are not hashed here.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


P1_SEVERITIES = frozenset({"major", "blocker"})
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _load_convergence():
    path = Path(__file__).resolve().parents[1] / "gate-aggregator" / "convergence.py"
    spec = importlib.util.spec_from_file_location("gate_convergence_from_disposition", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"convergence module is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_CONVERGENCE = _load_convergence()
SCHEMA_VERSION = _CONVERGENCE.DISPOSITION_RECEIPT_SCHEMA_VERSION


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _derive_epoch(scope: dict[str, Any]) -> str:
    return _sha256_json(scope)


def _read_stdin_envelope(enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {}
    raw = sys.stdin.buffer.read()
    envelope = json.loads(raw.decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("stdin envelope must be a JSON object")
    return envelope


def _value(args: argparse.Namespace, envelope: dict[str, Any], name: str, env_name: str | None = None) -> Any:
    value = getattr(args, name, None)
    if value is not None:
        return value
    if name in envelope:
        return envelope[name]
    if env_name:
        return os.environ.get(env_name)
    return None


def _required(args: argparse.Namespace, envelope: dict[str, Any], name: str, env_name: str | None = None) -> Any:
    value = _value(args, envelope, name, env_name)
    if value is None or (isinstance(value, str) and not value):
        raise ValueError(f"{name} is required")
    return value


def _safe_component(name: str, field: str) -> str:
    if not isinstance(name, str) or not SAFE_COMPONENT.fullmatch(name):
        raise ValueError(f"{field} must be a safe artifact-name component")
    return name


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _approved_at(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("approved_at is required")
    if "T" not in value:
        raise ValueError("approved_at must include a time-of-day component")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("approved_at must be an ISO-8601 timestamp") from exc
    return value


def _audit_findings(audit: Any) -> list[dict[str, Any]]:
    if not isinstance(audit, dict) or not isinstance(audit.get("result"), dict):
        raise ValueError("canonical audit result is missing")
    findings = audit["result"].get("findings")
    if not isinstance(findings, list) or not all(isinstance(finding, dict) for finding in findings):
        raise ValueError("canonical audit findings must be an array of objects")
    return findings


def _read_scope(args: argparse.Namespace, envelope: dict[str, Any], *, repository_id: str, pr_number: int, head_sha: str) -> dict[str, Any]:
    raw_scope = _required(args, envelope, "scope_json", "DISPOSITION_SCOPE_JSON")
    scope = json.loads(raw_scope) if isinstance(raw_scope, str) else raw_scope
    required = {
        "repository_id", "pr_number", "base_sha", "head_sha", "diff_digest",
        "policy_version", "policy_digest", "tier", "caller_sha", "reusable_workflow_sha",
    }
    if not isinstance(scope, dict) or set(scope) != required:
        raise ValueError("scope_json must contain the complete canonical Scope fields")
    if scope["repository_id"] != int(repository_id) or scope["pr_number"] != pr_number:
        raise ValueError("scope repository/PR does not match current control target")
    if scope["head_sha"] != head_sha:
        raise ValueError("scope head does not match current control target")
    return scope


def _receipt_fields(args: argparse.Namespace, envelope: dict[str, Any]) -> dict[str, Any]:
    audit_path = Path(_required(args, envelope, "audit_path", "DISPOSITION_AUDIT_PATH"))
    raw_audit = audit_path.read_bytes()
    audit = json.loads(raw_audit.decode("utf-8"))
    audit_digest = _CONVERGENCE.canonical_audit_digest(audit)

    repository_id = str(_required(args, envelope, "repository_id", "GITHUB_REPOSITORY_ID"))
    pr_number = _positive_int(_required(args, envelope, "pr_number", "PR_NUMBER"), "pr_number")
    head_sha = str(_required(args, envelope, "head_sha", "DISPOSITION_HEAD_SHA"))
    finding_id = str(_required(args, envelope, "finding_id", "DISPOSITION_FINDING_ID"))
    reason = str(_required(args, envelope, "reason", "DISPOSITION_REASON"))
    approver = str(_required(args, envelope, "approver", "DISPOSITION_APPROVER"))
    approver_id = _positive_int(
        _required(args, envelope, "approver_id", "DISPOSITION_APPROVER_ID"), "approver_id",
    )
    approved_at = _approved_at(_required(args, envelope, "approved_at", "DISPOSITION_APPROVED_AT"))
    if not reason.strip():
        raise ValueError("reason must be non-empty")
    if not approver.strip():
        raise ValueError("approver must be non-empty")
    if not head_sha:
        raise ValueError("head_sha must be non-empty")
    scope = _read_scope(
        args, envelope, repository_id=repository_id, pr_number=pr_number,
        head_sha=head_sha,
    )
    matching = [finding for finding in _audit_findings(audit) if finding.get("id") == finding_id]
    if len(matching) != 1:
        raise ValueError("finding_id must identify exactly one canonical audit finding")
    if matching[0].get("severity") not in P1_SEVERITIES:
        raise ValueError("finding_id must identify a P1 finding")
    fields = {
        "schema_version": SCHEMA_VERSION,
        "disposition": "false-positive",
        "repository_id": repository_id,
        "pr_number": pr_number,
        "epoch": _derive_epoch(scope),
        "head_sha": head_sha,
        "audit_digest": audit_digest,
        "finding_id": finding_id,
        "reason": reason,
        "approver": approver,
        "approver_id": approver_id,
        "approved_at": approved_at,
    }
    return fields


def _write_immutable(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable artifact conflict: {path.name}")
        return False
    with path.open("xb") as output:
        output.write(payload)
    return True


def issue(args: argparse.Namespace, envelope: dict[str, Any]) -> int:
    fields = _receipt_fields(args, envelope)
    payload = {
        **fields,
        "kind": _CONVERGENCE.DISPOSITION_RECEIPT_KIND,
    }
    receipt = _CONVERGENCE.DispositionReceipt(
        **{key: fields[key] for key in _CONVERGENCE.DispositionReceipt.__dataclass_fields__}
    )
    _safe_component(fields["epoch"], "epoch")
    _safe_component(fields["finding_id"], "finding_id")
    name = _CONVERGENCE.disposition_receipt_artifact_name(receipt)
    output = Path(_required(args, envelope, "output_dir", "DISPOSITION_OUTPUT_DIR")) / name
    changed = _write_immutable(output, _canonical_json(payload))
    print(json.dumps({"artifact": name, "path": str(output), "written": changed}, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True).add_parser("issue")
    sub.add_argument("--input-stdin", action="store_true")
    sub.add_argument("--output-dir")
    sub.add_argument("--reason")
    sub.add_argument("--audit-path")
    for name in (
        "repository-id", "pr-number", "head-sha", "finding-id", "scope-json",
        "approver", "approver-id", "approved-at",
    ):
        sub.add_argument(f"--{name}", dest=name.replace("-", "_"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    envelope = _read_stdin_envelope(args.input_stdin)
    return issue(args, envelope)


if __name__ == "__main__":
    raise SystemExit(main())
