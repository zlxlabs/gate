"""Behavioral checks for the inline failure-only silo network diagnostic."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/gate-v2.yml"
STEP_NAME = "Diagnose silo network after job failure"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _diagnostics(workflow):
    return {
        job_id: [step for step in job.get("steps", []) if step.get("name") == STEP_NAME]
        for job_id, job in workflow["jobs"].items()
    }


def _silo_jobs(workflow):
    jobs = set()
    for job_id, job in workflow["jobs"].items():
        if {"SILO_EXEC", "SILO_STORE"} & set((job.get("env") or {})):
            jobs.add(job_id)
        elif any(
            "SILO_EXEC" in step.get("run", "") or "SILO_STORE" in step.get("run", "")
            for step in job.get("steps", [])
        ):
            jobs.add(job_id)
    return jobs


def test_each_silo_job_has_one_failure_only_diagnostic_as_its_last_step():
    workflow = _workflow()
    silo_jobs = _silo_jobs(workflow)
    diagnostics = _diagnostics(workflow)
    assert silo_jobs == {job for job, steps in diagnostics.items() if steps}
    assert silo_jobs
    for job_id in silo_jobs:
        steps = workflow["jobs"][job_id]["steps"]
        assert len(diagnostics[job_id]) == 1
        assert steps[-1] is diagnostics[job_id][0]
        assert diagnostics[job_id][0].get("if") == "failure()"
        assert "GATE_NET_DIAG_JOB" in diagnostics[job_id][0].get("env", {})


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path in {"/minio/health/live", "/zen"} else 404)
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def local_http():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _run_diagnostic(endpoint, local_port, expected):
    workflow = _workflow()
    candidates = [
        step["run"]
        for steps in _diagnostics(workflow).values()
        for step in steps
        if "run" in step
    ]
    assert candidates and len(set(candidates)) == 1
    env = {
        "PATH": os.environ["PATH"],
        "SILO_ENDPOINT": endpoint,
        "GATE_NET_DIAG_JOB": "quality",
        "GATE_NET_DIAG_TAILNET_DNS_HOST": "127.0.0.1",
        "GATE_NET_DIAG_TAILNET_DNS_PORT": str(local_port),
        "GATE_NET_DIAG_GITHUB_URL": f"http://127.0.0.1:{local_port}/zen",
    }
    started = time.monotonic()
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", candidates[0]],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    elapsed = time.monotonic() - started
    assert elapsed <= 30
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("GATE-NET-DIAG-V1 job=quality ")
    assert lines[1] == f"::warning::{lines[0]}"
    values = dict(re.findall(r"([a-z_]+)=([^ ]+)", lines[0]))
    assert values == {
        "job": "quality",
        "tailnet_dns_tcp": expected[0],
        "silo_resolve": "ok",
        "silo_tcp": expected[1],
        "silo_http": expected[2],
        "github_https": "http_200",
    }
    return result.stdout


def test_all_network_layers_pass_against_local_http_service(local_http):
    output = _run_diagnostic(f"http://127.0.0.1:{local_http}", local_http, ("ok", "ok", "http_200"))
    assert "GATE-NET-DIAG-V1" in output


def test_closed_silo_port_is_distinguished_from_other_healthy_layers(local_http):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    _run_diagnostic(
        f"http://127.0.0.1:{closed_port}",
        local_http,
        ("ok", "fail:connect", "fail:curl_7"),
    )
