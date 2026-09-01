# gate#101 ledger terminal 回落进度

## 2026-09-01 工件解析回落

- 当前阶段：implementing / milestone ① 工件解析回落
- 本段结论：`ledger` 的 `Resolve v2 ledger artifacts` 对 `gate-terminal` 改为与 input/audit 相同的 `attempt <= current` 取最大；不再使用 `exact_attempt=current`。当前 attempt 无 terminal 但有前序时选出该前序；只有未来 attempt 或完全没有 terminal 时仍硬失败并保留原文案。
- 关键决策与已否决方案：去掉已无调用方的 `exact_attempt` 参数，不另做第二套选择函数。归因闸留到下一里程碑，本段回落在聚合器本 attempt 是否运行上暂不区分。
- 下一步唯一动作：加归因闸，仅在当前 attempt 无 terminal 时查 jobs 列表。

## 2026-09-01 归因闸

- 当前阶段：implementing / milestone ② 归因闸
- 本段结论：当前 attempt 无 terminal 且选出前序时，才读可注入的 jobs 列表；`gate` / `… / gate` 出现则硬失败，文案点名聚合器本 attempt 跑了却没写终态。工件齐备时不读 jobs（缺失或坏 JSON 都不报错）。生产路径在未注入文件时用 `gh api …/attempts/{n}/jobs`，失败不静默回落。
- 关键决策与已否决方案：jobs 数据走可选 argv 文件，测试离线可驱；生产用 subprocess 按需调用而不是 shell 先无条件拉 jobs。不把 `gate / quality` 之类前缀误判成聚合器。
- 下一步唯一动作：放宽 `build_ledger` 对 terminal `run_attempt` 的等值校验。
