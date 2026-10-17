# 安全阶段账本消费独立审查

failure-visibility: clean

## 结论

- Verdict：通过；阻断交付：否。
- 固定审查范围：`15f984a81ed184e64fb36d99f171770bf4b05a80..6d9233c896c001278c8a6b06bf37bd8f90f2ab08`。
- 未发现违反任务不变式的 finding。

## Findings

| Finding | 违反的不变式 | 真实触发 | 后果 | 阻断交付 |
| --- | --- | --- | --- | --- |
| 无 | 无 | 未观察到 | 无 | 否 |

## 证据

- gate-hub 固定提交 `80fab6aef993573ef738b771dd8a634fd7be6cc8` 的 `scripts/review/review-primary` 分别给 primary/security attempts 标注 stage，并写入 canonical audit（约 1440、1470、1550 行）；同提交 `tests/test_review_primary.py::test_sensitive_security_chain_is_stage_bound_and_c6_gated` 走真实 CLI 与 mock reviewer，断言写出的 audit 含两个 stage。
- 从该 producer 测试生成的 audit 与新增 fixture 比对：attempt stage 和字段集合一致；security finding 对象逐字段相等；producer 的 `repository_id/repository/pr/run_id/run_attempt` 与 fixture 相符。
- 消费侧 `.github/actions/review-ledger/build_ledger.py:66-87` 只在输入 attempt 带 `review_stage` 时复制该字段；`:187` 将 audit 的 `result` 原样写入 ledger；`:205-297` 按仓库、PR、run、head 及显式 expected identity 校验 audit 身份。
- 新增 CLI 用例 `tests/test_review_ledger.py::test_live_record_cli_preserves_primary_and_security_review_stages` 断言 ledger 字节含 primary/security stage，并验证旧 audit 的 attempt 输出仍无 `review_stage`。实际 CLI 输出的 primary identity 与 fixture 一致，finding 对象也与 producer fixture 完全相等。

## 验证与限制

- 固定 consumer 提交的上述定向 CLI 用例：`1 passed`；旧输入也在同一用例中验证。
- gate-hub 固定提交的 mock producer 安全阶段用例：`4 passed, 113 deselected`，覆盖 clean、block、bad quote、unavailable。fixture 中的 finding 与实际 producer 输出相同。
- producer 验证在 `/tmp` 的固定提交归档上运行；归档没有 `.git`，按 producer 入口要求在临时快照提供 `REGISTRY_COMMIT=80fab6aef993573ef738b771dd8a634fd7be6cc8` 后通过。producer 原工作树未改动；未调用模型或 S3。
- 固定 head 不含 `scripts/review/extract-failure-visibility.sh`；已用精确匹配确认机读行唯一且合法。
- 未跑全量测试；按任务说明未重复 OCR。派发时主干基线查询失败，继承红无法判定；本次未运行 CI。
