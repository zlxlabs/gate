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
