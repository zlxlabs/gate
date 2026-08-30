# gate-C：已消费 disposition receipt 进 ledger 投影（G3 gate 侧）

## 2026-08-31 里程碑 ① terminal 结构化消费块

- 当前阶段：implementing / milestone ① terminal structured block
- 本段结论：`build_terminal_envelope` 恒写顶层 `disposition_receipt_consumption`。数据来自判定者第一次 `consume_dispositions` 的结构化对象（finding_id / receipt 名 / 授权三字段 / reason + 计数 + rejected_reasons + fail_closed），不解析 G4 显示字符串。无消费时写空列表与零计数，缺省键恒在。
- 关键决策与已否决方案：用第一次消费结果而非 `round_decision.disposition`（后者只看到已转发的 consumed receipts，丢 rejected）。`reason` 写入 resolved 项，避免 hub-A 再去解析 G4 行。否决把该块塞进 `_review_summary`。
- 下一步唯一动作：ledger job 下载本 run 的 `gate-terminal-v1-*` artifact（fail-loud）。
