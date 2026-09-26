"""Contract and cross-process fixture for gate-v2's mirrored checkout prefetch."""

import json
import os
import random
import socket
import subprocess
import time
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2.yml"
CHECKOUT_ACTION = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
BYTE_STEP = "Record network bytes"
MIRROR_STEP = "Prime checkout from host Git mirror"
TEMP_REF = "refs/internal/gate-checkout-target"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _git(*args, cwd=None, env=None):
    return subprocess.run(
        ["git", *map(str, args)],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_action_checkout(workspace, origin_url, sha):
    workspace.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="fetch.unpackLimit", GIT_CONFIG_VALUE_0="0")
    if (workspace / ".git").is_dir():
        _git("clean", "-ffdx", cwd=workspace, env=env)
        _git("reset", "--hard", "HEAD", cwd=workspace, env=env)
    else:
        _git("init", "--quiet", cwd=workspace, env=env)
        _git("remote", "add", "origin", origin_url, cwd=workspace, env=env)
    _git("config", "--local", "gc.auto", "0", cwd=workspace, env=env)
    refspec = f"+{sha}:refs/remotes/pull/1/merge"
    _git("-c", "protocol.version=2", "fetch", "--no-tags", "--keep", "--prune", "--no-recurse-submodules", "--depth=1", "origin", refspec, cwd=workspace, env=env)
    _git("checkout", "--force", "refs/remotes/pull/1/merge", cwd=workspace, env=env)


def _pack_bytes(workspace):
    return sum(path.stat().st_size for path in (workspace / ".git/objects/pack").glob("*.pack"))


def _tree_state(workspace):
    files = sorted(path.relative_to(workspace) for path in workspace.rglob("*") if path.is_file() and ".git" not in path.parts)
    return {
        "head": _git("rev-parse", "HEAD", cwd=workspace),
        "status": _git("status", "--porcelain=v1", cwd=workspace),
        "origin": _git("remote", "get-url", "origin", cwd=workspace),
        "shallow": (workspace / ".git/shallow").read_text().splitlines(),
        "files": {str(path): (workspace / path).read_bytes() for path in files},
    }


def _fixture(tmp_path, *, advance_mirror=False):
    server_root = tmp_path / "origin"
    origin = server_root / "zlxlabs/repo"
    origin.parent.mkdir(parents=True)
    _git("init", "--bare", "--initial-branch=main", origin)
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    _git("config", "user.name", "Gate Fixture", cwd=source)
    _git("config", "user.email", "gate-fixture@example.invalid", cwd=source)
    (source / "README.md").write_text("checkout fixture\n")
    payload = random.Random(41)
    (source / "large.bin").write_bytes(bytes(payload.getrandbits(8) for _ in range(128 * 1024)))
    _git("add", "README.md", "large.bin", cwd=source)
    _git("commit", "-m", "base", cwd=source)
    _git("remote", "add", "origin", origin, cwd=source)
    _git("push", "origin", "main", cwd=source)
    base_sha = _git("rev-parse", "HEAD", cwd=source)

    mirror_root = tmp_path / "cache/git"
    mirror_repo = mirror_root / "zlxlabs/repo.git"
    mirror_repo.parent.mkdir(parents=True)
    _git("clone", "--mirror", origin, mirror_repo)
    (mirror_repo / "consume.lock").write_text("")
    if advance_mirror:
        (source / "next.txt").write_text("small second commit\n")
        _git("add", "next.txt", cwd=source)
        _git("commit", "-m", "advance origin", cwd=source)
        _git("push", "origin", "main", cwd=source)
    sha = _git("rev-parse", "HEAD", cwd=source)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(
        [
            "git", "daemon", "--reuseaddr", "--export-all",
            f"--base-path={server_root}", "--listen=127.0.0.1", f"--port={port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = False
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                ready = True
                break
        except OSError:
            time.sleep(0.02)
    if not ready:
        server.terminate()
        server.wait(timeout=5)
        raise AssertionError("git daemon fixture did not start")
    return {
        "origin": origin,
        "origin_url": f"git://127.0.0.1:{port}/zlxlabs/repo",
        "server_url": f"git://127.0.0.1:{port}",
        "server": server,
        "mirror_root": mirror_root,
        "mirror_repo": mirror_repo,
        "sha": sha,
        "base_sha": base_sha,
    }


def _run_mirror_script(script, fixture, workspace, mirror_root=None, server_url=None):
    env = os.environ.copy()
    env.update(
        GITHUB_WORKSPACE=str(workspace),
        GITHUB_REPOSITORY="zlxlabs/repo",
        GITHUB_SERVER_URL=fixture["server_url"] if server_url is None else server_url,
        GITHUB_SHA=fixture["sha"],
        GITHUB_REF="refs/pull/1/merge",
        GATE_GITHUB_TOKEN="fixture-token",
        GATE_HUB_GIT_MIRROR_DIR=str(mirror_root if mirror_root is not None else fixture["mirror_root"]),
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="fetch.unpackLimit",
        GIT_CONFIG_VALUE_0="0",
    )
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )


def _stop_git_daemon(server):
    server.terminate()
    server.wait(timeout=5)


def _result_line(run):
    line = next(line for line in run.stdout.splitlines() if line.startswith("GATE-CHECKOUT-MIRROR-V1 "))
    return json.loads(line.removeprefix("GATE-CHECKOUT-MIRROR-V1 "))


def _mirror_signature(mirror_repo):
    return {
        str(path.relative_to(mirror_repo)): path.read_bytes()
        for path in mirror_repo.rglob("*")
        if path.is_file()
    }


def test_three_gate_checkouts_keep_action_and_byte_measurement_around_mirror_consumer():
    workflow = _workflow()
    script = workflow["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    assert "flock --shared" in script
    assert "refs/heads refs/demand" in script
    assert 'exec {mirror_lock_fd}<"$lock_file"' in script
    assert script.count('rm -rf -- "$workspace/.git"') == 1
    assert "fail_prefetch origin-fetch-failed" in script
    assert 'consumer-error:$consumer_step' in script
    assert 'exit "$fetch_status"' not in script
    assert script.index("printf '%s\\n' \"$mirror_objects\" > \"$alternates\"") < script.index("fetch --no-tags --keep --depth=1 origin \"$sha\"")
    assert script.index("fetch --no-tags --keep --depth=1 origin \"$sha\"") < script.index("repack --no-local -a -d")
    assert script.index("repack --no-local -a -d") < script.rindex('rm -f -- "$alternates"') < script.rindex('flock --unlock "$mirror_lock_fd"')
    assert "file://" not in script

    jobs = workflow["jobs"]
    checkout_count = 0
    for job_name in ("quality", "primary", "ocr"):
        steps = jobs[job_name]["steps"]
        checkout_indexes = [i for i, step in enumerate(steps) if step.get("uses", "").startswith(CHECKOUT_ACTION)]
        assert len(checkout_indexes) == 1
        checkout_count += 1
        index = checkout_indexes[0]
        before, prime, checkout, after = steps[index - 2 : index + 2]
        assert before.get("name", "").startswith(BYTE_STEP)
        assert prime["name"] == MIRROR_STEP
        assert prime["run"] == 'bash -euo pipefail -c "$GATE_CHECKOUT_MIRROR_SCRIPT"'
        assert prime["env"]["GATE_GITHUB_TOKEN"] == "${{ github.token }}"
        assert prime.get("if") == checkout.get("if")
        assert checkout["uses"] == CHECKOUT_ACTION
        assert checkout["with"] == {"fetch-depth": 1}
        assert after.get("name", "").startswith(BYTE_STEP)
        assert "GATE_CHECKOUT_RX_BYTES" in before["run"]
        assert "GATE-CHECKOUT-BYTES-V1" in after["run"]
        assert "refs/internal/gate-checkout-target" in after["run"]
    assert checkout_count == 3


def test_mirror_hit_localizes_checkout_and_sends_no_origin_pack(tmp_path, request):
    fixture = _fixture(tmp_path)
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    workspace = tmp_path / "primed"
    mirror_before = _mirror_signature(fixture["mirror_repo"])
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    prime = _run_mirror_script(script, fixture, workspace)
    assert prime.returncode == 0, f"mirror consumer failed: {prime.stderr}\n{prime.stdout}"
    result = _result_line(prime)
    assert result["hit"] == 1
    assert result["reason"] == "ok"
    assert result["origin_fetch_pack_bytes"] <= 64
    assert result["object_type"] == "commit"
    assert not (workspace / ".git/objects/info/alternates").exists()
    assert not _git("for-each-ref", "--format=%(refname)", "refs/remotes/mirror", cwd=workspace)
    assert _git("cat-file", "-t", fixture["sha"], cwd=workspace) == "commit"
    assert _git("rev-parse", TEMP_REF, cwd=workspace) == fixture["sha"]
    assert _mirror_signature(fixture["mirror_repo"]) == mirror_before

    packs_before_action_fetch = _pack_bytes(workspace)
    _run_action_checkout(workspace, fixture["origin_url"], fixture["sha"])
    action_fetch_delta = _pack_bytes(workspace) - packs_before_action_fetch
    _git("update-ref", "-d", TEMP_REF, cwd=workspace)
    baseline = tmp_path / "baseline"
    _run_action_checkout(baseline, fixture["origin_url"], fixture["sha"])
    assert action_fetch_delta <= 64
    assert _pack_bytes(baseline) > result["origin_fetch_pack_bytes"]
    assert _tree_state(workspace) == _tree_state(baseline)


def test_mirror_misses_report_reason_and_origin_checkout_succeeds(tmp_path, request):
    fixture = _fixture(tmp_path, advance_mirror=True)
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]

    absent_workspace = tmp_path / "absent-mirror"
    absent = _run_mirror_script(script, fixture, absent_workspace, tmp_path / "missing-cache")
    assert absent.returncode == 0, absent.stderr
    assert _result_line(absent) == {"hit": 0, "reason": "mirror-dir-missing"}
    assert not (absent_workspace / ".git").exists()
    _run_action_checkout(absent_workspace, fixture["origin_url"], fixture["sha"])
    assert _git("rev-parse", "HEAD", cwd=absent_workspace) == fixture["sha"]
    assert not _git("status", "--porcelain=v1", cwd=absent_workspace)

    missing_sha_workspace = tmp_path / "missing-sha"
    missing = _run_mirror_script(script, fixture, missing_sha_workspace)
    assert missing.returncode == 0, f"origin fetch failed: {missing.stderr}\n{missing.stdout}"
    result = _result_line(missing)
    assert result["hit"] == 0
    assert result["reason"] == "sha-missing"
    assert 0 < result["origin_fetch_pack_bytes"] < 32 * 1024
    assert not (missing_sha_workspace / ".git/objects/info/alternates").exists()
    assert _git("cat-file", "-t", fixture["sha"], cwd=missing_sha_workspace) == "commit"
    before_action_fetch = _pack_bytes(missing_sha_workspace)
    _run_action_checkout(missing_sha_workspace, fixture["origin_url"], fixture["sha"])
    assert _pack_bytes(missing_sha_workspace) - before_action_fetch <= 64
    _git("update-ref", "-d", TEMP_REF, cwd=missing_sha_workspace)
    baseline = tmp_path / "missing-sha-baseline"
    _run_action_checkout(baseline, fixture["origin_url"], fixture["sha"])
    assert _tree_state(missing_sha_workspace) == _tree_state(baseline)
    assert _pack_bytes(baseline) > result["origin_fetch_pack_bytes"]


def _assert_failed_prefetch_then_plain_fetch(run, workspace, fixture, reason):
    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    assert _result_line(run) == {"hit": 0, "reason": reason}
    assert not (workspace / ".git").exists(), "partial .git left behind"
    _git("init", "--quiet", cwd=workspace)
    _git("remote", "add", "origin", fixture["origin_url"], cwd=workspace)
    _git("fetch", "--no-tags", "--depth=1", "origin", fixture["sha"], cwd=workspace)
    assert _git("cat-file", "-t", fixture["sha"], cwd=workspace) == "commit"


def test_prefetch_failures_clear_partial_git_and_exit_zero(tmp_path, request):
    fixture = _fixture(tmp_path)
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]
    dead_workspace = tmp_path / "dead-origin"
    dead = _run_mirror_script(script, fixture, dead_workspace, server_url=f"git://127.0.0.1:{dead_port}")
    _assert_failed_prefetch_then_plain_fetch(dead, dead_workspace, fixture, "origin-fetch-failed")
    (fixture["mirror_repo"] / "refs/heads/dangling").write_text("b" * 40 + "\n")
    broken_workspace = tmp_path / "dangling-ref"
    broken = _run_mirror_script(script, fixture, broken_workspace)
    _assert_failed_prefetch_then_plain_fetch(broken, broken_workspace, fixture, "consumer-error:update-ref")
