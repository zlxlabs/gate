#!/usr/bin/env python3
"""Lint upload-artifact retention declarations in a caller repo (gate-hub#2419).

Pure stdlib on purpose: the self-hosted runner's system python3 has no PyYAML
(gate-v2.yml header note). Scans <scan-root>/.github/workflows/*.y{a,}ml plus
every **/action.yml (composite actions) with a text state machine and fails
on exactly two mechanical rules:

  1. every `uses: actions/upload-artifact@` step must declare
     `retention-days` as a positive integer literal — missing, 0, a
     `${{ }}` expression, or a quoted string all count as undeclared;
  2. a declared value > 7 (the largest tier ceiling) must sit next to a
     `# retention: <N> — 消费方 …` exemption comment whose N equals the
     declared value. Comment window: 8 lines above through 12 lines below
     the `uses:` line, the same window tests/test_artifact_retention.py
     asserts for this repo's own workflows.

Violations emit `::error file=…,line=…` annotations and exit non-zero, so
the step's failure propagates through caller-checks-outcome into a red PR.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

USES_RE = re.compile(r"^(\s*)(?:-\s+)?uses:\s*actions/upload-artifact@")
RETENTION_KEY_RE = re.compile(r"^(\s*)retention-days:\s*(.*?)\s*$")
NAME_KEY_RE = re.compile(r"^(\s*)name:\s*(.*?)\s*$")
STEP_NAME_RE = re.compile(r"^(\s*)-\s+name:\s*(.*?)\s*$")
INLINE_WITH_RE = re.compile(r"^\s*with:\s*\{(.*)\}\s*$")
INLINE_RETENTION_RE = re.compile(r"retention-days:\s*([^,}]+)")
RETENTION_COMMENT_RE = re.compile(r"# retention:\s+(\d+)\s+—")
CONSUMER_KEYWORD = "消费方"
WINDOW_BEFORE, WINDOW_AFTER = 8, 12
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
TIER_HINT = "档位提示：同 run 搬运 1 天、跨 run 验收 3 天、诊断取包 7 天"
CEILING_HINT = (
    f">7 天须在步骤旁（uses 行上 {WINDOW_BEFORE} 行到下 {WINDOW_AFTER} 行窗口）写 "
    "`# retention: <N> — 消费方 <谁>；理由 <为什么>`，且 N 与声明值一致"
)
FIX_EXAMPLE = (
    "修法示例：%0A  retention-days: 3%0A"
    "  # retention: 3 — 消费方 <谁>；理由 <为什么>"
)


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        files += sorted(
            p for p in workflows.iterdir()
            if p.is_file() and p.suffix in (".yml", ".yaml")
        )
    for path in sorted(root.rglob("action.yml")):
        parts = path.relative_to(root).parts[:-1]
        if any(
            seg in SKIP_DIRS
            or seg.startswith("_gate-")
            or (seg.startswith(".") and seg != ".github")
            for seg in parts
        ):
            continue
        files.append(path)
    return files


def _classify_value(raw: str):
    """Return (declared_days, None) or (None, undeclared_reason)."""
    value = raw.strip()
    if not value:
        return None, "缺省未声明"
    if "${{" in value:
        return None, "${{ }} 表达式按未声明处理"
    unquoted = value.strip("\"'")
    if unquoted != value or not re.fullmatch(r"[0-9]+", unquoted):
        return None, "不是正整数字面量（引号字符串视为未声明）"
    days = int(unquoted)
    if days <= 0:
        return None, "必须是正整数"
    return days, None


def _step_body(lines: list[str], start: int, indent: int):
    """Yield (index, line) for the step's own body below the uses line."""
    for j in range(start + 1, len(lines)):
        line = lines[j]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent < indent or (
            line_indent == indent and line.lstrip().startswith("- ")
        ):
            return
        yield j, line


def _step_name(lines: list[str], idx: int, indent: int) -> str | None:
    for j in range(idx - 1, max(-1, idx - 41), -1):
        match = STEP_NAME_RE.match(lines[j])
        if match and len(match.group(1)) + 2 <= indent:
            return match.group(2)
        match = NAME_KEY_RE.match(lines[j])
        if match and len(match.group(1)) == indent:
            return match.group(2)
    return None


def _artifact_name(lines: list[str], idx: int, indent: int) -> str | None:
    for j, body in _step_body(lines, idx, indent):
        match = NAME_KEY_RE.match(body)
        if match and len(match.group(1)) > indent:
            return match.group(2)
    return None


def _retention_days(lines: list[str], idx: int, indent: int):
    """Return (raw_value, line_no); raw_value None when the step omits the key."""
    for j, body in _step_body(lines, idx, indent):
        match = RETENTION_KEY_RE.match(body)
        if match and len(match.group(1)) > indent:
            return match.group(2), j + 1
        inline = INLINE_WITH_RE.match(body)
        if inline:
            found = INLINE_RETENTION_RE.search(inline.group(1))
            if found:
                return found.group(1), j + 1
    return None, None


def _error(rel: str, line_no: int, label: str, detail: str) -> str:
    message = f"{label}: {detail}。{TIER_HINT}；{CEILING_HINT}。{FIX_EXAMPLE}"
    return f"::error file={rel},line={line_no}::{message}"


def lint_file(path: Path, rel: str) -> list[str]:
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for idx, line in enumerate(lines):
        match = USES_RE.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        name = _step_name(lines, idx, indent)
        label = name or _artifact_name(lines, idx, indent) or "upload-artifact step"
        raw, key_line = _retention_days(lines, idx, indent)
        if raw is None:
            errors.append(_error(
                rel, idx + 1, label, "retention-days 未声明（步骤未写 retention-days）",
            ))
            continue
        declared, reason = _classify_value(raw)
        if declared is None:
            errors.append(_error(
                rel, key_line or idx + 1, label,
                f"retention-days: {raw!r} 不合规（{reason}）",
            ))
            continue
        if declared <= 7:
            continue
        window = "\n".join(lines[max(0, idx - WINDOW_BEFORE): idx + WINDOW_AFTER])
        comment = RETENTION_COMMENT_RE.search(window)
        if comment is None:
            errors.append(_error(
                rel, idx + 1, label,
                f"retention-days: {declared} > 7 但缺 `# retention:` 豁免注释",
            ))
        elif int(comment.group(1)) != declared:
            errors.append(_error(
                rel, idx + 1, label,
                f"`# retention: {comment.group(1)}` 与声明值 {declared} 不一致",
            ))
        elif CONSUMER_KEYWORD not in window:
            errors.append(_error(
                rel, idx + 1, label, "`# retention:` 豁免注释须写明「消费方」",
            ))
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd()
    files = collect_files(root)
    if not files:
        print(f"::error::artifact-retention-lint: no workflow/action.yml files under {root}")
        return 1
    errors: list[str] = []
    for path in files:
        errors += lint_file(path, path.relative_to(root).as_posix())
    for error in errors:
        print(error)
    if errors:
        print(f"artifact-retention-lint: {len(errors)} violation(s)")
        return 1
    print(f"artifact-retention-lint: {len(files)} file(s) scanned, no violations")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
