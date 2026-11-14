"""gate#409: disposition must fetch the audit from its actual source attempt.

E2E drives the real "Resolve current PR head and canonical primary audit"
step script from gate-v2-disposition.yml against a fake Silo CLI and a gh
stub; the step's resolver python heredoc is additionally extracted verbatim
and driven directly.  The authoritative record is the gate terminal envelope
the aggregator uploads on every executed attempt (audit.source_attempt).
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gate-v2-disposition.yml"
STEP_NAME = "Resolve current PR head and canonical primary audit"
REPOSITORY = "zlxlabs/repo"
REPOSITORY_ID = 123
PR_NUMBER = 42
RUN_ID = 777000
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
MARKER = "DISPOSITION-AUDIT-ATTEMPT-UNRESOLVED"


def _step_run() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(
        step["run"]
        for step in workflow["jobs"]["control"]["steps"]
        if step.get("name") == STEP_NAME
    )


def _resolve_heredoc() -> str:
    """The audit-attempt resolver python block, extracted verbatim from the step."""
    blocks = []
    collecting = None
    for line in _step_run().splitlines():
        if collecting is not None:
            if line == "PY":
                blocks.append(collecting)
                collecting = None
            else:
                collecting += line + "\n"
        elif line.endswith("<<'PY'"):
            collecting = ""
    assert collecting is None, "unterminated python heredoc in resolve step"
    assert len(blocks) == 1, "resolve step must carry exactly one python heredoc"
    return blocks[0]


def _terminal_name(attempt: int) -> str:
    return f"gate-terminal-v1-{REPOSITORY_ID}-{HEAD_SHA}-{RUN_ID}-{attempt}"


def _terminal_key(attempt: int) -> str:
    return f"d30/{REPOSITORY_ID}/{_terminal_name(attempt)}/gate-terminal.json"


def _audit_key(attempt: int) -> str:
    return f"d14/{REPOSITORY_ID}/primary-audit-v2-{REPOSITORY_ID}-{HEAD_SHA}-{RUN_ID}-{attempt}/primary-review-audit.json"


def _envelope(*, attempt: int, source_attempt: int | None, available: bool = True) -> dict:
    audit: dict = {"available": available, "source_attempt": source_attempt, "artifact_name": f"x-{attempt}"}
    return {
        "schema_version": 1,
        "kind": "gate_terminal",
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "pr_number": PR_NUMBER,
        "run_id": RUN_ID,
        "run_attempt": attempt,
        "head_sha": HEAD_SHA,
        "audit": audit,
    }


def _audit(*, run_attempt: int) -> dict:
    return {
        "schema_version": 1,
        "kind": "primary_review",
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "pr": PR_NUMBER,
        "run_id": RUN_ID,
        "run_attempt": run_attempt,
        "verdict": "fail",
    }


SILO_FAKE = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    if args[:1] != ["get"]:
        sys.stderr.write("unexpected silo verb: " + repr(args) + "\\n")
        raise SystemExit(64)
    key = args[args.index("--key") + 1]
    dest = args[args.index("--dest") + 1]
    with open(os.environ["GATE_FAKE_SILO_LOG"], "a", encoding="utf-8") as log:
        log.write(key + "\\n")
    store = json.loads(open(os.environ["GATE_FAKE_SILO_STORE"], encoding="utf-8").read())
    if key not in store:
        raise SystemExit(2)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(store[key])
    """
).lstrip()

GH_FAKE = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import os, sys
    if sys.argv[1:3] == ["api", "repos/%s/pulls/%d" % (os.environ["GITHUB_REPOSITORY"], int(os.environ["PR_NUMBER"]))]:
        sys.stdout.write(os.environ["GATE_FAKE_PR_JSON"])
        raise SystemExit(0)
    sys.stderr.write("unexpected gh invocation: " + repr(sys.argv) + "\\n")
    raise SystemExit(64)
    """
).lstrip()


@pytest.fixture()
def host(tmp_path: Path) -> dict:
    store_path = tmp_path / "silo-store.json"
    store_path.write_text("{}", encoding="utf-8")
    log_path = tmp_path / "silo-log"
    log_path.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    silo = bin_dir / "fake-silo-exec"
    silo.write_text(SILO_FAKE)
    silo.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text(GH_FAKE)
    gh.chmod(0o755)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    github_env = tmp_path / "github-env"
    github_env.write_text("", encoding="utf-8")
    pr_json = json.dumps({
        "head": {"sha": HEAD_SHA, "repo": {"full_name": REPOSITORY}},
        "base": {"sha": BASE_SHA},
    })
    return {
        "store": store_path,
        "log": log_path,
        "runner_temp": runner_temp,
        "github_env": github_env,
        "pr_json": pr_json,
    }


def _silo_events(host: dict, entries: dict[str, str]) -> None:
    host["store"].write_text(json.dumps(entries), encoding="utf-8")


def _requested_keys(host: dict) -> list[str]:
    return [line for line in host["log"].read_text(encoding="utf-8").splitlines() if line]


def _env(host: dict, *, attempt: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "RUNNER_TEMP": str(host["runner_temp"]),
        "GITHUB_ENV": str(host["github_env"]),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REPOSITORY_ID": str(REPOSITORY_ID),
        "GH_TOKEN": "test-token",
        "GH_REPO": REPOSITORY,
        "PR_NUMBER": str(PR_NUMBER),
        "PRIMARY_RUN_ID": str(RUN_ID),
        "PRIMARY_RUN_ATTEMPT": str(attempt),
        "AWS_ACCESS_KEY_ID": "test-key",
        "SILO_EXEC": str(host["store"].parent / "bin/fake-silo-exec"),
        "GATE_FAKE_SILO_STORE": str(host["store"]),
        "GATE_FAKE_SILO_LOG": str(host["log"]),
        "GATE_FAKE_PR_JSON": host["pr_json"],
    })
    # bin/ holds the fake gh and silo stubs; gh must be reachable via PATH.
    env["PATH"] = f"{host['store'].parent / 'bin'}{os.pathsep}{env['PATH']}"
    return env


def _run_step(host: dict, *, attempt: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _step_run()],
        env=_env(host, attempt=attempt), cwd=host["runner_temp"],
        capture_output=True, text=True, timeout=60,
    )


def _github_env(host: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in host["github_env"].read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key:
            result[key] = value
    return result


def test_resolved_attempt2_fetches_audit_from_attempt1(host: dict) -> None:
    _silo_events(host, {
        _terminal_key(2): json.dumps(_envelope(attempt=2, source_attempt=1)),
        _audit_key(1): json.dumps(_audit(run_attempt=1)),
    })
    done = _run_step(host, attempt=2)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert _requested_keys(host) == [_terminal_key(2), _audit_key(1)]
    env = _github_env(host)
    assert env["AUDIT_SOURCE_ATTEMPT"] == "1"
    assert env["DISPOSITION_REFERENCED_ATTEMPT"] == "2"
    assert env["CURRENT_HEAD_SHA"] == HEAD_SHA


def test_attempt1_key_identical_to_pre_gate409_behavior(host: dict) -> None:
    # Regression lock: with attempt=1 (audit where the caller says it is) the
    # fetched audit key must stay byte-identical to the pre-gate#409 key.
    _silo_events(host, {
        _terminal_key(1): json.dumps(_envelope(attempt=1, source_attempt=1)),
        _audit_key(1): json.dumps(_audit(run_attempt=1)),
    })
    done = _run_step(host, attempt=1)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert _audit_key(1) in _requested_keys(host)
    env = _github_env(host)
    assert env["AUDIT_PATH"].endswith("current-primary-audit/primary-review-audit.json")
    assert env["CURRENT_AUDIT_DIGEST"]


def test_missing_terminal_fails_fast_without_probing(host: dict) -> None:
    _silo_events(host, {_audit_key(2): json.dumps(_audit(run_attempt=2))})
    done = _run_step(host, attempt=2)
    assert done.returncode != 0
    assert MARKER in done.stderr
    assert "reason=terminal_fetch_failed" in done.stderr
    assert _requested_keys(host) == [_terminal_key(2)]
    assert "AUDIT_SOURCE_ATTEMPT" not in _github_env(host)


def test_terminal_audit_unavailable_fails_fast(host: dict) -> None:
    _silo_events(host, {_terminal_key(2): json.dumps(_envelope(attempt=2, source_attempt=None, available=False))})
    done = _run_step(host, attempt=2)
    assert done.returncode != 0
    assert MARKER in done.stderr
    assert "reason=audit_unavailable_in_terminal" in done.stderr


def _invoke_resolver(payload: str, *, attempt: str = "2", tmp_path: Path) -> subprocess.CompletedProcess:
    terminal = tmp_path / "gate-terminal.json"
    terminal.write_text(payload, encoding="utf-8")
    return subprocess.run(
        ["python3", "-", str(terminal), attempt],
        input=_resolve_heredoc(), capture_output=True, text=True, timeout=30,
    )


def test_resolver_heredoc_prints_source_attempt(tmp_path: Path) -> None:
    done = _invoke_resolver(json.dumps(_envelope(attempt=2, source_attempt=1)), tmp_path=tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "1\n"


@pytest.mark.parametrize(
    "payload,reason",
    [
        (json.dumps({"kind": "something_else"}), "terminal_not_gate_terminal"),
        ("{not json", "terminal_unreadable"),
        (json.dumps(_envelope(attempt=3, source_attempt=1)), "terminal_attempt_mismatch"),
        (json.dumps(_envelope(attempt=2, source_attempt=None, available=False)), "audit_unavailable_in_terminal"),
        (json.dumps(_envelope(attempt=2, source_attempt="1")), "audit_source_attempt_invalid"),
    ],
)
def test_resolver_heredoc_rejects_unresolvable_records(tmp_path: Path, payload: str, reason: str) -> None:
    done = _invoke_resolver(payload, tmp_path=tmp_path)
    assert done.returncode == 1
    assert MARKER in done.stderr
    assert f"reason={reason}" in done.stderr


def test_resolver_heredoc_missing_terminal_file(tmp_path: Path) -> None:
    done = subprocess.run(
        ["python3", "-", str(tmp_path / "absent.json"), "2"],
        input=_resolve_heredoc(), capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 1
    assert MARKER in done.stderr
    assert "reason=terminal_missing" in done.stderr


def test_resolve_step_carries_terminal_authority_contract() -> None:
    """The step must read the gate terminal envelope for the referenced attempt
    (audit.source_attempt is authoritative), fail fast with the marker, build
    the audit key from the resolved attempt, and keep both attempts on record."""
    run_text = _step_run()
    assert "gate-terminal-v1-${GITHUB_REPOSITORY_ID}-${head_sha}-${PRIMARY_RUN_ID}-${PRIMARY_RUN_ATTEMPT}" in run_text
    assert MARKER in run_text
    assert "primary-audit-v2-${GITHUB_REPOSITORY_ID}-${head_sha}-${PRIMARY_RUN_ID}-${audit_attempt}" in run_text
    assert "DISPOSITION-AUDIT-ATTEMPT-RESOLVED audit_attempt=" in run_text
    github_env_block = run_text.split('>> "$GITHUB_ENV"')[0].rsplit("{", 1)[1]
    assert "AUDIT_SOURCE_ATTEMPT=$audit_attempt" in github_env_block
    assert "DISPOSITION_REFERENCED_ATTEMPT=$PRIMARY_RUN_ATTEMPT" in github_env_block
