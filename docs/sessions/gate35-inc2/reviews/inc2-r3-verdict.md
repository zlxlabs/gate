FAIL

审查对象：`1d6a2f05b756052e77c155cf1f9db8fb156cefde..e06e868`；减法增量：`ff796e400fae82acb5c5db1967e110012bbb1f28..e06e868`

审查时间：2026-08-20（Asia/Shanghai）  
执行器与模型：Codex / GPT-5（`delegate --class big`，dispatch `dlg-20260820-120152-8d269d`）  
采用风险等级：`personal`。本仓唯一威胁模型是 agent 不应能给自己开绿灯；P1 只按数据丢失、静默出错、崩溃定级。

本轮新证据：对冻结 SHA 中的删除后调用方、生产者参数、测试实际改动和设计文档全文做了独立核对；用只读 Python 探针验证四种 disposition 绑定变化；用 YAML 解析确认 disposition workflow 只有 `workflow_dispatch`。`ocr-review` 已启动但约三分钟未返回最终 envelope，停止时记录为未完成/不可判定，未当作“扫过且干净”。本卡未运行测试套件。

## A. 减法审查

### A-1. 删除校验后是否静默通过

结论：未发现删除动作把当前轮 P1 静默变成 clean。保留下来的正确性守卫仍完整执行：

- `validate_disposition_receipt` 在 `.github/actions/gate-aggregator/convergence.py:419-457` 依次检查 receipt 类型、schema、唯一 `false-positive` 枚举、必需字段、 repository/PR、epoch、head、原始 audit digest 和 exact current P1 finding。任一不符返回 `consumable=False`，没有放行分支。
- `consume_dispositions` 在 `.github/actions/gate-aggregator/convergence.py:499-548` 对错误的 P1/receipt 形状直接 `fail_closed=True`；非 legacy stub 的不合法状态进入 `rejected_receipts`，命中 `.github/actions/gate-aggregator/convergence.py:477-485` 的 fail-closed 集合后在 `.github/actions/gate-aggregator/convergence.py:1126-1137` 使 round 失败。只有 `status.consumable` 且 exact finding 仍在 `remaining` 中时，`.github/actions/gate-aggregator/convergence.py:532-536` 才会移除 P1。
- 删除的 `accepted`、`wont-fix`、`fixed` 不会落入“普通不消费但继续计数”的旧分支；当前 `.github/actions/gate-aggregator/convergence.py:435-436` 将它们判为 `unknown_disposition`，并由 `.github/actions/gate-aggregator/convergence.py:477-485,538-541` fail-closed。这个行为与仍保留的一条测试断言不一致，详见后续 finding，但生产路径不是静默放行。
- `diff_digest` 没有从正确性事实中消失：它仍是 `Scope` 字段（`.github/actions/gate-aggregator/convergence.py:59-89`），`derive_epoch` 对完整 Scope 重算（`:603-607`），receipt epoch 与当前 epoch 不一致即失效（`:446-448`）。删除 receipt 内的直接 `diff_digest` 字段不会让 scope 变化绕过 epoch。
- 被删的 nonce/撤销、expiry、evidence manifest、issuer provenance、primary run 字段和 receipt 自摘要检查均不再有构造/消费分支；其缺失不会被当成“字段缺失所以 clean”，而是 receipt 只按新的九字段形状验证。它们属于 owner 已裁决删除的多人安全机制，不在 personal 档重新要求加回。
- `malformed_inputs` 原来只保存诊断原值，删除不改变 `fail_closed`、`remaining_p1_ids` 或 streak 转移；删除后的异常仍由 `.github/actions/gate-aggregator/convergence.py:512-518` 变成 typed rejection 和 fail-closed。

逐个分支核对结果：删除 expiry/evidence/issuer/nonce/revocation/receipt digest 的旧 `if` 后，没有新的“接受并计 clean”出口；删除的非 `false-positive` 枚举走 unknown/fail-closed；删除的直接 scope 字段由 epoch 绑定覆盖；删除的 primary run/attempt 仍只用于 control workflow 选择并核验 audit（`.github/workflows/gate-v2-disposition.yml:37-61`），不是 receipt 消费的隐式放行条件。

### A-2. 悬空引用、死代码与构造/序列化残留

结论：被删机制的代码引用已清理干净，未发现因字段缺失而永远取值或崩溃的生产分支。

- receipt 当前唯一构造形状由 `.github/actions/gate-disposition/issue_receipt.py:131-142` 生成，只有九个保留字段；命令行参数定义也只在 `:172-183` 声明当前参数。`.github/workflows/gate-v2-disposition.yml:5-10,127-146` 与 producer 参数一致。
- `DispositionReceipt.as_dict()` 在 `.github/actions/gate-aggregator/convergence.py:161-186` 只序列化九字段；`git grep` 对任务卡列出的 `nonce|revocation|evidence_manifest|approval_ref|issuer_login|issuer_user_id|expires_at|receipt_digest|malformed_inputs` 在目标 `.github/` 与 `tests/` 返回零匹配。
- `primary_run_id` / `primary_run_attempt` 仍出现在 `.github/workflows/gate-v2-disposition.yml:7-8,41-42`，但它们是人工输入的 canonical audit 定位参数，并在 `:55-61` 被自报 run/pr/head/attempt 校验；它们不再出现在 `DispositionReceipt` payload，不是悬空 receipt 字段。
- `_legacy_disposition_stub` 在 `.github/actions/gate-aggregator/convergence.py:395-407` 仍被 `validate_disposition_receipt`（`:424-427`）和 `consume_dispositions`（`:520-522`）调用，用于表示“无 disposition”，不是死代码或永真条件。
- `as_dict()`、producer payload 和 ledger projection 的目标字段均有调用方：producer 的跨进程 fixture 在 `tests/test_gate_convergence_artifact.py:125-160` 重建 receipt，ledger 在 `.github/actions/review-ledger/build_ledger.py:481-522` 只投影 `status/reason`。旧可选 digest/manifest 调用参数没有遗留引用。

### A-3. 增量 1 的既有测试语义是否被改写

结论：大多数改动是删除已裁决机制后的等价重写，未见把保留正确性目标的断言放松；但有一条测试语义未随枚举裁减更新，目标 SHA 下会失败。

- `tests/test_gate_convergence.py:152-197` 将 issuer/evidence/full-scope 旧测试缩为 current-round 的 head/epoch/digest/finding 绑定测试；exact finding 的 partial disposition 语义仍由 `:177-197` 锁定。
- `tests/test_gate_convergence.py:211-234` 保留重复 receipt 幂等和 malformed input fail-closed；`tests/test_gate_convergence_artifact.py:125-160` 仍断言真实 subprocess 写出的 payload 字节、raw audit digest、artifact 名和 immutable 重放；`tests/test_gate_v2_contract.py:105-121` 反向锁定旧输入/撤销/evidence/issuer 路径已不存在；`tests/test_review_ledger.py:117-143` 仍锁定 projection 不影响 required gate。
- 但 `tests/test_gate_convergence.py:200-208` 仍用 `disposition="accepted"` 并断言 `(clean_streak, eligible_rounds, decision) == (0, 1, "collecting")`。目标代码 `.github/actions/gate-aggregator/convergence.py:435-436,477-485` 已将 `accepted` 变成 unknown disposition，实际探针输出为 `fail_closed 0 0 invalid disposition: unknown_disposition`。这不是安全机制应加回，而是该既有测试应改为与裁减后的唯一枚举/失败语义一致。

### A-4. 文档与实现一致性

结论：`AGENTS.md` 与设计文档“增量 2”节本身已表达 personal 最小契约，但设计文档前面的总不变式/五轴表仍描述已删除机制，文档整体不一致，列入后续 P2 finding。

- `e06e868:AGENTS.md:1-9` 声明 `risk-tier: personal`、唯一威胁模型和仅人工控制入口；设计文档“增量 2”在 `docs/design/clean-streak-convergence.md:199-234` 明确删除 issuer/evidence/nonce/revocation/expiry/完整 scope 机制，只保留 `head_sha + audit_digest + epoch + finding_id`，与当前 `.github/actions/gate-aggregator/convergence.py:28,410-457` 及 workflow `:3-10` 对齐。
- 但同一设计文档 `:94,131-139,147,156,302` 仍要求 accepted/wont-fix/fixed、protected issuer、evidence ref/manifest、expiry、nonce、revocation、receipt digest 等已删除机制，并继续引用已删除的生命周期/撤销测试。该矛盾不会直接改变当前代码结果，但会把下一轮实现和验收重新导向已裁决的过度设计。

