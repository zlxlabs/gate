#!/usr/bin/env python3
"""Extract a closed-set Silo reason for deferred archive warnings."""

import json
import re
import sys

_PREFIX = b"SILO-REQUEST-FAILED "
_SAFE = re.compile(r"[A-Za-z_]+\Z")
_OLD = re.compile(rb"s3_error_code=([A-Za-z_]+)")
# silo_store.py connect() 的缺凭据 fail() 文案（MISSING_ACCESS_KEY / MISSING_SECRET_KEY）。
# 缺凭据与 S3 错误不同因，归入既有 reason missing_credentials；文案漂移由真 stderr 契约测试锁死。
_MISSING_CREDENTIALS = tuple(
    text.encode("utf-8") for text in ("SILO_ACCESS_KEY 未传入", "SILO_SECRET_KEY 未传入")
)


def extract_reason(output: bytes) -> str:
    for line in output.splitlines():
        if any(fragment in line for fragment in _MISSING_CREDENTIALS):
            return "missing_credentials"
        if line.startswith(_PREFIX):
            try:
                event = json.loads(line[len(_PREFIX) :])
            except (UnicodeDecodeError, json.JSONDecodeError):
                event = None
            if isinstance(event, dict):
                code = event.get("s3_error_code")
                if isinstance(code, str) and _SAFE.fullmatch(code):
                    return code
        old = _OLD.search(line)
        if old:
            return old.group(1).decode("ascii")
    return "unclassified"


if __name__ == "__main__":
    print(extract_reason(sys.stdin.buffer.read()))
