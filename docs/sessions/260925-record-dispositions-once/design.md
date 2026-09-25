# record_dispositions 单次计算

## 现场证据

在本次改动前，`aggregate.evaluate()` 先调用 `record_dispositions()`，并传入
`legacy_raw_audit_digest`；其结果写入 `outcome.disposition_audit`，供 pass/fail
分类使用。随后它调用 `evaluate_round()`，后者又独立调用 `record_dispositions()`，
但没有旧摘要参数，并用另一份结果计算 `remaining_p1_ids` 和 clean streak。

旧摘要路径可以让同一张回执在两次计算中得到不同结果：相同 schema v3 回执的
`audit_digest` 命中 `legacy_raw_audit_digest` 时，外层计算会记录该处置；不传旧摘要的
独立计算则会拒绝该回执。改动前新增的调用计数测试实际得到 2，证明两处调用确实发生。

## 收敛后的调用关系

```text
aggregate.evaluate()
  ├─ record_dispositions(..., legacy_raw_audit_digest=...)
  │    └─ outcome.disposition_audit
  └─ evaluate_round(..., disposition_audit=同一对象)
       └─ remaining_p1_ids → clean streak

replay_receipts()
  └─ evaluate_round(..., 不传 disposition_audit)
       └─ record_dispositions(...)
```

`evaluate_round()` 新增的 `disposition_audit` 参数可选。汇总器传入已经算好的对象，
因此 pass/fail 分类与 clean streak 消费同一计算结果；没有该对象的重放调用继续在
`evaluate_round()` 内部计算，调用方式和语义保持原样。回执 schema 版本校验没有变化。

## 验收依据

- `tests/test_gate_aggregator.py::test_evaluate_records_dispositions_once_for_active_p1`
  用 mock 断言一次聚合只调用一次 `record_dispositions()`，并覆盖旧摘要命中时
  disposition 审计结果与 clean streak 一致。
- `tests/test_gate_aggregator.py::test_evaluate_round_reuses_disposition_audit_with_legacy_digest`
  构造旧摘要命中的同一张回执，证明预先计算的 `remaining_p1_ids` 被 round decision
  原样使用；不传旧摘要的独立计算结果作为对照。
- 回执字段表中 `finding_id` 必需，`finding_key` 可选且缺失时取空字符串；字段细节见
  [受控出口设计](../260925-disposition-exit/design.md)。
