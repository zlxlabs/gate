# K14 进展：review-record gate_hub_commit 取 primary audit registry_commit

## 里程碑 1：实现 + 契约 fixture 更新（commit 12a7ff4）

- 当前阶段：实现完成，全量测试 1875 passed，反向红验通过，已推送分支。
- 本段结论：`build_live_review_record` 不再把 `gate_hub_commit` 写死为 None，
  而是取同一 entry 的 primary audit `registry_commit`；非 40 位小写 hex 时
  以 `GATE-HUB-COMMIT-INVALID` 开头的 ValueError 直接失败；无 primary 审查
  （skip 等终态）时保持 None 并记准确的 unknown 理由。契约 fixture 与
  `_live_record_audit`  helper 同步换成合法 40 位 hex，旧 `"r"*40` 在新校验下
  本来就是非法输入。
- 关键决策与已否决方案：unknown 缺失理由没有复用原来的
  「live ledger producer has no exact value for this field」，而是写
  「no primary audit for this run so gate_hub_commit is unavailable」——旧文案
  在有审查时是错的（明明有值却说没有），在无审查时又没说清为什么没有。
  否决过「只改赋值、不校验格式」：卡面明确要求 fail-loud，且现网值已被
  gate-hub 侧强制为 40 位 hex，非法值进来一定是上游 bug，必须炸出来。
- 下一步唯一动作：写 delegate 报告并收工（PR 与合并由主脑接手）。
