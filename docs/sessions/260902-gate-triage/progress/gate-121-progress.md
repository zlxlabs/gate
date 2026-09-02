# gate#121 进度存档 — quality 短路时 ledger 仍写出账本行

## 里程碑 1：红测落盘

- 当前阶段：测试先行完成，实现未写，新断言按预期红。
- 本段结论：W1–W5 的新断言在实现前失败（AssertionError / SystemExit / ValueError）；W2 与 W6 锁的是既有行为，红测提交时已绿。
- 关键决策与已否决方案：W1 先断言 `build_entry` / `_review_summary` 签名含 `input_short_circuited`，避免红测阶段因未知 kwargs 变成 TypeError；短路判定不许嗅探空 preflight。
- 下一步唯一动作：实现传递链三跳 + `_review_summary` 新格，转绿。
