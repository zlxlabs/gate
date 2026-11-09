#!/usr/bin/env python3
"""Measure the workflow cache around an install or entry-quality command."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _warn(reason: str) -> None:
    print(f"::warning::GATE-INSTALL-RESULT-DEGRADED reason={reason}")


def _ecosystem() -> str:
    if Path("uv.lock").is_file():
        return "uv"
    if Path("package-lock.json").is_file():
        return "npm"
    if Path("pnpm-lock.yaml").is_file():
        return "pnpm"
    if Path("go.sum").is_file():
        return "go-skipped"
    return "none"


def _cache_dir(ecosystem: str) -> str:
    name = {"uv": "UV_CACHE_DIR", "npm": "npm_config_cache",
            "pnpm": "pnpm_config_store_dir"}.get(ecosystem)
    return os.environ.get(name, "") if name else ""


def _size(path: str) -> int:
    result = subprocess.run(["du", "-sb", path], capture_output=True, text=True)
    if result.returncode != 0:
        raise OSError("du failed")
    return int(result.stdout.split()[0])


def _paths() -> tuple[Path, Path]:
    return Path(os.environ["GATE_INSTALL_TELEMETRY_STATE"]), Path(os.environ["INSTALL_RESULT_PATH"])


def _start() -> None:
    ecosystem = _ecosystem()
    lane = os.environ["GATE_RUNNER_LANE"]
    cache_dir = _cache_dir(ecosystem)
    scope = None
    if lane == "self":
        scope = {"uv": "uv-per-slot", "npm": "host-shared", "pnpm": "host-shared"}.get(ecosystem)
    before = None
    if lane == "self" and ecosystem in {"uv", "npm", "pnpm"} and not cache_dir:
        _warn("cache-directory-unset")
    if lane == "self" and cache_dir:
        try:
            before = _size(cache_dir)
        except (OSError, ValueError, IndexError):
            print(f"INSTALL-CACHE-TELEMETRY-UNAVAILABLE lane=self ecosystem={ecosystem} cache_dir={cache_dir}")
            _warn("cache-measurement-unavailable")
    state_path, _ = _paths()
    state_path.write_text(json.dumps({
        "started_at": int(time.time()), "ecosystem": ecosystem, "lane": lane,
        "cache_dir": cache_dir, "cache_scope": scope, "cache_before": before,
        "uv_cache_hit": os.environ.get("UV_CACHE_HIT", ""),
    }), encoding="utf-8")


def _finish(status: str) -> None:
    state_path, result_path = _paths()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    ecosystem = state["ecosystem"]
    cache_new_bytes = None
    cache_hit = None
    if ecosystem == "uv" and state["lane"] != "self":
        if state["uv_cache_hit"] == "true":
            cache_hit = True
        elif state["uv_cache_hit"] == "false":
            cache_hit = False
    if state["lane"] == "self":
        before = state["cache_before"]
        if before is not None:
            try:
                cache_new_bytes = _size(state["cache_dir"]) - before
                if cache_new_bytes < 0:
                    cache_new_bytes = None
                elif state["cache_scope"] == "uv-per-slot":
                    cache_hit = cache_new_bytes == 0
            except (OSError, ValueError, IndexError):
                _warn("cache-measurement-unavailable")
        if ecosystem in {"uv", "npm", "pnpm"} and cache_new_bytes is None:
            print(f"INSTALL-CACHE-TELEMETRY-UNAVAILABLE lane=self ecosystem={ecosystem} cache_dir={state['cache_dir'] or 'unset'}")
    result = {
        "ecosystem": ecosystem,
        "status": status,
        "duration_s": max(0, int(time.time()) - state["started_at"]),
        "cache_hit": cache_hit,
        "cache_scope": state["cache_scope"],
        "runner_name": os.environ["RUNNER_NAME"],
        "cache_new_bytes": cache_new_bytes,
    }
    result_path.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> int:
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "start":
            _start()
        elif len(sys.argv) == 3 and sys.argv[1] == "finish":
            _finish(sys.argv[2])
        else:
            raise ValueError("expected start or finish <status>")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        _warn(f"telemetry-initialization-or-write-failed-{type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
