## 里程碑 1
- 当前阶段：implementing
- 本段结论：探针固定比较 `.github/workflows/`、`.github/actions/`、`scripts/` 的树对象；新增真实本地 bare 仓库验收内容滞后、docs-only 不误报及远端查询失败，并覆盖树查询失败。
- 关键决策与已否决方案：用 `git ls-tree` 比目录树对象，避免部分克隆为比对内容拉取 blob；不以 SHA 单独判定滞后。
- 下一步唯一动作：实现 workflow 六态汇总行与契约测试。

## 里程碑 2
- 当前阶段：implementing
- 本段结论：`v2-tag-sync.yml` 增加 always 汇总步骤，每次 sync job 输出一行机读状态并写入 Step Summary；契约测试锁定六态分支及 promoted 必须经过 move 成功。
- 关键决策与已否决方案：保持现有 job/main 触发边界；状态由 workflow step outcome 与已存在输出推导，不扩展 guard 脚本。
- 下一步唯一动作：记录巡检到 finding 的已知送达路径和未证实缺口。
