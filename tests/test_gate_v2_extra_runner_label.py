"""gate-hub#1724: caller-supplied extra runner labels on gate-v2's runs-on.

Two inputs split BY RUNNER CLASS: `extra_runner_label_ci` appends ONE label to
quality's SELF-HOSTED branch (ci-class slots) only; `extra_runner_label_review`
appends ONE label to primary / ledger's (codex-class slots) only. Empty inputs
leave every branch byte-equivalent to the pre-#1724 form; a legal input appends
at the END of its own class's array only; an illegal one fails loudly with an
EXTRA-RUNNER-LABEL-INVALID anchor naming the offending input.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# GATE_V2_WORKFLOW_PATH is a mutation hook for the red-verification run.
WORKFLOW = Path(os.environ.get("GATE_V2_WORKFLOW_PATH") or REPO_ROOT / ".github/workflows/gate-v2.yml")
CI_JOBS = ("quality",)
REVIEW_JOBS = ("primary", "ledger")
TARGET_JOBS = CI_JOBS + REVIEW_JOBS

# The pre-#1724 runs-on parts, verbatim (base commit ea62830).
_GUARD_CONTROL = """(inputs.runner == 'self' && inputs.control_runner != 'github-hosted' && (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository))"""
_GUARD_AUTHOR = """(inputs.runner == 'self' && github.event.pull_request.head.repo.full_name == github.repository && startsWith(toJSON(github.event.pull_request.user.login), '"') && endsWith(toJSON(github.event.pull_request.user.login), '"') && toJSON(github.event.pull_request.user.login) != '""' && toJSON(github.event.pull_request.user.login) != '"dependabot[bot]"')"""
_GUARD_CI = """(inputs.runner == 'self' && (github.event_name != 'pull_request' || (github.event.pull_request.head.repo.full_name == github.repository && startsWith(toJSON(github.event.pull_request.user.login), '"') && endsWith(toJSON(github.event.pull_request.user.login), '"') && toJSON(github.event.pull_request.user.login) != '""' && toJSON(github.event.pull_request.user.login) != '"dependabot[bot]"')))"""
PRE_CHANGE = {
    **{job: (_GUARD_CONTROL, ["self-hosted", "linux", "codex"]) for job in ("classify_pr_paths", "gate", "ledger", "notify")},
    **{job: (_GUARD_AUTHOR, ["self-hosted", "linux", "codex"]) for job in ("primary", "resolve_advisory", "ocr")},
    "quality": (_GUARD_CI, ["self-hosted", "linux", "ci"]),
}

_RUNS_ON_RE = re.compile(r"^\$\{\{\s*(?P<guard>\(.*\))\s*&&\s*(?P<slot>\(.*\)|fromJSON\('[^']*'\))\s*\|\|\s*(?P<hosted>fromJSON\('[^']*'\))\s*\}\}$")
_GHA_LITERAL = re.compile(r"('(?:[^']|'')*')")
_GHA_NS = {
    "_gha_fromJSON": json.loads,
    "_gha_toJSON": lambda v: json.dumps(v, separators=(",", ":")),
    "_gha_format": lambda fmt, *a: re.sub(r"\{(\d+)\}", lambda m: str(a[int(m.group(1))]), fmt),
}


def _split(runs_on):
    """Split a runs-on expression into (guard, self-hosted slot, hosted fallback)."""
    match = _RUNS_ON_RE.match(runs_on.strip())
    assert match, f"unparsable runs-on expression: {runs_on!r}"
    parts = (match.group("guard"), match.group("slot"), match.group("hosted"))
    assert " ".join(f"${{{{ {parts[0]} && {parts[1]} || {parts[2]} }}}}".split()) == " ".join(runs_on.split())
    return parts


def _gha_python(src):
    """Translate the GHA expression subset gate-v2 uses into Python source."""
    def chunk(text):
        text = text.replace("&&", " and ").replace("||", " or ")
        text = re.sub(r"\b(fromJSON|toJSON|format)\(", r"_gha_\1(", text)
        return re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z_][A-Za-z0-9_]*)+)",
            lambda m: m.group(1) + "".join(f"[{p!r}]" for p in m.group(2)[1:].split(".")),
            text,
        )
    parts = _GHA_LITERAL.split(src)
    return "".join(chunk(p) if index % 2 == 0 else p for index, p in enumerate(parts))


def _render_slot(slot, ci="", review=""):
    """Render the label array of a slot for the given input labels."""
    return eval(_gha_python(slot), {"__builtins__": {}},
                {**_GHA_NS, "inputs": {"extra_runner_label_ci": ci, "extra_runner_label_review": review}})


def _workflow():
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return raw, raw.get("on", raw.get(True))

_RETIRED_BARE = re.compile(r"extra_runner_label(?![_A-Za-z0-9])")


def test_label_inputs_are_declared_and_the_retired_input_is_unused():
    raw, trigger = _workflow()
    inputs = trigger["workflow_call"]["inputs"]
    assert inputs["extra_runner_label_ci"] == inputs["extra_runner_label_review"] == {"type": "string", "default": ""}
    # gate-hub#1724 retirement: the pre-#1724 single input stays DECLARED
    # (the moving-v2 contract at 8c475d11 forbids removing inputs) but is
    # only referenced by its declaration and the classify_pr_paths retirement
    # check — never by any runs-on.
    assert inputs["extra_runner_label"] == {"type": "string", "default": ""}
    assert not any(_RETIRED_BARE.search(str(job.get("runs-on"))) for job in raw["jobs"].values())
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        if not _RETIRED_BARE.search(line):
            continue
        assert (
            re.match(r'\s*extra_runner_label:\s*\{ type: string, default: "" \}\s*(#.*)?$', line)
            or "inputs.extra_runner_label" in line
            or "is retired" in line
        ), line


def test_only_the_three_target_jobs_reference_the_label_inputs():
    raw, _ = _workflow()
    referencing = [job for job in raw["jobs"] if "extra_runner_label_" in str(raw["jobs"][job]["runs-on"])]
    assert sorted(referencing) == sorted(TARGET_JOBS)


def test_runs_on_parts_match_the_pre_change_baseline():
    raw, _ = _workflow()
    for job, (old_guard, old_labels) in PRE_CHANGE.items():
        guard, slot, hosted = _split(str(raw["jobs"][job]["runs-on"]))
        # Same guard => same branch everywhere; same slot => empty input is a no-op.
        assert guard == old_guard, job
        assert _render_slot(hosted, "") == ["ubuntu-latest"], job
        assert _render_slot(slot, "") == old_labels, job


def test_a_label_appends_only_to_jobs_of_its_own_runner_class():
    raw, _ = _workflow()
    for label_kwargs, own_jobs in ((dict(ci="canary-abc123-ci-03"), CI_JOBS), (dict(review="canary-abc123-review-01"), REVIEW_JOBS)):
        for job in TARGET_JOBS:
            slot, hosted = _split(str(raw["jobs"][job]["runs-on"]))[1:]
            expected = PRE_CHANGE[job][1] + ([next(iter(label_kwargs.values()))] if job in own_jobs else [])
            assert _render_slot(slot, **label_kwargs) == expected and "extra_runner_label_" not in hosted, (label_kwargs, job)


def test_both_labels_each_target_their_own_jobs():
    raw, _ = _workflow()
    ci_label, review_label = "canary-ci-01", "canary-review-02"
    for job in TARGET_JOBS:
        slot = _split(str(raw["jobs"][job]["runs-on"]))[1]
        extra = [ci_label] if job in CI_JOBS else [review_label]
        assert _render_slot(slot, ci=ci_label, review=review_label) == PRE_CHANGE[job][1] + extra, job


def test_slot_renderer_semantics_selfcheck():
    slot = """(inputs.extra_runner_label_review != '' && fromJSON(format('["a",{0}]', toJSON(inputs.extra_runner_label_review))) || fromJSON('["b"]'))"""
    assert _render_slot(slot, review="x") == ["a", "x"]
    assert _render_slot(slot) == ["b"]
    assert _render_slot("""fromJSON('["c"]')""", ci="x") == ["c"]


def _validation_step():
    raw, _ = _workflow()
    job = raw["jobs"]["classify_pr_paths"]
    assert str(job.get("if")) == "always()"
    step = job["steps"][0]
    assert step["name"] == "Validate extra runner label inputs"
    assert step["env"]["EXTRA_RUNNER_LABEL_CI"] == "${{ inputs.extra_runner_label_ci }}"
    assert step["env"]["EXTRA_RUNNER_LABEL_REVIEW"] == "${{ inputs.extra_runner_label_review }}"
    return step


def _run_validation(script, ci_label="", review_label="", retired_label=""):
    env = dict(
        os.environ,
        EXTRA_RUNNER_LABEL_RETIRED=retired_label,
        EXTRA_RUNNER_LABEL_CI=ci_label,
        EXTRA_RUNNER_LABEL_REVIEW=review_label,
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_validation_step_rejects_an_illegal_label_loudly():
    script = _validation_step()["run"]
    for bad in ("Uppercase", "has space", "x" * 65, "label!", "标签", 'a"b', "a\nb", "ci=x"):
        for name, kwargs in (("extra_runner_label_ci", dict(ci_label=bad)), ("extra_runner_label_review", dict(review_label=bad))):
            result = _run_validation(script, **kwargs)
            anchors = [ln for ln in result.stdout.splitlines() if ln.startswith("EXTRA-RUNNER-LABEL-INVALID")]
            assert result.returncode != 0 and len(anchors) == 1 and name in anchors[0], (name, bad, anchors)


def test_validation_step_accepts_an_empty_or_legal_label():
    script = _validation_step()["run"]
    for good in ("", "canary-abc123-ci-i5", "a", "x" * 64):
        result = _run_validation(script, ci_label=good, review_label=good)
        assert result.returncode == 0, (good, result.stdout, result.stderr)
        assert "EXTRA-RUNNER-LABEL-INVALID" not in result.stdout


def test_retired_input_fails_loudly_when_non_empty_and_passes_when_empty():
    script = _validation_step()["run"]
    result = _run_validation(script, retired_label="canary-legacy-slot")
    anchors = [ln for ln in result.stdout.splitlines() if ln.startswith("EXTRA-RUNNER-LABEL-INVALID")]
    assert result.returncode != 0, result.stdout
    assert anchors == [
        "EXTRA-RUNNER-LABEL-INVALID: extra_runner_label is retired; use extra_runner_label_ci / extra_runner_label_review"
    ]
    assert _run_validation(script).returncode == 0
