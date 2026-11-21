"""Credential-free replay jobs for behavior_replay counterevidence (gate#404 G2).

Same PyYAML style as test_gate_v2_disposition_source.py: parse
gate-v2-disposition.yml, assert the structural contract of the replay-plan /
replay jobs, and run their inline plan scripts offline against fixtures.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gate-v2-disposition.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
CONVERGENCE_SPARSE = ".github/actions/gate-aggregator/convergence.py"
ARGV_SPARSE = ".github/actions/gate-aggregator/counterevidence_argv.py"
BEHAVIOR_SPARSE = ".github/actions/gate-aggregator/behavior_counterevidence.py"
REPOSITORY = "zlxlabs/repo"

DIFF = (
    "--- a/src/lock.py\n"
    "+++ b/src/lock.py\n"
    "@@ -10,7 +10,7 @@ def locked():\n"
    "-    return True\n"
    "+    return False\n"
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job(name: str) -> dict:
    jobs = _workflow()["jobs"]
    if name not in jobs:
        raise AssertionError(f"workflow has no {name} job")
    return jobs[name]


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def _heredoc_blocks(run_text: str) -> list[str]:
    blocks: list[str] = []
    collecting: str | None = None
    for line in run_text.splitlines():
        if collecting is not None:
            if line == "PY":
                blocks.append(collecting)
                collecting = None
            else:
                collecting += line + "\n"
        elif line.endswith("<<'PY'"):
            collecting = ""
    assert collecting is None, "unterminated python heredoc in run script"
    return blocks


def _run_python(code: str, argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "-", *argv],
        input=code,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _string_values(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [value for key in node for value in _string_values(node[key])]
    if isinstance(node, list):
        return [value for item in node for value in _string_values(item)]
    return []


def _behavior_payload(**overrides) -> dict:
    payload = {
        "kind": "behavior_replay",
        "result": "refuted",
        "pointer": "tests/test_lock.py::test_locked_behavior",
        "finding_id": "p1",
        "primary_run_id": "123",
        "primary_run_attempt": "1",
        "head_sha": "a" * 40,
        "runner": "pytest",
        "nodeid": "tests/test_lock.py::test_locked_behavior",
        "patch": {
            "sha256": hashlib.sha256(DIFF.encode("utf-8")).hexdigest(),
            "byte_len": len(DIFF.encode("utf-8")),
            "unified_diff": DIFF,
        },
    }
    payload.update(overrides)
    return payload


def _pr_json(*, head_full_name: str = REPOSITORY, user_type: str = "User") -> dict:
    return {
        "head": {"sha": "a" * 40, "repo": {"full_name": head_full_name}},
        "user": {"login": "someone", "type": user_type},
    }


def test_replay_jobs_do_not_run_on_git_source_service():
    plan = _job("replay-plan")
    replay = _job("replay")
    assert plan["runs-on"] == ["self-hosted", "linux", "ci"]
    assert "needs.replay-plan.outputs.runs_on" in replay["runs-on"]
    for job in (plan, replay):
        assert "git-source-service" not in " ".join(_string_values(job))


def test_replay_plan_does_not_use_hosted_runner():
    # No caller code runs in replay-plan, so it must stay on the self-hosted
    # ci slot: a hosted-runner quota outage must not stall the literal-grep
    # disposition path (r1).
    assert "ubuntu-latest" not in _job("replay-plan")["runs-on"]


def test_replay_publishes_evidence_artifact_name():
    # gate#433: the name is declared by its producer and handed to the
    # consumer verbatim via the job output; the upload step keeps building
    # it from run_id/run_attempt, and control only ever references the
    # output (see test_control_downloads_replay_artifact_only_on_behavior_path).
    replay = _job("replay")
    upload = _step(replay, "Upload behavior replay evidence")
    assert upload["with"]["name"] == (
        "gate-behavior-replay-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    outputs = replay.get("outputs") or {}
    assert outputs.get("evidence_artifact_name") == upload["with"]["name"]


def _preflight_code() -> str:
    plan = _job("replay-plan")
    return _heredoc_blocks(
        _step(plan, "Preflight canonical quality junit artifact")["run"]
    )[0]


def test_replay_plan_preflight_contract():
    # gate#433: the canonical junit existence check runs in the earliest job,
    # only on the behavior path, with the lookup failure and the missing
    # artifact reported as two distinct errors; guidance text must tell the
    # caller what to change. Existence-only: no nodeid parsing here.
    plan = _job("replay-plan")
    # The artifacts query needs the Actions scope; it is granted at the
    # workflow level (actions: write, mirrored by caller templates) and the
    # job inherits it — a job-level re-declaration would trip the
    # caller-contract guard's expansion check (gate#433). The declaration
    # must stay shape-compatible with the v2 baseline (contents +
    # pull-requests only) and the workflow ceiling must cover actions.
    assert plan["permissions"] == {"contents": "read", "pull-requests": "read"}
    workflow_permissions = _workflow()["permissions"]
    assert workflow_permissions.get("actions") in ("read", "write")
    step = _step(plan, "Preflight canonical quality junit artifact")
    assert step["if"] == "steps.classify.outputs.behavior == 'true'"
    assert step["env"]["GH_REPO"] == "${{ github.repository }}"
    assert step["env"]["PRIMARY_RUN_ID"] == "${{ inputs.primary_run_id }}"
    assert step["env"]["PRIMARY_RUN_ATTEMPT"] == "${{ inputs.primary_run_attempt }}"
    run_text = step["run"]
    assert "behavior_canonical_junit_missing" in run_text
    assert "behavior_canonical_junit_lookup_failed" in run_text
    assert "GATE_ARTIFACT_DIR/junit" in run_text
    assert "nodeid" not in run_text
    # Paged lookup: per_page caps at 100 and stopping after page 1 would
    # report a false missing on artifact-heavy primary runs.
    assert "per_page=100" in run_text
    assert "page=" in run_text


def test_replay_pytest_runs_in_checked_out_worktree():
    # nodeids are relative to the repository root; both pytest calls must run
    # in the checkout directory, not the workspace root (r1).
    replay = _job("replay")
    checkout = next(
        step for step in replay["steps"] if step.get("uses", "").startswith("actions/checkout")
    )
    run_step = _step(replay, "Replay counterevidence nodeid green then red")
    assert run_step.get("working-directory") == checkout["with"]["path"]
    # gate#404: direct pytest invocations are gone; the single remaining
    # `python3 -m pytest` is the fallback branch of the shared runner
    # selection function (see test_replay_pytest_goes_through_shared_runner_selection).
    pytest_lines = [
        line
        for line in run_step["run"].splitlines()
        if "python3 -m pytest" in line and not line.strip().startswith("#")
    ]
    assert len(pytest_lines) == 1


def test_every_gh_run_download_call_carries_repository_flag():
    # gate#404 canary run 43685935055: the control job's workspace root is
    # not a git checkout, so `gh run download` without -R dies with
    # "fatal: not a git repository" on every caller and the behavior path
    # always reports behavior_canonical_junit_missing.  Every call in this
    # workflow must carry -R "$GITHUB_REPOSITORY".
    offenders = [
        line.strip()
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if "gh run download" in line
        and not line.strip().startswith("#")
        and '-R "$GITHUB_REPOSITORY"' not in line
    ]
    assert offenders == []


def test_replay_pytest_goes_through_shared_runner_selection():
    # gate#404 canary run 43685935055: bare `python3 -m pytest` on the ci
    # slot host died with "No module named pytest" for both baseline and
    # red, producing no junit.  Both invocations must route through one
    # runner-selection function with the same ladder as legacy quality's
    # pyproject branch (gate-v2.yml run-tests): uv (--frozen under
    # uv.lock) > uv run > bare pytest > python3 -m pytest.
    run_text = _step(_job("replay"), "Replay counterevidence nodeid green then red")["run"]
    lines = run_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip().endswith("replay_pytest() {"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "}")
    body = "\n".join(lines[start:end])
    outside = "\n".join(lines[:start] + lines[end + 1 :])
    # Four branches, same ladder as legacy quality.
    assert "[ -f pyproject.toml ]" in body
    assert "command -v uv" in body
    assert "[ -f uv.lock ]" in body
    assert "uv run --frozen pytest" in body
    assert re.search(r"^\s*uv run pytest\b", body, re.MULTILINE)
    assert "command -v pytest" in body
    assert re.search(r"^\s*pytest -p no:cacheprovider\b", body, re.MULTILINE)
    assert "python3 -m pytest -p no:cacheprovider" in body
    # Baseline and red share the one function; nothing else invokes pytest.
    calls = [line.strip() for line in lines if line.strip().startswith("replay_pytest ")]
    assert calls == ['replay_pytest "$baseline_xml"', 'replay_pytest "$red_xml"']
    assert not re.search(
        r"(?m)^\s*(?:uv run (?:--frozen )?pytest\b|python3 -m pytest\b|pytest -p no:cacheprovider\b)",
        outside,
    )


def test_replay_jobs_have_no_credentials_or_environment():
    for name in ("replay-plan", "replay"):
        job = _job(name)
        assert "environment" not in job
        assert "secrets." not in " ".join(_string_values(job))
        # Full dump covers run-block text too (e.g. a stray $SILO_EXEC
        # reference inside a script would pass the env-key scan below).
        dump = yaml.dump(job)
        assert "secrets." not in dump
        assert "SILO_" not in dump
        env_keys = list(job.get("env", {}).keys())
        for step in job["steps"]:
            env_keys.extend(step.get("env", {}).keys())
        cred_keys = [key for key in env_keys if key.startswith(("AWS_", "SILO_"))]
        assert not cred_keys


def test_replay_checkout_does_not_persist_credentials():
    replay = _job("replay")
    checkout = next(
        step for step in replay["steps"] if step.get("uses", "").startswith("actions/checkout")
    )
    options = checkout["with"]
    assert options["persist-credentials"] is False
    assert options["ref"] == "${{ needs.replay-plan.outputs.head_sha }}"


def test_control_requires_replay_success_or_skip():
    control = _job("control")
    needs = control["needs"]
    if isinstance(needs, str):
        needs = [needs]
    assert "replay-plan" in needs
    assert "replay" in needs
    condition = control["if"]
    assert "!cancelled()" in condition
    assert "needs.replay.result == 'skipped'" in condition
    assert "needs.replay-plan.result == 'success'" in condition


def test_replay_and_plan_runs_do_not_expand_inputs_or_needs():
    for name in ("replay-plan", "replay"):
        for step in _job(name)["steps"]:
            run_text = step.get("run")
            if run_text is None:
                continue
            assert "${{ inputs." not in run_text
            assert "${{ needs." not in run_text


def test_only_replay_job_uses_upload_artifact():
    # fc3b011 intent, migrated from test_gate_v2_contract.py (file-size ratchet):
    # the disposition receipt leaves via Silo only; the behavior replay evidence
    # job (gate#404) is the sole Actions-artifact user in this workflow.
    raw = _workflow()
    for name, job in raw["jobs"].items():
        if name == "replay":
            continue
        assert "upload-artifact" not in yaml.safe_dump(job), name
    top_level = {key: value for key, value in raw.items() if key != "jobs"}
    assert "upload-artifact" not in yaml.safe_dump(top_level)


def test_control_downloads_replay_artifact_only_on_behavior_path():
    control = _job("control")
    fetch_replay = _step(control, "Fetch behavior replay evidence")
    fetch_head = _step(control, "Fetch caller head for counterevidence rerun")
    assert fetch_replay["if"] == "${{ needs.replay-plan.outputs.behavior == 'true' }}"
    assert fetch_head["if"] == "${{ needs.replay-plan.outputs.behavior != 'true' }}"
    assert str(fetch_replay.get("uses", "")).startswith(
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
    )
    assert fetch_replay["with"]["name"] == (
        "${{ needs.replay.outputs.evidence_artifact_name }}"
    )
    assert fetch_replay["with"]["path"] == "${{ runner.temp }}/behavior-replay"
    # gate#433: the evidence name is the producer's declaration; control must
    # not rebuild it from its own run_attempt (--failed reruns bump only
    # control's attempt while the replay artifact keeps attempt 1).
    assert "gate-behavior-replay-${{ github.run_id }}" not in yaml.safe_dump(control)
    canonical = _step(control, "Fetch canonical quality junit")
    assert canonical["if"] == "${{ needs.replay-plan.outputs.behavior == 'true' }}"
    assert "${{ inputs." not in canonical["run"]
    assert "${{ needs." not in canonical["run"]
    assert "behavior_canonical_junit_missing" in canonical["run"]
    assert 'gh run download -R "$GITHUB_REPOSITORY" "$PRIMARY_RUN_ID"' in canonical["run"]
    assert "gate-quality-junit-${PRIMARY_RUN_ID}-${PRIMARY_RUN_ATTEMPT}" in canonical["run"]


def test_control_sparse_lists_behavior_counterevidence_module():
    listed = _job("control")
    checkout = _step(listed, "Checkout disposition producer")
    sparse = checkout["env"]["GATE_CHECKOUT_SPARSE"]
    assert ".github/actions/gate-aggregator/behavior_counterevidence.py" in sparse


def test_control_new_run_steps_do_not_expand_inputs_or_needs():
    control = _job("control")
    for step in control["steps"]:
        run_text = step.get("run")
        if run_text is None:
            continue
        assert "${{ inputs." not in run_text
        assert "${{ needs." not in run_text


def _classify_code() -> str:
    plan = _job("replay-plan")
    return _heredoc_blocks(_step(plan, "Classify counterevidence as behavior_replay")["run"])[0]


def _guard_code() -> str:
    plan = _job("replay-plan")
    return _heredoc_blocks(_step(plan, "Guard PR identity and pick the runner slot")["run"])[0]


def _classify(
    tmp_path: Path, *, kind: str, counterevidence: str
) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    output = tmp_path / "github-output"
    env = {
        "DISPOSITION_KIND": kind,
        "COUNTEREVIDENCE_JSON": counterevidence,
        "GITHUB_OUTPUT": str(output),
    }
    proc = _run_python(_classify_code(), [], env)
    outputs = {}
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
    return proc, outputs


def test_plan_classifier_flags_behavior_input(tmp_path):
    proc, outputs = _classify(
        tmp_path,
        kind="false-positive",
        counterevidence=json.dumps(_behavior_payload()),
    )
    assert proc.returncode == 0, proc.stderr
    assert outputs["behavior"] == "true"
    assert outputs["head_sha"] == "a" * 40
    assert outputs["nodeid"] == "tests/test_lock.py::test_locked_behavior"


@pytest.mark.parametrize(
    "kind,counterevidence",
    [
        (
            "false-positive",
            json.dumps(
                {
                    "command": "git grep -n -F -e locked-behavior abc -- src/lock.py",
                    "output": "match",
                    "result": "refuted",
                    "pointer": "src/lock.py",
                }
            ),
        ),
        ("deferred", json.dumps(_behavior_payload())),
        ("false-positive", "not json at all {"),
        ("false-positive", ""),
        ("", json.dumps(_behavior_payload())),
    ],
    ids=["literal-grep", "deferred", "unparseable-json", "empty", "no-disposition"],
)
def test_plan_classifier_skips_non_behavior_inputs(tmp_path, kind, counterevidence):
    proc, outputs = _classify(tmp_path, kind=kind, counterevidence=counterevidence)
    assert proc.returncode == 0, proc.stderr
    assert outputs["behavior"] == "false"


def test_plan_classifier_rejects_control_chars_in_nodeid(tmp_path):
    # A newline in nodeid would let a caller forge extra GITHUB_OUTPUT lines
    # (e.g. overwrite head_sha=) because classify writes nodeid raw (r1).
    proc, outputs = _classify(
        tmp_path,
        kind="false-positive",
        counterevidence=json.dumps(
            _behavior_payload(nodeid="tests/a.py::t\nhead_sha=" + "0" * 40)
        ),
    )
    assert proc.returncode != 0
    assert "behavior_replay_input_invalid" in proc.stderr
    output_text = (tmp_path / "github-output").read_text(encoding="utf-8")
    assert "nodeid=" not in output_text
    assert output_text.count("head_sha=") <= 1


@pytest.mark.parametrize(
    "override",
    [
        {"head_sha": "A" * 40},
        {"head_sha": "a" * 39},
        {"nodeid": "tests/test_lock.py"},
        {"nodeid": "-tests/test_lock.py::test_locked_behavior"},
        {"nodeid": ""},
        {"patch": {"sha256": "0" * 64, "byte_len": 10, "unified_diff": DIFF}},
        {"patch": None},
    ],
    ids=[
        "upper-head-sha",
        "short-head-sha",
        "nodeid-no-separator",
        "nodeid-dash",
        "nodeid-empty",
        "sha-mismatch",
        "patch-missing",
    ],
)
def test_plan_classifier_rejects_invalid_shapes(tmp_path, override):
    proc, _ = _classify(
        tmp_path,
        kind="false-positive",
        counterevidence=json.dumps(_behavior_payload(**override)),
    )
    assert proc.returncode != 0
    assert "behavior_replay_input_invalid" in proc.stderr


def _guard(tmp_path: Path, pull: dict) -> subprocess.CompletedProcess:
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(pull), encoding="utf-8")
    env = {
        "EXPECTED_REPOSITORY": REPOSITORY,
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
    }
    return _run_python(_guard_code(), [str(pr_json)], env)


@pytest.mark.parametrize(
    "pull",
    [
        _pr_json(head_full_name="someone/repo"),
        _pr_json(user_type="Bot"),
    ],
    ids=["fork", "bot"],
)
def test_plan_guard_rejects_fork_and_bot(tmp_path, pull):
    proc = _guard(tmp_path, pull)
    assert proc.returncode != 0
    assert "behavior_unreviewed_or_fork" in proc.stderr


def _preflight(
    tmp_path: Path,
    *,
    pages: dict[str, dict],
    gh_rc: int = 0,
    run_id: str = "123",
    attempt: str = "1",
) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    """Drive the replay-plan preflight heredoc against a fake gh.

    The fake gh records argv (one JSON array per call) and serves one JSON
    document per `page=N` query argument; gh_rc != 0 simulates a failing API
    query on stderr. Returns the process and the recorded argv calls.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    pages_path = tmp_path / "gh-pages.json"
    pages_path.write_text(json.dumps(pages), encoding="utf-8")
    argv_log = tmp_path / "gh-argv.log"
    fake = bin_dir / "gh"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"with open({json.dumps(str(argv_log))}, 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"if {gh_rc} != 0:\n"
        "    print('API rate limit exceeded', file=sys.stderr)\n"
        f"    sys.exit({gh_rc})\n"
        "url = next(a for a in sys.argv[1:] if a.startswith('repos/'))\n"
        "query = url.split('?', 1)[1] if '?' in url else ''\n"
        "params = dict(kv.split('=', 1) for kv in query.split('&') if kv)\n"
        f"pages = json.load(open({json.dumps(str(pages_path))}, encoding='utf-8'))\n"
        "print(json.dumps(pages.get(params.get('page', '1'), {'total_count': 0, 'artifacts': []})))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "GH_REPO": REPOSITORY,
        "GH_TOKEN": "fake-token",
        "PRIMARY_RUN_ID": run_id,
        "PRIMARY_RUN_ATTEMPT": attempt,
    }
    proc = _run_python(_preflight_code(), [], env)
    calls = (
        [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
        if argv_log.exists()
        else []
    )
    return proc, calls


def test_preflight_fails_with_guidance_when_junit_absent(tmp_path):
    # gate#433 (a): no junit artifact on the primary run → replay-plan fails
    # here instead of after a full replay job, with actionable guidance.
    pages = {
        "1": {
            "total_count": 1,
            "artifacts": [{"name": "gate-quality-audit-123-1", "expired": False}],
        }
    }
    proc, calls = _preflight(tmp_path, pages=pages)
    assert proc.returncode != 0
    assert "behavior_canonical_junit_missing" in proc.stderr
    assert "GATE_ARTIFACT_DIR/junit" in proc.stderr
    assert calls == [
        ["api", f"repos/{REPOSITORY}/actions/runs/123/artifacts?per_page=100&page=1"]
    ]


def test_preflight_passes_when_junit_present(tmp_path):
    # gate#433 (b): artifact exists → preflight passes, single query.
    pages = {
        "1": {
            "total_count": 1,
            "artifacts": [{"name": "gate-quality-junit-123-1", "expired": False}],
        }
    }
    proc, calls = _preflight(tmp_path, pages=pages)
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 1


def test_preflight_treats_expired_junit_as_missing(tmp_path):
    # An expired artifact cannot be downloaded, so control's authoritative
    # fetch would fail anyway — same as absent, fail here instead.
    pages = {
        "1": {
            "total_count": 1,
            "artifacts": [{"name": "gate-quality-junit-123-1", "expired": True}],
        }
    }
    proc, _ = _preflight(tmp_path, pages=pages)
    assert proc.returncode != 0
    assert "behavior_canonical_junit_missing" in proc.stderr


def test_preflight_reports_lookup_failure_distinct_from_missing(tmp_path):
    # gate#433 (c): the query itself failing is a different error than the
    # artifact being absent — byte-distinct stderr, both fail-loud.
    proc, calls = _preflight(tmp_path, pages={}, gh_rc=1)
    assert proc.returncode != 0
    assert "behavior_canonical_junit_lookup_failed" in proc.stderr
    assert "behavior_canonical_junit_missing" not in proc.stderr
    assert calls


def test_preflight_paginates_beyond_first_hundred_artifacts(tmp_path):
    # per_page caps at 100; stopping after page 1 would report a false
    # missing on artifact-heavy primary runs.
    first = {
        "total_count": 101,
        "artifacts": [{"name": f"other-{i}", "expired": False} for i in range(100)],
    }
    second = {
        "total_count": 101,
        "artifacts": [{"name": "gate-quality-junit-123-1", "expired": False}],
    }
    proc, calls = _preflight(tmp_path, pages={"1": first, "2": second})
    assert proc.returncode == 0, proc.stderr
    assert [call[-1] for call in calls] == [
        f"repos/{REPOSITORY}/actions/runs/123/artifacts?per_page=100&page=1",
        f"repos/{REPOSITORY}/actions/runs/123/artifacts?per_page=100&page=2",
    ]


def test_preflight_rejects_nonnumeric_run_id_as_lookup_failure(tmp_path):
    # A non-numeric run id would reshape the query URL; fail as a lookup
    # failure instead of silently querying a different resource.
    proc, calls = _preflight(tmp_path, pages={}, run_id="123?foo=bar")
    assert proc.returncode != 0
    assert "behavior_canonical_junit_lookup_failed" in proc.stderr
    assert calls == []


def _sparse_env_values(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        value = node.get("GATE_CHECKOUT_SPARSE")
        if isinstance(value, str):
            found.append(value)
        for item in node.values():
            found.extend(_sparse_env_values(item))
    elif isinstance(node, list):
        for item in node:
            found.extend(_sparse_env_values(item))
    return found


def _convergence_sparse_lists() -> list[tuple[str, list[str]]]:
    listed: list[tuple[str, list[str]]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for index, sparse in enumerate(_sparse_env_values(raw)):
            lines = [line.strip() for line in sparse.splitlines() if line.strip()]
            if any(CONVERGENCE_SPARSE in line for line in lines):
                listed.append((f"{path.relative_to(ROOT).as_posix()}#{index}", lines))
    return listed


def test_every_convergence_sparse_lists_argv_and_behavior_modules():
    listed = _convergence_sparse_lists()
    assert listed, "no workflow sparse list names gate-aggregator/convergence.py"
    for label, lines in listed:
        blob = "\n".join(lines)
        assert ARGV_SPARSE in blob, label
        assert BEHAVIOR_SPARSE in blob, label


def _gate_v2_aggregator_sparse() -> list[str]:
    raw = yaml.safe_load((ROOT / ".github/workflows/gate-v2.yml").read_text(encoding="utf-8"))
    for sparse in _sparse_env_values(raw):
        lines = [line.strip() for line in sparse.splitlines() if line.strip()]
        if ARGV_SPARSE in lines and CONVERGENCE_SPARSE in lines:
            return lines
    raise AssertionError("gate-v2.yml has no aggregator sparse list")


def test_gate_v2_aggregator_sparse_import_accepts_behavior_receipt(tmp_path):
    from test_behavior_replay_verdict import _behavior_receipt

    dest = tmp_path / "sparse"
    for rel in _gate_v2_aggregator_sparse():
        src = ROOT / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
    module_path = dest / CONVERGENCE_SPARSE
    spec = importlib.util.spec_from_file_location("gate_convergence_sparse_r1", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    receipt, _, _ = _behavior_receipt()
    assert module._counterevidence_reason(receipt) is None


def test_plan_guard_picks_self_hosted_slot_for_human_pr(tmp_path):
    proc = _guard(tmp_path, _pr_json())
    assert proc.returncode == 0, proc.stderr
    output = tmp_path / "github-output"
    assert output.read_text(encoding="utf-8").strip() == (
        'runs_on=["self-hosted", "linux", "ci"]'
    )
