"""Structural and execution-level contracts for the gate-v2 advisor pair.

C5 (successor of the C2 shadow job): `advisor_shadow` is split into
`advisor_classify` (classification only, always succeeds, publishes the
`declared_only` output) and `advisor` (enforcing — the job verdict IS the
advisor verdict). agent-config's merge gate consumes the `gate / advisor`
check and auto-approves ONLY on `success`; a `skipped` advisor (non
declared-only classification, draft, fork, hosted, classifier exemption)
escalates to a human. The split exists because a single job that recorded
success for "not declared-only" would let the consumer approve a PR whose
advisor never ran whenever the gate-side and agent-config-side classifiers
disagree — a backdoor shape.
"""
from pathlib import Path

import base64
import json
import os
import subprocess

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
CLASSIFIER_GUARD = "needs.classify_pr_paths.outputs.review_expected != 'false'"
CLASSIFIER_GUARD_EQUIVALENT = (
    "!contains(fromJSON('[\"false\"]'), "
    "needs.classify_pr_paths.outputs.review_expected)"
)
DECLARED_ONLY_CONDITION = "needs.advisor_classify.outputs.declared_only == 'true'"
HEAD_SHA = "h" * 40
PARSED_VERDICTS = {
    "ADVISOR-VERDICT no_backdoor": "no_backdoor",
    "ADVISOR-VERDICT backdoor": "backdoor",
    "ADVISOR-VERDICT uncertain": "uncertain",
}


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _advisor_classify_step():
    job = _jobs()["advisor_classify"]
    assert len(job["steps"]) == 1
    return job["steps"][0]


def _advisor_step():
    job = _jobs()["advisor"]
    assert len(job["steps"]) == 1
    return job["steps"][0]


# ── structural contracts ─────────────────────────────────────────────────


def test_advisor_shadow_is_replaced_by_the_isolated_job_pair():
    jobs = _jobs()
    assert "advisor_shadow" not in jobs
    assert jobs["advisor_classify"]["needs"] == ["classify_pr_paths"]
    assert jobs["advisor"]["needs"] == ["advisor_classify"]
    for name, job in jobs.items():
        if name in ("advisor_classify", "advisor"):
            continue
        # exact matches: 'advisor' is a substring of resolve_advisory's name
        assert not (set(job.get("needs", [])) & {"advisor_classify", "advisor"}), name
    assert jobs["gate"]["needs"] == ["quality", "primary", "classify_pr_paths"]
    assert "advisor" not in str(jobs["gate"])


def test_advisor_classify_always_succeeds_and_exposes_declared_only():
    jobs = _jobs()
    primary = jobs["primary"]
    classify = jobs["advisor_classify"]
    expected_if = primary["if"].replace(CLASSIFIER_GUARD, CLASSIFIER_GUARD_EQUIVALENT)
    assert expected_if != primary["if"]
    assert classify["if"] == expected_if
    assert classify["runs-on"] == primary["runs-on"]
    # The JOB always succeeds: no job-level continue-on-error (the step below
    # carries it), and its only output feeds the advisor job's skip condition.
    assert classify.get("continue-on-error") is not True
    assert classify["outputs"] == {
        "declared_only": "${{ steps.classify-declared.outputs.declared_only }}",
    }
    step = _advisor_classify_step()
    assert step["id"] == "classify-declared"
    assert step["continue-on-error"] is True
    run = step["run"]
    assert "classify_human_required" in run
    assert 'hit.startswith("human:declared:")' in run
    assert "except Exception as exc:" in run  # equivalent full-exception catch
    assert "review-advisor" not in run  # the classify job never runs the advisor
    assert "ADVISOR-CLASSIFY declared_only={declared_only}" in run
    assert "reason={stage}_failed type={type(exc).__name__}" in run
    assert 'outputs.write(f"declared_only={declared_only}' in run


def test_advisor_job_enforces_the_verdict_with_no_continue_on_error():
    jobs = _jobs()
    primary = jobs["primary"]
    advisor = jobs["advisor"]
    assert advisor["if"] == primary["if"].replace(
        CLASSIFIER_GUARD, CLASSIFIER_GUARD_EQUIVALENT
    ).replace(
        "inputs.runner == 'self' &&",
        f"inputs.runner == 'self' && {DECLARED_ONLY_CONDITION} &&",
    )
    assert advisor["runs-on"] == primary["runs-on"]
    step = _advisor_step()
    assert step.get("continue-on-error") is not True
    run = step["run"]
    assert "scripts/review/review-advisor" in run
    assert "ADVISOR-VERDICT (no_backdoor|backdoor|uncertain)" in run
    assert (
        "ADVISOR-RESULT verdict={verdict} exit={exit_code} "
        "head={os.environ['HEAD_SHA']}"
    ) in run
    assert 'line.startswith("REVIEW-ADVISOR-ERROR")' in run
    assert "line[:500]" in run
    assert "][:5]" in run
    # success ⇔ (advisor exit 0 AND first summary line no_backdoor)
    assert 'verdict != "no_backdoor" or exit_code != "0"' in run
    assert "except Exception" in run


# ── execution-level contracts (stub gate-hub tree + stub gh) ─────────────


def _make_hub(tmp_path, *, classifier_mode=None, rule_hits="[]",
              advisor_first_line=None, advisor_exit=0, advisor_error_lines=0):
    hub = tmp_path
    review = hub / "scripts" / "review"
    review.mkdir(parents=True)
    if classifier_mode is not None:
        (review / "risk_classification.py").write_text(
            "import json\n"
            "import os\n"
            "\n"
            "\n"
            "def classify_human_required(changes, human_review_paths_text):\n"
            "    if os.environ['FAKE_CLASSIFIER_MODE'] == 'boom':\n"
            "        raise ValueError('fake-classifier-boom')\n"
            "    return {'rule_hits': json.loads(os.environ['FAKE_RULE_HITS'])}\n",
            encoding="utf-8",
        )
    if advisor_first_line is not None:
        stub = review / "review-advisor"
        stub.write_text(
            "import os\n"
            "import sys\n"
            "\n"
            "first_line = os.environ.get('FAKE_ADVISOR_FIRST_LINE', '')\n"
            "error_lines = int(os.environ.get('FAKE_ADVISOR_ERROR_LINES', '0'))\n"
            "with open(os.environ['GITHUB_STEP_SUMMARY'], 'w', encoding='utf-8') as summary:\n"
            "    summary.write(first_line + '\\n\\n## Advisor report\\nadvisor body text\\n')\n"
            "for index in range(error_lines):\n"
            "    sys.stderr.write('REVIEW-ADVISOR-ERROR line-%02d %s\\n' % (index, 'x' * 600))\n"
            "sys.exit(int(os.environ.get('FAKE_ADVISOR_EXIT', '0')))\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    return hub


def _make_gh(tmp_path):
    tmp_path.mkdir(parents=True)
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import base64\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "\n"
        "endpoint = sys.argv[-1]\n"
        "repo = os.environ['REPOSITORY']\n"
        "pr = os.environ['PR_NUMBER']\n"
        "base_sha = os.environ['BASE_SHA']\n"
        "head_sha = os.environ['HEAD_SHA']\n"
        "\n"
        "def reply(payload):\n"
        "    print(json.dumps(payload))\n"
        "\n"
        "if endpoint == f'repos/{repo}/pulls/{pr}':\n"
        "    reply({'base': {'sha': base_sha}, 'head': {'sha': head_sha}, 'changed_files': 1})\n"
        "elif endpoint == f'repos/{repo}/pulls/{pr}/files?per_page=100':\n"
        "    entry = {'filename': 'docs/secret-design.md'}\n"
        "    print(base64.b64encode(json.dumps(entry).encode()).decode())\n"
        "elif endpoint == f'repos/{repo}/git/trees/{base_sha}':\n"
        "    reply({'truncated': False, 'tree': [{'path': '.gate', 'type': 'tree', 'sha': 'gate-tree-sha001'}]})\n"
        "elif endpoint == f'repos/{repo}/git/trees/gate-tree-sha001':\n"
        "    reply({'truncated': False, 'tree': [{'path': 'human-review-paths', 'type': 'blob', 'sha': 'human-blob-sha01'}]})\n"
        "elif endpoint == f'repos/{repo}/git/blobs/human-blob-sha01':\n"
        "    reply({'encoding': 'base64', 'content': base64.b64encode(b'docs/secret-design.md\\n').decode()})\n"
        "else:\n"
        "    sys.stderr.write('stub gh: unexpected endpoint ' + endpoint + '\\n')\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return tmp_path


def _run_step(step, tmp_path, *, extra_env, summary, output=None, bin_dir=None):
    env = dict(os.environ)
    env.update(step.get("env") or {})
    env["GATE_HUB_DIR"] = str(tmp_path / "hub")
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    if output is not None:
        env["GITHUB_OUTPUT"] = str(output)
    env.update(extra_env)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    return subprocess.run(["bash", "-c", step["run"]], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, check=False)


def _run_classify(tmp_path, mode, rule_hits="[]"):
    _make_hub(tmp_path / "hub", classifier_mode=mode, rule_hits=rule_hits)
    bin_dir = _make_gh(tmp_path / "bin")
    summary = tmp_path / "summary.md"
    output = tmp_path / "output.txt"
    proc = _run_step(
        _advisor_classify_step(), tmp_path,
        extra_env={
            "FAKE_CLASSIFIER_MODE": mode,
            "FAKE_RULE_HITS": rule_hits,
            "REPOSITORY": "zlxlabs/caller", "PR_NUMBER": "42",
            "BASE_SHA": "b" * 40, "HEAD_SHA": HEAD_SHA,
            "GH_TOKEN": "stub-token",
        },
        summary=summary, output=output, bin_dir=bin_dir,
    )
    return (
        proc,
        summary.read_text(encoding="utf-8"),
        output.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize(
    "mode,rule_hits,declared_only",
    [
        ("hits", '["human:declared:secret-design"]', "true"),
        ("hits", '["human:workflow"]', "false"),
        ("hits", '["human:declared:secret-design", "human:workflow"]', "false"),
        ("hits", '["scripts/foo.py"]', "false"),
        ("boom", "[]", "error"),
    ],
)
def test_advisor_classify_publishes_declared_only_and_always_exits_zero(
    tmp_path, mode, rule_hits, declared_only,
):
    proc, summary, output = _run_classify(tmp_path, mode, rule_hits)
    assert proc.returncode == 0, proc.stderr
    assert f"declared_only={declared_only}" in output
    if declared_only == "error":
        assert (
            f"ADVISOR-CLASSIFY declared_only=error "
            f"reason=human_path_classification_failed type=ValueError head={HEAD_SHA}"
        ) in summary
    else:
        assert (
            f"ADVISOR-CLASSIFY declared_only={declared_only} head={HEAD_SHA}"
        ) in summary


def _run_advisor(tmp_path, *, first_line=None, exit_code=0, error_lines=0,
                 raising_stub=False):
    hub = _make_hub(
        tmp_path / "hub",
        advisor_first_line=None if raising_stub else first_line,
        advisor_exit=exit_code,
        advisor_error_lines=error_lines,
    )
    if raising_stub:
        stub = hub / "scripts" / "review" / "review-advisor"
        stub.write_text("raise RuntimeError('stub-advisor-boom')\n", encoding="utf-8")
        stub.chmod(0o755)
    summary = tmp_path / "summary.md"
    proc = _run_step(
        _advisor_step(), tmp_path,
        extra_env={
            "FAKE_ADVISOR_FIRST_LINE": first_line or "",
            "FAKE_ADVISOR_EXIT": str(exit_code),
            "FAKE_ADVISOR_ERROR_LINES": str(error_lines),
            "REPOSITORY": "zlxlabs/caller", "PR_NUMBER": "42",
            "HEAD_SHA": HEAD_SHA, "GH_TOKEN": "stub-token",
        },
        summary=summary,
    )
    return proc, summary.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "first_line,exit_code,expect_success",
    [
        ("ADVISOR-VERDICT no_backdoor", 0, True),
        ("ADVISOR-VERDICT backdoor", 0, False),
        ("ADVISOR-VERDICT uncertain", 0, False),
        ("not a verdict line", 0, False),
        ("", 0, False),
        ("ADVISOR-VERDICT no_backdoor", 3, False),  # exit 0 is required, not implied
    ],
)
def test_advisor_job_succeeds_only_on_no_backdoor_with_exit_zero(
    tmp_path, first_line, exit_code, expect_success,
):
    proc, summary = _run_advisor(tmp_path, first_line=first_line, exit_code=exit_code)
    expected_verdict = PARSED_VERDICTS.get(first_line, "error")
    last_line = summary.strip().splitlines()[-1]
    assert last_line == (
        f"ADVISOR-RESULT verdict={expected_verdict} exit={exit_code} head={HEAD_SHA}"
    )
    if expect_success:
        assert proc.returncode == 0, proc.stderr
    else:
        assert proc.returncode != 0


def test_advisor_job_keeps_summary_text_and_at_most_five_truncated_errors(tmp_path):
    proc, summary = _run_advisor(
        tmp_path, first_line="ADVISOR-VERDICT no_backdoor", exit_code=0,
        error_lines=7,
    )
    assert proc.returncode == 0, proc.stderr
    assert "## Advisor report\nadvisor body text" in summary
    error_lines = [
        ln for ln in summary.splitlines() if ln.startswith("REVIEW-ADVISOR-ERROR")
    ]
    assert len(error_lines) == 5
    assert all(len(line) == 500 for line in error_lines)


def test_advisor_job_fails_loudly_when_the_advisor_stub_raises(tmp_path):
    proc, summary = _run_advisor(tmp_path, raising_stub=True)
    assert proc.returncode != 0
    last_line = summary.strip().splitlines()[-1]
    assert last_line == f"ADVISOR-RESULT verdict=error exit=1 head={HEAD_SHA}"
