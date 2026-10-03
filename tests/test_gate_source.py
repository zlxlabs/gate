"""Service-path contracts for the host-declared git source (gate-hub W3b-1).

The fixtures here are real git repositories, a real read-only-style mirror, and
a fake `git-source-prepare` client that implements the documented contract:

    argv:   --repository <owner/repo> --commit <40hex> --deadline-epoch <int>
    stderr: exactly one `GIT-SOURCE-PREPARE-V1 {...}` line
    exit:   0 = ready (source hit|cold), 2-9 = SOURCE-* failure

Every git invocation on PATH is recorded, so a service-path test can prove the
job never reached GitHub (no `origin` argv, no https/git:// URL, no curl).
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from scripts import gate_source

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2.yml"
SHADOW_WORKFLOW = REPO_ROOT / ".github/workflows/gate-shadow-v2.yml"
DISPOSITION_WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2-disposition.yml"
BOUNDED_RETRY = REPO_ROOT / "scripts/gate_bounded_retry.py"
PREFLIGHT = REPO_ROOT / ".github/actions/pr-size-preflight/preflight.py"
ADVISORY = REPO_ROOT / ".github/actions/diff-coverage-advisory/advisory.py"
REPOSITORY = "zlxlabs/repo"
GATE_REPOSITORY = "zlxlabs/gate"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Gate Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Gate Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

CLIENT = '''#!/usr/bin/env python3
"""Fake host source client (contract: gate-hub W1/W2 runner/git_source_prepare.py)."""
import json
import os
import subprocess
import sys
import time

options = dict(zip(sys.argv[1::2], sys.argv[2::2]))
repository = options["--repository"]
commit = options["--commit"]
mirror = os.path.join(os.environ["GATE_HUB_GIT_MIRROR_DIR"], repository + ".git")
upstream = os.path.join(os.environ["GATE_FAKE_UPSTREAM"], repository + ".git")


def emit(status, code, source="cold"):
    payload = {
        "status": status, "code": code, "repository": repository,
        "commit_sha": commit, "source": source, "elapsed_ms": 0,
    }
    sys.stderr.write("GIT-SOURCE-PREPARE-V1 " + json.dumps(payload, sort_keys=True) + chr(10))


mode = os.environ.get("GATE_FAKE_CLIENT", "ok")
if mode == "hang":
    time.sleep(3600)
if mode == "garbage":
    sys.stderr.write("git-source-prepare: unparseable answer" + chr(10))
    sys.exit(0)
if mode == "unready":
    emit("garbage", "SOURCE-UNPARSEABLE")
    sys.exit(0)
if mode == "fail":
    emit("failed", "SOURCE-COLD-FAILED")
    sys.exit(3)
if not os.path.isdir(os.path.join(mirror, "objects")):
    subprocess.run(["git", "init", "--bare", "--quiet", mirror], check=True)
    open(os.path.join(mirror, "consume.lock"), "a").close()
refs = subprocess.run(
    ["git", "--git-dir", upstream, "for-each-ref", "--points-at", commit,
     "--format=%(refname)", "refs/heads"],
    check=True, capture_output=True, text=True,
).stdout.split()
if not refs:
    emit("failed", "SOURCE-UNKNOWN-COMMIT")
    sys.exit(4)
demand = "refs/demand/" + commit
warm = subprocess.run(
    ["git", "--git-dir", mirror, "rev-parse", "--verify", "--quiet", demand],
    capture_output=True,
).returncode == 0
subprocess.run(
    ["git", "-c", "advice.detachedHead=false", "--git-dir", mirror, "fetch", "--no-tags",
     upstream, refs[0] + ":" + demand],
    check=True, capture_output=True,
)
emit("ready", "OK", source="hit" if warm else "cold")
'''

GIT_WRAPPER = '''#!/usr/bin/env python3
"""Record every git argv the job runs, then exec the real git."""
import os
import sys

with open(os.environ["GATE_ARGV_LOG"], "a", encoding="utf-8") as log:
    log.write("git " + " ".join(sys.argv[1:]) + chr(10))
os.execv("{real_git}", ["git", *sys.argv[1:]])
'''

CURL_WRAPPER = '''#!/bin/sh
echo "curl $*" >> "$GATE_ARGV_LOG"
exit 1
'''


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV},
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def source_host(tmp_path):
    """Caller repo + gate repo upstream, a mirror root, a fake client, an argv log."""
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "README.md").write_text("caller base\n")
    (work / "app.py").write_text("value = 1\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "base", cwd=work)
    base_sha = _git("rev-parse", "HEAD", cwd=work)
    _git("checkout", "-q", "-b", "feature", cwd=work)
    (work / "feature.txt").write_text("feature payload\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "feature", cwd=work)
    head_sha = _git("rev-parse", "HEAD", cwd=work)
    # A GitHub-style synthetic `refs/pull/N/merge` commit: two parents, head first.
    tree = _git("rev-parse", f"{head_sha}^{{tree}}", cwd=work)
    merge_sha = _git("commit-tree", tree, "-p", head_sha, "-p", base_sha, "-m", "merge", cwd=work)
    _git("checkout", "-q", "--orphan", "sibling", cwd=work)
    _git("rm", "-rq", "--cached", ".", cwd=work)
    for path in work.iterdir():
        if path.name != ".git":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    (work / "sibling.txt").write_text("unrelated base\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "sibling", cwd=work)
    sibling_sha = _git("rev-parse", "HEAD", cwd=work)

    upstream = tmp_path / "upstream"
    caller_bare = upstream / f"{REPOSITORY}.git"
    caller_bare.parent.mkdir(parents=True)
    _git("init", "--bare", "--quiet", "-b", "main", caller_bare)
    _git("remote", "add", "fixture", str(caller_bare), cwd=work)
    _git(
        "push", "--quiet", "fixture",
        f"{head_sha}:refs/heads/feature",
        f"{merge_sha}:refs/heads/pr-1-merge",
        f"{sibling_sha}:refs/heads/sibling",
        cwd=work,
    )

    gate_work = tmp_path / "gate-work"
    gate_work.mkdir()
    _git("init", "-q", "-b", "main", cwd=gate_work)
    (gate_work / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts/gate_source.py", gate_work / "scripts/gate_source.py")
    shutil.copy(REPO_ROOT / "scripts/gate_bounded_retry.py", gate_work / "scripts/gate_bounded_retry.py")
    (gate_work / "scripts/unrelated.py").write_text("# not fetched\n")
    _git("add", "-A", cwd=gate_work)
    _git("commit", "-qm", "gate tools", cwd=gate_work)
    gate_sha = _git("rev-parse", "HEAD", cwd=gate_work)
    gate_bare = upstream / f"{GATE_REPOSITORY}.git"
    _git("init", "--bare", "--quiet", "-b", "main", gate_bare)
    _git("push", "--quiet", gate_bare, f"{gate_sha}:refs/heads/main", cwd=gate_work)

    mirror_root = tmp_path / "cache/git"
    mirror_root.mkdir(parents=True)
    (mirror_root / "SOURCE-MODE").write_text("service\n")
    client = mirror_root / "git-source-prepare"
    client.write_text(CLIENT)
    client.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    (bin_dir / "git").write_text(GIT_WRAPPER.format(real_git=real_git))
    (bin_dir / "curl").write_text(CURL_WRAPPER)
    for name in ("git", "curl"):
        (bin_dir / name).chmod(0o755)

    return {
        "tmp_path": tmp_path,
        "work": work,
        "upstream": upstream,
        "caller_bare": caller_bare,
        "gate_bare": gate_bare,
        "mirror_root": mirror_root,
        "bin": bin_dir,
        "argv_log": tmp_path / "argv.log",
        "runner_temp": tmp_path / "runner-temp",
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_sha": merge_sha,
        "sibling_sha": sibling_sha,
        "gate_sha": gate_sha,
    }


def _env(host, workspace: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(GIT_ENV)
    env.update({
        "PATH": f"{host['bin']}{os.pathsep}{env['PATH']}",
        "GATE_ARGV_LOG": str(host["argv_log"]),
        "GATE_HUB_GIT_MIRROR_DIR": str(host["mirror_root"]),
        "GATE_FAKE_UPSTREAM": str(host["upstream"]),
        "RUNNER_TEMP": str(host["runner_temp"]),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_API_URL": "https://api.github.com",
        "GITHUB_SHA": host["merge_sha"],
        "GITHUB_REF": "refs/pull/1/merge",
        "GITHUB_OUTPUT": str(host["tmp_path"] / "github-output"),
        "GATE_GITHUB_TOKEN": "fixture-token",
        "GATE_SOURCE_BUDGET_SECS": "20",
        "GATE_NET_RETRY_ATTEMPTS": "3",
        "GATE_NET_RETRY_BACKOFF_SECS": "5 15",
        "GATE_NET_ATTEMPT_TIMEOUT_SECS": "25",
    })
    env.update(overrides)
    host["runner_temp"].mkdir(parents=True, exist_ok=True)
    return env


def _run_bash(script: str, env: dict[str, str], *extra_env_script: str) -> subprocess.CompletedProcess:
    prelude = "\n".join(extra_env_script)
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", f"{prelude}\n{script}"],
        env=env, capture_output=True, text=True, timeout=180,
    )


def _script(name: str, workflow: str = "gate-v2") -> str:
    raw = yaml.safe_load(
        (WORKFLOW if workflow == "gate-v2" else SHADOW_WORKFLOW).read_text(encoding="utf-8")
    )
    return raw["env"][name]


def _script_env(env: dict[str, str]) -> None:
    for name in ("GATE_SOURCE_DECIDE_SCRIPT", "GATE_SOURCE_BOOTSTRAP_SCRIPT", "GATE_CHECKOUT_MIRROR_SCRIPT"):
        env[name] = _script(name)


def _marker(stdout: str) -> dict:
    line = next(line for line in stdout.splitlines() if line.startswith("GATE-SOURCE-V1 "))
    return json.loads(line.removeprefix("GATE-SOURCE-V1 "))


def _mirror_line(stdout: str) -> dict:
    line = next(line for line in stdout.splitlines() if line.startswith("GATE-CHECKOUT-MIRROR-V1 "))
    return json.loads(line.removeprefix("GATE-CHECKOUT-MIRROR-V1 "))


def _argv(host) -> list[str]:
    if not host["argv_log"].exists():
        return []
    return host["argv_log"].read_text(encoding="utf-8").splitlines()


def _network_argv(host) -> list[str]:
    """Anything that could have talked to GitHub: a fetch/pull over the network,
    or a Contents API curl.  `git remote add origin <url>` is a local config write
    and is deliberately not counted."""
    return [
        line for line in _argv(host)
        if line.startswith(("curl", "git pull"))
        or ("fetch" in line and ("origin" in line or "http" in line or "git://" in line))
    ]


def _source_mode(host, value: str | None) -> None:
    path = host["mirror_root"] / "SOURCE-MODE"
    if value is None:
        path.unlink()
    else:
        path.write_text(value)


def test_caller_checkout_service_materializes_merge_commit_without_github(source_host):
    workspace = source_host["tmp_path"] / "workspace"
    workspace.mkdir()
    env = _env(source_host, workspace)
    env["GATE_SOURCE_TOOL_REPOSITORY"] = GATE_REPOSITORY
    env["GATE_SOURCE_TOOL_REF"] = source_host["gate_sha"]
    _script_env(env)

    run = _run_bash('bash -euo pipefail -c "$GATE_CHECKOUT_MIRROR_SCRIPT"', env)

    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    assert _mirror_line(run.stdout)["mode"] == "service"
    assert _marker(run.stdout)["step"] == "checkout"
    assert _marker(run.stdout)["source"] in {"hit", "cold"}
    assert (source_host["tmp_path"] / "github-output").read_text().strip() == "mode=service"

    assert _git("rev-parse", "HEAD", cwd=workspace) == source_host["merge_sha"]
    assert _git("rev-parse", "--is-shallow-repository", cwd=workspace) == "false"
    assert _git("remote", "get-url", "origin", cwd=workspace) == f"https://github.com/{REPOSITORY}"
    # gate-hub's review entry treats a non-shallow checkout as self-sufficient.
    assert _git("rev-parse", f"{source_host['base_sha']}^{{commit}}", cwd=workspace) == source_host["base_sha"]
    for name in ("README.md", "app.py", "feature.txt"):
        expected = subprocess.run(
            ["git", "--git-dir", str(source_host["caller_bare"]), "cat-file", "blob",
             f"{source_host['merge_sha']}:{name}"],
            env={**os.environ, **GIT_ENV}, check=True, capture_output=True,
        ).stdout
        assert (workspace / name).read_bytes() == expected
    assert not (workspace / ".git/objects/info/alternates").exists()
    assert _network_argv(source_host) == []


def test_workflow_sha_sparse_checkout_service_pins_and_keeps_paths(source_host):
    workspace = source_host["tmp_path"] / "workspace"
    env = _env(
        source_host, workspace,
        GATE_CHECKOUT_REPOSITORY=GATE_REPOSITORY,
        GATE_CHECKOUT_REF=source_host["gate_sha"],
        GATE_CHECKOUT_PATH="_gate-action-src",
        GATE_CHECKOUT_SPARSE="scripts/gate_source.py\nscripts/gate_bounded_retry.py\n",
    )
    _script_env(env)

    run = _run_bash(
        'bash -euo pipefail -c "$GATE_SOURCE_BOOTSTRAP_SCRIPT"\n'
        'python3 "${RUNNER_TEMP}/gate_bounded_retry.py" checkout',
        env,
    )

    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    dest = workspace / "_gate-action-src"
    assert _git("rev-parse", "HEAD", cwd=dest) == source_host["gate_sha"]
    assert not (dest / "scripts/unrelated.py").exists()
    for name in ("gate_source.py", "gate_bounded_retry.py"):
        assert (dest / "scripts" / name).read_bytes() == (REPO_ROOT / "scripts" / name).read_bytes()
        assert (source_host["runner_temp"] / name).read_bytes() == (REPO_ROOT / "scripts" / name).read_bytes()
    assert _network_argv(source_host) == []


def test_tool_bootstrap_service_reads_pinned_scripts_from_the_mirror(source_host):
    env = _env(
        source_host, source_host["tmp_path"] / "workspace",
        GATE_CHECKOUT_REPOSITORY=GATE_REPOSITORY,
        GATE_CHECKOUT_REF=source_host["gate_sha"],
    )
    _script_env(env)

    run = _run_bash('bash -euo pipefail -c "$GATE_SOURCE_BOOTSTRAP_SCRIPT"', env)

    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    pinned = subprocess.run(
        ["git", "--git-dir", str(source_host["gate_bare"]), "cat-file", "blob",
         f"{source_host['gate_sha']}:scripts/gate_source.py"],
        env={**os.environ, **GIT_ENV}, check=True, capture_output=True,
    ).stdout
    assert (source_host["runner_temp"] / "gate_source.py").read_bytes() == pinned
    assert (source_host["runner_temp"] / "gate_bounded_retry.py").is_file()
    assert not _network_argv(source_host)


def test_advisory_and_preflight_service_fetch_absent_base_from_the_mirror(source_host):
    workspace = source_host["tmp_path"] / "workspace"
    workspace.mkdir()
    _git("init", "-q", cwd=workspace)
    _git("remote", "add", "origin", str(source_host["caller_bare"]), cwd=workspace)
    _git("fetch", "--no-tags", str(source_host["caller_bare"]),
         f"{source_host['merge_sha']}:refs/demand/merge", cwd=workspace)
    _git("checkout", "-q", "--force", "FETCH_HEAD", cwd=workspace)
    assert not (workspace / ".git/shallow").exists()
    env = _env(source_host, workspace, GITHUB_REPOSITORY=REPOSITORY)
    _script_env(env)

    advisory_run = subprocess.run(
        ["python3", str(ADVISORY),
         "--base-sha", source_host["sibling_sha"],
         "--head-sha", source_host["merge_sha"],
         "--lcov-path", "coverage/lcov.info"],
        env=env, cwd=workspace, capture_output=True, text=True, timeout=180,
    )
    assert advisory_run.returncode == 0, f"{advisory_run.stderr}\n{advisory_run.stdout}"
    assert _git("cat-file", "-e", f"{source_host['sibling_sha']}^{{commit}}", cwd=workspace) == ""
    assert any(str(source_host["mirror_root"]) in line for line in _argv(source_host))
    assert _network_argv(source_host) == []


def test_preflight_action_uses_the_service_for_an_absent_base(source_host, monkeypatch):
    workspace = source_host["tmp_path"] / "workspace"
    workspace.mkdir()
    _git("init", "-q", cwd=workspace)
    _git("remote", "add", "origin", str(source_host["caller_bare"]), cwd=workspace)
    _git("fetch", "--no-tags", str(source_host["caller_bare"]),
         f"{source_host['merge_sha']}:refs/demand/merge", cwd=workspace)
    _git("checkout", "-q", "--force", "FETCH_HEAD", cwd=workspace)
    env = _env(source_host, workspace)
    monkeypatch.setenv("PATH", f"{source_host['bin']}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GATE_ARGV_LOG", str(source_host["argv_log"]))
    monkeypatch.setenv("GATE_HUB_GIT_MIRROR_DIR", str(source_host["mirror_root"]))
    monkeypatch.setenv("GATE_FAKE_UPSTREAM", str(source_host["upstream"]))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.chdir(workspace)

    spec = importlib.util.spec_from_file_location("preflight_service", PREFLIGHT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_review_commits(workspace, source_host["sibling_sha"], source_host["merge_sha"])

    assert _git("cat-file", "-e", f"{source_host['sibling_sha']}^{{commit}}", cwd=workspace) == ""
    assert any(str(source_host["mirror_root"]) in line for line in _argv(source_host))
    assert _network_argv(source_host) == []


@pytest.mark.parametrize(
    ("declaration", "expected_code"),
    (
        (None, gate_source.MODE_UNREADABLE),
        ("", gate_source.MODE_INVALID),
        ("service\norigin\n", gate_source.MODE_INVALID),
        ("mirror\n", gate_source.MODE_INVALID),
        ("service \n", gate_source.MODE_INVALID),
    ),
)
def test_broken_source_mode_declaration_is_a_hard_failure(source_host, declaration, expected_code):
    _source_mode(source_host, declaration)
    env = _env(source_host, source_host["tmp_path"] / "workspace")
    env["GATE_SOURCE_TOOL_REPOSITORY"] = GATE_REPOSITORY
    env["GATE_SOURCE_TOOL_REF"] = source_host["gate_sha"]
    _script_env(env)

    run = _run_bash('bash -euo pipefail -c "$GATE_CHECKOUT_MIRROR_SCRIPT"', env)

    assert run.returncode != 0
    assert expected_code in run.stderr
    assert _argv(source_host) == []


def test_service_mode_is_required_for_every_service_operation(source_host, monkeypatch):
    monkeypatch.setenv("GATE_HUB_GIT_MIRROR_DIR", str(source_host["mirror_root"]))
    _source_mode(source_host, "origin\n")
    with pytest.raises(gate_source.SourceError) as error:
        gate_source.checkout(REPOSITORY, source_host["merge_sha"], source_host["tmp_path"] / "nope")
    assert error.value.code == gate_source.MODE_NOT_SERVICE


def test_client_failure_is_fatal_and_never_reaches_origin(source_host):
    workspace = source_host["tmp_path"] / "workspace"
    workspace.mkdir()
    env = _env(source_host, workspace, GATE_FAKE_CLIENT="fail")
    env["GATE_SOURCE_TOOL_REPOSITORY"] = GATE_REPOSITORY
    env["GATE_SOURCE_TOOL_REF"] = source_host["gate_sha"]
    _script_env(env)

    run = _run_bash('bash -euo pipefail -c "$GATE_CHECKOUT_MIRROR_SCRIPT"', env)

    assert run.returncode != 0
    assert gate_source.CLIENT_FAILED in run.stderr
    assert "SOURCE-COLD-FAILED" in run.stderr
    assert _network_argv(source_host) == []
    assert not (workspace / ".git").exists()


def test_client_contract_violation_is_rejected(source_host):
    workspace = source_host["tmp_path"] / "workspace"
    workspace.mkdir()
    env = _env(source_host, workspace, GATE_FAKE_CLIENT="garbage",
               GATE_CHECKOUT_REPOSITORY=GATE_REPOSITORY,
               GATE_CHECKOUT_REF=source_host["gate_sha"])
    _script_env(env)

    run = _run_bash('bash -euo pipefail -c "$GATE_SOURCE_BOOTSTRAP_SCRIPT"', env)

    assert run.returncode != 0
    assert gate_source.CLIENT_CONTRACT in run.stderr
    assert not (source_host["runner_temp"] / "gate_source.py").exists()


def test_python_client_reader_rejects_an_unready_status(source_host, monkeypatch):
    monkeypatch.setenv("GATE_HUB_GIT_MIRROR_DIR", str(source_host["mirror_root"]))
    monkeypatch.setenv("GATE_FAKE_CLIENT", "unready")
    with pytest.raises(gate_source.SourceError) as error:
        gate_source.run_client(GATE_REPOSITORY, source_host["gate_sha"], int(time.time()) + 60)
    assert error.value.code == gate_source.CLIENT_CONTRACT


def test_unknown_commit_from_the_client_is_fatal(source_host):
    env = _env(source_host, source_host["tmp_path"] / "workspace",
               GATE_CHECKOUT_REPOSITORY=GATE_REPOSITORY,
               GATE_CHECKOUT_REF="a" * 40)
    _script_env(env)

    run = _run_bash('bash -euo pipefail -c "$GATE_SOURCE_BOOTSTRAP_SCRIPT"', env)

    assert run.returncode != 0
    assert gate_source.CLIENT_FAILED in run.stderr


def test_lock_timeout_is_a_bounded_failure(source_host):
    workspace = source_host["tmp_path"] / "workspace"
    workspace.mkdir()
    env = _env(
        source_host, workspace,
        GATE_SOURCE_BUDGET_SECS="2",
        GATE_CHECKOUT_REPOSITORY=GATE_REPOSITORY,
        GATE_CHECKOUT_REF=source_host["gate_sha"],
    )
    _script_env(env)
    subprocess.run(["bash", "-euo", "pipefail", "-c", _script("GATE_SOURCE_BOOTSTRAP_SCRIPT")],
                   env=env, capture_output=True, text=True, timeout=120)
    lock = source_host["mirror_root"] / f"{GATE_REPOSITORY}.git" / "consume.lock"
    handle = os.open(lock, os.O_RDONLY)
    fcntl.flock(handle, fcntl.LOCK_EX)
    try:
        run = _run_bash('bash -euo pipefail -c "$GATE_SOURCE_BOOTSTRAP_SCRIPT"', env)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)
    assert run.returncode != 0
    assert gate_source.LOCK_TIMEOUT in run.stderr


def test_origin_declaration_keeps_the_pre_existing_git_bounded_retry_path(source_host):
    _source_mode(source_host, "origin\n")
    workspace = source_host["tmp_path"] / "workspace"
    workspace.mkdir()
    env = _env(
        source_host, workspace,
        GATE_CHECKOUT_REPOSITORY=REPOSITORY,
        GATE_CHECKOUT_REF=source_host["head_sha"],
        GATE_CHECKOUT_ORIGIN_URL=str(source_host["caller_bare"]),
    )
    (source_host["runner_temp"] / "gate-source-mode").write_text("origin\n")

    run = subprocess.run(
        ["python3", str(BOUNDED_RETRY), "checkout"], env=env, cwd=workspace,
        capture_output=True, text=True, timeout=120,
    )

    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    assert _git("rev-parse", "HEAD", cwd=workspace) == source_host["head_sha"]
    assert (workspace / ".git/shallow").exists()
    assert any(
        line == f"git fetch --no-tags --no-recurse-submodules --depth 1 origin {source_host['head_sha']}"
        for line in _argv(source_host)
    ), _argv(source_host)


def test_unset_mirror_dir_declares_origin(monkeypatch):
    monkeypatch.delenv("GATE_HUB_GIT_MIRROR_DIR", raising=False)
    assert gate_source.declared_mode() == "origin"


def test_repositories_outside_the_fleet_are_rejected(source_host, monkeypatch):
    monkeypatch.setenv("GATE_HUB_GIT_MIRROR_DIR", str(source_host["mirror_root"]))
    with pytest.raises(gate_source.SourceError) as error:
        gate_source.check_repository("someone-else/repo")
    assert error.value.code == gate_source.REPOSITORY_REJECTED
    with pytest.raises(gate_source.SourceError) as commit_error:
        gate_source.check_commit("not-a-sha")
    assert commit_error.value.code == gate_source.COMMIT_INVALID


@pytest.mark.parametrize(
    "literal",
    (
        gate_source.MIRROR_DIR_MISSING, gate_source.MODE_UNREADABLE, gate_source.MODE_INVALID,
        gate_source.MODE_NOT_SERVICE, gate_source.REPOSITORY_REJECTED, gate_source.COMMIT_INVALID,
        gate_source.CLIENT_MISSING, gate_source.CLIENT_CONTRACT, gate_source.CLIENT_FAILED,
        gate_source.DEADLINE_EXCEEDED, gate_source.LOCK_UNREADABLE, gate_source.LOCK_TIMEOUT,
        gate_source.MIRROR_UNREADABLE, gate_source.DEMAND_REF_MISSING, gate_source.FETCH_FAILED,
        gate_source.CHECKOUT_FAILED, gate_source.PIN_MISMATCH, gate_source.PATH_MISSING,
    ),
)
def test_failure_literals_are_stable_and_unique(literal):
    assert literal.startswith("SOURCE-")
    assert literal not in {"SOURCE-", "SOURCE-X"}
    assert literal == literal.strip().upper()


def test_pin_mismatch_is_reported_not_swallowed(source_host, monkeypatch):
    monkeypatch.setenv("GATE_HUB_GIT_MIRROR_DIR", str(source_host["mirror_root"]))
    monkeypatch.setenv("GATE_FAKE_UPSTREAM", str(source_host["upstream"]))
    monkeypatch.setenv("GATE_SOURCE_BUDGET_SECS", "30")
    with pytest.raises(gate_source.SourceError) as error:
        gate_source.checkout(
            REPOSITORY,
            source_host["head_sha"],
            source_host["tmp_path"] / "workspace",
            ["nonexistent/path.py"],
        )
    assert error.value.code == gate_source.PATH_MISSING


def test_shadow_and_disposition_share_the_same_declaration_scripts(source_host):
    gate = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["env"]
    shadow = yaml.safe_load(SHADOW_WORKFLOW.read_text(encoding="utf-8"))["env"]
    disposition = yaml.safe_load(DISPOSITION_WORKFLOW.read_text(encoding="utf-8"))["env"]
    for name in ("GATE_SOURCE_DECIDE_SCRIPT", "GATE_SOURCE_BOOTSTRAP_SCRIPT"):
        assert shadow[name] == gate[name]
        assert disposition[name] == gate[name]
    decide = gate["GATE_SOURCE_DECIDE_SCRIPT"]
    assert decide.count(gate_source.MODE_UNREADABLE) == 1
    assert decide.count(gate_source.MODE_INVALID) == 1
    assert decide.count("/SOURCE-MODE") == 1
    # The client must run before the consume lock or the host writer deadlocks.
    assert gate["GATE_SOURCE_BOOTSTRAP_SCRIPT"].index("git-source-prepare") < \
        gate["GATE_SOURCE_BOOTSTRAP_SCRIPT"].index("consume.lock")
    assert gate["GATE_SOURCE_BUDGET_SECS"] == "180"


def test_every_workflow_bootstrap_shares_the_one_script():
    for path, expected in ((WORKFLOW, 9), (DISPOSITION_WORKFLOW, 1)):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        runs = [
            step["run"] for job in raw["jobs"].values()
            for step in job.get("steps", []) if "run" in step
        ]
        assert sum('"$GATE_SOURCE_BOOTSTRAP_SCRIPT"' in run for run in runs) == expected
        assert not any("api.github.com" in run for run in runs if "$GATE_SOURCE_BOOTSTRAP_SCRIPT" in run)