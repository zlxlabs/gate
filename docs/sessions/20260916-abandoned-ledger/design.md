# abandoned 主审结果记账修复

## 范围

真实运行 `agent-config` run `34740209146` 的 `gate / primary` 在无 steps 的情况下被取消，ledger 收到的 `PRIMARY_RESULT` 观测值为 `abandoned`。取消根因未知，本卡仅修输入与记账契约，不声称修好 gate-hub#818 或 #803 的全部子项。

## 决策

- 仅在 gate-v2 的 Aggregate required verdict 与 Resolve v2 ledger artifacts 两个入口，把已观测的 `abandoned` 映射为既有 `cancelled`，并保留原始 `PRIMARY_RESULT_RAW`。
- raw 的保留载体是 GitHub Actions 对应 step 的 env/log；terminal 与 ledger 仍只写规范四值，raw 保留期随 Actions 日志策略，不新增永久存储或 schema 字段。
- 复用 aggregator 已有的 cancelled fail-closed 路径：门禁进程仍失败并写终态；不改判决算法，也不把取消视为主审通过。
- resolver 只在规范化后的 cancelled 路径允许缺 canonical audit；quality 成功的 input 和 gate terminal 仍必需。
- 取消时跳过空 audit artifact 的下载；ledger 继续用既有 schema v2 的 `review.status=not_run`、`verdict=null` 记录未审结行。
- 不扩 terminal/ledger schema，不改变 success/failure/skipped 语义，不增加依赖、重试或 fallback。
