# gate-261015-g1 进度（G1：增量阻断影子纯分类模块与单测）

## 里程碑 1：incremental_shadow 纯分类模块 + 单测 + gate-hub 自有账本回放

- 当前阶段：implementing
- 本段结论：`incremental_shadow.py`（五公开函数，纯函数无 I/O，invalid input 一律 `incremental_shadow_invalid_input` ValueError）与 `test_incremental_shadow.py`（43 用例）落地。回放数据源为 gate-hub 自有 Silo 账本（premise 缓存 `entries.jsonl`），期望分类值用离线脚本同口径函数（ast 逐字节切取）对同一批行实算得出；4 条真实行断言（duplicate/neither/no_anchor×2）全绿，on_diff 在该账本 26 条 r≥2 P1 分类中为 0 条，按卡面以明确标合成的用例补齐（仓名 example/synthetic）。窄验 43 passed，全仓 1611 passed。
- 关键决策与已否决方案：PR1446 r2 真实锁定 duplicate > on_diff 优先级（finding 行 98 既与 r1 行 92 判重又落在改动行 ±3 内）；fixture 的 diff 文本做结构保持脱敏（parse 等价断言）并按被断言消费的文件 section 裁剪；把分类逻辑直接写进 build_ledger.py 的方案维持否决（离线回放不可行）。
- 下一步唯一动作：红验（改坏 LOOSE_LINE_DELTA 确认回放转红后还原），推分支更新 PR。

## 里程碑 2：红验闭环与交付

- 当前阶段：ready-to-handoff
- 本段结论：红验改坏 `LOOSE_LINE_DELTA`（15→5）后真实行回放与单测同步转红（`AssertionError: 'on_diff' == 'duplicate'`，PR1446 duplicate 被错分为 on_diff），仅还原该行后工作区与提交一致、43 passed；全仓 Verify-Command 1612 passed；新入口按闭集守卫登记（ledger 槽）。fixture 零他仓数据：`repository` 字段全部为 gate-hub 自有账本原值。
- 关键决策与已否决方案：无。
- 下一步唯一动作：等待主脑验收与 G2 装配卡开工。
