# gate caller template r1 verdict

## 总评

**PASS**。本轮只审查冻结范围
`ca4a0a9395f52c984eb99c260e538e266657b1f7..339dfd2fee83c7815f0cc01cae4ae4a63fb4a630`；未发现违反本卡不变式的 P1、P2 或 P3 finding。

## 审查范围与风险

- 仓库风险等级：`personal`。
- H0：`339dfd2fee83c7815f0cc01cae4ae4a63fb4a630`，且已核对 `origin/card/gate-20260831-08` 正好指向该 SHA。
- diff 只修改 `templates/caller-gate-disposition.yml` 与 `tests/test_gate_v2_contract.py`，统计为 14 行新增、10 行删除。
- 不审查 callee、gate-hub，未把 Environment Required reviewers 列为建议。

## Findings

无。

P1：无（personal 档红线为数据丢失、静默出错、崩溃）。

P2：无。

P3：无。

`git diff --check` 仅报告测试文件末尾新增空白行（`tests/test_gate_v2_contract.py:1426`）；它不违反本卡任何不变式，也不构成 finding。

## 不变式核对

1. **job 权限与 secrets**：`templates/caller-gate-disposition.yml` 的
   `jobs.disposition.permissions` 恰好为 `actions: write`、`contents: read`、
   `pull-requests: read`，并设置 `secrets: inherit`。契约测试以完整 mapping 和精确
   `inherit` 值断言，未发现权限缺项、额外权限或 secrets 传递缺失。
2. **caller 形状与 pin**：caller 顶层没有 `concurrency`；`disposition` job 没有
   `environment`；`uses` 的 pin 与 `with.gate_ref` 相同，均为
   `__PINNED_GATE_SHA__`。新增测试分别锁定这些条件以及完整 `with` 输入集合。
3. **契约测试约束力**：删除 `secrets: inherit` 会使 `job["secrets"] == "inherit"`
   断言失败；加回 caller 顶层 `concurrency` 会使 `assert "concurrency" not in raw`
   失败。本卡固定条款明确无红验，因此未做改坏注入。
4. **门禁 caller 的禁止 inherit 契约**：`templates/caller-gate-v2.yml` 不在本次
   diff 中；既有 `test_caller_permissions_minimal_and_no_secrets_inherit` 保持原断言，
   定向测试复跑通过。

job 级 `permissions` 与 `secrets: inherit` 均是 reusable workflow caller 的官方支持语法，
与本次跨仓 caller 启动所需的权限边界相符：[GitHub Actions workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。

## OCR 分诊

OCR 返回 `status=reviewed`、`coverage=complete`、`findings=[]`，不是 `skipped`；profile 为
`minimax`，CLI status 为 `complete`。因此本轮可记为“已完成扫描且无 OCR finding”，不是“跳过扫描”。
OCR 的 verifier 字段为 `verify_status=skipped`、`verifier=none`，这是验证器未启用，不改变
OCR 外层 `status=reviewed` 的结论。

按“工具标注 / 本仓判定 / P1 两问”分诊：

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| OCR：无 finding | 无 finding | 无需对严重度重新定级；没有工具意见可触发真实路径，也没有不可接受后果。 |

## 验证证据

- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py`：`68 passed`。
- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`：`709 passed`。
- `python3 scripts/check_pinned_uses.py`：`OK: checked 8 live workflow/action metadata file(s)`。
- OCR：`status=reviewed`、`coverage=complete`、`findings=[]`。

## 熵增审查

没有新增生产抽象、状态、包装层、fallback、重试或防御式 catch。新增的
`expected_permissions` 只是契约测试中的局部期望值；job 级权限、secrets 继承和移除 caller
并发均直接服务本卡已锁定的跨仓 caller 不变式，不产生额外运行时路径。

## 固定条款

执行器必须在本卡分支上小步 commit（署名/归因由 delegate 自动注入），未提交的工作按未完成处理，不得把提交留给验收方。

本卡无红验。

本卡无红验。

禁止顺手新增抽象。
