#!/usr/bin/env python3
"""Remove runner-local values from text before publishing it outside Actions.

The patterns are intentionally explicit. Review output may contain commit IDs,
repository-relative paths, and other long identifiers that a generic token
heuristic would damage. Runtime values shorter than three characters are
reported but left unchanged because replacing them would corrupt normal prose.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping


REDACTION_PREFIX = "[REDACTED:"

_RUNTIME_CATEGORY_BY_KEY = {
    "RUNNER_NAME": "RUNNER_NAME",
    "RUNNER": "RUNNER_NAME",
    "HOSTNAME": "HOSTNAME",
    "HOST": "HOSTNAME",
    "USER": "USERNAME",
    "USERNAME": "USERNAME",
    "LOGNAME": "USERNAME",
    "HOME": "ABSOLUTE_PATH",
    "RUNNER_TEMP": "ABSOLUTE_PATH",
    "RUNNER_WORKSPACE": "ABSOLUTE_PATH",
    "GITHUB_WORKSPACE": "ABSOLUTE_PATH",
}
_CLI_RUNTIME_KEYS = tuple(_RUNTIME_CATEGORY_BY_KEY)
_IPV4_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"

_STATIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PRIVATE_IP",
        re.compile(
            rf"(?<![\w.])(?:10(?:\.{_IPV4_OCTET}){{3}}|"
            rf"192\.168(?:\.{_IPV4_OCTET}){{2}}|"
            rf"172\.(?:1[6-9]|2\d|3[0-1])(?:\.{_IPV4_OCTET}){{2}})"
            r"(?![\d.])"
        ),
    ),
    (
        "AUTH_PATH",
        re.compile(
            r"(?:(?<=file://)|(?<![/\w:]))/opt/review-auth(?:/[^\s`'\"<>()[\]{};,]+)*"
        ),
    ),
    (
        "ABSOLUTE_PATH",
        re.compile(
            r"(?:(?<=file://)|(?<![/\w:]))/(?:home|Users|root|runner/_work|workspaces|"
            r"private/var|var/folders|tmp|mnt|opt|srv|etc|usr/local|var/log|data)/"
            r"[^\s`'\"<>()[\]{};,]+"
        ),
    ),
    (
        "RUNNER_NAME",
        re.compile(r"(?<![\w])gatehub-[a-zA-Z0-9][a-zA-Z0-9_-]*-slot-[0-9]+(?![\w])"),
    ),
    (
        "TOKEN",
        re.compile(
            r"(?i)(?:\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|"
            r"xox[baprs]-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b|"
            r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}|"
            r"\b(?:token|api[_-]?key|secret|password)\s*[:=]\s*['\"]?"
            r"[A-Za-z0-9._~+/=-]{20,})"
        ),
    ),
)


class ScrubError(RuntimeError):
    """Raised when an outbound scrub result violates its publication contract."""


def _marker(category: str) -> str:
    return f"{REDACTION_PREFIX}{category}]"


def _runtime_patterns(runtime_values: Mapping[str, str]) -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for key, value in sorted(runtime_values.items(), key=lambda item: len(item[1]), reverse=True):
        if not isinstance(value, str) or len(value) < 3:
            continue
        category = _RUNTIME_CATEGORY_BY_KEY.get(key.upper(), "RUNTIME_VALUE")
        patterns.append(
            (
                category,
                re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"),
            )
        )
    return patterns


def _short_runtime_value_keys(runtime_values: Mapping[str, str]) -> list[str]:
    return [
        key
        for key, value in runtime_values.items()
        if isinstance(value, str) and 0 < len(value) < 3
    ]


def emit_scrub_diagnostics(
    categories: list[str], *, runtime_values: Mapping[str, str] | None = None
) -> None:
    if categories:
        print("scrub_outbound: redacted " + ", ".join(categories), file=sys.stderr)
    for key in _short_runtime_value_keys(runtime_values or {}):
        print(
            f"scrub_outbound: runtime value {key} too short to scrub safely, skipped",
            file=sys.stderr,
        )


def scrub_outbound_text(
    text: str, *, runtime_values: dict[str, str] | None = None
) -> tuple[str, list[str]]:
    """Return scrubbed text and unique redaction categories encountered."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if runtime_values is not None and not isinstance(runtime_values, dict):
        raise TypeError("runtime_values must be a dict or None")

    categories: list[str] = []
    scrubbed = text
    patterns = list(_STATIC_PATTERNS)
    patterns.extend(_runtime_patterns(runtime_values or {}))
    for category, pattern in patterns:
        if pattern.search(scrubbed):
            if category not in categories:
                categories.append(category)
            scrubbed = pattern.sub(_marker(category), scrubbed)
    if text and not scrubbed and REDACTION_PREFIX not in scrubbed:
        raise ScrubError("scrub produced empty output without a redaction marker")
    return scrubbed, categories


def scrub_for_publish(text: str, *, runtime_values: dict[str, str] | None = None) -> str:
    """Scrub one human-readable outbound payload or raise before publication."""
    scrubbed, _ = scrub_outbound_text(text, runtime_values=runtime_values)
    if text and not scrubbed and REDACTION_PREFIX not in scrubbed:
        raise ScrubError("scrub produced empty output without a redaction marker")
    return scrubbed


def runtime_values_from_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in _CLI_RUNTIME_KEYS if os.environ.get(key)}


def main() -> int:
    text = sys.stdin.read()
    runtime_values = runtime_values_from_environment()
    scrubbed, categories = scrub_outbound_text(text, runtime_values=runtime_values)
    sys.stdout.write(scrubbed)
    emit_scrub_diagnostics(categories, runtime_values=runtime_values)
    return 0


if __name__ == "__main__":
    sys.exit(main())
