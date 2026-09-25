from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts import v2_release_state


V2_SHA = "a" * 40
MAIN_SHA = "b" * 40
UNRELEASED_SHA = "c" * 40
NOW = 1_800_000_000


def _runner(
    v2_output: str,
    main_output: str,
    timestamp: int = NOW - 3600,
    calls=None,
    range_output: str = "",
    rev_list_status: int = 0,
    timestamps=None,
    tree_status: int = 0,
    content_diff: bool = True,
):
    def run(command, **_kwargs):
        if calls is not None:
            calls.append(command)
        if command[1:3] == ["ls-remote", "https://remote.test/repo"]:
            output = main_output if "refs/heads/main" in command else v2_output
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        if command[1] == "rev-list":
            return SimpleNamespace(returncode=rev_list_status, stdout=range_output, stderr="")
        if command[1] == "ls-tree":
            sha = command[3]
            tree_output = "different-tree\n" if content_diff else "same-tree\n"
            if content_diff:
                tree_output += sha + "\n"
            return SimpleNamespace(returncode=tree_status, stdout=tree_output, stderr="")
        if command[1:3] == ["log", "-1"]:
            commit_timestamp = (timestamps or {}).get(command[-1], timestamp)
            return SimpleNamespace(returncode=0, stdout=f"{commit_timestamp}\n", stderr="")
        raise AssertionError(command)

    return run


def _invoke(monkeypatch, v2_output: str, main_output: str, *extra: str, range_output="", timestamps=None, rev_list_status=0):
    fake = _runner(v2_output, main_output, range_output=range_output, timestamps=timestamps, rev_list_status=rev_list_status)
    monkeypatch.setattr(v2_release_state.subprocess, "run", fake)
    monkeypatch.setattr(v2_release_state.time, "time", lambda: NOW)
    return v2_release_state.main(["--remote", "https://remote.test/repo", "--threshold-hours", str(v2_release_state.DEFAULT_THRESHOLD_HOURS), *extra])


def test_v2_equals_main_is_silent(monkeypatch, capsys):
    assert _invoke(monkeypatch, f"{V2_SHA}\t{v2_release_state.TAG_REF}\n", f"{V2_SHA}\t{v2_release_state.MAIN_REF}\n") == 0
    assert capsys.readouterr() == ("", "")


def test_behind_but_within_threshold_is_silent(monkeypatch, capsys):
    assert _invoke(monkeypatch, f"{V2_SHA}\t{v2_release_state.TAG_REF}\n", f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n", range_output=f"{MAIN_SHA}\n") == 0
    assert capsys.readouterr() == ("", "")


def test_behind_over_threshold_alerts(monkeypatch, capsys):
    v2_output = f"{V2_SHA}\t{v2_release_state.TAG_REF}\n"
    main_output = f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n"
    assert _invoke(monkeypatch, v2_output, main_output, "--threshold-hours", "0", range_output=f"{MAIN_SHA}\n") == 1
    output = capsys.readouterr().out
    assert V2_SHA in output and MAIN_SHA in output and "main_lead_hours=" in output


def test_old_v2_age_does_not_alert_for_new_main_lead(monkeypatch, capsys):
    assert _invoke(monkeypatch, f"{V2_SHA}\t{v2_release_state.TAG_REF}\n", f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n", range_output=f"{UNRELEASED_SHA}\n", timestamps={V2_SHA: NOW - 100 * 3600, UNRELEASED_SHA: NOW - 3600}) == 0
    assert capsys.readouterr() == ("", "")


def test_rev_list_failure_is_inconclusive(monkeypatch, capsys):
    assert _invoke(monkeypatch, f"{V2_SHA}\t{v2_release_state.TAG_REF}\n", f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n", rev_list_status=1) == 2
    assert v2_release_state.QUERY_FAILED in capsys.readouterr().err


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
    monkeypatch.setattr(v2_release_state.subprocess, "run", _runner(
        remote_output,
        f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n",
        calls=calls,
        range_output=f"{MAIN_SHA}\n",
    ))
    monkeypatch.setattr(v2_release_state.time, "time", lambda: NOW)
    assert v2_release_state.main(["--remote", "https://remote.test/repo", "--threshold-hours", "0"]) == 1
    assert ["git", "ls-remote", "https://remote.test/repo", v2_release_state.TAG_REF, f"{v2_release_state.TAG_REF}^{{}}"] in calls
    assert ["git", "ls-remote", "https://remote.test/repo", v2_release_state.MAIN_REF] in calls
    log = next(command for command in calls if command[1:3] == ["log", "-1"])
    assert log[-1] == MAIN_SHA


def _git(cwd, *arguments, env=None):
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _release_fixture(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    _git(tmp_path, "clone", str(remote), str(checkout))
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "release-state-test",
        "GIT_AUTHOR_EMAIL": "release-state-test@example.com",
        "GIT_COMMITTER_NAME": "release-state-test",
        "GIT_COMMITTER_EMAIL": "release-state-test@example.com",
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00",
    }
    for directory in (".github/workflows", ".github/actions", "scripts", "docs"):
        (checkout / directory).mkdir(parents=True, exist_ok=True)
    (checkout / ".github/workflows/gate-v2.yml").write_text("name: original\n")
    (checkout / ".github/actions/action.yml").write_text("name: original\n")
    (checkout / "scripts/release.py").write_text("original = True\n")
    (checkout / "docs/notes.md").write_text("original\n")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "initial release", env=env)
    _git(checkout, "push", "origin", "main")
    _git(checkout, "tag", "v2")
    _git(checkout, "push", "origin", "refs/tags/v2")
    return remote, checkout, env


def _run_release_probe(checkout, remote):
    return subprocess.run(
        [
            os.environ.get("PYTHON", "python3"),
            str(Path(v2_release_state.__file__).resolve()),
            "--remote",
            str(remote),
            "--threshold-hours",
            "6",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )


def test_real_git_fixture_reports_overdue_consumer_path_content_lag(tmp_path):
    remote, checkout, env = _release_fixture(tmp_path)
    (checkout / ".github/workflows/gate-v2.yml").write_text("name: changed\n")
    _git(checkout, "add", ".github/workflows/gate-v2.yml")
    _git(checkout, "commit", "-m", "change caller workflow", env=env)
    _git(checkout, "push", "origin", "main")

    result = _run_release_probe(checkout, remote)

    assert result.returncode == 1
    assert "V2-RELEASE-STATE-CONTENT-LAG" in result.stdout


def test_real_git_fixture_ignores_overdue_docs_only_history(tmp_path):
    remote, checkout, env = _release_fixture(tmp_path)
    for index in range(2):
        (checkout / "docs/notes.md").write_text(f"docs update {index}\n")
        _git(checkout, "add", "docs/notes.md")
        _git(checkout, "commit", "-m", f"docs update {index}", env=env)
    _git(checkout, "push", "origin", "main")

    result = _run_release_probe(checkout, remote)

    assert result.returncode == 0
    assert "V2-RELEASE-STATE-CONTENT-CURRENT" in result.stdout


def test_real_git_fixture_keeps_remote_query_failure_distinct(tmp_path):
    _, checkout, _ = _release_fixture(tmp_path)

    result = _run_release_probe(checkout, tmp_path / "missing-remote.git")

    assert result.returncode == 2
    assert v2_release_state.QUERY_FAILED in result.stderr
    assert "V2-RELEASE-STATE-CONTENT-LAG" not in result.stdout + result.stderr


def test_tree_query_failure_is_inconclusive(monkeypatch, capsys):
    monkeypatch.setattr(
        v2_release_state.subprocess,
        "run",
        _runner(
            f"{V2_SHA}\t{v2_release_state.TAG_REF}\n",
            f"{MAIN_SHA}\t{v2_release_state.MAIN_REF}\n",
            range_output=f"{UNRELEASED_SHA}\n",
            tree_status=128,
        ),
    )

    assert v2_release_state.main(["--remote", "https://remote.test/repo"]) == 2
    output = capsys.readouterr()
    assert v2_release_state.QUERY_FAILED in output.err
    assert v2_release_state.CONTENT_LAG not in output.out + output.err
