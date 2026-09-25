# PRIMARY_RESULT 域外值统一归一化

## 决策

三个消费入口（gate 聚合、状态面板、ledger resolver）使用同一 GitHub Actions 表达式：
`success`、`failure`、`cancelled`、`skipped` 原样透传，其余值（包括空串）映射为 `cancelled`。
当原始值与归一化结果不同时，各入口写一条 `::warning::`，并用 Python `repr` 保留空串等值的可辨识性。

Ledger resolver 继续校验归一化后的四值域。`cancelled` 时允许缺少 primary audit；质量输入和 gate terminal 的既有要求不变。聚合器仍按 `primary_cancelled` 路径判失败并写 terminal，ledger 将 `review.status=not_run` 记一行。

## 推翻旧口径的出处

gate#178 的设计记录曾决定仅把已观测的 `abandoned` 映射为 `cancelled`，未知值继续失败：
[2026-09-16 设计记录](../20260916-abandoned-ledger/design.md)。gate-hub#803 在 2026-09-16 的评论也记录了该窄口径。

本决策按 [gate-hub#1079](https://github.com/zlxlabs/gate-hub/issues/1079) 推翻它：当 primary 没有产出裁决时，ledger 应如实记录 `not_run`，不能因域外值再让记账步骤崩溃。归一化仍使 gate 通过既有 `primary_cancelled` 路径失败，不会产生假绿。

## 保留的边界

- 仅处理 `PRIMARY_RESULT`；`QUALITY_RESULT` 校验、聚合器域校验和 terminal / ledger schema 不变。
- `PRIMARY_RESULT_RAW` 只用于 Actions step 日志诊断，不写入 artifact 或 ledger schema。
- 不处理 primary 为何被取消，也不处理 `build_ledger.py` 外部调用挂住的第二形态。
