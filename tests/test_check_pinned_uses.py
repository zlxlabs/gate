"""Tests for the live internal uses pin guard."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.check_pinned_uses import find_pinned_use_violations


REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_pinned_uses.py"
INDEPENDENT_SHA = "9a7927410b8caef10d1c0ae5c31b3bb94bb1f5fc"
SELF_CALLER = ".github/workflows/gate.yml"
PUBLISHED_GATE = "zlxlabs/gate/.github/workflows/gate-v2.yml@v2"


def test_current_live_workflows_have_no_disallowed_internal_uses():
    assert find_pinned_use_violations(REPO_ROOT) == []
    checked = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_self_caller_allows_published_gate(tmp_path):
    workflow = tmp_path / SELF_CALLER
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  gate:\n    uses: {PUBLISHED_GATE}\n", encoding="utf-8")

    assert find_pinned_use_violations(tmp_path) == []


@pytest.mark.parametrize(
    ("path", "value", "ref"),
    [
        (SELF_CALLER, "zlxlabs/gate/.github/workflows/gate-v2.yml@main", "main"),
        (SELF_CALLER, f"zlxlabs/gate/.github/workflows/gate-v2.yml@{INDEPENDENT_SHA}", INDEPENDENT_SHA),
        (SELF_CALLER, "zlxlabs/gate/.github/workflows/gate-v2.yml@v20", "v20"),
        (SELF_CALLER, "zlxlabs/gate/.github/workflows/gate-shadow-v2.yml@v2", "v2"),
        (".github/workflows/nested/gate.yml", PUBLISHED_GATE, "v2"),
    ],
)
def test_self_caller_exemption_rejects_other_paths_and_references(tmp_path, path, value, ref):
    workflow = tmp_path / path
    workflow.parent.mkdir(parents=True)
    workflow.write_text(f"jobs:\n  gate:\n    uses: {value}\n", encoding="utf-8")

    violations = find_pinned_use_violations(tmp_path)

    assert [(item.file_path, item.line_number, item.ref) for item in violations] == [
        (path, 3, ref)
    ]


def test_self_caller_workflow_contract():
    # BaseLoader keeps GitHub's `on` key a string rather than YAML 1.1 true.
    workflow = yaml.load((REPO_ROOT / SELF_CALLER).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert workflow["name"] == "gate"
    assert workflow["on"]["pull_request"]["types"] == [
        "opened", "synchronize", "reopened", "ready_for_review", "converted_to_draft"
    ]
    assert workflow["permissions"] == {
        "actions": "read", "contents": "read", "pull-requests": "write"
    }
    gate = workflow["jobs"]["gate"]
    assert gate["uses"] == PUBLISHED_GATE
    assert gate["with"] == {"tier": "personal", "runner": "self"}
    assert gate["secrets"] == "inherit"


def test_temporary_sample_reports_file_line_and_ref(tmp_path):
    workflow = tmp_path / ".github" / "workflows" / "sample.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n"
        "  gate:\n"
        "    uses: zlxlabs/gate/.github/workflows/gate.yml@main\n"
        "  external:\n"
        "    uses: actions/checkout@v4\n",
        encoding="utf-8",
    )

    violations = find_pinned_use_violations(tmp_path)

    assert [(item.file_path, item.line_number, item.ref) for item in violations] == [
        (".github/workflows/sample.yml", 3, "main")
    ]


def test_relative_internal_action_is_allowed(tmp_path):
    metadata = tmp_path / ".github" / "actions" / "local" / "action.yml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - uses: ./.github/actions/another\n",
        encoding="utf-8",
    )

    assert find_pinned_use_violations(tmp_path) == []


def test_dash_uses_in_composite_metadata_reports_line_ref_and_cli_failure(tmp_path):
    metadata = tmp_path / ".github" / "actions" / "local" / "action.yml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - uses: zlxlabs/gate/.github/actions/review-ledger@main\n"
        "    - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )

    violations = find_pinned_use_violations(tmp_path)

    assert [(item.file_path, item.line_number, item.ref) for item in violations] == [
        (".github/actions/local/action.yml", 4, "main")
    ]
    checked = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 1
    assert ".github/actions/local/action.yml:4:main" in checked.stdout


def test_dash_uses_in_composite_metadata_rejects_independent_sha(tmp_path):
    metadata = tmp_path / ".github" / "actions" / "local" / "action.yml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "runs:\n"
        "  using: composite\n"
        f"  steps:\n    - uses: zlxlabs/gate/.github/actions/review-ledger@{INDEPENDENT_SHA}\n",
        encoding="utf-8",
    )

    violations = find_pinned_use_violations(tmp_path)

    assert [(item.file_path, item.line_number, item.ref) for item in violations] == [
        (".github/actions/local/action.yml", 4, INDEPENDENT_SHA)
    ]


def test_cli_rejects_independent_sha_and_floating_ref(tmp_path):
    workflow = tmp_path / ".github" / "workflows" / "sample.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        f"jobs:\n  gate:\n    uses: zlxlabs/gate/.github/workflows/gate.yml@{INDEPENDENT_SHA}\n",
        encoding="utf-8",
    )
    independent = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert independent.returncode == 1
    assert f".github/workflows/sample.yml:3:{INDEPENDENT_SHA}" in independent.stdout
    assert "internal uses must use a workspace-relative path" in independent.stdout
    assert "FAIL: found 1 independently referenced internal uses" in independent.stderr

    workflow.write_text(
        "jobs:\n  gate:\n    uses: zlxlabs/gate/.github/workflows/gate.yml@main\n",
        encoding="utf-8",
    )
    injected = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert injected.returncode == 1
    assert ".github/workflows/sample.yml:3:main" in injected.stdout
