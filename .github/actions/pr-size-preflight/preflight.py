#!/usr/bin/env python3
"""Measure a PR before expensive CI and publish one sticky size warning."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GATE_ROOT = Path(__file__).resolve().parents[3]
if str(GATE_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE_ROOT))

from scripts.scrub_outbound import runtime_values_from_environment, scrub_for_publish


MARKER = "<!-- pr-size-preflight -->"
SIZE_FILTER_CONTRACT = "v1"
EXCLUDED_SUFFIXES = (
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".whl",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".icns", ".svg",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".min.js", ".min.css", ".map", ".lock",
)
GENERATED_PATCH_LINE_LIMIT = 5000


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo)


def ensure_review_commits(repo: Path, base_sha: str, head_sha: str) -> None:
    """Make the exact PR endpoints available without requiring full history.

    `actions/checkout` intentionally fetches only the workflow ref.  A PR diff is
    nevertheless fully defined by its base and head commits, so fetch just those
    objects when either is absent instead of making every Gate run clone every
    branch and tag in the repository.
    """
    try:
        for sha in (base_sha, head_sha):
            _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    except subprocess.CalledProcessError:
        _git(repo, "fetch", "--no-tags", "origin", base_sha, head_sha)


def classify(diff_lines: int, max_diff_lines: int, warn_lines: int, max_review_shards: int) -> tuple[str, bool]:
    hard_lines = max_diff_lines * max_review_shards
    if diff_lines > hard_lines:
        return "blocked", False
    if diff_lines > warn_lines:
        return "warning", True
    if diff_lines > max_diff_lines:
        return "sharded", True
    return "single", True


def _numstat_entries(numstat: bytes) -> tuple[dict[str, bool], int, int, int]:
    fields = numstat.split(b"\0")
    binary_by_path: dict[str, bool] = {}
    additions = deletions = changed_files = 0
    index = 0
    while index < len(fields) - 1:
        record = fields[index]
        index += 1
        if not record:
            continue
        parts = record.split(b"\t", 2)
        if len(parts) != 3:
            raise ValueError("invalid git numstat record")
        added, deleted, path = parts
        changed_files += 1
        if added.isdigit():
            additions += int(added)
        if deleted.isdigit():
            deletions += int(deleted)
        paths = [path] if path else []
        if not path:
            if index + 1 >= len(fields):
                raise ValueError("truncated git numstat rename record")
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
            if not old_path or not new_path:
                raise ValueError("invalid git numstat rename record")
            paths = [old_path, new_path]
        is_binary = added == b"-" and deleted == b"-"
        for raw_path in paths:
            binary_by_path[raw_path.decode("utf-8", "surrogateescape")] = is_binary
    return binary_by_path, additions, deletions, changed_files


def _byte_line_count(value: bytes) -> int:
    if not value:
        return 0
    return value.count(b"\n") + (not value.endswith(b"\n"))


def _patch_sections(patch: bytes) -> list[int]:
    sections: list[int] = []
    current_lines = 0
    lines = patch.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    for line in lines:
        if line.startswith(b"diff --git "):
            if current_lines:
                sections.append(current_lines)
            current_lines = 1
        elif current_lines:
            current_lines += 1
    if current_lines:
        sections.append(current_lines)
    return sections


def _exclusion_rule(path: str, is_binary: bool, raw_lines: int) -> str | None:
    if is_binary:
        return "R1"
    if path.lower().endswith(EXCLUDED_SUFFIXES):
        return "R2"
    if raw_lines > GENERATED_PATCH_LINE_LIMIT:
        return "R3"
    return None


def measure(
    repo: Path,
    base_sha: str,
    head_sha: str,
    *,
    max_diff_lines: int,
    warn_lines: int,
    max_review_shards: int,
) -> dict[str, Any]:
    ensure_review_commits(repo, base_sha, head_sha)
    patch = _git(repo, "diff", "--no-ext-diff", "--binary", base_sha, head_sha)
    numstat = _git(repo, "diff", "--numstat", "-z", base_sha, head_sha)
    binary_by_path, additions, deletions, changed_files = _numstat_entries(numstat)
    paths = [
        raw_path.decode("utf-8", "surrogateescape")
        for raw_path in _git(repo, "diff", "--name-only", "-z", base_sha, head_sha).split(b"\0")
        if raw_path
    ]
    section_lines = _patch_sections(patch)
    if len(paths) != len(section_lines):
        raise ValueError(
            f"git diff file metadata mismatch: paths={len(paths)} sections={len(section_lines)}"
        )
    excluded_files: list[dict[str, Any]] = []
    reviewable_lines = 0
    for path, raw_lines in zip(paths, section_lines):
        if path not in binary_by_path:
            raise ValueError(f"git numstat has no entry for diff path {path!r}")
        is_binary = binary_by_path[path]
        rule = _exclusion_rule(path, is_binary, raw_lines)
        if rule is None:
            reviewable_lines += raw_lines
        else:
            excluded_files.append({"path": path, "rule": rule, "raw_lines": raw_lines})
    raw_patch_lines = _byte_line_count(patch)
    classification, reviewable = classify(reviewable_lines, max_diff_lines, warn_lines, max_review_shards)
    return {
        "schema_version": 1,
        "size_filter_contract": SIZE_FILTER_CONTRACT,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "diff_lines": reviewable_lines,
        "reviewable_lines": reviewable_lines,
        "raw_patch_lines": raw_patch_lines,
        "changed_lines": additions + deletions,
        "additions": additions,
        "deletions": deletions,
        "changed_files": changed_files,
        "excluded_files": excluded_files,
        "classification": classification,
        "reviewable": reviewable,
        "review_plan": "blocked" if not reviewable else ("single" if classification == "single" else "sharded"),
        "preflight_result": "blocked" if classification == "blocked" else "success",
        "thresholds": {
            "single_turn_lines": max_diff_lines,
            "warn_lines": warn_lines,
            "hard_lines": max_diff_lines * max_review_shards,
            "max_review_shards": max_review_shards,
        },
    }


def build_unavailable_result(
    base_sha: str,
    head_sha: str,
    *,
    max_diff_lines: int,
    warn_lines: int,
    max_review_shards: int,
    error: subprocess.CalledProcessError,
) -> dict[str, Any]:
    """Publish a failed git measurement as unavailable, never as a size decision."""
    return {
        "schema_version": 1,
        "size_filter_contract": SIZE_FILTER_CONTRACT,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "diff_lines": None,
        "reviewable_lines": None,
        "raw_patch_lines": None,
        "changed_lines": None,
        "additions": None,
        "deletions": None,
        "changed_files": None,
        "excluded_files": [],
        "classification": "unavailable",
        "reviewable": False,
        "review_plan": "unavailable",
        "preflight_result": "unavailable",
        "measurement_status": "unavailable",
        "measurement_error": f"git measurement failed (exit={error.returncode})",
        "thresholds": {
            "single_turn_lines": max_diff_lines,
            "warn_lines": warn_lines,
            "hard_lines": max_diff_lines * max_review_shards,
            "max_review_shards": max_review_shards,
        },
    }


def render_comment(result: dict[str, Any]) -> str:
    kind = result["classification"]
    thresholds = result["thresholds"]
    reviewable_lines = result["reviewable_lines"]
    if kind == "unavailable":
        title = "⚠️ PR 体积预检：基础设施不可用"
        explanation = "Git 无法读取 PR 的 base/head 对象，未能测量审查 Patch；门禁保持阻断，不能据此判断 PR 体积。"
        action = "请先恢复 Git 对象读取或网络连接后重新运行；当前结果不会放行，也不要求拆分 PR。"
    elif kind == "blocked":
        title = "⛔ PR 体积预检：超过完整审查能力，已拦截"
        explanation = (
            f"当前审查 Patch 为 **{reviewable_lines} 行**，超过最多 "
            f"**{thresholds['hard_lines']} 行 / {thresholds.get('max_review_shards', '?')} 个分片**的完整审查预算。"
        )
        action = "请把改动拆成可独立验收的 small PR 或 stacked PR；未拆分前 Gate 不会把未审完的改动放行。"
    elif kind == "warning":
        title = "⚠️ PR 体积预检：强警告"
        explanation = (
            f"当前审查 Patch 为 **{reviewable_lines} 行**，已超过强警告线 "
            f"**{thresholds['warn_lines']} 行**。本轮仍会完整分片 review，但反馈更慢、修复成本更高。"
        )
        action = "后续请按单一功能拆成 small PR；如果存在依赖关系，使用 stacked PR。"
    elif kind == "sharded":
        title = "ℹ️ PR 体积预检：将自动分片审查"
        explanation = (
            f"当前审查 Patch 为 **{reviewable_lines} 行**，超过单轮预算 "
            f"**{thresholds['single_turn_lines']} 行**，Codex 会覆盖所有分片并做跨模块整合。"
        )
        action = "这次可以继续，但下次优先按单一功能拆成 small PR，以缩短反馈时间。"
    else:
        title = "✅ PR 体积已回到单轮审查范围"
        explanation = f"当前审查 Patch 为 **{reviewable_lines} 行**，可由 Codex 单轮完整审查。"
        action = "此前的大 PR 提醒已解除。"
    if kind == "unavailable":
        changed_lines = "未知"
        additions = deletions = "未知"
        changed_files = "未知"
    else:
        changed_lines = result.get("changed_lines", result["additions"] + result["deletions"])
        additions = result["additions"]
        deletions = result["deletions"]
        changed_files = result["changed_files"]
    return (
        f"{MARKER}\n\n### {title}\n\n{explanation}\n\n"
        f"- 文件：{changed_files}\n"
        f"- 实际增删：{changed_lines} 行（+{additions} / -{deletions}）\n"
        f"- 审查 Patch：{reviewable_lines if reviewable_lines is not None else '未知'} 行（可审文本口径）\n"
        f"- 原始 Patch：{result['raw_patch_lines'] if result['raw_patch_lines'] is not None else '未知'} 行（含被排除文件）\n"
        f"- Reviewed commit: `{result['head_sha']}`\n\n{action}\n"
    )


def _request(token: str, method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "zlxlabs-gate-pr-size-preflight",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def post_sticky_comment(result: dict[str, Any], *, token: str, repository: str, pr_number: int) -> None:
    body = scrub_for_publish(
        render_comment(result),
        runtime_values=runtime_values_from_environment(),
    )
    api = f"https://api.github.com/repos/{repository}"
    current = _request(token, "GET", f"{api}/pulls/{pr_number}")
    if current["head"]["sha"] != result["head_sha"]:
        print("::notice::skip stale PR size result; head advanced")
        return
    comments = _request(token, "GET", f"{api}/issues/{pr_number}/comments?per_page=100")
    existing = next((comment for comment in comments if MARKER in comment.get("body", "")), None)
    if result["classification"] == "single" and existing is None:
        return
    if existing:
        _request(token, "PATCH", f"{api}/issues/comments/{existing['id']}", {"body": body})
    else:
        _request(token, "POST", f"{api}/issues/{pr_number}/comments", {"body": body})


def _append_summary(result: dict[str, Any], path: str) -> None:
    status = result["classification"]
    if status == "unavailable":
        summary_text = (
            "### PR size preflight\n\n"
            "- Status: `unavailable`\n"
            "- Measurement: unavailable (git measurement failed)\n"
            "- Gate decision: blocked pending infrastructure recovery\n"
            "- Plan: `unavailable`\n"
        )
    else:
        summary_text = (
            "### PR size preflight\n\n"
            f"- Status: `{status}`\n- Review patch: {result['diff_lines']} lines\n"
            f"- Reviewable text: {result['reviewable_lines']} lines\n"
            f"- Raw patch: {result['raw_patch_lines']} lines\n"
            f"- Changed: {result.get('changed_lines', result['additions'] + result['deletions'])} lines "
            f"(+{result['additions']} / -{result['deletions']})\n"
            f"- Files: {result['changed_files']}\n- Plan: `{result['review_plan']}`\n"
        )
    excluded_files = result["excluded_files"]
    if excluded_files:
        summary_text += "- Excluded files:\n"
        for item in excluded_files:
            path_value = json.dumps(item["path"], ensure_ascii=False)
            summary_text += f"  - {path_value} — rule `{item['rule']}`, raw patch {item['raw_lines']} lines\n"
    else:
        summary_text += "- Excluded files: none\n"
    with open(path, "a", encoding="utf-8") as summary:
        summary.write(scrub_for_publish(summary_text, runtime_values=runtime_values_from_environment()))


def _append_action_outputs(result: dict[str, Any], path: str) -> None:
    with open(path, "a", encoding="utf-8") as output:
        output.write(f"reviewable-lines={result['reviewable_lines'] if result['reviewable_lines'] is not None else ''}\n")
        output.write(f"preflight-result={result['preflight_result']}\n")
        output.write(
            "excluded-files="
            + json.dumps(result["excluded_files"], ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--max-diff-lines", required=True, type=int)
    parser.add_argument("--warn-lines", required=True, type=int)
    parser.add_argument("--max-review-shards", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if min(args.max_diff_lines, args.warn_lines, args.max_review_shards) <= 0:
        parser.error("all thresholds must be positive")

    try:
        result = measure(
            Path.cwd(), args.base_sha, args.head_sha,
            max_diff_lines=args.max_diff_lines,
            warn_lines=args.warn_lines,
            max_review_shards=args.max_review_shards,
        )
    except subprocess.CalledProcessError as error:
        result = build_unavailable_result(
            args.base_sha,
            args.head_sha,
            max_diff_lines=args.max_diff_lines,
            warn_lines=args.warn_lines,
            max_review_shards=args.max_review_shards,
            error=error,
        )
    result.update({
        "repository": os.environ.get("GITHUB_REPOSITORY", "unknown"),
        "pr_number": int(os.environ.get("PR_NUMBER", "0") or 0),
        "measured_at": datetime.now(timezone.utc).isoformat(),
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        _append_summary(result, os.environ["GITHUB_STEP_SUMMARY"])
    if os.environ.get("GITHUB_OUTPUT"):
        _append_action_outputs(result, os.environ["GITHUB_OUTPUT"])

    token = os.environ.get("GH_TOKEN", "")
    if token and result["pr_number"]:
        try:
            post_sticky_comment(result, token=token, repository=result["repository"], pr_number=result["pr_number"])
        except Exception as error:
            print(f"::warning::could not update PR size comment ({type(error).__name__}): {error}")

    if result["classification"] == "unavailable":
        print("::error::PR size preflight unavailable: git measurement failed; infrastructure recovery is required")
        return 1
    if result["classification"] == "blocked":
        print("::error::PR exceeds complete Codex review capacity; split it into small or stacked PRs")
        return 1
    if result["classification"] == "warning":
        print("::warning::large PR will be reviewed completely, but should be split next time")
    elif result["classification"] == "sharded":
        print("::notice::PR exceeds one Codex turn and will use complete sharded review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
