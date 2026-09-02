# gate#110 进度存档 — aggregator stale-draft 载荷复核

## 里程碑 1：DESIGN-note 入库

- 当前阶段：design 落盘完成，准备写红测。
- 本段结论：`design-gate.md` 已按字节原样复制为 `docs/sessions/260902-gate-triage/design.md`（diff 校验一致）。下游消费者核查完成：gate-hub `scripts/review` 与 `tests` 中所有 `reason_code` 命中均属 shadow/ocr 状态域，无任何 gate-terminal reason 枚举/白名单；本仓内唯一域校验在 `aggregate.py` 自身的 `TERMINAL_REASON_DOMAIN` 元组，扩元组即覆盖。
- 关键决策与已否决方案：新 reason code 复用 `review_unavailable` classification（锁定决策）；`pr_draft_now=None` 在 `is_draft and primary skipped` 分支语义为「复核失败」fail-closed——这会让既有 `(skipped, is_draft=True)` 测试格与 CLI 用例变红，须改为显式传 `pr_draft_now=True` / monkeypatch `_fetch_pr_draft`（语义必然，将在报告写明）。
- 下一步唯一动作：写判定矩阵新格、`_fetch_pr_draft` 单测、CLI 测试（红）。

## 里程碑 2：红测落盘

- 当前阶段：测试先行完成，30 个用例按预期红（实现未写）。
- 本段结论：判定矩阵新增 3 格（`pr_draft_now` False/None/is_draft=False+None），既有 `(skipped, is_draft=True)` 格与 5 个既有 CLI/evaluate 用例改为显式 `pr_draft_now=True` 或 monkeypatch `_fetch_pr_draft` 返回 True——`None` 在该分支语义变为「复核失败」是锁定决策的必然结果。新增 `_fetch_pr_draft` 单测 5 条、CLI 级测试 2 条、reason 域全元组锁 1 条、problems 逐字断言 2 条。
- 关键决策与已否决方案：monkeypatch 点选 `AGG._fetch_pr_draft`（main 内只有一处调用）与 `AGG.time.sleep`（模块内引用，测后不残留）；否决在测试里设真实 GITHUB_TOKEN——会真打 API。
- 下一步唯一动作：实现 evaluate 分支、`_fetch_pr_draft`、main 接线，转绿。

## 里程碑 3：实现转绿

- 当前阶段：实现完成，`tests/test_gate_aggregator.py` 219 全绿。
- 本段结论：`evaluate()` 新增 keyword-only `pr_draft_now`，draft+skipped 分支按真值表三分叉（False→`review_unavailable/review_expected_stale`，None→`review_unavailable/pr_state_unverifiable`，True→维持 `expected_skip`）；`_fetch_pr_draft` 走既有 `_github_json`，连接级异常 3 次尝试、退避 1s/2s，HTTPError 不重试，耗尽/畸形/缺 token 或 pr_number 一律 None；`main()` 仅在 `primary_result == "skipped" and is_draft` 时调用，发生在 publish 预算激活前（`_ACTIVE_PUBLISH_BUDGET` 为 None，走 `GITHUB_API_TIMEOUT_SECONDS`）。
- 关键决策与已否决方案：常量命名 `PR_DRAFT_FETCH_ATTEMPTS` / `PR_DRAFT_FETCH_BACKOFF_SECONDS` / `_RETRYABLE_CONNECTION_ERRORS`（形态抄 build_ledger，不 import）；helper 置于 `_github_json` 之后；重试请求不吃 publish 预算（时序保证，未加预算守卫代码——无第二消费者）。
- 下一步唯一动作：红验——把 `pr_draft_now is False` 分支改回落 `expected_skip`，确认矩阵第二格转 AssertionError，再还原。

## 里程碑 4：红验完成

- 当前阶段：红验通过，实现已还原，等待全量回归。
- 本段结论：把 `pr_draft_now is False` 分支判据行改坏为落回 `expected_skip / review_not_expected`（单行注入），判定矩阵第二格 `test_terminal_classification_matrix[kwargs4-expected4]` 转红且失败类型为 AssertionError（`assert ('expected_skip', 'review_not_expected', 'skipped') == ('review_unavailable', 'review_expected_stale', 'unavailable')`），同红的还有 problems 逐字断言与 CLI 测试；只还原该一行后 219 全绿、`git diff` 为空。
- 关键决策与已否决方案：注入点只选判据赋值那一行（固定条款「默认只改判据本身那一行」）；未做整文件 checkout。
- 下一步唯一动作：全量 pytest + `check_pinned_uses.py`，写最终报告。
