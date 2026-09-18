# triggering_actor 收据记录 — 进度

## 2026-09-18 现场接手 / 语义对照

- 当前阶段：repairing，已对照真身与 gate-hub PR #903 后开始落盘。
- 本段结论：真身 `DISPOSITION_RECEIPT_SCHEMA_VERSION` 已经是 2（edae390，#335 approver 字段），不是任务卡按镜像写的 1。签发端继续产 2；若升到 3，KIND/制品名前缀变成 `gate-disposition-receipt-v3-`，而 `aggregate.py` 硬编码 `gate-disposition-receipt-v2-` 且本卡禁止改该文件，新收据会从聚合扫描里消失。
- 关键决策与已否决方案：
  - 采用 PR #903 的 `_resolve_triggering_actor`：用空 `Namespace`/空 envelope 隔离 `_value`，优先级 env > cli > envelope，strip 后判空，全空 `ValueError`。
  - 否决把 schema 升到 3：会打断 silo/聚合前缀契约。
  - 校验端接受 schema 1 与 2、拒绝 3；旧 v2 缺新字段仍可校验（silo 存档），签发端缺身份 fail-closed。
  - `test_review_ledger.py` 有一条真实 producer 子进程，必须带 `GITHUB_TRIGGERING_ACTOR`，否则全量测试恒红。
- 下一步唯一动作：跑受影响测试，绿后提交第一段。

## 2026-09-18 签发 + 校验端落盘

- 当前阶段：repairing，producer/parse/validate 与测试已绿。
- 本段结论：`issue_receipt.py` 写入 `triggering_actor` + `triggering_actor_source`；`DispositionReceipt`/`parse`/`as_dict` 同步两字段；`validate_disposition_receipt` 接受 schema 1 与 2、拒绝 3。受影响四份测试文件 483 项 + aggregator 268 项通过。
- 关键决策与已否决方案：未改 workflow；`GITHUB_TRIGGERING_ACTOR` 靠 Actions 默认注入继承。契约测试锁死 step env 不含该键、argv 不含 `--triggering-actor`、没有 `env -i`。
- 下一步唯一动作：红验优先级/schema 双版本，再跑全量验证命令。
