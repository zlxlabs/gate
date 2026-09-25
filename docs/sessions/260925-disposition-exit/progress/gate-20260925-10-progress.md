### 2026-09-25 / convergence 消费语义

- 当前阶段：implementing
- 本段结论：回执 schema 升至 v3；消费侧验证反证/跟踪引用、tier 和当前绑定，并只扣减精确匹配的 P1。轴 A 覆盖全部/部分解除、重复、过期绑定及 saas deferred 的收敛单测通过。
- 关键决策与已否决方案：旧 v1/v2 回执不具备 v3 证据契约，统一拒收，不允许历史无证据回执解除阻断。
- 下一步唯一动作：实现 issue_receipt.py 的两类回执签发与拒签矩阵。

### 2026-09-25 / 签发端契约

- 当前阶段：implementing
- 本段结论：签发器生成 v3 false-positive/deferred 回执；反证字段、同仓 issue 引用、saas 限制均在 artifact 写入前 fail-fast。15 项 producer 子进程测试通过并验证实际写出字节。
- 关键决策与已否决方案：`#n` 仅校验格式；不查询 issue 是否存在或 open，也不增加 GitHub 权限。
- 下一步唯一动作：扩展 disposition workflow 与 caller 模板，使新 optional inputs 进入签发器并统一 approver 上下文。

### 2026-09-25 / workflow 接线

- 当前阶段：implementing
- 本段结论：disposition workflow 与 caller 模板新增三个 optional inputs；approver 与 approver_id 同取 `github.actor` 上下文；不改权限、不触发标签，v2 caller 模板明确无标签豁免。相关契约测试和 pin 检查通过。
- 关键决策与已否决方案：保留老 caller 的 required-input/权限契约；旧 caller 可启动，但缺反证的 false-positive 会在上传前明确拒签。
- 下一步唯一动作：接通 aggregator 的逐条终态投影与 summary/ledger 留痕，并做真实 producer 到 ledger E2E。

### 2026-09-25 / aggregator 与 ledger 端到端

- 当前阶段：implementing
- 本段结论：聚合器只从当前 P1 集合扣除有效回执覆盖项；全覆盖转 pass，部分覆盖保留未覆盖 P1 并继续 fail。真实 producer 子进程字节已贯通 aggregator、terminal 与 ledger；全覆盖、部分覆盖、SaaS deferred 拒收和旧绑定负例覆盖在 649 项受影响测试中。
- 关键决策与已否决方案：head_sha 在 epoch 前校验，确保 force-push 旧回执给出 `head_sha_mismatch`；独立 epoch 不匹配仍报 `epoch_mismatch_stale`。SaaS deferred 在签发侧和消费侧均拒收。
- 下一步唯一动作：更新 README、状态机设计与新契约设计文档，并完成最终全量验证。

### 2026-09-25 / 修正 G4 结构守卫

- 当前阶段：implementing
- 本段结论：全量测试发现 G4 行构造器守卫仍匹配旧的 `receipt claim (` 文案，与新逐条留痕格式不符；更新为锁定当前唯一行构造器，定向测试通过。
- 关键决策与已否决方案：不保留旧文案或兼容分支，测试直接约束新的 disposition/finding 行格式。
- 下一步唯一动作：完成设计文档并重跑全量验证。

### 2026-09-25 / 契约文档与全量验证

- 当前阶段：implementing
- 本段结论：README、收敛设计和新契约文档已更新；旧 record-only 会话文档仅追加重开缘由。全量 pytest 通过 1166 项，pin 检查、actionlint 和 diff whitespace 检查均通过。
- 关键决策与已否决方案：gate-hub#1073 对 internal deferred 的现存验收文字与锁定决策冲突；实现按 internal 允许 deferred，需主脑修订 issue 判据。
- 下一步唯一动作：验证 approver 上下文守卫的反向红例并完成提交、推送及报告。
