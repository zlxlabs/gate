from __future__ import annotations

from types import SimpleNamespace

from scripts import v2_release_state


V2_SHA = "a" * 40
MAIN_SHA = "b" * 40
NOW = 1_800_000_000


def _runner(v2_output: str, main_output: str, timestamp: int = NOW - 3600):
    def run(command, **_kwargs):
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
        ["--remote", "https://remote.test/repo", "--threshold-hours", "6", *extra]
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
