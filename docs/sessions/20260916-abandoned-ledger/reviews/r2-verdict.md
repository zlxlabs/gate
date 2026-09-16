# R2 独立审查结论

审查对象固定为 `8675302d82963b46cfbfe40ab6ae967f33e92384..e6f9b6ca6a9128da0fee980d2ecc51ff0235b1bd`，基线与被审 head 均为固定 SHA；risk-tier 为 `personal`。Spec 为本 head 的 `docs/sessions/20260916-abandoned-ledger/design.md`。结论：**通过，无 P1/P2/P3 finding**。

## 审查范围与增量四问

- 增量 `214fb0beba3419e07ac2b42dbf331541f253b9c2..e6f9b6ca6a9128da0fee980d2ecc51ff0235b1bd` 只补充契约文档、测试/夹具与证据更正；它对应 raw 日志消费边界、三处 env 契约、resolver 反向矩阵和真实 quality job ID，未扩大已登记语义。
- 增量未新增生产抽象、依赖或配置层；`PRIMARY_RESULT_RAW` 是现有 step env/log 诊断载体，不是 terminal/ledger 状态。
- 未新增状态、fallback、重试或防御式吞错；规范化结果只复用既有 `cancelled` 路径，未知值仍 fail-closed。
- 全量生产 diff 仅改 `.github/workflows/gate-v2.yml` 的三处 env 传递，以及取消时 audit artifact 的可选选择/下载；Aggregate、Publish、Resolve 的消费边界没有遗留未声明的业务双路径。其余全量变更为 docs、tests、fixture。

## Findings

无。以下结论覆盖误拒/误放、日志实际 consumer、终态与账本边界：

- `needs.primary.result == 'abandoned'` 在 `gate-v2.yml:1204-1206,1293-1295,1380-1382` 仅归一化为既有 `cancelled`；`PRIMARY_RESULT_RAW` 保留原值。Aggregate 通过 `--primary-result` 消费 normalized 值，raw 不进入业务 schema，符合 spec 对 raw 载体的限定。
- GitHub runner 固定源码 `80bb1fb827fa44d489263061e71ef4adba7ad8cd` 的 `ScriptHandler.PrintActionDetails` 会枚举 step env 并输出日志（`ScriptHandler.cs:128-137`）；`StepsRunner.cs:120-128` 合并 step.env，`ExecutionContext.cs:1099-1106` 经 SecretMasker 写日志。因此 raw 的实际消费方是 Actions 步骤日志，不是 Python 进程，且该载体已由真实日志证明。
- 真实 job `103681220092` 的白名单日志行记录 `REVIEW_EXPECTED: true`、`QUALITY_RESULT: success`、`PRIMARY_RESULT: abandoned`；attempt-1 jobs API 核实 quality 为 `103678532493`，primary 为 `103678552561`，ledger 为 `103681220092`。
- normalized `cancelled` 在 `aggregate.py:728-731` 进入 `review_unavailable/primary_cancelled`，`gate_result` 为 `unavailable`，不会成为 gate 通过；未知值在 `aggregate.py:663-673` 仍拒绝。
- terminal 只写规范化四值和既有原因/分类，写入点为 `aggregate.py:2188-2193`；ledger 仍使用既有缺 audit 投影 `review.status=not_run`、`verdict=null`、`result=null`。取消分支的 audit 选择在 `gate-v2.yml:1471-1477` 放宽，input 与 terminal 仍在 `1464-1477` 必需；success/failure 的 audit 要求未放松。空 audit 的下载在 `1602-1608` 由 artifact ID 为空而跳过。
- artifact 前缀同时绑定 repository、head、run ID，attempt 受当前 attempt 上限约束；terminal 校验 `repository/pr/run_id/run_attempt/head_sha`。draft、fork、hosted、classify guard 不在本 diff 中改变。
- terminal 生成前仅执行读取/本地 artifact 处理与 summary/receipt 文件写入；PR 面板发布在 terminal 上传成功后单独的 `--publish-only` 步骤执行，且从 terminal 读取，不以 raw 或 normalized env 另行判决。

## 验证证据

- `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py -k 'observed_abandoned or allows_missing_audit_for_observed_abandoned or still_requires'`：`7 passed, 136 deselected`。
- `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k 'observed_abandoned_fixture_produces_fail_terminal_and_unreviewed_ledger_row'`：`1 passed, 252 deselected`。
- `python3 scripts/check_pinned_uses.py`：9 个 workflow/action metadata 文件全部通过。
- `git diff --check 8675302d82963b46cfbfe40ab6ae967f33e92384..e6f9b6ca6a9128da0fee980d2ecc51ff0235b1bd`：通过。
- 主脑提供的本轮 OCR 状态为 `reviewed`；按任务卡未重复运行 OCR，本状态不作为本独立审查结论。

## 外部证据与未知

- `gh api repos/zlxlabs/agent-config/actions/permissions/artifact-and-log-retention` 返回 `days=90`、`maximum_allowed_days=90`；GitHub Actions 官方文档说明日志/制品受 retention policy 约束。raw 因此是限期排障证据，不是永久账本；可提前删除，spec 已明确接受此边界。
- 未重跑或发布外部 workflow；本轮以固定 runner 源码、真实历史 job 白名单日志、attempt-1 jobs API 与本地 producer/consumer 测试核对跨边界契约。
- 取消根因仍未知；本修复没有声称解决 gate-hub#803/#818 全部子项。该未知不影响本 diff 的取消归一化与 fail-closed 判定。
