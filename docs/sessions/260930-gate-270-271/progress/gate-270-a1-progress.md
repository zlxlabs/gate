### 2026-09-30 · 按名反查审计

**当前阶段**：implementing；已完成决策 6 的只读排查。

**本段结论**：唯一会按 Jobs API 的 job `name` 判断本聚合 job 的运行时代码在 `.github/workflows/gate-v2.yml:2117-2206`，当前识别 `gate` 与 `*/ gate`，改名后会漏掉 `gate / gate (draft)`，需扩为三个精确名称并补 resolver 参数测试。`tests/test_gate_v2_contract.py:2002-2014` 是这条运行逻辑的行为测试，需加 draft 名；`1858` 是 ready attempt 的固定契约 fixture，语义仍是 ready 检查名，保留不改。其余 `rg -n "gate / gate|\"gate\"|'gate'|job_name" .github scripts tests` 命中均为 job id、其他 job/step、workflow 身份或只描述 required context 的文案；不是按本 job 显示名反查，无需同步。

**关键决策与已否决方案**：只调整现有 `is_gate_aggregator_job` 名称匹配，不新增共享抽象；测试复用一个三项 job-name tuple 覆盖裸 job 名、ready 检查名和 draft 检查名。下游只读确认：`agent-config/scripts/git/pr_merge_ready.py:292` 在 draft 名不存在时无法接受 primary failure 的 gate disposition，`372-374` 对 skipped primary 因缺少成功的 `gate / gate` 返回 NOT_READY；`agent-config/scripts/git/gate_disposition.py:20,203-206` 的 `rerun-gate` 只查 `gate / gate`，draft 阶段没有该 check 时无法定位可重跑 job。按卡面不修改下游。

**下一步唯一动作**：调整聚合器 draft 判定与 workflow job 检查名，并为对应语义补测试。

### 2026-09-30 · 聚合器 draft 判定

**当前阶段**：implementing；聚合器实现与整文件测试完成，已提交。

**本段结论**：`evaluate()` 现只按 draft 事件 payload 接受 primary skipped，并将记录说明为 draft 阶段检查；`main()` 在该分支不扫描 waiver 回执，零 GitHub API 请求由 CLI 测试锁定。移除了实时 PR 状态参数、查询重试和两个失效 reason code；非 draft 缺失 primary 的原逻辑未改。`tests/test_gate_aggregator.py`：343 passed。

**关键决策与已否决方案**：不保留实时查询、重试或 stale/unverifiable 结果分支；draft 结果由独立检查名表达其阶段。现有非 draft `skipped` 路径是 `integration_error/unexpected_primary_skip`，不是任务卡文字所称的 `review_unavailable`；保留基线行为，`cancelled` 仍是 `review_unavailable/primary_cancelled`，报告说明差异。

**下一步唯一动作**：验证 workflow 名称契约和按名反查测试，再提交该单元。

### 2026-09-30 · draft 检查名及反查同步

**当前阶段**：implementing；workflow 与消费者同步完成，已提交。

**本段结论**：聚合 job 保持 id `gate`、`if: always()` 和三项 `needs`，按锁定表达式在 draft 事件下命名 `gate (draft)`；账本 resolver 的既有名称识别同步接受 `gate / gate (draft)`，参数测试覆盖三种识别名。README 和 workflow 头注释说明 draft 与可信 ready 检查的区别。`tests/test_gate_v2_contract.py`：202 passed；CI 同款 actionlint 命令退出码 0，原始输出为空。

**关键决策与已否决方案**：只扩展现有 resolver 的精确名称集合，不改下游仓；保留 job id、`if` 和 `needs` 不变。单独 actionlint 曾因未设置仓库 CI 的 `SHELLCHECK_OPTS=--severity=warning` 报出既有 style/info 信息并返回 1；按 CI 命令复跑退出码 0、无输出。

**下一步唯一动作**：对已提交的四项关键约束运行预定红验并记录原始失败输出。

### 2026-09-30 · 约束红验

**当前阶段**：implementing；四项约束红验完成，所有临时破坏已恢复。

**本段结论**：draft 路径临时发起 PR API 请求，零请求测试以 AssertionError 转红；非 draft skipped 临时接受后，fail-closed 测试观察到 exit code 0 并转红；workflow `name` 删除或改为字面 `gate` 时静态 YAML 契约均以 AssertionError 转红。四项被破坏点均已恢复，`git diff --check` 通过，退役标识 rg 零命中。

**关键决策与已否决方案**：保留上述断言边界；不把临时反向改动留在源码或测试里。报告保存各红验 pytest 原始输出与退出码。

**下一步唯一动作**：运行任务卡要求的全量 pytest。

### 2026-09-30 · 全量验证与 PR 交接

**当前阶段**：实现和验证完成，PR 已开；等待主脑按仓库约定合并。

**本段结论**：全量 pytest 1263 passed，pin 检查通过，CI actionlint 与 test 均为 SUCCESS；预算观测为 434 行（target 200、hard 450，within_hard）。PR #274 已打开，head 为 `104be14d919b0de2823ead297b3189508d6d1ee5`，工作树干净。

**关键决策与已否决方案**：执行器不合并。由于改动 reusable workflow，主脑应以 merge commit 合并；合并后按仓库自动 v2 标签同步机制发布，关 issue 前核实远端 v2 已包含修复 SHA。

**下一步唯一动作**：主脑以 merge commit 合并 PR #274，并按发布约定等待 v2 标签包含修复提交。
