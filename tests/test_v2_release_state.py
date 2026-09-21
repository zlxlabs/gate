from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts import v2_release_state


V2_SHA = "a" * 40
MAIN_SHA = "b" * 40
NOW = 1_800_000_000


def _runner(v2_output: str, main_output: str, timestamp: int = NOW - 3600, calls=None):
    def run(command, **_kwargs):
        if calls is not None:
            calls.append(command)
        if command[1:3] == ["ls-remote", "https://remote.test/repo"]:
            output = main_output if "refs/heads/main" in command else v2_output
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        if command[1:3] == ["show", "-s"]:
            return SimpleNamespace(returncode=0, stdout=f"{timestamp}\n", stderr="")
        raise AssertionError(command)

    return run


def _invoke(monkeypatch, v2_output: str, main_output: str, *extra: str):
    monkeypatch.setattr(v2_release_state.subprocess, "run", _runner(v2_output, main_output))
    monkeypatch.setattr(v2_release_state.time, "time", lambda: NOW)
    return v2_release_state.main(
        ["--remote", "https://remote.test/repo", "--threshold-hours", str(v2_release_state.DEFAULT_THRESHOLD_HOURS), *extra]
    )


def test_v2_equals_main_is_silent(monkeypatch, capsys):
    assert _invoke(monkeypatch, f"{V2_SHA}\t{v2_release_state.TAG_REF}\n", f"{V2_SHA}\t{v2_release_state.MAIN_REF}\n") == 0
    assert capsys.readouterr() == ("", "")


def test_behind_but_within_threshold_is_silent(monkeypatch, capsys):
    assert _invoke(monkeypatch, f"{V2_SHA}\t{v2_release_state.TAG_REF}\n", f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n") == 0
    assert capsys.readouterr() == ("", "")


def test_behind_over_threshold_alerts(monkeypatch, capsys):
    v2_output = f"{V2_SHA}\t{v2_release_state.TAG_REF}\n"
    main_output = f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n"
    assert _invoke(monkeypatch, v2_output, main_output, "--threshold-hours", "0") == 1
    output = capsys.readouterr().out
    assert V2_SHA in output and MAIN_SHA in output and "lag_hours=" in output


def _assert_query_failure(monkeypatch, capsys, v2_status, main_status):
    secret_url = "https://token-should-not-leak.example/repo"

    def run(command, **_kwargs):
        if "refs/heads/main" in command:
            output = f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n" if main_status == 0 else ""
            return SimpleNamespace(returncode=main_status, stdout=output, stderr=secret_url)
        output = f"{V2_SHA}\t{v2_release_state.TAG_REF}\n" if v2_status == 0 else ""
        return SimpleNamespace(returncode=v2_status, stdout=output, stderr=secret_url)

    monkeypatch.setattr(v2_release_state.subprocess, "run", run)
    result = v2_release_state.main(["--remote", secret_url])
    captured = capsys.readouterr()
    assert result == 2
    assert v2_release_state.QUERY_FAILED in captured.err
    assert secret_url not in captured.out + captured.err


@pytest.mark.parametrize("main_status", [0, 1])
def test_v2_query_failure_is_inconclusive(monkeypatch, capsys, main_status):
    _assert_query_failure(monkeypatch, capsys, 1, main_status)


def test_main_query_failure_is_inconclusive(monkeypatch, capsys):
    _assert_query_failure(monkeypatch, capsys, 0, 1)


def test_annotated_tag_resolves_to_commit(tmp_path, monkeypatch):
    repo = tmp_path / "producer"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "init"], cwd=repo, check=True, env=env)
    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, env=env).strip()
    subprocess.run(["git", "tag", "-a", "v2", "-m", "annotated"], cwd=repo, check=True, env=env)
    remote_output = subprocess.check_output(["git", "ls-remote", ".", "refs/tags/v2*"], cwd=repo, text=True)
    assert f"{commit_sha}\trefs/tags/v2^{{}}\n" in remote_output
    calls = []
    monkeypatch.setattr(v2_release_state.subprocess, "run", _runner(remote_output, f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n", calls=calls))
    monkeypatch.setattr(v2_release_state.time, "time", lambda: NOW)
    assert v2_release_state.main(["--remote", "https://remote.test/repo", "--threshold-hours", "0"]) == 1
    assert ["git", "ls-remote", "https://remote.test/repo", v2_release_state.TAG_REF, f"{v2_release_state.TAG_REF}^{{}}"] in calls
    assert ["git", "ls-remote", "https://remote.test/repo", v2_release_state.MAIN_REF] in calls
    show = next(command for command in calls if command[1:3] == ["show", "-s"])
    assert show[-1] == commit_sha
