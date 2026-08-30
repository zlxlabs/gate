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
