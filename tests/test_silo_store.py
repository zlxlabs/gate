"""Stubbed-boto3 contracts for scripts/silo_store.py. No disk network, no real S3."""

from __future__ import annotations

import io
import socket
import struct
from pathlib import Path

import pytest

from scripts import silo_store as store


class FakeS3:
    def __init__(self, objects=None, *, list_error=None):
        self.objects = dict(objects or {})
        self.list_error = list_error
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
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey: {Key}")
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
