## 进度 1

- 当前阶段：设计核销
- 本段结论：已完成任务卡归档，并确认 v2 producer 只写本次条目的字段边界；历史比较、历史状态、评论游标和跨条目冲突属于分析侧职责。
- 关键决策与已否决方案：保留 `codex-review-ledger-v2` / `ledger.jsonl` 名称；schema 升为 2；删除 `convergence_projection`，因为现实现读取 prior entries；不新增兼容开关或 producer 网络路径。
- 下一步唯一动作：按核销表改造 `build_ledger.py` 与 action 输入，并先跑 ledger 测试。
