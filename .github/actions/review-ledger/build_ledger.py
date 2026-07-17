#!/usr/bin/env python3
"""Build a cumulative, artifact-backed Codex review effectiveness ledger."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DISPOSITION_RE = re.compile(
    r"^Codex finding disposition:\s*([a-z0-9][a-z0-9._-]*)\s*=\s*"
    r"(false-positive|accepted|fixed|wont-fix)\s*(?:[-—:]\s*(.+))?$",
    re.IGNORECASE | re.MULTILINE,
)


class CrossHostAuthStripRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not leak the GitHub bearer token to signed artifact storage URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            for header_map in (redirected.headers, redirected.unredirected_hdrs):
                for key in list(header_map):
                    if key.lower() == "authorization":
                        del header_map[key]
        return redirected


URL_OPENER = urllib.request.build_opener(CrossHostAuthStripRedirectHandler())
STATE_MARKER = "<!-- codex-review-ledger-state -->"
STATE_RE = re.compile(r"<!-- codex-review-ledger-state:v1:([A-Za-z0-9_-]+={0,2}) -->")


def parse_dispositions(comments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for comment in comments:
        for match in DISPOSITION_RE.finditer(comment.get("body", "")):
            finding_id, disposition, reason = match.groups()
            result[finding_id.lower()] = {
                "disposition": disposition.lower(),
                "reason": (reason or "").strip(),
                "author": comment.get("user", {}).get("login", "unknown"),
                "recorded_at": comment.get("created_at"),
                "url": comment.get("html_url"),
            }
    return result


def parse_state_entries(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for comment in comments:
        user = comment.get("user", {})
        if user.get("type") != "Bot" or user.get("login") != "github-actions[bot]":
            continue
        match = STATE_RE.search(comment.get("body", ""))
        if not match:
            continue
        try:
            payload = base64.urlsafe_b64decode(match.group(1).encode())
            entries = json.loads(payload)
            if isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries):
                return entries
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return []


def render_state_comment(entries: list[dict[str, Any]], current: dict[str, Any]) -> str:
    relevant = [
        entry for entry in entries
        if entry.get("repository") == current.get("repository")
        and entry.get("pr_number") == current.get("pr_number")
    ][-20:]
    encoded = base64.urlsafe_b64encode(
        json.dumps(relevant, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()
    review = current["review"]
    comparison = current["comparison"]
    comparison_line = comparison["kind"]
    if comparison["kind"] == "new_head":
        comparison_line += (
            f"; persistent/resolved/new = {len(comparison['persistent_finding_ids'])}/"
            f"{len(comparison['resolved_finding_ids'])}/{len(comparison['new_finding_ids'])}"
        )
    elif comparison["kind"] == "same_head_rerun":
        comparison_line += (
            f"; stable/missing/appeared = {len(comparison['persistent_finding_ids'])}/"
            f"{len(comparison['missing_finding_ids'])}/{len(comparison['appeared_finding_ids'])}"
        )
    return (
        f"{STATE_MARKER}\n\n### 📒 Review ledger state\n\n"
        f"- Commit: `{current['head_sha']}`\n"
        f"- Round: **{current['review_round']}**\n"
        f"- Status / findings: **{review['status']} / {review['finding_count']}**\n"
        f"- Comparison: `{comparison_line}`\n\n"
        "完整数据保存在 `codex-review-ledger` artifact；此 sticky comment 仅保存跨 rerun 的连续游标。\n\n"
        f"<!-- codex-review-ledger-state:v1:{encoded} -->\n"
    )


def _review_summary(audit: dict[str, Any] | None, fallback_status: str) -> dict[str, Any]:
    if not audit:
        return {
            "status": fallback_status,
            "verdict": None,
            "finding_count": 0,
            "finding_ids": [],
            "severity_counts": {},
            "category_counts": {},
            "coverage": None,
            "runtime": None,
        }
    result = audit.get("result") or {}
    findings = result.get("findings") or []
    return {
        "status": audit.get("status", "unknown"),
        "verdict": result.get("verdict"),
        "finding_count": len(findings),
        "finding_ids": sorted({finding.get("id", "") for finding in findings if finding.get("id")}),
        "severity_counts": dict(sorted(Counter(finding.get("severity", "unknown") for finding in findings).items())),
        "category_counts": dict(sorted(Counter(finding.get("category", "unknown") for finding in findings).items())),
        "coverage": audit.get("coverage"),
        "runtime": audit.get("runtime"),
    }


def build_entry(
    *,
    repository: str,
    pr_number: int,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    preflight: dict[str, Any],
    audit: dict[str, Any] | None,
    prior_entries: list[dict[str, Any]],
    dispositions: dict[str, dict[str, Any]],
    install: dict[str, Any] | None = None,
    fallback_status: str = "not_run",
) -> dict[str, Any]:
    relevant = [
        entry for entry in prior_entries
        if entry.get("repository") == repository and entry.get("pr_number") == pr_number
    ]
    previous = relevant[-1] if relevant else None
    review = _review_summary(audit, fallback_status)
    current_ids = set(review["finding_ids"])
    comparison: dict[str, Any] = {"kind": "first_review"}
    if previous:
        previous_ids = set(previous.get("review", {}).get("finding_ids", []))
        same_head = previous.get("head_sha") == head_sha
        comparison = {
            "kind": "same_head_rerun" if same_head else "new_head",
            "previous_head_sha": previous.get("head_sha"),
            "previous_run_id": previous.get("run_id"),
            "persistent_finding_ids": sorted(previous_ids & current_ids),
        }
        if same_head:
            comparison.update({
                "missing_finding_ids": sorted(previous_ids - current_ids),
                "appeared_finding_ids": sorted(current_ids - previous_ids),
            })
        else:
            comparison.update({
                "resolved_finding_ids": sorted(previous_ids - current_ids),
                "new_finding_ids": sorted(current_ids - previous_ids),
            })
    relevant_dispositions = {
        finding_id: value for finding_id, value in dispositions.items()
        if finding_id in current_ids or any(finding_id in entry.get("review", {}).get("finding_ids", []) for entry in relevant)
    }
    return {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "pr_number": pr_number,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
        "review_round": len(relevant) + 1,
        "preflight": preflight or None,
        # D5(ci-cache-strategy.md 阶段 A):Install dependencies 步骤的度量信号 —
        # {ecosystem, status, duration_s, cache_hit}(见 gate.yml Install 步骤),
        # 缺失时为 None。纯新增字段,不影响任何读取 "review"/"preflight"/
        # "comparison" 等既有 key 的消费者。
        "install": install,
        "review": review,
        "comparison": comparison,
        "finding_dispositions": relevant_dispositions,
        "false_positive_count": sum(
            item.get("disposition") == "false-positive" for item in relevant_dispositions.values()
        ),
    }


def write_ledger(path: Path, entries: list[dict[str, Any]], *, max_entries: int) -> None:
    ordered = dedupe_entries(entries)[-max_entries:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in ordered))


def dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in entries:
        key = (entry.get("repository"), entry.get("run_id"), entry.get("run_attempt"))
        unique[key] = entry
    return sorted(unique.values(), key=lambda entry: (entry.get("recorded_at", ""), entry.get("run_id", 0), entry.get("run_attempt", 0)))


def _api_request(token: str, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> bytes:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "zlxlabs-gate-review-ledger",
            "Content-Type": "application/json",
        },
    )
    with URL_OPENER.open(request, timeout=30) as response:
        return response.read()


def _api_json(token: str, url: str) -> Any:
    return json.loads(_api_request(token, url))


def fetch_prior_entries(token: str, repository: str, *, artifact_limit: int = 10) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"name": "codex-review-ledger", "per_page": artifact_limit})
    payload = _api_json(token, f"https://api.github.com/repos/{repository}/actions/artifacts?{query}")
    entries: list[dict[str, Any]] = []
    for artifact in payload.get("artifacts", [])[:artifact_limit]:
        if artifact.get("expired"):
            continue
        try:
            archive = _api_request(token, artifact["archive_download_url"])
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                name = next((name for name in bundle.namelist() if name.endswith("ledger.jsonl")), None)
                if not name:
                    continue
                for line in bundle.read(name).decode("utf-8").splitlines():
                    if line.strip():
                        entries.append(json.loads(line))
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
            print(f"::warning::skip unreadable prior ledger artifact {artifact.get('id')}: {error}")
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in entries:
        deduped[(entry.get("repository"), entry.get("run_id"), entry.get("run_attempt"))] = entry
    return sorted(deduped.values(), key=lambda entry: (entry.get("recorded_at", ""), entry.get("run_id", 0), entry.get("run_attempt", 0)))


def fetch_comments(token: str, repository: str, pr_number: int) -> list[dict[str, Any]]:
    return _api_json(token, f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments?per_page=100")


def post_state_comment(
    token: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    entries: list[dict[str, Any]],
    current: dict[str, Any],
    comments: list[dict[str, Any]],
) -> None:
    api = f"https://api.github.com/repos/{repository}"
    pull = _api_json(token, f"{api}/pulls/{pr_number}")
    if pull.get("head", {}).get("sha") != head_sha:
        print("::notice::skip stale review ledger state; PR head advanced")
        return
    existing = next((comment for comment in comments if STATE_MARKER in comment.get("body", "")), None)
    body = render_state_comment(entries, current)
    if existing:
        _api_request(token, f"{api}/issues/comments/{existing['id']}", method="PATCH", payload={"body": body})
    else:
        _api_request(token, f"{api}/issues/{pr_number}/comments", method="POST", payload={"body": body})


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return json.loads(path.read_text())


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes"}


def _append_summary(entry: dict[str, Any], path: str) -> None:
    review = entry["review"]
    comparison = entry["comparison"]
    with open(path, "a", encoding="utf-8") as summary:
        summary.write(
            "### Review effectiveness ledger\n\n"
            f"- Round: {entry['review_round']} (`{comparison['kind']}`)\n"
            f"- Review status: `{review['status']}`\n"
            f"- Findings: {review['finding_count']}\n"
            f"- False positives recorded: {entry['false_positive_count']}\n"
        )
        if comparison["kind"] == "new_head":
            summary.write(
                f"- Persistent / resolved / new: {len(comparison['persistent_finding_ids'])} / "
                f"{len(comparison['resolved_finding_ids'])} / {len(comparison['new_finding_ids'])}\n"
            )
        elif comparison["kind"] == "same_head_rerun":
            summary.write(
                f"- Same-head stable / missing / appeared: {len(comparison['persistent_finding_ids'])} / "
                f"{len(comparison['missing_finding_ids'])} / {len(comparison['appeared_finding_ids'])}\n"
            )
        install = entry.get("install")
        if install:
            summary.write(
                f"- Install: `{install.get('ecosystem')}` status={install.get('status')} "
                f"duration={install.get('duration_s')}s cache_hit={install.get('cache_hit')}\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-path", required=True, type=Path)
    parser.add_argument("--preflight-path", required=True, type=Path)
    parser.add_argument("--install-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--codex-expected", default="false")
    parser.add_argument("--codex-waived", default="false")
    parser.add_argument("--max-entries", default=2000, type=int)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN", "")

    preflight = _load_json(args.preflight_path) or {}
    audit = _load_json(args.audit_path)
    install = _load_json(args.install_path)
    if not preflight.get("reviewable", True):
        fallback = "blocked_by_size"
    elif _truthy(args.codex_waived):
        fallback = "waived"
    elif not _truthy(args.codex_expected):
        fallback = "not_applicable"
    else:
        fallback = "not_run"

    prior_entries: list[dict[str, Any]] = []
    dispositions: dict[str, dict[str, Any]] = {}
    comments: list[dict[str, Any]] = []
    if token:
        try:
            prior_entries = fetch_prior_entries(token, args.repository)
        except Exception as error:  # Metrics are fail-open; never change the gate verdict.
            print(f"::warning::could not load prior review ledger: {error}")
        try:
            comments = fetch_comments(token, args.repository, args.pr_number)
            dispositions = parse_dispositions(comments)
            prior_entries = dedupe_entries([*prior_entries, *parse_state_entries(comments)])
        except Exception as error:
            print(f"::warning::could not load finding dispositions or PR ledger state: {error}")

    entry = build_entry(
        repository=args.repository,
        pr_number=args.pr_number,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        head_sha=args.head_sha,
        preflight=preflight,
        audit=audit,
        prior_entries=prior_entries,
        dispositions=dispositions,
        install=install,
        fallback_status=fallback,
    )
    all_entries = dedupe_entries([*prior_entries, entry])
    write_ledger(args.output, all_entries, max_entries=args.max_entries)
    if token:
        try:
            post_state_comment(
                token, args.repository, args.pr_number, args.head_sha,
                all_entries, entry, comments,
            )
        except Exception as error:
            print(f"::warning::could not update PR review ledger state: {error}")
    print(json.dumps(entry, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        _append_summary(entry, os.environ["GITHUB_STEP_SUMMARY"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
