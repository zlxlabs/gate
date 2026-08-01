#!/usr/bin/env python3
"""Reject floating references to this repository from live workflow code."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


FULL_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
USES_LINE_PREFIX = re.compile(r"^(?:uses:|-\s+uses:)(?P<value>.*)$")


@dataclass(frozen=True)
class InternalUseViolation:
    """One internal action/workflow reference that is not pinned immutably."""

    file_path: str
    line_number: int
    ref: str


def discover_use_files(repo_root: Path, include_templates: bool) -> list[Path]:
    """Find live workflows and composite action metadata under a repository root."""

    roots_and_patterns = [
        (repo_root / ".github" / "workflows", ("*.yml", "*.yaml")),
        (repo_root / ".github" / "actions", ("action.yml", "action.yaml")),
    ]
    if include_templates:
        roots_and_patterns.append(
            (repo_root / "templates", ("*.yml", "*.yaml"))
        )

    paths: set[Path] = set()
    for directory, patterns in roots_and_patterns:
        if not directory.is_dir():
            continue
        for pattern in patterns:
            paths.update(path for path in directory.rglob(pattern) if path.is_file())
    return sorted(paths)


def parse_internal_use_value(line: str) -> tuple[str, str] | None:
    """Extract an internal `uses:` value and its ref from one YAML line."""

    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    match = USES_LINE_PREFIX.match(stripped)
    if match is None:
        return None

    value = match.group("value").split("#", 1)[0].strip().strip("'\"")
    if not value.startswith("zlxlabs/gate/"):
        return None
    if "@" not in value:
        return value, "<missing>"
    return value, value.rsplit("@", 1)[1]


def find_pinned_use_violations(
    repo_root: Path, include_templates: bool = False
) -> list[InternalUseViolation]:
    """Return internal uses refs that are neither relative nor full commit SHAs."""

    violations: list[InternalUseViolation] = []
    for file_path in discover_use_files(repo_root, include_templates):
        relative_path = file_path.relative_to(repo_root).as_posix()
        for line_number, line in enumerate(
            file_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            parsed = parse_internal_use_value(line)
            if parsed is None:
                continue
            _, ref = parsed
            if not FULL_COMMIT_SHA.fullmatch(ref):
                violations.append(
                    InternalUseViolation(relative_path, line_number, ref)
                )
    return violations


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """Parse the optional repository root and template-scan switch."""

    parser = argparse.ArgumentParser(
        description=(
            "Check internal zlxlabs/gate action/workflow uses refs in live "
            "workflows and composite action metadata."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to scan (default: this script's repository)",
    )
    parser.add_argument(
        "--include-templates",
        action="store_true",
        help="also scan templates/; disabled by default because templates use onboarding placeholders",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the internal uses pin check and return a shell-friendly exit code."""

    args = parse_arguments(argv)
    repo_root = args.root.resolve()
    if not repo_root.is_dir():
        print(f"error: scan root is not a directory: {repo_root}", file=sys.stderr)
        return 2

    files = discover_use_files(repo_root, args.include_templates)
    violations = find_pinned_use_violations(repo_root, args.include_templates)
    if violations:
        for violation in violations:
            print(
                f"{violation.file_path}:{violation.line_number}:{violation.ref}: "
                "internal uses must use a full 40-hex commit SHA"
            )
        print(
            f"FAIL: found {len(violations)} floating or malformed internal uses "
            f"in {len(files)} scanned file(s)",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: checked {len(files)} live workflow/action metadata file(s); "
        "all internal uses are pinned"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
