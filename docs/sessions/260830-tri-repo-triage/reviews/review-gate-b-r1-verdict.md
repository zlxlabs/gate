# gate-B disposition receipt v2：第 1 轮对抗审查 verdict

审查对象固定为 `dab9c0cbb75ce7aab323397ba54e2e6b8eff63bf..d90f4c9da92efd51929667599833a2123c42df0f`；H0 为 `d90f4c9da92efd51929667599833a2123c42df0f`。只审本次 diff，不把审查期间的新提交纳入本轮。

## 总结

本轮没有 personal 档 P1。发现 4 条 P2：PR 状态面板没有发布 G4 resolved 行；新时间戳破坏重复签发的幂等行为；新增授权字段的 malformed 测试没有锁住全部分支；`_single_line_reason` 是无第二消费者的单用 helper。A/B 语义轴的归因问题按 P2 风险记录但不再重复计为独立 finding；C 语义轴安全。

## Findings

### P2-1：PR 状态面板没有消费 required_disposition_lines

- 违反条款：spec 5 要求 Step Summary、PR 评论、`::notice` 三处同源自 `required_disposition_lines`；同一不变式是“已用 receipt 的 G4 可读行在三处发布面一致可见”。
- 证据：新增的 builder 在 `.github/actions/gate-aggregator/convergence.py:661-671`；aggregate 只在 `.github/actions/gate-aggregator/aggregate.py:746-748` 将它写入 `Outcome.resolved_findings`，随后 `.github/actions/gate-aggregator/aggregate.py:916-919` 渲染 Step Summary，`.github/actions/gate-aggregator/aggregate.py:1937-1940` 输出 `::notice`。仓内唯一 PR 评论是状态面板（`.github/actions/gate-aggregator/aggregate.py:1049-1051`），其 renderer `.github/actions/gate-aggregator/aggregate.py:995-1046` 只接收 terminal rows，不接收 `resolved_findings`，也不调用 `required_disposition_lines`。
- 新证据：动态探针输出 `step_summary_has_required_line= True`、`pr_panel_has_required_line= False`。
- P1 两问：①真实使用会触发吗？会；只要当前 primary 有被 receipt 消费的 P1，Step Summary/notice 有 resolved 行，而 PR sticky panel 仍只有 pass 状态。②后果能否接受？不能满足 spec 的审计可见性，但 gate 的 pass 仍由已验证 receipt 决定，没有因缺少评论行而把失败判成通过、丢数据或崩溃；按 personal 档不升 P1，保留 P2。

### P2-2：workflow 的新 approved_at 破坏重复签发的幂等语义

- 违反条款：spec 1 的签发字段与 spec 6 的 `_write_immutable` 幂等不变式；同一 `(epoch, audit_digest, finding_id)` 的重复签发应保持既有 immutable artifact 语义，而不是因为自动生成的时间不同变成冲突。
- 证据：`.github/workflows/gate-v2-disposition.yml:167-175` 每次 job 都执行新的 `date -u` 并传入 `--approved-at`；artifact 名仍由 `.github/actions/gate-aggregator/convergence.py:651-658` 的 epoch/digest/finding 组成；producer 的 `.github/actions/gate-disposition/issue_receipt.py:186-194` 对同名不同字节抛 `immutable artifact conflict`。
- 复现：真实 `issue_receipt.py` subprocess，除 `approved_at` 外 argv、audit、scope、approver、reason 全相同：`first_returncode= 0`；`second_returncode= 1`；`second_error= ValueError: immutable artifact conflict: gate-disposition-receipt-v2-92c38163af0628e124583322d698e7edc59d00d26d75930d759b61f09805b934-6c4c59cba604-p1`。
- P1 两问：①真实使用会触发吗？会；重复 dispatch/re-run 同一 PR、finding 和 scope 时 workflow 会重新取当前时刻。②后果能否接受？不能接受其重跑体验和可重复签发性，但它保留旧 immutable receipt、以失败方式阻断新写入，未造成数据丢失或静默放绿；在已有 receipt 可继续被聚合器消费的情况下也不构成个人档 P1，按 P2 处理。

### P2-3：授权字段 malformed 测试没有锁住全部新增校验分支

- 违反条款：spec 7 要求每处新校验具备“改坏即红”测试；不变式是三字段缺失、空值和形状错误在 producer/consumer 发布边界均不能被放行。
- 证据：consumer 的 approver/id/time 校验在 `.github/actions/gate-aggregator/convergence.py:529-534`，ISO 解析异常分支在 `.github/actions/gate-aggregator/convergence.py:716-727`；producer 对 ISO 解析异常的分支在 `.github/actions/gate-disposition/issue_receipt.py:101-109`。当前 consumer 测试 `tests/test_gate_convergence.py:274-294` 只覆盖了缺失 approver、id=0、裸日期；producer 测试 `tests/test_gate_convergence_artifact.py:358-391` 只覆盖空白 approver、id=0、裸日期。没有覆盖缺失 `approver_id`/`approved_at`、带 `T` 但不是 ISO timestamp 的输入（如 `2026-08-30Tnot-a-time`），也没有覆盖 consumer 收到非字符串 approver。
- P1 两问：①真实使用会触发吗？当前实现对这些输入仍会拒绝；问题是对应回归断言缺失，未来改坏 ISO 解析或 parser 默认值时测试可能全绿。②后果能否接受？测试契约不完整会降低 fail-closed 防线的可持续性，但它本身不是当前生产路径上的数据丢失、静默错误或崩溃；按 P2 处理。

### P2-4：`_single_line_reason` 是无第二消费者的单用 helper

- 违反条款：任务卡固定的反熵条款；新增 helper 必须有第二消费者，或有明确的单消费者必要性。
- 证据：`.github/actions/gate-aggregator/convergence.py:674-678` 的 `_single_line_reason` 只有 `.github/actions/gate-aggregator/convergence.py:668` 一个运行时调用方；其逻辑仅为两步空白折叠和截断，可直接留在唯一的 G4 builder 中。没有第二个生产调用方，也没有替代旧路径或消除非法状态的独立机制。
- P1 两问：①真实使用会触发吗？会运行，但 helper 的存在不会改变结果。②后果能否接受？这是可维护性/熵增问题，不触发 personal P1 红线；按 P2 记录，不阻塞本轮 verdict。

## 三个语义轴裁决

| 轴 | 真实路径与证据 | P1 两问 | 本仓裁决 |
|---|---|---|---|
| A. `workflow_call` 的 `triggering_actor` | workflow 在 `.github/workflows/gate-v2-disposition.yml:155` 将 caller run 发起人注入 `DISPOSITION_APPROVER`。审查时 GitHub environment API 的白名单结构化结果显示 `gate-disposition` 存在 `required_reviewers` 保护规则。 | ①会不会触发？会，bot/自动化 caller 会被记录为 bot。②后果能否接受？归因不够准确，但单独不能让 bot 越过 required reviewer；不会把 gate 授权事实静默改写成 bot 已完成人工环境审批。 | 不升 P1；按审计归因 P2 风险观察。spec 明确要求 login 使用 `github.triggering_actor`，不另列违反 spec 的 finding。 |
| B. re-run 的 login/id 分叉 | login 使用 `github.triggering_actor`，id 使用 `github.actor_id`（`.github/workflows/gate-v2-disposition.yml:155-156`）；按任务卡给定语义，重跑可产生不同源的 pair。该 pair 只进入 receipt/G4 展示，不参与 `validate_disposition_receipt` 的授权判定。 | ①会不会触发？会，重跑人和原触发者可不同。②后果能否接受？审计 pair 可能不代表同一人，但不会绕过环境审批或改变 receipt 的 scope/finding/digest 命中语义。 | 不升 P1；按 P2 归因风险观察，并与 P2-2 的重跑幂等问题去重。 |
| C. v1→v2 扫描边界 | `.github/actions/gate-aggregator/aggregate.py:1378-1385` 先只收 `gate-disposition-receipt-v2-`；旧 v1 artifact 在下载前被过滤。v2 前缀制品若 parse 失败，`.github/actions/gate-aggregator/aggregate.py:1392-1404` 不加入 receipt；当前 finding 没有可消费 receipt 时仍保持 primary finding 阻塞。动态扫描探针输出 `downloaded_urls= ['v2']`、`parsed_receipts= 1`。 | ①会不会触发？会存在 TTL 内 v1 残留。②后果能否接受？v1 不会被当成 v2 消费；malformed v2 不会产生 resolved receipt，方向是留红而非放绿。 | 安全，无 P1/P2 finding。`parse_disposition_receipt` 对 v1 kind 输出 `ReceiptValidationError: unexpected disposition receipt kind`，将 v1 schema 改成 v2 kind 后输出 `schema_version_mismatch`；无 v1 兼容分支。 |

## 工具标注 / 本仓判定 / 两问答案

| 证据源 | 工具标注 | 本仓判定 | 两问答案摘要 |
|---|---|---|---|
| OCR 前置扫描 | `status=reviewed`，`coverage=complete`，`findings=[]` | 无 OCR finding；不替代本轮全量审查 | 不适用：工具没有提出缺陷 |
| H0 相关测试 | `367 passed in 21.20s` | 现有行为测试绿，不能抵消 P2-1/P2-2/P2-3 | 测试通过不是 P1 结论；实际渲染/producer 探针另行裁决 |
| pin 检查 | `OK: checked 8 live workflow/action metadata file(s)` | 无 pin 变化问题 | 不适用 |
| GitHub environment 结构化查询 | 存在 `required_reviewers` 规则 | 支持 A 轴“不因 bot actor 直接越过环境审批”的判断；不证明 receipt 记录了真正 reviewer | A：会发生归因偏差；后果不是 P1 |
| base 红验 | `test_gate_disposition_receipt_names_include_epoch_and_audit_digest FAILED`，`AssertionError`，`1 failed, 66 deselected` | 新契约测试不是恒真测试；base 上确实转红 | 反向验证有效，失败类型为断言失败 |

## 红验抽查原文

在 `e0bd30f88e99decb1e35a6601768d41615defbf8` 的临时 detached worktree 中，只拷入当前 `tests/test_gate_v2_contract.py`，运行：

```text
tests/test_gate_v2_contract.py::test_gate_disposition_receipt_names_include_epoch_and_audit_digest FAILED [100%]

>       assert "disposition_receipt_artifact_name" in producer
E       assert 'disposition_receipt_artifact_name' in '#!/usr/bin/env python3\\n... gate-disposition-receipt-v1-...'

tests/test_gate_v2_contract.py:186: AssertionError
======================= 1 failed, 66 deselected in 0.28s =======================
red_exit_code=1
```

失败是断言失败，不是 ImportError、AttributeError 或 SyntaxError；临时 worktree 已回收。当前 H0 同测试属于上面的 367 passed。

## 熵增审查

- v2 schema/kind 常量与 `DispositionReceipt` 三字段：不是熵 +1。schema/kind 被 producer、consumer 和 artifact naming 共同使用；三个字段同时被 producer 写入、parser/validator 读取，是任务目标所需数据。
- producer `_approved_at` 与 consumer `_approved_at_has_time`：代码边界各自 fail-loud/fail-closed，分别服务 producer 和 consumer，不能把 producer 的输入检查当成跨发布边界的消费检查；单消费者理由是两侧必须独立守卫同一不变式，不新增 fallback 或兼容路径。
- `_single_line_reason`：熵 +1，已作为 P2-4 登记；它只有一个运行时调用方。
- `DISPOSITION_REASON_DISPLAY_MAX`：虽然只有显示 builder 读取，但它是 G4 的稳定显示上限，测试也以该契约值断言；不引入运行时状态或新配置，不单列 finding。
- 新增 progress 文档 `docs/sessions/260830-tri-repo-triage/progress/gate-b-progress.md`：仅记录实现里程碑，不被运行时代码读取，也没有新增状态转发路径；属于任务记录，不是运行时熵。
- workflow env 注入和 v2 artifact prefix：分别有 workflow/producer/consumer 的多个消费者或直接生产边界用途，不是单实现接口；未发现新增 fallback、重试或防御式 catch。

## 取证命令摘要

- `ocr-review --repo ... --from dab9c0cbb75ce7aab323397ba54e2e6b8eff63bf --to d90f4c9da92efd51929667599833a2123c42df0f --audience agent --concurrency 4`：reviewed/complete/0 findings。
- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_convergence.py tests/test_gate_convergence_artifact.py tests/test_gate_aggregator.py tests/test_gate_v2_contract.py`：367 passed。
- `python3 scripts/check_pinned_uses.py`：8 个 live workflow/action metadata 全部通过。
- `git diff --check dab9c0cbb75ce7aab323397ba54e2e6b8eff63bf..d90f4c9da92efd51929667599833a2123c42df0f`：无 whitespace error。

verdict: pass
