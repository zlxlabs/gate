import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.v2_tag_guard import HOLD_MARKER, main, verify_remote_tag
from tests import v2_lag_observe
from tests.v2_lag_observe import (
    evaluate_v2_lag,
    parse_ls_remote_sha,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "v2-tag-sync.yml"


def _load():
    raw = yaml.safe_load(WORKFLOW.read_text())
    return raw, raw.get("on", raw.get(True))


def test_sync_workflow_is_main_push_and_has_contents_write():
    raw, trigger = _load()
    assert trigger["push"] == {"branches": ["main"]}
    assert "workflow_dispatch" in trigger
    assert not trigger["workflow_dispatch"]
    assert trigger["schedule"] == [{"cron": "17 * * * *"}]
    assert raw["permissions"] == {"contents": "write"}
    assert raw["jobs"]["sync"]["if"] == "github.ref == 'refs/heads/main'"
    assert raw["env"] == {
        "V2_LAG_HOURS": "6",
        "V2_LAG_MAIN_COMMITS": "",
    }


def test_sync_workflow_has_migration_switch_and_contract_before_push():
    raw, _ = _load()
    text = WORKFLOW.read_text()
    assert "vars.V2_TAG_SYNC_ENABLED" in text
    assert "scripts/v2_tag_guard.py" in text
    steps = raw["jobs"]["sync"]["steps"]
    contract_step = next(step for step in steps if step.get("name") == "Verify the v2 caller contract before moving the tag")
    move_step = next(step for step in steps if step.get("name") == "Move v2 to selected canary-verified main commit")
    assert contract_step["run"].strip().endswith(
        "tests/test_gate_caller_contract_guard.py"
    )
    assert steps.index(contract_step) < steps.index(move_step)
    assert "refs/tags/v2" in move_step["run"]
    assert '"${intended_sha}:refs/tags/v2" --force' in move_step["run"]
    assert '"HEAD:refs/tags/v2"' not in move_step["run"]
    assert "git ls-remote origin refs/tags/v2" in move_step["run"]
    assert "remote_result" in move_step["run"]
    assert "intended_sha" in move_step["run"]
    assert '"${push_status}" "${remote_result}"' in move_step["run"]
    assert "push_status=$?" in move_step["run"]
    assert "query_status=$?" in move_step["run"]
    assert re.search(r"(?m)^\s*exit 1\s*$", move_step["run"])
    guard = next(step for step in raw["jobs"]["sync"]["steps"] if step.get("id") == "guard")
    guard_run = guard["run"]
    assert "--hold-file" in guard_run
    assert ".github/v2-tag-sync.hold" in guard_run


def test_evidence_selection_is_before_contract_and_move():
    raw, _ = _load()
    steps = raw["jobs"]["sync"]["steps"]
    evidence = next(step for step in steps if step.get("id") == "evidence")
    monotonicity = next(step for step in steps if step.get("id") == "monotonicity")
    contract = next(step for step in steps if step.get("name") == "Verify the v2 caller contract before moving the tag")
    move = next(step for step in steps if step.get("name") == "Move v2 to selected canary-verified main commit")
    assert evidence["if"] == "steps.guard.outputs.advance == 'true'"
    assert "scripts/v2_tag_promotion_evidence.py" in evidence["run"]
    assert 'main_tip="$(git rev-parse refs/remotes/origin/main)"' in evidence["run"]
    assert '--main-tip "${main_tip}"' in evidence["run"]
    assert monotonicity["if"] == "steps.evidence.outputs.target_sha != ''"
    assert contract["if"] == "steps.monotonicity.outputs.move == 'true'"
    assert move["if"] == "steps.monotonicity.outputs.move == 'true'"
    assert "TARGET_SHA" in move["env"]
    lag = next(step for step in steps if step.get("id") == "lag")
    assert steps.index(move) < steps.index(lag)
    assert "move == 'true'" not in lag.get("if", "")
    assert "always()" in lag["if"]
    assert "git ls-remote origin refs/tags/v2" in lag["run"]
    assert "git ls-remote origin refs/heads/main" in lag["run"]
    assert "python3 tests/v2_lag_observe.py" in lag["run"]
    assert "git rev-parse" not in lag["run"]
    assert "v2-stuck-verified" not in lag["run"]
    assert "state-path" not in lag["run"]


def test_disabled_switch_skips_entire_evidence_step():
    raw, _ = _load()
    steps = raw["jobs"]["sync"]["steps"]
    guard = next(step for step in steps if step.get("id") == "guard")
    evidence = next(step for step in steps if step.get("id") == "evidence")
    assert "V2_TAG_SYNC_ENABLED" in guard["env"]
    assert evidence["if"] == "steps.guard.outputs.advance == 'true'"


def test_breaker_marker_blocks_advancement(capsys):
    assert main(
        ["--enabled", "true", "--commit-message", f"fix caller {HOLD_MARKER}"]
    ) == 1
    assert f"held by commit message marker: {HOLD_MARKER}" in capsys.readouterr().out


def test_squash_safe_breaker_file_blocks_advancement(tmp_path, capsys):
    hold_file = tmp_path / ".github" / "v2-tag-sync.hold"
    hold_file.parent.mkdir()
    hold_file.write_text("hold this migration")
    assert main(
        [
            "--enabled",
            "true",
            "--commit-message",
            "squashed pull request message",
            "--hold-file",
            str(hold_file),
        ]
    ) == 1
    assert f"held by breaker file: {hold_file}" in capsys.readouterr().out


def test_breaker_file_query_failure_is_hold(tmp_path, capsys):
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("not a directory")
    hold_file = not_a_directory / "v2-tag-sync.hold"
    assert main(
        [
            "--enabled",
            "true",
            "--commit-message",
            "clean squashed message",
            "--hold-file",
            str(hold_file),
        ]
    ) == 1
    output = capsys.readouterr().out
    assert f"held: breaker file query failed: {hold_file}" in output
    assert "NotADirectoryError" in output


def test_push_failure_is_success_when_remote_matches_intent(capsys):
    intended = "a" * 40
    assert verify_remote_tag(intended, 1, f"{intended}\trefs/tags/v2\n")
    assert "push exit code 1" in capsys.readouterr().out


def test_push_success_is_failure_when_remote_differs(capsys):
    intended = "a" * 40
    actual = "b" * 40
    assert not verify_remote_tag(intended, 0, f"{actual}\trefs/tags/v2\n")
    output = capsys.readouterr().out
    assert f"expected {intended}, found {actual}" in output
    assert "push exit code 0" in output


@pytest.mark.parametrize(
    "remote_result, expected",
    [
        (f"{'b' * 40}\trefs/tags/v2\n", "expected"),
        ("", "refs/tags/v2 was not returned"),
    ],
)
def test_push_failure_is_failure_when_remote_does_not_match(remote_result, expected, capsys):
    assert not verify_remote_tag("a" * 40, 1, remote_result)
    assert expected in capsys.readouterr().out


def _real_annotated_tag_producer(tmp_path: Path) -> tuple[str, str, str]:
    repo_dir = tmp_path / "annotated_repo"
    repo_dir.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial commit"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )
    commit_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True, env=env
    ).strip()
    subprocess.run(
        ["git", "tag", "-a", "v2", "-m", "annotated v2 tag"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )
    tag_sha = subprocess.check_output(
        ["git", "rev-parse", "v2"], cwd=repo_dir, text=True, env=env
    ).strip()
    ls_remote = subprocess.check_output(
        ["git", "ls-remote", ".", "refs/tags/v2*"],
        cwd=repo_dir,
        text=True,
        env=env,
    )
    return commit_sha, tag_sha, ls_remote


def test_annotated_tag_output_still_reads_direct_ref(tmp_path, capsys):
    commit_sha, tag_sha, remote_result = _real_annotated_tag_producer(tmp_path)
    assert commit_sha != tag_sha
    assert f"{tag_sha}\trefs/tags/v2\n" in remote_result
    assert f"{commit_sha}\trefs/tags/v2^{{}}\n" in remote_result
    assert not verify_remote_tag(commit_sha, 0, remote_result)
    output = capsys.readouterr().out
    assert f"expected {commit_sha}, found {tag_sha}" in output
    assert "remote v2 verification failed" in output


def test_missing_remote_tag_is_failure(capsys):
    assert not verify_remote_tag("a" * 40, 0, "")
    assert "refs/tags/v2 was not returned" in capsys.readouterr().out


def test_enabled_clean_commit_advances(capsys, tmp_path):
    missing = tmp_path / "absent-v2-tag-sync.hold"
    assert main(
        [
            "--enabled",
            "true",
            "--commit-message",
            "fix implementation",
            "--hold-file",
            str(missing),
        ]
    ) == 0
    assert "v2 tag sync enabled and no breaker signal found" in capsys.readouterr().out


def test_disabled_migration_switch_blocks_advancement(capsys):
    assert main(["--enabled", "", "--commit-message", "fix implementation"]) == 1
    assert "v2 tag sync disabled; set V2_TAG_SYNC_ENABLED=true after migration" in capsys.readouterr().out


def test_agents_md_requires_remote_v2_before_closing_issues():
    text = (REPO_ROOT / "AGENTS.md").read_text()
    assert "## 发布完成与关单" in text
    assert "git ls-remote --tags origin v2" in text
    assert "git merge-base --is-ancestor <fix-sha> <v2-commit>" in text
    assert "status:waiting" in text
    assert "禁止用本地 tag 副本判断发布" in text
    merge_section = text.split("## 发布完成与关单", 1)[0]
    assert "调用方钉 `@v2`" in merge_section
    assert "本仓 workflow 历史仍不可 rebase" in merge_section
    assert "@<40hex>" not in merge_section


def _quad_kwargs(**overrides):
    v2 = "a" * 40
    payload = {
        "v2_sha": v2,
        "main_sha": "b" * 40,
        "behind_main": 3,
        "age_h": 1,
        "target_sha": v2,
        "lag_hours": 6,
        "lag_main_commits": None,
    }
    payload.update(overrides)
    return payload


def test_lag_summary_quadruplet_present_for_current_and_newer_targets():
    for target, v2 in (("a" * 40, "a" * 40), ("b" * 40, "a" * 40)):
        summary, reasons = evaluate_v2_lag(**_quad_kwargs(target_sha=target, v2_sha=v2))
        assert reasons == []
        for field in ("v2_sha", "main_sha", "behind_main", "age_h"):
            assert f"- {field}:" in summary


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"age_h": 0, "lag_hours": 0, "target_sha": "b" * 40, "v2_sha": "a" * 40}, "age_h"),
        ({"lag_main_commits": 0, "target_sha": "b" * 40, "v2_sha": "a" * 40}, "behind_main"),
    ],
)
def test_threshold_env_zero_reports_default_does_not(overrides, reason):
    summary, reasons = evaluate_v2_lag(**_quad_kwargs(**overrides))
    assert reason in reasons
    assert "- v2_sha:" in summary
    _, quiet = evaluate_v2_lag(**_quad_kwargs())
    assert quiet == []


def test_age_h_over_threshold_without_candidate_does_not_report():
    for target in ("", "a" * 40):
        summary, reasons = evaluate_v2_lag(
            **_quad_kwargs(age_h=9, lag_main_commits=0, target_sha=target)
        )
        assert reasons == []
        assert "- age_h: 9" in summary


def test_age_h_over_threshold_with_newer_candidate_reports():
    summary, reasons = evaluate_v2_lag(
        **_quad_kwargs(age_h=9, target_sha="b" * 40, v2_sha="a" * 40)
    )
    assert "age_h" in reasons
    assert "- age_h: 9" in summary


def _git_with_dates(repo: Path, *date_and_message: tuple[str, str]) -> list[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=env)
    commits = []
    for date, message in date_and_message:
        commit_env = {**env, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", message],
            cwd=repo,
            check=True,
            capture_output=True,
            env=commit_env,
        )
        commits.append(
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, env=commit_env).strip()
        )
    return commits


def test_lag_age_uses_oldest_unpromoted_commit_not_old_v2_commit(tmp_path):
    repo = tmp_path / "age-repo"
    repo.mkdir()
    v2_sha, first_unpromoted, main_sha = _git_with_dates(
        repo,
        ("2026-09-10T00:00:00Z", "v2 base"),
        ("2026-09-20T00:00:00Z", "first unpromoted"),
        ("2026-09-21T00:00:00Z", "main tip"),
    )
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tests" / "v2_lag_observe.py"),
            "--v2-ls-remote",
            f"{v2_sha}\trefs/tags/v2\n",
            "--main-ls-remote",
            f"{main_sha}\trefs/heads/main\n",
            "--summary-path",
            str(summary),
            "--target-sha",
            main_sha,
            "--lag-hours",
            "100",
            "--now",
            "1789992000",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = summary.read_text(encoding="utf-8")
    assert "- age_h: 36" in report
    assert "- age_h_basis: oldest commit in v2..main (hours)" in report
    assert first_unpromoted != v2_sha


@pytest.mark.parametrize("failed_command", ["log", "rev-list"])
def test_lag_git_history_failure_is_fail_loud(monkeypatch, tmp_path, failed_command):
    calls = []

    def broken_check_output(command, text):
        calls.append(command)
        if command[1] == failed_command:
            raise subprocess.CalledProcessError(128, command)
        if command[1] == "log":
            return "1758326400\n"
        return "1\n"

    monkeypatch.setattr(v2_lag_observe.subprocess, "check_output", broken_check_output)
    summary = tmp_path / "summary.md"
    with pytest.raises(subprocess.CalledProcessError):
        v2_lag_observe.main(
            [
                "--v2-ls-remote",
                f"{'a' * 40}\trefs/tags/v2\n",
                "--main-ls-remote",
                f"{'b' * 40}\trefs/heads/main\n",
                "--summary-path",
                str(summary),
                "--target-sha",
                "b" * 40,
            ]
        )
    assert any(command[1] == failed_command for command in calls)
