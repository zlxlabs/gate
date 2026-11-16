"""Contract: gate-v2 quality blocks caller PRs with undeclared or over-ceiling
artifact retention (gate-hub#2419).

The lint script runs as a REAL subprocess against fixture trees; assertions
cover the `::error` annotation payload (file, line, step label) and the exit
code. A regression run scans this repo's own workflows and composite actions
— they must stay green (all uploads carry `# retention:` comments since #420).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LINT = REPO_ROOT / ".github" / "actions" / "artifact-retention-lint" / "lint_retention.py"

STEP_FMT = """\
jobs:
  build:
    steps:
      - name: {label}
        uses: actions/upload-artifact@v4
        with:
          name: out
          path: dist/
{extra}\
"""

# uses line is line 5 in every STEP_FMT fixture; retention-days sits inside
# the with-block (line 9), the exemption comment (when present) on line 10.
# A bad VALUE is annotated on its declaration line; a missing/over-ceiling
# declaration can only be annotated on the uses line itself.
EXPECTED_LINES = {
    "missing": 5, "zero": 9, "expression": 9, "quoted": 9,
    "over-no-comment": 5, "comment-mismatch": 5,
}
RED_CASES = {
    "missing": "",
    "zero": "          retention-days: 0\n",
    "expression": "          retention-days: ${{ github.run_id }}\n",
    "quoted": "          retention-days: '14'\n",
    "over-no-comment": "          retention-days: 14\n",
    "comment-mismatch": (
        "          retention-days: 14\n"
        "          # retention: 7 — 消费方 x；理由 y\n"
    ),
}

GREEN_CASES = {
    "within-ceiling": "          retention-days: 7\n",
    "exempt-with-comment": (
        "          retention-days: 14\n"
        "          # retention: 14 — 消费方 disposition control；理由 申诉窗口 14 天\n"
    ),
}


def _run(scan_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LINT), str(scan_root)],
        capture_output=True, text=True, check=False,
    )


def _fixture(root: Path, body: str, name: str = "wf.yml") -> None:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / name).write_text(body)


@pytest.mark.parametrize("case", sorted(RED_CASES))
def test_red_samples_fail_with_annotated_error(tmp_path, case):
    _fixture(tmp_path, STEP_FMT.format(label=f"upload {case}", extra=RED_CASES[case]))
    done = _run(tmp_path)
    assert done.returncode != 0, done.stdout
    errors = [ln for ln in done.stdout.splitlines() if ln.startswith("::error ")]
    assert len(errors) == 1, done.stdout
    # Producer payload: the annotation must name the exact file and the line
    # of the offense (declaration line for bad values, uses line otherwise).
    assert (
        f"file=.github/workflows/wf.yml,line={EXPECTED_LINES[case]}" in errors[0]
    ), errors[0]
    assert f"upload {case}" in errors[0], errors[0]
    assert "retention-days" in errors[0]


@pytest.mark.parametrize("case", sorted(GREEN_CASES))
def test_green_samples_pass_clean(tmp_path, case):
    _fixture(tmp_path, STEP_FMT.format(label=f"upload {case}", extra=GREEN_CASES[case]))
    done = _run(tmp_path)
    assert done.returncode == 0, done.stdout
    assert "::error" not in done.stdout


def test_composite_action_yml_is_scanned(tmp_path):
    compliant = (
        "name: x\nruns:\n  using: composite\n  steps:\n"
        "    - uses: actions/upload-artifact@v4\n      with:\n"
        "        name: out\n        path: dist/\n        retention-days: 3\n"
    )
    action = tmp_path / ".github" / "actions" / "myaction" / "action.yml"
    action.parent.mkdir(parents=True)
    action.write_text(compliant)
    done = _run(tmp_path)
    assert done.returncode == 0, done.stdout
    action.write_text(compliant.replace("retention-days: 3\n", ""))
    done = _run(tmp_path)
    assert done.returncode != 0
    assert f"file={action.relative_to(tmp_path).as_posix()},line=5" in done.stdout


def test_no_workflow_files_fails_loud(tmp_path):
    done = _run(tmp_path)
    assert done.returncode != 0
    assert "::error::artifact-retention-lint: no workflow/action.yml files" in done.stdout


def test_gate_repo_itself_stays_green():
    done = _run(REPO_ROOT)
    assert done.returncode == 0, done.stdout
    assert "::error" not in done.stdout
