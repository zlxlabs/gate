#!/usr/bin/env python3
"""Classify whether a pull request's changed paths warrant a model review.

Single source of truth for `.github/workflows/gate-v2.yml` and
`.github/workflows/gate-shadow-v2.yml`.

Reads GitHub compare API JSON (`{ "files": [ { "filename": ... }, ... ],
"truncated": bool }`) or Pull Request Files API JSON (an array of objects
with `filename`, or a `--paginate --slurp` array of such pages). Writes
`review_expected=true|false` to stdout and to `--github-output` /
`$GITHUB_OUTPUT`.

`review_expected=false` if and only if the path set is exactly
`{retro/acceptance-log.jsonl}`. Empty lists, any other path, truncated
compare listings (`truncated: true` or `--has-next-page`), and unusable
input all yield `true` (fail closed: still review).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


LEDGER_ONLY_PATH = "retro/acceptance-log.jsonl"


class ClassifyError(ValueError):
    """Input is not usable GitHub compare or pulls-files JSON."""


def _filenames_from_file_objects(items: list[Any]) -> list[str]:
    filenames: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise ClassifyError("files API item is not an object")
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename:
            raise ClassifyError("files API item is missing a string filename")
        filenames.append(filename)
    return filenames


def parse_listing(payload: Any) -> tuple[list[str], bool]:
    """Return filenames and truncation from compare or files API JSON.

    Compare objects are `{ "files": [ { "filename": ... }, ... ], "truncated":
    bool }`. Files API payloads are a JSON array of file objects, or an array
    of such arrays (gh `--paginate --slurp`). Anything else raises
    ClassifyError.
    """
    if isinstance(payload, dict):
        files = payload.get("files")
        if not isinstance(files, list):
            raise ClassifyError("compare payload files must be an array")
        truncated_field = payload.get("truncated", False)
        if truncated_field not in (True, False):
            raise ClassifyError("compare payload truncated must be a boolean")
        return _filenames_from_file_objects(files), bool(truncated_field)
    if not isinstance(payload, list):
        raise ClassifyError("files API payload must be a JSON array")
    if payload and all(isinstance(item, list) for item in payload):
        filenames: list[str] = []
        for page in payload:
            filenames.extend(_filenames_from_file_objects(page))
        return filenames, False
    return _filenames_from_file_objects(payload), False


def review_expected(filenames: list[str], *, has_next_page: bool = False) -> bool:
    """True unless the path set is exactly the acceptance ledger file."""
    if has_next_page:
        return True
    return set(filenames) != {LEDGER_ONLY_PATH}


def _write_result(value: str, github_output: Path | None) -> None:
    line = f"review_expected={value}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    if github_output is None:
        return
    github_output.parent.mkdir(parents=True, exist_ok=True)
    with github_output.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify whether model review is expected from PR files JSON.",
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        help="Path to GitHub pulls files API JSON. Defaults to stdin.",
    )
    parser.add_argument(
        "--github-output",
        default=os.environ.get("GITHUB_OUTPUT") or None,
        help="Append review_expected= to this file (defaults to $GITHUB_OUTPUT).",
    )
    parser.add_argument(
        "--has-next-page",
        action="store_true",
        help="Listing was truncated (Link rel=next remained). Forces review.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    github_output = Path(args.github_output) if args.github_output else None
    try:
        if args.json_file:
            raw = Path(args.json_file).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
        payload = json.loads(raw)
        filenames, truncated = parse_listing(payload)
        expected = review_expected(
            filenames, has_next_page=args.has_next_page or truncated
        )
    except (OSError, json.JSONDecodeError, ClassifyError, UnicodeError):
        _write_result("true", github_output)
        return 1
    _write_result("true" if expected else "false", github_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
