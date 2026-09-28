"""Fake-S3 contracts for the stdlib client in scripts/silo_store.py. No network, no real S3."""

from __future__ import annotations

import io
import json
import socket
import struct
from pathlib import Path

import pytest

from scripts import silo_store as store


class FakeClientError(Exception):
    def __init__(self, code: str, message: str = "Client error", key: str = ""):
        super().__init__(f"{code}: {message}")
        self.response = {"Error": {"Code": code, "Message": message, "Key": key}}


class FakeS3:
    def __init__(self, objects=None, *, list_error=None, get_error=None):
        self.objects = dict(objects or {})
        self.list_error = list_error
        self.get_error = get_error
        self.puts: list[str] = []

    def put_object(self, *, Bucket, Key, Body):
        if hasattr(Body, "read"):
            Body = Body.read()
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        self.objects[Key] = bytes(Body)
        self.puts.append(Key)
        return {}

    def get_object(self, *, Bucket, Key):
        if self.get_error is not None:
            if callable(self.get_error):
                raise self.get_error(Key)
            raise self.get_error
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey", f"Key {Key} not found", key=Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        if self.list_error is not None:
            raise self.list_error
        contents = [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)]
        return {"Contents": contents, "IsTruncated": False, "KeyCount": len(contents)}


def _env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setenv("SILO_BUCKET", "ci-artifacts")


def _use(monkeypatch, fake: FakeS3):
    _env(monkeypatch)
    monkeypatch.setattr(store, "connect", lambda *, managed_profile=False: fake)


def test_build_key_joins_tier_repo_name_and_relative_path():
    assert store.build_key("d14", "99", "primary-audit-v2-99-abc-7-1", "primary-review-audit.json") == (
        "d14/99/primary-audit-v2-99-abc-7-1/primary-review-audit.json"
    )
    assert store.build_key("d3", "1", "advisory-event-ocr-1-2", "advisory/event.json") == (
        "d3/1/advisory-event-ocr-1-2/advisory/event.json"
    )
    with pytest.raises(SystemExit) as caught:
        store.build_key("d7", "1", "name", "file.json")
    assert caught.value.code == store.EXIT_ERROR
    with pytest.raises(SystemExit):
        store.build_key("d1", "1", "name", "../escape")


def test_select_attempt_picks_max_at_or_below_current():
    names = [
        "primary-audit-v2-1-sha-9-1",
        "primary-audit-v2-1-sha-9-2",
        "primary-audit-v2-1-sha-9-4",
        "other-1",
    ]
    prefix = "primary-audit-v2-1-sha-9-"
    assert store.select_attempt(names, prefix, 3) == ("primary-audit-v2-1-sha-9-2", 2)
    assert store.select_attempt(names, prefix, 4) == ("primary-audit-v2-1-sha-9-4", 4)
    assert store.select_attempt(["gate-terminal-v1-1-sha-9-3"], "gate-terminal-v1-1-sha-9-", 2) is None


def test_put_get_and_put_dir(tmp_path, monkeypatch):
    fake = FakeS3()
    _use(monkeypatch, fake)
    source = tmp_path / "primary-review-audit.json"
    source.write_bytes(b'{"ok":true}')
    extra = tmp_path / "install-result.json"
    extra.write_text("{}", encoding="utf-8")
    assert store.main(["put", "--tier", "d14", "--repo-id", "42", "--name", "primary-audit-v2-42-deadbeef-99-1", "--file", str(source)]) == 0
    assert fake.puts == ["d14/42/primary-audit-v2-42-deadbeef-99-1/primary-review-audit.json"]
    fake.puts.clear()
    assert store.main(["put", "--tier", "d1", "--repo-id", "7", "--name", "review-ledger-input-v2-7-sha-1-1", "--file", str(source), "--file", str(extra)]) == 0
    assert fake.puts == [
        "d1/7/review-ledger-input-v2-7-sha-1-1/primary-review-audit.json",
        "d1/7/review-ledger-input-v2-7-sha-1-1/install-result.json",
    ]
    root = tmp_path / "diagnostics"
    (root / "nested").mkdir(parents=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "nested" / "raw.txt").write_bytes(b"x")
    fake.puts.clear()
    assert store.main(["put-dir", "--tier", "d3", "--repo-id", "8", "--name", "primary-review-diagnostics-v2-8-sha-1-1", "--dir", str(root)]) == 0
    assert set(fake.puts) == {
        "d3/8/primary-review-diagnostics-v2-8-sha-1-1/manifest.json",
        "d3/8/primary-review-diagnostics-v2-8-sha-1-1/nested/raw.txt",
    }
    dest = tmp_path / "out"
    assert store.main(["get", "--prefix", "d14/42/primary-audit-v2-42-deadbeef-99-1", "--dest", str(dest)]) == 0
    assert (dest / "primary-review-audit.json").read_bytes() == b'{"ok":true}'


def test_put_dir_empty_skip_prints_notice(tmp_path, monkeypatch, capsys):
    _use(monkeypatch, FakeS3())
    rc = store.main(["put-dir", "--tier", "d3", "--repo-id", "8", "--name", "primary-review-diagnostics-v2-8-sha-1-1", "--dir", str(tmp_path / "nope"), "--empty", "skip"])
    assert rc == 0
    assert "::notice::silo put-dir skipped" in capsys.readouterr().out


def test_put_single_file_missing_fails(tmp_path, monkeypatch, capsys):
    _use(monkeypatch, FakeS3())
    missing = tmp_path / "absent.json"
    with pytest.raises(SystemExit) as caught:
        store.main(["put", "--tier", "d1", "--repo-id", "7", "--name", "single-test", "--file", str(missing)])
    assert caught.value.code == store.EXIT_ERROR
    assert f"put source is not a file: {missing}" in capsys.readouterr().err


def test_put_multi_file_partial_missing_uploads_existing_and_logs_skip(tmp_path, monkeypatch, capsys):
    fake = FakeS3()
    _use(monkeypatch, fake)
    existing = tmp_path / "pr-size-preflight.json"
    existing.write_bytes(b'{"size": 42}')
    missing = tmp_path / "install-result.json"
    rc = store.main([
        "put", "--tier", "d1", "--repo-id", "7", "--name", "ledger-input",
        "--file", str(existing), "--file", str(missing),
    ])
    assert rc == 0
    assert fake.puts == ["d1/7/ledger-input/pr-size-preflight.json"]
    err = capsys.readouterr().err
    assert f"put skipping missing source file: {missing}" in err


def test_put_multi_file_all_missing_fails(tmp_path, monkeypatch, capsys):
    fake = FakeS3()
    _use(monkeypatch, fake)
    missing1 = tmp_path / "preflight.json"
    missing2 = tmp_path / "install.json"
    with pytest.raises(SystemExit) as caught:
        store.main([
            "put", "--tier", "d1", "--repo-id", "7", "--name", "ledger-input",
            "--file", str(missing1), "--file", str(missing2),
        ])
    assert caught.value.code == store.EXIT_ERROR
    assert fake.puts == []
    err = capsys.readouterr().err
    assert f"put skipping missing source file: {missing1}" in err
    assert f"put skipping missing source file: {missing2}" in err
    assert "all sources missing" in err


def test_resolve_prints_selected_prefix_and_exit_codes(monkeypatch, capsys, tmp_path):
    objects = {
        "d14/5/primary-audit-v2-5-sha-11-1/primary-review-audit.json": b"a",
        "d14/5/primary-audit-v2-5-sha-11-2/primary-review-audit.json": b"b",
        "d14/5/primary-audit-v2-5-sha-11-4/primary-review-audit.json": b"c",
    }
    _use(monkeypatch, FakeS3(objects))
    assert store.main(["resolve", "--tier", "d14", "--repo-id", "5", "--name-prefix", "primary-audit-v2-5-sha-11-", "--attempt", "3"]) == 0
    assert capsys.readouterr().out.strip() == "d14/5/primary-audit-v2-5-sha-11-2\t2\tprimary-audit-v2-5-sha-11-2"
    _use(monkeypatch, FakeS3({"d14/5/primary-audit-v2-5-sha-11-4/file.json": b"x"}))
    with pytest.raises(SystemExit) as missed:
        store.main(["resolve", "--tier", "d14", "--repo-id", "5", "--name-prefix", "primary-audit-v2-5-sha-11-", "--attempt", "3"])
    assert missed.value.code == store.EXIT_NOT_FOUND
    assert missed.value.code != store.EXIT_ERROR
    _use(monkeypatch, FakeS3(list_error=RuntimeError("connection refused")))
    with pytest.raises(SystemExit) as failed:
        store.main(["resolve", "--tier", "d14", "--repo-id", "5", "--name-prefix", "primary-audit-v2-5-sha-11-", "--attempt", "1"])
    assert failed.value.code == store.EXIT_ERROR
    _use(monkeypatch, FakeS3())
    with pytest.raises(SystemExit) as empty:
        store.main(["get", "--prefix", "d14/1/missing", "--dest", str(tmp_path)])
    assert empty.value.code == store.EXIT_NOT_FOUND


# Copied from B1 canary run 35082772768 on silo/ci-artifacts (mcli ls --recursive).
CANARY_TERMINAL_KEY = (
    "d30/1327629472/gate-terminal-v1-1327629472-"
    "2cdbc1bd4719665b7ba7548ba04edea73ec65ffc-35082772768-1/gate-terminal.json"
)
CANARY_AUDIT_KEY = (
    "d14/1327629472/primary-audit-v2-1327629472-"
    "2cdbc1bd4719665b7ba7548ba04edea73ec65ffc-35082772768-1/primary-review-audit.json"
)
CANARY_RECEIPT_NAME = (
    "gate-disposition-receipt-v2-"
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-0123456789ab-finding1"
)
CANARY_RECEIPT_KEY = f"d30/1327629472/{CANARY_RECEIPT_NAME}/{CANARY_RECEIPT_NAME}"


def test_list_prints_keys_under_name_prefix_and_optional_dest(tmp_path, monkeypatch, capsys):
    objects = {
        CANARY_TERMINAL_KEY: b'{"kind":"gate_terminal"}',
        CANARY_AUDIT_KEY: b'{"kind":"primary_review"}',
        "d30/1327629472/codex-review-ledger-v2-1327629472-dead-1/ledger.jsonl": b"{}",
        CANARY_RECEIPT_KEY: b'{"kind":"gate-disposition-receipt-v2"}',
    }
    _use(monkeypatch, FakeS3(objects))
    assert store.main([
        "list", "--tier", "d30", "--repo-id", "1327629472",
        "--name-prefix", "gate-terminal-v1-1327629472-",
    ]) == 0
    assert capsys.readouterr().out.splitlines() == [CANARY_TERMINAL_KEY]

    dest = tmp_path / "out"
    assert store.main([
        "list", "--prefix", "d30/1327629472/gate-disposition-receipt-v2-", "--dest", str(dest),
    ]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed == [CANARY_RECEIPT_KEY]
    saved = dest / CANARY_RECEIPT_NAME / CANARY_RECEIPT_NAME
    assert saved.read_bytes() == b'{"kind":"gate-disposition-receipt-v2"}'

    _use(monkeypatch, FakeS3(objects))
    assert store.main(["list", "--prefix", "d30/1327629472/missing-prefix-"]) == 0
    assert capsys.readouterr().out == ""

    _use(monkeypatch, FakeS3(list_error=RuntimeError("connection refused")))
    with pytest.raises(SystemExit) as failed:
        store.main(["list", "--prefix", "d30/1327629472/"])
    assert failed.value.code == store.EXIT_ERROR


def test_missing_access_key_stderr(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    profile_path = tmp_path / "silo.json"
    profile_path.write_text('{"access_key_id":"profile","secret_access_key":"profile"}')
    monkeypatch.setattr(store, "MANAGED_PROFILE_PATH", profile_path)
    with pytest.raises(SystemExit) as caught:
        store.connect()
    assert caught.value.code == store.EXIT_ERROR
    assert "SILO_ACCESS_KEY 未传入" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("profile_text", "error"),
    [
        (None, FileNotFoundError),
        ("not-json", json.JSONDecodeError),
        (json.dumps({"access_key_id": "one"}), KeyError),
        (json.dumps({"access_key_id": "", "secret_access_key": "secret"}), SystemExit),
    ],
)
def test_managed_profile_fails_without_falling_back_to_environment(monkeypatch, tmp_path, profile_text, error):
    _env(monkeypatch)
    profile_path = tmp_path / "silo.json"
    if profile_text is not None:
        profile_path.write_text(profile_text)
    monkeypatch.setattr(store, "MANAGED_PROFILE_PATH", profile_path)
    monkeypatch.setattr(store, "S3Client", lambda *args, **kwargs: pytest.fail("credential fallback"))
    with pytest.raises(error):
        store.connect(managed_profile=True)
    with pytest.raises(error):
        store.main(["--managed-profile", "put-dir", "--tier", "d3", "--repo-id", "8", "--name", "diagnostics", "--dir", str(tmp_path / "empty"), "--empty", "skip"])


def test_legacy_profile_presence_does_not_replace_environment(monkeypatch, tmp_path):
    _env(monkeypatch)
    profile_path = tmp_path / "silo.json"
    profile_path.write_text('{"access_key_id":"profile-access","secret_access_key":"profile-secret"}')
    monkeypatch.setattr(store, "MANAGED_PROFILE_PATH", profile_path)
    captured = {}
    monkeypatch.setattr(
        store,
        "S3Client",
        lambda endpoint, access, secret, **kwargs: captured.update(
            endpoint=endpoint, access=access, secret=secret, **kwargs
        ),
    )
    store.connect()
    assert captured["access"] == "test-access"
    assert captured["secret"] == "test-secret"


class _FakeDNS:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.packet = b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, timeout):
        return None

    def sendto(self, packet, address):
        self.packet = packet
        self.address = address
        if self.fail:
            raise OSError("Network is unreachable")

    def recvfrom(self, size):
        header = self.packet[:2] + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0)
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton("100.64.1.2")
        return header + self.packet[12:] + answer, ("100.100.100.100", 53)


def test_magicdns_prints_ip_or_lookup_error(monkeypatch, capsys):
    dns = _FakeDNS()
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: dns)
    assert store.main(["magicdns", "--endpoint", "https://zlx-vm-work-i5-infra.taile9071.ts.net:9000", "--nameserver", "100.100.100.100"]) == 0
    assert capsys.readouterr().out.strip() == "100.64.1.2 zlx-vm-work-i5-infra.taile9071.ts.net"
    assert dns.address == ("100.100.100.100", 53)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: _FakeDNS(fail=True))
    with pytest.raises(SystemExit) as caught:
        store.main(["magicdns", "--endpoint", "https://zlx-vm-work-i5-infra.taile9071.ts.net:9000", "--nameserver", "100.100.100.100"])
    assert caught.value.code == store.EXIT_ERROR
    err = capsys.readouterr().err
    assert "Silo MagicDNS lookup failed" in err
    assert "SILO_ENDPOINT=https://zlx-vm-work-i5-infra.taile9071.ts.net:9000" in err
    assert "100.100.100.100" in err


@pytest.mark.parametrize(
    ("code", "expected_rc"),
    [
        ("NoSuchKey", store.EXIT_NOT_FOUND),
        ("404", store.EXIT_NOT_FOUND),
        ("NoSuchBucket", store.EXIT_NOT_FOUND),
        ("AccessDenied", store.EXIT_ERROR),
        ("InternalError", store.EXIT_ERROR),
    ],
)
def test_get_client_error_mapping_contract(monkeypatch, capsys, tmp_path, code, expected_rc):
    fake = FakeS3(get_error=FakeClientError(code, f"Mocked {code}"))
    _use(monkeypatch, fake)
    target_key = "d14/42/primary-audit-v2-42-sha-1-1/primary-review-audit.json"
    with pytest.raises(SystemExit) as caught:
        store.main(["get", "--key", target_key, "--dest", str(tmp_path / "out.json")])
    assert caught.value.code == expected_rc
    err = capsys.readouterr().err
    if expected_rc == store.EXIT_NOT_FOUND:
        assert f"Silo object not found: {target_key}" in err
    else:
        assert f"({code}): {target_key}" in err


@pytest.mark.parametrize(
    ("code", "expected_rc"),
    [
        ("NoSuchKey", store.EXIT_NOT_FOUND),
        ("404", store.EXIT_NOT_FOUND),
        ("NoSuchBucket", store.EXIT_NOT_FOUND),
        ("AccessDenied", store.EXIT_ERROR),
    ],
)
def test_list_download_client_error_mapping_contract(monkeypatch, capsys, tmp_path, code, expected_rc):
    target_key = "d30/1327629472/gate-disposition-receipt-v2-xyz/gate-disposition-receipt-v2-xyz"
    objects = {target_key: b"{}"}
    fake = FakeS3(objects, get_error=FakeClientError(code, f"Mocked {code}"))
    _use(monkeypatch, fake)

    # list without --dest does not download and succeeds with exit 0
    assert store.main(["list", "--prefix", "d30/1327629472/gate-disposition-receipt-v2-"]) == 0
    assert target_key in capsys.readouterr().out

    # list with --dest triggers download and maps ClientError
    dest = tmp_path / "dest"
    with pytest.raises(SystemExit) as caught:
        store.main(["list", "--prefix", "d30/1327629472/gate-disposition-receipt-v2-", "--dest", str(dest)])
    assert caught.value.code == expected_rc
    err = capsys.readouterr().err
    if expected_rc == store.EXIT_NOT_FOUND:
        assert f"Silo object not found: {target_key}" in err
    else:
        assert f"({code}): {target_key}" in err


def test_get_non_structured_client_error_fails_loud(monkeypatch, capsys, tmp_path):
    class FakeLegacyClientError(Exception):
        pass  # Class name or str contains ClientError/NoSuchKey, but no structured .response dict

    fake = FakeS3(get_error=FakeLegacyClientError("ClientError: NoSuchKey happened"))
    _use(monkeypatch, fake)
    target_key = "d14/42/name/file.json"
    with pytest.raises(SystemExit) as caught:
        store.main(["get", "--key", target_key, "--dest", str(tmp_path / "out.json")])
    assert caught.value.code == store.EXIT_ERROR
    assert caught.value.code != store.EXIT_NOT_FOUND


# SigV4 KAT. Expected signatures cross-checked against botocore 1.34.46
# SigV4Auth (frozen 20150830T123600Z, AKIDEXAMPLE / wJalr...EXAMPLEKEY).
_KAT_ACCESS = "AKIDEXAMPLE"
_KAT_SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
_KAT_DATE = "20150830T123600Z"
_KAT_STAMP = "20150830"
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _kat_auth(method, uri, qs, headers, payload_hash, region, service):
    return store.sigv4_authorization(
        method=method, canonical_uri=uri, canonical_query_string=qs,
        signed_headers=headers, payload_hash=payload_hash, access_key=_KAT_ACCESS,
        secret_key=_KAT_SECRET, region=region, service=service,
        amz_date=_KAT_DATE, date_stamp=_KAT_STAMP,
    )


def test_sigv4_get_matches_cross_checked_vector():
    assert _kat_auth("GET", "/", "Action=ListUsers&Version=2010-05-08",
                     [("host", "iam.amazonaws.com"), ("x-amz-date", _KAT_DATE)],
                     _EMPTY_SHA256, "us-east-1", "iam") == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/iam/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=b2e4af44cfad96d9ffa3c5653674a927b9b0995c33de22e1f843745ce37c1d5e"
    )


def test_sigv4_put_canonical_request_and_signature():
    payload_hash = store.sha256_hex(b"hello world")
    assert payload_hash == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    headers = [("host", "silo.example.test:9000"), ("x-amz-date", _KAT_DATE)]
    canonical_request, signed_names = store.build_canonical_request(
        "PUT", "/ci-artifacts/d1/7/n/f.json", "", headers, payload_hash)
    assert canonical_request == (
        "PUT\n/ci-artifacts/d1/7/n/f.json\n\n"
        "host:silo.example.test:9000\nx-amz-date:20150830T123600Z\n\n"
        "host;x-amz-date\n"
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )
    assert signed_names == "host;x-amz-date"
    assert _kat_auth("PUT", "/ci-artifacts/d1/7/n/f.json", "", headers,
                     payload_hash, "us-east-1", "s3") == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=c1b27c3fb1304c88aefa3e40ccc7b84d5ea8c5d836afe90fecb5eb4e49521d3f"
    )


def test_sigv4_list_query_vector():
    assert _kat_auth("GET", "/ci-artifacts/", "list-type=2&prefix=d14%2F5%2F",
                     [("host", "silo.example.test:9000"), ("x-amz-date", _KAT_DATE)],
                     _EMPTY_SHA256, "us-east-1", "s3") == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=0c63b10a42106ba5cc619f256141ca5f5734d6b5bdb363fb719203aa03a8768e"
    )


def test_encode_s3_path_is_path_style():
    assert store.encode_s3_path("ci-artifacts", "d1/7/n/f.json") == "/ci-artifacts/d1/7/n/f.json"
    assert store.encode_s3_path("ci-artifacts", "d1/7/n/a b.json") == "/ci-artifacts/d1/7/n/a%20b.json"


def test_s3_client_rejects_non_http_endpoint():
    with pytest.raises(SystemExit) as caught:
        store.S3Client("ftp://silo.example.test:9000", "ak", "sk")
    assert caught.value.code == store.EXIT_ERROR


def test_s3_client_endpoint_error_hides_userinfo(capsys):
    with pytest.raises(SystemExit) as caught:
        store.S3Client("ftp://USER_MARKER:PASS_MARKER@minio.invalid:9000", "ak", "sk")
    assert caught.value.code == store.EXIT_ERROR
    err = capsys.readouterr().err
    assert "USER_MARKER" not in err
    assert "PASS_MARKER" not in err
    assert "ftp://minio.invalid:9000" in err


# Fake in-process S3 over stdlib http.server: proves path-style SigV4-signed
# requests, real XML list parsing with ContinuationToken pagination, and the
# 404-maps-to-miss / other-status-is-loud contract. No production Silo.
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlparse, urlsplit


class _FakeS3Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/xml"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append({
            "method": self.command, "path": self.path,
            "host": self.headers.get("Host") or "",
            "authorization": self.headers.get("Authorization") or "",
            "amz_date": self.headers.get("x-amz-date") or "",
            "payload_hash": self.headers.get("x-amz-content-sha256") or "",
        })
        parts = urlparse(self.path).path.strip("/").split("/", 1)
        bucket, key = (parts + [""])[:2]
        if bucket != self.server.bucket:
            self._send(404, b"<Error><Code>NoSuchBucket</Code><Message>no bucket</Message></Error>")
        elif self.command == "PUT" and key:
            self.server.objects[unquote(key)] = body
            self._send(200, b"", content_type="text/plain")
        elif self.command == "GET" and not key:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            assert query.get("list-type") == ["2"], self.path
            encoding_type = (query.get("encoding-type") or [""])[0]
            names = sorted(k for k in self.server.objects if k.startswith((query.get("prefix") or [""])[0]))
            page = names[1:] if "continuation-token" in query else names[:1]
            truncated = "continuation-token" not in query and len(names) > 1
            if encoding_type == "url":
                page = [quote(k, safe="/-_.~") for k in page]
            items = "".join(f"<Contents><Key>{k}</Key><Size>1</Size></Contents>" for k in page)
            token = "<NextContinuationToken>tok1</NextContinuationToken>" if truncated else ""
            encoding = "<EncodingType>url</EncodingType>" if encoding_type == "url" else ""
            self._send(200, (f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                             f"{encoding}<IsTruncated>{str(truncated).lower()}</IsTruncated>{items}{token}"
                             "</ListBucketResult>").encode())
        elif self.command == "GET" and key == "d1/7/n/boom.json":
            self._send(500, b"<Error><Code>InternalError</Code><Message>boom</Message></Error>")
        elif self.command == "GET" and key in self.server.objects:
            self._send(200, self.server.objects[key], content_type="application/octet-stream")
        elif self.command == "GET":
            self._send(404, b"<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>")
        else:
            self._send(400, b"<Error><Code>BadRequest</Code><Message>bad</Message></Error>")

    do_GET = _route
    do_PUT = _route

    def log_message(self, *args):
        pass


def _live_client(monkeypatch, objects=None):
    server = HTTPServer(("127.0.0.1", 0), _FakeS3Handler)
    server.bucket = "ci-artifacts"
    server.objects = dict(objects or {})
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.daemon = True
    thread.start()
    return server, store.S3Client(f"http://127.0.0.1:{server.server_port}", "test-access", "sk")


def test_managed_profile_reaches_all_five_commands_and_signs_profile_identity(monkeypatch, tmp_path):
    seed = "d1/7/seed/file.txt"
    server, _ = _live_client(monkeypatch, {seed: b"seed-bytes"})
    try:
        _env(monkeypatch)
        monkeypatch.setenv("SILO_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "environment-access")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "environment-secret")
        assert store.MANAGED_PROFILE_PATH == Path("/opt/review-auth/silo.json")
        profile_path = tmp_path / "silo.json"
        profile_path.write_text(json.dumps({"access_key_id": "profile-access", "secret_access_key": "profile-secret"}))
        monkeypatch.setattr(store, "MANAGED_PROFILE_PATH", profile_path)
        profile_reads = []
        read_text = Path.read_text

        def track_profile_read(path, *args, **kwargs):
            if path == profile_path:
                profile_reads.append(path)
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", track_profile_read)
        single = tmp_path / "single.txt"
        single.write_bytes(b"put-bytes")
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "nested.txt").write_bytes(b"dir-bytes")

        commands = [
            ["put", "--tier", "d1", "--repo-id", "7", "--name", "new", "--file", str(single)],
            ["put-dir", "--tier", "d1", "--repo-id", "7", "--name", "tree", "--dir", str(tree)],
            ["get", "--key", seed, "--dest", str(tmp_path / "get.txt")],
            ["list", "--prefix", "d1/7/seed/", "--dest", str(tmp_path / "listed")],
            ["resolve", "--tier", "d1", "--repo-id", "7", "--name-prefix", "artifact-run-", "--attempt", "1"],
        ]
        server.objects["d1/7/artifact-run-1/file.txt"] = b"resolve-bytes"
        for command in commands:
            assert store.main(["--managed-profile", *command]) == store.EXIT_OK

        empty_skip = ["put-dir", "--tier", "d3", "--repo-id", "8", "--name", "empty", "--dir", str(tmp_path / "empty"), "--empty", "skip"]
        requests_before_skip = len(server.requests)
        assert store.main(["--managed-profile", *empty_skip]) == store.EXIT_OK
        assert len(profile_reads) == len(commands) + 1
        assert len(server.requests) == requests_before_skip

        assert server.objects["d1/7/new/single.txt"] == b"put-bytes"
        assert server.objects["d1/7/tree/nested.txt"] == b"dir-bytes"
        assert (tmp_path / "get.txt").read_bytes() == b"seed-bytes"
        assert (tmp_path / "listed/seed/file.txt").read_bytes() == b"seed-bytes"
        assert server.requests
        for request in server.requests:
            auth = request["authorization"]
            parsed = urlsplit(request["path"])
            expected = store.sigv4_authorization(
                method=request["method"],
                canonical_uri=parsed.path,
                canonical_query_string=store.encode_query_string(parse_qsl(parsed.query, keep_blank_values=True)),
                signed_headers=[("host", request["host"]), ("x-amz-content-sha256", request["payload_hash"]), ("x-amz-date", request["amz_date"])],
                payload_hash=request["payload_hash"],
                access_key="profile-access",
                secret_key="profile-secret",
                region=store.S3_REGION,
                service=store.S3_SERVICE,
                amz_date=request["amz_date"],
                date_stamp=request["amz_date"][:8],
            )
            assert auth == expected
    finally:
        server.shutdown()
        server.server_close()


def test_live_fake_s3_put_get_list_pagination_and_errors(monkeypatch, tmp_path):
    server, client = _live_client(monkeypatch)
    try:
        _env(monkeypatch)
        monkeypatch.setattr(store, "connect", lambda *, managed_profile=False: client)
        (tmp_path / "a.json").write_bytes(b'{"n":1}')
        (tmp_path / "b.json").write_bytes(b'{"n":2}')
        put_args = ["put", "--tier", "d14", "--repo-id", "42",
                    "--name", "primary-audit-v2-42-deadbeef-99-1"]
        assert store.main(put_args + ["--file", str(tmp_path / "a.json")]) == 0
        assert store.main(put_args + ["--file", str(tmp_path / "b.json")]) == 0
        dest = tmp_path / "out"
        assert store.main(["get", "--prefix", "d14/42/primary-audit-v2-42-deadbeef-99-1",
                           "--dest", str(dest)]) == 0
        assert (dest / "a.json").read_bytes() == b'{"n":1}'
        assert (dest / "b.json").read_bytes() == b'{"n":2}'
        # fake S3 pages one key at a time; client must follow tok1
        assert store.list_keys(client, "ci-artifacts", "d14/42/") == [
            "d14/42/primary-audit-v2-42-deadbeef-99-1/a.json",
            "d14/42/primary-audit-v2-42-deadbeef-99-1/b.json",
        ]
        assert any("continuation-token=tok1" in r["path"] for r in server.requests)
        for record in server.requests:
            assert record["path"].startswith("/ci-artifacts/"), record["path"]
            assert record["authorization"].startswith("AWS4-HMAC-SHA256 Credential="), record
            assert "/us-east-1/s3/aws4_request" in record["authorization"], record
            assert record["amz_date"] and record["payload_hash"], record
        with pytest.raises(SystemExit) as missed:
            store.main(["get", "--key", "d1/7/n/missing.json", "--dest", str(tmp_path / "o.json")])
        assert missed.value.code == store.EXIT_NOT_FOUND
        with pytest.raises(SystemExit) as failed:
            store.main(["get", "--key", "d1/7/n/boom.json", "--dest", str(tmp_path / "o.json")])
        assert failed.value.code == store.EXIT_ERROR
    finally:
        server.shutdown()
        server.server_close()


def test_live_fake_s3_put_list_round_trips_literal_percent_escape(monkeypatch, tmp_path, capsys):
    server, client = _live_client(monkeypatch)
    try:
        _env(monkeypatch)
        monkeypatch.setattr(store, "connect", lambda *, managed_profile=False: client)
        source = tmp_path / "100%2Fdone.json"
        source.write_bytes(b'{"done":true}')
        name = "primary-audit-v2-42-deadbeef-99-1"
        expected_key = f"d14/42/{name}/100%2Fdone.json"

        assert store.main([
            "put", "--tier", "d14", "--repo-id", "42", "--name", name, "--file", str(source),
        ]) == store.EXIT_OK
        assert capsys.readouterr().out.splitlines() == [expected_key]

        assert store.main(["list", "--prefix", f"d14/42/{name}/"]) == store.EXIT_OK
        assert capsys.readouterr().out.splitlines() == [expected_key]
        list_requests = [r["path"] for r in server.requests if r["method"] == "GET" and "list-type=2" in r["path"]]
        assert list_requests
        assert all("encoding-type=url" in request for request in list_requests)
    finally:
        server.shutdown()
        server.server_close()
