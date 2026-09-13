#!/usr/bin/env python3
"""Decision gate for the guarded v2 tag promotion workflow."""

from __future__ import annotations

import argparse
from pathlib import Path


HOLD_MARKER = "[v2-tag-sync:hold]"
HOLD_FILE = ".github/v2-tag-sync.hold"


def evaluate_advance(
    enabled: str, commit_message: str, hold_file: str | Path = HOLD_FILE
) -> tuple[bool, str]:
    if enabled != "true":
        return False, "v2 tag sync disabled; set V2_TAG_SYNC_ENABLED=true after migration"
    if HOLD_MARKER in commit_message:
        return False, f"v2 tag sync held by commit message marker: {HOLD_MARKER}"
    path = Path(hold_file)
    try:
        path.lstat()
    except FileNotFoundError:
        return True, "v2 tag sync enabled and no breaker signal found"
    except OSError as err:
        return (
            False,
            f"v2 tag sync held: breaker file query failed: {hold_file} ({type(err).__name__}: {err})",
        )
    return False, f"v2 tag sync held by breaker file: {hold_file}"


def should_advance(
    enabled: str, commit_message: str, hold_file: str | Path = HOLD_FILE
) -> bool:
    """Return whether this commit is allowed to advance the moving tag."""

    allowed, _ = evaluate_advance(enabled, commit_message, hold_file)
    return allowed


def _breaker_active(commit_message: str, hold_file: str | Path) -> bool:
    allowed, _ = evaluate_advance("true", commit_message, hold_file)
    return not allowed


def _remote_tag_sha(remote_result: str) -> str | None:
    direct_shas = []
    for line in remote_result.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "refs/tags/v2":
            direct_shas.append(fields[0])
    return direct_shas[0] if len(direct_shas) == 1 else None


def verify_remote_tag(
    intended_sha: str, push_exit_code: int, remote_result: str
) -> bool:
    """Verify the remote ref, regardless of the local push exit status."""

    remote_sha = _remote_tag_sha(remote_result)
    if remote_sha is None:
        print(
            "remote v2 verification failed: refs/tags/v2 was not returned "
            f"(push exit code {push_exit_code})"
        )
        return False
    if remote_sha != intended_sha:
        print(
            "remote v2 verification failed: "
            f"expected {intended_sha}, found {remote_sha} "
            f"(push exit code {push_exit_code})"
        )
        return False
    print(
        f"remote v2 tag verified at {remote_sha} "
        f"(push exit code {push_exit_code})"
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enabled", required=True)
    parser.add_argument("--commit-message", required=True)
    parser.add_argument("--hold-file", default=HOLD_FILE)
    args = parser.parse_args(argv)
    allowed, message = evaluate_advance(args.enabled, args.commit_message, args.hold_file)
    print(message)
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
