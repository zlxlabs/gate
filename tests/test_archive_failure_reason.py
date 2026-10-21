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
    assert len(branches) == 13
    assert all(run.count('python3 "${SILO_STORE%/*}/archive_failure_reason.py"') == 1 for run in branches)
    assert "grep -oE 's3_error_code=[A-Za-z_]+'" not in WORKFLOW.read_text(encoding="utf-8")
