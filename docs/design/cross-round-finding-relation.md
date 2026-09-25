# 主审 finding 的跨轮关系

同一条 `gate / primary` 在同一个 PR 上不记住上一轮 finding（gate #205、#181）。这里只让上一轮可见，并给本轮每条 finding 标关系。conflict 不变绿，也不放宽 P1。

`relation_to_previous` 只有 `new` / `repeat` / `conflict`。`repeat` 是 id 相同，`previous_finding_id` 就是该 id。`conflict` 是同文件、同一整数行、双方都是 major 或 blocker，且 `issue` 与 `acceptance` 在空白折叠和 casefold 之后都不相同；指针是唯一对上的上一轮 id。对不上、没有上一轮、或候选多于一条，记 `new` 且不带指针。id 相同优先于 conflict。其它取值（例如 `"maybe"`）报 `GATE-FINDING-RELATION-UNKNOWN`，不会当成 `new`。比较前文本截到 240 字符。同位置但要求文本相同，记 `new`。

上一轮只读门禁自己的 ledger：`d30/{repository_id}/codex-review-ledger-v2-{repository_id}-*`，取同一 `pr_number` 里 `(run_id, run_attempt)` 严格早于本轮的最新一条。不新查 GitHub，不读被评方仓。最多 30 条。前轮 findings 与 dispositions 经 `${RUNNER_TEMP}/previous-findings.json` 由 `REVIEW_PREVIOUS_ROUND_PATH` 传给 review-primary，消费端按四键契约严格解析后渲染进主审提示词。`DESIGN_DOC` 只传调用方传入的原始设计文档，不再拼接前轮 findings。

聚合器把 `finding_relation.counts` 写进本轮 gate terminal，ledger 原样抄走。`conflict` > 0 时 `review_terminal` 为 `manual_required`，并出现在既有状态面板的「跨轮冲突」一节。本轮若原本会 pass，终态改为 fail / `primary_findings`。clean-streak 状态机不改，避免写出和 streak 对不上的粘滞 `manual_required`。

上一轮 ledger 读不到时每条记 `new`，job 继续，不因此 fail-closed。日志一行 `GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=<缺的是哪一路>`。`detail` 区分 `silo-not-configured`、`silo-ledger-unreadable:<异常类型>`、`previous-findings-file-missing`、`previous-findings-file-invalid`、`inject-step-failed`、`aggregator-source-missing`。扫描成功但这个 PR 没有更早的 ledger，不打这一行。

false-positive disposition 仍计入终裁。不另做「不重复计入」：拿掉它会放宽 gate。证据：`convergence.py:799` `record_dispositions` 保留全部 primary P1；`convergence.py:1375` `evaluate_round` 的 clean round 只看当前 P1 是否为空，`waiver_receipts` 被忽略；`aggregate.py:1228` receipt 不改变送进 convergence 的 canonical P1；`docs/sessions/260915-disposition-record-only/design.md` 锁定有效 claim 仍保留 P1、失败且不增加 clean streak。

## 处置回执进前轮上下文（gate-hub#1070）

前轮上下文 JSON 在原有 `available`、`detail`、`findings` 之外增加 `dispositions`。处置仅作为 review-primary 的审查上下文，不自动 resolve finding，也不改变 `record_dispositions`、`evaluate_round`、`apply_finding_relation` 或汇总器终态。

处置只从 Silo `d30/{repository_id}/gate-disposition-receipt-v3-...` 读取，并复用 gate 的 `_fetch_silo_disposition_receipts` 与回执解析。按 `repository_id` 和 `pr_number` 过滤，不按当前 head 或 audit digest 过滤；不读取 GitHub artifact。只保留 `false-positive` 且 `_counterevidence_reason` 通过（command、output、result、pointer 非空且 result 为 `refuted`），或 `deferred` 且 `tracking_issue` 为非空字符串的 v3 回执。旧版、无效反证和未知 disposition 不进入数组；扫描投影输出 `GATE-PREVIOUS-DISPOSITIONS: kept=<n> dropped=<m>`，其中超上限的有效记录也计入 dropped。

每条只输出 `finding_id`、`disposition`、`head_sha`、`approved_at`、`reason`、`counterevidence`、`tracking_issue`。文本使用 `_clip_text` 限制为 240 字符，`head_sha` 保留原值；false-positive 的 `counterevidence` 是 command、output、result、pointer 四字段，`tracking_issue` 为 null；deferred 的 `counterevidence` 为 null，`tracking_issue` 保留其值。按 `approved_at` 倒序，最多 30 条。

Silo 未配置或前轮 ledger 不可用时，JSON 的 `available` 为 false、`findings` 与 `dispositions` 都为空数组，并沿用 `GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=...`。成功注入后，只有 `${RUNNER_TEMP}/previous-findings.json` 非空才经 `$GITHUB_ENV` 导出 `REVIEW_PREVIOUS_ROUND_PATH`；变量值是该文件的绝对路径。消费端只要收到该变量，就必须把文件按上述四键契约严格解析，不接受缺键或非法 JSON。原 `DESIGN_DOC` 拼接通道已随消费端（gate-hub `7b4f322b` 起读 `REVIEW_PREVIOUS_ROUND_PATH`）上线删除；`--annotate-primary-audit` 行为保留。
