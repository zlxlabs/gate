#!/usr/bin/env python3
"""Decision gate for the guarded v2 tag promotion workflow."""

from __future__ import annotations

import argparse


HOLD_MARKER = "[v2-tag-sync:hold]"


def should_advance(enabled: str, commit_message: str) -> bool:
    """Return whether this commit is allowed to advance the moving tag."""

    return enabled == "true" and HOLD_MARKER not in commit_message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enabled", required=True)
    parser.add_argument("--commit-message", required=True)
    args = parser.parse_args(argv)
    if args.enabled != "true":
        print("v2 tag sync disabled; set V2_TAG_SYNC_ENABLED=true after migration")
        return 1
    if HOLD_MARKER in args.commit_message:
        print(f"v2 tag sync held by commit marker {HOLD_MARKER}")
        return 1
    print("v2 tag sync enabled and no breaker marker found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
