# gate #199 根治进度（card: quality 自带 caller_checks 证据）

## 里程碑 1 — 汇总器后端：证据判据 + quality_infra + CLI 参数

- 当前阶段：repairing（汇总器后端完成，工作流记录步待做）
- 本段结论：`evaluate()` 在 `quality=failure` 前提下只认 `caller_checks=failed` 才判代码问题（`ci_failure`/`quality_failure`/`fail`），其余（含空值缺席）一律 `review_unavailable`/`quality_infra`/`unavailable`；`preflight_result=failure` 单列一相仍走 `fail`。面板桶自动落「修基础设施」（`PANEL_BUCKET_BY_GATE_RESULT` 按 `gate_result` 映射，不动四桶）。
- 关键决策与已否决方案：`quality_infra` 挂靠 `review_unavailable`（借现有「修基础设施」桶，不新增 classification）；`Diff coverage advisory` 不计入业务相（advisory 性质且 `continue-on-error`，用 outcome 会把 advisory 失败算成代码红）；未知非空 `caller_checks` 按非法输入 fail-closed（无效输入 Outcome，不进任何桶）。
- 下一步唯一动作：给 quality job 业务步骤补 `id:` 并加 `if: always()` 记录步与 `outputs.caller_checks` 接线。

## 里程碑 2 — 记录步：业务步 id + if:always() 证据 + 两处调用点接线

- 当前阶段：repairing（生产接线完成，契约/矩阵测试待做）
- 本段结论：6 个业务步补齐 `id:`（`run-quality`/`lint-format`/`duplicate-check`/`dependency-direction`/`run-tests`，`install` 已有），`pr-size-preflight` 单列 `id:`；新增 `Record caller checks outcome`（`if: always()`，只读原生 `outcome`，`failed>passed>not_started` 三值输出 + `preflight_result` 透传）；`quality.outputs` 与 gate job 两处调用点（aggregate + publish-only）同步接线完毕。
- 关键决策与已否决方案：`Diff coverage advisory` 不计入（advisory + `continue-on-error`，计入会把建议失败算成代码红）；`skipped`/空 outcome 中性跳过（entry/legacy 双模下对方阵营步骤恒为 skipped，不影响三值）；载体用 env（复用 `QUALITY_RESULT` 先例模式，不自创花样）。
- 下一步唯一动作：写跨发布边界契约测试（读真实 YAML 断言接线不断链）。

## 里程碑 3 — 契约测试：真实 YAML 接线不断链

- 当前阶段：repairing（接线有契约锁死，判定矩阵待补）
- 本段结论：5 个契约测试全部读真实 `gate-v2.yml`：业务步 `id:` 齐备、记录步 `if: always()` 且逐 id 引用 `steps.<id>.outcome`、三值输出 `GITHUB_OUTPUT`、`quality.outputs` 与 gate job 两处调用点 env/argv 接线、证据路径无日志关键字匹配。
- 关键决策与已否决方案：无（纯接线断言；publish-only 调用点同样传参——它虽不重算 verdict，接线一致才能保证未来改动不分叉）。
- 下一步唯一动作：补判定表矩阵测试（三行逐格断言 classification/reason/gate_result 且 ok 为 False）。

## 里程碑 4 — 判定矩阵：三行逐格 + 面板 + 非法输入 + CLI 到达性

- 当前阶段：repairing（实现与测试齐备，待红验与全量验收）
- 本段结论：判定表三行逐行有测试（`failed→fail`；`passed/not_started/空→unavailable`；`preflight failed→fail`），每格 `ok is False`；`quality_infra` 面板渲染「修基础设施」且不含「要修代码」；未知证据值 fail-closed；`cancelled` 腿不受证据影响；CLI  flags→判据到达性有端到端测试。
- 关键决策与已否决方案：止血 8 测试中 7 个保持原期望（其中 draft/hosted 两例补 `caller_checks="failed"` 显式表达「真红」），仅 `test_issue199_quality_failure_with_passing_primary_stays_fail_known_residual` 按其自带注释「根治卡再翻」翻转为 `unavailable`（更名明示）；矩阵 `kwargs0` 与可见轴旧断言同理补证据。
- 下一步唯一动作：红验（改坏记录步/判据确认新断言变红）+ 全量 Verify-Command + 写报告。
