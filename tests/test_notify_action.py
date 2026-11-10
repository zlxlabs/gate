"""Tests for the shared .github/actions/notify action (WeCom / Feishu).

The webhook POST is a process boundary: every send test asserts the actual
bytes notify.py puts on the wire, captured by a local HTTP server. To keep the
hostname allowlist fully real the URL keeps production hostnames and the test
wrapper only redirects the TCP layer. The action.yml -> notify.py handoff is
exercised by resolving the composite step's env mapping out of action.yml
itself and running its own `run:` command via subprocess, as a runner would.
"""

import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_DIR = REPO_ROOT / ".github" / "actions" / "notify"
ACTION_YML = ACTION_DIR / "action.yml"
BASE_COMMIT = "a6b6b423"  # historical inline implementation used for the payload compatibility contract

SECRET_KEY = "SECRETKEY-SHOULD-NEVER-APPEAR-IN-LOGS"
WECOM_WEBHOOK = "https://qyapi.weixin.qq.com:{port}/cgi-bin/webhook/send?key=" + SECRET_KEY
FEISHU_WEBHOOK = "https://open.feishu.cn:{port}/open-apis/bot/v2/hook/" + SECRET_KEY

_INPUT_REF = re.compile(r"^\$\{\{\s*inputs\.(\w+)\s*\}\}$")

# Runs notify.py under its production command while pinning the TCP layer to
# the local recorder (mode=local) or making every connect fail (mode=fail).
_WRAPPER = r"""
import http.client, os, runpy, socket, sys

mode = os.environ["NOTIFY_TEST_NET"]
if mode == "local":
    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", int(os.environ["NOTIFY_TEST_PORT"])), timeout=self.timeout)
    http.client.HTTPSConnection.connect = connect
else:
    def fail_connect(self):
        raise OSError("connection refused (test-injected)")
    http.client.HTTPSConnection.connect = fail_connect
runpy.run_path(sys.argv[1], run_name="__main__")
"""


class _Recorder:
    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()
        self.response_status = 200
        self.response_body = b'{"errcode": 0, "errmsg": "ok"}'
        self.port = None

    def record(self, item):
        with self.lock:
            self.requests.append(item)


def _make_handler(recorder):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            recorder.record(
                {
                    "path": self.path,
                    "content_type": self.headers.get("Content-Type"),
                    "body": body,
                }
            )
            payload = recorder.response_body
            self.send_response(recorder.response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    return Handler


@pytest.fixture
def webhook_server():
    recorder = _Recorder()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(recorder))
    recorder.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield recorder
    server.shutdown()
    server.server_close()


def _load_action():
    return yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))


def _run_action(inputs, *, net=None, local_port=None):
    """Run the composite action's own step command with runner-like env.

    net="local" redirects outbound connections to a local recorder server on
    local_port; net="fail" makes every connect raise; None runs the command
    untouched.
    """
    action = _load_action()
    step = next(s for s in action["runs"]["steps"] if "run" in s)
    env = dict(os.environ)
    env["GITHUB_ACTION_PATH"] = str(ACTION_DIR)
    for key, value in step.get("env", {}).items():
        match = _INPUT_REF.match(value.strip())
        env[key] = inputs.get(match.group(1), "") if match else value
    command = step["run"].strip()
    if net is not None:
        env["NOTIFY_TEST_NET"] = net
        env["NOTIFY_TEST_PORT"] = str(local_port)
        command = f"python3 -c '{_WRAPPER}' \"$GITHUB_ACTION_PATH/notify.py\""
    return subprocess.run(
        ["bash", "-c", command], env=env, capture_output=True, text=True, timeout=60
    )


@pytest.fixture
def recorder_server(webhook_server):
    return webhook_server


def test_action_yml_declares_self_contained_composite_contract():
    action = _load_action()
    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    assert set(inputs) == {"webhook", "title_prefix", "severity", "title", "body", "links"}
    assert inputs["severity"]["required"] is True
    assert inputs["title"]["required"] is True
    run_steps = [s for s in action["runs"]["steps"] if "run" in s]
    assert len(run_steps) == 1
    assert run_steps[0]["shell"] == "bash"
    assert '"$GITHUB_ACTION_PATH/notify.py"' in run_steps[0]["run"]
    # Self-containment: ci-templates will reference this directory cross-repo.
    assert (ACTION_DIR / "notify.py").is_file()


def test_wecom_success_posts_markdown_payload_bytes(recorder_server):
    proc = _run_action(
        {
            "webhook": WECOM_WEBHOOK.format(port=recorder_server.port),
            "title_prefix": "",  # default prefix must kick in
            "severity": "P0",
            "title": "部署失败 · demo#7",
            "body": "**仓库** `zlxlabs/demo`\n第二行正文",
            "links": json.dumps(
                [
                    {"text": "查看 Run", "url": "https://run.example/42"},
                    {"text": "查看 PR", "url": "https://pr.example/7"},
                ]
            ),
        },
        net="local",
        local_port=recorder_server.port,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "::error" not in output
    assert len(recorder_server.requests) == 1
    sent = recorder_server.requests[0]
    assert sent["content_type"] == "application/json"
    payload = json.loads(sent["body"])
    assert payload["msgtype"] == "markdown"
    content = payload["markdown"]["content"]
    assert content.startswith("**🔴 [zlxlabs·CI] P0 部署失败 · demo#7**")
    assert "**仓库** `zlxlabs/demo`" in content
    assert "[查看 Run](https://run.example/42)" in content
    assert "[查看 PR](https://pr.example/7)" in content


def test_wecom_truncates_oversized_content_to_4096_bytes(recorder_server):
    proc = _run_action(
        {
            "webhook": WECOM_WEBHOOK.format(port=recorder_server.port),
            "title_prefix": "[zlxlabs·CI]",
            "severity": "P2",
            "title": "t",
            "body": "门" * 3000,  # 9000 UTF-8 bytes of multi-byte characters
            "links": "",
        },
        net="local",
        local_port=recorder_server.port,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    content = json.loads(recorder_server.requests[0]["body"])["markdown"]["content"]
    encoded = content.encode("utf-8")
    assert len(encoded) <= 4096
    assert content.endswith("…（已截断）")
    assert content.startswith("**🟠 [zlxlabs·CI] P2 t**")


def _base_inline_feishu_card(title_prefix, repo, pr, body, run_url, pr_url):
    """Byte-for-byte expectation of the inline gate-v2.yml implementation at
    base {} (notify job, Feishu card step) for the same inputs.""".format(BASE_COMMIT)
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {
                    "tag": "plain_text",
                    "content": f"🟠 {title_prefix} P2 门禁失败 · {repo}#{pr}",
                },
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看 Run"},
                            "type": "primary",
                            "url": run_url,
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看 PR"},
                            "type": "default",
                            "url": pr_url,
                        },
                    ],
                },
            ],
        },
    }


def test_feishu_card_matches_base_inline_implementation(recorder_server):
    recorder_server.response_body = b'{"code": 0, "msg": "success"}'
    repo, pr, pr_title = "zlxlabs/demo", "123", "Fix the thing"
    title = f"门禁失败 · {repo}#{pr}"
    body = (
        f"**仓库** `{repo}`\n"
        f"**PR** #{pr} · {pr_title}\n"
        f"**分支** `card/fix`  ·  **commit** `abc123def456`  ·  **tier** `personal`\n"
        f"Required Gate v2 门禁失败(quality / primary review 其一未过)。点按钮看详情。"
    )
    run_url = "https://github.com/zlxlabs/demo/actions/runs/42"
    pr_url = "https://github.com/zlxlabs/demo/pull/123"
    proc = _run_action(
        {
            "webhook": FEISHU_WEBHOOK.format(port=recorder_server.port),
            "title_prefix": "[zlxlabs·CI]",
            "severity": "P2",
            "title": title,
            "body": body,
            "links": json.dumps(
                [
                    {"text": "查看 Run", "url": run_url},
                    {"text": "查看 PR", "url": pr_url},
                ],
                ensure_ascii=False,
            ),
        },
        net="local",
        local_port=recorder_server.port,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert len(recorder_server.requests) == 1
    expected = _base_inline_feishu_card("[zlxlabs·CI]", repo, pr, body, run_url, pr_url)
    sent_bytes = recorder_server.requests[0]["body"]
    assert json.loads(sent_bytes) == expected
    # Same serializer settings as the inline implementation (default dumps).
    assert sent_bytes == json.dumps(expected).encode("utf-8")


def test_empty_webhook_skips_with_warning_and_sends_nothing(webhook_server):
    for value in ("", "   \n  "):
        proc = _run_action(
            {
                "webhook": value,
                "severity": "P2",
                "title": "t",
            },
            net=None,  # literal action.yml command; nothing to redirect
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "::warning title=notify-skipped::" in proc.stdout
    assert webhook_server.requests == []


@pytest.mark.parametrize(
    "webhook",
    [
        # look-alike host (substring forgery), plain http, unrelated host
        "https://qyapi.weixin.qq.com.evil.example/hook?key=" + SECRET_KEY,
        "http://qyapi.weixin.qq.com/hook?key=" + SECRET_KEY,
        "https://webhook.example.com/hook?key=" + SECRET_KEY,
    ],
)
def test_rejected_webhooks_send_nothing_and_leak_no_secret(recorder_server, webhook):
    proc = _run_action(
        {"webhook": webhook, "severity": "P1", "title": "t", "body": "b", "links": ""},
        net="local",
        local_port=recorder_server.port,
    )
    assert proc.returncode != 0
    output = proc.stdout + proc.stderr
    assert "::error title=notify-unknown-host::" in output
    assert SECRET_KEY not in output
    assert webhook not in output  # only the hostname may be named, never the URL
    assert recorder_server.requests == []


@pytest.mark.parametrize(
    "response_status,response_body,webhook_template",
    [
        (200, b'{"errcode": 93000, "errmsg": "invalid webhook"}', WECOM_WEBHOOK),
        (200, b'{"errcode": false}', WECOM_WEBHOOK),
        (200, b'{"errcode": 0.0}', WECOM_WEBHOOK),
        (200, b'{"code": 19021, "msg": "hook disabled"}', FEISHU_WEBHOOK),
        (200, b'{"code": 0.0}', FEISHU_WEBHOOK),
        (500, b'{"errcode": 0}', WECOM_WEBHOOK),
        (200, b'<html>gateway error</html>', WECOM_WEBHOOK),
    ],
)
def test_failed_responses_fail_loud(
    recorder_server, response_status, response_body, webhook_template
):
    recorder_server.response_status = response_status
    recorder_server.response_body = response_body
    proc = _run_action(
        {
            "webhook": webhook_template.format(port=recorder_server.port),
            "severity": "P2",
            "title": "t",
        },
        net="local",
        local_port=recorder_server.port,
    )
    assert proc.returncode != 0
    output = proc.stdout + proc.stderr
    assert "::error" in output
    assert len(recorder_server.requests) == 1


def test_network_failure_fails_loud(recorder_server):
    proc = _run_action(
        {
            "webhook": WECOM_WEBHOOK.format(port=recorder_server.port),
            "severity": "P2",
            "title": "t",
        },
        net="fail",
        local_port=recorder_server.port,
    )
    assert proc.returncode != 0
    output = proc.stdout + proc.stderr
    assert "::error title=notify-request-failed::" in output
    assert recorder_server.requests == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"severity": "P3"},
        {"severity": ""},
        {"links": "not-json"},
        {"links": '[{"text": "x"}]'},  # missing url
    ],
)
def test_invalid_inputs_fail_fast_without_sending(recorder_server, overrides):
    proc = _run_action(
        {
            "webhook": WECOM_WEBHOOK.format(port=recorder_server.port),
            "severity": "P2",
            "title": "t",
            **overrides,
        },
        net="local",
        local_port=recorder_server.port,
    )
    assert proc.returncode != 0
    assert "::error" in proc.stdout + proc.stderr
    assert recorder_server.requests == []
