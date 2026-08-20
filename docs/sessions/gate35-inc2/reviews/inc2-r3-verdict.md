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

## B. personal 档最小充分性

### B-1. 四种绑定变化是否分别使豁免失效

结论：四种变化均失效，没有发现会让 clean streak 虚假的漏绑。

只读探针以当前 Scope、一个 `false-positive` receipt 和一个 P1 finding 为基线，输出如下：

```text
head   False False False epoch_mismatch_stale
audit  False False False audit_digest_mismatch
epoch  False False False epoch_mismatch_stale
finding False False False finding_not_current_p1
```

- 换 head：当前 Scope 的 epoch 先在 `.github/actions/gate-aggregator/convergence.py:446-450` 重算并与 receipt 比较，因此换 head 返回 `epoch_mismatch_stale`；即使同步替换 primary head，也不会消费旧 receipt。
- 换 audit：`.github/actions/gate-aggregator/convergence.py:451-452` 检查 audit digest 格式及精确相等，返回 `audit_digest_mismatch`。
- 换 epoch：`.github/actions/gate-aggregator/convergence.py:446-448` 直接拒绝 receipt epoch 与当前 Scope 派生值不一致的输入，返回 `epoch_mismatch_stale`。
- 换 finding：`.github/actions/gate-aggregator/convergence.py:453-456` 拒绝 wildcard/category target，并要求 finding id 仍在当前 `primary.p1_ids` 中，返回 `finding_not_current_p1`。

四种情况均在 `validate_disposition_receipt` 层变成不可消费；`consume_dispositions` 只有在 `.github/actions/gate-aggregator/convergence.py:532-536` 成功移除当前 exact finding 后才会影响 streak。

### B-2. agent 能否在 CI 中触发签发

结论：目标 workflow 的唯一触发器是人工 `workflow_dispatch`，没有 `push`、`pull_request`、`schedule` 或其他自动触发路径。

- `.github/workflows/gate-v2-disposition.yml:3-10` 只有 `workflow_dispatch` 及五个手工输入；YAML 解析的触发器键为 `{'workflow_dispatch': ...}`。
- 签发脚本只在 `.github/workflows/gate-v2-disposition.yml:127-146` 的 control job 中被调用；没有自动 job 或 CI caller。
- 这满足当前 `AGENTS.md` 和设计文档增量 2 `docs/design/clean-streak-convergence.md:225-228` 的 personal 威胁模型。真实 aggregator 下载 receipt、Required Check 消费和 control workflow canary 不属于本轮，见越界意见。

### B-3. 是否还残留只为不存在对手服务的机制

结论：receipt 的九个保留字段都服务于当前轮正确性或可读性，没有发现必须为多人威胁模型保留的 receipt 字段或校验。

- `repository_id`、`pr_number`、`head_sha`、`epoch`、`audit_digest`、`finding_id` 是当前 PR/轮次/exact finding 的正确性绑定；`schema_version` 和唯一 `false-positive` 是协议形状；`reason` 是三个月后可读的人工理由（`.github/actions/gate-aggregator/convergence.py:161-186`）。
- workflow 中仍有 `primary_run_id` / `primary_run_attempt`（`.github/workflows/gate-v2-disposition.yml:7-8`），它们用于人工指定并重新下载当前 canonical audit（`:37-61`），不是 issuer/多人字段，也不能从 receipt 消费侧绕过四项绑定。
- `.github/workflows/gate-v2-disposition.yml:17-19` 的 PR 级 `concurrency` 与 `:22-27` 的 `gate-disposition` environment 声明是个人档下不参与 correctness 的遗留运维钩子；当前没有 required reviewer 配置，代码也不读取 issuer 审批。列为 P3 backlog，不作为 P1/P2 finding，不要求恢复任何已删除安全机制。

### B-4. 序列化、reducer 与字段缺失是否会崩溃

结论：当前已通电的调用点没有发现 `AttributeError` / `KeyError` / `TypeError` 崩溃路径；异常 receipt 会 fail-closed。

- `DispositionReceipt.as_dict()`、`Scope.as_dict()`、`CanonicalPrimary.as_dict()` 和 `ConvergenceState.as_dict()` 的字段均在 `.github/actions/gate-aggregator/convergence.py:74-89,148-158,175-186,321-332` 明确列出。只读探针对四类对象执行 `json.dumps(obj.as_dict())` 均成功，长度分别为 `588/184/352/396`。
- `validate_disposition_receipt` 在 `.github/actions/gate-aggregator/convergence.py:419-432` 先检查 receipt/primary 类型，再读取字段；`consume_dispositions` 对非 `DispositionReceipt` 在 `:512-518` 生成 typed rejection，不直接访问畸形对象属性。
- producer 的 JSON 外壳 `kind` 由 `.github/actions/gate-disposition/issue_receipt.py:156-168` 添加；当前 subprocess fixture 在 `tests/test_gate_convergence_artifact.py:145-160` 明确筛选 dataclass 字段后重建 receipt。artifact 下载/解析 adapter 尚未通电，如何剥离 `kind` 属增量 3 前置项，不在本轮判定为 finding。

## Findings

### F-1 — P2：裁减后的唯一枚举与既有测试断言冲突

- 严重级别：P2（personal；阻断测试契约，但不是数据丢失、静默出错或崩溃）。
- 文件：`tests/test_gate_convergence.py:200-208`；实现依据 `.github/actions/gate-aggregator/convergence.py:28,435-436,477-485,538-541`。
- 违反 spec：设计文档增量 2 的 receipt 形状与唯一准入约束 `docs/design/clean-streak-convergence.md:220-228`，以及 `AGENTS.md` 的 personal 风险裁决；`DISPOSITION_KINDS` 只允许 `false-positive`。
- 具体失败场景（已实测）：目标测试 helper 构造 `DispositionReceipt(disposition="accepted")`，然后 `evaluate_round`。当前实现返回 `fail_closed`、`clean_streak=0`、`eligible_rounds=0`、`reason="invalid disposition: unknown_disposition"`；但该测试仍断言 `collecting` 且 `eligible_rounds=1`。也就是说目标 SHA 中这条既有测试的语义没有随 `accepted/wont-fix/fixed` 删除而更新，任务卡给出的“573 passed”与此测试行为不一致。
- 建议修法：只修测试契约，将该 case 改为构造 malformed/unknown current-target receipt 并断言 fail-closed；不要把 `accepted` 加回实现，也不要把 unknown disposition 变成普通 blocked 后继续计数。

### F-2 — P2：设计文档总览仍要求已裁决删除的多人机制

- 严重级别：P2（personal；文档契约矛盾，不直接改变当前 gate 结果）。
- 文件：`docs/design/clean-streak-convergence.md:94,131-139,147,156,302`；已改写的裁决在同文件 `:199-234`。
- 违反 spec：同一设计文档增量 2 的范围裁决（`:201-208`）和最小 receipt/准入契约（`:214-228`）。
- 具体失败场景：下一轮实现者按 §1.5 `INV-C3`、§2.3 生命周期表或 §2.5 介质表实现/验收时，会重新要求 expiry、evidence manifest、issuer provenance、nonce、revocation、receipt digest 及 `accepted/wont-fix/fixed`；但当前实现只生成九字段 receipt（`.github/actions/gate-disposition/issue_receipt.py:131-142`），且 workflow/测试已删除这些输入。文档还引用 `test_disposition_lifecycle_invalidates_on_head_digest_expiry_and_revocation` 等目标 SHA 中不存在的测试名，导致“代码绿、设计验收红”或反向加回过度设计。
- 建议修法：仅同步文档：删除/改写旧总不变式、五轴 disposition 表、介质表和最终判据中的多人机制描述，统一写成 personal 的 `workflow_dispatch` + `head_sha/audit_digest/epoch/finding_id`；不新增任何安全机制。

## Backlog

### 存量

- 本轮不审 `.github/actions/gate-aggregator/aggregate.py` 的真实 disposition 消费；当前调用仍是 `waiver_receipts=()`，属于增量 3 接线前置项。
- workflow artifact 的真实下载、receipt `kind` 外壳剥离、原始字节/artifact id 的解析契约和跨进程 reducer 消费尚未通电；保留为增量 3，不把未通电路径倒灌为本轮 finding。
- 真实 `gate/gate` Required Check 是否因 disposition 变绿、`ConvergenceState` 外部 replay、control workflow dispatch/canary 均待增量 3 实证。

### 增量 3 前置项

- aggregator 必须从 artifact 下载 receipt 并将九字段映射为 `DispositionReceipt`，保留 `kind` 只作外壳元数据；下载失败、JSON 形状错误或历史不完整必须 fail-closed/manual，不能默认为无 receipt 后 clean。
- 需要真实验证 current head/audit/epoch/finding 的 receipt 消费，以及缺 receipt、旧 epoch、错误 digest、未知 finding 的 gate 终态；纯函数探针不替代 canary。
- 需要保留 producer 实际 artifact 名、原始 audit bytes digest 和 control run 元数据的跨进程契约产物；本轮只确认 producer 写入契约，未确认真实下载链路。

## 越界意见

本轮没有把以下事项列为 finding：aggregator 从 artifact 下载 disposition receipt 并交给 reducer；真实 `gate/gate` 因 disposition 变绿；`ConvergenceState` 外部重放；control workflow 的真实 dispatch/canary。它们是设计文档 `docs/design/clean-streak-convergence.md:236-285` 明确归属的增量 3。也没有因个人档缺少 issuer/evidence/nonce/expiry/revocation/完整 scope 字段而要求加回；这些是 owner 已裁决删除的多人安全机制。

## 与前两轮的关系

前两轮共 5 个 P1、2 个 P2；本轮不重报其旧失败场景，裁决如下：

| 前两轮 finding | 本轮裁决 | 依据 |
|---|---|---|
| R1-P1：canonical audit artifact 名称错误 | 已修 | 当前 `.github/workflows/gate-v2-disposition.yml:53-56` 按 `repository_id/head/run_id/run_attempt` 构造完整名称，并在 `:55-61` 校验下载内容的 run/attempt/head/PR。 |
| R1-P1：盲读 audit `.epoch` 导致 receipt 不可消费 | 已修 | 当前 `.github/workflows/gate-v2-disposition.yml:69-125` 从 PR 与 audit scope 构造 `Scope`，调用 `validate_scope`/`derive_epoch`；producer `.github/actions/gate-disposition/issue_receipt.py:88-104,122-140` 只接收完整 scope。 |
| R1-P1：evidence ref 未读取/验证 | 已随裁减消失 | evidence manifest/ref 及其校验已按 personal 裁决删除；本轮不以“应恢复 evidence”作为意见。 |
| R1-P2：receipt digest 不承载完整 scope | 已随裁减消失（正确性目标仍由 epoch 保留） | 直接 scope/digest 字段按裁决删除；`.github/actions/gate-aggregator/convergence.py:446-450,603-607` 仍用完整 Scope 派生 epoch 并拒绝旧 epoch。 |
| R1-P2：approval ref / issuer provenance 可伪造 | 已随裁减消失 | `AGENTS.md` 与设计文档增量 2 规定单一人类 owner、仅 workflow dispatch；issuer provenance 是多人协作重开条件，不属于 personal P1。 |
| R2-P1：环境无 required reviewer，普通 write 用户可签发 | 已随裁减消失 | personal 威胁模型不包含第二个人类 write 用户；当前准入只需人工 `workflow_dispatch`，不重新要求环境审批或 issuer 字段。 |
| R2-P1：任意 Actions run/artifact 可伪造 canonical audit provenance | 已随裁减消失 | run provenance 校验被明确列为多人协作重开条件；本轮只审 personal 的 manual dispatch 入口，不把该越权模型升级为 P1。 |

## 评审边界与命令记录

- 未运行测试套件，符合任务卡要求；只运行了冻结 SHA 的 `git show`/`git diff`、`git grep`、YAML 解析和 Python 临时探针。
- 四项绑定探针输出见 B-1；序列化探针输出见 B-4；`accepted` stale-test 探针输出见 F-1。
- OCR 前置扫描命令为 `ocr-review --repo <repo> --from 1d6a2f05b756052e77c155cf1f9db8fb156cefde --to e06e868 --audience agent --concurrency 4 --background-file /tmp/gate35-inc2-r3-ocr-bg.md`；约三分钟无最终 envelope，发送中断后进程以 `exit_code=130` 结束，stderr 显示本地 verify funnel 在等待子进程时被 `KeyboardInterrupt`，故不视为已审过。
