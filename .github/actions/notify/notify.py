#!/usr/bin/env python3
"""Shared CI notify sender: WeCom markdown.

The channel is a property of the receiving webhook address: scheme must be
https and the hostname matched exactly to qyapi.weixin.qq.com (never by
substring) -> WeCom markdown (success: errcode == 0, int).
Anything else (http, unknown or look-alike hosts like
qyapi.weixin.qq.com.evil.example) fails loudly WITHOUT sending; errors name
the host, never the webhook value. A missing webhook is a ::warning + exit 0
skip (callers keep their own fail-open policy via continue-on-error). No
retries, no channel fallback.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

WECOM_HOST = "qyapi.weixin.qq.com"
MAX_WECOM_CONTENT_BYTES = 4096
TRUNCATION_SUFFIX = "…（已截断）"
HTTP_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 65536

# severity -> icon
SEVERITY_STYLES = {
    "P0": "🔴",
    "P1": "🟠",
    "P2": "🟠",
    "info": "🔵",
}
DEFAULT_TITLE_PREFIX = "[zlxlabs·CI]"


def _parse_links(raw: str) -> list[dict]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        links = json.loads(raw)
    except (TypeError, ValueError):
        print("::error title=notify-invalid-input::links 不是合法 JSON")
        raise SystemExit(1)
    if not isinstance(links, list):
        print("::error title=notify-invalid-input::links 必须是 JSON 数组")
        raise SystemExit(1)
    for link in links:
        if (
            not isinstance(link, dict)
            or not isinstance(link.get("text"), str)
            or not isinstance(link.get("url"), str)
            or not link["text"]
            or not link["url"]
        ):
            print("::error title=notify-invalid-input::links 每项需含非空 text 与 url")
            raise SystemExit(1)
    return links


def _truncate_wecom_content(content: str) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_WECOM_CONTENT_BYTES:
        return content
    budget = MAX_WECOM_CONTENT_BYTES - len(TRUNCATION_SUFFIX.encode("utf-8"))
    cut = encoded[:budget]
    while cut:
        try:
            return cut.decode("utf-8") + TRUNCATION_SUFFIX
        except UnicodeDecodeError:
            cut = cut[:-1]  # never split a multi-byte character
    return TRUNCATION_SUFFIX


def _wecom_payload(heading: str, body: str, links: list[dict]) -> dict:
    lines = [f"**{heading}**"]
    if body:
        lines.append(body)
    lines.extend(f"[{link['text']}]({link['url']})" for link in links)
    content = _truncate_wecom_content("\n".join(lines))
    return {"msgtype": "markdown", "markdown": {"content": content}}


def _post_and_check(webhook: str, payload: dict) -> int:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            status = response.status
            response_body = response.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace")
    except Exception as exc:  # network / HTTP >= 400: fail loud, no swallow
        print(f"::error title=notify-request-failed::{type(exc).__name__}: {exc}")
        return 1
    if status != 200:
        print(f"::error title=notify-http-error::HTTP {status}")
        return 1
    try:
        response = json.loads(response_body)
    except (TypeError, ValueError):
        print("::error title=notify-bad-response::响应不是合法 JSON")
        return 1
    code = response.get("errcode") if isinstance(response, dict) else None
    if not (isinstance(code, int) and not isinstance(code, bool) and code == 0):
        print(f"::error title=notify-rejected::errcode={code}")
        return 1
    return 0


def main() -> int:
    webhook = os.environ.get("NOTIFY_WEBHOOK", "").strip()
    if not webhook:
        print("::warning title=notify-skipped::未配置通知地址，未发送")
        return 0

    severity = os.environ.get("NOTIFY_SEVERITY", "").strip()
    icon = SEVERITY_STYLES.get(severity)
    if icon is None:
        print(
            "::error title=notify-invalid-severity::"
            f"severity 必须是 P0/P1/P2/info，收到 {severity!r}"
        )
        return 1

    parsed = urllib.parse.urlsplit(webhook)
    host = parsed.hostname or ""
    if parsed.scheme != "https":
        print(
            "::error title=notify-unknown-host::"
            f"通知地址必须是 https，收到 {parsed.scheme or '无'} scheme（host={host or '无法解析'}）"
        )
        return 1
    if host != WECOM_HOST:
        print(
            "::error title=notify-unknown-host::"
            f"不支持的通知地址主机 {host}（仅支持 {WECOM_HOST}）"
        )
        return 1

    title_prefix = os.environ.get("NOTIFY_TITLE_PREFIX", "").strip() or DEFAULT_TITLE_PREFIX
    heading = f"{icon} {title_prefix} {severity} {os.environ.get('NOTIFY_TITLE', '')}"
    body = os.environ.get("NOTIFY_BODY", "")
    links = _parse_links(os.environ.get("NOTIFY_LINKS", ""))

    payload = _wecom_payload(heading, body, links)
    return _post_and_check(webhook, payload)


if __name__ == "__main__":
    sys.exit(main())
