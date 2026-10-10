"""Silo 控制路径零运行期装包：闭集入口测试（白名单）。

背景：review/quality 槽与 Silo 密钥同机执行，任何运行期取包
（包管理器拉取可执行代码）既计费又不可审计。本测试把“会在这些槽里
执行的入口文件”收敛为一个闭集：新增入口必须显式加入 ENTRY_FILES 并
写明理由，否则第一条测试即红。

为什么是白名单而不是黑名单 grep：白名单是有界枚举，黑名单是无界枚举，
grep 永远追不上下一个取包工具。本文件用四层锁：

1. test_entry_set_is_closed — 入口清单本身是闭集（glob 发现 == 允许列表）。
2. test_entries_import_only_stdlib_and_repo — 每个入口只许导入标准库与
   仓内 scripts 包；任何第三方 SDK 导入（含 S3 SDK）在 diff 期变红。
3. test_entries_contain_no_runtime_fetch — 入口文本不得出现取包启动器。
4. test_silo_cli_never_consults_package_manager / test_stdlib_client_end_to_end
   — 动态层：断言聚合器实际发出的 argv，并在“缓存为空 + 假 uv 在 PATH
   首位”的环境跑一次真实 Silo 存取，证明标准库客户端无需取包即可用。

判据可证伪：所有动态断言都检查实际 argv/实际调用，不依赖“本机恰好没有
uv”。在装有 uv 的机器上，若有人重新引入包管理器入口，argv 断言与假 uv
标记同样变红（见报告中的红/绿两次输出）。
"""

from __future__ import annotations

import ast
import http.server
import os
import socket
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

# 闭集：会在 review/quality/ledger/disposition/CI 槽执行的入口文件。
# key: 仓内相对路径；value: 该入口所在的槽与纳入理由。
ENTRY_FILES = {
    # gate job：跨 run 消费 Silo（terminal 历史、disposition receipts、
    # previous findings）。本卡主修对象。
    ".github/actions/gate-aggregator/aggregate.py": "gate 槽聚合器",
    ".github/actions/gate-aggregator/convergence.py": "被聚合器载入的同目录模块",
    # quality 槽：非门禁 advisory（缺包时退化，不取包）。
    ".github/actions/diff-coverage-advisory/advisory.py": "quality 槽 advisory 本体",
    ".github/actions/diff-coverage-advisory/action.yml": "quality 槽 advisory 包装器",
    # disposition 槽：receipt 铸造。
    ".github/actions/gate-disposition/issue_receipt.py": "disposition 槽 receipt 铸造",
    # quality 槽：仅调 git，无网络取包。
    ".github/actions/pr-size-preflight/preflight.py": "quality 槽 preflight 本体",
    ".github/actions/pr-size-preflight/action.yml": "quality 槽 preflight 包装器",
    # ledger 槽。
    ".github/actions/review-ledger/build_ledger.py": "ledger 槽本体",
    ".github/actions/review-ledger/action.yml": "ledger 槽包装器",
    # 各槽共享：纯标准库 Silo 客户端与包装器。
    # 各槽共享：主机取码服务（service/origin）协议的唯一实现，纯标准库。
    "scripts/gate_source.py": "各槽共享取码协议实现（客户端+只读镜像锁+物化）",
    "scripts/silo_store.py": "Silo 标准库客户端（SigV4 自签）",
    "scripts/silo_exec.sh": "Silo 包装器（exec python3 + 仓内脚本）",
    # 各槽共享：checkout / MagicDNS（仅调 git、curl 固定 SHA 脚本、系统 DNS）。
    "scripts/gate_bounded_retry.py": "各槽 checkout 与 MagicDNS",
    # 被多个入口导入的发布脱敏。
    "scripts/scrub_outbound.py": "被入口导入的脱敏库",
    # quality/primary 入口判定（review-expected 分类）。
    "scripts/classify_pr_reviewable_paths.py": "quality/primary 入口分类",
    # 本仓 CI 入口：同属“运行期装包计费”相邻面，纳入防漂移。
    "scripts/check_pinned_uses.py": "本仓 CI pin 检查",
    # tag 同步/发布入口（hosted 槽），纳入闭集防漂移。
    "scripts/v2_release_state.py": "发布状态入口",
    "scripts/v2_tag_guard.py": "tag 门禁入口",
    "scripts/v2_tag_promotion_evidence.py": "tag 晋升证据入口",
    "scripts/v2_source_failure_probe.sh": "tag 同步自托管失败路径探针（v2 抬升前置，gate-hub#1426）",
}

# 运行期取包启动器（黑名单只锁 payload，白名单锁清单，见模块 docstring）。
# 注意：本表不得包含被禁 SDK 自身的名字——“import 第三方包”由
# test_entries_import_only_stdlib_and_repo 捕获，两层正交。
BANNED_LAUNCHERS = (
    "uv run",
    "uvx",
    "uv tool",
    "pip install",
    "python -m pip",
    "npx",
    "pnpm dlx",
    "go install",
    "playwright install",
)

# 标准库白名单 = 运行解释器自带的模块名全集 + 仓内包 + __future__。
IMPORT_ALLOWLIST_EXTRA = frozenset({"__future__", "scripts"})


def _discover_entries() -> set[str]:
    discovered: set[str] = set()
    for pattern in (
        ".github/actions/**/*.py",
        ".github/actions/*/action.yml",
        "scripts/*.py",
        "scripts/*.sh",
    ):
        discovered.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern))
    return discovered


def test_entry_set_is_closed():
    discovered = _discover_entries()
    assert discovered == set(ENTRY_FILES), (
        "入口清单漂移：新增入口文件必须显式加入 ENTRY_FILES 并写明槽与理由；"
        f"仅删除旧文件也必须同步更新清单。新增={sorted(discovered - set(ENTRY_FILES))} "
        f"减少={sorted(set(ENTRY_FILES) - discovered)}"
    )


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_entries_import_only_stdlib_and_repo():
    allowed = set(sys.stdlib_module_names) | IMPORT_ALLOWLIST_EXTRA
    violations: dict[str, list[str]] = {}
    for rel in sorted(ENTRY_FILES):
        if not rel.endswith(".py"):
            continue
        foreign = sorted(_imports_of(ROOT / rel) - allowed)
        if foreign:
            violations[rel] = foreign
    assert not violations, (
        "入口引入了标准库/仓内包之外的模块（第三方 SDK 必须在 diff 期变红）:"
        f" {violations}"
    )


def test_entries_contain_no_runtime_fetch():
    violations: dict[str, list[str]] = {}
    for rel in sorted(ENTRY_FILES):
        text = (ROOT / rel).read_text(encoding="utf-8")
        hits = [token for token in BANNED_LAUNCHERS if token in text]
        if hits:
            violations[rel] = hits
    assert not violations, f"入口含有运行期取包启动器: {violations}"


def _make_fake_uv(bin_dir: Path, marker: Path) -> Path:
    fake = bin_dir / "uv"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "CONSULTED $@" >> "{marker}"\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _aggregate_module():  # noqa: ANN202 — 测试内按需载入被测模块
    import importlib.util

    path = ROOT / ".github" / "actions" / "gate-aggregator" / "aggregate.py"
    spec = importlib.util.spec_from_file_location("gate_aggregate_e2e", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_silo_cli_never_consults_package_manager(tmp_path, monkeypatch):
    """即使 PATH 首位有 uv，聚合器发出的 argv 也不得经过它。"""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    marker = tmp_path / "uv-consulted.txt"
    _make_fake_uv(fake_bin, marker)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("SILO_STORE", str(ROOT / "scripts" / "silo_store.py"))

    agg = _aggregate_module()
    proc = agg._silo_cli(["--help"])

    assert proc.returncode == 0, proc.stderr
    assert not marker.exists(), "聚合器咨询了包管理器（假 uv 被调用）"
    assert "usage" in (proc.stdout or "").lower()


class _StubS3Handler(http.server.BaseHTTPRequestHandler):
    store: dict[str, bytes] = {}
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, content_type: str = "application/xml") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self) -> None:  # noqa: N802 — http.server 回调命名
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        key = urllib.parse.unquote(self.path.split("?", 1)[0].split("/", 2)[-1])
        type(self).store[key] = body
        self._send(200, b"")

    def do_GET(self) -> None:  # noqa: N802 — http.server 回调命名
        raw_path, _, raw_qs = self.path.partition("?")
        query = urllib.parse.parse_qs(raw_qs)
        parts = urllib.parse.unquote(raw_path).split("/", 2)
        if query.get("list-type") == ["2"]:
            prefix = query.get("prefix", [""])[0]
            keys = sorted(k for k in type(self).store if k.startswith(prefix))
            items = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<ListBucketResult><IsTruncated>false</IsTruncated>{items}</ListBucketResult>"
            ).encode()
            self._send(200, body)
            return
        key = parts[-1] if len(parts) == 3 else ""
        if key in type(self).store:
            self._send(200, type(self).store[key], "application/octet-stream")
        else:
            self._send(
                404,
                b"<Error><Code>NoSuchKey</Code><Message>not found</Message></Error>",
            )

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def stub_silo():
    _StubS3Handler.store = {}
    server = http.server.HTTPServer(("127.0.0.1", 0), _StubS3Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def test_stdlib_client_end_to_end_without_package_cache(tmp_path, monkeypatch, stub_silo):
    """缓存为空环境的真实 Silo 存取：子进程 argv 即聚合器同款形态。

    - 解释器加 -S（禁 site-packages）：证明客户端仅凭标准库可用；
    - HOME/UV_CACHE_DIR 指向空目录：无任何预热缓存可用；
    - 假 uv 在 PATH 首位：若有任何取包行为，标记文件落盘即红。
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    marker = tmp_path / "uv-consulted.txt"
    _make_fake_uv(fake_bin, marker)
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()

    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(empty_home),
        "SILO_ENDPOINT": stub_silo,
        "AWS_ACCESS_KEY_ID": "test-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret",
        "SILO_BUCKET": "ci-artifacts",
    }
    store = str(ROOT / "scripts" / "silo_store.py")
    base = [sys.executable, "-S", store]
    key = "d30/7/artifact-e2e/payload.json"
    payload_file = tmp_path / "payload.json"
    payload_file.write_bytes(b'{"e2e": true}')
    dest = tmp_path / "got"

    put = subprocess.run(
        [*base, "put", "--tier", "d30", "--repo-id", "7",
         "--name", "artifact-e2e", "--file", str(payload_file)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert put.returncode == 0, put.stderr
    listed = subprocess.run(
        [*base, "list", "--prefix", "d30/7/", "--dest", str(dest)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert key in listed.stdout.splitlines()
    assert (dest / "artifact-e2e" / "payload.json").read_bytes() == b'{"e2e": true}'

    assert not marker.exists(), "标准库客户端路径咨询了包管理器（假 uv 被调用）"


def test_closed_set_predicate_can_go_red():
    """判据自证：谓词本身必须能变红（喂已知为否的输入），否则是恒真判据。"""
    assert any(token in "uv run --with something" for token in BANNED_LAUNCHERS)
    assert not any(token in "python3 scripts/silo_store.py list" for token in BANNED_LAUNCHERS)
    allowed = set(sys.stdlib_module_names) | IMPORT_ALLOWLIST_EXTRA
    assert "json" in allowed and "definitely-not-a-module" not in allowed
