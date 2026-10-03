# PR #280 独立审查 verdict

审查对象固定为 `98a3ed241294a0671ac95bee7e0b987147e7adf4..ad80149d23c7c70d86ebac396ebdb05a584ea33f`，risk-tier `personal`。结论：2 条 P2，无 P1；当前早拒行为符合拒绝域要求，但提示文案违反 deferred 提示契约，且 Silo/audit 未触达测试没有锁住 workflow 后续步骤的控制流。

## OCR 前置扫描

```json
{"tri_state":"reviewed","reason":"primary_selected","profile":"minimax","findings":5,"verified":5,"confirmed":4,"refuted":1}
```

独立核验后，OCR 的多行 kind 命令注入候选不成立：新增 `echo` 写入 stderr，而 GitHub Actions 将 workflow commands 经 stdout 发送给 runner（[官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands)）。其余无 spec 违例的维护性候选不列为 finding。

## Findings

### P2 — 错误提示暗示 deferred 可凭 tracking issue 继续

- 位置：`.github/workflows/gate-v2-disposition.yml:181`
- Spec：错误提示须真实可操作，且不得让调用方误以为补 `tracking_issue` 能放行被禁止的 deferred。
- 复现：用真实 workflow 输入映射及 run 块运行 `legacy-empty-evidence`，预检返回 1，提示包含 “a deferred needs tracking_issue”；同一真实 receipt producer 对 `deferred` + `#12` 的差分结果为预检 0、producer 1，错误为 `deferred_not_allowed_for_tier`。权威拒绝位于 `.github/actions/gate-disposition/issue_receipt.py:253-262`。因此旧 caller 按通用提示暴露并转发 deferred 与 tracking 后，仍会在后续失败。
- 建议文案说明 deferred 无论 tracking 值如何都由权威 validator 拒绝；不要把它列作可通过的证据路径。
- P1 问1（真实使用会触发吗）：空证据输入可稳定触发该文案；本次没有运行生产 workflow，不能量频率。
- P1 问2（触发后果能否接受）：可能误导重试并多走后续读取步骤，但 receipt 仍被拒绝，没有数据丢失、错误放行或崩溃，故判 P2。

### P2 — “未触达 Silo/audit”断言没有执行后续 workflow 控制流

- 位置：`tests/test_gate_v2_contract.py:1102-1123`；顺序断言在 `:1045-1053`。
- Spec：早拒须在 Silo/canonical audit 前终止，并以真实控制流及访问记录证明；还需确认拒绝来自目标机制而非 fixture 错误。
- 复现：`test_disposition_preflight_failure_never_reaches_silo_or_the_audit` 只调用 `_run_preflight` 执行预检步骤的 shell body。`gh`、`curl`、`aws`、`jq`、`git` 与 Silo recorder 因而没有机会被后续步骤调用，`record` 不存在不能证明 job 调度跳过了 Silo/audit。现有顺序检查确认预检为首步并找到后续步骤，但没有锁定它们的条件。
- 当前 workflow 本身的控制流安全：前 6 步为默认 `success()`；最终上传虽为 `always()`，仍要求 `steps.disposition.outcome == 'success'`。缺口在测试覆盖，不是当前路径已经访问了 Silo。
- P1 问1（真实使用会触发吗）：当前 workflow 不会触发；需后续把访问步骤改成失败后仍执行才触发。
- P1 问2（触发后果能否接受）：可能多做 Silo/audit 读取，但不会因此生成被拒 receipt 或丢失数据，故判 P2。

## 不变式核对与降层三问

- 真实输入差分：旧五输入/空证据、显式缺证据、未知 kind、空白 kind 都是预检与 receipt producer 同为拒绝；合法反证配空 kind 默认值或显式 `false-positive` 均两者成功；带 tracking 的 `deferred` 通过预检后仍由 receipt producer 拒绝。空 kind 的默认语义一致；未知/空白 kind 都按精确字符串拒绝，预检没有新增 strip 或类型接受域。
- Diff 仅改 `.github/workflows/gate-v2-disposition.yml` 与 `tests/test_gate_v2_contract.py`；模板、身份/证据 validator、service/origin 子目录 checkout 链未改。没有按 caller 来源或版本推断分支。
- receipt 成功前的动作：checkout 与 Silo/audit 获取是前置读取；immutable receipt 仅在 producer 完成校验后写本地文件，Silo 上传又受 receipt step 成功条件保护。
- 身份来源：既有 receipt step 从本 workflow job 的 `github.actor` / `github.actor_id` 注入 approver；本次 diff 未改身份链。
- 保护覆盖行为而非仅写入：预检失败会阻止后续默认成功条件步骤；上述访问记录测试尚未证明调度边界。
- 未运行生产 workflow、未读取真实 Silo/canonical audit、未发送处置回执；测试使用确定性 audit fixture 与真实 receipt producer。

## 验证

- `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_source.py`：`305 passed in 107.04s`。
- base 红验：在 base `98a3ed2` 临时 worktree 仅拷入新增首步顺序断言；断言以 `Checkout disposition producer != Preflight disposition evidence inputs` 失败（1 failed），不是导入失败。临时 worktree 已由 scratch-worktree 工具清除，红验 venv 位于临时树；主仓及审查树 shebang 检查无 `/tmp` 污染。
- `git diff --check`：通过；固定范围 diff 检查也通过。
- 主干基线在派发时不可用（`gh api request failed`）；继承红未能判定。本地两文件测试结果不等同于生产 CI。

failure-visibility: p2-only
