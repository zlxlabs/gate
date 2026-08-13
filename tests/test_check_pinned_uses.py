"""Tests for the live internal uses pin guard."""

import subprocess
import sys
from pathlib import Path

from scripts.check_pinned_uses import find_pinned_use_violations


REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_pinned_uses.py"
INDEPENDENT_SHA = "9a7927410b8caef10d1c0ae5c31b3bb94bb1f5fc"


def test_current_live_workflows_have_no_floating_internal_uses():
    assert find_pinned_use_violations(REPO_ROOT) == []


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
