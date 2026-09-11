# gate-751-exempt-input 进度

## 2026-09-12 · classifier 脚本与表驱动用例

- **当前阶段**：implementing
- **本段结论**：`classify_pr_reviewable_paths.py` 改为「舰队默认账本 ∪ `REVIEW_EXEMPT_PATHS` 声明」的 ⊆ 判定；空声明走原账本全等语义。非法条目整份作废并打 `::warning::`。
- **关键决策与已否决方案**：脚本读 env `REVIEW_EXEMPT_PATHS`（与 workflow `env:` 传递同名），不用 `--exempt-paths-file`。前缀匹配用 `directory + "/"`，避免 `docs` 吃掉 `docs-old`。
- **下一步唯一动作**：把 `review_exempt_paths` input 接到两份可复用工作流的 classify job env，并更新契约测试。
