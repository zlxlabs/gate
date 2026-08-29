# gate PR #92 R1 verdict

审查对象固定为 `d67aa0c03f201bf074557007c0d7d9580a64916c..4c52f82e122e06b1eec4d5dfd6e2bc4e3d3cc58c`；新提交不纳入本轮。工作树现场干净，diff 为 5 个文件、139 行新增、6 行删除；`git diff --check` 通过。

## 结论摘要

两把 job 级锁的当前 YAML 形状正确：顶层没有 `concurrency`；quality/primary 组独立、按 PR 取消且非 PR 用 run id 兜底；gate/ledger 仍是 base 的 `cancel-in-progress: false` 写入锁；其他 job 未加锁。`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_shadow_v2_contract.py` 结果为 141 passed；`python3 scripts/check_pinned_uses.py` 也通过。

## Findings

### F1 — P2：取消上游 job 不保证旧 run 的 ledger 实际落盘

- 严重度：P2（本仓 P1 两问：①真实 PR 连推时会触发；②后果是旧 run 的账本 job 可见失败/缺少观测记录，但 gate 会 fail-closed，且最新 run 仍可写入，当前不构成静默放行或不可接受的数据决策错误，因此不升 P1）。
- 违反依据：不变式 1 对“每 run 独立写入 ledger”的保护意图，以及不变式 7“文档只能陈述已实现事实与已知限制”。
- 位置：`.github/workflows/gate-v2.yml:330-339,377-381,485-501,863-883,1094-1209`；新增文档 `docs/design/gate-convergence-criterion.md:10-11,28-36`。
- 触发路径：同一 PR 的新 `synchronize` run 在旧 quality/primary 尚未完成 `Upload v2 review ledger inputs` 或 `Upload canonical primary audit` 前入组；job 级 `cancel-in-progress: true` 取消旧上游。旧 run 的 `gate` 因 `if: always()` 仍会继续，aggregator 能写终态并由 panel 步骤发布 fail-closed 状态；但 `ledger` 虽有 `if: always()` 和 `cancel-in-progress: false`，仍依赖 `[quality, primary, gate]`，其 resolver 将 ledger input 设为必需、预期 PR 的 primary audit 也设为必需，后续下载/构建没有 `continue-on-error`，缺 artifact 时直接失败。因此 false cancel 只保住了 ledger job 不被这把锁取消，不保证账本文件写完。文档写“为了保住这两处写入”“排队写完”，没有披露这个限制。
- 建议：至少把“被 supersede 的旧 run 可能只有 panel、没有 ledger”记录为已知限制；若产品要求每个 run 都有 ledger，则需在保留两把锁决策下补一条能把取消/缺 artifact 作为可审计账本记录的路径。

### F2 — P2：quality/primary 契约未锁定 fallback 顺序

- 严重度：P2（当前实现正确；缺口只会在未来表达式被改坏时触发，故不满足本仓 P1 的“当前真实路径已触发”）。
- 违反依据：不变式 2 要求“PR 号优先、非 PR 用 run_id 兜底”，以及不变式 6 要求契约测试对改坏敏感。
- 位置：`tests/test_gate_v2_contract.py:310-320`。
- 触发路径：测试只断言组名包含 `github.event.pull_request.number`、`github.run_id` 和 `||`，没有断言 `number || run_id` 的顺序或按代表性事件求值。将当前表达式改为 `github.run_id || github.event.pull_request.number` 后，静态辅助断言仍全部通过；真实 PR 每次 run 会以不同 run id 分组，旧 quality/primary 不再被同 PR 新 head 取消。
- 建议：对完整表达式做精确相等断言，或用 PR / 非 PR 两组上下文验证求值结果。

### F3 — P2：gate/ledger 锁未由测试锁定为 base 的逐字节契约

- 严重度：P2（当前 H0 的两个 mapping 经直接比较确实与 base 相同；问题是回归测试覆盖不足，不是当前运行时形状错误）。
- 违反依据：不变式 3 要求 gate（panel）与 ledger 的 concurrency 块和 base 逐字节相同，不变式 6 要求测试锁死不变式 1–5。
- 位置：`tests/test_gate_v2_contract.py:291-307`。
- 触发路径：测试只分别检查前缀、`repository_id`、PR 字段存在/不存在和 `cancel-in-progress: false`，没有加载 base 或比较完整 mapping。未来把 panel/ledger 的 suffix、字段值或表达式改成另一种仍满足这些局部断言的形态时，测试会保持绿色。
- 建议：用 base fixture/精确常量比较两个 writer concurrency mapping；保留当前字段语义断言作为补充即可。

## 降层三问

1. 终态写入成功前的不可逆动作：quality 已可能 checkout、安装依赖、运行 caller tests 并产生临时 preflight/install 文件；primary 已可能向外部模型发出请求并消耗额度。取消只停止本地 job，不回滚已发出的模型请求；runner.temp 中未上传的中间产物随 runner 清理，已上传的 artifact 则留在该旧 run。gate 的 aggregator/panel 是后续 GitHub API/Artifact 写入，旧 primary 被取消时可能只能写 fail-closed 终态；ledger 可能因缺输入而不写。
2. 守卫值唯一性：在真实 `workflow_call` PR caller 中，`github` context 绑定 caller，`repository_id + pull_request.number` 对一个仓库内的 PR 唯一；多仓共用 reusable 时 caller 的 repository id 不同；fork PR 使用 base 仓库的 PR 号和 repository id，仍在 base 仓库内唯一；非 PR 事件的 PR 字段为空，`|| github.run_id` 提供每 run 唯一值。当前模板只触发 `pull_request`，但新锁的 fallback 对非 PR 也成立。
3. 保护范围：`cancel-in-progress: false` 保护的是 gate/ledger job 的排队与运行机会，不等于保护写入内容正确，也不保证上游 artifact 存在。内容正确性仍由 aggregator、artifact identity/schema 校验和 ledger 的 fail-closed resolver 负责；这正是 F1 的边界。

## 对抗视角

- `workflow_call` 上下文：没有发现“PR 号恒为空”的问题。GitHub 官方说明 called reusable workflow 的 `github` context 始终关联 caller workflow；本仓 caller 模板由 `pull_request` 触发，因此 `github.event.pull_request.number` 可取值。`jobs.<job_id>.concurrency.group` 也允许使用 `github` context。非 PR 场景才走显式 run-id fallback。
- `needs` 与 `always()`：取消 quality/primary 后，`gate` 的 job-level `if: always()` 会在两项依赖结束后继续；其 aggregator 看到 cancelled 结果并 fail-closed，终态上传与 panel 发布步骤仍以 `always()` 继续，notify 在 gate 失败时由 `failure()` 触发。`ledger` 也会被调度，但不能据此推断 ledger 正常写完：它的 artifact resolver/下载/构建链对缺失输入是硬失败，见 F1。
- 组命名空间：新组的 `gate-required-v2-quality-` / `gate-required-v2-primary-` 前缀与 panel、ledger、shadow 前缀不同，且 quality/primary 不共组；当前实现没有跨组误杀证据。

## 熵增审查

没有发现需要删除的新增抽象。`_assert_expensive_job_cancel_lock` 有 quality/primary 两个实际消费者；新增并发设计文档被 `clean-streak-convergence.md:34` 的事实表引用，并与 workflow/contract test 共同说明同一契约；progress 文件是本卡要求的过程产物，不是运行时抽象。没有新增状态、配置项、fallback 或重试机制。

## 证据与范围

- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_shadow_v2_contract.py`：141 passed。
- `python3 scripts/check_pinned_uses.py`：通过。
- `actionlint .github/workflows/gate-v2.yml`：无 workflow error；仅报告已有 shellcheck 风格提示（变更未涉及的脚本行）。
- 直接静态探针：H0 的 gate/ledger concurrency mapping 与 base 相同；反转 quality fallback 顺序的突变仍通过现有 helper 的所有形状断言。
- 运行时语义依据：[GitHub reusable workflow context](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)、[GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency?apiVersion=2022-11-28)、[job needs/always](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs)、[workflow cancellation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation)。

## Verdict

无 P1；F1–F3 为可接受不阻塞合并的 P2 backlog。若要求取消旧 run 后仍为每个 run 产出 ledger，F1 应升级为必修设计问题。

verdict: pass
