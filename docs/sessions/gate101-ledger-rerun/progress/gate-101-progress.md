# gate#101 ledger terminal 回落进度

## 2026-09-01 工件解析回落

- 当前阶段：implementing / milestone ① 工件解析回落
- 本段结论：`ledger` 的 `Resolve v2 ledger artifacts` 对 `gate-terminal` 改为与 input/audit 相同的 `attempt <= current` 取最大；不再使用 `exact_attempt=current`。当前 attempt 无 terminal 但有前序时选出该前序；只有未来 attempt 或完全没有 terminal 时仍硬失败并保留原文案。
- 关键决策与已否决方案：去掉已无调用方的 `exact_attempt` 参数，不另做第二套选择函数。归因闸留到下一里程碑，本段回落在聚合器本 attempt 是否运行上暂不区分。
- 下一步唯一动作：加归因闸，仅在当前 attempt 无 terminal 时查 jobs 列表。
