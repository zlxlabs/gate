# R1 独立审查 verdict：隔离 UI 评论副作用

- 审查仓：`zlxlabs/gate`；风险档：`personal`
- 工单：`zlxlabs/gate-hub#1751`
- 冻结代码审查范围：`7b547448bd83150f1a767573d7ab81b5f55de452..71ac0c29e3e7c0bdb8546993eb674f0b85e85b6f`
- 审查结论：可合并；冻结代码 diff 无阻断 finding，正常 PR 检查仍须通过。
failure-visibility: clean

本文件记录的代码审查对象固定为上述 SHA 范围。写入本 verdict 和更新 PR 描述不改变该范围。

## 范围与真实调用契约

冻结 diff 只在 `.github/actions/ui-evidence-publish/action.yml` 的评论步骤添加条件 `continue-on-error: ${{ inputs.e2e-status == 'success' }}`，并在 `tests/test_ui_evidence_publish.py` 锁定该 manifest 值。

真实调用方是 gate-hub 提交 `9fba2e73fcb4f7fd2445c6712141e29bb6b86b51` 的 `templates/ui-visual/workflow.yml`：

- 仅 PR 事件且 `steps.upload_evidence.outcome == 'success'` 时调用评论 action。
- 必需输入 `e2e-status` 明确取自 `${{ steps.e2e.outcome }}`；完整 head SHA、PWRS URL 和 subject 也由 caller 显式传入，GitHub token 使用 action 默认值。
- `e2e` 步骤没有 `continue-on-error`。E2E 失败本身使 job 失败；被前序失败跳过时，job 也已失败。
- 必需报告上传是独立步骤，没有 `continue-on-error`。上传失败时评论 action 条件为假，上传失败仍使 job 失败。
- UI visual 不在 branch protection；此改动减少非权威评论步骤造成的假红与重跑成本，不改变合并阻断能力。

## 不变式检查

1. **Caller 输入**：当前受支持调用明确从 `steps.e2e.outcome` 传 `e2e-status`，不存在通过省略该必需输入来判定 E2E 成功的路径。
2. **E2E 结果**：E2E 为 success 时，评论步骤的 `continue-on-error` 为 true；E2E 非 success 时为 false。E2E 失败不会被评论步骤的容错改成成功。
3. **必需上传**：评论 action 只在报告上传成功后运行。上传失败时 action 不运行，原上传步骤失败仍保留在 job 结论中。
4. **评论副作用**：评论步骤只执行 PR comments 的 GET/PATCH/POST，不创建、上传或搬运 E2E 报告。评论参数缺失或 API/运行环境故障可能令评论步骤失败，但脚本错误仍出现在 step outcome 和日志中；报告本身已由前序步骤上传。
5. **Runner 语义**：当前 R8 runner 镜像记录的 Actions runner 版本为 `2.338.0`。固定版本 schema 的 `run-step` / `uses-step` 允许 `continue-on-error: boolean-steps-context`，该上下文含 `inputs`。runner 执行实现会保留内层 step 的原始 failure outcome、将其 conclusion 按 continue-on-error 处理，日志仍可见，且不会清除此前 E2E / 上传步骤的失败。
6. **核心产物**：E2E 和报告上传不在这个容错步骤内；没有将核心测试结果或报告发布的判决交给评论步骤。
7. **熵增**：没有新增文件、抽象、状态或配置层；仅让评论步骤按真实 E2E producer outcome 决定是否容忍失败。

Runner 语义依据：[v2.338.0 `action_yaml.json`](https://github.com/actions/runner/blob/v2.338.0/src/Runner.Worker/action_yaml.json)、[ActionRunner.cs](https://github.com/actions/runner/blob/v2.338.0/src/Runner.Worker/ActionRunner.cs#L206-L218)、[ActionManifestManager.cs](https://github.com/actions/runner/blob/v2.338.0/src/Runner.Worker/ActionManifestManager.cs#L521-L554)、[CompositeActionHandler.cs](https://github.com/actions/runner/blob/v2.338.0/src/Runner.Worker/Handlers/CompositeActionHandler.cs#L437-L477)、[ExecutionContext.cs](https://github.com/actions/runner/blob/v2.338.0/src/Runner.Worker/ExecutionContext.cs#L1422-L1452)。

## 外部 OCR 预审与处置

OCR envelope 为 `status=reviewed_fallback`、`coverage=complete`、`cli_status=complete`；minimax primary 因 `leg_timeout` 在 `900.115s` 终止，deepseek 备腿成功。验证器报告 `total=1`、`confirmed=1`、`refuted=0`、`unverifiable=0`、`unverified=0`。

OCR 将一项候选标为 `category=bug`、`severity=medium`：因为 `continue-on-error` 作用于整个评论步骤，E2E 成功时，评论参数或运行环境错误也可能只让评论失败、job 仍成功。该标签是外部工具输入，不直接作为本仓风险结论。

按 gate 仓 `personal` 风险档的真实使用方式核查：支持的 caller 会传入真实 E2E outcome；当前工作流已独立完成必需报告上传，评论只是非权威深链发布。即使评论参数/API/运行环境出错，报告与 E2E 判决不被更改，step outcome 和错误日志仍可见，受影响的是 PR 粘性评论是否出现。此情形不触及个人档 P1 红线（数据丢失、静默出错或崩溃），故该候选裁为**非 P1、不阻断交付**，本轮不为它增加额外状态或分支。

独立审查另检查了“caller 漏传 `e2e-status`”的推演：它不符合真实 caller 的 required-input 契约，因此不作为当前实现缺陷；也不为虚构 caller 增加兼容逻辑。

## 验证与边界

- 实现验证：`tests/test_ui_evidence_publish.py` 13 passed；gate 全量测试 1925 passed；`python3 scripts/check_pinned_uses.py` 通过。针对条件值的变异红验将 `success` 改为 `failure` 后测试失败，还原后通过。
- PR 检查快照：`actionlint` 成功；GitHub `test` 检查当时仍在运行。
- 未在托管 Actions job 中注入评论 API 故障；对运行结果的判断依据是冻结 diff、真实 caller 和 runner v2.338.0 schema / 执行实现。独立审查本身没有运行测试或触发 GitHub 写操作。
