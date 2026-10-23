"""SOURCE-MODE split for disposition counterevidence rerun (gate#373).

E2E runs the real fetch + issue step scripts from gate-v2-disposition.yml
against a bare-mirror fixture. Only git-source-prepare is stubbed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gate-v2-disposition.yml"
REPOSITORY = "zlxlabs/repo"
LITERAL = "locked-behavior"
RELPATH = "src/lock.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "gate-test",
    "GIT_AUTHOR_EMAIL": "gate-test@example.com",
    "GIT_COMMITTER_NAME": "gate-test",
    "GIT_COMMITTER_EMAIL": "gate-test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

CLIENT = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import json, os, subprocess, sys
    options = dict(zip(sys.argv[1::2], sys.argv[2::2]))
    repository = options["--repository"]
    commit = options["--commit"]
    mirror = os.path.join(os.environ["GATE_HUB_GIT_MIRROR_DIR"], repository + ".git")
    upstream = os.path.join(os.environ["GATE_FAKE_UPSTREAM"], repository + ".git")
    real_git = os.environ["GATE_REAL_GIT"]
    with open(os.environ["GATE_PREPARE_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(sys.argv) + "\n")

    def emit(status, code, source="cold"):
        payload = {
            "status": status, "code": code, "repository": repository,
            "commit_sha": commit, "source": source, "elapsed_ms": 0,
        }
        sys.stderr.write("GIT-SOURCE-PREPARE-V1 " + json.dumps(payload, sort_keys=True) + "\n")

    mode = os.environ.get("GATE_FAKE_CLIENT", "ok")
    if mode == "fail":
        emit("error", "SOURCE-SERVICE-UNAVAILABLE", source=None)
        sys.exit(3)
    if not os.path.isdir(os.path.join(mirror, "objects")):
        subprocess.run([real_git, "init", "--bare", "--quiet", mirror], check=True)
        open(os.path.join(mirror, "consume.lock"), "a").close()
    refs = subprocess.run(
        [real_git, "--git-dir", upstream, "for-each-ref", "--points-at", commit,
         "--format=%(refname)", "refs/heads"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    if not refs:
        emit("error", "SOURCE-PREPARE-FAILED", source=None)
        sys.exit(4)
    demand = "refs/demand/" + commit
    subprocess.run(
        [real_git, "-c", "advice.detachedHead=false", "--git-dir", mirror, "fetch",
         "--no-tags", upstream,
         os.environ.get("GATE_FAKE_DEMAND_REF_TARGET", refs[0]) + ":" + demand],
        check=True, capture_output=True,
    )
    emit("ready", None, source="cold")
    """
).lstrip()

GIT_WRAPPER = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import json, os, sys
    count = int(os.environ.get("GIT_CONFIG_COUNT") or "0")
    safe = None
    for i in range(count):
        if os.environ.get("GIT_CONFIG_KEY_%d" % i) == "safe.directory":
            safe = os.environ.get("GIT_CONFIG_VALUE_%d" % i)
    with open(os.environ["GATE_ARGV_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({
            "argv": sys.argv[1:],
            "GIT_NO_REPLACE_OBJECTS": os.environ.get("GIT_NO_REPLACE_OBJECTS"),
            "safe.directory": safe,
        }, sort_keys=True) + chr(10))
    if os.environ.get("GATE_GIT_FETCH_EXIT") and "fetch" in sys.argv[1:]:
        raise SystemExit(int(os.environ["GATE_GIT_FETCH_EXIT"]))
    os.execv(__REAL_GIT__, ["git", *sys.argv[1:]])
    """
).lstrip()


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step_run(name: str) -> str:
    return next(
        step["run"]
        for step in _workflow()["jobs"]["control"]["steps"]
        if step.get("name") == name
    )


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV},
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _seed_caller(root: Path, *, absent: bool) -> dict[str, str]:
    work = root / "work"
    (work / "src").mkdir(parents=True)
    (work / RELPATH).write_text(LITERAL + "\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", RELPATH, cwd=work)
    _git("commit", "-qm", "seed", cwd=work)
    base_sha = _git("rev-parse", "HEAD", cwd=work)
    head_sha = base_sha
    if absent:
        (work / RELPATH).write_text("from official_sdk import Client\n", encoding="utf-8")
        _git("add", RELPATH, cwd=work)
        _git("commit", "-qm", "remove literal", cwd=work)
        head_sha = _git("rev-parse", "HEAD", cwd=work)
    upstream = root / "upstream" / f"{REPOSITORY}.git"
    upstream.mkdir(parents=True)
    _git("init", "--bare", "--quiet", "-b", "main", cwd=upstream)
    _git("remote", "add", "fixture", str(upstream), cwd=work)
    _git(
        "push", "--quiet", "fixture",
        f"{base_sha}:refs/heads/base",
        f"{head_sha}:refs/heads/main",
        cwd=work,
    )
    return {"work": work, "upstream": upstream, "base_sha": base_sha, "head_sha": head_sha}


def _host(tmp_path: Path, *, mode: str = "service", absent: bool = False) -> dict:
    seeded = _seed_caller(tmp_path, absent=absent)
    mirror_root = tmp_path / "cache/git"
    mirror_root.mkdir(parents=True)
    if mode is not None:
        (mirror_root / "SOURCE-MODE").write_text(mode + "\n")
    client = mirror_root / "git-source-prepare"
    client.write_text(CLIENT)
    client.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    (bin_dir / "git").write_text(GIT_WRAPPER.replace("__REAL_GIT__", repr(real_git)))
    (bin_dir / "git").chmod(0o755)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "_gate-disposition-src").symlink_to(ROOT)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    output_dir = runner_temp / "out"
    output_dir.mkdir()
    return {
        **seeded,
        "mirror_root": mirror_root,
        "bin": bin_dir,
        "real_git": real_git,
        "workspace": workspace,
        "runner_temp": runner_temp,
        "output_dir": output_dir,
        "argv_log": tmp_path / "argv.log",
        "prepare_log": tmp_path / "prepare.log",
        "github_env": tmp_path / "github-env",
        "github_output": tmp_path / "github-output",
    }


def _apply_github_env(path: Path, env: dict[str, str]) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key:
            env[key] = value


def _argv_events(path: Path) -> list[dict]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _joined_argv(events: list[dict]) -> str:
    return "\n".join("git " + " ".join(event["argv"]) for event in events)


def _scope(head_sha: str, base_sha: str) -> dict:
    return {
        "repository_id": 123,
        "pr_number": 42,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "diff_digest": "d" * 64,
        "policy_version": "policy-v1",
        "policy_digest": "p" * 64,
        "tier": "personal",
        "caller_sha": "c" * 40,
        "reusable_workflow_sha": "w" * 40,
    }


def _audit(head_sha: str, base_sha: str) -> dict:
    return {
        **_scope(head_sha, base_sha),
        "repository": REPOSITORY,
        "pr": 42,
        "run_id": 7,
        "run_attempt": 1,
        "verdict": "fail",
        "result": {
            "findings": [{
                "id": "finding-one",
                "severity": "major",
                "trigger_kind": "inferred",
                "file": "src/guard.py",
                "line": 12,
                "category": "correctness",
            }],
        },
    }


def _counterevidence(head_sha: str, *, expectation: str) -> dict:
    pathspec = "*.py" if expectation == "absent" else RELPATH
    payload = {
        "command": f"git grep -n -F -e {LITERAL} {head_sha} -- {pathspec}",
        "output": "refuted-by-rerun",
        "result": "refuted",
        "pointer": pathspec,
    }
    if expectation == "absent":
        payload["match_expectation"] = "absent"
    return payload


def _env(host: dict, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    workflow_env = _workflow()["env"]
    env = os.environ.copy()
    env.update(GIT_ENV)
    env.update({
        "PATH": f"{host['bin']}{os.pathsep}{env['PATH']}",
        "GATE_ARGV_LOG": str(host["argv_log"]),
        "GATE_PREPARE_LOG": str(host["prepare_log"]),
        "GATE_REAL_GIT": host["real_git"],
        "GATE_HUB_GIT_MIRROR_DIR": str(host["mirror_root"]),
        "GATE_FAKE_UPSTREAM": str(host["upstream"].parent.parent),
        "GATE_SOURCE_DECIDE_SCRIPT": workflow_env["GATE_SOURCE_DECIDE_SCRIPT"],
        "GATE_SOURCE_BUDGET_SECS": "20",
        "RUNNER_TEMP": str(host["runner_temp"]),
        "GITHUB_WORKSPACE": str(host["workspace"]),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_ENV": str(host["github_env"]),
        "GITHUB_OUTPUT": str(host["github_output"]),
        "GH_TOKEN": "test-token",
        "CURRENT_HEAD_SHA": host["head_sha"],
        "CURRENT_SCOPE_JSON": json.dumps(_scope(host["head_sha"], host["base_sha"])),
        "DISPOSITION_COUNTEREVIDENCE_JSON": json.dumps(_counterevidence(host["head_sha"], expectation="present")),
        "AUDIT_PATH": str(host["runner_temp"] / "audit.json"),
        "CURRENT_AUDIT_DIGEST": "x",
        "CURRENT_EPOCH": "x",
        "DISPOSITION_APPROVER": "owner",
        "DISPOSITION_APPROVER_ID": "10",
        "GITHUB_TRIGGERING_ACTOR": "owner",
        "CALLER_HEAD_DIR": "",
        "OUTPUT_DIR": str(host["output_dir"]),
        "REPOSITORY_ID": "123",
        "PR_NUMBER": "42",
        "FINDING_ID": "finding-one",
        "DISPOSITION_REASON": "canonical evidence reviewed",
        "DISPOSITION_KIND": "false-positive",
        "DISPOSITION_TRACKING_ISSUE": "",
        "DISPOSITION_REPOSITORY": REPOSITORY,
    })
    (host["runner_temp"] / "audit.json").write_text(
        json.dumps(_audit(host["head_sha"], host["base_sha"])), encoding="utf-8",
    )
    host["github_env"].write_text("", encoding="utf-8")
    host["github_output"].write_text("", encoding="utf-8")
    if extra:
        env.update(extra)
    return env


def _run(script: str, env: dict[str, str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=60,
    )


def _run_fetch_issue(host: dict, env: dict[str, str]) -> tuple[subprocess.CompletedProcess, subprocess.CompletedProcess | None]:
    fetch = _run(_step_run("Fetch caller head for counterevidence rerun"), env, cwd=host["workspace"])
    if fetch.returncode != 0:
        return fetch, None
    _apply_github_env(host["github_env"], env)
    issue = _run(_step_run("Issue immutable disposition artifact"), env, cwd=host["workspace"])
    return fetch, issue


def test_service_present_issues_receipt_without_origin(tmp_path):
    host = _host(tmp_path, absent=False)
    env = _env(host)
    fetch, issue = _run_fetch_issue(host, env)
    assert fetch.returncode == 0, fetch.stderr
    assert issue is not None and issue.returncode == 0, (issue.stdout, issue.stderr)
    events = _argv_events(host["argv_log"])
    joined = _joined_argv(events)
    assert "fetch" not in joined and "github.com" not in joined
    prepare = host["prepare_log"].read_text(encoding="utf-8").splitlines()
    assert len(prepare) == 1
    assert f"--commit {host['head_sha']}" in prepare[0]
    assert f"--repository {REPOSITORY}" in prepare[0]
    receipts = list(host["output_dir"].iterdir())
    assert len(receipts) == 1
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    rerun = payload["counterevidence"]["gate_rerun"]
    assert rerun["argv"] == [
        "git", "grep", "-n", "-F", "-e", LITERAL, host["head_sha"], "--", RELPATH,
    ]
    assert rerun["exit_code"] == 0 and rerun["match_count"] >= 1
    grep_events = [event for event in events if event["argv"][:1] == ["grep"]]
    assert grep_events, joined
    assert all(event["GIT_NO_REPLACE_OBJECTS"] == "1" for event in grep_events)
    assert all(event["safe.directory"] == env["CALLER_HEAD_DIR"] for event in grep_events)


def test_service_absent_prepares_base_and_head(tmp_path):
    host = _host(tmp_path, absent=True)
    env = _env(host, extra={
        "DISPOSITION_COUNTEREVIDENCE_JSON": json.dumps(
            _counterevidence(host["head_sha"], expectation="absent"),
        ),
    })
    fetch, issue = _run_fetch_issue(host, env)
    assert fetch.returncode == 0, fetch.stderr
    assert issue is not None and issue.returncode == 0, (issue.stdout, issue.stderr)
    prepare = host["prepare_log"].read_text(encoding="utf-8").splitlines()
    assert len(prepare) == 2
    assert prepare[0].count("--commit") == 1 and host["head_sha"] in prepare[0]
    assert host["base_sha"] in prepare[1]
    joined = _joined_argv(_argv_events(host["argv_log"]))
    assert "fetch" not in joined and "github.com" not in joined
    payload = json.loads(next(host["output_dir"].iterdir()).read_text(encoding="utf-8"))
    rerun = payload["counterevidence"]["gate_rerun"]
    assert rerun["argv"][6] == host["head_sha"]
    assert (rerun["exit_code"], rerun["match_count"]) == (1, 0)
    assert rerun["base_rerun"]["match_count"] >= 1
    assert rerun["base_rerun"]["argv"][6] == host["base_sha"]


def test_service_prepare_unavailable_does_not_touch_origin(tmp_path):
    host = _host(tmp_path)
    env = _env(host, extra={"GATE_FAKE_CLIENT": "fail"})
    fetch, issue = _run_fetch_issue(host, env)
    assert fetch.returncode != 0
    assert issue is None
    combined = fetch.stderr + fetch.stdout
    assert "COUNTEREVIDENCE-SOURCE-SERVICE-UNAVAILABLE" in combined
    assert not list(host["output_dir"].iterdir())
    joined = _joined_argv(_argv_events(host["argv_log"]))
    assert "fetch" not in joined and "github.com" not in joined


def test_service_demand_pin_mismatch_does_not_rerun(tmp_path):
    host = _host(tmp_path, absent=True)
    env = _env(host, extra={"GATE_FAKE_DEMAND_REF_TARGET": "refs/heads/base"})
    fetch, issue = _run_fetch_issue(host, env)
    assert fetch.returncode == 0, fetch.stderr
    assert issue is not None and issue.returncode != 0
    assert "COUNTEREVIDENCE-SOURCE-PIN-MISMATCH" in issue.stderr
    assert not list(host["output_dir"].iterdir())
    joined = _joined_argv(_argv_events(host["argv_log"]))
    assert " grep " not in f" {joined} "
    assert not any(event["argv"] and event["argv"][0] == "grep" for event in _argv_events(host["argv_log"]))


@pytest.mark.parametrize(
    "mode_text,needle",
    [
        (None, "SOURCE-MODE-UNREADABLE"),
        ("bogus\n", "SOURCE-MODE-INVALID"),
    ],
)
def test_source_mode_missing_or_invalid_fails(tmp_path, mode_text, needle):
    host = _host(tmp_path, mode="service")
    mode_file = host["mirror_root"] / "SOURCE-MODE"
    if mode_text is None:
        mode_file.unlink()
    else:
        mode_file.write_text(mode_text)
    env = _env(host)
    fetch, issue = _run_fetch_issue(host, env)
    assert fetch.returncode != 0
    assert issue is None
    assert "SOURCE-MODE" in fetch.stderr
    assert needle in fetch.stderr


def test_origin_fetch_timeout_is_counterevidence_source_timeout(tmp_path):
    host = _host(tmp_path)
    env = _env(host, extra={"GATE_GIT_FETCH_EXIT": "124"})
    env.pop("GATE_HUB_GIT_MIRROR_DIR")
    fetch, issue = _run_fetch_issue(host, env)
    assert fetch.returncode != 0
    assert issue is None
    assert "COUNTEREVIDENCE-SOURCE-TIMEOUT" in fetch.stderr
    assert "COUNTEREVIDENCE-SOURCE-FETCH-FAILED" not in fetch.stderr
    assert "caller head/base not fetchable" not in fetch.stderr


def test_origin_fetch_other_failure_is_not_timeout(tmp_path):
    host = _host(tmp_path)
    env = _env(host, extra={"GATE_GIT_FETCH_EXIT": "1"})
    env.pop("GATE_HUB_GIT_MIRROR_DIR")
    fetch, issue = _run_fetch_issue(host, env)
    assert fetch.returncode != 0
    assert "COUNTEREVIDENCE-SOURCE-FETCH-FAILED" in fetch.stderr
    assert "COUNTEREVIDENCE-SOURCE-TIMEOUT" not in fetch.stderr
