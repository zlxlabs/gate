"""gate#486: replay the ready_for_review ledger producer and its consumers."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from test_gate_v2_contract import PRIMARY_RESULT_EXPR, _evaluate_ledger_if
from test_gate_v2_quality_reuse import QUALITY_CALLER_CHECKS, QUALITY_PREFLIGHT_RESULT, QUALITY_RESULT
from test_review_ledger import _aggregator
from test_silo_store import _start_record_server

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/review-ledger"
# Issue #486 / run 46129267245: whitelist from the run metadata, Jobs API,
# and classify/resolve logs. The success case varies the primary verdict only.
REPO = "zlxlabs/agent-config"
REPO_ID = 1309811311
PR = 8587
HEAD = "7764e3202a6b6bab55a013701fdc70d9336a677a"
RUN = 46129267245
ATTEMPT = 1
SOURCE_RUN = "46126272164"
TERMINAL = f"d30/{REPO_ID}/gate-terminal-v1-{REPO_ID}-{HEAD}-{RUN}-{ATTEMPT}"
D30_KEY = f"d30/{REPO_ID}/codex-review-ledger-v2-{REPO_ID}-{HEAD}-{RUN}-{ATTEMPT}/ledger.jsonl"


def _render(text, values):
    for expression, value in values.items():
        text = text.replace("${{ " + expression + " }}", str(value))
    assert "${{" not in text, text
    return text


def _bash(step, env, values):
    step_env = {key: _render(value, values) for key, value in step.get("env", {}).items()}
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _render(step["run"], values)],
        env={**env, **step_env}, capture_output=True, timeout=15,
    )


def _outputs(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


@pytest.fixture
def ledger_replay(tmp_path):
    jobs = yaml.safe_load((ROOT / ".github/workflows/gate-v2.yml").read_text())["jobs"]
    steps = {step.get("id", step["name"]): step for step in jobs["ledger"]["steps"]}
    server = _start_record_server(body=(
        "<ListBucketResult><IsTruncated>false</IsTruncated>"
        f"<Contents><Key>{TERMINAL}/gate-terminal.json</Key></Contents></ListBucketResult>"
    ).encode())
    env = {
        "PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ACTION_PATH": str(ACTION), "GITHUB_REPOSITORY": REPO,
        "GITHUB_RUN_ID": str(RUN), "GITHUB_RUN_ATTEMPT": str(ATTEMPT),
        "GITHUB_OUTPUT": str(tmp_path / "resolve.out"),
        "AWS_ACCESS_KEY_ID": "fixture", "AWS_SECRET_ACCESS_KEY": "fixture",
        "SILO_BUCKET": "ci-artifacts", "SILO_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "SILO_EXEC": str(ROOT / "scripts/silo_exec.sh"), "SILO_STORE": str(ROOT / "scripts/silo_store.py"),
    }
    values = {
        "github.token": "fixture", "github.repository": REPO, "github.repository_id": REPO_ID,
        "github.event.pull_request.head.sha": HEAD, "github.event.pull_request.number": PR,
        "github.run_id": RUN, "github.run_attempt": ATTEMPT, "runner.temp": tmp_path,
        jobs["primary"]["if"][4:-3]: "true",  # observed REVIEW_EXPECTED and codex-expected
    }
    try:
        yield jobs, steps, env, values, server
    finally:
        server.shutdown()
        server.server_close()


def _resolve(steps, env, values, *, primary, reusable="true", source=SOURCE_RUN, quality="skipped", upload=""):
    return _bash(steps["resolve-ledger-artifacts"], env, values | {
        "needs.classify_pr_paths.outputs.quality_reusable": reusable,
        "needs.classify_pr_paths.outputs.reuse_source_run_id": source,
        "needs.quality.result": quality,
        "needs.quality.outputs.ledger_input_upload": upload,
        "needs.primary.result": primary,
        PRIMARY_RESULT_EXPR[4:-3]: primary,
        QUALITY_RESULT[4:-3]: "success" if reusable == "true" and quality == "skipped" else quality,
        QUALITY_CALLER_CHECKS[4:-3]: "passed",
        QUALITY_PREFLIGHT_RESULT[4:-3]: "success",
    })


@pytest.mark.parametrize(("primary", "verdict", "audit_fixture"), [
    ("failure", "fail", "primary_security_stage_producer_audit.json"),
    ("success", "pass", "primary_review_v2_with_tier.json"),
])
def test_verified_reuse_writes_current_ledger_bytes(ledger_replay, tmp_path, primary, verdict, audit_fixture):
    jobs, steps, env, values, server = ledger_replay
    resolved = _resolve(steps, env, values, primary=primary)
    assert resolved.returncode == 0, resolved.stderr + resolved.stdout
    output = Path(env["GITHUB_OUTPUT"])
    assert output.read_bytes() == (
        "input_artifact_id=\ninput_source_attempt=\ninput_short_circuited=true\n"
        f"terminal_artifact_id={TERMINAL}\nterminal_source_attempt={ATTEMPT}\nreview_expected=true\n"
    ).encode()
    values |= {f"steps.resolve-ledger-artifacts.outputs.{key}": val for key, val in _outputs(output).items()}

    identity = dict(repository=REPO, run_id=RUN, run_attempt=ATTEMPT, head_sha=HEAD)
    # Keep actual canonical producer fixtures; rebind only the target identity.
    audit = json.loads((ROOT / "tests/data" / audit_fixture).read_bytes())
    audit.update(identity, repository_id=REPO_ID, pr=PR)
    source_bytes = json.dumps(audit, ensure_ascii=False).encode()
    (tmp_path / "primary-review-audit.json").write_bytes(source_bytes)
    publish = next(step for step in jobs["primary"]["steps"] if step.get("id") == "publish-audit-output")
    audit_output = tmp_path / "audit.out"
    published = _bash(publish, env | {"GITHUB_OUTPUT": str(audit_output)}, values)
    assert published.returncode == 0, published.stderr
    values |= {f"needs.primary.outputs.{key}": val for key, val in _outputs(audit_output).items()}
    values |= {"needs.primary.result": primary, PRIMARY_RESULT_EXPR[4:-3]: primary}
    read = _bash(steps["read-primary-audit-for-ledger"], env, values)
    assert read.returncode == 0, read.stderr
    assert (tmp_path / "primary-audit/primary-review-audit.json").read_bytes() == source_bytes

    agg = _aggregator()
    scope = agg.Identity(repository_id=REPO_ID, pr=PR, head_sha=HEAD, run_id=RUN, run_attempt=ATTEMPT)
    gate_facts = dict(quality_result="success", primary_result=primary, runner="self", is_draft=False,
                      review_expected=True, identity=scope)
    decision = agg.evaluate(**gate_facts, is_fork=False, audit=audit, audit_error=None, pr_author="owner")
    terminal = agg.build_terminal_envelope(repository=REPO, **gate_facts, outcome=decision)
    server.objects[f"/ci-artifacts/{TERMINAL}/gate-terminal.json"] = json.dumps(terminal).encode()
    downloaded = _bash(steps["download-gate-terminal-for-ledger"], env, values)
    assert downloaded.returncode == 0, downloaded.stderr

    build = steps["build-review-ledger"]
    assert _evaluate_ledger_if(build["if"], {
        "resolve-ledger-artifacts": "success", "read-primary-audit-for-ledger": "success",
        "download-gate-terminal-for-ledger": "success",
    }, job_success=True)
    values |= {
        "github.event.pull_request.base.sha": audit["base_sha"],
        "github.workflow_sha": audit["caller_sha"], "job.workflow_sha": audit["reusable_workflow_sha"],
        "steps.resolve-ledger-artifacts.outputs.terminal_artifact_id != '' && "
        "format('{0}/gate-terminal/gate-terminal.json', runner.temp) || ''":
            str(tmp_path / "gate-terminal/gate-terminal.json"),
    }
    inputs = {f"inputs.{key}": _render(str(val).lower() if isinstance(val, bool) else val, values)
              for key, val in build["with"].items()}
    action_step = yaml.safe_load((ACTION / "action.yml").read_text())["runs"]["steps"][0]
    built = _bash(action_step, env, values | inputs)
    assert built.returncode == 0, built.stderr
    payload = (tmp_path / "review-ledger/ledger.jsonl").read_bytes()
    # The CLI prints the row unsorted; the on-disk protocol sorts its keys.
    expected_payload = (json.dumps(json.loads(built.stdout), ensure_ascii=False, sort_keys=True) + "\n").encode()
    assert payload == expected_payload
    assert payload.count(b"\n") == 1 and payload.endswith(b"\n")
    row = json.loads(payload)
    assert {key: row[key] for key in identity} == identity
    assert row["pr_number"] == PR
    assert row["review"]["verdict"] == verdict
    assert row["preflight"] is row["install"] is row["review"]["coverage"] is None
    assert row["disposition_receipt_consumption"] == terminal["disposition_receipt_consumption"]
    assert SOURCE_RUN.encode() not in payload

    upload = steps["Upload v2 review effectiveness ledger"]
    assert _evaluate_ledger_if(upload["if"], {"build-review-ledger": "success"}, job_success=True)
    uploaded = _bash(upload, env, values)
    assert uploaded.returncode == 0, uploaded.stderr
    assert [(item["path"], item["body"]) for item in server.received if item["method"] == "PUT"] == [
        (f"/ci-artifacts/{D30_KEY}", payload),
    ]
    assert server.objects[f"/ci-artifacts/{D30_KEY}"] == payload


@pytest.mark.parametrize(("reusable", "source", "quality", "upload"), [
    ("false", SOURCE_RUN, "skipped", ""),
    ("", SOURCE_RUN, "skipped", ""),
    ("true", "", "skipped", ""),
    ("true", SOURCE_RUN, "success", ""),
    ("true", SOURCE_RUN, "skipped", "failure"),
])
def test_unverified_reuse_or_failed_upload_does_not_open_build(ledger_replay, tmp_path, reusable, source, quality, upload):
    _, steps, env, values, _ = ledger_replay
    resolved = _resolve(steps, env, values, primary="success", reusable=reusable, source=source,
                        quality=quality, upload=upload)
    assert resolved.returncode == 1
    assert f"No matching required ledger input artifact found (quality upload outcome: {upload or 'unknown'})".encode() in resolved.stderr
    assert not Path(env["GITHUB_OUTPUT"]).exists()
    assert not _evaluate_ledger_if(steps["build-review-ledger"]["if"], {
        "resolve-ledger-artifacts": "failure", "read-primary-audit-for-ledger": "success",
        "download-gate-terminal-for-ledger": "skipped",
    }, job_success=True)
    assert not (tmp_path / "review-ledger/ledger.jsonl").exists()


def test_verified_reuse_still_requires_current_run_terminal(ledger_replay):
    _, steps, env, values, server = ledger_replay
    server.body = b"<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>"
    resolved = _resolve(steps, env, values, primary="failure")
    assert resolved.returncode == 1
    assert b"No matching required gate terminal artifact found" in resolved.stderr
    assert not Path(env["GITHUB_OUTPUT"]).exists()
