import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "scripts/silo_store.py"
PARSER = ROOT / "scripts/archive_failure_reason.py"
WORKFLOW = ROOT / ".github/workflows/gate-v2.yml"


def _parse(data: bytes) -> str:
    result = subprocess.run([sys.executable, str(PARSER)], input=data, capture_output=True, check=True)
    return result.stdout.decode("ascii").strip()


class _Handler(BaseHTTPRequestHandler):
    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = f"<Error><Code>{self.server.code}</Code></Error>".encode()
        self.send_response(self.server.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _producer_stderr(tmp_path: Path, status: int, code: str) -> bytes:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.status, server.code = status, code
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = tmp_path / f"payload-{status}"
    payload.write_bytes(b"archive payload")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "AWS_ACCESS_KEY_ID": "test",
           "AWS_SECRET_ACCESS_KEY": "test", "SILO_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
           "SILO_BUCKET": "ci-artifacts"}
    try:
        result = subprocess.run([sys.executable, str(PRODUCER), "put", "--tier", "d1",
                                 "--repo-id", "1", "--name", "reason-test", "--file", str(payload)],
                               env=env, capture_output=True, check=False)
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode != 0 and b"SILO-REQUEST-FAILED {" in result.stderr
    assert f'"s3_error_code":"{code}"'.encode() in result.stderr
    return result.stderr


def test_real_producer_403_and_500_payloads():
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as temp:
        path = Path(temp)
        for status, code in ((403, "AccessDenied"), (500, "InternalError")):
            assert _parse(_producer_stderr(path, status, code)) == code


def test_missing_legacy_and_unsafe_reasons():
    assert _parse(b'SILO-REQUEST-FAILED {"s3_error_code":null}') == "unclassified"
    assert _parse(b"SILO-REQUEST-FAILED s3_error_code=Foo") == "Foo"
    assert _parse(b'SILO-REQUEST-FAILED {"s3_error_code":"Foo\\n::error::bad"}') == "unclassified"
    safe = _parse(b'SILO-REQUEST-FAILED {"s3_error_code":"AccessDenied"}\nsecret body')
    assert safe == "AccessDenied" and re.fullmatch(r"[A-Za-z_]+", safe)


def test_every_dynamic_deferred_reason_uses_one_parser():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    branches = [step["run"] for job in workflow["jobs"].values() for step in job.get("steps", [])
                if "GATE-ARCHIVE-DEFERRED" in step.get("run", "")
                and "reason=${reason:-unclassified}" in step["run"]]
    assert len(branches) == 20
    assert all(run.count('python3 "${SILO_STORE%/*}/archive_failure_reason.py"') == 1 for run in branches)
    assert "grep -oE 's3_error_code=[A-Za-z_]+'" not in WORKFLOW.read_text(encoding="utf-8")


def test_archive_deferred_reasons_come_from_classifier_not_hardcoded():
    # 归档上传失败分支必须捕获 $SILO_EXEC 输出并交给归类脚本，不再硬编码 put_dir_failed / put_failed。
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "reason=put_dir_failed" not in text
    assert "reason=put_failed" not in text
    workflow = yaml.safe_load(text)
    classified = [step["run"] for job in workflow["jobs"].values() for step in job.get("steps", [])
                  if "archive_failure_reason.py" in step.get("run", "")]
    assert classified
    assert all("reason=${reason:-unclassified}" in run for run in classified)


def test_real_producer_missing_credentials_stderr_maps_to_missing_credentials():
    # 跨边界：喂给归类脚本的必须是 silo_store.py 真实发出的 stderr 字节，不是手写仿制品。
    from tempfile import TemporaryDirectory
    for env_extras, message in (({}, "SILO_ACCESS_KEY 未传入"),
                                ({"AWS_ACCESS_KEY_ID": "test"}, "SILO_SECRET_KEY 未传入")):
        with TemporaryDirectory() as temp:
            payload = Path(temp) / "payload"
            payload.write_bytes(b"archive payload")
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "SILO_BUCKET": "ci-artifacts"}
            env.update(env_extras)
            result = subprocess.run(
                [sys.executable, str(PRODUCER), "put", "--tier", "d1", "--repo-id", "1",
                 "--name", "reason-test", "--file", str(payload)],
                env=env, capture_output=True, check=False)
        assert result.returncode != 0
        assert message.encode("utf-8") in result.stderr
        assert _parse(result.stderr) == "missing_credentials"


def test_real_producer_missing_source_stderr_maps_to_source_file_missing():
    # 跨边界：缺源文件的判定也必须来自 silo_store.py 真实子进程的 stderr 字节；
    # 四条 fail 文案都在 connect() 之前触发，无需网络与凭据。patrol 对 cancelled
    # run 只跳过 source_file_missing，这里落错类会重新开单。
    from tempfile import TemporaryDirectory

    def producer_fail_stderr(argv: list[str]) -> bytes:
        with TemporaryDirectory() as temp:
            payload = Path(temp) / "probe"  # 仅占位，不写入，保持缺失
            result = subprocess.run(
                [sys.executable, str(PRODUCER), *argv],
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                     "SILO_BUCKET": "ci-artifacts"},
                capture_output=True, check=False)
        assert result.returncode != 0
        return result.stderr

    missing = "nonexistent-source-probe"
    cases = (
        (["put", "--tier", "d1", "--repo-id", "1", "--name", "reason-test",
          "--file", missing], "put source is not a file:"),
        (["put", "--tier", "d1", "--repo-id", "1", "--name", "reason-test",
          "--file", missing, "--file", missing + "-2"],
         "put requires at least one existing --file: all sources missing"),
        (["put-dir", "--tier", "d3", "--repo-id", "1", "--name", "reason-test",
          "--dir", missing], "put-dir source is empty or missing:"),
        (["put-review-record", "--file", missing],
         "put-review-record source is not a file:"),
    )
    for argv, message in cases:
        stderr = producer_fail_stderr(argv)
        assert message.encode("utf-8") in stderr
        assert _parse(stderr) == "source_file_missing"
