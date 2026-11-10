#!/usr/bin/env python3
"""Shared CI notify sender: WeCom markdown or Feishu interactive card.

The channel is a property of the receiving webhook address: scheme must be
https and the hostname matched exactly, never by substring:
qyapi.weixin.qq.com -> WeCom markdown (success: errcode == 0, int);
open.feishu.cn / open.larksuite.com -> Feishu card (success: code == 0, int).
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
FEISHU_HOSTS = ("open.feishu.cn", "open.larksuite.com")
MAX_WECOM_CONTENT_BYTES = 4096
TRUNCATION_SUFFIX = "…（已截断）"
HTTP_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 65536

# severity -> (icon, feishu header template colour)
SEVERITY_STYLES = {
    "P0": ("🔴", "red"),
    "P1": ("🟠", "orange"),
    "P2": ("🟠", "orange"),
    "info": ("🔵", "blue"),
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


def _feishu_payload(heading: str, template: str, body: str, links: list[dict]) -> dict:
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": body}}]
    if links:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": link["text"]},
                        "type": "primary" if index == 0 else "default",
                        "url": link["url"],
                    }
                    for index, link in enumerate(links)
                ],
            }
        )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": heading},
            },
            "elements": elements,
        },
    }


def _post_and_check(webhook: str, payload: dict, success_key: str) -> int:
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
    code = response.get(success_key) if isinstance(response, dict) else None
    if not (isinstance(code, int) and not isinstance(code, bool) and code == 0):
        print(f"::error title=notify-rejected::{success_key}={code}")
        return 1
    return 0


def main() -> int:
    webhook = os.environ.get("NOTIFY_WEBHOOK", "").strip()
    if not webhook:
        print("::warning title=notify-skipped::未配置通知地址，未发送")
        return 0

    severity = os.environ.get("NOTIFY_SEVERITY", "").strip()
    style = SEVERITY_STYLES.get(severity)
    if style is None:
        print(
            "::error title=notify-invalid-severity::"
            f"severity 必须是 P0/P1/P2/info，收到 {severity!r}"
        )
        return 1
    icon, template = style

    parsed = urllib.parse.urlsplit(webhook)
    host = parsed.hostname or ""
    if parsed.scheme != "https":
        print(
            "::error title=notify-unknown-host::"
            f"通知地址必须是 https，收到 {parsed.scheme or '无'} scheme（host={host or '无法解析'}）"
        )
        return 1
    if host == WECOM_HOST:
        success_key = "errcode"
    elif host in FEISHU_HOSTS:
        success_key = "code"
    else:
        print(
            "::error title=notify-unknown-host::"
            f"不支持的通知地址主机 {host}（仅支持 {WECOM_HOST} / "
            f"{FEISHU_HOSTS[0]} / {FEISHU_HOSTS[1]}）"
        )
        return 1

    title_prefix = os.environ.get("NOTIFY_TITLE_PREFIX", "").strip() or DEFAULT_TITLE_PREFIX
    heading = f"{icon} {title_prefix} {severity} {os.environ.get('NOTIFY_TITLE', '')}"
    body = os.environ.get("NOTIFY_BODY", "")
    links = _parse_links(os.environ.get("NOTIFY_LINKS", ""))

    if success_key == "errcode":
        payload = _wecom_payload(heading, body, links)
    else:
        payload = _feishu_payload(heading, template, body, links)
    return _post_and_check(webhook, payload, success_key)


if __name__ == "__main__":
    sys.exit(main())
