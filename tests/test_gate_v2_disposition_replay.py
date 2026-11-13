"""Credential-free replay jobs for behavior_replay counterevidence (gate#404 G2).

Same PyYAML style as test_gate_v2_disposition_source.py: parse
gate-v2-disposition.yml, assert the structural contract of the replay-plan /
replay jobs, and run their inline plan scripts offline against fixtures.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gate-v2-disposition.yml"
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


def test_replay_pytest_runs_in_checked_out_worktree():
    # nodeids are relative to the repository root; both pytest calls must run
    # in the checkout directory, not the workspace root (r1).
    replay = _job("replay")
    checkout = next(
        step for step in replay["steps"] if step.get("uses", "").startswith("actions/checkout")
    )
    run_step = _step(replay, "Replay counterevidence nodeid green then red")
    assert run_step.get("working-directory") == checkout["with"]["path"]
    assert run_step["run"].count("python3 -m pytest") == 2


def test_replay_jobs_have_no_credentials_or_environment():
    for name in ("replay-plan", "replay"):
        job = _job(name)
        assert "environment" not in job
        assert "secrets." not in " ".join(_string_values(job))
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


def test_plan_guard_picks_self_hosted_slot_for_human_pr(tmp_path):
    proc = _guard(tmp_path, _pr_json())
    assert proc.returncode == 0, proc.stderr
    output = tmp_path / "github-output"
    assert output.read_text(encoding="utf-8").strip() == (
        'runs_on=["self-hosted", "linux", "ci"]'
    )
