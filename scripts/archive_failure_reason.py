#!/usr/bin/env python3
"""Extract a closed-set Silo reason for deferred archive warnings."""

import json
import re
import sys

_PREFIX = b"SILO-REQUEST-FAILED "
_SAFE = re.compile(r"[A-Za-z_]+\Z")
_OLD = re.compile(rb"s3_error_code=([A-Za-z_]+)")


def extract_reason(output: bytes) -> str:
    for line in output.splitlines():
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
