## 里程碑 1：聚合器定因与标记

- 当前阶段：implementing
- 本段结论：聚合器按 draft、fork、hosted_runner、review_exempt 顺序识别跳过原因；无法定因的 primary skip 进入 `integration_error / unexpected_primary_skip`。聚合器把单行 V1 标记写入 stdout 和 Step Summary，并把 `skip_reason` 写入 terminal envelope。
- 关键决策与已否决方案：`skip_reason` 存在 `Outcome` 上，供标记、Step Summary 和 terminal envelope 共用。未知原因的真实跳过必须保留 `primary=skipped, skip_reason=null`，这与卡面“primary=executed 等价于 skip_reason=null”的通用断言冲突；本实现遵循负例和实际执行事实。
- 下一步唯一动作：通过 gate workflow 传入 fork 与 classify 原始输出，并增加同源条件契约测试。
