# 261019-gate-triage G1b 进度存档（记账区步骤级 continue-on-error）

## 2026-10-20 — 修复与验证

- **当前阶段**：已完成实现与本地验证，提交推送后等待合入。
- **本段结论**：先改 `tests/test_gate_v2_silo_region_guard.py`，再在原 workflow 上运行守卫得到断言失败，完整列出 `Resolve v2 ledger artifacts`、`Read canonical primary audit from primary job output for ledger`、`Download gate terminal envelope for ledger`、`Upload v2 review effectiveness ledger`、`Upload v2 review effectiveness ledger latest pointer` 五项；随后将五步都改为步骤级 `continue-on-error: true`。修正了 job 级 continue-on-error 只影响 run 级结论、不能把失败的 job check 变绿的误导注释；判定区未改。守卫新增反例，证明 job 级设置不能代替步骤级设置，且把判定步骤临时标为记账区但无步骤级设置会被拒。目标回归测试 15 passed；全量测试 1874 passed；`python3 scripts/check_pinned_uses.py` 通过。
- **关键决策与已否决方案**：守卫只接受每个 bookkeeping 触点自身的步骤级 `continue-on-error: true`，不接受 ledger job 级设置；不改判定区、Silo 客户端、记账步骤既有 `GATE-ARCHIVE-DEFERRED` 输出与非 Silo 构建步骤语义。派发时刻主干基线查询失败，继承红无法判定。
- **下一步唯一动作**：提交修复后按卡面做两次改坏红验（步骤级字段缺失、判定区改标 bookkeeping），每次只恢复对应改动；确认干净后推送分支并交报告。

## 2026-10-20 — 续修：步骤级 continue-on-error 后显式门控下游

- **当前阶段**：实现与全量测试完成；提交后需完成 Build 条件反例红验并推送。
- **本段结论**：在 `Compute incremental blocking shadow`、Build 起始标记与 `Build v2 review effectiveness ledger` 上加显式输入条件：job `success()` 且 resolver、ledger-side audit reader outcome 均为 success；terminal 下载 outcome 为 success 或 skipped（仅当实际下载时要求成功）。两个账本上传步骤新增 `success() && steps.build-review-ledger.outcome == 'success'`。守卫扩展为禁止 ledger 中 bookkeeping 之后的无 `continue-on-error` 步骤依赖隐式 `success()`，并断言 Build 引用三个上游 outcome。新增条件契约测试模拟 resolver outcome=failure、job success=true，确认 Build 与两上传均跳过；另有成功路径对照。全量 1878 passed，`check_pinned_uses.py` 通过。
- **关键决策与已否决方案**：保留 `success()` 以继续 fail-closed 于普通非 continue-on-error 步骤失败；单独检查 coe 步骤的 outcome，以避免其 failure 被转换成 success 后误构建。terminal download skipped 表示没有输入产物，允许豁免/未评审场景；若实际执行则必须 success。额外 actionlint 检查未绿：相同路径的原始 workflow 基线也有相同 9 条 shellcheck 警告（当前行号因本轮插入条件偏移），均非本轮修改所致。
- **下一步唯一动作**：提交后只移除 Build 的 `if` 做守卫红验、仅恢复该条件；复测并确认工作树干净后推送，完成报告及门禁答复。

## 2026-10-20 — 续修交付

- **当前阶段**：delivered（续修实现、验证、红验、提交与推送完成）。
- **本段结论**：提交 `3554e632e258b87247ef30da0d348e899e80552d` 为下游条件修复；先提交后移除 Build `if` 的反例令守卫按预期断言失败，恢复唯一改动后相关测试 16 passed、全量 1878 passed、`check_pinned_uses.py` 通过，分支已推送并核实远端 SHA。行动作 lint 的 9 条 shellcheck 警告在上一个 workflow 基线同样存在。
- **关键决策与已否决方案**：保留明确的 `success()` 加上游 outcome 条件；terminal download skipped 作为可选终态输入通过，resolver/read failure 或已运行的 terminal download failure 均跳过 Build 与 ledger 两个上传。
- **下一步唯一动作**：由主脑审查推送分支并安排合入；执行器不再追加代码改动。
