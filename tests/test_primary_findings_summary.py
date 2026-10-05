"""Contracts for the primary finding summary (gate-hub#1294).

A `fail` primary round used to publish nothing a downstream reader could act
on: the job log carried the verdict and the aggregate line, and the findings
lived only inside the canonical audit — which a caller repo cannot read, since
it has no silo credentials. These tests lock what the primary job now
republishes into the job log and `$GITHUB_STEP_SUMMARY`: four fields per
finding, capped and injection-safe, with every degraded outcome distinguishable
from "the reviewer found nothing".

The audit fixtures are real producer output, not invented shapes:
`tests/fixtures/primary-audit-redline-golden.json` is a captured canonical
audit written by gate-hub's `contracts.build_primary_record`, so the field
names read here are the producer's, and `title` is the alias that producer's
`_project_finding_text_aliases` copies onto every finding with a non-empty
`issue`.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
AGGREGATE = REPO_ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
GOLDEN_AUDIT = REPO_ROOT / "tests" / "fixtures" / "primary-audit-redline-golden.json"
STEP_NAME = "Summarize primary findings in the job log"
UNAVAILABLE = "PRIMARY-FINDINGS-SUMMARY-UNAVAILABLE"


def _render(audit_path: Path, tmp_path: Path, *, summary_name: str = "step-summary.md") -> tuple[subprocess.CompletedProcess[bytes], bytes]:
    """Run the real entry point in a subprocess and return (proc, summary bytes).

    The environment is reduced to PATH + GITHUB_STEP_SUMMARY on purpose: the
    renderer scrubs runner-local runtime values (see scripts/scrub_outbound.py),
    and an inherited ambient environment would make the expected bytes depend on
    the machine running the test.
    """
    summary = tmp_path / summary_name
    proc = subprocess.run(
        [
            sys.executable, str(AGGREGATE),
            "--render-findings-summary",
            "--audit-path", str(audit_path),
            "--summary-path", str(summary),
        ],
        capture_output=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_STEP_SUMMARY": str(summary)},
        check=False,
    )
    return proc, summary.read_bytes() if summary.exists() else b""


def _write_audit(tmp_path: Path, findings: list[dict], *, name: str = "audit.json", verdict: str = "fail") -> Path:
    """A captured canonical audit whose finding list is replaced by `findings`."""
    audit = copy.deepcopy(json.loads(GOLDEN_AUDIT.read_text(encoding="utf-8")))
    audit["verdict"] = verdict
    audit["result"]["verdict"] = verdict
    audit["result"]["findings"] = findings
    path = tmp_path / name
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _finding(**overrides) -> dict:
    finding = copy.deepcopy(json.loads(GOLDEN_AUDIT.read_text(encoding="utf-8"))["result"]["findings"][0])
    finding.update(overrides)
    return finding


def test_fail_audit_publishes_four_fields_per_finding(tmp_path):
    # The captured audit is a real `fail` round carrying three findings.
    proc, summary = _render(GOLDEN_AUDIT, tmp_path)
    assert proc.returncode == 0, proc.stderr.decode()
    assert proc.stdout.decode() == (
        "PRIMARY-FINDINGS-SUMMARY verdict=fail findings=3 shown=3\n"
        "PRIMARY-FINDING severity=major id=correctness.data-loss at=app.py:1 title=问题\n"
        "PRIMARY-FINDING severity=minor id=reliability.below-redline at=app.py:1 title=问题\n"
        "PRIMARY-FINDING severity=major id=correctness.unannotated at=app.py:1 title=问题\n"
    )
    assert summary.decode() == (
        "## primary review findings (verdict fail, 3 of 3 shown)\n"
        "\n"
        "| severity | id | location | title |\n"
        "| --- | --- | --- | --- |\n"
        "| major | correctness.data-loss | app.py:1 | 问题 |\n"
        "| minor | reliability.below-redline | app.py:1 | 问题 |\n"
        "| major | correctness.unannotated | app.py:1 | 问题 |\n"
    )


def test_absent_audit_is_not_rendered_as_zero_findings(tmp_path):
    proc, summary = _render(tmp_path / "never-written.json", tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.decode() == f"{UNAVAILABLE} reason=audit-missing\n"
    assert summary.decode() == f"## primary review findings\n\n{UNAVAILABLE} reason=audit-missing\n"
    assert "PRIMARY-FINDING " not in proc.stdout.decode()


def test_unparseable_audit_says_so_instead_of_falling_back(tmp_path):
    audit = tmp_path / "audit.json"
    audit.write_bytes(b'{"kind": "primary_review", "result": {"findings": [')
    proc, summary = _render(audit, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.decode().startswith(f"{UNAVAILABLE} reason=audit-unparseable ")
    assert f"{UNAVAILABLE} reason=audit-unparseable " in summary.decode()
    assert "| severity |" not in summary.decode()


def test_audit_without_findings_field_is_its_own_outcome(tmp_path):
    # A `unavailable` round carries result=null — no findings FIELD at all, which
    # is a different claim from a reviewer that looked and found nothing.
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps({"kind": "primary_review", "schema_version": 2, "verdict": "unavailable", "result": None}) + "\n",
        encoding="utf-8",
    )
    proc, summary = _render(audit, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.decode() == f"{UNAVAILABLE} reason=findings-field-missing\n"
    assert f"{UNAVAILABLE} reason=findings-field-missing\n" in summary.decode()

    empty = _write_audit(tmp_path, [], name="clean.json", verdict="pass")
    proc, summary = _render(empty, tmp_path, summary_name="clean-summary.md")
    assert proc.stdout.decode() == "PRIMARY-FINDINGS-SUMMARY verdict=pass findings=0 shown=0\n"
    assert summary.decode() == (
        "## primary review findings (verdict pass, 0 of 0 shown)\n"
        "\n"
        "| severity | id | location | title |\n"
        "| --- | --- | --- | --- |\n"
    )


def test_injected_workflow_command_never_reaches_either_surface(tmp_path):
    audit = _write_audit(
        tmp_path,
        [_finding(id="correctness.injected", title="first line\n::error::injected | pipe")],
    )
    proc, summary = _render(audit, tmp_path)
    assert proc.returncode == 0
    stdout = proc.stdout.decode()
    # No line may start with `::` (it would be parsed as a workflow command), and
    # the command prefix must not survive anywhere at all.
    assert not [line for line in stdout.splitlines() if line.startswith("::")]
    assert "::" not in stdout
    assert "::" not in summary.decode()
    assert (
        "PRIMARY-FINDING severity=major id=correctness.injected at=app.py:1 "
        "title=first line :error:injected | pipe\n"
    ) in stdout
    # A `|` inside a title would split the markdown row, so the cell escapes it.
    assert r"| major | correctness.injected | app.py:1 | first line :error:injected \| pipe |" in summary.decode()


def test_location_line_is_cleaned_like_every_other_field(tmp_path):
    # `line` is model output too, and it is joined into the location cell, so a
    # string carrying a newline, a `::` command prefix and a markdown-breaking
    # `|` must not reach either surface raw.
    audit = _write_audit(tmp_path, [_finding(id="correctness.line", line="3\n::error::x|y")])
    proc, summary = _render(audit, tmp_path)
    assert proc.returncode == 0
    stdout = proc.stdout.decode()
    assert not [line for line in stdout.splitlines() if line.startswith("::")]
    assert (
        "PRIMARY-FINDING severity=major id=correctness.line at=app.py:3 :error:x|y title=问题\n"
    ) in stdout
    # The published cell escapes the pipe so the table row survives.
    assert "| major | correctness.line | app.py:3 :error:x\\|y | 问题 |" in summary.decode()


def test_title_is_capped_and_finding_count_is_capped(tmp_path):
    long_title = "x" * 400
    audit = _write_audit(tmp_path, [_finding(id="correctness.long", title=long_title)] + [
        _finding(id=f"correctness.f{index}", title=f"finding {index}") for index in range(59)
    ])
    proc, summary = _render(audit, tmp_path)
    assert proc.returncode == 0
    stdout = proc.stdout.decode()
    rows = [line for line in stdout.splitlines() if line.startswith("PRIMARY-FINDING ")]
    assert len(rows) == 50
    assert "findings=60 shown=50" in stdout
    assert stdout.splitlines()[-1] == "PRIMARY-FINDINGS-SUMMARY-OVERFLOW ... and 10 more"
    long_row = next(row for row in rows if "id=correctness.long" in row)
    assert long_row.endswith("title=" + "x" * 199 + "…")
    assert long_title not in stdout
    # The same cap and the same overflow note hold on the published surface.
    summary_text = summary.decode()
    assert len([line for line in summary_text.splitlines() if line.startswith("| major |")]) == 50
    assert summary_text.startswith("## primary review findings (verdict fail, 50 of 60 shown)\n")
    assert summary_text.endswith("| major | correctness.f48 | app.py:1 | finding 48 |\n\n... and 10 more\n")


def test_published_titles_go_through_the_outbound_scrubber(tmp_path):
    # Every other publish path in this aggregator runs text through
    # scrub_for_publish before it leaves the runner; a finding title is model
    # output quoting the diff, so it gets the same treatment.
    audit = _write_audit(
        tmp_path, [_finding(id="security.token", title="leaked ghp_abcdefghijklmnopqrstuvwxyz0123456789 in app.py")]
    )
    proc, summary = _render(audit, tmp_path)
    assert proc.returncode == 0
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in proc.stdout.decode()
    assert "[REDACTED:TOKEN]" in summary.decode()


def _primary_steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["primary"]["steps"]


def test_workflow_step_publishes_the_summary_without_touching_the_verdict():
    steps = _primary_steps()
    names = [step.get("name") for step in steps]
    index = names.index(STEP_NAME)
    step = steps[index]
    # After the producer wrote the audit (and after relation annotation made it
    # final), before anything consumes or uploads it.
    assert names.index("Run review-primary") < index
    assert names.index("Annotate primary findings with cross-round relation") < index
    assert index < names.index("Upload canonical primary audit")
    assert step["if"] == "always()"
    assert "continue-on-error" not in step
    assert "env" not in step, "the step must not need any new secret"
    run = step["run"]
    assert "secrets." not in run and "permissions" not in run
    # The renderer always exits 0; the guard covers the interpreter itself, so
    # no failure mode of this step can change the primary job's conclusion.
    assert run.rstrip().endswith('|| echo "PRIMARY-FINDINGS-SUMMARY-UNAVAILABLE reason=render-invocation-failed"')
    assert f"{UNAVAILABLE} reason=aggregator-source-missing" in run
    assert "--render-findings-summary" in run
    assert '--audit-path "${{ runner.temp }}/primary-review-audit.json"' in run
    assert '--summary-path "$GITHUB_STEP_SUMMARY"' in run
    # Reuses the aggregate.py the relation step already fetched (same sparse
    # checkout directory) instead of adding a fetch to the primary job.
    annotate = steps[names.index("Annotate primary findings with cross-round relation")]
    assert '_gate-silo-src/.github/actions/gate-aggregator/aggregate.py' in run
    assert '_gate-silo-src/.github/actions/gate-aggregator/aggregate.py' in annotate["run"]
