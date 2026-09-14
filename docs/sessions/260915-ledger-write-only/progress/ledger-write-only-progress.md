## 进度 1

- 当前阶段：设计核销
- 本段结论：已完成任务卡归档，并确认 v2 producer 只写本次条目的字段边界；历史比较、历史状态、评论游标和跨条目冲突属于分析侧职责。
- 关键决策与已否决方案：保留 `codex-review-ledger-v2` / `ledger.jsonl` 名称；schema 升为 2；删除 `convergence_projection`，因为现实现读取 prior entries；不新增兼容开关或 producer 网络路径。
- 下一步唯一动作：按核销表改造 `build_ledger.py` 与 action 输入，并先跑 ledger 测试。

## 进度 2

- 当前阶段：producer 与单元测试改造
- 本段结论：`build_ledger.py` 已移除 artifact/comment 网络路径、历史 merge/dedupe、游标、comparison、review round、网络预算和 `--max-entries`；输出改为 schema 2 单行，保留当前输入投影和终态 receipt 校验。`action.yml` 不再传递 token；ledger 测试已删除历史/评论功能用例并补入断网 `main()` 与 import AST 锁定测试。
- 关键决策与已否决方案：`finding_dispositions` 和 `false_positive_count` 保留为当前行字段但 producer 不再读取评论，故本次分别为 `{}` 与 `0`；`disposition_receipt_consumption`/`terminal_source_attempt` 仍按当前 terminal 输入条件出现。
- 下一步唯一动作：更新 `gate-v2.yml` ledger job 的 timeout/env 与契约测试，并运行完整验证。
