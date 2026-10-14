# gate-20261014-41：共享 quality 入口按需准备 mise（gate#314）

## 背景与目标

zlxlabs/herdweb actions run 38427594289（head `2d37b771`）：`gate / quality` 死于
`./scripts/gate-quality: 9: mise: not found`（exit 127）。业务入口假定 mise 存在，
但共享 quality job 没有工具管理器准备接口；GitHub 的 reusable workflow 也不允许
caller 把自己的 setup 步骤插进 quality job 的步骤序列。

目标：gate-v2.yml 新增 boolean input `quality_prepare_mise`（default false）。仅当
quality 处于 entry 模式**且** caller 显式 opt-in 时，用官方 `jdx/mise-action@v3`
（`install: false`）准备 mise 管理器本身。业务入口继续拥有 mise trust、工具安装与
全部项目依赖；存量 caller 不传即行为不变。

## 非目标

不改 herdweb 或任何业务仓、runner、限额/重试/证书；不改 primary/aggregator/权限/
路由/Silo/legacy；不做通用 setup 脚本框架、UI action；不做 mise 二进制
version+sha 双锁；不修 herdweb 既有 TLS 问题（不在本卡证据链内，不宣布已修）。

## 不变式（tests/test_gate_quality_mise.py 锁死）

1. **默认关闭**：input `type: boolean`、`default: false`；不传该 input 的 caller
   （workflow_call 取默认值）准备步骤必然跳过，对关闭开关或 legacy 路径零行为变化。
2. **entry 门槛**：准备步骤 if 必须恰好是
   `steps.quality-entry.outputs.mode == 'entry' && inputs.quality_prepare_mise`；
   四种 entry/legacy × true/false 组合加未传 input，求值仅 entry+true 为真，
   legacy 自动探测路径永不准备 mise。
3. **只引导管理器、失败即停**：`uses: jdx/mise-action@v3`、`with: install: false`
   （不装项目工具/依赖，不与业务入口重复准备，不自建 curl 安装器）；步骤无
   continue-on-error / always()，原始失败直接阻断 quality、不进入业务入口。步骤
   位于 Detect repository quality entry 之后、Run scripts/gate-quality 之前，业务
   入口仍恰好被调用一次。

## 单 consumer 必要性

workflow_call 只透传 inputs/secrets，caller 无法注入 job 内部步骤，所以「准备工具
管理器」必须是本 workflow 自带的 opt-in input，而不是平台插件或脚本框架；当前唯一
已知 consumer 是 herdweb 的 entry 入口（修复 38427594289 这类 127）。

## 验收

- 新测试读真实 gate-v2.yml（生产者即 caller 经 @v2 消费的对象，非自造 fixture）：
  红在 base、绿在实现；变异（default 改 true / 去 entry guard / install 改 true）
  各自 AssertionError 后还原。
- 全量 pytest 绿；`python3 scripts/check_pinned_uses.py` OK；`git diff --check` 干净。
- README inputs 表 + 质量入口段写清契约：默认关闭、仅供 mise 入口引导、不装项目
  依赖、业务所有权不变，附最小启用示例一行。
- 本卡不宣称完整修复：herdweb 侧真实 opt-in 与执行由其 owner 后续验收。
