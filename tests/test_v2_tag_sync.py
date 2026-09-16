import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.v2_tag_guard import HOLD_MARKER, main, verify_remote_tag


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "v2-tag-sync.yml"


def _load():
    raw = yaml.safe_load(WORKFLOW.read_text())
    return raw, raw.get("on", raw.get(True))


def test_sync_workflow_is_main_push_and_has_contents_write():
    raw, trigger = _load()
    assert trigger["push"] == {"branches": ["main"]}
    assert "workflow_dispatch" in trigger
    assert trigger["workflow_dispatch"]["inputs"]["canary_run_id"]["required"] is True
    assert trigger["schedule"] == [{"cron": "17 * * * *"}]
    assert raw["permissions"] == {"contents": "write"}
    assert raw["jobs"]["sync"]["if"] == "github.ref == 'refs/heads/main'"


def test_permission_probe_is_dispatch_only_and_suppresses_responses():
    raw, _ = _load()
    steps = raw["jobs"]["sync"]["steps"]
    probe = next(step for step in steps if step.get("name") == "Probe canary Actions API permissions")
    assert probe["if"] == "github.event_name == 'workflow_dispatch'"
    assert "V2_TAG_SYNC_ENABLED" not in probe["run"]
    assert "github.token" in probe["env"]["GITHUB_TOKEN"]
    assert probe["run"].count("--output /dev/null") == 1
    assert probe["run"].count("--write-out '%{http_code}'") == 1
    assert probe["run"].count("probe_canary_endpoint") == 4
    assert "actions/runs?per_page=1" in probe["run"]
    assert "/actions/runs/${CANARY_RUN_ID}" in probe["run"]
    assert "/actions/runs/${CANARY_RUN_ID}/jobs?per_page=1" in probe["run"]


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
    assert monotonicity["if"] == "steps.evidence.outputs.target_sha != ''"
    assert contract["if"] == "steps.monotonicity.outputs.move == 'true'"
    assert move["if"] == "steps.monotonicity.outputs.move == 'true'"
    assert "TARGET_SHA" in move["env"]
    assert steps.index(move) == len(steps) - 1


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
