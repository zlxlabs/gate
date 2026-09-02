# gate#121 进度存档 — quality 短路时 ledger 仍写出账本行

## 里程碑 1：红测落盘

- 当前阶段：测试先行完成，实现未写，新断言按预期红。
- 本段结论：W1–W5 的新断言在实现前失败（AssertionError / SystemExit / ValueError）；W2 与 W6 锁的是既有行为，红测提交时已绿。
- 关键决策与已否决方案：W1 先断言 `build_entry` / `_review_summary` 签名含 `input_short_circuited`，避免红测阶段因未知 kwargs 变成 TypeError；短路判定不许嗅探空 preflight。
- 下一步唯一动作：实现传递链三跳 + `_review_summary` 新格，转绿。

## 里程碑 2：实现转绿

- 当前阶段：实现完成，Verify-Command 范围内新测全绿。
- 本段结论：新增一格 `primary_review + input_short_circuited + 空/缺失 preflight → coverage=None`，其余格仍走原校验；CLI `--input-short-circuited` 缺省即 false、取值域 `{true,false}` fail-loud；action input 与 gate-v2 Build 步完成三跳。
- 关键决策与已否决方案：CLI 用 `default=None` 再显式分支，不写 argparse `default="false"`；布尔串按字面量转发，不用 `${x:-}`。
- 下一步唯一动作：红验四处注入并留痕。
