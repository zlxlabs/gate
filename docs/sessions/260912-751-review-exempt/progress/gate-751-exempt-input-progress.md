# gate-751-exempt-input 进度

## 2026-09-12 · classifier 脚本与表驱动用例

- **当前阶段**：implementing
- **本段结论**：`classify_pr_reviewable_paths.py` 改为「舰队默认账本 ∪ `REVIEW_EXEMPT_PATHS` 声明」的 ⊆ 判定；空声明走原账本全等语义。非法条目整份作废并打 `::warning::`。
- **关键决策与已否决方案**：脚本读 env `REVIEW_EXEMPT_PATHS`（与 workflow `env:` 传递同名），不用 `--exempt-paths-file`。前缀匹配用 `directory + "/"`，避免 `docs` 吃掉 `docs-old`。
- **下一步唯一动作**：把 `review_exempt_paths` input 接到两份可复用工作流的 classify job env，并更新契约测试。

## 2026-09-12 · 工作流 input 与契约

- **当前阶段**：implementing
- **本段结论**：gate-v2 / gate-shadow-v2 都加了 `review_exempt_paths`（string，默认空），classify job 用 env 传给脚本，`run:` 块不含 `}` 表达式。caller 模板只加注释、不声明默认豁免路径。
- **关键决策与已否决方案**：无
- **下一步唯一动作**：按完成条件做三条红验，再跑全量 pytest。

## 2026-09-12 · 红验与全量绿

- **当前阶段**：completed
- **本段结论**：三条红验均以 AssertionError 转红（⊆→交集、删 `.github/` 拒绝、前缀丢掉 `/`），还原后工作树与提交一致。全量 `911 passed`。
- **关键决策与已否决方案**：无
- **下一步唯一动作**：无——写 report.md 交主脑验收。本卡 PR 必须用 merge commit 合并，禁止 squash。
