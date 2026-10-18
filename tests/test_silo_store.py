"""Fake-S3 contracts for the stdlib client in scripts/silo_store.py. No network, no real S3."""

from __future__ import annotations

import io
import socket
import struct
from pathlib import Path

import pytest
import yaml

from scripts import silo_store as store


class FakeClientError(Exception):
    def __init__(self, code: str, message: str = "Client error", key: str = ""):
        super().__init__(f"{code}: {message}")
        self.response = {"Error": {"Code": code, "Message": message, "Key": key}}


class FakeS3:
    def __init__(self, objects=None, *, list_error=None, get_error=None, get_transform=None):
        self.objects = dict(objects or {})
        self.list_error = list_error
        self.get_error = get_error
        self.get_transform = get_transform
        self.puts: list[str] = []
        self.gets: list[str] = []

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
        self.gets.append(Key)
        body = self.objects[Key]
        if self.get_transform is not None:
            body = self.get_transform(Key, body)
        return {"Body": io.BytesIO(body)}

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


def test_archive_key_allows_only_per_run_review_evidence_pairs():
    repo_id = "1326603956"
    head_sha = "a" * 40
    run_id = "32446501755"
    allowed = (
        (f"codex-review-ledger-v2-{repo_id}-{head_sha}-{run_id}-1", "ledger.jsonl"),
        (f"primary-audit-v2-{repo_id}-{head_sha}-{run_id}-1", "primary-review-audit.json"),
        (f"gate-terminal-v1-{repo_id}-{head_sha}-{run_id}-1", "gate-terminal.json"),
        (f"primary-review-diagnostics-v2-{repo_id}-{head_sha}-{run_id}-1", "manifest.json"),
        (f"primary-review-raw-evidence-v1-{repo_id}-{head_sha}-{run_id}-1", "invocation.json"),
        ("advisory-event-claude-glm-5-3-32446501755-1", "nested/advisory-event.json"),
        (f"gate-convergence-receipt-v1-{repo_id}-{head_sha}-{run_id}-1", "convergence-receipt.json"),
        (f"gate-status-panel-delivery-v1-{repo_id}-{head_sha}-{run_id}-1", "gate-status-panel-delivery.json"),
    )
    for artifact_name, relative_path in allowed:
        assert store.build_key("archive", repo_id, artifact_name, relative_path) == (
            f"archive/{repo_id}/{artifact_name}/{relative_path}"
        )

    rejected = (
        (allowed[0][0], "primary-review-audit.json"),
        (allowed[1][0], "ledger.jsonl"),
        (allowed[2][0], "primary-review-audit.json"),
        (f"primary-audit-v2-999-{head_sha}-{run_id}-1", "primary-review-audit.json"),
        (allowed[0][0], "nested/ledger.jsonl"),
        (allowed[7][0], "nested/gate-status-panel-delivery.json"),
        ("advisory-event-__none__-32446501755-1", "advisory/event.json"),
    )
    for artifact_name, relative_path in rejected:
        with pytest.raises(SystemExit) as caught:
            store.build_key("archive", repo_id, artifact_name, relative_path)
        assert caught.value.code == store.EXIT_ERROR


def test_archive_tier_rejects_unknown_directory_and_multi_file_writes(tmp_path, monkeypatch, capsys):
    fake = FakeS3()
    _use(monkeypatch, fake)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(b'{"run_id":1}\n')
    extra = tmp_path / "extra.json"
    extra.write_bytes(b"{}")
    artifact_name = f"codex-review-ledger-v2-1326603956-{'a' * 40}-1-1"

    with pytest.raises(SystemExit) as multi_file:
        store.main([
            "put", "--tier", "archive", "--repo-id", "1326603956", "--name", artifact_name,
            "--file", str(ledger), "--file", str(extra),
        ])
    assert multi_file.value.code == store.EXIT_ERROR
    assert "archive tier put requires exactly one allowlisted review evidence file" in capsys.readouterr().err

    with pytest.raises(SystemExit) as directory:
        store.main([
            "put-dir", "--tier", "archive", "--repo-id", "1326603956", "--name", "unlisted-artifact",
            "--dir", str(tmp_path),
        ])
    assert directory.value.code == store.EXIT_ERROR
    assert "archive tier put-dir only accepts" in capsys.readouterr().err
    assert fake.puts == []

    with pytest.raises(SystemExit) as direct_directory_file:
        store.main(["put", "--tier", "archive", "--repo-id", "1326603956", "--name", f"primary-review-diagnostics-v2-1326603956-{'a' * 40}-1-1", "--file", str(ledger)])
    assert direct_directory_file.value.code == store.EXIT_ERROR
    assert "does not accept files from directory artifacts" in capsys.readouterr().err
    assert fake.puts == []


def test_archive_directory_rejects_manifest_collision_and_existing_destination_before_put(
    tmp_path, monkeypatch, capsys,
):
    repo_id = "1326603956"
    name = f"gate-convergence-receipt-v1-{repo_id}-{'a' * 40}-32446501755-1"
    source = tmp_path / "receipt"
    source.mkdir()
    (source / "convergence-receipt.json").write_bytes(b"receipt")
    (source / store.ARCHIVE_MANIFEST_FILENAME).write_bytes(b"collision")
    fake = FakeS3()
    _use(monkeypatch, fake)
    with pytest.raises(SystemExit) as collision:
        store.main(["put-dir", "--tier", "archive", "--repo-id", repo_id, "--name", name, "--dir", str(source)])
    assert collision.value.code == store.EXIT_ERROR
    assert "collides with generated" in capsys.readouterr().err
    assert fake.puts == []

    (source / store.ARCHIVE_MANIFEST_FILENAME).unlink()
    occupied_key = f"archive/{repo_id}/{name}/old-object.json"
    fake.objects[occupied_key] = b"pre-existing"
    with pytest.raises(SystemExit) as occupied:
        store.main(["put-dir", "--tier", "archive", "--repo-id", repo_id, "--name", name, "--dir", str(source)])
    assert occupied.value.code == store.EXIT_ERROR
    assert "already exists; refusing to overwrite" in capsys.readouterr().err
    assert fake.puts == []
    assert fake.objects[occupied_key] == b"pre-existing"


def test_archive_primary_diagnostics_requires_complete_original_manifest(tmp_path, monkeypatch, capsys):
    repo_id = "1326603956"
    name = f"primary-review-diagnostics-v2-{repo_id}-{'a' * 40}-32446501755-1"
    source = tmp_path / "diagnostics"
    source.mkdir()
    (source / "manifest.json").write_text('{"schema_version":1,"attempt":1,"file_count":0,"files":[]}\n')
    raw = source / "attempt-1-codex-sub-raw-output.txt"
    raw.write_bytes(b"raw")
    fake = FakeS3()
    _use(monkeypatch, fake)
    with pytest.raises(SystemExit) as incomplete:
        store.main(["put-dir", "--tier", "archive", "--repo-id", repo_id, "--name", name, "--dir", str(source)])
    assert incomplete.value.code == store.EXIT_ERROR
    assert "raw output inventory differs" in capsys.readouterr().err
    assert fake.puts == []


@pytest.mark.parametrize("artifact_class", ["primary", "advisory", "convergence"])
def test_archive_empty_directory_semantics_match_each_producer(tmp_path, monkeypatch, capsys, artifact_class):
    repo_id = "1326603956"
    if artifact_class == "primary":
        name = f"primary-review-diagnostics-v2-{repo_id}-{'a' * 40}-32446501755-1"
        empty = tmp_path / "primary"
        assert store.main([
            "put-dir", "--tier", "archive", "--repo-id", repo_id,
            "--name", name, "--dir", str(empty), "--empty", "skip",
        ]) == store.EXIT_OK
        assert "::notice::silo put-dir skipped" in capsys.readouterr().out
    else:
        name = (
            f"advisory-event-claude-32446501755-1" if artifact_class == "advisory"
            else f"gate-convergence-receipt-v1-{repo_id}-{'a' * 40}-32446501755-1"
        )
        empty = tmp_path / artifact_class
        with pytest.raises(SystemExit) as missing:
            store.main(["put-dir", "--tier", "archive", "--repo-id", repo_id, "--name", name, "--dir", str(empty)])
        assert missing.value.code == store.EXIT_ERROR
        assert "empty or missing" in capsys.readouterr().err


def test_archive_readback_mismatch_fails_after_put_and_does_not_retry(tmp_path, monkeypatch, capsys):
    repo_id = "1326603956"
    name = f"gate-status-panel-delivery-v1-{repo_id}-{'a' * 40}-32446501755-1"
    panel = tmp_path / "gate-status-panel-delivery.json"
    panel.write_bytes(b"original panel")
    fake = FakeS3(get_transform=lambda _key, _body: b"changed panel")
    _use(monkeypatch, fake)
    with pytest.raises(SystemExit) as mismatch:
        store.main(["put", "--tier", "archive", "--repo-id", repo_id, "--name", name, "--file", str(panel)])
    assert mismatch.value.code == store.EXIT_ERROR
    assert "readback hash mismatch" in capsys.readouterr().err
    key = f"archive/{repo_id}/{name}/gate-status-panel-delivery.json"
    assert fake.puts == fake.gets == [key]


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
from urllib.parse import parse_qs, quote, unquote, urlparse


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


def test_live_fake_s3_put_get_list_pagination_and_errors(monkeypatch, tmp_path):
    server, client = _live_client(monkeypatch)
    try:
        _env(monkeypatch)
        monkeypatch.setattr(store, "connect", lambda: client)
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
        monkeypatch.setattr(store, "connect", lambda: client)
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


import hashlib
import http.client
import json
import os
import subprocess
import sys

_STORE_PY = Path(__file__).resolve().parents[1] / "scripts" / "silo_store.py"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DIAG = "SILO-REQUEST-FAILED "
_ACCESS, _SECRET = "AKIA_SENTINEL_ACCESS_NOT_FOR_LOG", "wJalr_SENTINEL_SECRET_NOT_FOR_LOG"
_BODY_S, _MSG_S = "BODY_SENTINEL_PAYLOAD_NOT_FOR_LOG", "MSG_SENTINEL_XML_NOT_FOR_LOG"
_HTML_S = "<html>HTML_SENTINEL_NOT_FOR_LOG</html>"
_EVENT_KEYS = frozenset({
    "event", "schema", "method", "phase", "body_len", "declared_content_length",
    "request_send_completed", "elapsed_ms", "client_timeout_s", "http_status",
    "s3_error_code", "exception_type", "resp_server", "resp_amz_request_id",
    "resp_amz_id_2", "resp_content_length", "request_seq",
})
_INCOMPLETE_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>IncompleteBody</Code>'
    b"<Message>" + _MSG_S.encode() + b" missing bytes</Message>"
    b"<Resource>/ci-artifacts/d1/1/diag/secret.bin</Resource></Error>"
)
_WL = (("Server", "MinIO"), ("x-amz-request-id", "RID-WHITELIST"), ("x-amz-id-2", "ID2-WHITELIST"))


def _events(stderr: str) -> list[dict]:
    return [json.loads(line[len(_DIAG):]) for line in stderr.splitlines() if line.startswith(_DIAG)]


def _run_store(args: list[str], endpoint: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "AWS_ACCESS_KEY_ID": _ACCESS, "AWS_SECRET_ACCESS_KEY": _SECRET,
        "SILO_ENDPOINT": endpoint, "SILO_BUCKET": "ci-artifacts",
    }
    return subprocess.run(
        [sys.executable, str(_STORE_PY), *args], capture_output=True, text=True, env=env, check=False,
    )


def _put(endpoint: str, path: Path) -> subprocess.CompletedProcess:
    return _run_store(["put", "--tier", "d1", "--repo-id", "1", "--name", "diag", "--file", str(path)], endpoint)


def _get(endpoint: str, dest: Path) -> subprocess.CompletedProcess:
    return _run_store(["get", "--key", "d1/1/diag/x.bin", "--dest", str(dest)], endpoint)


def _assert_redacted(text: str) -> None:
    for sentinel in (_ACCESS, _SECRET, _BODY_S, _MSG_S, _HTML_S, "AWS4-HMAC"):
        assert sentinel not in text, sentinel


def _assert_shape(event: dict) -> None:
    assert set(event) == _EVENT_KEYS
    assert event["event"] == "silo_request_failed" and event["schema"] == 1
    assert isinstance(event["elapsed_ms"], int) and event["elapsed_ms"] >= 0
    assert event["client_timeout_s"] == store.S3_TIMEOUT_SECONDS
    assert isinstance(event["request_seq"], int) and event["request_seq"] >= 1
    for key in ("resp_server", "resp_amz_request_id", "resp_amz_id_2", "resp_content_length", "s3_error_code"):
        value = event[key]
        if value is not None:
            assert isinstance(value, str) and len(value) <= 128
            assert all(32 <= ord(ch) <= 126 for ch in value)


class _RecordHandler(BaseHTTPRequestHandler):
    def _handle(self):
        raw_cl = self.headers.get("Content-Length")
        n = int(raw_cl) if raw_cl else 0
        body = self.rfile.read(n) if n else b""
        self.server.received.append({
            "method": self.command,
            "path": self.path,
            "content_length_header": raw_cl,
            "body": body,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_len": len(body),
        })
        if self.server.close_after_read:
            self.close_connection = True
            self.connection.close()
            return
        if self.command == "PUT" and self.server.status < 400:
            self.server.objects[self.path] = body
        is_listing = self.command == "GET" and "list-type=2" in self.path
        status = 200 if is_listing else (
            self.server.get_status if self.command == "GET" and self.server.get_status else self.server.status
        )
        payload = self.server.body
        if self.command == "GET":
            payload = self.server.objects.get(self.path, payload)
        if is_listing:
            payload = self.server.body
        if self.command == "GET" and self.server.corrupt_get and self.path in self.server.objects:
            payload += b"readback-mismatch"
        self.server.responded.append({"method": self.command, "path": self.path, "status": status, "body": payload})
        self.send_response_only(status)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in self.server.extra_headers:
            self.send_header(name, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)
    do_GET = _handle
    do_PUT = _handle
    def log_message(self, *args):
        pass


def _start_record_server(**attrs):
    server = HTTPServer(("127.0.0.1", 0), _RecordHandler)
    server.received, server.responded, server.status, server.body = [], [], 200, b""
    server.objects, server.get_status, server.corrupt_get = {}, None, False
    server.extra_headers, server.close_after_read = (), False
    for key, value in attrs.items():
        setattr(server, key, value)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.daemon = True
    thread.start()
    return server


def _run_gate_silo_step(
    step: dict,
    *,
    tmp_path: Path,
    server: HTTPServer,
    artifact_prefix: str,
    repo_id: str,
    source_env_name: str,
    source_path: Path,
) -> subprocess.CompletedProcess:
    step_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "AWS_ACCESS_KEY_ID": _ACCESS,
        "AWS_SECRET_ACCESS_KEY": _SECRET,
        "SILO_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "SILO_BUCKET": "ci-artifacts",
        "SILO_EXEC": str(_REPO_ROOT / "scripts/silo_exec.sh"),
        "SILO_STORE": str(_STORE_PY),
        "RUNNER_TEMP": str(tmp_path),
        "ARTIFACT_PREFIX": artifact_prefix,
        "ARTIFACT_NAME": artifact_prefix.rsplit("/", 1)[-1],
        "REPO_ID": repo_id,
        "ARCHIVE_READBACK_PATH": str(tmp_path / f"{source_env_name}-archive-readback.json"),
        source_env_name: str(source_path),
    }
    run = step["run"].replace("${{ github.repository_id }}", repo_id)
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", run],
        capture_output=True,
        text=True,
        env=step_env,
        check=False,
    )


def test_gate_workflow_dual_writes_the_real_ledger_bytes_to_d30_and_archive(tmp_path):
    workflow = yaml.safe_load((_REPO_ROOT / ".github/workflows/gate-v2.yml").read_text())
    steps = workflow["jobs"]["ledger"]["steps"]
    d30_step = next(step for step in steps if step.get("name") == "Upload v2 review effectiveness ledger")
    archive_steps = [
        step for step in steps if step.get("name") == "Upload v2 raw review ledger to permanent archive"
    ]
    assert len(archive_steps) == 1, "the ledger workflow must have one permanent raw ledger writer"
    archive_step = archive_steps[0]

    audit_path = _REPO_ROOT / "tests/data/primary_review_v2_with_tier.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    ledger_path = tmp_path / "review-ledger/ledger.jsonl"
    ledger_path.parent.mkdir(parents=True)
    producer = _REPO_ROOT / ".github/actions/review-ledger/build_ledger.py"
    producer_args = [
        sys.executable, str(producer),
        "--audit-path", str(audit_path),
        "--preflight-path", str(_REPO_ROOT / "tests/fixtures/quality-ledger-input/pr-size-preflight.json"),
        "--install-path", str(_REPO_ROOT / "tests/fixtures/quality-ledger-input/install-result.json"),
        "--output", str(ledger_path),
        "--repository", audit["repository"],
        "--pr-number", str(audit["pr"]),
        "--run-id", str(audit["run_id"]),
        "--run-attempt", str(audit["run_attempt"]),
        "--head-sha", audit["head_sha"],
        "--expected-repository-id", str(audit["repository_id"]),
        "--expected-base-sha", audit["base_sha"],
        "--expected-caller-sha", audit["caller_sha"],
        "--expected-reusable-workflow-sha", audit["reusable_workflow_sha"],
        "--codex-expected", "true",
        "--codex-waived", "false",
        "--input-short-circuited", "false",
    ]
    subprocess.run(producer_args, check=True, capture_output=True, text=True)
    ledger_bytes = ledger_path.read_bytes()

    repo_id = str(audit["repository_id"])
    artifact_name = (
        f"codex-review-ledger-v2-{repo_id}-{audit['head_sha']}-"
        f"{audit['run_id']}-{audit['run_attempt']}"
    )
    server = _start_record_server()
    upload_results = []
    try:
        for step in (d30_step, archive_step):
            step_env = {
                "ARTIFACT_NAME": step["env"]["ARTIFACT_NAME"]
                    .replace("${{ github.repository_id }}", repo_id)
                    .replace("${{ github.event.pull_request.head.sha }}", audit["head_sha"])
                    .replace("${{ github.run_id }}", str(audit["run_id"]))
                    .replace("${{ github.run_attempt }}", str(audit["run_attempt"])),
                "LEDGER_PATH": str(ledger_path),
                "ARCHIVE_READBACK_PATH": str(tmp_path / "review-ledger/archive-readback.jsonl"),
            }
            run = step["run"].replace("${{ github.repository_id }}", repo_id)
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "AWS_ACCESS_KEY_ID": _ACCESS,
                "AWS_SECRET_ACCESS_KEY": _SECRET,
                "SILO_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
                "SILO_BUCKET": "ci-artifacts",
                "SILO_EXEC": str(_REPO_ROOT / "scripts/silo_exec.sh"),
                "SILO_STORE": str(_STORE_PY),
                "RUNNER_TEMP": str(tmp_path),
                **step_env,
            }
            upload_results.append(
                subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", run],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
            )
        assert [result.returncode for result in upload_results] == [0, 0]
        assert (tmp_path / "review-ledger/archive-readback.jsonl").read_bytes() == ledger_bytes
        archive_run = ["bash", "-euo", "pipefail", "-c", run]
        for status, corrupt, message in ((403, False, "failed"), (200, True, "bytes do not match")):
            server.get_status, server.corrupt_get = status, corrupt
            failed = subprocess.run(archive_run, capture_output=True, text=True, env=env, check=False)
            assert failed.returncode != 0 and f"Permanent review ledger archive readback {message}" in failed.stderr
    finally:
        server.shutdown()
        server.server_close()

    assert [record["path"] for record in server.received[:2]] == [
        f"/ci-artifacts/d30/{repo_id}/{artifact_name}/ledger.jsonl",
        f"/ci-artifacts/archive/{repo_id}/{artifact_name}/ledger.jsonl",
    ]
    expected_get = ("GET", f"/ci-artifacts/archive/{repo_id}/{artifact_name}/ledger.jsonl")
    assert (server.received[2]["method"], server.received[2]["path"]) == expected_get
    assert [record["body"] for record in server.received[:2]] == [ledger_bytes, ledger_bytes]


def test_gate_workflow_archives_audit_and_terminal_source_bytes(tmp_path):
    workflow = yaml.safe_load((_REPO_ROOT / ".github/workflows/gate-v2.yml").read_text())
    steps = workflow["jobs"]["ledger"]["steps"]
    download_audit = next(step for step in steps if step.get("name") == "Download canonical primary audit for ledger")
    archive_audit = next(step for step in steps if step.get("name") == "Archive canonical primary audit permanently")
    download_terminal = next(step for step in steps if step.get("name") == "Download gate terminal envelope for ledger")
    archive_terminal = next(step for step in steps if step.get("name") == "Archive gate terminal envelope permanently")
    build_ledger = next(step for step in steps if step.get("name") == "Build v2 review effectiveness ledger")
    archive_ledger = next(step for step in steps if step.get("name") == "Upload v2 raw review ledger to permanent archive")
    assert steps.index(download_audit) < steps.index(download_terminal) < steps.index(build_ledger)
    assert steps.index(build_ledger) < steps.index(archive_ledger) < steps.index(archive_audit) < steps.index(archive_terminal)
    assert download_terminal["if"] == "always() && steps.resolve-ledger-artifacts.outputs.terminal_artifact_id != ''"
    assert archive_ledger["if"] == "always() && steps.build-review-ledger.outcome == 'success'"
    assert archive_audit["if"] == "always() && steps.resolve-ledger-artifacts.outputs.audit_artifact_id != ''"
    assert archive_terminal["if"] == "always() && steps.resolve-ledger-artifacts.outputs.terminal_artifact_id != ''"
    assert archive_audit["env"]["ARTIFACT_PREFIX"] == "${{ steps.resolve-ledger-artifacts.outputs.audit_artifact_id }}"
    assert archive_terminal["env"]["ARTIFACT_PREFIX"] == "${{ steps.resolve-ledger-artifacts.outputs.terminal_artifact_id }}"
    assert "continue-on-error" not in archive_audit and "continue-on-error" not in archive_terminal

    audit_path = _REPO_ROOT / "tests/data/primary_review_v2_with_tier.json"
    audit_bytes = audit_path.read_bytes()
    audit = json.loads(audit_bytes)
    audit_source_attempt = int(audit["run_attempt"])
    current_attempt = audit_source_attempt + 1
    audit_dir = tmp_path / "primary-audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_bytes(audit_bytes)
    terminal_path = tmp_path / "gate-terminal" / "gate-terminal.json"
    terminal_path.parent.mkdir()
    source_audit_name = (
        f"primary-audit-v2-{audit['repository_id']}-{audit['head_sha']}-{audit['run_id']}-{audit_source_attempt}"
    )
    terminal_name = (
        f"gate-terminal-v1-{audit['repository_id']}-{audit['head_sha']}-{audit['run_id']}-{audit_source_attempt}"
    )
    subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / ".github/actions/gate-aggregator/aggregate.py"),
            "--quality-result", "success",
            "--primary-result", "success",
            "--preflight-result", "success",
            "--runner", "self",
            "--is-draft", "false",
            "--pr-author-json", json.dumps("archive-fixture-author"),
            "--is-fork", "false",
            "--classify-review-expected", "true",
            "--review-expected", "true",
            "--repository-id", str(audit["repository_id"]),
            "--repository", audit["repository"],
            "--head-sha", audit["head_sha"],
            "--run-id", str(audit["run_id"]),
            "--run-attempt", str(audit_source_attempt),
            "--pr-number", str(audit["pr"]),
            "--audit-source-attempt", str(audit_source_attempt),
            "--audit-artifact-name", source_audit_name,
            "--audit-dir", str(audit_dir),
            "--terminal-path", str(terminal_path),
            "--summary-path", str(tmp_path / "summary.md"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    terminal_bytes = terminal_path.read_bytes()
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_bytes(b'{"source":"ledger"}\n')
    ledger_artifact_name = (f"codex-review-ledger-v2-{audit['repository_id']}-{audit['head_sha']}-"
                            f"{audit['run_id']}-{current_attempt}")

    server = _start_record_server()
    archive_cases = (
        (archive_audit, "AUDIT_PATH", audit_dir / "primary-review-audit.json", source_audit_name, audit_bytes),
        (archive_terminal, "TERMINAL_PATH", terminal_path, terminal_name, terminal_bytes),
    )
    try:
        ledger_archive = _run_gate_silo_step(
            archive_ledger, tmp_path=tmp_path, server=server, artifact_prefix=ledger_artifact_name,
            repo_id=str(audit["repository_id"]), source_env_name="LEDGER_PATH", source_path=ledger_path,
        )
        assert ledger_archive.returncode == 0, ledger_archive.stderr + ledger_archive.stdout
        assert server.objects[
            f"/ci-artifacts/archive/{audit['repository_id']}/{ledger_artifact_name}/ledger.jsonl"
        ] == (tmp_path / "LEDGER_PATH-archive-readback.json").read_bytes() == ledger_path.read_bytes()

        for step, source_env_name, source_path, artifact_name, expected_bytes in archive_cases:
            artifact_tier = "d14" if source_env_name == "AUDIT_PATH" else "d30"
            artifact_prefix = f"{artifact_tier}/{audit['repository_id']}/{artifact_name}"
            result = _run_gate_silo_step(
                step,
                tmp_path=tmp_path,
                server=server,
                artifact_prefix=artifact_prefix,
                repo_id=str(audit["repository_id"]),
                source_env_name=source_env_name,
                source_path=source_path,
            )
            expected_key = f"archive/{audit['repository_id']}/{artifact_name}/{source_path.name}"
            assert result.returncode == 0, result.stderr + result.stdout
            assert server.objects[f"/ci-artifacts/{expected_key}"] == expected_bytes
            assert (tmp_path / f"{source_env_name}-archive-readback.json").read_bytes() == expected_bytes

        put_records = [record for record in server.received if record["method"] == "PUT"]
        assert [record["body"] for record in put_records[:3]] == [
            ledger_path.read_bytes(), audit_bytes, terminal_bytes,
        ]
        assert [record["path"] for record in put_records] == [
            f"/ci-artifacts/archive/{audit['repository_id']}/{ledger_artifact_name}/ledger.jsonl",
            f"/ci-artifacts/archive/{audit['repository_id']}/{source_audit_name}/primary-review-audit.json",
            f"/ci-artifacts/archive/{audit['repository_id']}/{terminal_name}/gate-terminal.json",
        ]

        for step, source_env_name, source_path, artifact_name, _ in archive_cases:
            artifact_tier = "d14" if source_env_name == "AUDIT_PATH" else "d30"
            artifact_prefix = f"{artifact_tier}/{audit['repository_id']}/{artifact_name}"
            server.get_status = 403
            failed_get = _run_gate_silo_step(
                step,
                tmp_path=tmp_path,
                server=server,
                artifact_prefix=artifact_prefix,
                repo_id=str(audit["repository_id"]),
                source_env_name=source_env_name,
                source_path=source_path,
            )
            assert failed_get.returncode != 0
            assert f"Permanent {'primary audit' if source_env_name == 'AUDIT_PATH' else 'gate terminal'} archive readback failed" in failed_get.stderr

            server.get_status = None
            server.corrupt_get = True
            mismatched_get = _run_gate_silo_step(
                step,
                tmp_path=tmp_path,
                server=server,
                artifact_prefix=artifact_prefix,
                repo_id=str(audit["repository_id"]),
                source_env_name=source_env_name,
                source_path=source_path,
            )
            assert mismatched_get.returncode != 0
            assert "archive readback bytes do not match" in mismatched_get.stderr

            server.corrupt_get = False
            missing_source = _run_gate_silo_step(
                step,
                tmp_path=tmp_path,
                server=server,
                artifact_prefix=artifact_prefix,
                repo_id=str(audit["repository_id"]),
                source_env_name=source_env_name,
                source_path=tmp_path / f"missing-{source_env_name}.json",
            )
            assert missing_source.returncode != 0
            assert "archive upload failed" in missing_source.stderr

        server.get_status = 403
        failed_audit_archive = _run_gate_silo_step(
            archive_audit, tmp_path=tmp_path, server=server,
            artifact_prefix=f"d14/{audit['repository_id']}/{source_audit_name}",
            repo_id=str(audit["repository_id"]), source_env_name="AUDIT_PATH",
            source_path=audit_dir / "primary-review-audit.json",
        )
        assert failed_audit_archive.returncode != 0
        assert "Permanent primary audit archive readback failed" in failed_audit_archive.stderr

        server.get_status = None
        terminal_after_audit_failure = _run_gate_silo_step(
            archive_terminal, tmp_path=tmp_path, server=server,
            artifact_prefix=f"d30/{audit['repository_id']}/{terminal_name}",
            repo_id=str(audit["repository_id"]), source_env_name="TERMINAL_PATH",
            source_path=terminal_path,
        )
        assert terminal_after_audit_failure.returncode == 0, terminal_after_audit_failure.stderr
        assert server.objects[f"/ci-artifacts/archive/{audit['repository_id']}/{terminal_name}/gate-terminal.json"] == terminal_bytes
    finally:
        server.shutdown()
        server.server_close()

    assert all(
        artifact_name.endswith(f"-{audit_source_attempt}")
        and not artifact_name.endswith(f"-{current_attempt}")
        for _, _, _, artifact_name, _ in archive_cases
    )


def test_skipped_gate_archives_terminal_without_fabricating_audit(tmp_path):
    workflow = yaml.safe_load((_REPO_ROOT / ".github/workflows/gate-v2.yml").read_text())
    steps = workflow["jobs"]["ledger"]["steps"]
    archive_audit = next(step for step in steps if step.get("name") == "Archive canonical primary audit permanently")
    archive_terminal = next(step for step in steps if step.get("name") == "Archive gate terminal envelope permanently")
    audit_output = "steps.resolve-ledger-artifacts.outputs.audit_artifact_id"
    assert audit_output in archive_audit["if"] and "!= ''" in archive_audit["if"]

    fixture = json.loads((_REPO_ROOT / "tests/data/primary_review_v2_with_tier.json").read_text())
    repo_id = str(fixture["repository_id"])
    source_attempt = 1
    run_id = str(fixture["run_id"])
    terminal_name = f"gate-terminal-v1-{repo_id}-{fixture['head_sha']}-{run_id}-{source_attempt}"
    terminal_path = tmp_path / "gate-terminal.json"
    subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / ".github/actions/gate-aggregator/aggregate.py"),
            "--quality-result", "success",
            "--primary-result", "skipped",
            "--runner", "self",
            "--is-draft", "false",
            "--pr-author-json", json.dumps("archive-fixture-author"),
            "--is-fork", "false",
            "--classify-review-expected", "false",
            "--review-expected", "false",
            "--repository-id", repo_id,
            "--repository", fixture["repository"],
            "--head-sha", fixture["head_sha"],
            "--run-id", run_id,
            "--run-attempt", str(source_attempt),
            "--pr-number", str(fixture["pr"]),
            "--audit-dir", str(tmp_path / "empty-audit"),
            "--terminal-path", str(terminal_path),
            "--summary-path", str(tmp_path / "skip-summary.md"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    terminal_bytes = terminal_path.read_bytes()
    terminal = json.loads(terminal_bytes)
    assert terminal["gate_result"] == "skipped" and terminal["skip_reason"]

    server = _start_record_server()
    try:
        result = _run_gate_silo_step(
            archive_terminal,
            tmp_path=tmp_path,
            server=server,
            artifact_prefix=f"d30/{repo_id}/{terminal_name}",
            repo_id=repo_id,
            source_env_name="TERMINAL_PATH",
            source_path=terminal_path,
        )
    finally:
        server.shutdown()
        server.server_close()
    expected_key = f"/ci-artifacts/archive/{repo_id}/{terminal_name}/gate-terminal.json"
    assert result.returncode == 0, result.stderr + result.stdout
    assert server.objects[expected_key] == terminal_bytes
    assert (tmp_path / "TERMINAL_PATH-archive-readback.json").read_bytes() == terminal_bytes
    assert not any("/primary-review-audit.json" in record["path"] for record in server.received)


def test_cli_put_incomplete_body_emits_one_status_event(tmp_path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes(_BODY_S.encode())
    server = _start_record_server(status=400, body=_INCOMPLETE_XML, extra_headers=_WL)
    try:
        result = _put(f"http://127.0.0.1:{server.server_port}", payload)
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == store.EXIT_ERROR
    _assert_redacted(result.stderr)
    events = _events(result.stderr)
    assert len(events) == 1
    event, rec = events[0], server.received[0]
    _assert_shape(event)
    assert rec["content_length_header"] == str(len(_BODY_S))
    assert rec["body_sha256"] == hashlib.sha256(_BODY_S.encode()).hexdigest()
    assert rec["body_len"] == event["body_len"] == event["declared_content_length"] == len(_BODY_S)
    assert event["method"] == "PUT" and event["phase"] == "status" and event["http_status"] == 400
    assert event["s3_error_code"] == "IncompleteBody" and event["exception_type"] is None
    assert event["request_send_completed"] is True and event["request_seq"] == 1
    assert event["resp_server"] == "MinIO"
    assert event["resp_amz_request_id"] == "RID-WHITELIST"
    assert event["resp_amz_id_2"] == "ID2-WHITELIST"
    assert event["resp_content_length"] == str(len(_INCOMPLETE_XML))
    assert "Silo put failed: S3Error: 400 IncompleteBody" in result.stderr


def test_cli_put_success_stdout_and_204_still_fails(tmp_path):
    payload = tmp_path / "ok.bin"
    payload.write_bytes(b"ok-bytes")
    server = _start_record_server(status=200, body=b"")
    try:
        ok = _put(f"http://127.0.0.1:{server.server_port}", payload)
        server.status = 204
        twenty = _put(f"http://127.0.0.1:{server.server_port}", payload)
    finally:
        server.shutdown()
        server.server_close()
    assert ok.returncode == store.EXIT_OK
    assert ok.stdout == "d1/1/diag/ok.bin\n"
    assert "SILO-REQUEST-FAILED" not in ok.stderr
    _assert_redacted(ok.stderr + ok.stdout)
    assert twenty.returncode == store.EXIT_ERROR
    events = _events(twenty.stderr)
    assert len(events) == 1
    assert events[0]["http_status"] == 204 and events[0]["phase"] == "status"


def test_cli_get_close_after_read_is_recv_headers(tmp_path):
    server = _start_record_server(close_after_read=True)
    try:
        result = _get(f"http://127.0.0.1:{server.server_port}", tmp_path / "out.bin")
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == store.EXIT_ERROR and result.stdout == ""
    events = _events(result.stderr)
    assert len(events) == 1
    event = events[0]
    _assert_shape(event)
    assert event["method"] == "GET" and event["phase"] == "recv_headers"
    assert event["http_status"] is event["s3_error_code"] is None
    assert event["exception_type"] and event["exception_type"].isidentifier()
    assert event["request_send_completed"] is True
    assert event["declared_content_length"] is None and event["body_len"] == 0
    human = [line for line in result.stderr.splitlines() if not line.startswith(_DIAG)]
    assert human == [f"Silo request failed: {event['exception_type']}"]
    _assert_redacted(result.stderr)


def test_cli_get_html_502_and_long_server_are_redacted(tmp_path):
    long_server = "A\x00B\x01" + ("C" * 996)
    assert len(long_server) == 1000
    extra = (("Server", long_server), ("x-amz-request-id", "RID-HTML"), ("x-amz-id-2", "ID2-HTML"))
    server = _start_record_server(status=502, body=_HTML_S.encode(), extra_headers=extra)
    try:
        result = _get(f"http://127.0.0.1:{server.server_port}", tmp_path / "out.bin")
        empty = tmp_path / "empty.bin"
        empty.write_bytes(b"")
        empty_result = _put(f"http://127.0.0.1:{server.server_port}", empty)
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == store.EXIT_ERROR
    events = _events(result.stderr)
    assert len(events) == 1
    event = events[0]
    _assert_shape(event)
    assert event["http_status"] == 502 and event["s3_error_code"] is None and event["phase"] == "status"
    assert event["declared_content_length"] is None and event["body_len"] == 0
    assert server.received[0]["content_length_header"] in (None, "0")
    assert event["resp_server"] and len(event["resp_server"]) <= 128
    assert all(32 <= ord(ch) <= 126 for ch in event["resp_server"])
    assert long_server not in result.stderr
    _assert_redacted(result.stderr)
    empty_events = _events(empty_result.stderr)
    assert len(empty_events) == 1
    assert empty_events[0]["declared_content_length"] is None and empty_events[0]["body_len"] == 0
    assert server.received[1]["content_length_header"] in (None, "0")


def test_request_send_and_recv_body_exceptions(monkeypatch, capsys):
    client = store.S3Client("http://127.0.0.1:9", "ak", "sk")

    def install(fail_at, exc):
        class _Resp:
            status = 200
            def getheader(self, name, default=None):
                return default
            def read(self):
                if fail_at == "recv_body":
                    raise exc
                return b"ok"
        class _Conn:
            def __init__(self, *a, **k):
                pass
            def request(self, *a, **k):
                if fail_at == "send":
                    raise exc
            def getresponse(self):
                if fail_at == "recv_headers":
                    raise exc
                return _Resp()
            def close(self):
                pass
        monkeypatch.setattr(http.client, "HTTPConnection", _Conn)

    cases = (
        ("send", OSError("send-secret-boom"), "send", False, None),
        ("recv_headers", TimeoutError("wait-secret-boom"), "recv_headers", True, None),
        ("recv_body", http.client.IncompleteRead(b"", 4), "recv_body", True, 200),
    )
    for fail_at, exc, phase, sent, status in cases:
        install(fail_at, exc)
        capsys.readouterr()
        with pytest.raises(SystemExit) as caught:
            client._request("PUT", "/ci-artifacts/k", [], b"xx")
        assert caught.value.code == store.EXIT_ERROR
        err = capsys.readouterr().err
        events = _events(err)
        assert len(events) == 1, err
        event = events[0]
        _assert_shape(event)
        assert event["phase"] == phase
        assert event["request_send_completed"] is sent
        assert event["exception_type"] == type(exc).__name__
        assert event["http_status"] == status
        assert event["declared_content_length"] == event["body_len"] == 2
        assert f"Silo request failed: {type(exc).__name__}" in err
        assert str(exc) not in err


def test_gate_workflow_writes_live_review_record_bytes_to_versioned_silo_key(tmp_path):
    workflow = yaml.safe_load((_REPO_ROOT / ".github/workflows/gate-v2.yml").read_text())
    action = yaml.safe_load((_REPO_ROOT / ".github/actions/review-ledger/action.yml").read_text())
    steps = workflow["jobs"]["ledger"]["steps"]
    build = next(step for step in steps if step.get("name") == "Build v2 review effectiveness ledger")
    upload = next(step for step in steps if step.get("name") == "Upload live review record/v1 to permanent Silo")
    assert upload["if"] == "always() && steps.build-review-ledger.outcome == 'success'"
    prior_archives = (
        "Upload v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger latest pointer",
        "Upload v2 raw review ledger to permanent archive",
        "Archive canonical primary audit permanently",
        "Archive gate terminal envelope permanently",
    )
    prior_archive_indices = [
        steps.index(next(step for step in steps if step.get("name") == name))
        for name in prior_archives
    ]
    assert max(prior_archive_indices) < steps.index(upload)
    audit_path = _REPO_ROOT / "tests/data/primary_review_v2_with_tier.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    runner_temp = tmp_path / "runner"
    record_path = Path(build["with"]["record-output"].replace("${{ runner.temp }}", str(runner_temp)))
    record_input = action["inputs"]["record-output"]
    assert record_input.get("required") is False and record_input.get("default") == ""
    input_paths = {
        "audit-path": str(audit_path),
        "preflight-path": str(_REPO_ROOT / "tests/fixtures/quality-ledger-input/pr-size-preflight.json"),
        "install-path": str(_REPO_ROOT / "tests/fixtures/quality-ledger-input/install-result.json"),
        "terminal-path": "",
        "shadow-path": str(runner_temp / "incremental-shadow/shadow.json"),
        "record-output": str(record_path),
        "codex-expected": "true",
        "codex-waived": "false",
        "expected-repository-id": str(audit["repository_id"]),
        "expected-base-sha": audit["base_sha"],
        "expected-caller-sha": audit["caller_sha"],
        "expected-reusable-workflow-sha": audit["reusable_workflow_sha"],
        "input-short-circuited": "false",
    }
    action_run = action["runs"]["steps"][0]["run"]
    for name, value in input_paths.items():
        action_run = action_run.replace("${{ inputs." + name + " }}", value)
    for expression, value in {
        "${{ github.event.pull_request.number }}": str(audit["pr"]),
        "${{ github.run_id }}": str(audit["run_id"]),
        "${{ github.run_attempt }}": str(audit["run_attempt"]),
        "${{ github.event.pull_request.head.sha }}": audit["head_sha"],
    }.items():
        action_run = action_run.replace(expression, value)
    action_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GITHUB_ACTION_PATH": str(_REPO_ROOT / ".github/actions/review-ledger"),
        "GITHUB_REPOSITORY": audit["repository"],
        "GITHUB_RUN_ID": str(audit["run_id"]),
        "GITHUB_RUN_ATTEMPT": str(audit["run_attempt"]),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "REVIEW_RECORD_OUTPUT": str(record_path),
    }
    build_result = subprocess.run(
        ["bash", "-x", "-euo", "pipefail", "-c", action_run],
        capture_output=True, text=True,
        env=action_env,
        check=False,
    )
    assert build_result.returncode == 0, build_result.stderr + build_result.stdout
    assert action["runs"]["steps"][0]["env"]["REVIEW_RECORD_OUTPUT"] == "${{ inputs.record-output }}"
    assert f"--record-output {record_path}" in build_result.stderr
    ledger_only_temp = tmp_path / "runner-ledger-only"
    ledger_only_result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", action_run], capture_output=True, text=True,
        env={**action_env, "RUNNER_TEMP": str(ledger_only_temp), "REVIEW_RECORD_OUTPUT": ""}, check=False,
    )
    assert ledger_only_result.returncode == 0, ledger_only_result.stderr + ledger_only_result.stdout
    assert (ledger_only_temp / "review-ledger/ledger.jsonl").is_file()
    assert not (ledger_only_temp / "review-ledger/review-record-v1.json").exists()
    record_bytes = record_path.read_bytes()
    record = json.loads(record_bytes)
    assert record["schema_version"] == "review-record/v1"
    assert record["source"] == "ci" and record["pr"] == audit["pr"]
    raw_artifact = f"codex-review-ledger-v2-{audit['repository_id']}-{audit['head_sha']}-{audit['run_id']}-{audit['run_attempt']}"
    assert record["provenance"]["source_key"] == f"silo/ci-artifacts/d30/{audit['repository_id']}/{raw_artifact}/ledger.jsonl"

    server = _start_record_server(body=b"<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>")
    try:
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", upload["run"]],
            capture_output=True, text=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "AWS_ACCESS_KEY_ID": _ACCESS,
                "AWS_SECRET_ACCESS_KEY": _SECRET,
                "SILO_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
                "SILO_BUCKET": "ci-artifacts",
                "SILO_EXEC": str(_REPO_ROOT / "scripts/silo_exec.sh"),
                "SILO_STORE": str(_STORE_PY),
                "RUNNER_TEMP": str(tmp_path),
                "REVIEW_RECORD_PATH": str(record_path),
            },
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    owner, repo = audit["repository"].split("/")
    expected_key = f"archive/review-record/v1/{owner}/{repo}/{audit['pr']}/{audit['head_sha']}/ci-{audit['run_id']}-{audit['run_attempt']}.json"
    assert result.returncode == 0, result.stderr + result.stdout
    assert server.objects[f"/ci-artifacts/{expected_key}"] == record_bytes
    put = next(item for item in server.received if item["method"] == "PUT")
    assert put["path"] == f"/ci-artifacts/{expected_key}" and put["body"] == record_bytes
    get = next(item for item in server.responded if item["method"] == "GET" and item["path"] == f"/ci-artifacts/{expected_key}")
    assert get["body"] == record_bytes
    assert [item["method"] for item in server.received] == ["GET", "PUT", "GET"]
    assert not any("latest" in item["path"] or item["method"] == "DELETE" for item in server.received)


@pytest.mark.parametrize("payload", [
    b"not json",
    json.dumps({"schema_version": "review-record/v1", "source": "unknown", "repo": "example-org/example-repo", "pr": 2, "head_sha": "a" * 40, "run_id": 7, "attempt": 1}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "../gate", "pr": 2, "head_sha": "a" * 40, "run_id": 7, "attempt": 1}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo", "pr": True, "head_sha": "a" * 40, "run_id": 7, "attempt": 1}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo", "pr": 0, "head_sha": "a" * 40, "run_id": 7, "attempt": 1}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo", "pr": 2, "head_sha": "A" * 40, "run_id": 7, "attempt": 1}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo", "pr": 2, "head_sha": "a" * 40, "run_id": True, "attempt": 1}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo", "pr": 2, "head_sha": "a" * 40, "run_id": 0, "attempt": 1}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo", "pr": 2, "head_sha": "a" * 40, "run_id": 7, "attempt": True}).encode(),
    json.dumps({"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo", "pr": 2, "head_sha": "a" * 40, "run_id": 7, "attempt": 0}).encode(),
])
def test_put_review_record_rejects_bad_payload_before_connect(tmp_path, monkeypatch, payload):
    source = tmp_path / "review-record.json"
    source.write_bytes(payload)
    monkeypatch.setattr(store, "connect", lambda: pytest.fail("invalid review record reached Silo"))
    with pytest.raises(SystemExit) as caught:
        store.main(["put-review-record", "--file", str(source)])
    assert caught.value.code == store.EXIT_ERROR
    assert source.read_bytes() == payload


def test_put_review_record_refuses_identical_existing_key(tmp_path, monkeypatch, capsys):
    record = {"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo",
              "pr": None, "head_sha": "a" * 40, "run_id": 7, "attempt": 1}
    payload = (json.dumps(record) + "\n").encode()
    source = tmp_path / "record.json"
    source.write_bytes(payload)
    key = "archive/review-record/v1/example-org/example-repo/none/" + "a" * 40 + "/ci-7-1.json"
    fake = FakeS3({key: payload})
    _use(monkeypatch, fake)
    with pytest.raises(SystemExit) as caught:
        store.main(["put-review-record", "--file", str(source)])
    assert caught.value.code == store.EXIT_ERROR
    assert "refusing to overwrite" in capsys.readouterr().err
    assert fake.objects[key] == payload and fake.puts == []


@pytest.mark.parametrize("failure", ["put", "get", "hash"])
def test_put_review_record_http_failure_keeps_source_bytes(tmp_path, failure):
    record = {"schema_version": "review-record/v1", "source": "ci", "repo": "example-org/example-repo",
              "pr": 2, "head_sha": "b" * 40, "run_id": 8, "attempt": 1}
    source = tmp_path / "record.json"
    source.write_bytes((json.dumps(record, separators=(",", ":")) + "\n").encode())
    source_bytes = source.read_bytes()
    server = _start_record_server(
        body=b"<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>",
        status=403 if failure == "put" else 200,
        get_status=403 if failure == "get" else None,
        corrupt_get=failure == "hash",
    )
    try:
        result = _run_store(["put-review-record", "--file", str(source)], f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == store.EXIT_ERROR
    assert source.read_bytes() == source_bytes
    assert not any(item["method"] == "DELETE" for item in server.received)
