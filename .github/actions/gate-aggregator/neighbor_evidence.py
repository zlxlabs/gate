#!/usr/bin/env python3
"""Neighbor-evidence collection for drifted disposition receipts (gate#297).

convergence.py implements the pure neighbor-consumption logic but owns no I/O;
this module is the gate-job side that collects the missing facts for each
candidate receipt (a signed ``false-positive`` with a ``finding_key`` whose
``head_sha`` or ``audit_digest`` no longer matches the current round):

  1. locate the issuing round in the already-loaded PR terminal history by
     ``head_sha`` and pull that round's canonical primary audit from Silo
     (falling back to ``gh run download`` on rc=2, the issuance workflow's
     two-level contract),
  2. adopt it only when exactly one copy matches the receipt's
     ``audit_digest`` (``_receipt_audit_digest_matches`` rule), then project
     the issuing Scope exactly like ``_convergence_scope_from_audit``,
  3. shallow-fetch both heads (no ``--filter``, gate#302, 300s bounded) and
     read the real ``git diff --unified=0`` hunks for the anchored file,
  4. re-run the allowlisted counterevidence grep with only argv index 6
     (head) replaced by the current head; record real exit/match_count.

Any failure yields exactly one greppable GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE
line and no evidence, keeping the consumer on its fail-closed reason codes.
Every git subprocess is timeout-bounded; a timeout is just another
unavailable reason (``git_fetch_timeout`` / ``git_diff_timeout``), never an
exception escaping to the gate job.  No retries, no alternate sources;
failures are never swallowed into evidence.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Sequence

_MODULE_FILE = Path(__file__).resolve()
GATE_ROOT = _MODULE_FILE.parents[3]
_CONVERGENCE_PATH = _MODULE_FILE.with_name("convergence.py")


def _load_convergence():
    """Reuse the host process's convergence module, or exec our own.

    aggregate.py loads convergence.py first; convergence guards compare
    dataclass identity (``isinstance``), so NeighborEvidence payloads must
    carry the SAME ``Scope`` class the consumer validates against.
    """
    for module in list(sys.modules.values()):
        module_file = getattr(module, "__file__", None)
        if module_file and Path(module_file).resolve() == _CONVERGENCE_PATH:
            return module
    spec = importlib.util.spec_from_file_location(
        "gate_convergence_for_neighbor_evidence", _CONVERGENCE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_CONVERGENCE = _load_convergence()

# Same canonical-or-legacy-raw digest equality rule the consumer applies.
_receipt_audit_digest_matches = _CONVERGENCE._receipt_audit_digest_matches

# Both git fetches against the caller repo use the issuance workflow's bound:
# `timeout 300 git fetch --depth 1 origin <sha>`, never blob-filtered (gate#302).
GIT_FETCH_TIMEOUT_S = 300
_GIT_SETUP_TIMEOUT_S = 30
_GIT_DIFF_TIMEOUT_S = 60
_GH_DOWNLOAD_TIMEOUT_S = 300
_SILO_GET_TIMEOUT_S = 120
_AUDIT_BASENAME = "primary-review-audit.json"

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)

_SCOPE_AUDIT_FIELDS = (
    "base_sha", "diff_digest", "policy_version", "policy_digest",
    "caller_sha", "reusable_workflow_sha",
)


def parse_unified_zero_hunks(diff_text: str) -> list[tuple[int, int, int, int]]:
    """Parse hunk headers into (old_start, old_count, new_start, new_count);
    a missing count (``@@ -5 +5 @@``) means 1, git's unified-diff convention."""
    hunks: list[tuple[int, int, int, int]] = []
    for match in _HUNK_RE.finditer(diff_text):
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        hunks.append((old_start, old_count, new_start, new_count))
    return hunks


def anchor_window_touched(
    hunks: Sequence[tuple[int, int, int, int]], old_line: int, tolerance: int,
) -> bool:
    """Whether any hunk's old side intersects ``[old_line-t, old_line+t]``.  A
    pure insertion (``-a,0``) lands between old lines a and a+1; treated as
    the closed interval [a, a+1] — conservative: a boundary-adjacent insertion
    voids the receipt instead of silently re-anchoring."""
    lo = old_line - tolerance
    hi = old_line + tolerance
    for old_start, old_count, _new_start, _new_count in hunks:
        if old_count == 0:
            if lo - 1 <= old_start <= hi:
                return True
        elif old_start <= hi and old_start + old_count - 1 >= lo:
            return True
    return False


def projected_anchor_line(
    hunks: Sequence[tuple[int, int, int, int]], old_line: int, tolerance: int,
) -> int:
    """Map the receipt-head anchor line onto the current head: sum
    ``new_count - old_count`` over hunks entirely before the window.  Only
    meaningful when ``anchor_window_touched`` is False."""
    lo = old_line - tolerance
    delta = 0
    for old_start, old_count, _new_start, new_count in hunks:
        last_old = old_start if old_count == 0 else old_start + old_count - 1
        if last_old < lo:
            delta += new_count - old_count
    return old_line + delta


def _finding_anchor_span(finding: Any) -> Optional[tuple[str, int, str]]:
    """Return a canonical file, line, and exact quote; null lines cannot close findings."""
    if not isinstance(finding, dict):
        return None
    path, line, quote = finding.get("file"), finding.get("line"), finding.get("quote")
    if (
        not isinstance(path, str) or not path or Path(path).is_absolute()
        or ".." in Path(path).parts or "\\" in path or type(line) is not int or line < 1
        or not isinstance(quote, str) or not quote
    ):
        return None
    return path, line, quote


def _bounded_git_timeout_s(maximum_s: float, deadline: Optional[float]) -> float:
    """Keep anchor-closure Git calls within one wall-clock budget."""
    if deadline is None:
        return maximum_s
    remaining_s = deadline - time.monotonic()
    if remaining_s <= 0:
        raise subprocess.TimeoutExpired("GATE-ANCHOR-CLOSURE deadline", maximum_s)
    return min(maximum_s, remaining_s)


def collect_anchor_updates(
    *, findings: Sequence[Any], repository: str, previous_head_sha: str,
    current_head_sha: str, token: str,
) -> Optional[dict[str, Optional[int]]]:
    """Map exact old-source anchors to current lines; deleted lines map to None.

    Missing or unavailable Git evidence returns no update, so stale coordinates
    are never represented as verified coordinates for the new head.
    """
    candidates = [
        (finding["id"], span)
        for finding in findings if isinstance(finding, dict)
        if isinstance(finding.get("id"), str) and finding["id"]
        if (span := _finding_anchor_span(finding)) is not None
    ]
    if not candidates:
        return {}
    valid_round_heads = (
        re.fullmatch(r"[0-9a-f]{40}", previous_head_sha)
        and re.fullmatch(r"[0-9a-f]{40}", current_head_sha)
        and previous_head_sha != current_head_sha
    )
    if not valid_round_heads:
        print("GATE-ANCHOR-CLOSURE-UNAVAILABLE reason=round_heads_invalid_or_unchanged")
        return None
    if not repository or not token:
        print("GATE-ANCHOR-CLOSURE-UNAVAILABLE reason=repository_or_token_missing")
        return None
    collector = _Collector(
        repository=repository, repository_id=0, pr_number=0,
        current_head_sha=current_head_sha, terminal_history_rows=(), token=token)
    deadline = time.monotonic() + _GIT_SETUP_TIMEOUT_S
    try:
        reason = collector._init_caller_repo(deadline=deadline)
        if reason:
            print(f"GATE-ANCHOR-CLOSURE-UNAVAILABLE reason={reason}")
            return None
        caller_dir = collector._caller_dir
        assert caller_dir is not None
        for sha in (previous_head_sha, current_head_sha):
            reason = collector._fetch_commit_into(caller_dir, sha, deadline=deadline)
            if reason:
                print(f"GATE-ANCHOR-CLOSURE-UNAVAILABLE reason={reason}")
                return None
        updates: dict[str, Optional[int]] = {}
        for path in {anchor[0] for _finding_id, anchor in candidates}:
            diff = _run_git(
                ["git", "-C", str(caller_dir), "diff", "--unified=0",
                 previous_head_sha, current_head_sha, "--", path],
                timeout_s=_bounded_git_timeout_s(_GIT_DIFF_TIMEOUT_S, deadline),
            )
            if diff.returncode != 0:
                print("GATE-ANCHOR-CLOSURE-UNAVAILABLE reason=git_diff_failed")
                return None
            hunks = parse_unified_zero_hunks(diff.stdout.decode("utf-8", errors="replace"))
            path_candidates = [
                (finding_id, anchor) for finding_id, anchor in candidates if anchor[0] == path
            ]
            old_file = _run_git(
                ["git", "-C", str(caller_dir), "show", f"{previous_head_sha}:{path}"],
                timeout_s=_bounded_git_timeout_s(_GIT_DIFF_TIMEOUT_S, deadline))
            if old_file.returncode != 0:
                continue
            try:
                old_lines = old_file.stdout.decode("utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for finding_id, (_candidate_path, line, quote) in path_candidates:
                if line > len(old_lines) or old_lines[line - 1] != quote:
                    continue
                replaced = any(
                    count > 0 and old_start <= line <= old_start + count - 1
                    for old_start, count, _new_start, _new_count in hunks
                )
                updates[finding_id] = None if replaced else projected_anchor_line(hunks, line, 0)
        return updates
    except subprocess.TimeoutExpired:
        print("GATE-ANCHOR-CLOSURE-UNAVAILABLE reason=git_diff_timeout")
        return None
    except OSError:
        print("GATE-ANCHOR-CLOSURE-UNAVAILABLE reason=git_diff_failed")
        return None
    finally:
        if collector._caller_dir is not None:
            shutil.rmtree(collector._caller_dir, ignore_errors=True)


def _finding_key_sha(finding_key: str) -> str:
    return hashlib.sha256(finding_key.encode("utf-8")).hexdigest()[:12]


def _run_git(
    argv: list[str], *, cwd: Optional[Path] = None, timeout_s: float,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def _silo_audit_get(key: str, timeout_s: float) -> Optional[bytes]:
    """Fetch one silo object; ``None`` = absent (rc=2).  Any other non-zero
    exit raises — same contract as the issuance workflow; no alternate source."""
    store = os.environ.get("SILO_STORE") or str(GATE_ROOT / "scripts" / "silo_store.py")
    with tempfile.TemporaryDirectory(prefix="gate-neighbor-silo-") as dest:
        proc = subprocess.run(
            [sys.executable, store, "get", "--key", key, "--dest", dest],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode == 2:
            return None
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "silo get failed").strip())
        return (Path(dest) / _AUDIT_BASENAME).read_bytes()


def _gh_run_download(
    *, repository: str, run_id: int, artifact_name: str, dest_dir: Path, token: str,
) -> Optional[bytes]:
    """Second-level audit fetch (issuance contract: silo rc=2 → gh download)."""
    env = dict(os.environ, GH_TOKEN=token)
    proc = subprocess.run(
        ["gh", "run", "download", "-R", repository, str(run_id),
         "--name", artifact_name, "--dir", str(dest_dir)],
        capture_output=True,
        text=True,
        timeout=_GH_DOWNLOAD_TIMEOUT_S,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        return None
    matches = sorted(dest_dir.rglob(_AUDIT_BASENAME)) or sorted(dest_dir.rglob("*.json"))
    if not matches:
        return None
    return matches[0].read_bytes()


def _is_positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


class _Collector:
    """Per-round collector state shared across candidate receipts."""

    def __init__(
        self, *,
        repository: str,
        repository_id: int,
        pr_number: int,
        current_head_sha: str,
        terminal_history_rows: Sequence[Any],
        token: str,
    ) -> None:
        self._repository = repository
        self._repository_id = repository_id
        self._pr_number = pr_number
        self._current_head_sha = current_head_sha
        self._terminal_history_rows = terminal_history_rows
        self._token = token
        self._audit_cache: dict[tuple[str, int, int], Optional[bytes]] = {}
        self._fetched_heads: set[str] = set()
        self._caller_dir: Optional[Path] = None
        # Current-round digest pair for candidate filtering; set_current_digests().
        self._audit_digest_for_candidates = ""
        self._legacy_raw_audit_digest_for_candidates: Optional[str] = None

    def collect(self, receipts: Sequence[Any]) -> dict[str, Any]:
        """Collect evidence per candidate; ``{}`` when none (then zero
        network/git/silo calls).  Candidate = false-positive receipt with a
        finding_key whose head_sha/digest no longer matches this round."""
        tolerance = _CONVERGENCE.NEIGHBOR_LINE_TOLERANCE
        candidates = [
            receipt for receipt in receipts
            if isinstance(receipt, _CONVERGENCE.DispositionReceipt)
            and receipt.disposition == "false-positive"
            and bool(receipt.finding_key)
            and (
                receipt.head_sha != self._current_head_sha
                or not _receipt_audit_digest_matches(
                    receipt.audit_digest,
                    self._audit_digest_for_candidates,
                    self._legacy_raw_audit_digest_for_candidates,
                )
            )
        ]
        evidence: dict[str, Any] = {}
        if not candidates:
            return evidence
        try:
            for receipt in candidates:
                key_sha = _finding_key_sha(receipt.finding_key)
                collected = self._collect_one(receipt, tolerance=tolerance)
                if collected is None:
                    continue
                evidence[receipt.finding_key] = collected
                print(
                    f"GATE-NEIGHBOR-EVIDENCE finding_key_sha={key_sha} "
                    f"window_touched={collected.anchor_window_touched} "
                    f"rerun_exit={collected.rerun_exit_code} "
                    f"match_count={collected.rerun_match_count} "
                    f"anchor_line_current={collected.anchor_line_current}"
                )
        finally:
            if self._caller_dir is not None:
                shutil.rmtree(self._caller_dir, ignore_errors=True)
        return evidence

    def set_current_digests(self, audit_digest: str, legacy: Optional[str]) -> None:
        self._audit_digest_for_candidates = audit_digest
        self._legacy_raw_audit_digest_for_candidates = legacy

    def _fail(self, key_sha: str, reason: str) -> None:
        print(f"GATE-NEIGHBOR-EVIDENCE-UNAVAILABLE finding_key_sha={key_sha} reason={reason}")

    def _collect_one(self, receipt: Any, *, tolerance: int) -> Optional[Any]:
        """Collect evidence for one candidate receipt or print why not."""
        key_sha = _finding_key_sha(receipt.finding_key)

        # -- decode the finding key into an anchor (no I/O yet) ----------------
        decoded = _CONVERGENCE._decode_canonical_finding_key(receipt.finding_key)
        neighbor_key = (
            _CONVERGENCE.neighbor_finding_key(decoded) if isinstance(decoded, dict) else None
        )
        if decoded is None or neighbor_key is None:
            self._fail(key_sha, "finding_key_not_neighbor")
            return None
        anchor_file = decoded["file"]
        anchor_line = decoded["line"]

        # -- validate the receipt's own rerun argv (no I/O yet) ----------------
        counterevidence = receipt.counterevidence if isinstance(receipt.counterevidence, dict) else {}
        rerun = counterevidence.get("gate_rerun") if isinstance(counterevidence, dict) else None
        receipt_argv = rerun.get("argv") if isinstance(rerun, dict) else None
        if (
            not isinstance(receipt_argv, list)
            or _CONVERGENCE.counterevidence_argv_reason(receipt_argv, receipt.head_sha) is not None
        ):
            self._fail(key_sha, "counterevidence_argv_invalid")
            return None

        # -- locate the issuing round in the loaded terminal history -----------
        rows = [
            row for row in self._terminal_history_rows
            if isinstance(row, dict)
            and row.get("head_sha") == receipt.head_sha
            and _is_positive_int(row.get("run_id"))
            and _is_positive_int(row.get("run_attempt"))
        ]
        if not rows:
            self._fail(key_sha, "receipt_head_not_in_history")
            return None

        # -- fetch each round's canonical audit copy; exactly one digest match --
        matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
        fetch_failed = False
        for row in rows:
            cache_key = (receipt.head_sha, row["run_id"], row["run_attempt"])
            raw = self._fetch_audit_copy(
                head_sha=receipt.head_sha, run_id=row["run_id"], run_attempt=row["run_attempt"],
                cache_key=cache_key,
            )
            if raw is None:
                fetch_failed = True
                continue
            try:
                audit = json.loads(raw.decode("utf-8"))
                if not isinstance(audit, dict):
                    raise ValueError("audit is not a JSON object")
            except (ValueError, UnicodeDecodeError):
                continue
            canonical_digest = _CONVERGENCE.canonical_audit_digest(audit)
            raw_digest = hashlib.sha256(raw).hexdigest()
            if _receipt_audit_digest_matches(receipt.audit_digest, canonical_digest, raw_digest):
                matched.append((audit, row))
        if len(matched) != 1:
            if matched:
                self._fail(key_sha, "audit_digest_ambiguous")
            elif fetch_failed:
                self._fail(key_sha, "audit_fetch_failed")
            else:
                self._fail(key_sha, "audit_digest_no_match")
            return None
        audit, _row = matched[0]

        # -- project the issuing Scope exactly like _convergence_scope_from_audit --
        if any(f not in audit for f in _SCOPE_AUDIT_FIELDS) or audit.get("head_sha") != receipt.head_sha:
            self._fail(key_sha, "audit_scope_invalid")
            return None
        try:
            receipt_scope = _CONVERGENCE.Scope(
                repository_id=self._repository_id,
                pr_number=self._pr_number,
                base_sha=audit["base_sha"],
                head_sha=receipt.head_sha,
                diff_digest=audit["diff_digest"],
                policy_version=audit["policy_version"],
                policy_digest=audit["policy_digest"],
                tier=audit.get("tier") if audit.get("tier") in _CONVERGENCE.SUPPORTED_TIERS else "internal",
                caller_sha=audit["caller_sha"],
                reusable_workflow_sha=audit["reusable_workflow_sha"],
            )
            _CONVERGENCE.validate_scope(receipt_scope)
        except Exception:
            self._fail(key_sha, "audit_scope_invalid")
            return None

        # -- shallow-fetch both heads into the shared caller repo --------------
        caller_dir = self._caller_dir
        if caller_dir is None:
            setup_reason = self._init_caller_repo()
            if setup_reason is not None:
                self._fail(key_sha, setup_reason)
                return None
            caller_dir = self._caller_dir
            assert caller_dir is not None
        for sha in (receipt.head_sha, self._current_head_sha):
            if sha in self._fetched_heads:
                continue
            fetch_reason = self._fetch_commit_into(caller_dir, sha)
            if fetch_reason is not None:
                self._fail(key_sha, fetch_reason)
                return None

        # -- anchor window against the real diff --
        try:
            diff = _run_git(
                ["git", "-C", str(caller_dir), "diff", "--unified=0",
                 receipt.head_sha, self._current_head_sha, "--", anchor_file],
                timeout_s=_GIT_DIFF_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            self._fail(key_sha, "git_diff_timeout")
            return None
        except OSError:
            self._fail(key_sha, "git_diff_failed")
            return None
        if diff.returncode != 0:
            self._fail(key_sha, "git_diff_failed")
            return None
        hunks = parse_unified_zero_hunks(diff.stdout.decode("utf-8", errors="replace"))
        # A file deleted or renamed on the current head counts as touched.
        # _file_exists_in_head raises TimeoutExpired/OSError; the cat-file
        # belongs to this diff step, so its timeout folds into git_diff_timeout.
        try:
            file_gone = not self._file_exists_in_head(
                caller_dir, self._current_head_sha, anchor_file,
            )
        except subprocess.TimeoutExpired:
            self._fail(key_sha, "git_diff_timeout")
            return None
        except OSError:
            self._fail(key_sha, "git_diff_failed")
            return None
        touched = file_gone or anchor_window_touched(hunks, anchor_line, tolerance)
        line_current = (
            anchor_line if touched
            else projected_anchor_line(hunks, anchor_line, tolerance)
        )

        # -- rerun the allowlisted counterevidence against the current head --
        rerun_argv = list(receipt_argv)
        rerun_argv[6] = self._current_head_sha
        try:
            completed = _run_git(
                rerun_argv,
                cwd=caller_dir,
                timeout_s=_CONVERGENCE.COUNTEREVIDENCE_RERUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            self._fail(key_sha, "counterevidence_rerun_timeout")
            return None
        except OSError:
            self._fail(key_sha, "counterevidence_rerun_spawn_failed")
            return None
        stdout = completed.stdout or b""
        match_count = len([line for line in stdout.splitlines() if line])

        return _CONVERGENCE.NeighborEvidence(
            current_head_sha=self._current_head_sha,
            anchor_line_current=line_current,
            anchor_window_touched=touched,
            rerun_argv=rerun_argv,
            rerun_exit_code=completed.returncode,
            rerun_match_count=match_count,
            receipt_scope=receipt_scope,
        )

    def _init_caller_repo(self, *, deadline: Optional[float] = None) -> Optional[str]:
        """Set up the throwaway caller repo; ``None`` = ready to fetch, else
        the fail enum for this receipt's UNAVAILABLE line."""
        repo_dir: Optional[Path] = None
        try:
            repo_dir = Path(tempfile.mkdtemp(prefix="gate-neighbor-caller-"))
            auth = base64.b64encode(f"x-access-token:{self._token}".encode("utf-8")).decode("ascii")
            setup = [
                ["git", "init", "--quiet"],
                ["git", "-C", str(repo_dir), "remote", "add", "origin",
                 f"https://github.com/{self._repository}.git"],
                ["git", "-C", str(repo_dir), "config", "http.extraheader",
                 f"AUTHORIZATION: basic {auth}"],
            ]
            for argv in setup:
                proc = _run_git(
                    argv, cwd=repo_dir,
                    timeout_s=_bounded_git_timeout_s(_GIT_SETUP_TIMEOUT_S, deadline),
                )
                if proc.returncode != 0:
                    shutil.rmtree(repo_dir, ignore_errors=True)
                    return "git_fetch_failed"
            self._caller_dir = repo_dir
            return None
        except subprocess.TimeoutExpired:
            if repo_dir is not None:
                shutil.rmtree(repo_dir, ignore_errors=True)
            return "git_fetch_timeout" if deadline is not None else "git_fetch_failed"
        except (OSError, subprocess.SubprocessError):
            if repo_dir is not None:
                shutil.rmtree(repo_dir, ignore_errors=True)
            return "git_fetch_failed"

    def _fetch_commit_into(
        self, repo_dir: Path, sha: str, *, deadline: Optional[float] = None,
    ) -> Optional[str]:
        """Shallow-fetch one commit; ``None`` = fetched, else a fail enum
        (timeout → git_fetch_timeout, including this step's cat-file check)."""
        try:
            proc = _run_git(
                ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", sha],
                timeout_s=_bounded_git_timeout_s(GIT_FETCH_TIMEOUT_S, deadline),
            )
        except subprocess.TimeoutExpired:
            return "git_fetch_timeout"
        except OSError:
            return "git_fetch_failed"
        if proc.returncode != 0:
            return "git_fetch_failed"
        try:
            check = _run_git(
                ["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"],
                timeout_s=_bounded_git_timeout_s(_GIT_SETUP_TIMEOUT_S, deadline),
            )
        except subprocess.TimeoutExpired:
            return "git_fetch_timeout"
        except OSError:
            return "git_fetch_failed"
        if check.returncode != 0:
            return "git_fetch_failed"
        self._fetched_heads.add(sha)
        return None

    def _file_exists_in_head(self, repo_dir: Path, head_sha: str, path: str) -> bool:
        """Raises TimeoutExpired/OSError through — the caller maps them onto
        the diff step's fail enums."""
        proc = _run_git(
            ["git", "-C", str(repo_dir), "cat-file", "-e", f"{head_sha}:{path}"],
            timeout_s=_GIT_SETUP_TIMEOUT_S,
        )
        return proc.returncode == 0

    def _fetch_audit_copy(
        self, *, head_sha: str, run_id: int, run_attempt: int,
        cache_key: tuple[str, int, int],
    ) -> Optional[bytes]:
        """One audit copy via silo, gh download on rc=2; negatives are cached so
        a second receipt bound to the same round never retries the fetch."""
        if cache_key in self._audit_cache:
            return self._audit_cache[cache_key]
        artifact_name = f"primary-audit-v2-{self._repository_id}-{head_sha}-{run_id}-{run_attempt}"
        silo_key = f"d14/{self._repository_id}/{artifact_name}/{_AUDIT_BASENAME}"
        absent = False
        try:
            raw = _silo_audit_get(silo_key, _SILO_GET_TIMEOUT_S)
            absent = raw is None
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self._audit_cache[cache_key] = None
            return None
        if absent:
            with tempfile.TemporaryDirectory(prefix="gate-neighbor-gh-") as dest:
                try:
                    raw = _gh_run_download(
                        repository=self._repository,
                        run_id=run_id,
                        artifact_name=artifact_name,
                        dest_dir=Path(dest),
                        token=self._token,
                    )
                except (OSError, subprocess.SubprocessError):
                    raw = None
            if raw is None:
                self._audit_cache[cache_key] = None
                return None
        self._audit_cache[cache_key] = raw
        return raw


def collect_neighbor_evidence(
    *,
    receipts: Sequence[Any],
    repository: str,
    repository_id: int,
    pr_number: int,
    current_head_sha: str,
    audit_digest: str,
    legacy_raw_audit_digest: Optional[str],
    terminal_history_rows: Sequence[Any],
    token: str,
) -> dict[str, Any]:
    """Entry point used by aggregate.py's main; see :class:`_Collector`."""
    collector = _Collector(
        repository=repository,
        repository_id=repository_id,
        pr_number=pr_number,
        current_head_sha=current_head_sha,
        terminal_history_rows=terminal_history_rows,
        token=token,
    )
    collector.set_current_digests(audit_digest, legacy_raw_audit_digest)
    return collector.collect(receipts)
