#!/usr/bin/env python3
"""Measure diff coverage and write a one-line advisory to the job summary."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

GATE_ROOT = Path(__file__).resolve().parents[3]
if str(GATE_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE_ROOT))

from scripts.scrub_outbound import runtime_values_from_environment, scrub_for_publish

NOTE_PREFIX = "diff-coverage: "
DEFAULT_LCOV_PATH = Path("coverage/lcov.info")
CODE_EXTENSIONS = (
    ".py", ".pyi", ".pyw",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".mts", ".cts",
    ".go",
    ".rs",
    ".java", ".kt", ".kts",
    ".swift",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".cs",
    ".rb", ".rake",
    ".php",
    ".scala", ".sc",
    ".vue", ".svelte",
    ".sh", ".bash", ".zsh",
    ".sql",
    ".r", ".R",
    ".m", ".mm",
    ".lua",
    ".ex", ".exs",
    ".erl", ".hrl",
    ".clj", ".cljs",
    ".hs",
    ".elm",
    ".dart",
    ".zig",
)


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo)


def ensure_review_commits(repo: Path, base_sha: str, head_sha: str) -> None:
    """Fetch only the PR endpoints when absent (same contract as pr-size-preflight)."""
    try:
        for sha in (base_sha, head_sha):
            _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    except subprocess.CalledProcessError:
        _git(repo, "fetch", "--no-tags", "origin", base_sha, head_sha)


def changed_paths(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    ensure_review_commits(repo, base_sha, head_sha)
    raw = _git(repo, "diff", "--name-only", "-z", base_sha, head_sha)
    return [
        path.decode("utf-8", "surrogateescape")
        for path in raw.split(b"\0")
        if path
    ]


def is_code_path(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in CODE_EXTENSIONS)


def is_docs_only(repo: Path, base_sha: str, head_sha: str) -> bool:
    paths = changed_paths(repo, base_sha, head_sha)
    if not paths:
        return True
    return not any(is_code_path(path) for path in paths)


def _run_diff_cover(repo: Path, base_sha: str, lcov_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "diff-cover.json"
        command = [
            sys.executable,
            "-m",
            "diff_cover.diff_cover_tool",
            str(lcov_path),
            "--compare-branch",
            base_sha,
            "--format",
            f"json:{report_path}",
        ]
        subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True)
        return json.loads(report_path.read_text(encoding="utf-8"))


def _format_percent(value: float | int) -> str:
    if isinstance(value, int) or value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def render_note_line(result: dict[str, Any]) -> str | None:
    state = result["state"]
    if state == "skip":
        return None
    if state == "no_data":
        return f"{NOTE_PREFIX}no coverage data"
    covered = result["covered_lines"]
    total = result["total_lines"]
    pct = _format_percent(result["percent_covered"])
    return f"{NOTE_PREFIX}{pct}% ({covered}/{total} changed lines)"


def measure(
    repo: Path,
    base_sha: str,
    head_sha: str,
    *,
    lcov_path: Path = DEFAULT_LCOV_PATH,
) -> dict[str, Any]:
    ensure_review_commits(repo, base_sha, head_sha)
    if is_docs_only(repo, base_sha, head_sha):
        return {
            "state": "skip",
            "reason": "docs_only",
            "base_sha": base_sha,
            "head_sha": head_sha,
        }

    resolved_lcov = repo / lcov_path
    if not resolved_lcov.is_file():
        return {
            "state": "no_data",
            "reason": "missing_lcov",
            "lcov_path": str(lcov_path),
            "base_sha": base_sha,
            "head_sha": head_sha,
        }

    report = _run_diff_cover(repo, base_sha, resolved_lcov)
    total_lines = int(report["total_num_lines"])
    if total_lines == 0:
        return {
            "state": "skip",
            "reason": "no_measurable_code_lines",
            "base_sha": base_sha,
            "head_sha": head_sha,
        }

    violations = int(report["total_num_violations"])
    covered = total_lines - violations
    return {
        "state": "covered",
        "percent_covered": report["total_percent_covered"],
        "covered_lines": covered,
        "total_lines": total_lines,
        "lcov_path": str(lcov_path),
        "base_sha": base_sha,
        "head_sha": head_sha,
    }


def _append_summary(result: dict[str, Any], path: str) -> None:
    note = render_note_line(result)
    if note is None:
        summary = "### Diff coverage advisory\n\n- Status: `skipped`\n"
    elif result["state"] == "no_data":
        summary = f"### Diff coverage advisory\n\n- Status: `no_data`\n- Note: `{note}`\n"
    else:
        summary = (
            "### Diff coverage advisory\n\n"
            f"- Status: `covered`\n"
            f"- Note: `{note}`\n"
            f"- LCOV: `{result.get('lcov_path', DEFAULT_LCOV_PATH)}`\n"
        )
    with open(path, "a", encoding="utf-8") as summary_file:
        summary_file.write(
            scrub_for_publish(summary, runtime_values=runtime_values_from_environment())
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--lcov-path", default=str(DEFAULT_LCOV_PATH))
    try:
        args = parser.parse_args()
    except SystemExit:
        print("::warning::diff-coverage advisory degraded to missing note: invalid arguments")
        return 0

    try:
        result = measure(
            Path.cwd(),
            args.base_sha,
            args.head_sha,
            lcov_path=Path(args.lcov_path),
        )
    except Exception as error:  # noqa: BLE001 — advisory must never fail the workflow
        print(f"::warning::diff-coverage advisory degraded to missing note: {error}")
        return 0

    print(json.dumps(result, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        try:
            _append_summary(result, os.environ["GITHUB_STEP_SUMMARY"])
        except OSError as error:
            print(f"::warning::could not append diff-coverage summary: {error}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
