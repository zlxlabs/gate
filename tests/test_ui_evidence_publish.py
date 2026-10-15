"""public ui-evidence-publish（gate-20261014-48，refs gate#314）契约与行为测试。

锁定不变量：manifest 契约（inputs/PR guard/env 映射/action_path）；空 URL 在任何
gh 请求前失败；分页流经真实 jq slurp 合并、重复 marker 只取首个 id PATCH；无匹配
POST；API 错误非零上抛；正文如实反映 success/failure/unknown；深链含完整 40 位
SHA；末尾斜杠归一化；subject 引号/反斜杠不破坏 jq。E2E：生产脚本真实 bash 子进程
+ 系统 jq 消费保留的多页 GH JSON fixture；假 gh 只记录实际 argv（含 `-f body=`
bytes），显式假 token，绝不连真实 GitHub。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = ROOT / ".github/actions/ui-evidence-publish/action.yml"
SCRIPT = ROOT / ".github/actions/ui-evidence-publish/sticky-comment.sh"
REPO = "zlxlabs/herdweb"
FULL_SHA = "a" * 40
BASE_URL = "https://evidence.example.com:8443"

FAKE_GH = """#!/usr/bin/env bash
set -u
printf '%s\\0' "$@" >> "$FAKE_GH_DIR/argv.log"
case " $* " in
  *" --paginate "*)
    if [ "${FAKE_GH_FAIL:-}" = get ]; then echo "gh: fake API error" >&2; exit 1; fi
    cat "$FAKE_GH_DIR/page1.json" "$FAKE_GH_DIR/page2.json" ;;
  *)
    if [ -n "${FAKE_GH_FAIL:-}" ]; then echo "gh: fake API error" >&2; exit 1; fi ;;
esac
exit 0
"""


def _manifest() -> dict:
    for path in (ACTION_YML, SCRIPT):
        assert path.exists(), (
            f"public action 文件缺失: {path.relative_to(ROOT)} —— 先落盘 action.yml 与脚本再实现"
        )
    return yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))


def _run(tmp_path: Path, *, status="success", subject="UI", url=BASE_URL,
         page1="[]", page2="[]", fail=None, sha=FULL_SHA):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    gh = fakebin / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    rec = tmp_path / "rec"
    rec.mkdir(exist_ok=True)
    (rec / "page1.json").write_text(page1, encoding="utf-8")
    (rec / "page2.json").write_text(page2, encoding="utf-8")
    env = {
        # 白名单 env：显式假 token，不继承真实环境（无 GH_TOKEN/GITHUB_* 泄漏面）。
        "PATH": f"{fakebin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path),
        "GH_TOKEN": "fake-token-never-real",
        "GITHUB_REPOSITORY": REPO,
        "PR_NUMBER": "7",
        "E2E_STATUS": status,
        "SUBJECT": subject,
        "HEAD_SHA": sha,
        "FAKE_GH_DIR": str(rec),
    }
    if url is not None:
        env["EVIDENCE_BASE_URL"] = url
    if fail:
        env["FAKE_GH_FAIL"] = fail
    assert SCRIPT.exists(), f"生产脚本缺失: {SCRIPT.relative_to(ROOT)}"
    proc = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    return proc, rec


def _calls(rec: Path) -> list[list[bytes]]:
    """把 argv.log（NUL 分隔）按 gh api 调用边界切开，保留原始 bytes。"""
    log = rec / "argv.log"
    if not log.exists():
        return []
    calls: list[list[bytes]] = []
    for tok in log.read_bytes().rstrip(b"\0").split(b"\0"):
        if tok == b"api":
            calls.append([tok])
        else:
            assert calls, f"gh 调用外出现 argv 片段: {tok!r}"
            calls[-1].append(tok)
    return calls


def _body(call: list[bytes]) -> bytes:
    for i, tok in enumerate(call):
        if tok == b"-f" and call[i + 1].startswith(b"body="):
            return call[i + 1][len(b"body="):]
    raise AssertionError(f"该 gh 调用没有 -f body=: {call!r}")


def test_action_manifest_contract():
    manifest = _manifest()
    assert manifest["runs"]["using"] == "composite"
    inputs = manifest["inputs"]
    for name in ("e2e-status", "head-sha", "evidence-base-url"):
        assert inputs[name].get("required") is True, name
        assert "default" not in inputs[name], f"{name} 必须无默认（显式传参）"
    assert inputs["subject"].get("default") == "UI"
    assert inputs["github-token"].get("default") == "${{ github.token }}"
    for name, spec in inputs.items():
        assert "${{" not in spec.get("description", ""), f"input {name} 描述含会被 GitHub 求值的表达式"
    step = manifest["runs"]["steps"][0]
    assert step["if"] == "${{ github.event_name == 'pull_request' }}", "必须 PR 事件 only"
    assert "github.action_path" in step["run"], "脚本须从 github.action_path 解析"
    assert "sticky-comment.sh" in step["run"]
    assert step["env"] == {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "GITHUB_REPOSITORY": "${{ github.repository }}",
        "PR_NUMBER": "${{ github.event.pull_request.number }}",
        "E2E_STATUS": "${{ inputs.e2e-status }}",
        "SUBJECT": "${{ inputs.subject }}",
        "HEAD_SHA": "${{ inputs.head-sha }}",
        "EVIDENCE_BASE_URL": "${{ inputs.evidence-base-url }}",
    }
    text = ACTION_YML.read_text(encoding="utf-8") + SCRIPT.read_text(encoding="utf-8")
    assert "ts.net" not in text and "tailnet" not in text.lower(), "私有部署痕迹回流进 public action"


def test_url_missing_fails_before_any_gh_call(tmp_path):
    """不变量 1：URL 未设置/空串都必须在任何 gh 请求前失败。"""
    for url in (None, ""):
        proc, rec = _run(tmp_path, url=url)
        assert proc.returncode != 0, f"url={url!r} 却退出 0: {proc.stdout}"
        assert "UI-EVIDENCE-URL-MISSING" in proc.stderr
        assert not (rec / "argv.log").exists(), f"url={url!r} 时发生了 gh 请求"


def test_paginated_merge_patches_first_matching_id(tmp_path):
    """不变量 2：两页流合并，重复 marker 只取首个 id 单值 PATCH，不 POST。"""
    marker = "<!-- ui-evidence-bot:UI -->"
    page1 = json.dumps([
        {"id": 11, "body": "<!-- ui-evidence-bot:Reader --> 其他界面"},
        {"id": 42, "body": f"{marker}\n### 旧版本评论"},
    ], ensure_ascii=False)
    page2 = json.dumps([
        {"id": 87, "body": f"{marker} 第二页重复 marker"},
        {"id": 99, "body": "普通评论"},
    ], ensure_ascii=False)
    proc, rec = _run(tmp_path, page1=page1, page2=page2)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(rec)
    assert calls[0][:3] == [b"api", b"--paginate", f"repos/{REPO}/issues/7/comments".encode()]
    patches = [c for c in calls if b"PATCH" in c]
    assert len(patches) == 1, calls
    assert patches[0][:4] == [b"api", b"--method", b"PATCH",
                              f"repos/{REPO}/issues/comments/42".encode()]
    body = _body(patches[0])
    assert marker.encode() in body
    assert f"{BASE_URL}/reports?search={FULL_SHA}".encode() in body, "深链须含完整 40 位 SHA"
    assert "✅".encode() in body
    assert not any(b"POST" in c for c in calls), "已有评论不应 POST"


def test_creates_comment_when_no_marker_on_any_page(tmp_path):
    proc, rec = _run(tmp_path, page1=json.dumps([{"id": 11, "body": "普通评论"}]),
                     page2=json.dumps([]))
    assert proc.returncode == 0, proc.stderr
    posts = [c for c in _calls(rec) if b"POST" in c]
    assert len(posts) == 1
    assert posts[0][:4] == [b"api", b"--method", b"POST",
                            f"repos/{REPO}/issues/7/comments".encode()]
    body = _body(posts[0]).decode("utf-8")
    assert "<!-- ui-evidence-bot:UI -->" in body
    assert "不进 branch protection" not in body
    assert "不阻断合并" not in body


@pytest.mark.parametrize("status,expected,forbidden", [
    ("success", ["✅"], ["❌", "⚪️"]),
    ("failure", ["❌"], ["✅", "⚪️"]),
    ("unknown", ["⚪️ unknown"], ["✅", "❌"]),  # unknown 不得假报 success/failure
])
def test_body_reflects_real_e2e_status(tmp_path, status, expected, forbidden):
    """不变量 3：正文如实反映真实状态。"""
    proc, rec = _run(tmp_path, status=status)
    assert proc.returncode == 0, proc.stderr
    body = _body([c for c in _calls(rec) if b"POST" in c][0]).decode("utf-8")
    for token in expected:
        assert token in body, f"status={status} 正文缺 {token}: {body}"
    for token in forbidden:
        assert token not in body, f"status={status} 正文不该有 {token}: {body}"


def test_subject_with_quotes_and_backslash_reaches_marker(tmp_path):
    subject = 'He said "hi" \\ back'
    marker = f"<!-- ui-evidence-bot:{subject} -->"
    proc, rec = _run(tmp_path, subject=subject,
                     page1=json.dumps([{"id": 5, "body": f"{marker} 旧"}]), page2="[]")
    assert proc.returncode == 0, proc.stderr
    patches = [c for c in _calls(rec) if b"PATCH" in c]
    assert len(patches) == 1
    assert f"repos/{REPO}/issues/comments/5".encode() in patches[0]
    assert marker.encode() in _body(patches[0]), "引号/反斜杠经 jq --arg 须原样存活"


def test_trailing_slash_in_base_url_does_not_double_slash(tmp_path):
    proc, rec = _run(tmp_path, url=BASE_URL + "/")
    assert proc.returncode == 0, proc.stderr
    body = _body([c for c in _calls(rec) if b"POST" in c][0]).decode("utf-8")
    assert f"{BASE_URL}/reports?search={FULL_SHA}" in body
    assert "//reports" not in body


@pytest.mark.parametrize("fail", ["get", "patch", "post"])
def test_api_error_exits_nonzero_without_success_output(tmp_path, fail):
    """不变量 2 后半：API 错误非零上抛，且不输出成功字样。"""
    page1 = json.dumps([{"id": 42, "body": "<!-- ui-evidence-bot:UI --> 旧"}])
    proc, rec = _run(tmp_path, page1=page1, page2="[]", fail=fail)
    assert proc.returncode != 0, f"fail={fail} 却退出 0"
    assert "updated comment" not in proc.stdout and "created comment" not in proc.stdout


def test_post_api_error_without_marker_exits_nonzero(tmp_path):
    """无 marker 夹具时实际走 GET→POST；POST 失败须非零上抛且错误可见，不得误走 PATCH。"""
    proc, rec = _run(
        tmp_path,
        page1=json.dumps([{"id": 11, "body": "普通评论"}]),
        page2="[]",
        fail="post",
    )
    assert proc.returncode != 0, f"POST 失败却退出 0: {proc.stderr!r}"
    assert "updated comment" not in proc.stdout and "created comment" not in proc.stdout
    assert "gh: fake API error" in proc.stderr
    calls = _calls(rec)
    kinds = []
    for call in calls:
        if b"--paginate" in call:
            kinds.append("GET")
        elif b"POST" in call:
            kinds.append("POST")
        elif b"PATCH" in call:
            kinds.append("PATCH")
        else:
            kinds.append("OTHER")
    assert kinds == ["GET", "POST"], f"请求序列应为 GET→POST 而非 PATCH: {calls!r}"
    assert calls[0][:3] == [b"api", b"--paginate", f"repos/{REPO}/issues/7/comments".encode()]
    assert calls[1][:4] == [b"api", b"--method", b"POST",
                            f"repos/{REPO}/issues/7/comments".encode()]
