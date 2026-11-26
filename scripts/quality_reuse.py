#!/usr/bin/env python3
"""Build and fail-closed lookup of gate quality reuse receipts (stdlib only)."""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RECEIPT_SCHEMA_VERSION = 1
RECEIPT_KIND = "gate_quality_reuse"
RECEIPT_FILENAME = "quality-reuse-receipt.json"
API_ROOT = "https://api.github.com"
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
RECEIPT_FIELDS = {
    "kind", "schema_version", "repository_id", "pr_number", "head_sha", "base_sha",
    "merge_tree", "runner", "extra_runner_label_ci", "tier", "source_run_id",
    "caller_checks", "preflight_result",
}
Fetch = Callable[[str, float], bytes]


@dataclass(frozen=True)
class ReuseRequest:
    repository: str
    repository_id: int
    pr_number: int
    head_sha: str
    base_sha: str
    merge_tree: str
    runner: str
    extra_runner_label_ci: str
    tier: str
    current_run_id: int
    event_action: str


@dataclass(frozen=True)
class ReuseDecision:
    reusable: bool
    reason: str
    source_run_id: int | None = None
    caller_checks: str = ""
    preflight_result: str = ""


class LookupFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not any(char in value for char in "\r\n\x00")


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def artifact_name_for(repository_id: int, pr_number: int, head_sha: str) -> str:
    if not _positive_int(repository_id) or not _positive_int(pr_number) or not _valid_sha(head_sha):
        raise ValueError("repository_id, pr_number, and head_sha are invalid")
    return f"gate-quality-reuse-v1-{repository_id}-{pr_number}-{head_sha}"


def build_receipt(
    *, repository_id: int, pr_number: int, head_sha: str, base_sha: str,
    merge_tree: str, runner: str, extra_runner_label_ci: str, tier: str,
    source_run_id: int, caller_checks: str, preflight_result: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": RECEIPT_KIND,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "repository_id": repository_id,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "merge_tree": merge_tree,
        "runner": runner,
        "extra_runner_label_ci": extra_runner_label_ci,
        "tier": tier,
        "source_run_id": source_run_id,
        "caller_checks": caller_checks,
        "preflight_result": preflight_result,
    }
    error = _receipt_error(payload)
    if error:
        raise ValueError(error)
    return payload


def _receipt_error(payload: object) -> str:
    if not isinstance(payload, dict) or set(payload) != RECEIPT_FIELDS:
        return "receipt_fields_invalid"
    if payload["kind"] != RECEIPT_KIND or type(payload["schema_version"]) is not int or payload["schema_version"] != RECEIPT_SCHEMA_VERSION:
        return "receipt_version_invalid"
    for field in ("repository_id", "pr_number", "source_run_id"):
        if not _positive_int(payload[field]):
            return "receipt_integer_invalid"
    for field in ("head_sha", "base_sha", "merge_tree"):
        if not _valid_sha(payload[field]):
            return "receipt_sha_invalid"
    for field in ("runner", "tier", "caller_checks", "preflight_result"):
        if not _nonempty_string(payload[field]):
            return "receipt_string_invalid"
    if not isinstance(payload["extra_runner_label_ci"], str) or any(char in payload["extra_runner_label_ci"] for char in "\r\n\x00"):
        return "receipt_string_invalid"
    return ""


def validate_receipt(payload: object, expected: ReuseRequest) -> tuple[bool, str]:
    error = _receipt_error(payload)
    if error:
        return False, error
    expected_values = {
        "repository_id": expected.repository_id,
        "pr_number": expected.pr_number,
        "head_sha": expected.head_sha,
        "base_sha": expected.base_sha,
        "merge_tree": expected.merge_tree,
        "runner": expected.runner,
        "extra_runner_label_ci": expected.extra_runner_label_ci,
        "tier": expected.tier,
    }
    for field, value in expected_values.items():
        if payload[field] != value:
            return False, "receipt_mismatch"
    if payload["caller_checks"] != "passed":
        return False, "caller_checks_not_passed"
    if payload["preflight_result"] == "blocked":
        return False, "preflight_blocked"
    return True, "reusable"


def _json_bytes(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LookupFailure("json_invalid") from None


def _api_fetch(url: str, timeout: float) -> bytes:
    token = os.environ.get("GH_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _request_valid(request: ReuseRequest) -> bool:
    return (
        isinstance(request.repository, str)
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", request.repository) is not None
        and _positive_int(request.repository_id)
        and _positive_int(request.pr_number)
        and _positive_int(request.current_run_id)
        and all(_valid_sha(getattr(request, field)) for field in ("head_sha", "base_sha", "merge_tree"))
        and all(_nonempty_string(getattr(request, field)) for field in ("runner", "tier"))
        and isinstance(request.extra_runner_label_ci, str)
        and isinstance(request.event_action, str)
    )


def _pages(fetcher: Fetch, url: str, timeout: float):
    page = 1
    while True:
        separator = "&" if "?" in url else "?"
        try:
            raw = fetcher(f"{url}{separator}page={page}&per_page=100", timeout)
        except Exception:
            raise LookupFailure("api_error") from None
        payload = _json_bytes(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts", payload.get("jobs")), list):
            raise LookupFailure("api_response_invalid")
        items = payload.get("artifacts", payload.get("jobs"))
        yield items
        if len(items) < 100:
            return
        page += 1


def _receipt_from_zip(raw: bytes) -> object:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if names != [RECEIPT_FILENAME]:
                raise LookupFailure("artifact_contents_invalid")
            return _json_bytes(archive.read(RECEIPT_FILENAME))
    except zipfile.BadZipFile:
        raise LookupFailure("zip_invalid") from None


def _quality_success(fetcher: Fetch, repository: str, run_id: int, timeout: float) -> bool:
    encoded_repo = urllib.parse.quote(repository, safe="/")
    url = f"{API_ROOT}/repos/{encoded_repo}/actions/runs/{run_id}/jobs?filter=latest"
    jobs: list[object] = []
    for page in _pages(fetcher, url, timeout):
        jobs.extend(page)
    quality = []
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("name"), str):
            raise LookupFailure("api_response_invalid")
        if job["name"] == "quality":
            quality.append(job)
    return len(quality) == 1 and quality[0].get("conclusion") == "success"


def lookup_reusable_quality(
    request: ReuseRequest, fetcher: Fetch = _api_fetch, timeout: float = 10.0,
) -> ReuseDecision:
    if request.event_action != "ready_for_review":
        return ReuseDecision(False, "event_not_ready_for_review")
    if not _request_valid(request) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        return ReuseDecision(False, "invalid_request")
    try:
        name = artifact_name_for(request.repository_id, request.pr_number, request.head_sha)
        encoded_repo = urllib.parse.quote(request.repository, safe="/")
        listing = f"{API_ROOT}/repos/{encoded_repo}/actions/artifacts?name={urllib.parse.quote(name)}"
        artifacts: list[object] = []
        for page in _pages(fetcher, listing, float(timeout)):
            artifacts.extend(page)
        if any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in artifacts):
            raise LookupFailure("api_response_invalid")
        if any(item.get("expired") is not None and not isinstance(item.get("expired"), bool) for item in artifacts):
            raise LookupFailure("api_response_invalid")
        candidates = [item for item in artifacts if item["name"] == name and not item.get("expired", False)]
        if not candidates:
            return ReuseDecision(False, "artifact_not_found")
        saw_mismatch = False
        saw_other_reason = "quality_job_not_success"
        for artifact in candidates:
            artifact_id = artifact.get("id")
            workflow_run = artifact.get("workflow_run")
            if not _positive_int(artifact_id) or not isinstance(workflow_run, dict) or not _positive_int(workflow_run.get("id")):
                raise LookupFailure("api_response_invalid")
            run_id = workflow_run["id"]
            zip_url = f"{API_ROOT}/repos/{encoded_repo}/actions/artifacts/{artifact_id}/zip"
            try:
                zip_bytes = fetcher(zip_url, float(timeout))
            except Exception:
                raise LookupFailure("api_error") from None
            receipt = _receipt_from_zip(zip_bytes)
            if isinstance(receipt, dict) and _receipt_error(receipt) == "":
                if receipt["source_run_id"] != run_id:
                    raise LookupFailure("source_run_mismatch")
            valid, reason = validate_receipt(receipt, request)
            if reason == "receipt_mismatch":
                saw_mismatch = True
                continue
            if not valid:
                saw_other_reason = reason
                continue
            if run_id == request.current_run_id:
                saw_other_reason = "source_run_is_current"
                continue
            if not _quality_success(fetcher, request.repository, run_id, float(timeout)):
                saw_other_reason = "quality_job_not_success"
                continue
            return ReuseDecision(True, "reusable", run_id, receipt["caller_checks"], receipt["preflight_result"])
        if saw_mismatch:
            return ReuseDecision(False, "receipt_mismatch")
        return ReuseDecision(False, saw_other_reason)
    except LookupFailure as error:
        return ReuseDecision(False, error.reason)
    except Exception:
        return ReuseDecision(False, "lookup_error")


def _write_output(path: str | None, decision: ReuseDecision) -> None:
    if not path:
        return
    values = {
        "quality_reusable": str(decision.reusable).lower(),
        "reuse_reason": decision.reason,
        "reuse_source_run_id": str(decision.source_run_id) if decision.source_run_id is not None and decision.reusable else "",
        "reuse_caller_checks": decision.caller_checks if decision.reusable else "",
        "reuse_preflight_result": decision.preflight_result if decision.reusable else "",
    }
    with Path(path).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-receipt")
    for flag in ("repository-id", "pr-number", "head-sha", "base-sha", "merge-tree", "runner", "extra-runner-label-ci", "tier", "source-run-id", "caller-checks", "preflight-result", "out"):
        build.add_argument(f"--{flag}", required=True, type=int if flag in ("repository-id", "pr-number", "source-run-id") else str)
    lookup = commands.add_parser("lookup")
    for flag in ("repository", "repository-id", "pr-number", "head-sha", "base-sha", "merge-tree", "runner", "extra-runner-label-ci", "tier", "current-run-id", "event-action"):
        lookup.add_argument(f"--{flag}", required=True, type=int if flag in ("repository-id", "pr-number", "current-run-id") else str)
    lookup.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT") or None)
    lookup.add_argument("--timeout", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None, fetcher: Fetch = _api_fetch) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "build-receipt":
        payload = build_receipt(
            repository_id=args.repository_id, pr_number=args.pr_number, head_sha=args.head_sha,
            base_sha=args.base_sha, merge_tree=args.merge_tree, runner=args.runner,
            extra_runner_label_ci=args.extra_runner_label_ci, tier=args.tier,
            source_run_id=args.source_run_id, caller_checks=args.caller_checks,
            preflight_result=args.preflight_result,
        )
        Path(args.out).write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        return 0
    request = ReuseRequest(
        repository=args.repository, repository_id=args.repository_id, pr_number=args.pr_number,
        head_sha=args.head_sha, base_sha=args.base_sha, merge_tree=args.merge_tree,
        runner=args.runner, extra_runner_label_ci=args.extra_runner_label_ci, tier=args.tier,
        current_run_id=args.current_run_id, event_action=args.event_action,
    )
    decision = lookup_reusable_quality(request, fetcher, args.timeout)
    try:
        _write_output(args.github_output, decision)
    except OSError:
        decision = ReuseDecision(False, "output_error")
    source = str(decision.source_run_id) if decision.reusable else "none"
    print(f"GATE-QUALITY-REUSE-V1 reusable={str(decision.reusable).lower()} reason={decision.reason} source_run_id={source}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"quality-reuse: {error}", file=sys.stderr)
        raise SystemExit(2)
