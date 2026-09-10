# gate-734 纯账本跳过模型腿 — 进度

## 2026-09-10 红测（分类器表驱动）

- 当前阶段：implementing / milestone ① 红测
- 本段结论：新增 `tests/test_classify_pr_reviewable_paths.py`。表驱动覆盖恰好 `{retro/acceptance-log.jsonl}` → `review_expected=false`；空列表 / jsonl+其它 / 仅其它 / 非 JSON / 缺 `filename` / 分页截断（jsonl 单页 + `--has-next-page`）→ 不得为 false。夹具是 GitHub pulls files API 形状，含用量看板仓 PR 201 脱敏后的真实 `filename` 对象。脚本尚未落地，本段测试预期红。
- 关键决策与已否决方案：输入只认 files API JSON（数组或 `--paginate --slurp` 的页数组），截断用 CLI `--has-next-page` 表达（Link `rel=next` 的对位），不在 JSON 里自造字段。已否决第三种字段名、已否决把 markdown 放进可跳过集合。
- 下一步唯一动作：实现 `scripts/classify_pr_reviewable_paths.py`，让本文件表驱动转绿。
