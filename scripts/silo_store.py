#!/usr/bin/env python3
"""Put, get, list, and resolve CI artifacts on Silo.

Key layout: d<tier>/<repo_id>/<artifact_name>/<relative path>
tier ∈ {d1, d3, d14, d30}. Env: AWS_ACCESS_KEY_ID (SILO_ACCESS_KEY),
AWS_SECRET_ACCESS_KEY, SILO_ENDPOINT, SILO_BUCKET (default ci-artifacts).
Exit: 0 ok; 2 miss; 1 any other failure (distinct from miss).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


TIERS = frozenset({"d1", "d3", "d14", "d30"})
DEFAULT_BUCKET = "ci-artifacts"
S3_REGION = "us-east-1"
S3_SERVICE = "s3"
S3_TIMEOUT_SECONDS = 60
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
    """Build a path-style S3 client against SILO_ENDPOINT. No network until used.

    The client signs with SigV4 from the standard library only, so review jobs
    never download S3 SDK packages from PyPI at runtime.
    """

    access = (os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    endpoint = (os.environ.get("SILO_ENDPOINT") or "").strip()
    if not access:
        fail(MISSING_ACCESS_KEY)
    if not secret:
        fail(MISSING_SECRET_KEY)
    if not endpoint:
        fail("SILO_ENDPOINT 未设置")
    return S3Client(endpoint, access, secret, region=S3_REGION)


class S3Error(Exception):
    """S3 HTTP failure; ``response`` keeps the old SDK ClientError shape so the
    NoSuchKey/404/NoSuchBucket miss-mapping in cmd_get/cmd_list keeps working."""

    def __init__(self, code: str, message: str = "", status: int = 0):
        super().__init__(f"{code}: {message}" if message else code)
        self.response = {"Error": {"Code": code, "Message": message}}
        self.status = status


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_canonical_request(
    method: str,
    canonical_uri: str,
    canonical_query_string: str,
    signed_headers: list[tuple[str, str]],
    payload_hash: str,
) -> tuple[str, str]:
    """Return (canonical_request, signed_headers_names); names lowercased+sorted."""

    normalized = sorted((name.strip().lower(), value.strip()) for name, value in signed_headers)
    canonical_headers = "".join(f"{name}:{value}\n" for name, value in normalized)
    signed_names = ";".join(name for name, _ in normalized)
    # canonical_headers ends with "\n"; the extra one is the blank line the spec
    # requires between headers and SignedHeaders.
    canonical_request = (
        f"{method.upper()}\n{canonical_uri}\n{canonical_query_string}\n"
        f"{canonical_headers}\n{signed_names}\n{payload_hash}"
    )
    return canonical_request, signed_names


def _hmac(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def derive_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key = ("AWS4" + secret_key).encode("utf-8")
    for scope_part in (date_stamp, region, service, "aws4_request"):
        key = _hmac(key, scope_part)
    return key


def sigv4_authorization(
    *,
    method: str,
    canonical_uri: str,
    canonical_query_string: str,
    signed_headers: list[tuple[str, str]],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    amz_date: str,
    date_stamp: str,
) -> str:
    """Compute the SigV4 ``Authorization`` header value for one request."""

    canonical_request, signed_names = build_canonical_request(
        method, canonical_uri, canonical_query_string, signed_headers, payload_hash
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    hashed_request = sha256_hex(canonical_request.encode("utf-8"))
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{hashed_request}"
    signing_key = derive_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_names}, Signature={signature}"
    )


def encode_s3_path(bucket: str, key: str) -> str:
    """Path-style canonical URI: /<bucket>/<key>, segments percent-encoded."""

    return f"/{quote(bucket, safe='-_.~')}/{quote(key, safe='/-_.~')}"


def encode_query_string(params: list[tuple[str, str]]) -> str:
    return "&".join(
        f"{quote(name, safe='-_.~')}={quote(value, safe='-_.~')}"
        for name, value in sorted(params)
    )


def _xml_local_texts(root: ET.Element, local_name: str) -> list[str]:
    """Text of every descendant named ``local_name``, any XML namespace."""

    texts: list[str] = []
    for element in root.iter():
        tag = element.tag
        name = tag.rsplit("}", 1)[-1] if "}" in tag else tag
        if name == local_name:
            texts.append(element.text or "")
    return texts


class S3Client:
    """Minimal path-style S3 client over stdlib http.client with SigV4."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, region: str = S3_REGION):
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https"):
            authority = parsed.netloc.rsplit("@", 1)[-1]
            fail(f"SILO_ENDPOINT scheme must be http or https: {parsed.scheme}://{authority}")
        if not parsed.hostname:
            fail("SILO_ENDPOINT must contain a hostname")
        self.tls = parsed.scheme == "https"
        self.host = parsed.hostname
        default_port = 443 if self.tls else 80
        self.port = parsed.port or default_port
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        if self.port == default_port:
            self.host_header = self.host
        else:
            self.host_header = f"{self.host}:{self.port}"

    def _request(
        self, method: str, path: str, query_params: list[tuple[str, str]], body: bytes
    ) -> tuple[int, bytes]:
        payload_hash = sha256_hex(body)
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        canonical_qs = encode_query_string(query_params)
        signed_headers = [
            ("host", self.host_header),
            ("x-amz-content-sha256", payload_hash),
            ("x-amz-date", amz_date),
        ]
        authorization = sigv4_authorization(
            method=method,
            canonical_uri=path,
            canonical_query_string=canonical_qs,
            signed_headers=signed_headers,
            payload_hash=payload_hash,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=self.region,
            service=S3_SERVICE,
            amz_date=amz_date,
            date_stamp=date_stamp,
        )
        target = path if not canonical_qs else f"{path}?{canonical_qs}"
        headers = {
            "Host": self.host_header,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authorization,
        }
        if body:
            headers["Content-Length"] = str(len(body))  # explicit; MinIO closes keep-alive otherwise
        connection = (http.client.HTTPSConnection if self.tls else http.client.HTTPConnection)(
            self.host, self.port, timeout=S3_TIMEOUT_SECONDS)
        try:
            connection.request(method, target, body=body or None, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        except Exception as err:  # noqa: BLE001 — surfaced as exit 1 by callers
            fail(f"Silo request failed: {type(err).__name__}: {err}")
            raise AssertionError("unreachable")
        finally:
            connection.close()

    @staticmethod
    def _raise_for_status(status: int, data: bytes) -> S3Error:
        code = ""
        message = ""
        try:
            root = ET.fromstring(data)
            codes = _xml_local_texts(root, "Code")
            messages = _xml_local_texts(root, "Message")
            if codes:
                code = codes[0].strip()
            if messages:
                message = messages[0].strip()
        except ET.ParseError:
            pass
        code = code or str(status)
        if not message:
            message = data[:200].decode("utf-8", "replace").strip()
        return S3Error(code, message, status=status)

    def put_object(self, *, Bucket: str, Key: str, Body) -> dict:
        data = _body_bytes(Body)
        status, payload = self._request("PUT", encode_s3_path(Bucket, Key), [], data)
        if status != 200:
            raise self._raise_for_status(status, payload)
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        status, payload = self._request("GET", encode_s3_path(Bucket, Key), [], b"")
        if status != 200:
            raise self._raise_for_status(status, payload)
        return {"Body": BytesIO(payload)}

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", ContinuationToken=None, **_: object) -> dict:
        query = [("encoding-type", "url"), ("list-type", "2"), ("prefix", Prefix)]
        if ContinuationToken:
            query.append(("continuation-token", str(ContinuationToken)))
        status, payload = self._request("GET", f"/{quote(Bucket, safe='-_.~')}/", query, b"")
        if status != 200:
            raise self._raise_for_status(status, payload)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as err:
            raise S3Error("MalformedXML", f"invalid ListBucketResult: {err}", status=status)
        keys = [text for text in _xml_local_texts(root, "Key") if text]
        truncated = _xml_local_texts(root, "IsTruncated")
        is_truncated = bool(truncated) and truncated[0].strip().lower() == "true"
        tokens = _xml_local_texts(root, "NextContinuationToken")
        result: dict = {
            "Contents": [{"Key": unquote(key)} for key in keys],
            "IsTruncated": is_truncated,
            "KeyCount": len(keys),
        }
        if is_truncated and tokens and tokens[0]:
            result["NextContinuationToken"] = tokens[0]
        return result


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


def resolve_magicdns(endpoint: str, nameserver: str = "100.100.100.100", timeout: float = 15) -> tuple[str, str]:
    """Query MagicDNS for the A record of SILO_ENDPOINT's hostname.

    Returns ``(ip, host)``. Query failure is fail-loud: hosted runners without
    tailnet 100.100.100.100 must turn red, not skip S3.
    """

    import socket
    import struct

    host = urlparse(endpoint).hostname
    if not host:
        fail("SILO_ENDPOINT must contain a hostname")
    labels = host.rstrip(".").split(".")
    if any(not label or len(label) > 63 for label in labels):
        fail("SILO_ENDPOINT hostname contains an invalid label")
    question_name = b"".join(bytes((len(label),)) + label.encode("idna") for label in labels) + b"\0"
    query_id = b"\x01\x02"
    packet = query_id + struct.pack("!HHHHH", 0x0100, 1, 0, 0, 0) + question_name + struct.pack("!HH", 1, 1)

    def skip_name(data: bytes, offset: int) -> int:
        while True:
            if offset >= len(data):
                fail("MagicDNS returned a truncated name")
            length = data[offset]
            if length == 0:
                return offset + 1
            if length & 0xC0 == 0xC0:
                if offset + 1 >= len(data):
                    fail("MagicDNS returned a truncated pointer")
                return offset + 2
            if length & 0xC0:
                fail("MagicDNS returned an invalid name")
            offset += length + 1

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns:
            dns.settimeout(timeout)
            dns.sendto(packet, (nameserver, 53))
            response, _ = dns.recvfrom(4096)
    except OSError as err:
        fail(
            f"Silo MagicDNS lookup failed; SILO_ENDPOINT={endpoint}; "
            f"nameserver={nameserver} ({err})"
        )

    if len(response) < 12 or response[:2] != query_id:
        fail("MagicDNS returned an invalid response")
    _, flags, question_count, answer_count, _, _ = struct.unpack("!HHHHHH", response[:12])
    if flags & 0x000F:
        fail(f"MagicDNS returned DNS error {flags & 0x000F}")
    offset = 12
    for _ in range(question_count):
        offset = skip_name(response, offset)
        if offset + 4 > len(response):
            fail("MagicDNS returned a truncated question")
        offset += 4
    for _ in range(answer_count):
        offset = skip_name(response, offset)
        if offset + 10 > len(response):
            fail("MagicDNS returned a truncated answer")
        record_type, record_class, _, record_length = struct.unpack("!HHIH", response[offset : offset + 10])
        offset += 10
        if offset + record_length > len(response):
            fail("MagicDNS returned a truncated record")
        record = response[offset : offset + record_length]
        offset += record_length
        if record_type == 1 and record_class == 1 and record_length == 4:
            return socket.inet_ntoa(record), host
    fail(f"MagicDNS returned no A record for {host}")
    raise AssertionError("unreachable")


def cmd_magicdns(args: argparse.Namespace) -> int:
    ip, host = resolve_magicdns(args.endpoint, args.nameserver)
    print(f"{ip} {host}")
    return EXIT_OK


def cmd_put(args: argparse.Namespace) -> int:
    raw_files = list(args.file or [])
    if not raw_files:
        fail("put requires at least one --file")
    if len(raw_files) == 1:
        path = Path(raw_files[0])
        if not path.is_file():
            fail(f"put source is not a file: {path}")
        object_name = args.object_name if args.object_name else path.name
        files = [(path, object_name)]
    else:
        files: list[tuple[Path, str]] = []
        for raw in raw_files:
            path = Path(raw)
            if not path.is_file():
                print(f"put skipping missing source file: {path}", file=sys.stderr)
                continue
            files.append((path, path.name))
        if not files:
            fail("put requires at least one existing --file: all sources missing")
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
                resp = getattr(err, "response", None)
                if isinstance(resp, dict):
                    error_dict = resp.get("Error")
                    code = error_dict.get("Code") if isinstance(error_dict, dict) else None
                    if code in {"NoSuchKey", "404", "NoSuchBucket"}:
                        fail(f"Silo object not found: {key}", EXIT_NOT_FOUND)
                    if code:
                        fail(f"Silo get failed ({code}): {key}")
                    fail(f"Silo get failed: {key}")
                fail(f"Silo get failed: {err}")
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


def _layout_suffix(key: str) -> tuple[str, str]:
    """Return (artifact_name, relative_path) for a canonical four-segment key."""

    parts = key.split("/", 3)
    if len(parts) != 4 or not all(parts):
        fail(f"object key {key!r} is not tier/repo_id/artifact_name/path")
    return parts[2], parts[3]


def cmd_list(args: argparse.Namespace) -> int:
    prefix = (args.prefix or "").strip()
    if prefix:
        listing_prefix = prefix
    else:
        if not args.tier or not args.repo_id:
            fail("list requires --prefix or both --tier and --repo-id")
        listing_prefix = f"{args.tier}/{args.repo_id}/"
        name_prefix = (args.name_prefix or "").strip()
        if name_prefix:
            listing_prefix = f"{args.tier}/{args.repo_id}/{name_prefix}"
    client = connect()
    bucket = bucket_name()
    keys = list_keys(client, bucket, listing_prefix)
    dest = Path(args.dest) if args.dest else None
    try:
        for key in keys:
            print(key)
            if dest is None:
                continue
            artifact_name, relative = _layout_suffix(key)
            try:
                response = client.get_object(Bucket=bucket, Key=key)
            except Exception as err:  # noqa: BLE001
                resp = getattr(err, "response", None)
                if isinstance(resp, dict):
                    error_dict = resp.get("Error")
                    code = error_dict.get("Code") if isinstance(error_dict, dict) else None
                    if code in {"NoSuchKey", "404", "NoSuchBucket"}:
                        fail(f"Silo object not found: {key}", EXIT_NOT_FOUND)
                    if code:
                        fail(f"Silo list download failed ({code}): {key}")
                    fail(f"Silo list download failed: {key}")
                fail(f"Silo list download failed: {err}")
            body = _body_bytes(response.get("Body") if isinstance(response, dict) else response)
            target = dest / artifact_name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    except SystemExit:
        raise
    except Exception as err:  # noqa: BLE001
        fail(f"Silo list failed: {type(err).__name__}: {err}")
    return EXIT_OK


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

    magicdns = sub.add_parser("magicdns", help="resolve SILO_ENDPOINT via MagicDNS (100.100.100.100)")
    magicdns.add_argument("--endpoint", required=True)
    magicdns.add_argument("--nameserver", default="100.100.100.100")
    magicdns.set_defaults(func=cmd_magicdns)

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

    listing = sub.add_parser(
        "list",
        help="print object keys under a prefix; optionally download each key",
    )
    listing.add_argument("--prefix", default="", help="raw S3 prefix; overrides --tier/--repo-id/--name-prefix")
    listing.add_argument("--tier", default=None, choices=sorted(TIERS))
    listing.add_argument("--repo-id", default="")
    listing.add_argument("--name-prefix", default="", help="artifact_name prefix after tier/repo_id/")
    listing.add_argument("--dest", default="", help="if set, download each key to dest/artifact_name/relative")
    listing.set_defaults(func=cmd_list)

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
