"""G2：raw evidence 一律脱敏后上传（design C 段，gate#348）。

跨边界测试：实际执行 workflow YAML 里该步骤的 run 脚本（打桩 $SILO_EXEC 为
记录参数并拷贝 --dir 内容的假命令），断言被上传目录的真实字节。
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"
STEP_NAME = "Archive raw primary review evidence permanently"

REPO_ID = "1326603956"
HEAD_SHA = "a" * 40
RUN_ID = "32446501755"
RUN_ATTEMPT = "1"
ARTIFACT_NAME = f"primary-review-raw-evidence-v1-{REPO_ID}-{HEAD_SHA}-{RUN_ID}-{RUN_ATTEMPT}"

SUBSTITUTIONS = {
    "${{ github.repository_id }}": REPO_ID,
    "${{ github.event.pull_request.head.sha }}": HEAD_SHA,
    "${{ github.run_id }}": RUN_ID,
    "${{ github.run_attempt }}": RUN_ATTEMPT,
}


def _step():
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["primary"]["steps"]
    step = next(s for s in steps if s.get("name") == STEP_NAME)
    return step


def _run_script():
    run = _step()["run"]
    for expression, replacement in SUBSTITUTIONS.items():
        run = run.replace(expression, replacement)
    return run


def _install_silo_src(cwd: Path, *, broken: bool = False):
    target = cwd / "_gate-silo-src" / "scripts" / "scrub_outbound.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    if broken:
        target.write_bytes(b"this is not valid python !!!\n", )
    else:
        target.write_bytes((REPO_ROOT / "scripts" / "scrub_outbound.py").read_bytes())


def _install_fake_silo_exec(cwd: Path):
    fake = cwd / "fake-silo-exec.sh"
    args_log = cwd / "silo-args.log"
    dest = cwd / "uploaded"
    fake.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f'printf \'%s\\n\' "$*" >> "{args_log}"\n'
        'dir=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --dir) dir="$2"; shift 2;;\n'
        '    *) shift;;\n'
        '  esac\n'
        'done\n'
        f'mkdir -p "{dest}"\n'
        'cp -a "$dir/." "' + str(dest) + '/"\n',
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake, args_log, dest


def _run_archive(tmp_path: Path, files: dict[str, bytes], *, broken_scrub: bool = False):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for relative, payload in files.items():
        target = evidence_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    original = {
        p.relative_to(evidence_dir).as_posix(): p.read_bytes()
        for p in evidence_dir.rglob("*") if p.is_file()
    }
    _install_silo_src(tmp_path, broken=broken_scrub)
    fake, args_log, dest = _install_fake_silo_exec(tmp_path)
    env = {
        **os.environ,
        "PATH": os.environ["PATH"],
        "PRIMARY_RAW_EVIDENCE_DIR": str(evidence_dir),
        "RUNNER_TEMP": str(tmp_path),
        "SILO_EXEC": str(fake),
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "ARTIFACT_NAME": ARTIFACT_NAME,
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _run_script()],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    uploaded = {}
    if dest.exists():
        uploaded = {
            p.relative_to(dest).as_posix(): p.read_bytes()
            for p in dest.rglob("*") if p.is_file()
        }
    called = args_log.exists() and args_log.read_text(encoding="utf-8").strip() != ""
    after = {
        p.relative_to(evidence_dir).as_posix(): p.read_bytes()
        for p in evidence_dir.rglob("*") if p.is_file()
    }
    return result, uploaded, called, original, after, args_log


TOKEN_HUNK = (
    b"diff --git a/x b/x\n"
    b"@@ -10,7 +10,7 @@ token: synthetic-owner-token-abcdefghijklmnop123456\n"
    b"+added line\n"
)


def test_token_hunk_is_scrubbed_before_upload(tmp_path):
    files = {"review.diff": TOKEN_HUNK, "clean.json": b'{"ok": true}\n'}
    result, uploaded, called, original, after, _ = _run_archive(tmp_path, files)
    assert result.returncode == 0, result.stderr + result.stdout
    assert called, "scrubbed copy must reach put-dir"
    assert "raw-evidence-scrubbed categories=TOKEN files=1/2" in result.stdout
    assert set(uploaded) == {"review.diff", "clean.json"}
    assert b"[REDACTED:TOKEN]" in uploaded["review.diff"]
    assert b"synthetic-owner-token" not in uploaded["review.diff"]
    assert uploaded["clean.json"] == b'{"ok": true}\n'
    assert after == original, "original evidence directory must not be modified"
    assert b"synthetic-owner-token" in after["review.diff"]


def test_clean_files_upload_byte_identical(tmp_path):
    files = {"a.txt": b"hello world\n", "sub/b.txt": b'{"ok": true}\n'}
    result, uploaded, called, original, after, _ = _run_archive(tmp_path, files)
    assert result.returncode == 0, result.stderr + result.stdout
    assert called
    assert "raw-evidence-scrubbed categories=none files=0/2" in result.stdout
    assert uploaded == original
    assert after == original


def test_non_utf8_file_fails_loud_without_upload(tmp_path):
    files = {"good.txt": b"hello\n", "bad.bin": b"producer bytes\n\xff\xfe"}
    result, uploaded, called, original, after, _ = _run_archive(tmp_path, files)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "reason=non_utf8_file" in result.stdout + result.stderr
    assert "GATE-ARCHIVE-DEFERRED kind=primary-raw-evidence" in result.stdout + result.stderr
    assert not called, "unscrubbed bytes must never reach SILO_EXEC"
    assert uploaded == {}
    assert after == original


def test_unloadable_scrubber_fails_loud_without_upload(tmp_path):
    files = {"a.txt": b"hello world\n"}
    result, uploaded, called, original, after, _ = _run_archive(
        tmp_path, files, broken_scrub=True
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "reason=scrub_failed" in result.stdout + result.stderr
    assert "GATE-ARCHIVE-DEFERRED kind=primary-raw-evidence" in result.stdout + result.stderr
    assert not called, "unscrubbed bytes must never reach SILO_EXEC"
    assert uploaded == {}
    assert after == original
