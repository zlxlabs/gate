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

## 2026-08-29 设计文档改写

- 当前阶段：implementing / milestone ③ 设计文档
- 本段结论：本仓原先没有 `docs/design/gate-convergence-criterion.md`（规划卡引用的 280–288 行在 gate-hub）。按允许清单在本仓新建该路径，只写两把锁事实，明确否决顶层一把 cancel true；不复制 hub 全文，hub §12.4 由另一张卡处理。
- 关键决策与已否决方案：不把 hub 524 行设计整份拷进本仓（超预算且越权）。不改 `docs/design/clean-streak-convergence.md` 里已过时的旧测试名指针（不在允许清单）。
- 下一步唯一动作：红验（改 primary 的 cancel-in-progress 为 false，确认新契约转红为 AssertionError）后写报告。

## 2026-08-29 R1 三条 P2 收口

- 当前阶段：repairing / R1 findings F1–F3
- 本段结论：F1 只做文档披露——`docs/design/gate-convergence-criterion.md` 新增「已知限制」节（被 supersede 的旧 run 可能有 panel 无 ledger）；F2 `_assert_expensive_job_cancel_lock` 收紧为完整 group 表达式精确相等（锁定 `pull_request.number || github.run_id` 顺序）；F3 gate/ledger writer 锁收紧为完整 mapping 精确相等（常量 `_WRITER_CONCURRENCY`）。红验通过：反转 quality fallback 顺序与 ledger 组名改一字符均转 AssertionError 后还原。workflow 未改。
- 关键决策与已否决方案：不为 F1 加 continue-on-error / 兜底下载路径（P2 禁新机制，review-discipline）；删除 `_FORBIDDEN_EXPENSIVE_GROUP_PREFIXES` 常量与 quality≠primary 断言（精确相等已蕴含，避免双路径断言体系）。
- 下一步唯一动作：R2 审查（另卡）。
