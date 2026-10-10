# gate PR301 汇总预算修复独立审查

- 固定范围：`6b70e369955891b6f58428e377cd30f139aacde2..2b941c29329b001b512b55eeeefbd439bd0c9ffd`
- 风险档：personal。
- 结论：本次 8→15 分钟止血可接受；有两个 P2 文档问题，均不阻断交付。
failure-visibility: p2-only

## 不变式核对

1. **只提高既有汇总预算：通过。** 固定 diff 仅有 `jobs.gate.timeout-minutes` 从 8 改为 15、对应注释和原契约测试；`jobs` 清单、其他 job、顶层 workflow、gate 的其余字段均不变。固定 H0 scratch-tree 中用 PyYAML 独立解析 base/head 得到：其他 job 完全相等，gate 除 timeout 外相等，非注释/非 timeout 的 YAML 行差异为 0。证据：`.github/workflows/gate-v2.yml:1816-1820`、`tests/test_gate_v2_contract.py:2957-2962`。
2. **失败判定语义不变：通过。** `gate` 仍 `if: always()`、依赖 `quality/primary/classify_pr_paths`，并保持原 concurrency group 与 `cancel-in-progress: false`；aggregate、terminal upload、panel publish 的 steps/env/if 均结构相同。只延长 job 硬超时，没有新增继续成功、重试或软失败路径。证据：base/head 结构差集与上述 workflow 解析输出。
3. **历史根因有确定信号：通过。** run `37500314726` attempt 2 / job `112408977035` 于 `2026-10-06T17:32:29Z` 开始、`17:41:24Z` 结束（8m55s）；run `37494214791` attempt 2 / job `112400743934` 于 `17:13:47Z` 开始、`17:24:51Z` 结束（11m04s）。两个 job 的 conclusion 都是 `cancelled`，annotation 均为 `The job has exceeded the maximum execution time of 8m0s`；11m04 是 timeout-cancelled 历史 job 的 start-to-complete wallclock，不是 15 分钟候选预算的成功实测。裁决 annotation 分别记录 `review_not_expected`/`skipped` 与 `primary_pass`/`pass`。因果依据是 annotation，不是 `cancel-in-progress: false`。
4. **回归断言有效：通过。** 把 H0 测试文件放进 base scratch-tree，仅运行目标测试，实际 `AssertionError: assert 8 == 15`；另单独验证新注释断言在 base 的旧注释上按 `AssertionError` 转红。H0 scratch-tree 完整 `tests/test_gate_v2_contract.py` 为 217 passed，`scripts/check_pinned_uses.py` 通过。注释文字断言只锁文档，不作为业务语义或运行时证明。
5. **绑定 H0 的 CI 证据：通过。** Actions run `38049871843` 的 `head_sha` 精确等于 H0，事件为 `pull_request`；`test` job `114206670958` 与 `actionlint` job `114206670909` 均为 `success`。仓内 `ci.yml` 的 test job 顺序执行全量 `python -m pytest -q` 和 `python3 scripts/check_pinned_uses.py`。此证据证明 H0 的仓内 CI，不证明 gate reusable workflow 的慢 API 终态。
6. **慢 API 真实终态：未完成，但不阻断本次止血。** `gate-v2.yml` 只有 `workflow_call`，没有候选 SHA 输入或 `workflow_dispatch`；仓内没有调用该 reusable workflow 的候选验证入口。`v2-tag-sync.yml` 的 dispatch 只在 main 上选择已 canary-verified 的 main SHA，且会移动 v2，本卡不能触发。现有证据分别是静态预算差集、历史 timeout annotation；候选 ref 在 15 分钟预算下的实际慢 API job 终态仍未知。因本次只解除已观察的 8 分钟墙钟限制、不承诺根治 API 慢，缺失的慢 API E2E 不阻断该有界参数止血；合并后的 canary/自然下游观察归主脑收尾。
7. **PR292 隔离：通过。** 只读远端 ref 与本地对象表明 PR292 head 为 `fb513d550c926612ac3105dc85f3e9d6200a2c7c`，与 H0 的共同祖先为 `21e32f82ddaba41814a9f365f8b026fd36d0a8b5`。它增加独立 `arbiter` job（15 分钟），其 `gate` 预算和旧断言仍为 8；固定 PR301 diff 没有混入 arbiter 内容。PR292 后续更新基线应保留本卡的 gate 15 分钟和对应测试；本审查未检出或合并该分支。
8. **发布契约：确认。** gate 仓要求 workflow PR 使用 merge commit；合并与自动 canary/v2 发布由主脑负责。本审查未合并、未移动 v2、未部署。

## Findings

### F1 — P2，非阻断：注释把本批“不拆面板”写成长期禁令

- 规格：设计 A 将“不拆面板 job”列为本批非目标；审查卡明确说明这不是永久架构契约。
- 证据：`.github/workflows/gate-v2.yml:1819` 写着 `Do not split this required job.`，没有限定为本批。
- 影响：未来维护者可能把一次预算修复决策当成长期禁止拆分的架构约束。它不改变当前运行行为，不触及 personal 档 P1 红线。
- 阻断：否。建议后续把句子限定为本批范围或移除；无需改变这次 timeout 止血行为。

### F2 — P2，非阻断：预算注释未提独立的 panel publish 预算旋钮

- 规格：本批只改既有 job timeout；预算说明应与真实控制路径一致，且不能把静态/历史证据写成无条件保证。
- 证据：`.github/workflows/gate-v2.yml:2058` 的 `GATE_PUBLISH_BUDGET_SECONDS` 可取仓库变量，缺省为 120 秒；`.github/actions/gate-aggregator/aggregate.py:346-361` 只拒绝非正值，没有将其上限绑定到 15 分钟 job ceiling。新注释 `:1816-1819` 仅说剩余 headroom 覆盖 runner jitter。实际仓库变量值未查询，不能据此推断当前配置超限。
- 影响：以后若 owner 单独调高 panel 预算，job 仍可能先触发 15 分钟硬超时；当前默认值与现有 run 不构成已证实的回归，timeout 仍以 cancelled 显式失败，不会静默变绿。
- 阻断：否。建议后续注释点明这是两个独立上限；本卡不要求加校验、旋钮或改失败语义。

## OCR 前置扫描

- 状态：`reviewed`，profile `minimax`，模型 `MiniMax-M3.1-Flash-Preview`；envelope 完整，`cli_status=complete`、`coverage=complete`，3 条 finding 已核验（confirmed 2 / refuted 1 / unverifiable 0）。
- OCR 的永久禁拆 finding 经核实后记为 F1；独立 panel 预算旋钮经源码核实后记为 F2。OCR 对“注释声称新 15 分钟已通过 E2E”的 finding 被驳回：注释只叙述历史 wallclock，A-budget.md:163-168 明确没有候选 ref 的慢 API 终态验证。
- OCR severity 是候选输入，本 verdict 的级别按 personal 风险与实证重新判定。

## 验证环境与未覆盖边界

- 结构比较：用仓内 `scripts/git/scratch-worktree.sh` 在固定 H0 scratch-tree 解析 base/head YAML；比较器位于仓外临时目录，scratch-tree 自动清理。
- 红验：base `6b70e369955891b6f58428e377cd30f139aacde2` scratch-tree；仅临时放入 H0 测试文件，没有修改生产代码或实现 worktree。
- 绿测：H0 `2b941c29329b001b512b55eeeefbd439bd0c9ffd` scratch-tree；完整目标测试文件及 pinned uses 检查通过。CI run/job 绑定相同 H0。
- GitHub API 只读调用共 6 次；未查询当前 repo variable 实值、未执行真实慢 API、未触发 canary，也未对 PR292 做合并模拟。
- 派发卡注明主干基线查询 `gh api` 失败；同作业名/首失败步骤的继承红无法与该基线判定。H0 自身的 CI 结论为 success。

## 失败可见性

只读审查；没有改运行时，未知和未覆盖边界均显式记录。
