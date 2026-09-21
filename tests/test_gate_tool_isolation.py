"""Contracts for keeping gate-owned tools outside the caller project context."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2.yml"
DISPOSITION_WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2-disposition.yml"
DIFF_COVERAGE_ACTION = REPO_ROOT / ".github/actions/diff-coverage-advisory/action.yml"
SILO_EXEC = REPO_ROOT / "scripts/silo_exec.sh"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _run_blocks(workflow: dict):
    for job in workflow["jobs"].values():
        yield from (step["run"] for step in job.get("steps", []) if "run" in step)


def test_v2_workflows_have_no_package_manager_silo_calls():
    gate_text = GATE_WORKFLOW.read_text(encoding="utf-8")
    disposition_text = DISPOSITION_WORKFLOW.read_text(encoding="utf-8")
    assert gate_text.count("uv run") == 2, "gate-v2 uv run lines must be caller install/test only"
    assert disposition_text.count("uv run") == 0, "disposition must not run uv"
    for workflow in (_load(GATE_WORKFLOW), _load(DISPOSITION_WORKFLOW)):
        for run in _run_blocks(workflow):
            assert 'python3 "$SILO_STORE"' not in run, "Silo commands must use SILO_EXEC"


def test_every_silo_sparse_checkout_includes_wrapper():
    for path in (GATE_WORKFLOW, DISPOSITION_WORKFLOW):
        workflow = _load(path)
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                sparse = (step.get("env") or {}).get("GATE_CHECKOUT_SPARSE", "")
                if "scripts/silo_store.py" in sparse:
                    assert "scripts/silo_exec.sh" in sparse, (
                        f"{path.name}: sparse checkout with silo_store.py omits silo_exec.sh"
                    )


def test_silo_store_jobs_have_absolute_store_and_wrapper_paths():
    for path in (GATE_WORKFLOW, DISPOSITION_WORKFLOW):
        workflow = _load(path)
        for job_name, job in workflow["jobs"].items():
            env = job.get("env") or {}
            if "SILO_STORE" not in env:
                continue
            assert env.get("SILO_EXEC"), f"{path.name}:{job_name} must define SILO_EXEC"
            assert env["SILO_STORE"].startswith("${{ github.workspace }}/"), (
                f"{path.name}:{job_name} SILO_STORE must be workspace-absolute"
            )
            assert env["SILO_EXEC"].startswith("${{ github.workspace }}/"), (
                f"{path.name}:{job_name} SILO_EXEC must be workspace-absolute"
            )


def test_diff_coverage_uses_no_project_mode():
    action = _load(DIFF_COVERAGE_ACTION)
    runs = [step["run"] for step in action["runs"]["steps"] if "run" in step]
    diff_cover_run = next(run for run in runs if "diff-cover" in run)
    assert "uv run --no-project --with diff-cover python3" in diff_cover_run


def _run_with_uv_stub(tmp_path: Path, exit_code: int = 0):
    poisoned = tmp_path / "poisoned"
    poisoned.mkdir()
    (poisoned / "pyproject.toml").write_text(
        '[project]\nname = "poisoned"\nversion = "0.1.0"\ndependencies = ["aiohttp==3.8.5"]\n',
        encoding="utf-8",
    )
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    stub_output = tmp_path / "uv-call.txt"
    uv_stub = stub_bin / "uv"
    uv_stub.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'PWD=%s\\n' \"$PWD\" > \"$STUB_OUTPUT\"\n"
        "for arg in \"$@\"; do printf 'ARG=%s\\n' \"$arg\" >> \"$STUB_OUTPUT\"; done\n"
        "exit \"${STUB_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    uv_stub.chmod(0o755)
    silo_store = tmp_path / "silo_store.py"
    silo_store.write_text("# The uv stub records the invocation before this file is used.\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_bin}:{env['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "SILO_STORE": str(silo_store),
            "STUB_OUTPUT": str(stub_output),
            "STUB_EXIT": str(exit_code),
        }
    )
    result = subprocess.run(
        [str(SILO_EXEC), "get", "--key", "example"],
        cwd=poisoned,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, runner_temp, poisoned, silo_store, stub_output


def test_silo_wrapper_emits_isolated_cwd_and_argv(tmp_path):
    result, runner_temp, poisoned, silo_store, stub_output = _run_with_uv_stub(tmp_path)
    assert result.returncode == 0, result.stderr
    records = stub_output.read_text(encoding="utf-8").splitlines()
    assert Path(records[0].removeprefix("PWD=")).resolve() == runner_temp.resolve()
    assert Path(records[0].removeprefix("PWD=")).resolve() != poisoned.resolve()
    argv = [line.removeprefix("ARG=") for line in records[1:]]
    assert argv == [
        "run",
        "--no-project",
        "--python",
        "3.12",
        "--with",
        "boto3",
        "--",
        "python3",
        str(silo_store),
        "get",
        "--key",
        "example",
    ]


def test_silo_wrapper_transparent_exit_code(tmp_path):
    result, *_ = _run_with_uv_stub(tmp_path, exit_code=2)
    assert result.returncode == 2
