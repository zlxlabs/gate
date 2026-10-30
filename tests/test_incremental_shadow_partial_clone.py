"""Real-git contracts for the incremental-shadow blob:none fetch path."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IO_PATH = ROOT / ".github" / "actions" / "gate-aggregator" / "incremental_shadow_io.py"


def _io_module():
    name = "gate_incremental_shadow_io_partial_clone"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, IO_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


def _fixture_repo(root: Path) -> tuple[Path, Path, str, str]:
    """A filtered-fetch-capable bare remote with ordinary, rename, large and deleted files."""
    bare = root / "origin.git"
    work = root / "work"
    bare.mkdir(parents=True)
    _git("init", "--quiet", "--bare", str(bare))
    _git("config", "uploadpack.allowFilter", "true", cwd=bare)
    _git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    work.mkdir()
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    _git("init", "--quiet", "-b", "main", cwd=work, env=env)
    _git("remote", "add", "origin", bare.as_uri(), cwd=work)
    (work / "ordinary.txt").write_text("keep\nold line\nend\n", encoding="utf-8")
    (work / "rename-old.txt").write_text("".join(f"stable line {i}\n" for i in range(40)), encoding="utf-8")
    large_lines = (f"record {i:06d}: unchanged payload\n" for i in range(65536))
    (work / "large.dat").write_text("".join(large_lines), encoding="utf-8")
    (work / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    _git("add", ".", cwd=work, env=env)
    _git("commit", "--quiet", "-m", "previous", cwd=work, env=env)
    prev = _git("rev-parse", "HEAD", cwd=work)

    (work / "ordinary.txt").write_text("keep\nnew line\nadded line\nend\n", encoding="utf-8")
    (work / "rename-old.txt").rename(work / "rename-new.txt")
    (work / "rename-new.txt").write_text(
        (work / "rename-new.txt").read_text(encoding="utf-8").replace("stable line 20\n", "changed line 20\n"),
        encoding="utf-8",
    )
    large = work / "large.dat"
    lines = large.read_text(encoding="utf-8").splitlines()
    lines[32768] = "record 032768: changed payload"
    lines.extend(["appended large-file line", "another appended line"])
    large.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (work / "deleted.txt").unlink()
    _git("add", "-A", cwd=work, env=env)
    _git("commit", "--quiet", "-m", "head", cwd=work, env=env)
    head = _git("rev-parse", "HEAD", cwd=work)
    _git("push", "--quiet", "origin", "main", cwd=work, env=env)
    return bare, work, prev, head


def _bare_url(path: Path) -> str:
    return path.as_uri()


def test_blob_none_fetch_matches_full_clone_diff_and_fetches_once(tmp_path, monkeypatch):
    io = _io_module()
    bare, _work, prev, head = _fixture_repo(tmp_path)
    full_clone = tmp_path / "full-clone"
    _git("clone", "--quiet", _bare_url(bare), str(full_clone))
    expected_text = _git("diff", "-U0", "-M", f"{prev}..{head}", cwd=full_clone)
    expected = io.parse_diff_added_lines(expected_text)

    calls: list[list[str]] = []
    object_bytes: list[int] = []
    real_run_git = io._run_git

    def recording_run_git(argv, **kwargs):
        calls.append(list(argv))
        result = real_run_git(argv, **kwargs)
        if len(argv) > 1 and argv[1] == "init":
            # Make explicit -M part of the contract, not an ambient default.
            _git("-C", str(kwargs["cwd"]), "config", "diff.renames", "false")
        if len(argv) > 3 and argv[3] in {"fetch", "diff"}:
            repo_dir = Path(argv[2])
            object_files = (repo_dir / ".git" / "objects").rglob("*")
            object_bytes.append(sum(path.stat().st_size for path in object_files if path.is_file()))
        return result

    monkeypatch.setattr(io, "_run_git", recording_run_git)
    actual = io._inter_round_added_lines(prev, head, token="test-token", remote_url=_bare_url(bare))

    fetches = [argv for argv in calls if len(argv) > 3 and argv[3] == "fetch"]
    assert len(fetches) == 1
    assert fetches[0][4:] == ["--depth", "1", "--filter=blob:none", "origin", prev, head]
    assert actual == expected
    assert len(object_bytes) == 2 and object_bytes[1] > object_bytes[0]
    assert {"ordinary.txt", "rename-new.txt", "large.dat"}.issubset(actual)
    assert "deleted.txt" not in actual
    assert actual["ordinary.txt"] == {2, 3}
    assert actual["rename-new.txt"] == {21}
    assert actual["large.dat"] == {32769, 65537, 65538}


def test_blob_none_fetch_reports_force_pushed_away_prev_as_diff_failed(tmp_path):
    io = _io_module()
    bare, work, prev, head = _fixture_repo(tmp_path)
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    _git("checkout", "--quiet", "--orphan", "replacement", cwd=work, env=env)
    for child in work.iterdir():
        if child.name != ".git":
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    (work / "replacement.txt").write_text("replacement\n", encoding="utf-8")
    _git("add", ".", cwd=work, env=env)
    _git("commit", "--quiet", "-m", "force-pushed replacement", cwd=work, env=env)
    _git("push", "--quiet", "--force", "origin", "HEAD:main", cwd=work, env=env)
    _git("reflog", "expire", "--expire=now", "--all", cwd=bare)
    _git("gc", "--prune=now", cwd=bare)

    with pytest.raises(io._DiffUnavailable) as error:
        io._inter_round_added_lines(prev, head, token="test-token", remote_url=_bare_url(bare))
    assert error.value.reason == "diff_failed"


def test_blob_none_diff_lazy_fetch_failure_is_diff_failed(tmp_path, monkeypatch):
    io = _io_module()
    bare, _work, prev, head = _fixture_repo(tmp_path)
    real_run_git = io._run_git
    fetch_succeeded = False

    def disable_promisor_remote_after_fetch(argv, **kwargs):
        nonlocal fetch_succeeded
        result = real_run_git(argv, **kwargs)
        if len(argv) > 3 and argv[3] == "fetch" and result.returncode == 0:
            fetch_succeeded = True
            repo_dir = Path(argv[2])
            _git("remote", "set-url", "origin", (tmp_path / "unavailable.git").as_uri(), cwd=repo_dir)
        return result

    monkeypatch.setattr(io, "_run_git", disable_promisor_remote_after_fetch)
    with pytest.raises(io._DiffUnavailable) as error:
        io._inter_round_added_lines(prev, head, token="test-token", remote_url=_bare_url(bare))
    assert fetch_succeeded
    assert error.value.reason == "diff_failed"
