from pathlib import Path

import yaml

from scripts.v2_tag_guard import HOLD_MARKER, should_advance


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


def test_breaker_marker_blocks_advancement():
    assert not should_advance("true", f"fix caller {HOLD_MARKER}")


def test_enabled_clean_commit_advances():
    assert should_advance("true", "fix implementation")


def test_disabled_migration_switch_blocks_advancement():
    assert not should_advance("", "fix implementation")
