# gate-v2 两把锁进度

## 2026-08-29 workflow 改动

- 当前阶段：implementing / milestone ① workflow
- 本段结论：`quality` 与 `primary` 已各加独立 job 级 concurrency，组名 `gate-required-v2-{job}-${{ github.repository_id }}-${{ github.event.pull_request.number || github.run_id }}`，`cancel-in-progress: true`。顶层仍无 concurrency；`gate`/`ledger` 锁块与 base 逐字节相同。
- 关键决策与已否决方案：组名按卡面建议拼法落地（前缀含 job 名，不与 panel/ledger/shadow 冲突）。不采用顶层一把锁、不共用 quality/primary 一组。验证命令红了一条：`test_concurrency_group_is_required_v2_and_defined_once_at_workflow_level`（第 299 行仍排除 `{ledger, gate}`）；`test_gate_shadow_v2_contract.py` 全绿（顶层缺省未动）。
- 下一步唯一动作：翻转 `tests/test_gate_v2_contract.py` 契约，排除集合改为 `{ledger, gate, quality, primary}`。

## 2026-08-29 契约测试翻转

- 当前阶段：implementing / milestone ② 契约测试
- 本段结论：旧测试 `test_concurrency_group_is_required_v2_and_defined_once_at_workflow_level` 已拆成四个具名测试：顶层无 concurrency；gate/ledger 仍 cancel false；quality/primary 各有独立 cancel true 组（含 job 身份、repository_id、PR 号、run_id 兜底、禁 panel/ledger/shadow 前缀）；其余 job 无 concurrency。`test_gate_shadow_v2_contract.py` 未改。
- 关键决策与已否决方案：无。旧函数名已删除（与新事实矛盾）。`_assert_expensive_job_cancel_lock` 只服务 quality/primary 两处同构断言，不是新抽象层。
- 下一步唯一动作：改写本仓设计文档为两把锁事实（本仓原先没有 `docs/design/gate-convergence-criterion.md`）。
