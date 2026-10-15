#!/usr/bin/env python3
"""Ledger-job I/O assembly for the incremental blocking shadow (card G2).

Consumes the pure classifier in ``incremental_shadow.py`` (card G1) and the
inputs the ``ledger`` job of ``gate-v2.yml`` already has on disk (canonical
primary audit, gate terminal envelope) plus Silo history (per-run
``codex-review-ledger-v2-*`` archives) and one inter-round ``git diff``, and
writes the §5.1 ``shadow.json`` payload consumed by ``build_ledger.py``.
Design contract: gate-hub
``docs/designs/convergence-incremental-blocking-shadow.md`` §2 (data flow),
§4 (algorithm), §5.1 (payload shape), §8 (budget + failure semantics).
This step is observational-only: it never changes a gate conclusion, and its
single sanctioned fail-open is one greppable
``GATE-INCREMENTAL-SHADOW-UNAVAILABLE: reason=<code>`` line per degraded run.

Round sequence is rebuilt from Silo archive rows — never from the per-PR
latest pointer (gate#309 last-writer-wins race).  The candidate run set is
this PR's head-branch ``pull_request`` runs, enumerated with the same
full-list-or-None semantics as the aggregator's ``_pr_target_run_ids``
(INV-A4: a history that cannot be fully listed yields ``history_truncated``,
never a partial count).  Git fetches are bounded shallow fetches of exact
SHAs against a throwaway repo, never blob-filtered (gate#302).  No retries
anywhere: a failed archive is skipped and recorded as
``silo_archives_partial``; everything else degrades the whole payload.

The script always writes ``shadow.json`` (even for skips, degrades and
unexpected exceptions) and always exits 0, so the ledger row is built
whether or not the shadow produced data; a missing or invalid file is then
dropped by ``build_ledger.py`` with its own unavailable line.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from incremental_shadow import (  # noqa: E402
    FINDING_CLASSES,
    classify_p1_findings,
    fold_canonical_rounds,
    parse_diff_added_lines,
)

SCHEMA_VERSION = 1
DEFAULT_BUDGET_S = 240
BUDGET_ENV = "GATE_INCREMENTAL_SHADOW_BUDGET_S"
LEDGER_ARTIFACT_PREFIX = "codex-review-ledger-v2"
LEDGER_OBJECT_NAME = "ledger.jsonl"
MAX_HISTORY_RUNS = 50
RUNS_PAGE_SIZE = 100
GIT_SETUP_TIMEOUT_S = 30
GIT_FETCH_TIMEOUT_S = 300  # `timeout 300 git fetch --depth 1 origin <sha>`, never blob-filtered (gate#302).
GIT_DIFF_TIMEOUT_S = 120  # design §8: full-tree `git diff -U0 -M` upper bound.
SILO_TIMEOUT_S = 120  # same per-object bound neighbor_evidence.py uses for silo gets.
GH_API_TIMEOUT_S = 30
UNAVAILABLE_PREFIX = "GATE-INCREMENTAL-SHADOW-UNAVAILABLE"
STATUS_DOMAIN = ("computed", "skipped", "degraded")
SKIP_REASONS = ("review_not_expected", "no_prior_distinct_head", "history_truncated", "audit_absent")
DEGRADE_REASONS = (
    "silo_list_failed",
    "runs_list_failed",
    "silo_archives_partial",
    "diff_fetch_timeout",
    "diff_failed",
    "budget_exhausted",
    "internal_error",
)
# First match wins when several degrades accumulated (stop-cause first, then
# history-side, then diff-side).
DEGRADE_PRECEDENCE = (
    "budget_exhausted",
    "silo_archives_partial",
    "diff_fetch_timeout",
    "diff_failed",
    "silo_list_failed",
    "runs_list_failed",
)


def _invalid_budget(raw: str) -> ValueError:
    return ValueError(f"{BUDGET_ENV} must be a non-negative integer, got {raw!r}")


def budget_seconds(raw: str | None) -> int:
    """Parse the wall-clock budget; missing env means the 240s default (§8)."""
    if raw is None or not raw.strip():
        return DEFAULT_BUDGET_S
    text = raw.strip()
    if not text.isdigit():
        raise _invalid_budget(raw)
    return int(text)


class _HistoryTruncated(Exception):
    """More runs than one page / MAX_HISTORY_RUNS — skip, never half-count."""


def _remote_url(repository: str) -> str:
    """Caller-repo https remote used for the inter-round fetch. Module-level
    so offline replay/tests can point it at a local fixture remote; the
    ledger job always uses the default."""
    return f"https://github.com/{repository}.git"


class _StopBeforeHistory(Exception):
    """Budget expired before the history read; keep the degraded payload."""


class _DiffUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Budget:
    """Wall-clock budget; checked before each network/git operation (§8:
    超限立即停止取数 — operation granularity, subprocess timeouts still bound
    the single operation already started)."""

    def __init__(self, seconds: int) -> None:
        self._deadline = time.monotonic() + seconds

    def expired(self) -> bool:
        return time.monotonic() >= self._deadline


def _empty_counts() -> dict[str, int]:
    return {name: 0 for name in FINDING_CLASSES}


def _base_payload(head_sha: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "skipped",
        "skip_reason": "",
        "degrade_reason": "",
        "canonical_round": 0,
        "prev_head_sha": "",
        "head_sha": head_sha,
        "prior_rounds_used": 0,
        "diff_status": "unavailable",
        "findings": [],
        "truncated": False,
        "class_counts": _empty_counts(),
        "counterfactual": "not_applicable",
    }


def _load_audit_findings(path: Path) -> list[dict[str, Any]] | None:
    """Current round's findings from the canonical audit; ``None`` when the
    audit is absent/unreadable (→ skipped ``audit_absent``; §4.1 pseudo-code
    folds missing audit into a skip, the §5.1 domain splits the reasons)."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        audit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(audit, dict):
        return None
    result = audit.get("result")
    if not isinstance(result, dict):
        return None
    findings = result.get("findings")
    if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
        return None
    return findings


def _load_terminal_facts(path: Path) -> tuple[str | None, str | None]:
    """(gate_result, reason_code) from the gate terminal envelope; (None,
    None) when absent/unreadable → counterfactual stays ``unclassified``
    (the caller cannot establish the round was failed by primary findings)."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None, None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    gate_result = payload.get("gate_result")
    reason_code = payload.get("reason_code")
    if not isinstance(gate_result, str) or not isinstance(reason_code, str):
        return None, None
    return gate_result, reason_code


def _gh_api_json(path: str, token: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=GH_API_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _enumerate_branch_run_ids(*, repository: str, pr_number: int, token: str) -> list[int]:
    """This PR's head-branch ``pull_request`` run ids, newest first, with
    full-list-or-None truncation semantics (mirrors the aggregator's
    ``_pr_target_run_ids`` INV-A4).  Raises RuntimeError when the runs API is
    unavailable; returns a list that may be empty (first run of the PR)."""
    if not token:
        raise RuntimeError("GH_TOKEN/GITHUB_TOKEN is not set")
    pull = _gh_api_json(f"/repos/{repository}/pulls/{pr_number}", token)
    if not isinstance(pull, dict) or not isinstance(pull.get("head"), dict):
        raise RuntimeError("PR response has an invalid shape")
    head_ref = pull["head"].get("ref")
    if not isinstance(head_ref, str) or not head_ref:
        raise RuntimeError("PR head branch is missing")
    payload = _gh_api_json(
        f"/repos/{repository}/actions/runs"
        f"?event=pull_request&branch={urllib.parse.quote(head_ref, safe='')}&per_page={RUNS_PAGE_SIZE}",
        token,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise RuntimeError("Actions runs response has an invalid shape")
    workflow_runs = payload["workflow_runs"]
    total_count = payload.get("total_count")
    if isinstance(total_count, int) and total_count > len(workflow_runs):
        raise _HistoryTruncated()
    if not isinstance(total_count, int) and len(workflow_runs) >= RUNS_PAGE_SIZE:
        raise _HistoryTruncated()
    run_ids = [run["id"] for run in workflow_runs if isinstance(run, dict) and isinstance(run.get("id"), int)]
    unique_run_ids = list(dict.fromkeys(run_ids))
    if len(unique_run_ids) > MAX_HISTORY_RUNS:
        raise _HistoryTruncated()
    return unique_run_ids


def _silo_store_path() -> str:
    store = os.environ.get("SILO_STORE", "").strip()
    if not store:
        raise RuntimeError("SILO_STORE is not set")
    return store


def _silo_cli(args: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _silo_store_path(), *args],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def _parse_archive_name(name: str, repo_id: int) -> tuple[str, int, int] | None:
    """``codex-review-ledger-v2-{repo_id}-{head40}-{run_id}-{attempt}`` →
    (head40, run_id, attempt); None for foreign names."""
    prefix = f"{LEDGER_ARTIFACT_PREFIX}-{repo_id}-"
    if not name.startswith(prefix):
        return None
    parts = name[len(prefix):].rsplit("-", 2)
    if len(parts) != 3:
        return None
    head, run_id_text, attempt_text = parts
    head = head.lower()
    if len(head) != 40 or any(char not in "0123456789abcdef" for char in head):
        return None
    if not run_id_text.isdigit() or not attempt_text.isdigit():
        return None
    return head, int(run_id_text), int(attempt_text)


def _list_archive_names(*, repo_id: int) -> list[str]:
    proc = _silo_cli(
        ["list", "--tier", "d30", "--repo-id", str(repo_id),
         "--name-prefix", f"{LEDGER_ARTIFACT_PREFIX}-{repo_id}-"],
        SILO_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "silo list failed").strip())
    names = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split("/")
        if len(parts) == 4 and parts[0] == "d30" and parts[1] == str(repo_id) and parts[3] == LEDGER_OBJECT_NAME:
            names.append(parts[2])
    return names


def _fetch_archive_rows(name: str, *, repo_id: int, dest_dir: Path) -> list[dict[str, Any]]:
    """One per-run ledger archive → its row dicts.  Raises RuntimeError on
    any failure (rc!=0 — including rc=2 absent — or unparseable content);
    the caller counts it as a partial miss and keeps going (§4.2)."""
    dest = dest_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    proc = _silo_cli(["get", "--key", f"d30/{repo_id}/{name}/{LEDGER_OBJECT_NAME}", "--dest", str(dest)], SILO_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"silo get failed: {name}").strip())
    rows = []
    for line in (dest / LEDGER_OBJECT_NAME).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise RuntimeError(f"archive {name} has a non-object ledger row")
        rows.append(row)
    return rows


def _run_git(argv: list[str], *, cwd: Path | None = None, timeout_s: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=None if cwd is None else str(cwd), capture_output=True, timeout=timeout_s, check=False,
    )


def _inter_round_added_lines(
    prev_head: str, head_sha: str, *, token: str, remote_url: str,
) -> dict[str, set[int]]:
    """Shallow-fetch both heads into a throwaway repo and parse
    ``git diff -U0 -M prev..head`` new-side added lines (§2.4).  Any failure
    raises _DiffUnavailable with ``diff_fetch_timeout``/``diff_failed``."""
    repo_dir: Path | None = None
    try:
        repo_dir = Path(tempfile.mkdtemp(prefix="gate-incremental-shadow-"))
        auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        setup = [
            ["git", "init", "--quiet"],
            ["git", "-C", str(repo_dir), "remote", "add", "origin", remote_url],
            ["git", "-C", str(repo_dir), "config", "http.extraheader", f"AUTHORIZATION: basic {auth}"],
        ]
        for argv in setup:
            if _run_git(argv, cwd=repo_dir, timeout_s=GIT_SETUP_TIMEOUT_S).returncode != 0:
                raise _DiffUnavailable("diff_failed")
        for sha in (prev_head, head_sha):
            try:
                proc = _run_git(
                    ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", sha],
                    timeout_s=GIT_FETCH_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                raise _DiffUnavailable("diff_fetch_timeout") from None
            except OSError:
                raise _DiffUnavailable("diff_failed") from None
            if proc.returncode != 0:
                # Includes force-pushed-away prev heads (§4.2): anchors
                # from Silo archives still apply, the diff does not.
                raise _DiffUnavailable("diff_failed")
        try:
            diff = _run_git(
                ["git", "-C", str(repo_dir), "diff", "-U0", "-M", f"{prev_head}..{head_sha}"],
                timeout_s=GIT_DIFF_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise _DiffUnavailable("diff_failed") from None
        except OSError:
            raise _DiffUnavailable("diff_failed") from None
        if diff.returncode != 0:
            raise _DiffUnavailable("diff_failed")
        return parse_diff_added_lines(diff.stdout.decode("utf-8", errors="replace"))
    finally:
        if repo_dir is not None:
            shutil.rmtree(repo_dir, ignore_errors=True)


def build_shadow(
    *,
    audit_path: Path,
    terminal_path: Path,
    repository: str,
    repo_id: int,
    pr_number_text: str,
    run_id: int,
    head_sha: str,
    review_expected: str,
    budget_s: int,
    token: str,
    remote_url: str | None = None,
) -> dict[str, Any]:
    """Produce the §5.1 payload; never raises for operational failures (the
    caller's top-level handler is the last-resort ``internal_error`` path)."""
    budget = _Budget(budget_s)
    head_sha = head_sha.strip()
    degrades: list[str] = []

    def degrade(reason: str) -> None:
        if reason not in degrades:
            degrades.append(reason)

    def finalize(payload: dict[str, Any]) -> dict[str, Any]:
        if degrades:
            was_classified = payload["status"] == "computed"
            payload["status"] = "degraded"
            payload["skip_reason"] = ""
            payload["degrade_reason"] = next(code for code in DEGRADE_PRECEDENCE if code in degrades)
            if not was_classified:
                # degraded without classification: the counterfactual premise
                # (round failed BY primary findings) is unobservable.
                payload["counterfactual"] = "unclassified"
        return payload

    if review_expected != "true" or not head_sha:
        return _skipped_payload("review_not_expected", head_sha=head_sha)
    if not pr_number_text.strip().isdigit():
        # Review expected but no PR context is a caller contract violation —
        # degrade instead of crashing the ledger job.
        degrade("runs_list_failed")
        return finalize(_base_payload(head_sha))
    pr_number = int(pr_number_text.strip())

    findings = _load_audit_findings(audit_path)
    if findings is None:
        return _skipped_payload("audit_absent", head_sha=head_sha)

    try:
        if budget.expired():
            degrade("budget_exhausted")
            raise _StopBeforeHistory()
        branch_run_ids = _enumerate_branch_run_ids(repository=repository, pr_number=pr_number, token=token)
    except _HistoryTruncated:
        return _skipped_payload("history_truncated", head_sha=head_sha)
    except _StopBeforeHistory:
        return finalize(_base_payload(head_sha))
    except Exception:
        degrade("runs_list_failed")
        return finalize(_base_payload(head_sha))
    branch_run_id_set = set(branch_run_ids)

    try:
        if budget.expired():
            degrade("budget_exhausted")
            raise _StopBeforeHistory()
        archive_names = _list_archive_names(repo_id=repo_id)
    except _StopBeforeHistory:
        return finalize(_base_payload(head_sha))
    except Exception:
        degrade("silo_list_failed")
        return finalize(_base_payload(head_sha))

    candidates = []
    for name in archive_names:
        parsed = _parse_archive_name(name, repo_id)
        if parsed is None:
            continue
        head, archive_run_id, attempt = parsed
        if archive_run_id >= run_id or archive_run_id not in branch_run_id_set:
            continue  # current/older-attempt runs are excluded (§4.1: parse_run_id < run_id)
        candidates.append(((archive_run_id, attempt), name))
    candidates.sort(reverse=True)
    candidates = candidates[:MAX_HISTORY_RUNS]

    rows: list[dict[str, Any]] = []
    partial_misses = 0
    with tempfile.TemporaryDirectory(prefix="gate-incremental-shadow-silo-") as silo_dir:
        for (_, name) in candidates:
            if budget.expired():
                degrade("budget_exhausted")
                break
            try:
                rows.extend(_fetch_archive_rows(name, repo_id=repo_id, dest_dir=Path(silo_dir)))
            except Exception:
                partial_misses += 1  # §4.2: missing archive rows only shrink the anchor set
    if partial_misses:
        degrade("silo_archives_partial")

    rows = [row for row in rows if row.get("pr_number") == pr_number]
    try:
        prior_rounds = fold_canonical_rounds(rows)
    except ValueError:
        # A structurally broken eligible archive row is a producer bug; the
        # fail-open line keeps the ledger row alive without the shadow field.
        degrade("silo_archives_partial")
        return finalize(_base_payload(head_sha))

    if not prior_rounds or all(round_["head_sha"] == head_sha for round_ in prior_rounds):
        payload = _skipped_payload(
            "no_prior_distinct_head", head_sha=head_sha,
            canonical_round=len(prior_rounds),
            prev_head_sha=prior_rounds[-1]["head_sha"] if prior_rounds else "",
            prior_rounds_used=len(prior_rounds),
        )
        return finalize(payload)

    prev_head = prior_rounds[-1]["head_sha"]
    diff_lines: dict[str, set[int]] | None = None
    if budget.expired():
        degrade("budget_exhausted")
    else:
        try:
            diff_lines = _inter_round_added_lines(
                prev_head, head_sha,
                token=token,
                remote_url=remote_url or _remote_url(repository),
            )
        except _DiffUnavailable as exc:
            degrade(exc.reason)

    gate_result, reason_code = _load_terminal_facts(terminal_path)
    if gate_result is None or reason_code is None:
        # Terminal facts unavailable: classification stands, but the
        # counterfactual premise (round failed BY primary findings) is
        # unobservable → force ``unclassified`` after the pure call.
        gate_result, reason_code = "fail", "primary_findings"
        terminal_known = False
    else:
        terminal_known = True

    payload = classify_p1_findings(
        prior_rounds, findings, diff_lines,
        head_sha=head_sha, gate_result=gate_result, reason_code=reason_code,
    )
    if not terminal_known:
        payload["counterfactual"] = "unclassified"
    payload = {**_base_payload(head_sha), **payload}
    payload["status"] = "computed"
    return finalize(payload)


def _skipped_payload(
    reason: str, *, head_sha: str, canonical_round: int = 0,
    prev_head_sha: str = "", prior_rounds_used: int = 0,
) -> dict[str, Any]:
    payload = _base_payload(head_sha)
    payload["skip_reason"] = reason
    payload["canonical_round"] = canonical_round
    payload["prev_head_sha"] = prev_head_sha
    payload["prior_rounds_used"] = prior_rounds_used
    if reason in {"history_truncated"}:
        payload["counterfactual"] = "unclassified"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audit-path", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--shadow-out", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument("--pr-number", default="")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--review-expected", default="false")
    args = parser.parse_args()

    try:
        payload = build_shadow(
            audit_path=Path(args.audit_path),
            terminal_path=Path(args.terminal_path),
            repository=args.repository,
            repo_id=args.repository_id,
            pr_number_text=args.pr_number,
            run_id=args.run_id,
            head_sha=args.head_sha,
            review_expected=args.review_expected,
            budget_s=budget_seconds(os.environ.get(BUDGET_ENV)),
            token=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "",
        )
    except Exception:
        # Last-resort fail-open (§8): traceback on stderr, then a degraded
        # payload; the single unavailable line is printed by the outcome
        # printer below (exactly one line per degraded run).
        traceback.print_exc()
        payload = _base_payload(args.head_sha.strip())
        payload["status"] = "degraded"
        payload["degrade_reason"] = "internal_error"
        payload["counterfactual"] = "unclassified"

    out_path = Path(args.shadow_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    if payload["status"] == "degraded":
        print(f"{UNAVAILABLE_PREFIX}: reason={payload['degrade_reason']}")
    elif payload["status"] == "skipped":
        print(f"incremental-blocking-shadow: skipped reason={payload['skip_reason']}")
    else:
        print(
            "incremental-blocking-shadow: computed "
            f"canonical_round={payload['canonical_round']} "
            f"p1={payload['class_counts'] and sum(payload['class_counts'].values())} "
            f"counterfactual={payload['counterfactual']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
