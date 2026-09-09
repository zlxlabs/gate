# gate#131 进度

- 2026-09-10：在 `c13c92a` 基线加入 `measured` P1 反证红验。
- 基线验证：`tests/test_gate_aggregator.py::test_measured_p1_receipt_cannot_resolve_required_gate` 失败，
  现行为错误地将 required gate 置为 `pass`（`AssertionError`，不是导入错误）。
- 下一步：把 canonical finding 的 `id/severity/trigger_kind` 贯穿投影、receipt、digest、消费和 replay，
  并补齐 inferred/measured/unmeasurable/缺失/未知矩阵及真实 producer 边界测试。
