#!/usr/bin/env python3
"""v2-tag-sync lag quadruplet and threshold report (gate#189 / gate#192)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STUCK_SCHEMA = "v2-stuck-verified-v1"


def parse_ls_remote_sha(output: str, wanted_ref: str) -> str:
    found = [line.split()[0] for line in output.splitlines() if line.split()[-1:] == [wanted_ref] and len(line.split()) == 2]
    if len(found) != 1:
        raise ValueError(f"git ls-remote did not return exactly one {wanted_ref}: {output!r}")
    return found[0]


def load_stuck_verified_state(path: Path) -> int:
    if not path.is_file():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    missed = raw.get("missed_cycles")
    if raw.get("schema") != STUCK_SCHEMA or type(missed) is not int or missed < 0:
        raise ValueError(f"stuck state schema mismatch: {raw!r}")
    return missed


def save_stuck_verified_state(path: Path, missed_cycles: int) -> None:
    path.write_text(
        json.dumps({"missed_cycles": missed_cycles, "schema": STUCK_SCHEMA}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def evaluate_v2_lag(
    *,
    v2_sha: str,
    main_sha: str,
    behind_main: int,
    age_h: int,
    move: str,
    target_sha: str,
    event_name: str,
    previous_missed: int,
    lag_hours: int,
    lag_main_commits: int | None,
    stuck_threshold: int,
) -> tuple[str, list[str], int]:
    if not target_sha or target_sha == v2_sha:
        missed = 0
    elif event_name == "schedule":
        missed = previous_missed + 1
    else:
        missed = previous_missed
    has_newer_candidate = bool(target_sha) and target_sha != v2_sha
    reasons: list[str] = []
    if has_newer_candidate and age_h >= lag_hours:
        reasons.append("age_h")
    if has_newer_candidate and lag_main_commits is not None and behind_main >= lag_main_commits:
        reasons.append("behind_main")
    if move == "false" and target_sha and target_sha != v2_sha:
        reasons.append("newer_target_without_move")
    if missed >= stuck_threshold:
        reasons.append("stuck_verified_cycles")
    summary = (
        "## v2 lag\n\n"
        f"- v2_sha: {v2_sha}\n- main_sha: {main_sha}\n"
        f"- behind_main: {behind_main}\n- age_h: {age_h}\n"
        f"- move: {move or '(none)'}\n- target_sha: {target_sha or '(none)'}\n"
        f"- missed_cycles: {missed}\n- reasons: {', '.join(reasons) if reasons else '(none)'}\n"
    )
    return summary, reasons, missed


def main(argv: list[str] | None = None) -> int:
    env = os.environ.get
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-ls-remote", required=True)
    parser.add_argument("--main-ls-remote", required=True)
    parser.add_argument("--state-path", default="v2-stuck-verified.json")
    parser.add_argument("--summary-path", default=env("GITHUB_STEP_SUMMARY", ""))
    parser.add_argument("--move", default=env("MOVE", ""))
    parser.add_argument("--target-sha", default=env("TARGET_SHA", ""))
    parser.add_argument("--event-name", default=env("EVENT_NAME", ""))
    parser.add_argument("--lag-hours", default=env("V2_LAG_HOURS", "6"))
    parser.add_argument("--lag-main-commits", default=env("V2_LAG_MAIN_COMMITS", ""))
    parser.add_argument("--stuck-cycles", default=env("V2_STUCK_VERIFIED_CYCLES", "2"))
    parser.add_argument("--now", type=int, default=None)
    args = parser.parse_args(argv)
    if not args.summary_path:
        print("GITHUB_STEP_SUMMARY or --summary-path is required", file=sys.stderr)
        return 2
    v2_sha = parse_ls_remote_sha(args.v2_ls_remote, "refs/tags/v2")
    main_sha = parse_ls_remote_sha(args.main_ls_remote, "refs/heads/main")
    committer = int(subprocess.check_output(["git", "log", "-1", "--format=%ct", v2_sha], text=True).strip())
    behind = int(subprocess.check_output(["git", "rev-list", "--count", f"{v2_sha}..{main_sha}"], text=True).strip())
    now = args.now if args.now is not None else int(time.time())
    lag_main = None if args.lag_main_commits == "" else int(args.lag_main_commits)
    state_path = Path(args.state_path)
    summary, reasons, missed = evaluate_v2_lag(
        v2_sha=v2_sha,
        main_sha=main_sha,
        behind_main=behind,
        age_h=max(0, (now - committer) // 3600),
        move=args.move,
        target_sha=args.target_sha,
        event_name=args.event_name,
        previous_missed=load_stuck_verified_state(state_path),
        lag_hours=int(args.lag_hours),
        lag_main_commits=lag_main,
        stuck_threshold=int(args.stuck_cycles),
    )
    with Path(args.summary_path).open("a", encoding="utf-8") as summary_file:
        summary_file.write(summary)
    save_stuck_verified_state(state_path, missed)
    if reasons:
        print(f"::error::v2 lag threshold exceeded: {', '.join(reasons)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
