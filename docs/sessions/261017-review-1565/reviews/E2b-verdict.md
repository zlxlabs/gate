# E2b 独立审查结论

**结论：通过（无 finding）**。审查固定范围 `82c2a68231569e2774cddc052ddfccf5baaf9f78..094d1e44eb88a4a5ad0882fd99ab267cc0f89197`，风险档为 personal。改动只新增 terminal 中的 `convergence.fail_count` 与 `convergence.reconsider`，没有改写现有 required gate 判定路径。

failure-visibility: clean

## 不变式核验

- 不变式 1：[`_compute_pr_primary_convergence`](../../../../.github/actions/gate-aggregator/aggregate.py#L2252) 按 `run_id` 分组，head SHA 不作为分区；只在有实际 `pass`/`fail` verdict 的 attempt 中选最高 `run_attempt`，更晚的 unavailable 不会抹去已验证 verdict。它不读取 `gate_result` 作为 primary verdict。对应覆盖：[`test_cli_convergence_counts_latest_actual_attempt_per_run_and_known_skips`](../../../../tests/test_gate_aggregator.py#L4113)，含跨 head、重复 attempt 和已知 skip。
- 不变式 2：[`_load_pr_convergence_history`](../../../../.github/actions/gate-aggregator/aggregate.py#L2708) 保存 GitHub 实际枚举的 `target_run_ids`。已枚举但未读到 terminal 的非当前 run 会使 `complete` 变为 false；legacy terminal 无 primary verdict、无历史或历史不完整也不能被当作零。`_terminal_row` 只把带合法 skip reason 且无可用 audit 的 skip 标为已知零轮。对应覆盖：[`test_cli_convergence_keeps_unknown_history_and_verified_lower_bound`](../../../../tests/test_gate_aggregator.py#L4143) 与 [`test_main_terminal_convergence_reads_target_run_gap_and_quality_fail_primary_pass`](../../../../tests/test_gate_aggregator.py#L4153)。
- 不变式 3：当前已验证 fail 且至少三次 distinct run fail 时，即使历史不完整也给 `reconsider=true`；当前 pass 或已知 skip 为 false；其他历史不足阈值的未知情形保留 null。上述两组测试覆盖完整历史、缺目标、legacy verdict 缺失和当前 pass。
- 不变式 4：新逻辑复用既有 history load；diff 没有新增 API 调用、重试或 required gate 阻断条件。main 集成测试断言 quality failure + primary pass 仍为 exit 1 / `gate_result=fail`，同时 `primary_verdict=pass`、convergence 只报告 unknown 与 `reconsider=false`。
- 不变式 5：terminal 既有字段仍由 `build_terminal_envelope` 原样生成；仅在有 convergence 结果时添加新对象。`_finish` 写入实际 terminal JSON 文件并按原 `outcome.ok` 返回退出码；main 集成测试读取该 JSON 文件字节解析并断言字段及退出码。

## Finding

无。没有需要说明违反不变式、真实复现或阻断性的 finding。

## 验证与未知

- `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py`：通过，`407 passed in 14.69s`。
- `git diff --check 82c2a68231569e2774cddc052ddfccf5baaf9f78..094d1e44eb88a4a5ad0882fd99ab267cc0f89197`：通过，无输出。
- OCR 前置扫描：skipped。依照任务卡“不请求真实模型”，未调用已安装的 `ocr-review`。
- 基线 API 在派发时不可用；**继承红：未能判定**。本轮指定测试无新红。
- pickup 巡检原文：`open issue 总数：1；存活探针不可用；memory 候审 1 条（死指针 0 / 超龄 0 / 探针失败 1 / 探针出错 0）`。无匹配交接单。最新 runtime 的 pending 检查报告 ledger backlog 查询失败（exit 2）；PR #332 已由作者认领。该现场检查不影响固定 diff 审查结论。
