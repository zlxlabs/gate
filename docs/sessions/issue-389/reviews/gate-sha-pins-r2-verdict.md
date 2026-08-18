# gate-sha-pins R2 verdict（跨仓 producer/consumer 契约与发布顺序）

- **审查对象（gate 冻结点）**：`a6dbd54898d2d914f42153e0cb75592e86782baf..ddbd74ceb180de33203a264705a410228042db08`（base..H0）
- **消费方证据（gate-hub 冻结点）**：`3ed62417dc5b58eef3923d6a8cc6ded686fc0e16`（PR [#393](https://github.com/zlxlabs/gate-hub/pull/393)，`agent/action-archive-cache`，**OPEN**）
- **关联 PR（gate）**：[#70](https://github.com/zlxlabs/gate/pull/70)（`agent/action-sha-pins`，**OPEN**；HEAD `4996c2f` = H0 + R1 文档，无额外代码）
- **审查者**：cursor 执行器（R2 跨仓交叉审查）
- **Dispatch-Id**：`dlg-20260818-030748-3e2499`
- **证据新鲜度**：2026-08-18T03:12:45Z（本机实测；两 PR 仍为 OPEN）

## 本轮新证据（相对 R1）

R1 已证 gate 仓内 workflow 字节与 issue #389 锁定清单一致。R2 新增 **gate-hub producer 侧**实测与发布顺序分析：

1. **gate-hub `runner/action-archive-cache.lock` 字节**（@ `3ed62417`）与 gate H0 两份 v2 workflow 全部 `uses:` SHA **逐项一致**（见下表）。
2. **gate-hub producer 契约测试**（@ `3ed62417`，detached HEAD）：`python3 -m pytest -q tests/test_docker_context.py -k action_archive` → **2 passed**（`test_action_archive_cache_lock_has_only_the_production_four_sha_pins`、`test_action_archive_producer_writes_real_tar_archives_to_runner_layout`）。
3. **gate-hub 红验**：lock 文件变异（错误 40 位 SHA、`v4` tag）→ `test_action_archive_cache_lock_has_only_the_production_four_sha_pins` **失败**（returncode 1）。
4. **gate 消费方红验**（H0 逻辑，本卡复跑）：`@v4`、短 SHA、错误 SHA → `test_production_v2_official_actions_are_exactly_sha_pinned` **失败**（与 R1 一致）。
5. **Dockerfile 消费路径**：`ENV ACTIONS_RUNNER_ACTION_ARCHIVE_CACHE=/opt/actionarchivecache`；镜像 build 阶段 `install-action-archive-cache.sh` 按 lock 拉取 `codeload.github.com/{repo}/tar.gz/{sha}` 并落盘 `{owner}_{repo}/{sha}.tar.gz`（@ `3ed62417`）。
6. **PR 头对齐**：`gh api repos/zlxlabs/gate-hub/pulls/393` → head `3ed62417…` 与冻结点一致；gate #70 head `4996c2f` 仅追加 R1 文档，代码冻结点仍为 `ddbd74c`。

## 四 SHA 跨仓一致性

| action | gate-hub lock | gate-v2.yml | gate-shadow-v2.yml | 一致 |
|---|---|---|---|---|
| `actions/checkout` | `11d5960a326750d5838078e36cf38b85af677262` | 6 步 | 1 步 | ✓ |
| `actions/cache` | `0057852bfaa89a56745cba8c7296529d2fc39830` | 1 步 | —（base 亦无 cache） | ✓ |
| `actions/upload-artifact` | `ea165f8d65b6e75b540449e92b4886f43607fa02` | 6 步 | 1 步 | ✓ |
| `actions/download-artifact` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` | 3 步 | 1 步 | ✓ |

每 workflow 内同一 action 仅一个 SHA 集合（Python 提取脚本 + 合同测试双重确认）。**无 SHA 漂移、无未登记第五 action。**

## 合同测试锁（跨边界）

| 不变式 | Producer（gate-hub） | Consumer（gate） |
|---|---|---|
| lock 仅含生产四 SHA 且与常量表一致 | `tests/test_docker_context.py::test_action_archive_cache_lock_has_only_the_production_four_sha_pins` + `_ACTION_ARCHIVE_ENTRIES` | `test_*_official_actions_are_exactly_sha_pinned` + `EXPECTED_ACTION_REFS` |
| 安装脚本按 lock 拉取真实 tar 到 runner 布局 | `test_action_archive_producer_writes_real_tar_archives_to_runner_layout`（subprocess 调 `install-action-archive-cache.sh`，断言 curl URL 与落盘路径） | —（消费发生在 runner 运行时，由 Actions 解析 `uses:`） |
| 错 SHA / tag 会红 | lock 变异红验（本卡） | workflow 变异红验（本卡 + R1） |
| v2 全量合同仍绿 | — | `pytest -q tests/test_gate_v2_contract.py tests/test_gate_shadow_v2_contract.py` → **103 passed in 2.72s**（@ `ddbd74c`） |

## 发布顺序安全性

gate-hub `runner/README.md`（@ `3ed62417`）明示操作序列：

> 先 bake **新旧** archive 并 **部署镜像** → 再切换 workflow pin → fleet 对齐后移除旧 archive。

结合两 PR 均为 **OPEN**、均未合并：

| 顺序 | 正确性 | 缓存收益 | 判定 |
|---|---|---|---|
| **gate-hub #393 合并 + runner 镜像部署 → gate #70 合并** | 自托管 runner 命中 `ACTIONS_RUNNER_ACTION_ARCHIVE_CACHE`，避免 codeload 拉取 | 完整 | **推荐（文档约定顺序）** |
| **仅 gate #70 先合并**（runner 仍为旧镜像） | SHA pin 仍可通过 runner 原生网络下载解析（与 base `@v4` 同类网络依赖）；README 写明 cache miss 走原生下载 | 无（直至镜像更新） | **可运行但降级**； egress 受限环境风险与 base 相同，非本 diff 新引入 |
| **gate-hub 先部署、gate 未合并** | workflow 仍 `@v4`；缓存存在但未消费 | 无浪费性故障 | 安全 |

**结论**：就代码契约而言发布顺序 **安全**（consumer SHA ⊆ producer lock）；就运维与 issue #389 目标（消除 codeload 依赖）而言，**必须先 land gate-hub #393 并完成 runner 镜像滚动，再 merge gate #70**。README 亦要求 post-merge/post-deploy codeload 429 canary——本地 producer 契约测试 **不能替代** 该验收。

## 运行证据

```text
# gate @ ddbd74c
python3 -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_shadow_v2_contract.py
# → 103 passed in 2.72s

git diff --check a6dbd54898d2d914f42153e0cb75592e86782baf..ddbd74ceb180de33203a264705a410228042db08
# → 无输出

# gate-hub @ 3ed62417
python3 -m pytest -q tests/test_docker_context.py -k action_archive
# → 2 passed, 19 deselected in 0.21s
```

## Findings

### P1

（无）

### P2

| ID | 描述 | 证据 | 建议 |
|---|---|---|---|
| P2-1 | 两仓 PR 均未合并；若 gate #70 先于 gate-hub #393 镜像上线，自托管 v2 运行将错过 archive cache（网络降级路径） | PR #70/#393 `state=OPEN`；README cache-miss 行为 | 合并/部署顺序：gate-hub #393 → runner 镜像 → gate #70；合并后跑 codeload 429 canary |

### P3 / backlog

（无代码缺陷）—— `gate.yml` / `ci.yml` 仍 `@v4` 属 legacy/CI 路径，本卡 scope 外。

## 结论

**PASS** — gate H0 与 gate-hub 冻结点 `action-archive-cache.lock` 四 SHA **逐项完全一致**；producer/consumer 契约测试与红验均有效；发布顺序在正确性上安全，运维上须 gate-hub 镜像先行（P2-1）。
