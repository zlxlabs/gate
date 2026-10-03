"""Contract and cross-process fixture for gate-v2's mirrored checkout prefetch."""

import json
import os
import random
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from test_gate_source import CLIENT


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github/workflows/gate-v2.yml"
SHADOW_WORKFLOW = REPO_ROOT / ".github/workflows/gate-shadow-v2.yml"
CHECKOUT_ACTION = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
BYTE_STEP = "Record network bytes"
MIRROR_STEP = "Prime checkout from host Git mirror"
TEMP_REF = "refs/internal/gate-checkout-target"
# The synthetic merge commit is built without touching the fixture's ambient
# git identity, so the object id does not depend on the runner's global config.
_IDENTITY = ("-c", "user.name=Gate Fixture", "-c", "user.email=gate-fixture@example.invalid")


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


def _fixture(
    tmp_path,
    *,
    advance_mirror=False,
    synthetic_merge=False,
    with_mirror=True,
    source_mode="origin\n",
):
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
    if synthetic_merge:
        # A GitHub-style synthetic refs/pull/N/merge commit: two parents, and the
        # object id itself is what the gate asks the host client to prepare.
        _git("checkout", "-b", "pr-1", cwd=source)
        (source / "pr.txt").write_text("pr payload\n")
        _git("add", "pr.txt", cwd=source)
        _git("commit", "-m", "pr head", cwd=source)
        head_sha = _git("rev-parse", "HEAD", cwd=source)
        merge_sha = _git(
            *_IDENTITY, "commit-tree", f"{head_sha}^{{tree}}",
            "-p", head_sha, "-p", base_sha, "-m", "Merge pull/1", cwd=source,
        )
        _git("push", "origin", f"{merge_sha}:refs/heads/pr-1-merge", cwd=source)

    mirror_root = tmp_path / "cache/git"
    mirror_repo = mirror_root / "zlxlabs/repo.git"
    mirror_repo.parent.mkdir(parents=True)
    # Every host that injects GATE_HUB_GIT_MIRROR_DIR also declares SOURCE-MODE;
    # a missing marker is an illegal declaration and fails the decide step, so
    # the fixture always ships one.  `origin` keeps the pre-W3c prefetch path.
    (mirror_root / "SOURCE-MODE").write_text(source_mode)
    if with_mirror:
        _git("clone", "--mirror", origin, mirror_repo)
        (mirror_repo / "consume.lock").write_text("")
    if advance_mirror:
        (source / "next.txt").write_text("small second commit\n")
        _git("add", "next.txt", cwd=source)
        _git("commit", "-m", "advance origin", cwd=source)
        _git("push", "origin", "main", cwd=source)
    sha = _git("rev-parse", "HEAD", cwd=source)
    if synthetic_merge:
        sha = merge_sha
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    daemon_config = ["-c", "uploadpack.allowAnySHA1InWant=true", "-c", "uploadpack.allowReachableSHA1InWant=true"] if synthetic_merge else []
    server = subprocess.Popen(
        [
            "git", *daemon_config, "daemon", "--reuseaddr", "--export-all",
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
        "server_root": server_root,
        "tmp_path": tmp_path,
        "origin_url": f"git://127.0.0.1:{port}/zlxlabs/repo",
        "server_url": f"git://127.0.0.1:{port}",
        "server": server,
        "mirror_root": mirror_root,
        "mirror_repo": mirror_repo,
        "sha": sha,
        "base_sha": base_sha,
    }


def _run_mirror_script(script, fixture, workspace, mirror_root=None, server_url=None, script_env=None, env_scripts_from=WORKFLOW):
    env = os.environ.copy()
    for name in ("GATE_CHECKOUT_REPOSITORY", "GATE_CHECKOUT_REF", "GATE_CHECKOUT_PATH"):
        env.pop(name, None)
    workflow_env = yaml.safe_load(env_scripts_from.read_text())["env"]
    env.update(
        # Workflow-level env, as the real step gets it: the prime script evals
        # both of these before it touches the mirror.
        GATE_SOURCE_DECIDE_SCRIPT=workflow_env["GATE_SOURCE_DECIDE_SCRIPT"],
        GATE_SOURCE_PREPARE_SCRIPT=workflow_env["GATE_SOURCE_PREPARE_SCRIPT"],
    )
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
    env.update(script_env or {})
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


def test_shadow_checkouts_prime_from_the_same_script_and_keep_checkout_fallback():
    gate_workflow = _workflow()
    shadow_workflow = yaml.safe_load(SHADOW_WORKFLOW.read_text())
    script = gate_workflow["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    assert shadow_workflow["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"] == script
    assert "GATE-CHECKOUT-MIRROR-V1" in script

    jobs = shadow_workflow["jobs"]
    for job_name in ("classify_pr_paths", "shadow"):
        steps = jobs[job_name]["steps"]
        checkout_indexes = [i for i, step in enumerate(steps) if step.get("uses", "").startswith(CHECKOUT_ACTION)]
        assert len(checkout_indexes) == 1
        index = checkout_indexes[0]
        before, prime, checkout, after = steps[index - 2 : index + 2]
        assert before.get("name", "").startswith(BYTE_STEP)
        assert prime["name"] == MIRROR_STEP
        assert prime["run"] == 'bash -euo pipefail -c "$GATE_CHECKOUT_MIRROR_SCRIPT"'
        assert prime["env"]["GATE_GITHUB_TOKEN"] == "${{ github.token }}"
        assert prime.get("if") == checkout.get("if")
        assert checkout["uses"] == CHECKOUT_ACTION
        assert after.get("name", "").startswith(BYTE_STEP)
        assert "GATE-CHECKOUT-BYTES-V1" in after["run"]

        if job_name == "classify_pr_paths":
            assert checkout["with"] == {
                "repository": "${{ job.workflow_repository }}",
                "ref": "${{ job.workflow_sha }}",
                "path": "_gate-classify-src",
            }
            assert prime["env"]["GATE_CHECKOUT_REPOSITORY"] == "${{ job.workflow_repository }}"
            assert prime["env"]["GATE_CHECKOUT_REF"] == "${{ job.workflow_sha }}"
            assert prime["env"]["GATE_CHECKOUT_PATH"] == "_gate-classify-src"
        else:
            assert checkout["with"] == {"fetch-depth": 1}
            assert "GATE_CHECKOUT_REPOSITORY" not in prime["env"]
            assert "GATE_CHECKOUT_REF" not in prime["env"]
            assert "GATE_CHECKOUT_PATH" not in prime["env"]


def test_gate_v2_mirror_defaults_keep_the_original_checkout_context(tmp_path, request):
    fixture = _fixture(tmp_path)
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    workspace = tmp_path / "gate-v2-workspace"
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]

    prime = _run_mirror_script(script, fixture, workspace)

    assert prime.returncode == 0, f"{prime.stderr}\n{prime.stdout}"
    assert _result_line(prime)["hit"] == 1
    assert _git("rev-parse", "HEAD", cwd=workspace) == fixture["sha"]
    assert _git("remote", "get-url", "origin", cwd=workspace) == fixture["origin_url"]
    assert not (workspace / "_gate-classify-src" / ".git").exists()
    assert not (workspace / ".git" / "objects" / "info" / "alternates").exists()


@pytest.mark.parametrize(
    ("target", "target_path"),
    (("shadow", None), ("classify_pr_paths", "_gate-classify-src")),
)
@pytest.mark.parametrize("mode", ("hit", "miss", "failure"))
def test_shadow_checkout_targets_hit_miss_and_failure_cleanup(tmp_path, request, target, target_path, mode):
    fixture = _fixture(tmp_path, advance_mirror=(mode == "miss"))
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    shadow_workflow = yaml.safe_load(SHADOW_WORKFLOW.read_text())
    script = shadow_workflow["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    steps = shadow_workflow["jobs"][target]["steps"]
    prime = next(step for step in steps if step.get("name") == MIRROR_STEP)
    checkout = next(step for step in steps if step.get("uses", "").startswith(CHECKOUT_ACTION))
    workspace_root = tmp_path / f"{target}-{mode}-workspace"
    workspace_root.mkdir()
    workspace = workspace_root / target_path if target_path else workspace_root
    script_env = {}
    if target == "classify_pr_paths":
        assert prime["env"]["GATE_CHECKOUT_REPOSITORY"] == "${{ job.workflow_repository }}"
        assert prime["env"]["GATE_CHECKOUT_REF"] == "${{ job.workflow_sha }}"
        assert prime["env"]["GATE_CHECKOUT_PATH"] == target_path
        assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
        assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
        script_env.update(
            GATE_CHECKOUT_REPOSITORY="zlxlabs/repo",
            GATE_CHECKOUT_REF=fixture["sha"],
            GATE_CHECKOUT_PATH=target_path,
        )
    else:
        assert "GATE_CHECKOUT_REPOSITORY" not in prime["env"]
        assert "GATE_CHECKOUT_REF" not in prime["env"]
        assert "GATE_CHECKOUT_PATH" not in prime["env"]

    server_url = None
    if mode == "failure":
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead_port = sock.getsockname()[1]
        server_url = f"git://127.0.0.1:{dead_port}"
    run = _run_mirror_script(
        script,
        fixture,
        workspace_root,
        server_url=server_url,
        script_env=script_env,
        env_scripts_from=SHADOW_WORKFLOW,
    )

    if mode == "failure":
        _assert_failed_prefetch_then_plain_fetch(run, workspace, fixture, "origin-fetch-failed")
        return

    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    result = _result_line(run)
    if mode == "hit":
        assert result["hit"] == 1
        assert result["reason"] == "ok"
        assert result["origin_fetch_pack_bytes"] <= 64
    else:
        assert result["hit"] == 0
        assert result["reason"] == "sha-missing"
        assert result["origin_fetch_pack_bytes"] > 0
    assert not (workspace / ".git/objects/info/alternates").exists()
    _run_action_checkout(workspace, fixture["origin_url"], fixture["sha"])
    _git("update-ref", "-d", TEMP_REF, cwd=workspace)
    assert _git("rev-parse", "HEAD", cwd=workspace) == fixture["sha"]
    assert not _git("status", "--porcelain=v1", cwd=workspace)


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
    assert _result_line(absent) == {
        "hit": 0, "reason": "mirror-dir-missing", "prepare": "skipped", "prepare_ms": 0,
    }
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
    assert _result_line(run) == {
        "hit": 0, "reason": reason, "prepare": "skipped", "prepare_ms": 0,
    }
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


# ── W3c: the prime step asks the host client to prepare the sha first ─────────
# 101 of 184 measured prefetches hit a mirror with no copy of the repository at
# all (reason=mirror-repo-missing), so nothing was prepared and the whole
# closure came back over the wire (boq-gen ~306 MB, VideoTranscriptAPI ~88 MB
# per run).  These tests drive the real workflow script against a real upstream,
# an initially repository-less mirror root and the contract client.


def _install_fake_client(fixture, repositories=("zlxlabs/repo",)):
    """`git-source-prepare` exactly as gate-hub's runner/git_source_prepare.py
    contract: prepares refs/demand/<sha> into <root>/<owner>/<repo>.git out of the
    host writer's upstream, which it resolves as <root>/<owner>/<repo>.git — the
    fixture's bare upstream is named without that suffix, so hand it the tree."""
    upstream = fixture["tmp_path"] / "client-upstream"
    for repository in repositories:
        link = upstream / f"{repository}.git"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(fixture["server_root"] / repository)
    client = fixture["mirror_root"] / "git-source-prepare"
    client.write_text(CLIENT)
    client.chmod(0o755)
    fixture["client_upstream"] = upstream


def _service_env(fixture, tmp_path, **extra):
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    env = {"RUNNER_TEMP": str(runner_temp), "GATE_FAKE_UPSTREAM": str(fixture["client_upstream"])}
    env.update(extra)
    return env


def test_service_host_prepares_the_sha_and_shrinks_the_origin_bytes(tmp_path, request):
    fixture = _fixture(tmp_path, synthetic_merge=True, with_mirror=False, source_mode="service\n")
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    _install_fake_client(fixture)
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]

    # Baseline: the behaviour without preparation — an `origin` host whose mirror
    # has no copy of this repository, so the prime step misses and the checkout
    # step that follows pays for the whole closure.
    cold_root = tmp_path / "cold-mirror"
    cold_root.mkdir()
    (cold_root / "SOURCE-MODE").write_text("origin\n")
    baseline_workspace = tmp_path / "baseline"
    baseline = _run_mirror_script(
        script, fixture, baseline_workspace, mirror_root=cold_root,
        script_env=_service_env(fixture, tmp_path),
    )
    assert baseline.returncode == 0, f"{baseline.stderr}\n{baseline.stdout}"
    assert _result_line(baseline) == {
        "hit": 0, "reason": "mirror-repo-missing", "prepare": "skipped", "prepare_ms": 0,
    }
    _run_action_checkout(baseline_workspace, fixture["origin_url"], fixture["sha"])
    baseline_bytes = _pack_bytes(baseline_workspace)
    assert baseline_bytes > 100 * 1024, f"fixture too small to measure a delta: {baseline_bytes}B"

    workspace = tmp_path / "prepared"
    prime = _run_mirror_script(script, fixture, workspace, script_env=_service_env(fixture, tmp_path))

    assert prime.returncode == 0, f"{prime.stderr}\n{prime.stdout}"
    result = _result_line(prime)
    assert result["hit"] == 1
    assert result["reason"] == "ok"
    assert result["prepare"] == "cold"
    assert result["prepare_ms"] >= 1
    assert result["origin_fetch_pack_bytes"] <= 64
    # The host client is what put this repository in the mirror, closure and all.
    demand = fixture["mirror_repo"] / "refs/demand" / fixture["sha"]
    assert demand.read_text().strip() == fixture["sha"]
    assert (fixture["mirror_repo"] / "consume.lock").is_file()
    packs_before = _pack_bytes(workspace)
    _run_action_checkout(workspace, fixture["origin_url"], fixture["sha"])
    assert _pack_bytes(workspace) - packs_before < baseline_bytes * 0.1
    _git("update-ref", "-d", TEMP_REF, cwd=workspace)
    assert _git("rev-parse", "HEAD", cwd=workspace) == fixture["sha"]
    assert not _git("status", "--porcelain=v1", cwd=workspace)
    assert not (workspace / ".git" / "objects" / "info" / "alternates").exists()


def test_service_prepare_failure_is_fatal_and_never_reaches_origin(tmp_path, request):
    fixture = _fixture(tmp_path, synthetic_merge=True, with_mirror=False, source_mode="service\n")
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    _install_fake_client(fixture)
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    workspace = tmp_path / "client-failed"

    run = _run_mirror_script(
        script, fixture, workspace, script_env=_service_env(fixture, tmp_path, GATE_FAKE_CLIENT="fail"),
    )

    assert run.returncode != 0, f"a failing client must not be swallowed: {run.stdout}"
    assert "SOURCE-CLIENT-FAILED" in run.stderr
    assert "SOURCE-COLD-FAILED" in run.stderr
    # Fatal, not a soft miss: no result line to read as "went to origin", no
    # half-built repository, and origin was never fetched at all.
    assert not [line for line in run.stdout.splitlines() if line.startswith("GATE-CHECKOUT-MIRROR-V1")]
    assert not (workspace / ".git").exists()


def test_origin_declaration_never_calls_the_client(tmp_path, request):
    fixture = _fixture(tmp_path, with_mirror=False, source_mode="origin\n")
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    _install_fake_client(fixture)
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    workspace = tmp_path / "origin-host"

    # GATE_FAKE_CLIENT=fail: any call at all would make this step red.
    run = _run_mirror_script(
        script, fixture, workspace, script_env=_service_env(fixture, tmp_path, GATE_FAKE_CLIENT="fail"),
    )

    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    assert _result_line(run) == {
        "hit": 0, "reason": "mirror-repo-missing", "prepare": "skipped", "prepare_ms": 0,
    }
    assert not fixture["mirror_repo"].exists(), "the client created a mirror copy on an origin host"
    assert not (workspace / ".git").exists()


def test_prepare_runs_before_the_consume_lock_is_taken(tmp_path, request):
    """The host writer needs the exclusive side of consume.lock to publish, so a
    consumer that calls the client while holding the shared side deadlocks; the
    fake client asserts it can take that exclusive lock, like #276's bootstrap.
    The mirror here already holds the repository and its lock, so the only thing
    that can make the client fail is the caller holding that lock."""
    fixture = _fixture(tmp_path, source_mode="service\n")
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    _install_fake_client(fixture)
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    for attempt in range(2):
        run = _run_mirror_script(
            script, fixture, tmp_path / f"lock-order-{attempt}",
            script_env=_service_env(fixture, tmp_path, GATE_FAKE_CLIENT_PROBE_LOCK="1"),
        )

        assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
        assert _result_line(run)["hit"] == 1


def test_classify_target_prepares_the_workflow_repository(tmp_path, request):
    """`classify_pr_paths` checks out the workflow's own repository at
    workflow_sha, so that is the repository the client has to be asked for."""
    fixture = _fixture(tmp_path, with_mirror=False, source_mode="service\n")
    request.addfinalizer(lambda: _stop_git_daemon(fixture["server"]))
    gate_origin = fixture["server_root"] / "zlxlabs/gate"
    _git("init", "--bare", "--quiet", "--initial-branch=main", gate_origin)
    _install_fake_client(fixture, ("zlxlabs/repo", "zlxlabs/gate"))
    work = tmp_path / "gate-source"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    (work / "README.md").write_text("workflow repo\n")
    _git("add", "README.md", cwd=work)
    _git(*_IDENTITY, "commit", "-m", "workflow", cwd=work)
    gate_sha = _git("rev-parse", "HEAD", cwd=work)
    _git("push", gate_origin, f"{gate_sha}:refs/heads/main", cwd=work)
    script = _workflow()["env"]["GATE_CHECKOUT_MIRROR_SCRIPT"]
    workspace_root = tmp_path / "classify-workspace"
    workspace_root.mkdir()

    run = _run_mirror_script(
        script, fixture, workspace_root,
        script_env=_service_env(
            fixture, tmp_path,
            GATE_CHECKOUT_REPOSITORY="zlxlabs/gate",
            GATE_CHECKOUT_REF=gate_sha,
            GATE_CHECKOUT_PATH="_gate-classify-src",
        ),
    )

    assert run.returncode == 0, f"{run.stderr}\n{run.stdout}"
    result = _result_line(run)
    assert (result["hit"], result["prepare"]) == (1, "cold")
    assert (fixture["mirror_root"] / "zlxlabs/gate.git" / "refs/demand" / gate_sha).read_text().strip() == gate_sha
    assert not (fixture["mirror_root"] / "zlxlabs/repo.git").exists()
    assert _git("rev-parse", "HEAD", cwd=workspace_root / "_gate-classify-src") == gate_sha
