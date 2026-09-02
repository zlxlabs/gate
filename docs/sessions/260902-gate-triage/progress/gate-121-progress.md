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

## 里程碑 3：红验完成

- 当前阶段：四处注入均红，还原后 `git diff` 为空；全量 800 passed；`check_pinned_uses.py` 绿。
- 本段结论：
  1. W1 分支 `coverage = None` 改回 `raise ValueError`：`sed -n '236,237p'` 命中后两测均 ValueError。
  2. W2 判据改成「preflight 为空就放行」（去掉 `input_short_circuited and`）：W2 `DID NOT RAISE ValueError`。
  3. 删 gate-v2.yml:1409 `input-short-circuited:`：W5 AssertionError。
  4. 删解析器 RESULT_DOMAIN 五行使 W6 六参全部 AssertionError（非法值落到「No matching required ledger input artifact found」，不再含 `must be one of`）。
- `_review_summary` 多 return：早期 `if not audit` 不在 W1 路径（W1 必带 primary_review audit），由既有 `test_review_summary_defaults_when_audit_missing` 锁；注入点取新增短路格，不是假阴。
- 下一步唯一动作：push、开 draft PR、写报告。
