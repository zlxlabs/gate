# 主审 finding 的跨轮关系

同一条 `gate / primary` 在同一个 PR 上不记住上一轮 finding（gate #205、#181）。这里只让上一轮可见，并给本轮每条 finding 标关系。conflict 不变绿，也不放宽 P1。

`relation_to_previous` 只有 `new` / `repeat` / `conflict`。`repeat` 是 id 相同，`previous_finding_id` 就是该 id。`conflict` 是同文件、同一整数行、双方都是 major 或 blocker，且 `issue` 与 `acceptance` 在空白折叠和 casefold 之后都不相同；指针是唯一对上的上一轮 id。对不上、没有上一轮、或候选多于一条，记 `new` 且不带指针。id 相同优先于 conflict。其它取值（例如 `"maybe"`）报 `GATE-FINDING-RELATION-UNKNOWN`，不会当成 `new`。比较前文本截到 240 字符。同位置但要求文本相同，记 `new`。

上一轮只读门禁自己的 ledger：`d30/{repository_id}/codex-review-ledger-v2-{repository_id}-*`，取同一 `pr_number` 里 `(run_id, run_attempt)` 严格早于本轮的最新一条。不新查 GitHub，不读被评方仓。最多 30 条。注入主审提示词的正文上限 6000 字符，超出截断并标 `…[truncated]`。通道是 review-primary 已读的 `DESIGN_DOC`：原设计说明在前，清单接在 `PREVIOUS ROUND FINDINGS` 分隔段后。没有清单时不改 `DESIGN_DOC`。

聚合器把 `finding_relation.counts` 写进本轮 gate terminal，ledger 原样抄走。`conflict` > 0 时 `review_terminal` 为 `manual_required`，并出现在既有状态面板的「跨轮冲突」一节。本轮若原本会 pass，终态改为 fail / `primary_findings`。clean-streak 状态机不改，避免写出和 streak 对不上的粘滞 `manual_required`。

上一轮 ledger 读不到时每条记 `new`，job 继续，不因此 fail-closed。日志一行 `GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=<缺的是哪一路>`。`detail` 区分 `silo-not-configured`、`silo-ledger-unreadable:<异常类型>`、`previous-findings-file-missing`、`previous-findings-file-invalid`、`inject-step-failed`、`aggregator-source-missing`。扫描成功但这个 PR 没有更早的 ledger，不打这一行。

false-positive disposition 仍计入终裁。不另做「不重复计入」：拿掉它会放宽 gate。证据：`convergence.py:799` `record_dispositions` 保留全部 primary P1；`convergence.py:1375` `evaluate_round` 的 clean round 只看当前 P1 是否为空，`waiver_receipts` 被忽略；`aggregate.py:1228` receipt 不改变送进 convergence 的 canonical P1；`docs/sessions/260915-disposition-record-only/design.md` 锁定有效 claim 仍保留 P1、失败且不增加 clean streak。
