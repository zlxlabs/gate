# gate-B：disposition receipt v2 授权三字段 + G4 可读行

## 2026-08-30 里程碑 ① convergence v2 schema 与校验

- 当前阶段：implementing / milestone ① consumption schema
- 本段结论：`DispositionReceipt` 升 `schema_version=2`，补 `approver` / `approver_id` / `approved_at`；校验按缺失/空/空白 login、非正整数 id、无时刻 `approved_at` 记 `malformed_receipt`，v1 记 `schema_version_mismatch`。artifact 名与扫描前缀改为 `gate-disposition-receipt-v2-`。
- 关键决策与已否决方案：`kind` 只接受 v2 字符串，v1 kind 在 parse 失败（无 v1 兼容分支）；v1 拒绝样例用 v2 kind + `schema_version=1` 走到 `schema_version_mismatch`。`approved_at` 的含 `T` 判定单独成行，供后续红验。生产端仍写 v1，本段测试用 overlay 过消费链。
- 下一步唯一动作：生产端 `issue_receipt.py` 写入三字段，workflow 用 `github.triggering_actor` / `github.actor_id` / 签发时刻注入，不经 inputs。

## 2026-08-30 里程碑 ② 生产端签发三字段

- 当前阶段：implementing / milestone ② producer + workflow env
- 本段结论：`issue_receipt.py` 经 argv/env 写入 `approver`/`approver_id`/`approved_at`，kind 与 artifact 名走消费端 v2 常量。workflow 从 `github.triggering_actor` 与 `github.actor_id` 注入，签发时刻由 job 内 `date -u` 一次生成后传入 `--approved-at`，inputs 集合不变。
- 关键决策与已否决方案：`approver_id` 用 `github.actor_id` 而非 API 反查；与 `triggering_actor` 在首次 dispatch 同源，Re-run jobs 时 actor 可能换成重跑人（边界写报告）。`approved_at` 不用进程内 `now()`，避免同目录二次签发因时间戳不同撞 `_write_immutable`。
- 下一步唯一动作：扩 `required_disposition_lines` 为 G4 可读行，三处发布面仍只调该函数。

## 2026-08-30 里程碑 ③ G4 resolved 行

- 当前阶段：implementing / milestone ③ G4 lines
- 本段结论：`required_disposition_lines` 现产出 `finding <id> (false-positive, approved by <approver>) resolved by receipt <name>: <reason>`；reason 折叠空白并截到 500 字。Step Summary / `::notice::` / `render_summary`（PR 评论同源）都读 `outcome.resolved_findings`，该列表只由这一函数填充。
- 关键决策与已否决方案：envelope 的 `resolved_findings` 仍保持 `{finding_id, receipt}` 机器结构，留给 G3 ledger 投影，不把 G4 字符串塞进 envelope（与设计「三处发布面」字面冲突，写报告）。显示上限 500 取自仓内既有人类可读截断量级（`MAX_HISTORY_WARNING_CHARS`），receipt 存储的 reason 本身不截。
- 下一步唯一动作：补 producer→parse 跨边界样例，收紧生产端字段集断言，跑全量测试。

## 2026-08-30 里程碑 ④ 跨边界样例与收口

- 当前阶段：implementing / milestone ④ producer contract + 全量验证
- 本段结论：`issue()` 与 `main` 写出的 payload 直接喂 `parse_disposition_receipt` 后可消费；生产端对空白 approver / 非正 id / 裸日期 fail-loud。全量 pytest 与 `check_pinned_uses.py` 收口。
- 关键决策与已否决方案：无。
- 下一步唯一动作：主脑走本地 review 循环；合并须用 merge commit（执行器不推不合并）。

## 2026-08-30 R1 P2 四条收口

- 当前阶段：repairing / R1 P2-1..P2-4
- 本段结论：状态面板从 terminal 行上的 `resolved_findings`（同源 `required_disposition_lines`）渲染 Resolved 段。同名 receipt 仅授权三字段不同时 `_write_immutable` no-op 保留原件。补缺失 id/时刻、非法 `T` 时间、非字符串 approver 负例。删掉单用 `_single_line_reason`。
- 关键决策与已否决方案：terminal 只在有 resolved 行时才写该字段，避免改无 receipt 的 golden 字节。幂等比较是去掉三字段后的 canonical JSON，不是忽略任意差异。不把 G4 字符串塞进 convergence envelope 机器结构。
- 下一步唯一动作：P2-2 红验后跑全量 pytest 与 pin 检查。

## 2026-08-30 R1 P2 验证收口

- 当前阶段：done / R1 P2-1..P2-4
- 本段结论：全量 pytest 685 passed、`check_pinned_uses.py` 退出码 0。P2-2 红验把 `if same_body:` 改成 `if False and same_body:` 后，`test_disposition_producer_same_params_new_approved_at_is_noop` 以 `AssertionError: assert 1 == 0` 转红，已只还原该行。
- 关键决策与已否决方案：无新增。幂等只认去掉 `approver`/`approver_id`/`approved_at` 后的 canonical JSON；reason 等其余差异仍 conflict。
- 下一步唯一动作：主脑本地 review；合并须用 merge commit（执行器不推不合并）。
