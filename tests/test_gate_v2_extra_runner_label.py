"""gate-hub#1724: caller-supplied `extra_runner_label` on gate-v2's runs-on.

The input appends ONE extra runner label to the SELF-HOSTED branch of the
quality / primary / ledger jobs. Locked here, on the workflow's own `runs-on`
text: an empty input (the default) leaves every branch byte-equivalent to the
pre-#1724 expression; a legal input appends the label at the END of the
self-hosted array only; an illegal input fails loudly with an
EXTRA-RUNNER-LABEL-INVALID anchor line. A `runs-on` expression is
`${{ <guard> && <slot> || <hosted> }}`; splitting it into those three parts
makes the branch coverage complete (an IDENTICAL guard selects the same branch
in every context, so an identical slot + hosted fallback => identical
rendering). Slots are rendered by translating the small GitHub Actions
expression subset gate-v2 uses into Python and evaluating it.
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
TARGET_JOBS = ("quality", "primary", "ledger")

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


def _render_slot(slot, label):
    """Render the label array of a slot for the given input label."""
    return eval(_gha_python(slot), {"__builtins__": {}},
                {**_GHA_NS, "inputs": {"extra_runner_label": label}})


def _workflow():
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return raw, raw.get("on", raw.get(True))

def test_extra_runner_label_input_is_declared_with_an_empty_default():
    _, trigger = _workflow()
    assert trigger["workflow_call"]["inputs"]["extra_runner_label"] == {"type": "string", "default": ""}


def test_only_the_three_target_jobs_reference_the_new_input():
    raw, _ = _workflow()
    referencing = [job for job in raw["jobs"] if "extra_runner_label" in str(raw["jobs"][job]["runs-on"])]
    assert sorted(referencing) == sorted(TARGET_JOBS)


def test_runs_on_parts_match_the_pre_change_baseline():
    raw, _ = _workflow()
    for job, (old_guard, old_labels) in PRE_CHANGE.items():
        guard, slot, hosted = _split(str(raw["jobs"][job]["runs-on"]))
        # Same guard => same branch everywhere; same slot => empty input is a no-op.
        assert guard == old_guard, job
        assert _render_slot(hosted, "") == ["ubuntu-latest"], job
        assert _render_slot(slot, "") == old_labels, job


def test_valid_label_appends_to_the_end_of_the_self_hosted_array():
    raw, _ = _workflow()
    label = "canary-abc123-ci-i5"
    for job in TARGET_JOBS:
        _, slot, hosted = _split(str(raw["jobs"][job]["runs-on"]))
        assert _render_slot(slot, label) == PRE_CHANGE[job][1] + [label], job
        assert "extra_runner_label" not in hosted, job


def test_slot_renderer_semantics_selfcheck():
    slot = """(inputs.extra_runner_label != '' && fromJSON(format('["a",{0}]', toJSON(inputs.extra_runner_label))) || fromJSON('["b"]'))"""
    assert _render_slot(slot, "x") == ["a", "x"]
    assert _render_slot(slot, "") == ["b"]
    assert _render_slot("""fromJSON('["c"]')""", "x") == ["c"]


def _validation_step():
    raw, _ = _workflow()
    job = raw["jobs"]["classify_pr_paths"]
    assert str(job.get("if")) == "always()"
    step = job["steps"][0]
    assert step["name"] == "Validate extra_runner_label"
    assert step["env"]["EXTRA_RUNNER_LABEL"] == "${{ inputs.extra_runner_label }}"
    return step


def _run_validation(script, label):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=dict(os.environ, EXTRA_RUNNER_LABEL=label))


def test_validation_step_rejects_an_illegal_label_loudly():
    script = _validation_step()["run"]
    for bad in ("Uppercase", "has space", "x" * 65, "label!", "标签", 'a"b', "a\nb"):
        result = _run_validation(script, bad)
        assert result.returncode != 0, bad
        anchors = [ln for ln in result.stdout.splitlines() if ln.startswith("EXTRA-RUNNER-LABEL-INVALID")]
        assert anchors, (bad, result.stdout, result.stderr)


def test_validation_step_accepts_an_empty_or_legal_label():
    script = _validation_step()["run"]
    for good in ("", "canary-abc123-ci-i5", "a", "x" * 64):
        result = _run_validation(script, good)
        assert result.returncode == 0, (good, result.stdout, result.stderr)
        assert "EXTRA-RUNNER-LABEL-INVALID" not in result.stdout
