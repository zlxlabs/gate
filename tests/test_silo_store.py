"""Stubbed-boto3 contracts for scripts/silo_store.py. No disk network, no real S3."""

from __future__ import annotations

import io
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
    monkeypatch.setattr(store, "connect", lambda: fake)


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


def test_missing_access_key_stderr(monkeypatch, capsys):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    with pytest.raises(SystemExit) as caught:
        store.connect()
    assert caught.value.code == store.EXIT_ERROR
    assert "SILO_ACCESS_KEY 未传入" in capsys.readouterr().err


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


def test_magicdns_prints_ip_or_tailnet_error(monkeypatch, capsys):
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
    assert "tailnet 不可达" in err
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
