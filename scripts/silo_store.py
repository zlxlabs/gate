#!/usr/bin/env python3
"""Put, get, list, and resolve CI artifacts on Silo.

Key layout: d<tier>/<repo_id>/<artifact_name>/<relative path>
or archive/<repo_id>/<artifact_name>/<relative path> for per-run review evidence.
tier ∈ {d1, d3, d14, d30, archive}. Env: AWS_ACCESS_KEY_ID (SILO_ACCESS_KEY),
AWS_SECRET_ACCESS_KEY, SILO_ENDPOINT, SILO_BUCKET (default ci-artifacts).
Exit: 0 ok; 2 miss; 1 any other failure (distinct from miss).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


ARCHIVE_TIER = "archive"
TIERS = frozenset({"d1", "d3", "d14", "d30", ARCHIVE_TIER})
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
ARCHIVE_MANIFEST_FILENAME = "archive-manifest-v1.json"
REVIEW_RECORD_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
REVIEW_RECORD_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")


def _archive_single_file_name_pattern(repo_id: str) -> tuple[tuple[str, str], ...]:
    return (
        (
            rf"codex-review-ledger-v2-{re.escape(repo_id)}-[0-9a-f]{{40}}-[0-9]+-[0-9]+",
            "ledger.jsonl",
        ),
        (
            rf"primary-audit-v2-{re.escape(repo_id)}-[0-9a-f]{{40}}-[0-9]+-[0-9]+",
            "primary-review-audit.json",
        ),
        (
            rf"gate-terminal-v1-{re.escape(repo_id)}-[0-9a-f]{{40}}-[0-9]+-[0-9]+",
            "gate-terminal.json",
        ),
        (
            rf"gate-status-panel-delivery-v1-{re.escape(repo_id)}-[0-9a-f]{{40}}-[0-9]+-[0-9]+",
            "gate-status-panel-delivery.json",
        ),
    )


def _archive_directory_name_patterns(repo_id: str) -> tuple[str, ...]:
    return (
        rf"primary-review-raw-evidence-v1-{re.escape(repo_id)}-[0-9a-f]{{40}}-[0-9]+-[0-9]+",
        rf"primary-review-diagnostics-v2-{re.escape(repo_id)}-[0-9a-f]{{40}}-[0-9]+-[0-9]+",
        r"advisory-event-(?!__none__-)[A-Za-z0-9_.-]+-[0-9]+-[0-9]+",
        rf"gate-convergence-receipt-v1-{re.escape(repo_id)}-[0-9a-f]{{40}}-[0-9]+-[0-9]+",
    )


def is_archive_directory_name(repo_id: str, artifact_name: str) -> bool:
    """Return whether a Silo archive name is one of the four per-run directories."""

    return any(
        re.fullmatch(pattern, artifact_name)
        for pattern in _archive_directory_name_patterns(repo_id)
    )


def fail(message: str, code: int = EXIT_ERROR) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def bucket_name() -> str:
    value = (os.environ.get("SILO_BUCKET") or DEFAULT_BUCKET).strip()
    if not value:
        fail("SILO_BUCKET is empty")
    return value


def build_key(tier: str, repo_id: str, artifact_name: str, relative_path: str) -> str:
    """Return a canonical key; archive keeps per-run raw review evidence bytes."""

    if tier not in TIERS:
        fail(f"tier must be one of {sorted(TIERS)}; got {tier!r}")
    if not repo_id or "/" in repo_id:
        fail("repo_id must be a non-empty path segment")
    if not artifact_name or "/" in artifact_name:
        fail("artifact_name must be a non-empty path segment")
    rel = relative_path.replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        fail(f"relative path is not a safe object suffix: {relative_path!r}")
    if tier == ARCHIVE_TIER:
        allowed_single = any(
            relative_path == allowed_suffix and re.fullmatch(name_pattern, artifact_name)
            for name_pattern, allowed_suffix in _archive_single_file_name_pattern(repo_id)
        )
        allowed_directory = is_archive_directory_name(repo_id, artifact_name)
        if not (allowed_single or allowed_directory):
            fail(
                "archive tier only accepts per-run review ledger, audit, terminal, diagnostics, raw-evidence, advisory, convergence, or status-panel artifacts"
            )
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
        super().__init__(f"{status} {code}" if status else code)
        self.response = {"Error": {"Code": code, "Message": message}}
        self.status = status


_REQUEST_SEQ = 0


def _diag_ascii(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(ch for ch in value if 32 <= ord(ch) <= 126)
    return cleaned[:128] if cleaned else None


def _s3_error_code_from_body(data: bytes) -> str | None:
    try:
        codes = _xml_local_texts(ET.fromstring(data), "Code") if data else []
    except ET.ParseError:
        return None
    return _diag_ascii(codes[0].strip()) if codes else None


def _emit_silo_request_failed(event: dict, response) -> None:
    getter = response.getheader if response is not None else lambda *_a, **_k: None
    event["resp_server"] = _diag_ascii(getter("Server"))
    event["resp_amz_request_id"] = _diag_ascii(getter("x-amz-request-id"))
    event["resp_amz_id_2"] = _diag_ascii(getter("x-amz-id-2"))
    event["resp_content_length"] = _diag_ascii(getter("Content-Length"))
    print(f"SILO-REQUEST-FAILED {json.dumps(event, separators=(',', ':'))}", file=sys.stderr)


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
        declared_content_length = (
            int(headers["Content-Length"]) if "Content-Length" in headers else None
        )
        global _REQUEST_SEQ
        _REQUEST_SEQ += 1
        request_seq = _REQUEST_SEQ
        phase = "send"
        request_send_completed = False
        http_status = None
        response = None
        payload = b""
        exception_type = None
        started = time.monotonic()
        connection = (http.client.HTTPSConnection if self.tls else http.client.HTTPConnection)(
            self.host, self.port, timeout=S3_TIMEOUT_SECONDS)
        try:
            connection.request(method, target, body=body or None, headers=headers)
            request_send_completed = True
            phase = "recv_headers"
            response = connection.getresponse()
            http_status = response.status
            phase = "recv_body"
            payload = response.read()
            phase = "status"
            return http_status, payload
        except Exception as err:  # noqa: BLE001 — surfaced as exit 1 by callers
            exception_type = type(err).__name__
            fail(f"Silo request failed: {exception_type}")
            raise AssertionError("unreachable")
        finally:
            connection.close()
            if exception_type is not None or http_status != 200:
                _emit_silo_request_failed({
                    "event": "silo_request_failed",
                    "schema": 1,
                    "method": method,
                    "phase": phase,
                    "body_len": len(body),
                    "declared_content_length": declared_content_length,
                    "request_send_completed": request_send_completed,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "client_timeout_s": S3_TIMEOUT_SECONDS,
                    "http_status": http_status,
                    "s3_error_code": _s3_error_code_from_body(payload),
                    "exception_type": exception_type,
                    "request_seq": request_seq,
                }, response)

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


def _validate_archive_directory_files(
    repo_id: str,
    artifact_name: str,
    files: list[tuple[Path, str, bytes]],
) -> None:
    """Reject incomplete primary and convergence d3 directory snapshots."""

    by_path = {relative: payload for _, relative, payload in files}
    if artifact_name.startswith("primary-review-diagnostics-v2-"):
        manifest_bytes = by_path.get("manifest.json")
        if manifest_bytes is None:
            fail("archive primary diagnostics are incomplete: manifest.json is missing")
        try:
            manifest = json.loads(manifest_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            fail("archive primary diagnostics are incomplete: manifest.json is invalid")
        if not isinstance(manifest, dict):
            fail("archive primary diagnostics are incomplete: manifest.json is invalid")
        suffix = artifact_name.rsplit("-", 1)[-1]
        raw_pattern = re.compile(r"^attempt-[0-9]+-[A-Za-z0-9_.-]+-raw-output\.txt$")
        raw_by_name = {
            relative: payload
            for _, relative, payload in files
            if "/" not in relative and raw_pattern.fullmatch(relative)
        }
        manifest_files = manifest.get("files")
        if (
            manifest.get("schema_version") != 1
            or manifest.get("attempt") != int(suffix)
            or not isinstance(manifest_files, list)
            or manifest.get("file_count") != len(manifest_files)
        ):
            fail("archive primary diagnostics are incomplete: manifest metadata does not match this attempt")
        expected_raw: dict[str, int] = {}
        for entry in manifest_files:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("name"), str)
                or not raw_pattern.fullmatch(entry["name"])
                or not isinstance(entry.get("bytes"), int)
                or entry["bytes"] < 0
                or entry["name"] in expected_raw
            ):
                fail("archive primary diagnostics are incomplete: manifest file entry is invalid")
            expected_raw[entry["name"]] = entry["bytes"]
        if set(expected_raw) != set(raw_by_name) or any(
            expected_raw[name] != len(raw_by_name[name]) for name in expected_raw
        ):
            fail("archive primary diagnostics are incomplete: raw output inventory differs from manifest.json")
        top_level_other = sum(
            1 for _, relative, _ in files
            if "/" not in relative and relative != "manifest.json" and relative not in raw_by_name
        )
        if manifest.get("other_file_count") != top_level_other:
            fail("archive primary diagnostics are incomplete: other file count differs from manifest.json")
        return

    if artifact_name.startswith("gate-convergence-receipt-v1-"):
        if set(by_path) != {"convergence-receipt.json"}:
            fail("archive convergence directory is incomplete: expected only convergence-receipt.json")


def _archive_prefix_is_empty(client, bucket: str, repo_id: str, artifact_name: str) -> None:
    prefix = artifact_prefix(ARCHIVE_TIER, repo_id, artifact_name) + "/"
    existing = list_keys(client, bucket, prefix)
    if existing:
        fail(f"Silo archive destination already exists; refusing to overwrite: {prefix}")


def _put_archive_bytes_and_verify(client, bucket: str, key: str, payload: bytes) -> None:
    expected_sha = sha256_hex(payload)
    try:
        client.put_object(Bucket=bucket, Key=key, Body=payload)
        response = client.get_object(Bucket=bucket, Key=key)
        readback = _body_bytes(response["Body"])
    except SystemExit:
        raise
    except Exception as err:  # noqa: BLE001 — preserve the failing Silo boundary
        fail(f"Silo archive PUT/GET failed for {key}: {type(err).__name__}")
    if len(readback) != len(payload) or sha256_hex(readback) != expected_sha:
        fail(f"Silo archive readback hash mismatch for {key}")
    print(key)


def review_record_archive_key(record: dict[str, object]) -> str:
    """Build the review-record/v1 Silo key after validating stable identity fields."""
    if record.get("schema_version") != "review-record/v1":
        fail("review-record/v1 file has an unsupported schema_version")
    repo = record.get("repo")
    parts = repo.split("/") if isinstance(repo, str) else []
    if len(parts) != 2 or any(
        part in {".", ".."} or not REVIEW_RECORD_REPO_PART.fullmatch(part) for part in parts
    ):
        fail("review-record/v1 repo must be a safe owner/repo path")
    pr = record.get("pr")
    if pr is None:
        pr_segment = "none"
    elif type(pr) is int and pr > 0:
        pr_segment = str(pr)
    else:
        fail("review-record/v1 pr must be a positive integer or null")
    head_sha = record.get("head_sha")
    if not isinstance(head_sha, str) or not REVIEW_RECORD_HEAD_SHA.fullmatch(head_sha):
        fail("review-record/v1 head_sha must be 40 lowercase hexadecimal characters")
    source = record.get("source")
    if not isinstance(source, str) or source not in {"ci", "pre_seal", "eval"}:
        fail("review-record/v1 source must be ci, pre_seal, or eval")
    run_id, attempt = record.get("run_id"), record.get("attempt")
    if type(run_id) is not int or run_id < 1:
        fail("review-record/v1 run_id must be a positive integer")
    if type(attempt) is not int or attempt < 1:
        fail("review-record/v1 attempt must be a positive integer")
    owner, repository = parts
    return (
        f"archive/review-record/v1/{owner}/{repository}/{pr_segment}/{head_sha}/"
        f"{source}-{run_id}-{attempt}.json"
    )


def cmd_put_review_record(args: argparse.Namespace) -> int:
    """Upload original review-record/v1 bytes once and verify the Silo readback."""
    path = Path(args.file)
    if not path.is_file():
        fail(f"put-review-record source is not a file: {path}")
    try:
        payload = path.read_bytes()
        record = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        fail(f"review-record/v1 source file is not readable UTF-8 JSON: {type(err).__name__}")
    if not isinstance(record, dict):
        fail("review-record/v1 file must contain a JSON object")
    key = review_record_archive_key(record)
    client = connect()
    bucket = bucket_name()
    if key in list_keys(client, bucket, key):
        fail(f"Silo review-record/v1 destination already exists; refusing to overwrite: {key}")
    _put_archive_bytes_and_verify(client, bucket, key, payload)
    return EXIT_OK


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
    if args.tier == ARCHIVE_TIER and len(raw_files) != 1:
        fail("archive tier put requires exactly one allowlisted review evidence file")
    if args.tier == ARCHIVE_TIER and is_archive_directory_name(args.repo_id, args.name):
        fail("archive tier put does not accept files from directory artifacts")
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
    keyed_files = [
        (path, build_key(args.tier, args.repo_id, args.name, relative))
        for path, relative in files
    ]
    client = connect()
    bucket = bucket_name()
    if args.tier == ARCHIVE_TIER and args.name.startswith("gate-status-panel-delivery-v1-"):
        _archive_prefix_is_empty(client, bucket, args.repo_id, args.name)
    try:
        for path, key in keyed_files:
            payload = path.read_bytes()
            if args.tier == ARCHIVE_TIER and args.name.startswith("gate-status-panel-delivery-v1-"):
                _put_archive_bytes_and_verify(client, bucket, key, payload)
            else:
                client.put_object(Bucket=bucket, Key=key, Body=payload)
                print(key)
    except SystemExit:
        raise
    except Exception as err:  # noqa: BLE001
        fail(f"Silo put failed: {type(err).__name__}: {err}")
    return EXIT_OK


def cmd_put_dir(args: argparse.Namespace) -> int:
    if args.tier == ARCHIVE_TIER and not is_archive_directory_name(args.repo_id, args.name):
        fail("archive tier put-dir only accepts per-run primary diagnostics, raw evidence, advisory, or convergence directories")
    directory = Path(args.dir)
    files = list(iter_dir_files(directory))
    if not files:
        if args.empty == "skip":
            print(
                f"::notice::silo put-dir skipped: directory empty or missing ({directory})"
            )
            return EXIT_OK
        fail(f"put-dir source is empty or missing: {directory}")
    if args.tier == ARCHIVE_TIER:
        if any(path.is_symlink() for path, _ in files):
            fail("archive put-dir does not accept symlink files")
        if any(relative == ARCHIVE_MANIFEST_FILENAME for _, relative in files):
            fail(f"archive put-dir source collides with generated {ARCHIVE_MANIFEST_FILENAME}")
        archived_files = [(path, relative, path.read_bytes()) for path, relative in files]
        _validate_archive_directory_files(args.repo_id, args.name, archived_files)
        manifest = {
            "schema_version": 1,
            "repository_id": args.repo_id,
            "artifact_name": args.name,
            "files": [
                {"path": relative, "bytes": len(payload), "sha256": sha256_hex(payload)}
                for _, relative, payload in archived_files
            ],
        }
        manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        archive_files = [
            (relative, payload)
            for _, relative, payload in archived_files
        ]
        archive_files.append((ARCHIVE_MANIFEST_FILENAME, manifest_bytes))
        keyed_files = [
            (relative, payload, build_key(args.tier, args.repo_id, args.name, relative))
            for relative, payload in archive_files
        ]
        client = connect()
        bucket = bucket_name()
        _archive_prefix_is_empty(client, bucket, args.repo_id, args.name)
        try:
            for _, payload, key in keyed_files:
                _put_archive_bytes_and_verify(client, bucket, key, payload)
        except SystemExit:
            raise
        except Exception as err:  # noqa: BLE001 — surface failed Silo archive operations
            fail(f"Silo archive put-dir failed: {type(err).__name__}")
        return EXIT_OK
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

    put_record = sub.add_parser("put-review-record", help="append one review-record/v1 to its versioned archive key")
    put_record.add_argument("--file", required=True, help="source review-record/v1 JSON; original bytes are preserved")
    put_record.set_defaults(func=cmd_put_review_record)

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
