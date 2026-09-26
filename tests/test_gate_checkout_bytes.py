"""Behavioral checks for checkout receive-byte measurements in v2 workflows."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "gate-v2": REPO_ROOT / ".github/workflows/gate-v2.yml",
    "gate-shadow-v2": REPO_ROOT / ".github/workflows/gate-shadow-v2.yml",
}
BEFORE_STEP = "Record network bytes before checkout"
AFTER_STEP = "Record network bytes after checkout"


def _checkouts():
    found = []
    for workflow_name, path in WORKFLOWS.items():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in workflow["jobs"].items():
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    found.append((workflow_name, job_id, steps, index, step))
    return found


def test_every_checkout_is_bracketed_by_fail_open_measurements():
    checkouts = _checkouts()
    assert len(checkouts) == 5
    sequences = {}
    for workflow_name, job_id, steps, index, checkout in checkouts:
        key = (workflow_name, job_id)
        sequences[key] = sequences.get(key, 0) + 1
        if workflow_name == "gate-v2":
            before = steps[index - 2]
            prime = steps[index - 1]
            assert prime.get("name") == "Prime checkout from host Git mirror"
            assert prime.get("if") == checkout.get("if")
            assert prime.get("env", {}).get("GATE_GITHUB_TOKEN") == "${{ github.token }}"
        else:
            before = steps[index - 1]
        after = steps[index + 1]
        assert before.get("name", "").startswith(BEFORE_STEP)
        assert after.get("name", "").startswith(AFTER_STEP)
        assert before.get("if") == checkout.get("if")
        checkout_condition = checkout.get("if")
        expected_after_condition = (
            f"always() && {checkout_condition}" if checkout_condition else "always()"
        )
        assert after.get("if") == expected_after_condition
        for step in (before, after):
            assert step.get("run")
            env = step.get("env", {})
            context = env.get("GATE_CHECKOUT_CONTEXT", "")
            assert context.startswith(f"workflow={workflow_name} job={job_id} step=")
            assert " repo=" in context
            assert env.get("GATE_CHECKOUT_SEQUENCE") == str(sequences[key])
            assert "NET_SYSFS_ROOT" in env
        assert before["env"] == after["env"]


def _measurement_steps(workflow_name, job_id):
    for name, candidate_job, steps, index, _checkout in _checkouts():
        if name == workflow_name and candidate_job == job_id:
            before = next(
                step for step in reversed(steps[:index])
                if step.get("name", "").startswith(BEFORE_STEP)
            )
            return before, steps[index + 1]
    raise AssertionError(f"no checkout found for {workflow_name}/{job_id}")


def _run_block(step, *, sysfs_root, github_env, extra_env=None):
    env = {
        "PATH": os.environ["PATH"],
        "NET_SYSFS_ROOT": str(sysfs_root),
        "GITHUB_ENV": str(github_env),
    }
    env.update(step["env"])
    for key, value in tuple(env.items()):
        if isinstance(value, str):
            env[key] = (
                value.replace("${{ github.repository }}", "owner/repo")
                .replace("${{ job.workflow_repository }}", "owner/repo")
                .replace("${{ vars.NET_SYSFS_ROOT }}", str(sysfs_root))
            )
    env["NET_SYSFS_ROOT"] = str(sysfs_root)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _set_rx_bytes(sysfs_root, interface, value):
    path = sysfs_root / interface / "statistics/rx_bytes"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="ascii")


def _output_values(output):
    lines = output.splitlines()
    assert len(lines) == 1
    prefix, *fields = lines[0].split()
    assert prefix == "GATE-CHECKOUT-BYTES-V1"
    return dict(field.split("=", 1) for field in fields)


def test_extracted_blocks_report_delta_and_fail_open_when_sysfs_is_missing(tmp_path):
    before, after = _measurement_steps("gate-v2", "quality")
    sysfs_root = tmp_path / "sysfs"
    _set_rx_bytes(sysfs_root, "lo", 777)
    _set_rx_bytes(sysfs_root, "eth0", 1000)
    _set_rx_bytes(sysfs_root, "eth1", 2000)
    github_env = tmp_path / "github-env"

    result = _run_block(before, sysfs_root=sysfs_root, github_env=github_env)
    assert result.returncode == 0, result.stderr
    key, start_value = github_env.read_text(encoding="utf-8").strip().split("=", 1)
    assert key == "GATE_CHECKOUT_RX_BYTES_1"
    assert start_value == "3000"

    _set_rx_bytes(sysfs_root, "lo", 888)
    _set_rx_bytes(sysfs_root, "eth0", 1500)
    _set_rx_bytes(sysfs_root, "eth1", 2600)
    result = _run_block(
        after,
        sysfs_root=sysfs_root,
        github_env=github_env,
        extra_env={key: start_value},
    )
    assert result.returncode == 0, result.stderr
    assert _output_values(result.stdout) == {
        "workflow": "gate-v2",
        "job": "quality",
        "step": "quality-1",
        "repo": "owner/repo",
        "rx_bytes": "1100",
    }

    missing_root = tmp_path / "missing-sysfs"
    missing_env = tmp_path / "missing-github-env"
    result = _run_block(before, sysfs_root=missing_root, github_env=missing_env)
    assert result.returncode == 0, result.stderr
    key, start_value = missing_env.read_text(encoding="utf-8").strip().split("=", 1)
    assert start_value == "unavailable"
    result = _run_block(
        after,
        sysfs_root=missing_root,
        github_env=missing_env,
        extra_env={key: start_value},
    )
    assert result.returncode == 0, result.stderr
    assert _output_values(result.stdout)["rx_bytes"] == "unavailable"
