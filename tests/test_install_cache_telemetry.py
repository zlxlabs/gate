"""Install-step cache telemetry (gate-hub 卡 C). Producer run block is executed
verbatim; assertions read the JSON bytes the producer wrote, not YAML text.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
CACHE_DIR_ENV_VARS = ("UV_CACHE_DIR", "npm_config_cache", "pnpm_config_store_dir")
UNAVAILABLE = "INSTALL-CACHE-TELEMETRY-UNAVAILABLE"
RUNNER = "gatehub-a1b2c3d4e5f6-slot-3"
_PYPROJECT = (
    "[project]\nname = \"cache-telemetry-probe\"\nversion = \"0.0.1\"\n"
    "requires-python = \">=3.12\"\ndependencies = [\"packaging>=24\"]\n"
)


def _install_step():
    raw = yaml.safe_load(WORKFLOW.read_text())
    return next(s for s in raw["jobs"]["quality"]["steps"] if s.get("id") == "install")


def _run_install_producer(tmp_path, *, lane, lockfile, stubs, env_extra,
                          runner_name=RUNNER, pre_run=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    if lockfile: (repo / lockfile).touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    result_path = tmp_path / "install-result.json"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "INSTALL_RESULT_PATH": str(result_path), "GATE_RUNNER_LANE": lane,
           "RUNNER_NAME": runner_name}
    for var in CACHE_DIR_ENV_VARS:
        env.pop(var, None)
    env.update(env_extra)
    if pre_run: pre_run(repo, env)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _install_step()["run"]],
        cwd=repo, env=env, capture_output=True, text=True)
    raw_text = result_path.read_text()
    assert raw_text.count("\n") == 1 and raw_text.endswith("\n")
    return result, json.loads(raw_text)


def test_install_step_static_cache_telemetry_contract():
    step = _install_step()
    run = step["run"]
    assert step["env"]["GATE_RUNNER_LANE"] == "${{ inputs.runner }}"
    assert UNAVAILABLE in run
    assert run.index("measure_cache_before \"$cache_dir\"") < run.index("uv sync --frozen")
    assert run.index("cache_after=$(du -sb") > run.index("pnpm install --frozen-lockfile")
    assert run.count("|| true") == 2
    assert run.rstrip().endswith('[ "$status" != "failed" ]')
    assert 'elif [ "$cache_scope" = "uv-per-slot" ]; then' in run


def test_install_step_self_uv_hit_reports_zero_delta_and_scope(tmp_path):
    cache_dir = tmp_path / "uv-cache"
    cache_dir.mkdir()
    (cache_dir / "existing.bin").write_bytes(b"x" * 16)
    result, payload = _run_install_producer(
        tmp_path, lane="self", lockfile="uv.lock",
        stubs={"uv": "#!/bin/sh\nexit 0\n"},
        env_extra={"UV_CACHE_DIR": str(cache_dir)},
    )
    assert result.returncode == 0, result.stderr
    assert payload == {
        "ecosystem": "uv", "status": "ok", "duration_s": payload["duration_s"],
        "cache_hit": True, "cache_scope": "uv-per-slot",
        "runner_name": RUNNER, "cache_new_bytes": 0,
    }
    assert UNAVAILABLE not in result.stdout + result.stderr


@pytest.mark.parametrize("ecosystem,lockfile,cache_var,write,expect_bytes", [
    ("npm", "package-lock.json", "npm_config_cache", True, 6),
    ("pnpm", "pnpm-lock.yaml", "pnpm_config_store_dir", True, 6),
    ("npm", "package-lock.json", "npm_config_cache", False, 0),
])
def test_install_step_self_shared_cache_records_bytes_hit_stays_null(
        tmp_path, ecosystem, lockfile, cache_var, write, expect_bytes):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    body = f'echo hello > "${cache_var}/new.bin"\n' if write else ""
    result, payload = _run_install_producer(
        tmp_path, lane="self", lockfile=lockfile,
        stubs={ecosystem: f"#!/bin/sh\n{body}exit 0\n"},
        env_extra={cache_var: str(cache_dir)},
    )
    assert result.returncode == 0, result.stderr
    assert payload["ecosystem"] == ecosystem and payload["status"] == "ok"
    assert payload["cache_hit"] is None and payload["cache_scope"] == "host-shared"
    assert payload["cache_new_bytes"] == expect_bytes
    assert UNAVAILABLE not in result.stdout + result.stderr


def test_install_step_du_failure_fails_open_with_null_fields(tmp_path):
    cache_dir = tmp_path / "uv-cache"
    cache_dir.mkdir()
    (cache_dir / "f.bin").write_bytes(b"x" * 8)
    result, payload = _run_install_producer(
        tmp_path, lane="self", lockfile="uv.lock",
        stubs={"uv": "#!/bin/sh\nexit 0\n", "du": "#!/bin/sh\nexit 1\n"},
        env_extra={"UV_CACHE_DIR": str(cache_dir)},
    )
    assert result.returncode == 0, result.stderr
    assert payload["status"] == "ok"
    assert payload["cache_hit"] is None and payload["cache_new_bytes"] is None
    assert payload["cache_scope"] == "uv-per-slot"
    assert UNAVAILABLE in result.stdout


@pytest.mark.parametrize("lane,lockfile,stubs,env_extra", [
    ("hosted", "uv.lock", {"uv": "#!/bin/sh\nexit 0\n"}, {"UV_CACHE_HIT": "true"}),
    ("hosted", "package-lock.json", {"npm": "#!/bin/sh\nexit 0\n"}, {}),
    ("self", None, {}, {}),
])
def test_install_step_null_telemetry_paths_stay_silent(tmp_path, lane, lockfile, stubs, env_extra):
    result, payload = _run_install_producer(tmp_path, lane=lane, lockfile=lockfile,
                                            stubs=stubs, env_extra=env_extra)
    assert result.returncode == 0, result.stderr
    if lane == "hosted" and lockfile == "uv.lock":
        assert payload["cache_hit"] is True
    else:
        assert payload["cache_hit"] is None
    assert payload["cache_scope"] is None and payload["cache_new_bytes"] is None
    assert UNAVAILABLE not in result.stdout + result.stderr


def test_install_step_self_missing_cache_env_logs_unavailable(tmp_path):
    result, payload = _run_install_producer(
        tmp_path, lane="self", lockfile="uv.lock",
        stubs={"uv": "#!/bin/sh\nexit 0\n"}, env_extra={},
    )
    assert result.returncode == 0, result.stderr
    assert payload["cache_hit"] is None and payload["cache_new_bytes"] is None
    assert f"{UNAVAILABLE} lane=self ecosystem=uv cache_dir=unset" in result.stdout


def test_install_step_negative_delta_reports_null_not_negative(tmp_path):
    cache_dir = tmp_path / "uv-cache"
    cache_dir.mkdir()
    (cache_dir / "f.bin").write_bytes(b"x" * 8)
    calls = tmp_path / "du-calls"
    du_stub = (
        "#!/bin/sh\n"
        f"n=$(cat {calls} 2>/dev/null || echo 0)\n"
        f"echo $((n + 1)) > {calls}\n"
        'if [ "$n" -eq 0 ]; then echo 100; else echo 40; fi\n'
    )
    result, payload = _run_install_producer(
        tmp_path, lane="self", lockfile="uv.lock",
        stubs={"uv": "#!/bin/sh\nexit 0\n", "du": du_stub},
        env_extra={"UV_CACHE_DIR": str(cache_dir)},
    )
    assert result.returncode == 0, result.stderr
    assert payload["cache_hit"] is None and payload["cache_new_bytes"] is None
    assert UNAVAILABLE in result.stdout


def test_install_step_real_uv_second_sync_reports_zero_delta(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("本机无 uv，无法验证真实命中时字节差")
    cache_dir = tmp_path / "uv-cache"
    cache_dir.mkdir()

    def _warm(repo, env):
        (repo / "pyproject.toml").write_text(_PYPROJECT)
        for args, why in ((["uv", "lock"], "uv lock"),
                          (["uv", "sync", "--frozen"], "uv sync")):
            proc = subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True)
            if proc.returncode != 0:
                pytest.skip(f"{why} 失败（无网络或索引不可达）: " + proc.stderr[:200])
        shutil.rmtree(repo / ".venv")

    result, payload = _run_install_producer(
        tmp_path, lane="self", lockfile=None, stubs={},
        env_extra={"UV_CACHE_DIR": str(cache_dir), "UV_PYTHON": sys.executable},
        pre_run=_warm,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert payload["ecosystem"] == "uv" and payload["status"] == "ok"
    assert payload["cache_scope"] == "uv-per-slot"
    assert payload["cache_hit"] is True and payload["cache_new_bytes"] == 0
    assert UNAVAILABLE not in result.stdout + result.stderr
