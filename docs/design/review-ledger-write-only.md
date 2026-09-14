# Review ledger v2：只写本次 run 的设计

## 目标与边界

`Build v2 review effectiveness ledger` 只消费当前 run 已下载的
preflight、install、canonical primary audit 和 gate terminal envelope，输出
`ledger.jsonl` 恰好一行。生产者不下载既有 ledger、不读取或更新 Pull Request
评论，也不调用 GitHub API；因此构建耗时只随本次输入体积变化。

artifact 名 `codex-review-ledger-v2` 和文件名 `ledger.jsonl` 不变，内容
schema 升为 2。`LEDGER_ENTRY_FIELDS` 是代码中的字段全集；其中终态 receipt
缺席时，`disposition_receipt_consumption` 和跨 attempt 才有的
`terminal_source_attempt` 按旧行为条件出现。

## v1 字段核销表

| v1 字段 | v2 处置 | 理由 / 迁移位置 |
| --- | --- | --- |
| `schema_version` | 保留（值改为 `2`） | 标记只写本次的新契约；本次输入可得。 |
| `recorded_at` | 保留 | 当前生产时间戳；本次输入可得。 |
| `repository` | 保留 | 当前 workflow 身份；本次输入可得。 |
| `pr_number` | 保留 | 当前事件身份；本次输入可得。 |
| `run_id` | 保留 | 当前 run 身份；本次输入可得。 |
| `run_attempt` | 保留 | 当前 run attempt；本次输入可得。 |
| `head_sha` | 保留 | 当前事件与 audit/terminal 绑定的提交；本次输入可得。 |
| `review_round` | 删除 | 依赖 prior entries 的去重计数；由 agent-config 采集层按 run 时间顺序离线计算。 |
| `history_status` | 删除 | 只描述 producer 是否成功读取历史；producer 不再读取历史。 |
| `disposition_status` | 保留 | 保持 v1 字符串字段；producer 不读取评论，当前无评论错误可报告时使用本地默认 `success`，终态 receipt 的 fail-closed 状态以 `disposition_receipt_consumption` 为准。 |
| `preflight` | 保留 | 当前 run 下载的 preflight 输入；缺失时仍按既有短路规则为 `null`。 |
| `install` | 保留 | 当前 run 下载的 install 输入；缺失时为 `null`。 |
| `primary_identity` | 保留 | 当前 run 的 canonical primary audit 身份投影。 |
| `review` | 保留 | 只由当前 primary audit 与 preflight 计算；子结构不改。 |
| `comparison` | 删除 | 依赖 prior finding 集合比较新 head/rerun；由 agent-config 采集层离线计算。 |
| `finding_dispositions` | 保留 | 保持 v1 对象字段；producer 已移除评论输入，因此本次生产默认为空对象，历史评论观察转到分析侧。 |
| `convergence_projection` | 删除 | 旧实现通过 prior entries 过滤 disposition，不能证明是本次输入；当前终态 receipt 已由独立字段承载。 |
| `false_positive_count` | 保留 | 保持当前 run 条目字段；无评论输入时为 `0`，终态 receipt 统计在 receipt consumption 中。 |
| `disposition_receipt_consumption` | 保留（有 terminal 时） | 直接校验并复制当前 run 下载的 gate terminal envelope 字段。 |
| `terminal_source_attempt` | 保留（复用旧 attempt 的 terminal 时） | 由当前 run 的 terminal envelope 的 `run_attempt` 得出，不读取历史 ledger。 |
| `ledger_conflict` | 删除 | 只用于跨历史条目的冲突标记；由分析侧在合并多个 artifact 时处理。 |

删除的 producer 功能及其测试：artifact 历史下载与重试、网络预算、评论
游标读写、跨条目 dedupe/conflict、`--max-entries` 容量参数、comparison 和
review round 的正反例。保留当前 audit/preflight/install/terminal 校验测试，
并新增断网 `main()` 单行测试与 import AST 测试。

## 读侧迁移与晋升前置条件

agent-config 采集层负责收集每次 run 的单行 artifact，并离线补算多轮
`review_round`、finding 的 persistent/resolved/new `comparison`、历史完整性
以及跨 artifact 的冲突。gate-hub 的人工报告和 replay 读侧随采集层契约升级，
不由 gate 生产者新增网络调用、凭据或内网依赖。

`v2` 标签暂不晋升。晋升前必须先完成 agent-config
`scripts/consult/consult_trigger.py` 的采集层迁移，使 consult 从采集层读取
多轮 finding；在此之前提前晋升会因缺少 artifact 内的 `review_round` 而报
`DataUnavailable`。

