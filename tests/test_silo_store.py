"""Stubbed-boto3 contracts for scripts/silo_store.py. No disk network, no real S3."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from scripts import silo_store as store


class FakeS3:
    def __init__(self, objects: dict[str, bytes] | None = None, *, list_error: Exception | None = None):
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


def _env(monkeypatch, **extra):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setenv("SILO_BUCKET", "ci-artifacts")
    for key, value in extra.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _connect_to(fake: FakeS3):
    def _connect():
        return fake

    return _connect


def test_build_key_joins_tier_repo_name_and_relative_path():
    assert store.build_key("d14", "99", "primary-audit-v2-99-abc-7-1", "primary-review-audit.json") == (
        "d14/99/primary-audit-v2-99-abc-7-1/primary-review-audit.json"
    )
    assert store.build_key("d3", "1", "advisory-event-ocr-1-2", "advisory/event.json") == (
        "d3/1/advisory-event-ocr-1-2/advisory/event.json"
    )


def test_build_key_rejects_unknown_tier_and_unsafe_relative_path():
    with pytest.raises(SystemExit) as caught:
        store.build_key("d7", "1", "name", "file.json")
    assert caught.value.code == store.EXIT_ERROR
    with pytest.raises(SystemExit):
        store.build_key("d1", "1", "name", "../escape")
    with pytest.raises(SystemExit):
        store.build_key("d1", "1/2", "name", "file.json")


def test_select_attempt_picks_max_at_or_below_current():
    names = [
        "primary-audit-v2-1-sha-9-1",
        "primary-audit-v2-1-sha-9-2",
        "primary-audit-v2-1-sha-9-4",
        "other-1",
    ]
    prefix = "primary-audit-v2-1-sha-9-"
    picked = store.select_attempt(names, prefix, 3)
    assert picked == ("primary-audit-v2-1-sha-9-2", 2)
    assert store.select_attempt(names, prefix, 2) == ("primary-audit-v2-1-sha-9-2", 2)
    assert store.select_attempt(names, prefix, 4) == ("primary-audit-v2-1-sha-9-4", 4)
    assert store.select_attempt(names, prefix, 1) == ("primary-audit-v2-1-sha-9-1", 1)


def test_select_attempt_returns_none_when_only_newer_attempts_exist():
    names = ["gate-terminal-v1-1-sha-9-3"]
    assert store.select_attempt(names, "gate-terminal-v1-1-sha-9-", 2) is None
    assert store.select_attempt([], "gate-terminal-v1-1-sha-9-", 1) is None


def test_put_writes_canonical_key(tmp_path, monkeypatch):
    _env(monkeypatch)
    fake = FakeS3()
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    source = tmp_path / "primary-review-audit.json"
    source.write_bytes(b'{"ok":true}')
    rc = store.main(
        [
            "put",
            "--tier", "d14",
            "--repo-id", "42",
            "--name", "primary-audit-v2-42-deadbeef-99-1",
            "--file", str(source),
        ]
    )
    assert rc == 0
    assert fake.puts == ["d14/42/primary-audit-v2-42-deadbeef-99-1/primary-review-audit.json"]
    assert fake.objects[fake.puts[0]] == b'{"ok":true}'


def test_put_multiple_files_use_basenames(tmp_path, monkeypatch):
    _env(monkeypatch)
    fake = FakeS3()
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    a = tmp_path / "pr-size-preflight.json"
    b = tmp_path / "install-result.json"
    a.write_text("{}", encoding="utf-8")
    b.write_text("{}", encoding="utf-8")
    rc = store.main(
        [
            "put",
            "--tier", "d1",
            "--repo-id", "7",
            "--name", "review-ledger-input-v2-7-sha-1-1",
            "--file", str(a),
            "--file", str(b),
        ]
    )
    assert rc == 0
    assert fake.puts == [
        "d1/7/review-ledger-input-v2-7-sha-1-1/pr-size-preflight.json",
        "d1/7/review-ledger-input-v2-7-sha-1-1/install-result.json",
    ]


def test_put_dir_stores_relative_paths(tmp_path, monkeypatch):
    _env(monkeypatch)
    fake = FakeS3()
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    root = tmp_path / "diagnostics"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    (nested / "raw.txt").write_bytes(b"x")
    rc = store.main(
        [
            "put-dir",
            "--tier", "d3",
            "--repo-id", "8",
            "--name", "primary-review-diagnostics-v2-8-sha-1-1",
            "--dir", str(root),
        ]
    )
    assert rc == 0
    assert set(fake.puts) == {
        "d3/8/primary-review-diagnostics-v2-8-sha-1-1/manifest.json",
        "d3/8/primary-review-diagnostics-v2-8-sha-1-1/nested/raw.txt",
    }


def test_put_dir_empty_skip_prints_notice(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    fake = FakeS3()
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    missing = tmp_path / "nope"
    rc = store.main(
        [
            "put-dir",
            "--tier", "d3",
            "--repo-id", "8",
            "--name", "primary-review-diagnostics-v2-8-sha-1-1",
            "--dir", str(missing),
            "--empty", "skip",
        ]
    )
    assert rc == 0
    assert fake.puts == []
    captured = capsys.readouterr()
    assert "::notice::silo put-dir skipped" in captured.out


def test_get_prefix_restores_relative_layout(tmp_path, monkeypatch):
    _env(monkeypatch)
    fake = FakeS3(
        {
            "d14/9/primary-audit-v2-9-sha-1-2/primary-review-audit.json": b"audit",
        }
    )
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    dest = tmp_path / "out"
    rc = store.main(
        [
            "get",
            "--prefix", "d14/9/primary-audit-v2-9-sha-1-2",
            "--dest", str(dest),
        ]
    )
    assert rc == 0
    assert (dest / "primary-review-audit.json").read_bytes() == b"audit"


def test_resolve_prints_selected_prefix_and_picks_max_attempt(monkeypatch, capsys):
    _env(monkeypatch)
    fake = FakeS3(
        {
            "d14/5/primary-audit-v2-5-sha-11-1/primary-review-audit.json": b"a",
            "d14/5/primary-audit-v2-5-sha-11-2/primary-review-audit.json": b"b",
            "d14/5/primary-audit-v2-5-sha-11-4/primary-review-audit.json": b"c",
        }
    )
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    rc = store.main(
        [
            "resolve",
            "--tier", "d14",
            "--repo-id", "5",
            "--name-prefix", "primary-audit-v2-5-sha-11-",
            "--attempt", "3",
        ]
    )
    assert rc == 0
    line = capsys.readouterr().out.strip()
    assert line == (
        "d14/5/primary-audit-v2-5-sha-11-2\t2\tprimary-audit-v2-5-sha-11-2"
    )


def test_resolve_no_match_exits_not_found(monkeypatch):
    _env(monkeypatch)
    fake = FakeS3({"d14/5/primary-audit-v2-5-sha-11-4/file.json": b"x"})
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    with pytest.raises(SystemExit) as caught:
        store.main(
            [
                "resolve",
                "--tier", "d14",
                "--repo-id", "5",
                "--name-prefix", "primary-audit-v2-5-sha-11-",
                "--attempt", "3",
            ]
        )
    assert caught.value.code == store.EXIT_NOT_FOUND
    assert caught.value.code != store.EXIT_ERROR


def test_resolve_listing_failure_exits_error_not_not_found(monkeypatch):
    _env(monkeypatch)
    fake = FakeS3(list_error=RuntimeError("connection refused"))
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    with pytest.raises(SystemExit) as caught:
        store.main(
            [
                "resolve",
                "--tier", "d14",
                "--repo-id", "5",
                "--name-prefix", "primary-audit-v2-5-sha-11-",
                "--attempt", "1",
            ]
        )
    assert caught.value.code == store.EXIT_ERROR
    assert caught.value.code != store.EXIT_NOT_FOUND


def test_get_missing_prefix_exits_not_found(tmp_path, monkeypatch):
    _env(monkeypatch)
    fake = FakeS3()
    monkeypatch.setattr(store, "connect", _connect_to(fake))
    with pytest.raises(SystemExit) as caught:
        store.main(["get", "--prefix", "d14/1/missing", "--dest", str(tmp_path)])
    assert caught.value.code == store.EXIT_NOT_FOUND


def test_missing_access_key_message(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    with pytest.raises(SystemExit) as caught:
        store.connect()
    assert caught.value.code == store.EXIT_ERROR
    # SystemExit message is the printed string when raised via fail()? fail() prints
    # then raise SystemExit(code) with just the code. Capture stderr instead.


def test_missing_access_key_stderr(monkeypatch, capsys):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    with pytest.raises(SystemExit) as caught:
        store.connect()
    assert caught.value.code == store.EXIT_ERROR
    assert "SILO_ACCESS_KEY 未传入" in capsys.readouterr().err


def test_put_missing_file_fails_without_connecting(tmp_path, monkeypatch):
    _env(monkeypatch)

    def boom():
        raise AssertionError("connect must not run when the source file is missing")

    monkeypatch.setattr(store, "connect", boom)
    with pytest.raises(SystemExit) as caught:
        store.main(
            [
                "put",
                "--tier", "d1",
                "--repo-id", "1",
                "--name", "review-ledger-input-v2-1-sha-1-1",
                "--file", str(tmp_path / "absent.json"),
            ]
        )
    assert caught.value.code == store.EXIT_ERROR


def test_connect_imports_boto3_lazily():
    source = Path(store.__file__).read_text(encoding="utf-8")
    assert "import boto3" in source
    assert source.index("def connect") < source.index("import boto3")
