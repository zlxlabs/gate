import re
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
    assert raw["permissions"] == {"contents": "write"}
    assert raw["jobs"]["sync"]["if"] == "github.ref == 'refs/heads/main'"


def test_sync_workflow_has_migration_switch_and_contract_before_push():
    raw, _ = _load()
    text = WORKFLOW.read_text()
    assert "vars.V2_TAG_SYNC_ENABLED" in text
    assert "scripts/v2_tag_guard.py" in text
    assert raw["jobs"]["sync"]["steps"][-2]["run"].strip().endswith(
        "tests/test_gate_caller_contract_guard.py"
    )
    assert "refs/tags/v2" in raw["jobs"]["sync"]["steps"][-1]["run"]
    assert "git ls-remote origin refs/tags/v2" in raw["jobs"]["sync"]["steps"][-1]["run"]
    assert "remote_result" in raw["jobs"]["sync"]["steps"][-1]["run"]
    assert "intended_sha" in raw["jobs"]["sync"]["steps"][-1]["run"]
    assert '"${push_status}" "${remote_result}"' in raw["jobs"]["sync"]["steps"][-1]["run"]
    assert "push_status=$?" in raw["jobs"]["sync"]["steps"][-1]["run"]
    assert "query_status=$?" in raw["jobs"]["sync"]["steps"][-1]["run"]
    assert re.search(r"(?m)^\s*exit 1\s*$", raw["jobs"]["sync"]["steps"][-1]["run"])
    guard = next(step for step in raw["jobs"]["sync"]["steps"] if step.get("id") == "guard")
    guard_run = guard["run"]
    assert "--hold-file" in guard_run
    assert ".github/v2-tag-sync.hold" in guard_run


def test_breaker_marker_blocks_advancement():
    assert main(
        ["--enabled", "true", "--commit-message", f"fix caller {HOLD_MARKER}"]
    ) == 1


def test_squash_safe_breaker_file_blocks_advancement(tmp_path):
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


def test_breaker_file_query_failure_is_hold(tmp_path, capsys):
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("not a directory")
    assert main(
        [
            "--enabled",
            "true",
            "--commit-message",
            "clean squashed message",
            "--hold-file",
            str(not_a_directory / "v2-tag-sync.hold"),
        ]
    ) == 1
    assert "held by breaker signal" in capsys.readouterr().out


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


def test_annotated_tag_output_still_reads_direct_ref(capsys):
    intended = "a" * 40
    peeled = "b" * 40
    remote_result = f"{intended}\trefs/tags/v2\n{peeled}\trefs/tags/v2^{{}}\n"
    assert verify_remote_tag(intended, 0, remote_result)
    assert "remote v2 tag verified" in capsys.readouterr().out


def test_missing_remote_tag_is_failure(capsys):
    assert not verify_remote_tag("a" * 40, 0, "")
    assert "refs/tags/v2 was not returned" in capsys.readouterr().out


def test_enabled_clean_commit_advances():
    assert main(["--enabled", "true", "--commit-message", "fix implementation"]) == 0


def test_disabled_migration_switch_blocks_advancement():
    assert main(["--enabled", "", "--commit-message", "fix implementation"]) == 1
