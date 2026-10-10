#!/usr/bin/env python3
"""Host-declared git source for the shared gate workflows.

A host declares how its CI jobs may obtain git objects by writing exactly one
line into ``$GATE_HUB_GIT_MIRROR_DIR/SOURCE-MODE``:

* unset / empty ``$GATE_HUB_GIT_MIRROR_DIR`` — ``origin``: no service on this
  host, every caller keeps the pre-existing GitHub fetch behaviour.
* ``origin`` — same as above, stated explicitly.
* ``service`` — the host runs the read-only source service: prepare any commit
  SHA through ``$GATE_HUB_GIT_MIRROR_DIR/git-source-prepare`` (no network from
  the job at all) and read the object closure out of the read-only mirror under
  a shared consume lock.  Any service failure is fatal — the service path never
  falls back to ``origin`` and never retries.

The declaration file is read exactly once per process and every caller shares
this module: the ``gate_bounded_retry.py`` tool bootstrap (workflow_sha sparse
directories) and the PR-size preflight / diff-coverage advisory actions
(base/head 补拉).  Caller checkout itself is deliberately NOT handled here —
it keeps ``actions/checkout`` on every host; see
``docs/design/host-source-service.md``.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

MIRROR_DIR_ENV = "GATE_HUB_GIT_MIRROR_DIR"
MODE_FILE_NAME = "SOURCE-MODE"
CLIENT_NAME = "git-source-prepare"
CLIENT_MARKER = "GIT-SOURCE-PREPARE-V1"
# Failure annotations carry the raw protocol line only up to this many chars;
# the line is evidence for the operator, not a field to parse.
CLIENT_LINE_DETAIL_LIMIT = 512
SOURCE_MARKER = "GATE-SOURCE-V1"
LOCK_NAME = "consume.lock"
DEMAND_REF = "refs/demand/{sha}"
BUDGET_ENV = "GATE_SOURCE_BUDGET_SECS"
DEFAULT_BUDGET_SECS = 180

MODE_ORIGIN = "origin"
MODE_SERVICE = "service"
MODES = (MODE_ORIGIN, MODE_SERVICE)
ALLOWED_ORGANIZATION = "zlxlabs"

REPOSITORY_ENV = "GATE_CHECKOUT_REPOSITORY"
REF_ENV = "GATE_CHECKOUT_REF"
PATH_ENV = "GATE_CHECKOUT_PATH"
SPARSE_ENV = "GATE_CHECKOUT_SPARSE"

SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
REPOSITORY_RE = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")

# Stable failure literals.  Every service-path failure class is one of these;
# they are the operator-facing grep handles for a red job.
MIRROR_DIR_MISSING = "SOURCE-MIRROR-DIR-MISSING"
MODE_UNREADABLE = "SOURCE-MODE-UNREADABLE"
MODE_INVALID = "SOURCE-MODE-INVALID"
MODE_NOT_SERVICE = "SOURCE-MODE-NOT-SERVICE"
REPOSITORY_REJECTED = "SOURCE-REPOSITORY-REJECTED"
COMMIT_INVALID = "SOURCE-COMMIT-INVALID"
CLIENT_MISSING = "SOURCE-CLIENT-MISSING"
CLIENT_CONTRACT = "SOURCE-CLIENT-CONTRACT"
CLIENT_FAILED = "SOURCE-CLIENT-FAILED"
DEADLINE_EXCEEDED = "SOURCE-DEADLINE-EXCEEDED"
LOCK_UNREADABLE = "SOURCE-LOCK-UNREADABLE"
LOCK_TIMEOUT = "SOURCE-LOCK-TIMEOUT"
MIRROR_UNREADABLE = "SOURCE-MIRROR-UNREADABLE"
DEMAND_REF_MISSING = "SOURCE-DEMAND-REF-MISSING"
FETCH_FAILED = "SOURCE-FETCH-FAILED"
CHECKOUT_FAILED = "SOURCE-CHECKOUT-FAILED"
PIN_MISMATCH = "SOURCE-PIN-MISMATCH"
PATH_MISSING = "SOURCE-PATH-MISSING"


class SourceError(RuntimeError):
    """A service-path failure carrying one of the SOURCE-* literals."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def fail(code: str, detail: str = "") -> None:
    raise SourceError(code, detail)


def mirror_root() -> Path:
    raw = os.environ.get(MIRROR_DIR_ENV, "").strip()
    if not raw:
        fail(MIRROR_DIR_MISSING, MIRROR_DIR_ENV)
    root = Path(raw)
    if not root.is_dir():
        fail(MIRROR_UNREADABLE, f"{root} is not a directory")
    return root


def declared_mode() -> str:
    """`origin` or `service`; a present but unreadable/invalid declaration fails.

    Byte-exact, matching the shell side: the file must be exactly `origin\\n` or
    `service\\n`.  Read as bytes so a CRLF file cannot be normalized into a pass.
    """
    if not os.environ.get(MIRROR_DIR_ENV, "").strip():
        return MODE_ORIGIN
    mode_file = mirror_root() / MODE_FILE_NAME
    try:
        raw = mode_file.read_bytes()
    except OSError as error:
        fail(MODE_UNREADABLE, f"{mode_file}: {error.strerror}")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        fail(MODE_INVALID, f"{mode_file}: {error}")
    if text not in {f"{MODE_ORIGIN}\n", f"{MODE_SERVICE}\n"}:
        fail(MODE_INVALID, f"{mode_file}: {raw!r} is not exactly 'origin\\n' or 'service\\n'")
    return text.strip()


def require_service_mode() -> str:
    mode = declared_mode()
    if mode != MODE_SERVICE:
        fail(MODE_NOT_SERVICE, f"declared mode is {mode!r}")
    return mode


def budget_secs() -> int:
    raw = os.environ.get(BUDGET_ENV, "").strip()
    if not raw:
        return DEFAULT_BUDGET_SECS
    value = int(raw)
    if value < 1:
        fail(DEADLINE_EXCEEDED, f"{BUDGET_ENV}={value} must be >= 1")
    return value


def check_repository(repository: str) -> str:
    if not REPOSITORY_RE.match(repository) or repository.split("/", 1)[0] != ALLOWED_ORGANIZATION:
        fail(REPOSITORY_REJECTED, repository)
    return repository


def check_commit(commit: str) -> str:
    if not SHA_RE.match(commit):
        fail(COMMIT_INVALID, commit)
    return commit


def _git(args: Sequence[str], *, cwd: Path | None = None, code: str, timeout: int) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(code, f"git {' '.join(args)} exceeded {timeout}s")
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip().splitlines()
        fail(code, f"git {' '.join(args)}: {detail[-1] if detail else error.returncode}")
    return completed.stdout.strip()


def _client_line(stderr: str) -> dict[str, Any]:
    """Parse the one protocol line a successful (rc=0) client reply must carry.

    Only what this repo consumes is validated: exactly one protocol line, a JSON
    object, and `source` (it feeds the caller's telemetry).  `status`,
    `repository` and `commit_sha` are the client's own contract — it already
    checked them before exiting 0, and fetching refs/demand/<sha> is the real
    guard against a wrong or incomplete answer.
    """
    lines = [line for line in stderr.splitlines() if line.startswith(f"{CLIENT_MARKER} ")]
    if len(lines) != 1:
        fail(CLIENT_CONTRACT, f"expected exactly one {CLIENT_MARKER} line, got {len(lines)}")
    try:
        payload = json.loads(lines[0][len(CLIENT_MARKER) + 1 :])
    except json.JSONDecodeError as error:
        fail(CLIENT_CONTRACT, f"{error}")
    if not isinstance(payload, dict):
        fail(CLIENT_CONTRACT, "reply must be a JSON object")
    if payload.get("source") not in ("hit", "cold"):
        fail(CLIENT_CONTRACT, "source is not hit or cold")
    return payload


def left(deadline_epoch: int) -> int:
    """Whole seconds left before the one budget this checkout is allowed."""
    return deadline_epoch - int(time.time())


def remaining(deadline_epoch: int) -> int:
    """Seconds left, or fail: no step may start a fresh full budget."""
    seconds = left(deadline_epoch)
    if seconds < 1:
        fail(DEADLINE_EXCEEDED, f"budget exhausted at deadline {deadline_epoch}")
    return seconds


def run_client(repository: str, commit: str, deadline_epoch: int) -> dict[str, Any]:
    """Prepare `commit` through the host service.  Never touches the network.

    The exit code is the verdict: rc!=0 is CLIENT-FAILED with the exit code and
    the truncated raw protocol line — the failure reply's fields belong to the
    producer and are never validated.  rc=0 only checks what this repo consumes
    (see `_client_line`).
    """
    client = mirror_root() / CLIENT_NAME
    if not os.access(client, os.X_OK):
        fail(CLIENT_MISSING, str(client))
    argv = [
        str(client),
        "--repository", repository,
        "--commit", commit,
        "--deadline-epoch", str(deadline_epoch),
    ]
    try:
        completed = subprocess.run(
            argv, check=False, text=True, capture_output=True, timeout=remaining(deadline_epoch)
        )
    except subprocess.TimeoutExpired:
        fail(DEADLINE_EXCEEDED, f"client exceeded deadline {deadline_epoch}")
    if completed.returncode != 0:
        lines = [line for line in completed.stderr.splitlines() if line.startswith(f"{CLIENT_MARKER} ")]
        detail = (
            f"exit={completed.returncode} line={lines[0][:CLIENT_LINE_DETAIL_LIMIT]}" if lines
            else f"exit={completed.returncode} no {CLIENT_MARKER} line"
        )
        fail(CLIENT_FAILED, detail)
    return _client_line(completed.stderr)


@contextlib.contextmanager
def shared_consume_lock(mirror_repo: Path, deadline_epoch: int) -> Iterator[None]:
    """Bounded shared lock on the mirror; released before anything else runs."""
    lock = mirror_repo / LOCK_NAME
    try:
        handle = os.open(lock, os.O_RDONLY)
    except OSError as error:
        fail(LOCK_UNREADABLE, f"{lock}: {error.strerror}")
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if left(deadline_epoch) <= 0:
                    fail(LOCK_TIMEOUT, f"{lock} still held at deadline {deadline_epoch}")
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def prepare(repository: str, commit: str, deadline_epoch: int) -> tuple[Path, dict[str, Any]]:
    """Client first, then the lock — the client must never run under the lock."""
    check_repository(repository)
    check_commit(commit)
    payload = run_client(repository, commit, deadline_epoch)
    mirror_repo = mirror_root() / f"{repository}.git"
    if not (mirror_repo / "objects").is_dir():
        fail(MIRROR_UNREADABLE, str(mirror_repo))
    return mirror_repo, payload


def fetch_demand_ref(repo: Path, mirror_repo: Path, commit: str, *, deadline_epoch: int) -> None:
    """Copy the prepared closure into the job repository as its own objects.

    The lock waits on the caller's deadline, never on a fresh budget of its own.
    """
    with shared_consume_lock(mirror_repo, deadline_epoch):
        demand_ref = DEMAND_REF.format(sha=commit)
        resolved = _git(
            ["--git-dir", str(mirror_repo), "rev-parse", "--verify", "--quiet", f"{demand_ref}^{{commit}}"],
            code=DEMAND_REF_MISSING,
            timeout=remaining(deadline_epoch),
        )
        if resolved != commit:
            fail(PIN_MISMATCH, f"{mirror_repo}:{demand_ref} resolves to {resolved}, expected {commit}")
        _git(
            ["fetch", "--no-tags", "--no-recurse-submodules", str(mirror_repo), demand_ref],
            cwd=repo,
            code=FETCH_FAILED,
            timeout=remaining(deadline_epoch),
        )


def checkout_from_environment(timeout: int | None = None) -> dict[str, Any]:
    """Materialize GATE_CHECKOUT_* through the service (same env contract as
    `gate_bounded_retry.py checkout`, which delegates here in service mode).

    The destination is always an explicit directory below the workspace (the
    workflow_sha tool-sparse sites); the workspace root is caller checkout's
    domain and stays on `actions/checkout`.
    """
    require_service_mode()
    repository = os.environ.get(REPOSITORY_ENV, "").strip()
    ref = os.environ.get(REF_ENV, "").strip()
    if not repository or not ref:
        fail(REPOSITORY_REJECTED, f"{REPOSITORY_ENV} and {REF_ENV} are required")
    relative = os.environ.get(PATH_ENV, "").strip()
    if not relative or relative == ".":
        fail(PATH_MISSING, f"{PATH_ENV} must name a destination directory below the workspace")
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    paths = [line.strip() for line in os.environ.get(SPARSE_ENV, "").splitlines() if line.strip()]
    return checkout(repository, ref, workspace / relative, paths, timeout=timeout)


def checkout(
    repository: str,
    ref: str,
    dest: Path,
    paths: Sequence[str] = (),
    *,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Full (non-shallow) materialization of `ref` at `dest` from the mirror.

    `dest` is rebuilt from scratch so a reused directory never carries
    residuals from a previous job.
    """
    require_service_mode()
    budget = budget_secs() if timeout is None else timeout
    started = time.monotonic()
    # Epoch seconds: the client is handed this deadline as --deadline-epoch, and
    # every remaining-budget check below compares against the same clock.
    deadline = int(time.time()) + budget
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    mirror_repo, payload = prepare(repository, ref, deadline)
    _git(["init", "--quiet"], cwd=dest, code=CHECKOUT_FAILED, timeout=remaining(deadline))
    if paths:
        _git(["sparse-checkout", "init", "--no-cone"], cwd=dest, code=CHECKOUT_FAILED, timeout=remaining(deadline))
        try:
            subprocess.run(
                ["git", "sparse-checkout", "set", "--no-cone", "--stdin"],
                cwd=dest, check=True, text=True, input="\n".join(paths) + "\n",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=remaining(deadline),
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
            fail(CHECKOUT_FAILED, f"git sparse-checkout set failed: {error}")
    fetch_demand_ref(dest, mirror_repo, ref, deadline_epoch=deadline)
    _git(["checkout", "--force", "--detach", ref], cwd=dest, code=CHECKOUT_FAILED, timeout=remaining(deadline))
    head = _git(["rev-parse", "HEAD"], cwd=dest, code=CHECKOUT_FAILED, timeout=remaining(deadline))
    if head.lower() != ref.lower():
        fail(PIN_MISMATCH, f"checked out {head}, expected {ref}")
    for relative in paths:
        if not (dest / relative).exists():
            fail(PATH_MISSING, f"sparse path missing after checkout: {relative}")
    return {
        "mode": MODE_SERVICE,
        "step": "checkout",
        "source": payload.get("source", "unknown"),
        "reason": "ok",
        "repository": repository,
        "commit_sha": ref,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def has_commit(repo: Path, sha: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def ensure_commits(
    repo: Path, repository: str, base_sha: str, head_sha: str, *, timeout: int | None = None
) -> None:
    """Make base/head available in the job repository from the mirror."""
    require_service_mode()
    budget = budget_secs() if timeout is None else timeout
    deadline = int(time.time()) + budget
    started = time.monotonic()
    for sha in (base_sha, head_sha):
        if has_commit(repo, sha):
            continue
        mirror_repo, _ = prepare(repository, sha, deadline)
        fetch_demand_ref(repo, mirror_repo, sha, deadline_epoch=deadline)
    emit({
        "mode": MODE_SERVICE,
        "step": "ensure",
        "source": "present" if has_commit(repo, base_sha) else "cold",
        "reason": "ok",
        "repository": repository,
        "commit_sha": head_sha,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    })


def emit(payload: dict[str, Any]) -> None:
    print(f"{SOURCE_MARKER} {json.dumps(payload, separators=(',', ':'), sort_keys=True)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ["mode"]:
            emit({"mode": declared_mode(), "step": "mode", "reason": "ok", "elapsed_ms": 0})
            return 0
        if args[:1] == ["checkout"]:
            emit(checkout_from_environment())
            return 0
        raise SystemExit(f"usage: gate_source.py mode|checkout (got {args!r})")
    except SourceError as error:
        print(f"{SOURCE_MARKER} {json.dumps({'mode': MODE_SERVICE, 'step': 'error', 'reason': error.code, 'detail': error.detail}, separators=(',', ':'), sort_keys=True)}", file=sys.stderr)
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
