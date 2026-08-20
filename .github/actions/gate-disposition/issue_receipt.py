#!/usr/bin/env python3
"""Issue and revoke immutable protected disposition artifacts.

The workflow owns GitHub authorization and audit/evidence retrieval.  This
producer deliberately accepts those verified values through argv, environment,
or a JSON stdin envelope and performs no network access.  The only evidence
allowlist is ``blob:<40-hex-git-sha>``: the manifest names a local downloaded
copy, its raw SHA-256, and the producer recomputes the Git blob SHA-1 before
signing.  Unverifiable strings, URLs, commits, and artifacts are rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
P1_SEVERITIES = frozenset({"major", "blocker"})
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _derive_epoch(scope: dict[str, Any]) -> str:
    return _sha256_json(scope)


def _git_blob_sha(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw).hexdigest()


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


def _audit_findings(audit: Any) -> list[dict[str, Any]]:
    if not isinstance(audit, dict) or not isinstance(audit.get("result"), dict):
        raise ValueError("canonical audit result is missing")
    findings = audit["result"].get("findings")
    if not isinstance(findings, list) or not all(isinstance(finding, dict) for finding in findings):
        raise ValueError("canonical audit findings must be an array of objects")
    return findings


def _verify_audit_identity(
    audit: dict[str, Any], *, scope: dict[str, Any], primary_run_id: str,
    primary_run_attempt: int,
) -> None:
    expected = {
        "repository_id": scope["repository_id"],
        "pr": scope["pr_number"],
        "base_sha": scope["base_sha"],
        "head_sha": scope["head_sha"],
        "diff_digest": scope["diff_digest"],
        "policy_version": scope["policy_version"],
        "policy_digest": scope["policy_digest"],
        "tier": scope["tier"],
        "effective_tier": scope["effective_tier"],
        "infra_classifier_version": scope["infra_classifier_version"],
        "infra_diff": scope["infra_diff"],
        "caller_sha": scope["caller_sha"],
        "reusable_workflow_sha": scope["reusable_workflow_sha"],
        "run_id": int(primary_run_id) if primary_run_id.isdigit() else primary_run_id,
        "run_attempt": primary_run_attempt,
    }
    aliases = {"pr": ("pr", "pr_number"), "run_id": ("run_id", "primary_run_id")}
    for field, expected_value in expected.items():
        candidates = aliases.get(field, (field,))
        present = next((candidate for candidate in candidates if candidate in audit), None)
        if present is None:
            raise ValueError(f"audit missing required binding field {field}")
        if audit[present] != expected_value:
            raise ValueError(f"audit {present} does not match requested binding")


def _read_scope(args: argparse.Namespace, envelope: dict[str, Any], *, repository_id: str, pr_number: int, head_sha: str, diff_digest: str) -> dict[str, Any]:
    raw_scope = _required(args, envelope, "scope_json", "DISPOSITION_SCOPE_JSON")
    scope = json.loads(raw_scope) if isinstance(raw_scope, str) else raw_scope
    required = {
        "repository_id", "pr_number", "base_sha", "head_sha", "diff_digest",
        "policy_version", "policy_digest", "tier", "effective_tier",
        "infra_classifier_version", "infra_diff", "caller_sha", "reusable_workflow_sha",
    }
    if not isinstance(scope, dict) or set(scope) != required:
        raise ValueError("scope_json must contain the complete canonical Scope fields")
    if scope["repository_id"] != int(repository_id) or scope["pr_number"] != pr_number:
        raise ValueError("scope repository/PR does not match current control target")
    if scope["head_sha"] != head_sha or scope["diff_digest"] != diff_digest:
        raise ValueError("scope head/diff does not match current control target")
    if type(scope["infra_diff"]) is not bool:
        raise ValueError("scope infra_diff must be a bool")
    epoch = str(_required(args, envelope, "epoch", "DISPOSITION_EPOCH"))
    if not re.fullmatch(r"[0-9a-f]{64}", epoch) or epoch != _derive_epoch(scope):
        raise ValueError("epoch does not match derive_epoch(scope)")
    return scope


def _read_evidence_manifest(args: argparse.Namespace, envelope: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    manifest_path = Path(_required(args, envelope, "evidence_manifest_path", "DISPOSITION_EVIDENCE_MANIFEST_PATH"))
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, list) or not raw_manifest:
        raise ValueError("evidence manifest must be a non-empty array")
    normalized: list[dict[str, str]] = []
    for item in raw_manifest:
        if not isinstance(item, dict) or set(item) != {"type", "ref", "path", "sha256"}:
            raise ValueError("evidence manifest item must have type/ref/path/sha256")
        if item["type"] != "blob" or not isinstance(item["ref"], str) or not re.fullmatch(r"blob:[0-9a-f]{40}", item["ref"]):
            raise ValueError("unsupported evidence ref; only blob:<git-sha> is allowlisted")
        if not isinstance(item["path"], str) or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("evidence manifest path or sha256 is malformed")
        raw = Path(item["path"]).read_bytes()
        actual_sha256 = _sha256_bytes(raw)
        if actual_sha256 != item["sha256"]:
            raise ValueError(f"evidence content digest mismatch: {item['ref']}")
        if _git_blob_sha(raw) != item["ref"][5:]:
            raise ValueError(f"evidence git blob digest mismatch: {item['ref']}")
        normalized.append({"type": "blob", "ref": item["ref"], "sha256": actual_sha256})
    refs = _value(args, envelope, "evidence_refs", "DISPOSITION_EVIDENCE_REFS")
    if isinstance(refs, str):
        refs = [item for item in refs.splitlines() if item]
    expected_refs = [item["ref"] for item in normalized]
    if refs != expected_refs:
        raise ValueError("evidence refs do not match the verified manifest")
    return normalized, _sha256_json(normalized)


def _receipt_fields(args: argparse.Namespace, envelope: dict[str, Any]) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    audit_path = Path(_required(args, envelope, "audit_path", "DISPOSITION_AUDIT_PATH"))
    raw_audit = audit_path.read_bytes()
    audit_digest = _sha256_bytes(raw_audit)
    requested_digest = _value(args, envelope, "audit_digest", "DISPOSITION_AUDIT_DIGEST")
    if requested_digest is not None and requested_digest != audit_digest:
        raise ValueError("dispatch audit_digest does not match raw audit bytes")
    audit = json.loads(raw_audit.decode("utf-8"))

    repository_id = str(_required(args, envelope, "repository_id", "GITHUB_REPOSITORY_ID"))
    pr_number = _positive_int(_required(args, envelope, "pr_number", "PR_NUMBER"), "pr_number")
    head_sha = str(_required(args, envelope, "head_sha", "DISPOSITION_HEAD_SHA"))
    diff_digest = str(_required(args, envelope, "diff_digest", "DISPOSITION_DIFF_DIGEST"))
    primary_run_id = str(_required(args, envelope, "primary_run_id", "DISPOSITION_PRIMARY_RUN_ID"))
    primary_run_attempt = _positive_int(
        _required(args, envelope, "primary_run_attempt", "DISPOSITION_PRIMARY_RUN_ATTEMPT"),
        "primary_run_attempt",
    )
    finding_id = str(_required(args, envelope, "finding_id", "DISPOSITION_FINDING_ID"))
    issuer_login = str(_required(args, envelope, "issuer_login", "GITHUB_ACTOR"))
    issuer_user_id = str(_required(args, envelope, "issuer_user_id", "GITHUB_ACTOR_ID"))
    pr_author_login = str(_required(args, envelope, "pr_author_login", "DISPOSITION_PR_AUTHOR_LOGIN"))
    if issuer_login == pr_author_login:
        raise ValueError("issuer must differ from PR author; protected approver was not available")
    control_run_id = str(_required(args, envelope, "control_run_id", "GITHUB_RUN_ID"))
    approval_ref = str(_required(args, envelope, "approval_ref", "DISPOSITION_APPROVAL_REF"))
    issued_at = str(_required(args, envelope, "issued_at", "DISPOSITION_ISSUED_AT"))
    expires_at = str(_required(args, envelope, "expires_at", "DISPOSITION_EXPIRES_AT"))
    nonce = _safe_component(str(_required(args, envelope, "nonce", "DISPOSITION_NONCE")), "nonce")
    reason = str(_required(args, envelope, "reason", "DISPOSITION_REASON"))
    disposition = str(_value(args, envelope, "disposition", "DISPOSITION_KIND") or "false-positive")
    if disposition not in {"false-positive", "accepted", "wont-fix", "fixed"}:
        raise ValueError("unknown disposition")
    if not reason.strip():
        raise ValueError("reason must be non-empty")
    if not head_sha or not diff_digest:
        raise ValueError("head_sha and diff_digest must be non-empty")
    scope = _read_scope(
        args, envelope, repository_id=repository_id, pr_number=pr_number,
        head_sha=head_sha, diff_digest=diff_digest,
    )
    _verify_audit_identity(audit, scope=scope, primary_run_id=primary_run_id, primary_run_attempt=primary_run_attempt)
    matching = [finding for finding in _audit_findings(audit) if finding.get("id") == finding_id]
    if len(matching) != 1:
        raise ValueError("finding_id must identify exactly one canonical audit finding")
    if matching[0].get("severity") not in P1_SEVERITIES:
        raise ValueError("finding_id must identify a P1 finding")
    evidence_manifest, evidence_manifest_digest = _read_evidence_manifest(args, envelope)
    supplied_manifest = _value(args, envelope, "evidence_manifest_digest", "DISPOSITION_EVIDENCE_MANIFEST_DIGEST")
    if supplied_manifest is not None and supplied_manifest != evidence_manifest_digest:
        raise ValueError("evidence manifest digest does not match evidence refs")
    fields = {
        "schema_version": SCHEMA_VERSION,
        "disposition": disposition,
        "repository_id": repository_id,
        "pr_number": pr_number,
        "epoch": _derive_epoch(scope),
        "head_sha": head_sha,
        "diff_digest": diff_digest,
        "primary_run_id": primary_run_id,
        "primary_run_attempt": primary_run_attempt,
        "audit_digest": audit_digest,
        "finding_id": finding_id,
        "issuer_login": issuer_login,
        "issuer_user_id": issuer_user_id,
        "control_run_id": control_run_id,
        "approval_ref": approval_ref,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "evidence_manifest_digest": evidence_manifest_digest,
        "scope": scope,
    }
    return fields, reason, evidence_manifest


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
    fields, reason, evidence_manifest = _receipt_fields(args, envelope)
    receipt_digest = _sha256_json(fields)
    payload = {
        **fields,
        "kind": "gate-disposition-receipt-v1",
        "reason": reason,
        "evidence_refs": [item["ref"] for item in evidence_manifest],
        "evidence_manifest": evidence_manifest,
        "receipt_digest": receipt_digest,
    }
    epoch = _safe_component(fields["epoch"], "epoch")
    digest_prefix = fields["audit_digest"][:12]
    nonce = _safe_component(fields["nonce"], "nonce")
    name = f"gate-disposition-receipt-v1-{epoch}-{digest_prefix}-{nonce}"
    output = Path(_required(args, envelope, "output_dir", "DISPOSITION_OUTPUT_DIR")) / name
    changed = _write_immutable(output, _canonical_json(payload))
    print(json.dumps({"artifact": name, "path": str(output), "receipt_digest": receipt_digest, "written": changed}, sort_keys=True))
    return 0


def revoke(args: argparse.Namespace, envelope: dict[str, Any]) -> int:
    epoch = _safe_component(str(_required(args, envelope, "epoch", "DISPOSITION_EPOCH")), "epoch")
    nonce = _safe_component(str(_required(args, envelope, "nonce", "DISPOSITION_NONCE")), "nonce")
    fields = {
        "schema_version": SCHEMA_VERSION,
        "kind": "gate-disposition-revocation-v1",
        "nonce": nonce,
        "reason": str(_required(args, envelope, "reason", "DISPOSITION_REASON")),
        "actor": str(_required(args, envelope, "actor", "GITHUB_ACTOR")),
        "revoked_at": str(_required(args, envelope, "revoked_at", "DISPOSITION_REVOKED_AT")),
        "evidence_ref": str(_required(args, envelope, "evidence_ref", "DISPOSITION_EVIDENCE_REF")),
    }
    if not all(fields[field].strip() for field in ("reason", "actor", "revoked_at", "evidence_ref")):
        raise ValueError("revocation reason, actor, time, and evidence_ref must be non-empty")
    name = f"gate-disposition-revocation-v1-{epoch}-{nonce}"
    output = Path(_required(args, envelope, "output_dir", "DISPOSITION_OUTPUT_DIR")) / name
    changed = _write_immutable(output, _canonical_json(fields))
    print(json.dumps({"artifact": name, "path": str(output), "written": changed}, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("issue", "revoke"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--input-stdin", action="store_true")
        sub.add_argument("--output-dir")
        sub.add_argument("--epoch")
        sub.add_argument("--nonce")
        sub.add_argument("--reason")
        if command == "issue":
            sub.add_argument("--audit-path")
            for name in (
                "repository-id", "pr-number", "head-sha", "diff-digest", "primary-run-id",
                "primary-run-attempt", "finding-id", "issuer-login", "issuer-user-id",
                "control-run-id", "approval-ref", "issued-at", "expires-at",
                "audit-digest", "evidence-manifest-digest", "disposition", "scope-json",
                "pr-author-login", "evidence-manifest-path",
            ):
                sub.add_argument(f"--{name}", dest=name.replace("-", "_"))
            sub.add_argument("--evidence-ref", dest="evidence_refs", action="append")
        else:
            sub.add_argument("--actor")
            sub.add_argument("--revoked-at")
            sub.add_argument("--evidence-ref", dest="evidence_ref")
    return parser


def main() -> int:
    args = _parser().parse_args()
    envelope = _read_stdin_envelope(args.input_stdin)
    if args.command == "issue":
        return issue(args, envelope)
    return revoke(args, envelope)


if __name__ == "__main__":
    raise SystemExit(main())
