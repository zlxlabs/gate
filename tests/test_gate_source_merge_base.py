from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import gate_source


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    git("config", "user.name", "Fixture", cwd=work)
    git("config", "user.email", "fixture@example.invalid", cwd=work)
    (work / "f").write_text("root\n")
    git("add", "f", cwd=work)
    git("commit", "-qm", "root", cwd=work)
    root = git("rev-parse", "HEAD", cwd=work)
    (work / "f").write_text("base\n")
    git("commit", "-qam", "base", cwd=work)
    base = git("rev-parse", "HEAD", cwd=work)
    git("checkout", "-qb", "head", root, cwd=work)
    (work / "f").write_text("head\n")
    git("commit", "-qam", "head", cwd=work)
    head = git("rev-parse", "HEAD", cwd=work)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "SOURCE-MODE").write_text("service\n")
    (mirror / "consume.lock").touch()
    bare = mirror / "zlxlabs/repo.git"
    bare.parent.mkdir()
    git("init", "--bare", "-q", str(bare))
    (bare / "consume.lock").touch()
    client = mirror / gate_source.CLIENT_NAME
    client.write_text("""#!/usr/bin/env python3
import json, subprocess, sys
opts = dict(zip(sys.argv[1::2], sys.argv[2::2]))
commit = opts['--commit']
mirror = __import__('os').environ['GATE_HUB_GIT_MIRROR_DIR'] + '/zlxlabs/repo.git'
upstream = __import__('os').environ['GATE_TEST_UPSTREAM']
subprocess.run(['git', '--git-dir', mirror, 'fetch', '-q', upstream, commit + ':refs/demand/' + commit], check=True)
print('GIT-SOURCE-PREPARE-V1 ' + json.dumps({'source':'cold'}), file=sys.stderr)
""")
    client.chmod(0o755)
    monkeypatch.setenv("GATE_HUB_GIT_MIRROR_DIR", str(mirror))
    monkeypatch.setenv("GATE_TEST_UPSTREAM", str(work))
    return base, head, root


def test_preflight_service_entrypoint_uses_real_mirror_without_http(source_repo, monkeypatch, tmp_path):
    base, head, root = source_repo
    work = Path(os.environ["GATE_TEST_UPSTREAM"])
    action = Path(__file__).resolve().parents[1] / ".github/actions/pr-size-preflight/preflight.py"
    spec = importlib.util.spec_from_file_location("preflight_mirror_e2e", action)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    monkeypatch.setenv("GITHUB_REPOSITORY", "zlxlabs/repo")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("HTTP attempted"))
    monkeypatch.chdir(work)
    output = tmp_path / "preflight.json"
    monkeypatch.setattr(sys, "argv", ["preflight.py", "--base-sha", base, "--head-sha", head,
        "--max-diff-lines", "20", "--warn-lines", "40", "--max-review-shards", "3", "--output", str(output)])

    assert root != base
    assert module.main() == 0
    result = json.loads(output.read_text())
    assert result["merge_base_sha"] == root
    assert result["reviewable_lines"] == len(subprocess.check_output(
        ["git", "diff", "--no-ext-diff", "--binary", root, head], cwd=work
    ).splitlines())
    assert result["preflight_result"] == "success"


def test_merge_base_uses_prepared_bare_mirror(source_repo):
    base, head, _ = source_repo
    assert gate_source.merge_base("zlxlabs/repo", base, head) == git("merge-base", base, head, cwd=Path(os.environ["GATE_TEST_UPSTREAM"]))


def test_merge_base_unrelated_history_fails_closed(source_repo):
    base, _, _ = source_repo
    work = Path(os.environ["GATE_TEST_UPSTREAM"])
    git("checkout", "--orphan", "unrelated", cwd=work)
    (work / "other").write_text("new history\n")
    git("add", "other", cwd=work)
    git("commit", "-qm", "unrelated", cwd=work)
    unrelated = git("rev-parse", "HEAD", cwd=work)
    with pytest.raises(gate_source.SourceError, match=gate_source.MERGE_BASE_FAILED):
        gate_source.merge_base("zlxlabs/repo", base, unrelated)


def test_merge_base_rejects_invalid_sha_before_client(source_repo):
    base, _, _ = source_repo
    with pytest.raises(gate_source.SourceError) as error:
        gate_source.merge_base("zlxlabs/repo", base, "bad")
    assert error.value.code == gate_source.COMMIT_INVALID


def test_merge_base_propagates_client_failure(source_repo, monkeypatch):
    base, head, _ = source_repo
    monkeypatch.setenv("GATE_TEST_UPSTREAM", "/no/such/upstream")
    with pytest.raises(gate_source.SourceError, match=gate_source.CLIENT_FAILED):
        gate_source.merge_base("zlxlabs/repo", base, head)


def test_merge_base_checks_demand_ref_pin(source_repo, monkeypatch):
    base, head, _ = source_repo
    mirror = Path(os.environ["GATE_HUB_GIT_MIRROR_DIR"]) / "zlxlabs/repo.git"
    subprocess.run(["git", "--git-dir", str(mirror), "fetch", "-q", os.environ["GATE_TEST_UPSTREAM"], head], check=True)
    git("--git-dir", str(mirror), "update-ref", f"refs/demand/{base}", head)
    monkeypatch.setattr(gate_source, "prepare", lambda repository, sha, deadline: (mirror, {}))
    with pytest.raises(gate_source.SourceError) as error:
        gate_source.merge_base("zlxlabs/repo", base, head)
    assert error.value.code == gate_source.PIN_MISMATCH
