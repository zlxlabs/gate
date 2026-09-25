## 里程碑 1
- 当前阶段：implementing
- 本段结论：探针固定比较 `.github/workflows/`、`.github/actions/`、`scripts/` 的树对象；新增真实本地 bare 仓库验收内容滞后、docs-only 不误报及远端查询失败，并覆盖树查询失败。
- 关键决策与已否决方案：用 `git ls-tree` 比目录树对象，避免部分克隆为比对内容拉取 blob；不以 SHA 单独判定滞后。
- 下一步唯一动作：实现 workflow 六态汇总行与契约测试。
