"""A 段行为契约：判定记录走 primary job outputs（gate / gate#348、#312）。

跨边界测试纪律：primary 的 `audit_json_b64` output 与 gate/ledger 的读取步骤之间是
跨 job 序列化边界——这里的测试用**真实的 canonical audit 样本**
（tests/fixtures/primary-audit-v2-live-sample.json，取自 Silo d14 的 fleet 实际产物）
真实执行 publish / read 两个 run 块，并驱动 aggregate.py 完整判定路径，断言字段逐一可用；
Silo 调用一律打桩（fake silo_exec 注入失败），证明记账失败不改变判定结论。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
LIVE_SAMPLE = REPO_ROOT / "tests" / "fixtures" / "primary-audit-v2-live-sample.json"
AGGREGATE = REPO_ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"


def _load_workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    steps = _load_workflow()["jobs"][job]["steps"]
    return next(step for step in steps if step.get("name") == name)


def _substitute(text: str, **values: str) -> str:
    mapping = {
        "${{ github.repository_id }}": "1011924119",
        "${{ github.event.pull_request.head.sha }}": "c" * 40,
        "${{ github.run_id }}": "38216552886",
        "${{ github.run_attempt }}": "1",
    }
    mapping.update(values)
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text


def _bash_run(run: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", run],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


# ── publish step (producer side of the verdict channel) ─────────────────────


def test_publish_run_block_encodes_live_sample_byte_exact(tmp_path):
    """真实样本 → GITHUB_OUTPUT：base64 单行、字节守卫、attempt/key 声明齐全。"""
    publish = _step("primary", "Publish canonical primary audit to job output")
    audit_path = tmp_path / "primary-review-audit.json"
    audit_path.write_bytes(LIVE_SAMPLE.read_bytes())
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    env = {
        "AUDIT_PATH": str(audit_path),
        "AUDIT_KEY": "primary-audit-v2-1011924119-" + "c" * 40 + "-38216552886-1",
        "MAX_B64_BYTES": "524288",
        "GITHUB_OUTPUT": str(github_output),
    }
    result = _bash_run(_substitute(publish["run"]), env, tmp_path)
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in github_output.read_text().splitlines())
    assert base64.b64decode(outputs["audit_json_b64"]) == LIVE_SAMPLE.read_bytes()
    assert "\n" not in outputs["audit_json_b64"]
    assert outputs["audit_bytes"] == str(LIVE_SAMPLE.stat().st_size)
    assert outputs["audit_attempt"] == "1"
    assert outputs["audit_key"].endswith("-38216552886-1")


def test_publish_run_block_fails_closed_on_missing_and_oversize(tmp_path):
    """判定通道 fail-closed：audit 缺失/空 → 步骤红；超过预算线 → 步骤红。"""
    publish = _step("primary", "Publish canonical primary audit to job output")
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    cases = {
        "missing": None,
        "empty": b"",
    }
    for name, payload in cases.items():
        audit_path = tmp_path / f"audit-{name}.json"
        if payload is not None:
            audit_path.write_bytes(payload)
        env = {
            "AUDIT_PATH": str(audit_path),
            "AUDIT_KEY": "k",
            "MAX_B64_BYTES": "524288",
            "GITHUB_OUTPUT": str(github_output),
        }
        result = _bash_run(_substitute(publish["run"]), env, tmp_path)
        assert result.returncode != 0, name
        assert "missing or empty" in result.stderr
        assert github_output.read_text() == "", name
    # oversize guard: the 1 MiB expression budget is protected before publish
    audit_path = tmp_path / "audit-big.json"
    audit_path.write_bytes(b'{"pad":"<' + b"x" * 700_000 + b'"}')
    env = {
        "AUDIT_PATH": str(audit_path),
        "AUDIT_KEY": "k",
        "MAX_B64_BYTES": "524288",
        "GITHUB_OUTPUT": str(github_output),
    }
    result = _bash_run(_substitute(publish["run"]), env, tmp_path)
    assert result.returncode != 0
    assert "exceeds" in result.stderr


# ── read step (consumer side) × three injected corruptions ──────────────────


def _read_gate_audit(tmp_path: Path, *, b64: str, declared: str) -> subprocess.CompletedProcess:
    reader = _step("gate", "Read canonical primary audit from primary job output")
    env = {
        "AUDIT_B64": b64,
        "AUDIT_BYTES_DECLARED": declared,
        "AUDIT_DIR": str(tmp_path / "primary-audit"),
        "AUDIT_FILENAME": "primary-review-audit.json",
    }
    return _bash_run(_substitute(reader["run"], **{"${{ needs.primary.result }}": "success"}), env, tmp_path)


def test_read_gate_audit_round_trips_live_sample(tmp_path):
    sample = LIVE_SAMPLE.read_bytes()
    result = _read_gate_audit(tmp_path, b64=base64.b64encode(sample).decode(), declared=str(len(sample)))
    assert result.returncode == 0, result.stderr
    written = tmp_path / "primary-audit" / "primary-review-audit.json"
    assert written.read_bytes() == sample


@pytest.mark.parametrize(
    ("corruption", "expect_in_stderr"),
    [
        ("missing", "audit output missing"),
        ("bad_base64", "not valid base64"),
        ("truncated", "truncated or padded"),
    ],
)
def test_read_gate_audit_fail_closed_paths(tmp_path, corruption, expect_in_stderr):
    sample = LIVE_SAMPLE.read_bytes()
    b64_full = base64.b64encode(sample).decode()
    if corruption == "missing":
        b64, declared = "", str(len(sample))
    elif corruption == "bad_base64":
        b64, declared = "!!!not-base64!!!", "123"
    else:
        b64, declared = b64_full[: len(b64_full) // 2], str(len(sample))
    result = _read_gate_audit(tmp_path, b64=b64, declared=declared)
    # exit 0 on purpose: the terminal envelope must still be produced; the
    # fail-closed decision belongs to the aggregator (audit_missing below).
    assert result.returncode == 0, result.stderr + result.stdout
    assert expect_in_stderr in result.stderr
    assert not (tmp_path / "primary-audit" / "primary-review-audit.json").exists()


# ── full path: publish → read → aggregate.py decides from the real sample ───


def _identity_args(audit: dict, audit_dir: Path, tmp_path: Path, source_attempt: str) -> list[str]:
    return [
        "--quality-result", "success",
        "--primary-result", "success",
        "--preflight-result", "success",
        "--runner", "self",
        "--is-draft", "false",
        "--is-fork", "false",
        "--classify-review-expected", "true",
        "--review-expected", "true",
        "--repository-id", str(audit["repository_id"]),
        "--repository", audit["repository"],
        "--head-sha", audit["head_sha"],
        "--run-id", str(audit["run_id"]),
        "--run-attempt", str(audit["run_attempt"]),
        "--pr-number", str(audit["pr"]),
        "--pr-author-json", json.dumps(audit.get("reviewer") or "live-sample-author"),
        "--audit-source-attempt", source_attempt,
        "--audit-artifact-name", f"primary-audit-v2-{audit['repository_id']}-{audit['head_sha']}-{audit['run_id']}-{source_attempt}",
        "--audit-dir", str(audit_dir),
        "--summary-path", str(tmp_path / "summary.md"),
        "--terminal-path", str(tmp_path / "gate-terminal.json"),
    ]


def test_live_sample_travels_publish_read_aggregate_and_passes(tmp_path):
    """E2E：真实样本经 publish→read→aggregate，verdict pass 且字段逐一可用。"""
    audit = json.loads(LIVE_SAMPLE.read_text(encoding="utf-8"))
    sample = LIVE_SAMPLE.read_bytes()
    b64 = base64.b64encode(sample).decode()

    # producer: publish step writes the job output
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    publish = _step("primary", "Publish canonical primary audit to job output")
    env = {
        "AUDIT_PATH": str(tmp_path / "in.json"),
        "AUDIT_KEY": f"primary-audit-v2-{audit['repository_id']}-{audit['head_sha']}-{audit['run_id']}-{audit['run_attempt']}",
        "MAX_B64_BYTES": "524288",
        "GITHUB_OUTPUT": str(github_output),
    }
    (tmp_path / "in.json").write_bytes(sample)
    result = _bash_run(_substitute(publish["run"]), env, tmp_path)
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in github_output.read_text().splitlines())

    # consumer: gate's read step decodes it onto the audit dir
    audit_dir = tmp_path / "primary-audit"
    reader = _step("gate", "Read canonical primary audit from primary job output")
    result = _bash_run(
        _substitute(reader["run"], **{"${{ needs.primary.result }}": "success"}),
        {
            "AUDIT_B64": outputs["audit_json_b64"],
            "AUDIT_BYTES_DECLARED": outputs["audit_bytes"],
            "AUDIT_DIR": str(audit_dir),
            "AUDIT_FILENAME": "primary-review-audit.json",
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stderr

    # decision: the aggregator consumes exactly what traveled the channel
    terminal_path = tmp_path / "gate-terminal.json"
    proc = subprocess.run(
        [sys.executable, str(AGGREGATE), *_identity_args(audit, audit_dir, tmp_path, outputs["audit_attempt"])],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal["gate_result"] == "pass"
    assert terminal["audit"]["available"] is True
    assert terminal["audit"]["source_attempt"] == int(outputs["audit_attempt"])
    assert terminal["audit"]["artifact_name"] == outputs["audit_key"]
    assert terminal["primary_verdict"] == "pass"


def test_live_sample_with_broken_channel_fails_closed(tmp_path):
    """故障注入：output 截断 → aggregate 走 audit_missing → gate unavailable。"""
    audit = json.loads(LIVE_SAMPLE.read_text(encoding="utf-8"))
    sample = LIVE_SAMPLE.read_bytes()
    b64 = base64.b64encode(sample).decode()
    audit_dir = tmp_path / "primary-audit"
    audit_dir.mkdir()
    reader = _step("gate", "Read canonical primary audit from primary job output")
    result = _bash_run(
        _substitute(reader["run"], **{"${{ needs.primary.result }}": "success"}),
        {
            "AUDIT_B64": b64[: len(b64) // 2],  # 截断
            "AUDIT_BYTES_DECLARED": str(len(sample)),
            "AUDIT_DIR": str(audit_dir),
            "AUDIT_FILENAME": "primary-review-audit.json",
        },
        tmp_path,
    )
    assert result.returncode == 0
    assert not (audit_dir / "primary-review-audit.json").exists()
    terminal_path = tmp_path / "gate-terminal.json"
    proc = subprocess.run(
        [sys.executable, str(AGGREGATE), *_identity_args(audit, audit_dir, tmp_path, str(audit["run_attempt"]))],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal["gate_result"] == "unavailable"
    assert terminal["reason_code"] == "audit_missing"


def test_job_audit_mismatch_survives_the_channel_switch(tmp_path):
    """不变式保持：output 里的 audit 说 pass、primary job 却 failure → fail-closed。"""
    audit = json.loads(LIVE_SAMPLE.read_text(encoding="utf-8"))
    sample = LIVE_SAMPLE.read_bytes()
    audit_dir = tmp_path / "primary-audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_bytes(sample)
    terminal_path = tmp_path / "gate-terminal.json"
    args = _identity_args(audit, audit_dir, tmp_path, str(audit["run_attempt"]))
    args[args.index("--primary-result") + 1] = "failure"
    proc = subprocess.run(
        [sys.executable, str(AGGREGATE), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal["gate_result"] == "unavailable"
    assert terminal["reason_code"] == "job_audit_mismatch"


# ── fault injection: Silo fully down, verdict still passes ──────────────────


def _fake_silo(tmp_path: Path) -> Path:
    fake = tmp_path / "silo_exec_fake.sh"
    fake.write_text(
        "#!/bin/bash\n"
        'echo "SILO-REQUEST-FAILED s3_error_code=SlowDown" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


@pytest.mark.parametrize(
    ("job", "step_name", "kind"),
    [
        ("primary", "Upload canonical primary audit", "primary-audit"),
        ("primary", "Upload primary review diagnostics", "primary-diagnostics"),
        ("gate", "Upload convergence receipt", "convergence-receipt"),
        ("gate", "Upload gate terminal envelope", "gate-terminal"),
        ("ledger", "Upload v2 review effectiveness ledger", "review-ledger"),
    ],
)
def test_silo_down_produces_deferred_rows_and_fails_the_step_only(tmp_path, job, step_name, kind):
    """「审阅 pass + 记账步骤失败」→ 步骤红 + GATE-ARCHIVE-DEFERRED；结论由
    continue-on-error 结构保证不变（守卫测试 + job 级语义锁死）。"""
    step = _step(job, step_name)
    fake = _fake_silo(tmp_path)
    source = tmp_path / "source.json"
    source.write_text("{}")
    env = {
        "SILO_EXEC": str(fake),
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "ARTIFACT_NAME": "fake-artifact",
        "REPO_ID": "123",
        "AUDIT_PATH": str(source),
        "DIAGNOSTICS_DIR": str(tmp_path / "diag"),
        "RECEIPT_DIR": str(tmp_path / "receipt"),
        "TERMINAL_PATH": str(source),
        "LEDGER_PATH": str(source),
    }
    (tmp_path / "diag").mkdir()
    (tmp_path / "receipt").mkdir()
    result = _bash_run(_substitute(step["run"]), env, tmp_path)
    assert result.returncode != 0, f"{step_name} must fail the step itself"
    assert f"GATE-ARCHIVE-DEFERRED kind={kind} " in result.stdout
    assert f"key=" in result.stdout
    assert "reason=SlowDown" in result.stdout
    assert "SILO-REQUEST-FAILED" in result.stderr  # 底层原因不被吞


def test_ledger_audit_read_degrades_for_skipped_primary(tmp_path):
    """primary skipped（draft/fork/豁免）→ output 合法缺失 → row 字段 null，不红。"""
    step = _step("ledger", "Read canonical primary audit from primary job output for ledger")
    env = {
        "PRIMARY_RESULT_RAW": "skipped",
        "PRIMARY_RESULT": "skipped",
        "AUDIT_B64": "",
        "AUDIT_BYTES_DECLARED": "",
        "AUDIT_DIR": str(tmp_path / "primary-audit"),
        "AUDIT_FILENAME": "primary-review-audit.json",
    }
    result = _bash_run(step["run"], env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "audit fields will be null" in result.stdout


def test_ledger_audit_read_rejects_corrupt_payload_for_reviewed_run(tmp_path):
    """reviewed run + 坏 payload → fail-loud（与改造前 required resolve 等价）。"""
    sample = LIVE_SAMPLE.read_bytes()
    step = _step("ledger", "Read canonical primary audit from primary job output for ledger")
    env = {
        "PRIMARY_RESULT_RAW": "success",
        "PRIMARY_RESULT": "success",
        "AUDIT_B64": base64.b64encode(sample).decode()[:20],  # 截断
        "AUDIT_BYTES_DECLARED": str(len(sample)),
        "AUDIT_DIR": str(tmp_path / "primary-audit"),
        "AUDIT_FILENAME": "primary-review-audit.json",
    }
    result = _bash_run(step["run"], env, tmp_path)
    assert result.returncode != 0
    assert "truncated" in result.stderr
