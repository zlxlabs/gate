#!/usr/bin/env python3
"""Bounded retry for workflow_sha git checkout and Silo MagicDNS (gate#190)."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

RETRY_ATTEMPTS_ENV = "GATE_NET_RETRY_ATTEMPTS"
RETRY_BACKOFF_ENV = "GATE_NET_RETRY_BACKOFF_SECS"
ATTEMPT_TIMEOUT_ENV = "GATE_NET_ATTEMPT_TIMEOUT_SECS"
LOW_SPEED_LIMIT_ENV = "GATE_HTTP_LOW_SPEED_LIMIT"
LOW_SPEED_TIME_ENV = "GATE_HTTP_LOW_SPEED_TIME"
GIT_BIN_ENV = "GATE_BOUNDED_RETRY_GIT"
TOKEN_ENV = "GATE_GITHUB_TOKEN"
REPOSITORY_ENV = "GATE_CHECKOUT_REPOSITORY"
REF_ENV = "GATE_CHECKOUT_REF"
PATH_ENV = "GATE_CHECKOUT_PATH"
SPARSE_ENV = "GATE_CHECKOUT_SPARSE"
ORIGIN_URL_ENV = "GATE_CHECKOUT_ORIGIN_URL"
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECS = (5, 15)
DEFAULT_ATTEMPT_TIMEOUT_SECS = 25
DEFAULT_LOW_SPEED_LIMIT = 1000
DEFAULT_LOW_SPEED_TIME = 20
MAGICDNS_ERROR = (
    "Silo MagicDNS lookup failed; tailnet 不可达 "
    "(hosted runner 或容器无 100.100.100.100)。SILO_ENDPOINT="
)
T = TypeVar("T")

def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise SystemExit(f"{name} must be >= 1")
    return value

def retry_attempts() -> int:
    return _env_int(RETRY_ATTEMPTS_ENV, DEFAULT_RETRY_ATTEMPTS)

def retry_backoff_secs() -> tuple[int, ...]:
    raw = os.environ.get(RETRY_BACKOFF_ENV, "").strip()
    if not raw:
        return DEFAULT_RETRY_BACKOFF_SECS
    values = tuple(int(part) for part in raw.split())
    if any(item < 0 for item in values):
        raise SystemExit(f"{RETRY_BACKOFF_ENV} must be >= 0")
    return values or DEFAULT_RETRY_BACKOFF_SECS

def attempt_timeout_secs() -> int:
    return _env_int(ATTEMPT_TIMEOUT_ENV, DEFAULT_ATTEMPT_TIMEOUT_SECS)

def worst_case_secs() -> int:
    backoff = retry_backoff_secs()
    sleeps = sum(backoff[min(i, len(backoff) - 1)] for i in range(retry_attempts() - 1))
    return retry_attempts() * attempt_timeout_secs() + sleeps

def run_with_retries(operation: Callable[[], T], *, label: str) -> T:
    attempts = retry_attempts()
    backoff = retry_backoff_secs()
    last_error: Exception | None = None
    for index in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            print(f"{label} attempt {index + 1}/{attempts} failed: {exc}", file=sys.stderr)
            if index + 1 >= attempts:
                break
            pause = backoff[min(index, len(backoff) - 1)]
            if pause:
                time.sleep(pause)
    assert last_error is not None
    raise last_error

def _temp() -> Path:
    return Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TMPDIR") or "/tmp")

def _workspace() -> Path:
    return Path(os.environ.get("GITHUB_WORKSPACE") or os.getcwd())

def write_git_http_config(token: str, origin: str) -> Path:
    parsed = urlparse(origin)
    path = _temp() / "gate-git-http.conf"
    lines = [
        "[http]\n",
        f"\tlowSpeedLimit = {_env_int(LOW_SPEED_LIMIT_ENV, DEFAULT_LOW_SPEED_LIMIT)}\n",
        f"\tlowSpeedTime = {_env_int(LOW_SPEED_TIME_ENV, DEFAULT_LOW_SPEED_TIME)}\n",
    ]
    if token and parsed.scheme in {"http", "https"} and parsed.netloc:
        auth = base64.b64encode(f"x-access-token:{token}".encode("ascii")).decode("ascii")
        lines += [f'[http "{parsed.scheme}://{parsed.netloc}/"]\n', f"\textraheader = AUTHORIZATION: basic {auth}\n"]
    lines += ["[safe]\n", "\tdirectory = *\n"]
    path.write_text("".join(lines), encoding="ascii")
    path.chmod(0o600)
    return path

def origin_url(repository: str) -> str:
    override = os.environ.get(ORIGIN_URL_ENV, "").strip()
    return override or f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com').rstrip('/')}/{repository}.git"

def git_argv(*args: str) -> list[str]:
    return [os.environ.get(GIT_BIN_ENV, "").strip() or "git", *args]

def run_git(args: Sequence[str], *, cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            git_argv(*args), cwd=cwd, env=env, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args)} exceeded {timeout}s attempt cap") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git {' '.join(args)} failed: {(exc.stderr or exc.stdout or str(exc)).strip()}") from exc

def checkout_once(dest: Path, repository: str, ref: str, paths: list[str], env: dict[str, str], timeout: int) -> None:
    workspace_root = dest.resolve() == _workspace().resolve()
    if workspace_root:
        git_dir = dest / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)
        dest.mkdir(parents=True, exist_ok=True)
    else:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
    origin = origin_url(repository)
    parsed = urlparse(origin)
    if parsed.username or parsed.password:
        raise SystemExit("checkout origin URL must not contain credentials")
    run_git(["init", "--quiet"], cwd=dest, env=env, timeout=timeout)
    run_git(["remote", "add", "origin", origin], cwd=dest, env=env, timeout=timeout)
    if paths:
        run_git(["sparse-checkout", "init", "--no-cone"], cwd=dest, env=env, timeout=timeout)
        try:
            subprocess.run(
                git_argv("sparse-checkout", "set", "--no-cone", "--stdin"),
                cwd=dest, env=env, check=True, text=True, input="\n".join(paths) + "\n",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"git sparse-checkout set failed: {exc}") from exc
    run_git(["fetch", "--no-tags", "--no-recurse-submodules", "--depth", "1", "origin", ref], cwd=dest, env=env, timeout=timeout)
    run_git(["checkout", "--force", "FETCH_HEAD"], cwd=dest, env=env, timeout=timeout)
    head = run_git(["rev-parse", "HEAD"], cwd=dest, env=env, timeout=timeout).stdout.strip()
    if len(ref) == 40 and all(c in "0123456789abcdefABCDEF" for c in ref) and head.lower() != ref.lower():
        raise RuntimeError(f"checked out {head}, expected {ref}")
    for relative in paths:
        if not (dest / relative).exists():
            raise RuntimeError(f"sparse path missing after checkout: {relative}")

def cmd_checkout() -> int:
    repository = os.environ.get(REPOSITORY_ENV, "").strip()
    ref = os.environ.get(REF_ENV, "").strip()
    if not repository or not ref:
        raise SystemExit(f"{REPOSITORY_ENV} and {REF_ENV} are required")
    relative = os.environ.get(PATH_ENV, "").strip()
    dest = _workspace() / relative if relative not in {"", "."} else _workspace()
    paths = [line.strip() for line in os.environ.get(SPARSE_ENV, "").splitlines() if line.strip()]
    config_path = write_git_http_config(os.environ.get(TOKEN_ENV, "").strip(), origin_url(repository))
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = str(config_path)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_CONFIG_COUNT", None)
    try:
        run_with_retries(
            lambda: checkout_once(dest, repository, ref, paths, env, attempt_timeout_secs()),
            label="workflow_sha checkout",
        )
    finally:
        try:
            config_path.unlink()
        except OSError:
            pass
    return 0

def cmd_magicdns(silo_store: str, endpoint: str, nameserver: str) -> int:
    if not silo_store or not endpoint:
        raise SystemExit("magicdns requires --silo-store and --endpoint")
    timeout = attempt_timeout_secs()

    def attempt() -> str:
        try:
            completed = subprocess.run(
                [sys.executable, silo_store, "magicdns", "--endpoint", endpoint, "--nameserver", nameserver],
                check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{MAGICDNS_ERROR}{endpoint} (attempt exceeded {timeout}s)") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError((exc.stderr or exc.stdout or f"{MAGICDNS_ERROR}{endpoint}").strip()) from exc
        text = completed.stdout.strip()
        if not text:
            raise RuntimeError(f"{MAGICDNS_ERROR}{endpoint}")
        return text

    try:
        print(run_with_retries(attempt, label="MagicDNS"))
    except Exception as exc:
        message = str(exc)
        print(message if "MagicDNS" in message else f"{MAGICDNS_ERROR}{endpoint} ({exc})", file=sys.stderr)
        return 1
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("usage: gate_bounded_retry.py checkout|magicdns")
    if args[0] == "checkout":
        return cmd_checkout()
    if args[0] == "magicdns":
        silo_store = os.environ.get("SILO_STORE", "")
        endpoint = os.environ.get("SILO_ENDPOINT", "")
        nameserver = "100.100.100.100"
        rest = args[1:]
        while rest:
            if rest[0] == "--silo-store" and len(rest) > 1:
                silo_store, rest = rest[1], rest[2:]
            elif rest[0] == "--endpoint" and len(rest) > 1:
                endpoint, rest = rest[1], rest[2:]
            elif rest[0] == "--nameserver" and len(rest) > 1:
                nameserver, rest = rest[1], rest[2:]
            else:
                raise SystemExit(f"unknown magicdns arg {rest[0]}")
        return cmd_magicdns(silo_store, endpoint, nameserver)
    raise SystemExit(f"unknown command {args[0]}")

if __name__ == "__main__":
    raise SystemExit(main())
