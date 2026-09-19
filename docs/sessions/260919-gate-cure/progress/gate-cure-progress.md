# gate #199 根治进度（card: quality 自带 caller_checks 证据）

## 里程碑 1 — 汇总器后端：证据判据 + quality_infra + CLI 参数

- 当前阶段：repairing（汇总器后端完成，工作流记录步待做）
- 本段结论：`evaluate()` 在 `quality=failure` 前提下只认 `caller_checks=failed` 才判代码问题（`ci_failure`/`quality_failure`/`fail`），其余（含空值缺席）一律 `review_unavailable`/`quality_infra`/`unavailable`；`preflight_result=failure` 单列一相仍走 `fail`。面板桶自动落「修基础设施」（`PANEL_BUCKET_BY_GATE_RESULT` 按 `gate_result` 映射，不动四桶）。
- 关键决策与已否决方案：`quality_infra` 挂靠 `review_unavailable`（借现有「修基础设施」桶，不新增 classification）；`Diff coverage advisory` 不计入业务相（advisory 性质且 `continue-on-error`，用 outcome 会把 advisory 失败算成代码红）；未知非空 `caller_checks` 按非法输入 fail-closed（无效输入 Outcome，不进任何桶）。
- 下一步唯一动作：给 quality job 业务步骤补 `id:` 并加 `if: always()` 记录步与 `outputs.caller_checks` 接线。
