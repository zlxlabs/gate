#!/usr/bin/env python3
"""Put, get, and resolve CI artifacts on Silo (S3-compatible object storage).

Key layout (locked for cards B2/C):

    d<tier>/<repo_id>/<artifact_name>/<relative path>

``tier`` is one of ``d1``, ``d3``, ``d14``, ``d30``. ``artifact_name`` is the
producer's GitHub-era name (including repo_id/sha/run_id/attempt suffixes).
Directory producers store one object per file; the last key segment is the
path relative to the uploaded directory.

Environment:

    AWS_ACCESS_KEY_ID       from secrets.SILO_ACCESS_KEY
    AWS_SECRET_ACCESS_KEY   from secrets.SILO_SECRET_KEY
    SILO_ENDPOINT           https://host:9000
    SILO_BUCKET             default ci-artifacts

Exit codes: 0 success; 2 resolve/get found no matching object; 1 any other
failure (missing credentials, listing/transport error, invalid args). A miss
must not reuse the query-failure code.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path


TIERS = frozenset({"d1", "d3", "d14", "d30"})
DEFAULT_BUCKET = "ci-artifacts"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 2
MISSING_ACCESS_KEY = "SILO_ACCESS_KEY 未传入"
MISSING_SECRET_KEY = "SILO_SECRET_KEY 未传入"
ATTEMPT_SUFFIX = re.compile(r"[0-9]+$")


def fail(message: str, code: int = EXIT_ERROR) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def bucket_name() -> str:
    value = (os.environ.get("SILO_BUCKET") or DEFAULT_BUCKET).strip()
    if not value:
        fail("SILO_BUCKET is empty")
    return value


def build_key(tier: str, repo_id: str, artifact_name: str, relative_path: str) -> str:
    """Return the canonical object key. All four segments are required."""

    if tier not in TIERS:
        fail(f"tier must be one of {sorted(TIERS)}; got {tier!r}")
    if not repo_id or "/" in repo_id:
        fail("repo_id must be a non-empty path segment")
    if not artifact_name or "/" in artifact_name:
        fail("artifact_name must be a non-empty path segment")
    rel = relative_path.replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        fail(f"relative path is not a safe object suffix: {relative_path!r}")
    return f"{tier}/{repo_id}/{artifact_name}/{rel}"


def parse_artifact_attempt(artifact_name: str, name_prefix: str) -> int | None:
    """Return the integer attempt suffix if ``artifact_name`` matches prefix+digits."""

    if not artifact_name.startswith(name_prefix):
        return None
    suffix = artifact_name[len(name_prefix) :]
    if not ATTEMPT_SUFFIX.fullmatch(suffix):
        return None
    return int(suffix)


def artifact_prefix(tier: str, repo_id: str, artifact_name: str) -> str:
    if tier not in TIERS:
        fail(f"tier must be one of {sorted(TIERS)}; got {tier!r}")
    return f"{tier}/{repo_id}/{artifact_name}"


def connect():
    """Build a path-style S3 client against SILO_ENDPOINT. No network until used."""

    access = (os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    endpoint = (os.environ.get("SILO_ENDPOINT") or "").strip()
    if not access:
        fail(MISSING_ACCESS_KEY)
    if not secret:
        fail(MISSING_SECRET_KEY)
    if not endpoint:
        fail("SILO_ENDPOINT 未设置")
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        fail("boto3 is not installed")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def _body_bytes(body: object) -> bytes:
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    read = getattr(body, "read", None)
    if callable(read):
        data = read()
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode("utf-8")
    if isinstance(body, str):
        return body.encode("utf-8")
    fail(f"unsupported object body type: {type(body).__name__}")
    raise AssertionError("unreachable")


def list_keys(client, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    token = None
    try:
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = client.list_objects_v2(**kwargs)
            for item in response.get("Contents") or []:
                key = item.get("Key")
                if isinstance(key, str) and key:
                    keys.append(key)
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
            if not token:
                break
    except Exception as err:  # noqa: BLE001 — surface any listing failure as code 1
        fail(f"Silo list failed: {type(err).__name__}: {err}")
    return keys


def iter_dir_files(directory: Path) -> Iterator[tuple[Path, str]]:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        yield path, relative


def cmd_put(args: argparse.Namespace) -> int:
    files: list[tuple[Path, str]] = []
    for raw in args.file or []:
        path = Path(raw)
        if not path.is_file():
            fail(f"put source is not a file: {path}")
        object_name = args.object_name if args.object_name and len(args.file) == 1 else path.name
        files.append((path, object_name))
    if not files:
        fail("put requires at least one --file")
    client = connect()
    bucket = bucket_name()
    try:
        for path, relative in files:
            key = build_key(args.tier, args.repo_id, args.name, relative)
            client.put_object(Bucket=bucket, Key=key, Body=path.read_bytes())
            print(key)
    except SystemExit:
        raise
    except Exception as err:  # noqa: BLE001
        fail(f"Silo put failed: {type(err).__name__}: {err}")
    return EXIT_OK


def cmd_put_dir(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    files = list(iter_dir_files(directory))
    if not files:
        if args.empty == "skip":
            print(
                f"::notice::silo put-dir skipped: directory empty or missing ({directory})"
            )
            return EXIT_OK
        fail(f"put-dir source is empty or missing: {directory}")
    client = connect()
    bucket = bucket_name()
    try:
        for path, relative in files:
            key = build_key(args.tier, args.repo_id, args.name, relative)
            client.put_object(Bucket=bucket, Key=key, Body=path.read_bytes())
            print(key)
    except SystemExit:
        raise
    except Exception as err:  # noqa: BLE001
        fail(f"Silo put-dir failed: {type(err).__name__}: {err}")
    return EXIT_OK


def cmd_get(args: argparse.Namespace) -> int:
    dest = Path(args.dest)
    client = connect()
    bucket = bucket_name()
    if args.key:
        keys = [args.key]
        prefix = args.key.rsplit("/", 1)[0] if "/" in args.key else ""
    else:
        prefix = args.prefix.rstrip("/")
        try:
            keys = list_keys(client, bucket, prefix + "/")
        except SystemExit:
            raise
        if not keys:
            fail(f"no objects under prefix {prefix}", EXIT_NOT_FOUND)
    try:
        for key in keys:
            try:
                response = client.get_object(Bucket=bucket, Key=key)
            except Exception as err:  # noqa: BLE001
                name = type(err).__name__
                if name in {"NoSuchKey", "ClientError"} or "NoSuchKey" in str(err):
                    fail(f"Silo object not found: {key}", EXIT_NOT_FOUND)
                fail(f"Silo get failed: {name}: {err}")
            body = _body_bytes(response.get("Body") if isinstance(response, dict) else response)
            if args.key and (dest.exists() and dest.is_dir() or str(args.dest).endswith("/")):
                target = dest / Path(key).name
            elif args.key:
                target = dest
            else:
                suffix = key[len(prefix) :].lstrip("/")
                if not suffix:
                    fail(f"object key {key!r} is not under prefix {prefix!r}")
                target = dest / suffix
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            print(str(target))
    except SystemExit:
        raise
    except Exception as err:  # noqa: BLE001
        fail(f"Silo get failed: {type(err).__name__}: {err}")
    return EXIT_OK


def select_attempt(
    artifact_names: Iterable[str], name_prefix: str, current_attempt: int
) -> tuple[str, int] | None:
    """Pick the largest attempt ≤ current_attempt. None if no candidate."""

    candidates: list[tuple[int, str]] = []
    for name in artifact_names:
        attempt = parse_artifact_attempt(name, name_prefix)
        if attempt is None:
            continue
        if attempt <= current_attempt:
            candidates.append((attempt, name))
    if not candidates:
        return None
    attempt, name = max(candidates, key=lambda item: item[0])
    return name, attempt


def artifact_names_from_keys(keys: Iterable[str], tier: str, repo_id: str) -> list[str]:
    expected = f"{tier}/{repo_id}/"
    names: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if not key.startswith(expected):
            continue
        rest = key[len(expected) :]
        name = rest.split("/", 1)[0]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def cmd_resolve(args: argparse.Namespace) -> int:
    if args.attempt < 1:
        fail("attempt must be >= 1")
    listing_prefix = f"{args.tier}/{args.repo_id}/{args.name_prefix}"
    client = connect()
    bucket = bucket_name()
    keys = list_keys(client, bucket, listing_prefix)
    names = artifact_names_from_keys(keys, args.tier, args.repo_id)
    selected = select_attempt(names, args.name_prefix, args.attempt)
    if selected is None:
        fail(
            f"No matching Silo artifact for prefix {args.name_prefix!r} "
            f"(attempt <= {args.attempt})",
            EXIT_NOT_FOUND,
        )
    name, attempt = selected
    prefix = artifact_prefix(args.tier, args.repo_id, name)
    print(f"{prefix}\t{attempt}\t{name}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    put = sub.add_parser("put", help="upload one or more files as objects")
    put.add_argument("--tier", required=True, choices=sorted(TIERS))
    put.add_argument("--repo-id", required=True)
    put.add_argument("--name", required=True, help="artifact_name path segment")
    put.add_argument("--file", action="append", default=[], help="repeatable source file")
    put.add_argument("--object-name", default="", help="override object suffix for a single --file")
    put.set_defaults(func=cmd_put)

    put_dir = sub.add_parser("put-dir", help="upload every file under a directory")
    put_dir.add_argument("--tier", required=True, choices=sorted(TIERS))
    put_dir.add_argument("--repo-id", required=True)
    put_dir.add_argument("--name", required=True)
    put_dir.add_argument("--dir", required=True)
    put_dir.add_argument(
        "--empty",
        choices=("error", "skip"),
        default="error",
        help="skip: notice and exit 0 when the directory is missing or empty",
    )
    put_dir.set_defaults(func=cmd_put_dir)

    get = sub.add_parser("get", help="download one key or every object under a prefix")
    get.add_argument("--dest", required=True)
    key_group = get.add_mutually_exclusive_group(required=True)
    key_group.add_argument("--key", default="")
    key_group.add_argument("--prefix", default="")
    get.set_defaults(func=cmd_get)

    resolve = sub.add_parser(
        "resolve",
        help="print the selected prefix, attempt, and artifact name (tab-separated)",
    )
    resolve.add_argument("--tier", required=True, choices=sorted(TIERS))
    resolve.add_argument("--repo-id", required=True)
    resolve.add_argument("--name-prefix", required=True)
    resolve.add_argument("--attempt", required=True, type=int)
    resolve.set_defaults(func=cmd_resolve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
