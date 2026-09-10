# gate-734 纯账本跳过模型腿 — 进度

## 2026-09-10 红测（分类器表驱动）

- 当前阶段：implementing / milestone ① 红测
- 本段结论：新增 `tests/test_classify_pr_reviewable_paths.py`。表驱动覆盖恰好 `{retro/acceptance-log.jsonl}` → `review_expected=false`；空列表 / jsonl+其它 / 仅其它 / 非 JSON / 缺 `filename` / 分页截断（jsonl 单页 + `--has-next-page`）→ 不得为 false。夹具是 GitHub pulls files API 形状，含用量看板仓 PR 201 脱敏后的真实 `filename` 对象。脚本尚未落地，本段测试预期红。
- 关键决策与已否决方案：输入只认 files API JSON（数组或 `--paginate --slurp` 的页数组），截断用 CLI `--has-next-page` 表达（Link `rel=next` 的对位），不在 JSON 里自造字段。已否决第三种字段名、已否决把 markdown 放进可跳过集合。
- 下一步唯一动作：实现 `scripts/classify_pr_reviewable_paths.py`，让本文件表驱动转绿。

## 2026-09-10 分类器脚本

- 当前阶段：implementing / milestone ② 脚本
- 本段结论：`scripts/classify_pr_reviewable_paths.py` 落地。stdout / `--github-output` / `$GITHUB_OUTPUT` 写 `review_expected=false` 当且仅当路径集合恰好为 `{retro/acceptance-log.jsonl}`；空列表、其它路径、`--has-next-page`、非 JSON / 缺 filename / 读文件失败一律 `true`（失败路径 exit 1 但不写 false）。`tests/test_classify_pr_reviewable_paths.py` 16 passed。
- 关键决策与已否决方案：截断信号用 `--has-next-page` 而不是 JSON 里自造 `truncated` 字段。失败先写 `true` 再非 0 退出，避免「写出 false 后崩溃」。不新增 path-list 第二种解析器——工作流喂 files API JSON。
- 下一步唯一动作：把分类 job 接到 `gate-v2.yml` / `gate-shadow-v2.yml`，并更新契约测试。
