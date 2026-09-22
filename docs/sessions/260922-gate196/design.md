# gate#196 disposition receipt de-duplication

## 目标

修复 `record_dispositions()` 对相同非法 disposition receipt 的错误去重：每一次非法输入都必须保留实际校验结果（`valid=False`、`active=False`、原拒绝原因），且不得进入记录集合或污染 finding 投影。

## 非目标

- 不改变合法 receipt 的既有幂等语义：相同合法 payload 最多记录一次，后续状态为 `duplicate_receipt_noop`。
- 不改变 primary P1 保留、receipt 仅作记录声明、过期或非 active receipt 的既有判定。
- 不改 workflow、镜像、registry、生产环境，也不处理 gate#197。

## 方案

先调用现有 `validate_disposition_receipt()`，只有校验结果同时为 `valid=True` 且 `active=True` 的 receipt 才参与 `seen_payloads` 去重。非法、过期或 inactive receipt 每次都保留自己的校验状态并走既有 rejected 路径；合法重复仍返回 `duplicate_receipt_noop`，不重复写入 `recorded_receipts`。

## 不变式

1. 每个 receipt 都对应一个真实校验状态；重复非法 payload 不得被伪装成 active 合法状态。
2. `recorded_receipts` 与 `recorded_finding_ids` 只包含当前校验为 valid 且 active 的 receipt，且合法 payload 至多一次。
3. `primary_p1_ids` 始终保留输入 primary 投影；任何 disposition receipt 都不能移除或替换它。
4. receipt 顺序不影响合法/非法混合输入的最终记录与拒绝结果；过期或 inactive receipt 不因重复去重而被激活。

## 验收

- 在旧实现上，新增相同非法 receipt 重复测试先红；修复后定向收敛测试全绿。
- 覆盖合法重复、非法重复、非法/合法同 finding 的两种顺序、非法不污染记录投影、expired/inactive receipt 不被激活。
- 运行仓库规定的全量 pytest 与 pin 检查；提交前执行 OCR review 并如实记录状态。
