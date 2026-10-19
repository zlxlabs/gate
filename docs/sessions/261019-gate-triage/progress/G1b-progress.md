# 261019-gate-triage G1b 进度存档（记账区步骤级 continue-on-error）

## 2026-10-20 — 修复与验证

- **当前阶段**：已完成实现与本地验证，提交推送后等待合入。
- **本段结论**：先改 `tests/test_gate_v2_silo_region_guard.py`，再在原 workflow 上运行守卫得到断言失败，完整列出 `Resolve v2 ledger artifacts`、`Read canonical primary audit from primary job output for ledger`、`Download gate terminal envelope for ledger`、`Upload v2 review effectiveness ledger`、`Upload v2 review effectiveness ledger latest pointer` 五项；随后将五步都改为步骤级 `continue-on-error: true`。修正了 job 级 continue-on-error 只影响 run 级结论、不能把失败的 job check 变绿的误导注释；判定区未改。守卫新增反例，证明 job 级设置不能代替步骤级设置，且把判定步骤临时标为记账区但无步骤级设置会被拒。目标回归测试 15 passed；全量测试 1874 passed；`python3 scripts/check_pinned_uses.py` 通过。
- **关键决策与已否决方案**：守卫只接受每个 bookkeeping 触点自身的步骤级 `continue-on-error: true`，不接受 ledger job 级设置；不改判定区、Silo 客户端、记账步骤既有 `GATE-ARCHIVE-DEFERRED` 输出与非 Silo 构建步骤语义。派发时刻主干基线查询失败，继承红无法判定。
- **下一步唯一动作**：提交修复后按卡面做两次改坏红验（步骤级字段缺失、判定区改标 bookkeeping），每次只恢复对应改动；确认干净后推送分支并交报告。
