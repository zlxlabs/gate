#!/usr/bin/env python3
"""Find the newest main commit exercised successfully by the canary gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


API_BASE = "https://api.github.com"
CANARY_REPOSITORY = "zlxlabs/ci-infra-canary"
CANARY_GATE_WORKFLOW_ID = "gate.yml"
GATE_WORKFLOW_REFERENCE = "zlxlabs/gate/.github/workflows/gate-v2.yml"
# Read from .github/workflows/gate-v2.yml:471: the reusable workflow job id is
# `primary` and it has no separate `name:`.  Actions may prefix it with the
# caller job name, so _is_primary_job also accepts the documented suffix form.
PRIMARY_JOB_ID = "primary"
RUNS_PAGE_SIZE = 100
JOBS_PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 60
SCHEDULE_INTERVAL_HOURS = 1
NO_ELIGIBLE_CANDIDATE_ALERT_AFTER_HOURS = 4 * SCHEDULE_INTERVAL_HOURS
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class EvidenceQueryError(RuntimeError):
    """The evidence source could not be queried or parsed."""


@dataclass(frozen=True)
class CandidateResult:
    sha: str
    eligible: bool
    reason: str
    run_id: str | None = None


@dataclass(frozen=True)
class PromotionResult:
    selected_sha: str | None
    checked: tuple[CandidateResult, ...]


@dataclass(frozen=True)
class GateShaScan:
    shas: tuple[str, ...] | None
    has_unrecognized_path: bool


ApiReader = Callable[[str], object]


def _validate_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise EvidenceQueryError(f"{label} is not a 40-character commit SHA")
    return value


def _api_json(endpoint: str) -> object:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise EvidenceQueryError("GITHUB_TOKEN is missing")
    request = urllib.request.Request(
        f"{API_BASE}/{endpoint}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise EvidenceQueryError(f"HTTP {status} for {endpoint}")
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise EvidenceQueryError(f"HTTP {exc.code} for {endpoint}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise EvidenceQueryError(f"network error for {endpoint}: {exc}") from exc
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EvidenceQueryError(f"invalid JSON for {endpoint}") from exc


def _mapping(value: object, endpoint: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EvidenceQueryError(f"invalid JSON shape for {endpoint}: expected object")
    return value


def _run_id(run: object, endpoint: str) -> str:
    mapping = _mapping(run, endpoint)
    value = mapping.get("id")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise EvidenceQueryError(f"invalid run id in {endpoint}")
    text = str(value)
    if not text.isdigit() or int(text) <= 0:
        raise EvidenceQueryError(f"invalid run id in {endpoint}")
    return text


def _load_runs(api_reader: ApiReader) -> list[object]:
    endpoint = (
        f"repos/{CANARY_REPOSITORY}/actions/workflows/"
        f"{CANARY_GATE_WORKFLOW_ID}/runs?per_page={RUNS_PAGE_SIZE}"
    )
    payload = _mapping(api_reader(endpoint), endpoint)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise EvidenceQueryError(f"invalid JSON shape for {endpoint}: workflow_runs")
    for run in runs:
        _run_id(run, endpoint)
    return runs


def _gate_shas(detail: dict[str, object], endpoint: str) -> GateShaScan:
    references = detail.get("referenced_workflows")
    if references is None:
        return GateShaScan(None, False)
    if not isinstance(references, list):
        raise EvidenceQueryError(
            f"invalid JSON shape for {endpoint}: referenced_workflows"
        )
    shas: list[str] = []
    has_unrecognized_path = False
    gate_path_prefix = f"{GATE_WORKFLOW_REFERENCE}@"
    for reference in references:
        if not isinstance(reference, dict):
            raise EvidenceQueryError(
                f"invalid JSON shape for {endpoint}: referenced_workflows entry"
            )
        path = reference.get("path")
        if not isinstance(path, str) or not path.startswith(GATE_WORKFLOW_REFERENCE):
            continue
        if not path.startswith(gate_path_prefix) or path == gate_path_prefix:
            has_unrecognized_path = True
            continue
        shas.append(_validate_sha(reference.get("sha"), "gate reference sha"))
    return GateShaScan(tuple(shas), has_unrecognized_path)


def _is_primary_job(name: object) -> bool:
    return name == PRIMARY_JOB_ID or (
        isinstance(name, str) and name.endswith(f" / {PRIMARY_JOB_ID}")
    )


def _jobs_for_run(run_id: str, api_reader: ApiReader) -> list[object]:
    endpoint = (
        f"repos/{CANARY_REPOSITORY}/actions/runs/{run_id}/jobs"
        f"?per_page={JOBS_PAGE_SIZE}"
    )
    payload = _mapping(api_reader(endpoint), endpoint)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise EvidenceQueryError(f"invalid JSON shape for {endpoint}: jobs")
    return jobs


def _candidate_result(
    candidate_sha: str, runs: list[object], api_reader: ApiReader
) -> CandidateResult:
    reasons: list[str] = []
    for run in runs:
        list_endpoint = (
            f"repos/{CANARY_REPOSITORY}/actions/workflows/"
            f"{CANARY_GATE_WORKFLOW_ID}/runs?per_page={RUNS_PAGE_SIZE}"
        )
        run_id = _run_id(run, list_endpoint)
        listed_conclusion = _mapping(run, list_endpoint).get("conclusion")
        if listed_conclusion is not None and listed_conclusion != "success":
            reasons.append(
                f"run {run_id}: conclusion={listed_conclusion!r}, required success"
            )
            continue
        detail_endpoint = f"repos/{CANARY_REPOSITORY}/actions/runs/{run_id}"
        detail = _mapping(api_reader(detail_endpoint), detail_endpoint)
        conclusion = detail.get("conclusion")
        if conclusion != "success":
            reasons.append(
                f"run {run_id}: conclusion={conclusion!r}, required success"
            )
            continue
        gate_scan = _gate_shas(detail, detail_endpoint)
        if gate_scan.shas is None:
            reasons.append(f"run {run_id}: referenced_workflows is absent")
            continue
        if gate_scan.has_unrecognized_path:
            reasons.append(
                f"run {run_id}: gate-v2 reference shape is unrecognized"
            )
            continue
        if not gate_scan.shas:
            reasons.append(f"run {run_id}: no gate-v2 reference found")
            continue
        if any(gate_sha != candidate_sha for gate_sha in gate_scan.shas):
            reasons.append(
                f"run {run_id}: gate-v2 references inconsistent "
                f"sha(s)={','.join(gate_scan.shas)}, expected every reference to be "
                f"{candidate_sha}"
            )
            continue
        jobs = _jobs_for_run(run_id, api_reader)
        primary_jobs = [
            job
            for job in jobs
            if isinstance(job, dict) and _is_primary_job(job.get("name"))
        ]
        if not primary_jobs:
            reasons.append(
                f"run {run_id}: primary job {PRIMARY_JOB_ID!r} is absent"
            )
            continue
        primary_conclusions = [job.get("conclusion") for job in primary_jobs]
        if all(conclusion == "success" for conclusion in primary_conclusions):
            return CandidateResult(
                candidate_sha, True, f"run {run_id}: gate-v2 and primary succeeded", run_id
            )
        if len(primary_conclusions) == 1 and primary_conclusions[0] == "skipped":
            reasons.append(f"run {run_id}: primary job conclusion=skipped, execution required")
        else:
            reasons.append(
                f"run {run_id}: primary job conclusions={primary_conclusions!r}, "
                "all primary jobs required success"
            )
    if not reasons:
        reasons.append("no canary run was returned")
    unique_reasons = list(dict.fromkeys(reasons))
    summary = "; ".join(unique_reasons[:4])
    if len(unique_reasons) > 4:
        summary += f"; {len(unique_reasons) - 4} more distinct reason(s)"
    return CandidateResult(candidate_sha, False, summary)


def select_latest_verified_commit(
    candidate_shas: list[str], api_reader: ApiReader | None = None
) -> PromotionResult:
    if not candidate_shas:
        raise EvidenceQueryError("candidate set is empty")
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in candidate_shas:
        candidate = _validate_sha(candidate, "candidate")
        if candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)
    api_reader = api_reader or _api_json
    response_cache: dict[str, object] = {}

    def read_once(endpoint: str) -> object:
        if endpoint not in response_cache:
            response_cache[endpoint] = api_reader(endpoint)
        return response_cache[endpoint]

    runs = _load_runs(read_once)
    checked: list[CandidateResult] = []
    for candidate in ordered:
        result = _candidate_result(candidate, runs, read_once)
        checked.append(result)
        if result.eligible:
            return PromotionResult(candidate, tuple(checked))
    return PromotionResult(None, tuple(checked))


def is_descendant_or_equal(current_sha: str, target_sha: str) -> bool:
    """Return whether target is current or a descendant of current."""

    current_sha = _validate_sha(current_sha, "current v2 sha")
    target_sha = _validate_sha(target_sha, "target sha")
    if current_sha == target_sha:
        return True
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", current_sha, target_sha],
        check=False,
    )
    return completed.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", action="append")
    parser.add_argument("--check-ancestor", nargs=2, metavar=("CURRENT", "TARGET"))
    parser.add_argument("--current-v2-commit-time")
    args = parser.parse_args(argv)
    if args.check_ancestor is not None:
        try:
            ok = is_descendant_or_equal(*args.check_ancestor)
        except EvidenceQueryError as exc:
            print(f"ancestor check failed: {exc}", file=sys.stderr)
            return 1
        if not ok:
            print("v2 promotion rejected: target is not a descendant of current v2", file=sys.stderr)
            return 1
        print("v2 promotion ancestry verified", file=sys.stderr)
        return 0
    try:
        result = select_latest_verified_commit(args.candidate or [])
    except EvidenceQueryError as exc:
        print(f"v2 promotion evidence query failed: {exc}", file=sys.stderr)
        return 1
    for candidate in result.checked:
        state = "eligible" if candidate.eligible else "ineligible"
        print(f"candidate={candidate.sha} {state}: {candidate.reason}", file=sys.stderr)
    if result.selected_sha is None:
        if args.current_v2_commit_time is not None:
            try:
                current_v2_commit_time = datetime.fromisoformat(
                    args.current_v2_commit_time.replace("Z", "+00:00")
                )
            except ValueError as exc:
                print(
                    f"v2 promotion evidence query failed: invalid current v2 commit time: {exc}",
                    file=sys.stderr,
                )
                return 1
            if current_v2_commit_time.tzinfo is None:
                print(
                    "v2 promotion evidence query failed: current v2 commit time must include a timezone",
                    file=sys.stderr,
                )
                return 1
            age = datetime.now(timezone.utc) - current_v2_commit_time
            if age > timedelta(hours=NO_ELIGIBLE_CANDIDATE_ALERT_AFTER_HOURS):
                age_hours = age.total_seconds() / 3600
                print(
                    "::error::no eligible canary-verified main commit after "
                    f"{age_hours:.1f}h; current v2 commit is older than the "
                    f"{NO_ELIGIBLE_CANDIDATE_ALERT_AFTER_HOURS}h threshold",
                    file=sys.stderr,
                )
                return 1
        print("no eligible canary-verified main commit; v2 tag will not move", file=sys.stderr)
    else:
        print(f"selected canary-verified main commit: {result.selected_sha}", file=sys.stderr)
        print(result.selected_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
