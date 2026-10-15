# gate-261015-g2 进度存档（G2 增量阻断影子——ledger job 装配与账本字段）

## 里程碑 1：影子 I/O 装配模块

- **当前阶段**：implementing
- **本段结论**：读完设计 §2–§5 与 gate-v2 ledger job 现状后落地 `incremental_shadow_io.py`：gh runs API 枚举（镜像 `_pr_target_run_ids` 的 full-list-or-None 语义）+ Silo 归档自建轮序 + throwaway 仓 shallow fetch 与 `git diff -U0 -M`，全部带超时与整体预算（默认 240s，`GATE_INCREMENTAL_SHADOW_BUDGET_S`），恒写 `shadow.json` 且恒 exit 0。
- **关键决策与已否决方案**：降级原因域 `silo_list_failed / runs_list_failed / silo_archives_partial / diff_fetch_timeout / diff_failed / budget_exhausted / internal_error`（设计 §5.1 列表带「…」扩展位）；「只要有降级就 degraded、跳过结论不可信」；counterfactual 前提（本轮被 primary_findings 打红）不可观测时强制 `unclassified`。已否决：per-PR latest pointer 折叠（gate#309）；结构坏行绕行（fold 抛错→按 silo_archives_partial 降级，不加兼容分支）。
- **下一步唯一动作**：build_ledger.py 增加可选字段校验与合并、action.yml `shadow-path` 输入。

## 里程碑 2：账本字段消费侧 + workflow 装配

- **当前阶段**：implementing
- **本段结论**：`build_ledger.py` 新增 `_validate_incremental_blocking_shadow`（§5.1 全字段域校验，校验失败丢弃字段并打一行 `GATE-INCREMENTAL-SHADOW-UNAVAILABLE: reason=invalid_payload`，行不失败）；`review-ledger` action 新增可选输入 `shadow-path`（默认空，模板 terminal-path）；`gate-v2.yml` ledger job 在 terminal 下载与 Build 之间新增 `Compute incremental blocking shadow` step（`continue-on-error: true` + `|| echo invocation_failed` 双保险），resolve step 增加 `review_expected` 输出（不新增 CLASSIFY_GUARD 表达式副本，保住 count==7 守卫）。wiring 测试 52 条全绿（真实本地 git remote 跑 fetch+diff、silo stub 走真 subprocess argv、producer 写出的字节原样喂给 consumer；无害性测试证明影子失败时账本行逐字节一致）。
- **关键决策与已否决方案**：`_remote_url` 抽成模块级函数作为离线回放 seam（测试指本地 fixture 仓，生产恒用 `https://github.com/{repository}.git`）；degraded-无分类时 `canonical_round=0` 合法（§5.1「≥2 才有分类结果」只对 computed 设硬门槛）。已否决：测试用第三份 review-expected 表达式副本（打破 count==7）；新增 env 开关切 remote（用参数+模块函数注入，不加生产配置项）。
- **下一步唯一动作**：红验（改坏生产代码确认测试红）+ 全仓验证 + 提交 PR。

## 里程碑 3：红验与验证收口

- **当前阶段**：validating
- **本段结论**：红验三处（build_ledger 丢字段路径、io 降级行、workflow shadow-path 链）全部转红且为断言失败；Narrow-Verify、全仓 Verify-Command（1664 passed）、`check_pinned_uses` 全绿。
- **关键决策与已否决方案**：无
- **下一步唯一动作**：推送分支并开 PR，报告贴验收输出。
