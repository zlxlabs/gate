"""Contract: quality job retains $GATE_ARTIFACT_DIR/junit/ after run-quality.

The reusable quality job already passes GATE_ARTIFACT_DIR to the caller's
scripts/gate-quality entry, but nothing read that directory before the
runner temp vanished. These tests lock the GitHub artifact upload that
keeps junit XML for 90 days, and the fail-open warning when that upload
fails (upload must not redden the required gate).

Reads the real producer workflow callers consume via @v2 — never a
hand-written fixture. 改坏步骤顺序、always() 守卫、路径/名字/保留期、
pin SHA、if-no-files-found、continue-on-error 或告警引用会红。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"

UPLOAD_USES = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
JUNIT_PATH = "${{ runner.temp }}/gate-quality/junit/"
JUNIT_NAME = "gate-quality-junit-${{ github.run_id }}-${{ github.run_attempt }}"
UPLOAD_IF = "${{ always() && steps.quality-entry.outputs.mode == 'entry' }}"
RUN_QUALITY_ID = "run-quality"
FAILED_MARKER = "JUNIT-UPLOAD-FAILED"


def _quality_steps():
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return raw["jobs"]["quality"]["steps"]


def _run_quality_index(steps):
    matches = [i for i, step in enumerate(steps) if step.get("id") == RUN_QUALITY_ID]
    assert len(matches) == 1, (
        f"quality job must have exactly one id={RUN_QUALITY_ID!r} step; found {len(matches)}"
    )
    return matches[0]


def _junit_upload_step(steps):
    matches = [
        (i, step)
        for i, step in enumerate(steps)
        if str(step.get("uses", "")) == UPLOAD_USES
        and str((step.get("with") or {}).get("path", "")) == JUNIT_PATH
    ]
    assert len(matches) == 1, (
        "quality job must declare exactly one junit upload step "
        f"(uses={UPLOAD_USES!r} path={JUNIT_PATH!r}); found {len(matches)}"
    )
    return matches[0]


def test_junit_upload_follows_run_quality_and_always_runs_in_entry_mode():
    steps = _quality_steps()
    run_quality_index = _run_quality_index(steps)
    upload_index, upload = _junit_upload_step(steps)
    assert upload_index > run_quality_index, (
        "junit upload must sit after run-quality so the entry can write "
        f"{JUNIT_PATH} before the runner temp is collected"
    )
    assert upload.get("if") == UPLOAD_IF, (
        "upload must run even when run-quality failed, but only in entry mode; "
        f"got if={upload.get('if')!r}"
    )
    assert "always()" in str(upload.get("if", ""))


def test_junit_upload_retains_caller_xml_for_ninety_days_without_failing_absent_files():
    _index, upload = _junit_upload_step(_quality_steps())
    with_block = upload.get("with") or {}
    assert with_block.get("path") == JUNIT_PATH
    assert with_block.get("name") == JUNIT_NAME
    assert with_block.get("retention-days") == 90
    assert with_block.get("if-no-files-found") == "ignore"
    assert upload.get("continue-on-error") is True, (
        "upload failure must not change the quality conclusion"
    )


def test_junit_upload_failure_emits_warning_without_changing_quality_verdict():
    steps = _quality_steps()
    upload_index, upload = _junit_upload_step(steps)
    upload_id = upload.get("id")
    assert upload_id, "junit upload step must have an id so the warning can read its outcome"
    assert upload_index + 1 < len(steps), "warning step must immediately follow the upload"
    warning = steps[upload_index + 1]
    guard = str(warning.get("if", ""))
    assert f"steps.{upload_id}.outcome == 'failure'" in guard, (
        f"warning if must reference steps.{upload_id}.outcome == 'failure'; got {guard!r}"
    )
    run = str(warning.get("run", ""))
    assert FAILED_MARKER in run, (
        f"warning step must emit {FAILED_MARKER}; got run={run!r}"
    )
    assert "::warning::" in run
    assert "run=" in run and "attempt=" in run
    assert warning.get("continue-on-error") is not True, (
        "the warning step itself is observability, not a second fail-open upload"
    )
