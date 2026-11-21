"""Follow-up treatment gate for primary review rounds (round >= 4).

Prints exactly ONE decision line on stdout for the gate-v2 inject step's
shell block:

    action=enable prev_head_sha=<40-hex>  export REVIEW_FOLLOWUP_MODE=treatment
                                          and REVIEW_PREV_HEAD_SHA
    action=skip reason=<token>            echo GATE-FOLLOWUP-SKIPPED reason=<token>
    action=none                           rounds 1-3: silent, full review path

Round counting reuses the existing PR round budget unit (canonical primary
rounds deduped by run id) over terminal rows with ``_eligible_primary_round``;
the previous canonical round's head_sha comes from the same rows. The carry
file is only validated here, never rewritten: review-primary receives the
very same path.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# User lock: rounds 1-3 stay full review; follow-up treatment starts at r >= 4.
MIN_FOLLOWUP_ROUND = 4
# Sibling-aggregator module name for the spec load below. Plain `import
# aggregate` would trip the entry-import allowlist scan (stdlib + scripts
# package only); sibling modules are loaded through importlib specs all over
# this directory (aggregate.py loads convergence.py the same way).
_AGGREGATE_MODULE_NAME = "gate_followup_aggregate"


def _load_aggregate():
    import importlib.util

    existing = sys.modules.get(_AGGREGATE_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        _AGGREGATE_MODULE_NAME, Path(__file__).with_name("aggregate.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prior_canonical_rows(rows: list[dict[str, Any]], run_id: int) -> list[dict[str, Any]]:
    return sorted(
        (
            row for row in rows
            if row.get("_eligible_primary_round") is True and row.get("run_id") != run_id
        ),
        key=lambda row: (row["run_id"], row["run_attempt"]),
    )


def _load_carry_payload(path: str) -> dict[str, Any] | None:
    """Read the producer bytes; None means unusable carry (see carry_missing)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("available") is not True
        or not isinstance(payload.get("open_findings"), list)
    ):
        return None
    return payload


def resolve_followup_round(
    *,
    repository: str,
    repository_id: int,
    pr_number: int,
    run_id: int,
    tier: str,
    head_sha: str,
    previous_json: str,
) -> str:
    aggregate = _load_aggregate()

    # The history loader annotates degradation on stdout; the shell block
    # captures this CLI's stdout as its entire decision protocol, so library
    # prints are diverted to stderr (still greppable in the step log).
    with contextlib.redirect_stdout(sys.stderr):
        status, _prior_ids, history = aggregate._load_pr_convergence_history(
            repository=repository, repository_id=repository_id, pr_number=pr_number,
        )
    if status != "available":
        return "action=skip reason=history_unavailable"
    rows = _prior_canonical_rows(history.rows, run_id)
    budget = aggregate._CONVERGENCE.pr_round_budget(
        prior_run_ids=[row["run_id"] for row in rows],
        current_run_id=run_id, current_eligible=True, tier=tier,
    )
    if budget["eligible_rounds"] < MIN_FOLLOWUP_ROUND:
        return "action=none"
    prev_head = str(rows[-1].get("head_sha") or "")
    if not _SHA_RE.fullmatch(prev_head):
        return "action=skip reason=prev_head_unavailable"
    if prev_head == head_sha:
        return "action=skip reason=same_head"
    try:
        payload = _load_carry_payload(previous_json)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if payload is None:
        return "action=skip reason=carry_missing"
    if payload.get("head_sha") != prev_head:
        return "action=skip reason=head_mismatch"
    return f"action=enable prev_head_sha={prev_head}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--tier", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--previous-json", required=True)
    args = parser.parse_args(argv)
    try:
        line = resolve_followup_round(
            repository=args.repository, repository_id=args.repository_id,
            pr_number=args.pr_number, run_id=args.run_id, tier=args.tier,
            head_sha=args.head_sha, previous_json=args.previous_json,
        )
    except Exception as exc:  # degrade to full review; the reason stays greppable
        line = f"action=skip reason={type(exc).__name__}"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
