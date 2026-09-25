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

## 里程碑 3
- 当前阶段：implementing
- 本段结论：设计文档记录 Actions 日志/Summary 与 gate-hub timer、探针、systemd OnFailure、finding-v1 sink 的链路，并明确 shell fetch 退出码同形及 sink 到人的未证实边界。
- 关键决策与已否决方案：finding 默认 sink 为 `/home/zlx/.local/state/gate-hub/findings.jsonl`，仍允许 `GATE_HUB_FINDINGS_PATH` 覆盖；不把写入 sink 等同于已送达人员。
- 下一步唯一动作：跑全量测试、部分克隆真实远端探针和最终洁净状态核验。

## 里程碑 4
- 当前阶段：completed
- 本段结论：全量测试 1140 passed；固定 workflow pin 检查、actionlint、diff check 通过；在巡检使用的 `blob:none` 部分克隆内对真实远端运行本工作树探针，退出 0 且无输出。
- 关键决策与已否决方案：空路径集合反向验证按预期以 `AssertionError` 转红；临时 clone 经 GitHub SSH 通道失败后未重试，改用已核实的巡检部分克隆完成真实调用。
- 下一步唯一动作：将分支和报告交回主脑进行验收与 merge-commit 合并。
