## 真实生产夹具

- 当前阶段：implementing
- 本段结论：从 gate-hub run 35987744407 的 ledger 日志行保存了完整真实 JSON，并用生产端 `issue_receipt.py issue` 为目标 finding 生成 v3 false-positive 回执。完整 ledger 有多条 P1 共享同一 canonical finding key，签发输入因此只含从该 ledger 原样抽出的目标 finding。
- 关键决策与已否决方案：保留原始 ledger 行；回执来源使用生产 CLI，反证记录 `--rm` 一次性容器与固定 SHA 下 Git 对象不可变的理由。不读取 GitHub artifact。
- 下一步唯一动作：实现前轮 JSON 的有效回执投影并先补行为单测。

## 回执投影与聚合器单测

- 当前阶段：implementing
- 本段结论：`previous-findings.json` 现在固定包含四个顶层键；成功时写入有效 false-positive/deferred 投影，最多 30 条，反证文本有界，失败时 dispositions 为空。聚合器测试通过：323 passed。
- 关键决策与已否决方案：复用 Silo 专用回执读取函数和既有反证判据；没有触碰判决、relation 或 audit 注解路径。
- 下一步唯一动作：在注入 step 成功且 JSON 非空时导出路径，并补 workflow 契约测试与设计文档。

## Workflow 导出与消费契约

- 当前阶段：implementing
- 本段结论：注入成功分支现在仅在 `${RUNNER_TEMP}/previous-findings.json` 非空时导出 `REVIEW_PREVIOUS_ROUND_PATH`；`Run review-primary` 不新增显式 env。workflow 契约测试通过：189 passed；pin 检查通过。
- 关键决策与已否决方案：卡面写 `/previous-findings.json`，但现有 CLI 实际写 `${RUNNER_TEMP}/previous-findings.json`；导出实际产物的绝对路径，避免 env 指向不存在的文件。保留 `DESIGN_DOC` 拼接与 primary audit 注解行为。
- 下一步唯一动作：完成红验与全量验收，写报告并推送分支。

## 导出条件判据

- 当前阶段：implementing
- 本段结论：契约测试对 `inject_status` 的成功分支、非空文件判断、导出顺序和 review-primary env 缺席分别作直接断言；针对性测试通过。断言缺失时会以 AssertionError 报出。
- 关键决策与已否决方案：不把路径写入 `Run review-primary` 的 `env:`；只通过 `$GITHUB_ENV` 在注入 step 内导出。
- 下一步唯一动作：逐项执行导出条件反向红验，再跑全量测试和静态检查。

## 红验与最终验证

- 当前阶段：verified
- 本段结论：去掉非空判断、将导出移到注入状态分支之前，两项反向红验都由目标契约断言触发 AssertionError；恢复后全量测试 1200 passed，两个指定文件分别 323 passed / 189 passed，CI 同配置 actionlint 与 pin 检查通过。
- 关键决策与已否决方案：红验只改动并恢复 workflow 中对应的单行；未改 gate 判决，也未使用任何绕闸操作。
- 下一步唯一动作：写入完整执行报告并核对已推送分支与干净工作树。
