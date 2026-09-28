# DESIGN-note：gate#249 公共 caller 的受管 Silo 供给

基线锁定：`zlxlabs/gate` `85916ed5c4f9d181fec866f15d1bb5f58111c9c8`（远端 main 同 SHA，含 PR #251）。Issue #249 P2；PR #251 合并与 canary 成功都不等于公开 caller 矩阵验收。

## 目标与边界

- 公共 caller 不持有/转发能访问 Silo 的长期 key；quality job 持续执行业务测试，但拿不到该 profile 或 key。
- gate-v2 可信 Silo subprocess 通过显式 managed mode 读 host-managed profile；disposition 继续走显式 legacy env mode。两个入口互斥，managed mode 缺 profile 就失败，不回落 env；不加 broker、policy knob 或重试。
- Gate 现有 Silo key 的 runner-managed 供给已获分阶段授权；但 all28 与真实 adapter 前置尚未通过，因此本卡未创建/部署 profile。#1183 只证明共享缓存隔离，不证明所有 host state 隔离。
- 设计/证据合计新增预算 ≤120 行；原始 runtime C1（`85916ed5c4f9d181fec866f15d1bb5f58111c9c8..0b5da65fa37ec00864d64a698844dc2b69f7642f`）新增代码与测试 189 行，符合 ≤200 行；兼容修订（`0b5da65fa37ec00864d64a698844dc2b69f7642f..8e894d5d2e166d76f94ceb3ebdff6d88c3bd4156`）另按 ≤160 行卡验收，新增 151 行。当前 PR 累计新增代码与测试 296 行，不是单张修订卡预算，也不回溯放宽原卡。只写当前 docs 目录两文件。

## 当前真实消费者（固定 SHA）

- `scripts/silo_store.py::connect()` 从 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`SILO_ENDPOINT` 构造 S3 client；`scripts/silo_exec.sh` 直接运行该可信脚本。
- `.github/workflows/gate-v2.yml` 的 `primary`、`ocr`、`gate`、`ledger` job env 注入 caller AWS keys；15 个 `SILO_EXEC` 消费点再检查 access key。`gate-aggregator/aggregate.py::_silo_configured()` 只看非秘密 `SILO_ENDPOINT`，MagicDNS 也使用该 endpoint。
- 同一 `scripts/silo_store.py::connect()` 还被 `.github/workflows/gate-v2-disposition.yml` 使用：`control` job 在 `[self-hosted, linux, ci]` runner 消费 caller secrets 并通过 `silo_exec.sh` 做 Silo get/put。全局切换 connect 会破坏该路径；C1 必须用显式 managed mode，仅 gate-v2 调用选择该模式，disposition 继续 env mode。
- `quality` job 没有 Silo AWS env。PR #251 的 producer 是 `GITHUB_OUTPUT` bundle → `needs.quality.outputs.ledger_input_bundle` → trusted ledger；禁止 GitHub artifact actions，不另造 artifact 通道。
- `templates/caller-gate-v2.yml` 仍映射两个 Silo secrets；README 仍要求 caller 配置。独立 `gate-v2-disposition.yml` 与 disposition caller 也依赖 caller secrets，但不在 #249 当前文件范围，不能宣称随本卡消除。

## 最小候选与依赖顺序

| 阶段 | 内容 / 锁定判据 |
|---|---|
| C1 前置（先于部署/合并） | Gate 现有 Silo key 供给按已获授权的固定路径候选 `/opt/review-auth/silo.json` 管理：host-managed、root-owned、0600、runner 内只读；quality runner 不可见。all28 与真实 adapter 前置通过前不实施供给、不标 ready/merge/@v2。 |
| C1 兼容修订卡（≤160 行代码/测试） | 可信 `github.repository_id` 只在 Gate `1295374164` 选择 `managed-profile`，其他 caller 选择 `legacy-env`；wrapper 在 managed 模式添加固定 flag 并清除 AWS env，legacy 保留旧 env，空/未知 selector fail-fast。四个 job 的 source 与 AWS env 一致，15 个操作和 aggregate 都走同一 `SILO_EXEC`；aggregate 保留源码路径默认值与 standalone 兼容。保留两个 optional secrets 声明；无改 schema/path/namespace/ACL/终态。 |
| C2 caller/终态验收 | 在 C1 runtime 与供给就绪后，更新 public caller 模板/README/契约测试，移除 public template 的 Silo key mapping；callee optional secret 声明保留为旧 private caller 兼容 API；仅 Gate ID 的四个 job 不消费/不注入，其他 private caller 仍经 legacy env 使用它们。disposition 的 caller key contract 原样保留，另卡裁决迁移。运行 private、public 同仓、external fork、Dependabot、draft→ready 矩阵，读取测试结论、主审 audit 身份与 `SUCCESS`/`SKIPPED`/unavailable 原因；真实矩阵需用户授权平台部署/运行。 |

## 不变式与未知

1. `CapsWriter-ASR-Server` 在锁定 registry 中的 active chain 为 Codex `codex-sub`、Claude `claude-deepseek-v4-pro`、Claude `claude-qwen-3-7-plus`；advisory/shadow 为空，当前 OCR 不活跃。现有 marker probe 只覆盖 Codex CLI 0.146.0；两个 active Claude adapter 都须真实工具路径 probe 后才能合并或推广。任意非缓存 host state 未证，完整结论等 #1136 与各实际 consumer 核验。
2. quality 测试代码与受管 profile 必须物理分离；不能把 runner label 当 mount 隔离证据。profile 路径在 quality consumer 实际不可见、trusted shell 可读，是上线前置验收。
3. Silo key 的 server-side repo/prefix ACL 未核实；目录名含 repo ID 不等于授权隔离。不得从现有 prefix 结构推断公开 PR 只有本仓权限。
4. gate-v2 disposition 使用同一 store 的 legacy env mode，保持其 `[self-hosted, linux, ci]` 供给路径不变；C1 只由显式 wrapper flag 选择 managed mode。PR #251 的 quality/job-output 改动保持现状，不重复实现。

## 已否决方案

复制 Silo key 到 public repo、扩大 org secret 可见性、同 job step/env 隔离、临时双源/fallback、用 #251 合并或 canary 绿代替可信 profile/公开矩阵实证；不创建 broker、新 sandbox 或 overlay。

## 卡片与裁决

- #262（API id `5613642332`）是 #249 的 C1 runtime 子卡；本设计是 C1/C2 先决与上线顺序，C1 可 draft，生产 profile 与各 active adapter 验收前不得 ready/merge/@v2。
- 顾问 event `20260928-191544-codex-09e2bb` 建议保留互斥 managed/env 模式并覆盖五个 store consumer、15 个 workflow argv 与 aggregator 直调；顾问报告未能用 `.git` 验明目标 SHA，且原始探针目录不可见，因此只采纳方案挑战，不将其当 SHA/运行证据。根侧在 gate worktree `85916ed5…` 定点核实 disposition 为 `[self-hosted, linux, ci]`，保留其 legacy env 路径。
