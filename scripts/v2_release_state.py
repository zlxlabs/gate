#!/usr/bin/env python3
"""Read the remote v2 release state and report an overdue tag."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.v2_tag_guard import _remote_tag_sha


DEFAULT_THRESHOLD_HOURS = 6
TAG_REF = "refs/tags/v2"
MAIN_REF = "refs/heads/main"
QUERY_FAILED = "V2-RELEASE-STATE-QUERY-FAILED"


def _ls_remote(remote: str, *refs: str) -> tuple[str | None, int]:
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote, *refs],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None, 127
    if result.returncode != 0:
        return None, result.returncode
    return result.stdout, 0


def _v2_sha(output: str) -> str | None:
    deref_ref = f"{TAG_REF}^{{}}"
    dereferenced = "\n".join(
        line.replace(deref_ref, TAG_REF)
        for line in output.splitlines()
        if len(line.split()) == 2 and line.split()[1] == deref_ref
    )
    if dereferenced:
        return _remote_tag_sha(dereferenced)
    return _remote_tag_sha(output)


def _main_sha(output: str) -> str | None:
    matches = [
        fields[0]
        for line in output.splitlines()
        if len(fields := line.split()) == 2 and fields[1] == MAIN_REF
    ]
    return matches[0] if len(matches) == 1 else None


def _commit_timestamp(sha: str) -> tuple[int | None, int]:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", sha],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None, 127
    if result.returncode != 0:
        return None, result.returncode
    try:
        return int(result.stdout.strip()), 0
    except ValueError:
        return None, 0


def _oldest_unreleased_sha(v2_sha: str, main_sha: str) -> tuple[str | None, int]:
    try:
        result = subprocess.run(
            ["git", "rev-list", f"{v2_sha}..{main_sha}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None, 127
    if result.returncode != 0:
        return None, result.returncode
    lines = result.stdout.splitlines()
    return (lines[-1] if lines else ""), 0


def _query_failed(exit_code: int) -> int:
    print(f"{QUERY_FAILED}: exit={exit_code}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", required=True)
    parser.add_argument(
        "--threshold-hours",
        "--lag-hours",
        dest="threshold_hours",
        type=float,
        default=DEFAULT_THRESHOLD_HOURS,
    )
    args = parser.parse_args(argv)

    v2_output, v2_status = _ls_remote(args.remote, TAG_REF, f"{TAG_REF}^{{}}")
    main_output, main_status = _ls_remote(args.remote, MAIN_REF)
    if v2_output is None:
        return _query_failed(v2_status)
    if main_output is None:
        return _query_failed(main_status)

    v2_sha = _v2_sha(v2_output)
    main_sha = _main_sha(main_output)
    if v2_sha is None or main_sha is None:
        return _query_failed(0)

    oldest_unreleased_sha, rev_list_status = _oldest_unreleased_sha(v2_sha, main_sha)
    if oldest_unreleased_sha is None:
        return _query_failed(rev_list_status)
    if not oldest_unreleased_sha:
        return 0

    commit_timestamp, timestamp_status = _commit_timestamp(oldest_unreleased_sha)
    if commit_timestamp is None:
        return _query_failed(timestamp_status)
    lag_seconds = max(0, int(time.time()) - commit_timestamp)
    if lag_seconds > args.threshold_hours * 3600:
        print(
            f"v2 behind main: v2_sha={v2_sha} main_sha={main_sha} "
            f"main_lead_hours={lag_seconds / 3600:.2f} "
            "(oldest unreleased commit age)"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
