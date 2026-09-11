#!/usr/bin/env python3
"""Classify whether a pull request's changed paths warrant a model review.

Single source of truth for `.github/workflows/gate-v2.yml` and
`.github/workflows/gate-shadow-v2.yml`.

Reads GitHub compare API JSON (`{ "files": [ { "filename": ... }, ... ],
"truncated": bool }`) or Pull Request Files API JSON (an array of objects
with `filename`, or a `--paginate --slurp` array of such pages). Writes
`review_expected=true|false` to stdout and to `--github-output` /
`$GITHUB_OUTPUT`.

`review_expected=false` if and only if the path set is non-empty and every
path is in the effective exempt set: the fleet default
`{retro/acceptance-log.jsonl}` union the caller-declared
`REVIEW_EXEMPT_PATHS` entries. Empty lists, any other path, truncated
compare listings (`truncated: true` or `--has-next-page`), unusable input,
and an illegal declaration all yield `true` (fail closed: still review).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence


FLEET_DEFAULT_EXEMPT: frozenset[str] = frozenset({"retro/acceptance-log.jsonl"})
REVIEW_EXEMPT_PATHS_ENV = "REVIEW_EXEMPT_PATHS"
_DIR_PREFIX_SUFFIX = "/**"
_WILDCARD_CHARS = frozenset("*?[")
_WARNING_ENTRY_MAX = 200
_INVALID_ENTRY_WARNING = (
    "classify_pr_paths: invalid review_exempt_paths entry: "
)


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


def _is_legal_exempt_entry(entry: str) -> bool:
    if entry.startswith("/") or entry.startswith("./"):
        return False
    if entry.startswith(".github/"):
        return False
    if any(part == ".." for part in entry.split("/")):
        return False
    if entry.endswith(_DIR_PREFIX_SUFFIX):
        directory = entry[: -len(_DIR_PREFIX_SUFFIX)]
        if not directory:
            return False
        return not any(ch in _WILDCARD_CHARS for ch in directory)
    return not any(ch in _WILDCARD_CHARS for ch in entry)


def parse_exempt_declaration(text: str) -> tuple[list[str], str | None]:
    """Return (legal entries, first illegal entry or None).

    Illegal entries void the whole declaration: the list is empty.
    Blank lines and surrounding whitespace are ignored.
    """
    entries: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not _is_legal_exempt_entry(line):
            return [], line
        entries.append(line)
    return entries, None


def _path_matches_entry(filename: str, entry: str) -> bool:
    if entry.endswith(_DIR_PREFIX_SUFFIX):
        directory = entry[: -len(_DIR_PREFIX_SUFFIX)]
        return filename.startswith(directory + "/")
    return filename == entry


def _path_is_exempt(filename: str, exempt: Sequence[str]) -> bool:
    for entry in (*FLEET_DEFAULT_EXEMPT, *exempt):
        if _path_matches_entry(filename, entry):
            return True
    return False


def review_expected(
    filenames: list[str],
    *,
    exempt: list[str] = (),
    has_next_page: bool = False,
) -> bool:
    """True unless every changed path is in the effective exempt set."""
    if has_next_page or not filenames:
        return True
    return not all(_path_is_exempt(name, exempt) for name in filenames)


def _sanitize_warning_entry(entry: str) -> str:
    display = " ".join(entry.split())
    if len(display) > _WARNING_ENTRY_MAX:
        return display[:_WARNING_ENTRY_MAX]
    return display


def _emit_invalid_entry_warning(entry: str) -> None:
    display = _sanitize_warning_entry(entry)
    sys.stderr.write(f"::warning::{_INVALID_ENTRY_WARNING}{display}\n")
    sys.stderr.flush()


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
    declaration = os.environ.get(REVIEW_EXEMPT_PATHS_ENV) or ""
    exempt, illegal = parse_exempt_declaration(declaration)
    if illegal is not None:
        _emit_invalid_entry_warning(illegal)
    try:
        if args.json_file:
            raw = Path(args.json_file).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
        payload = json.loads(raw)
        filenames, truncated = parse_listing(payload)
        if illegal is not None:
            expected = True
        else:
            expected = review_expected(
                filenames,
                exempt=exempt,
                has_next_page=args.has_next_page or truncated,
            )
    except (OSError, json.JSONDecodeError, ClassifyError, UnicodeError):
        _write_result("true", github_output)
        return 1
    _write_result("true" if expected else "false", github_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
