# Gate #810: disposition 只记账

用户裁决锁定：disposition receipt 只记录提交者对 finding 的 claim，不影响 gate 结论、primary P1 集合或 clean streak。`triggering_actor` 只标识账号，不能证明是人工审批；不新增审批身份或放行路径。

既有 clean-streak 设计、实现边界和非本卡状态机见 [clean-streak-convergence.md](../../../design/clean-streak-convergence.md)。本卡只改 disposition 语义：保留 producer bytes/schema、目标与 canonical audit 绑定校验、真实 primary/canonical audit fail-closed；receipt 的缺失、有效、重复、stale、invalid、读取失败均不得改变 gate state。读取失败只给有界诊断。

| receipt 状态 | primary 有 P1 | primary 无 P1 | 锁定检测点 |
|---|---|---|---|
| 无 | 保留 P1、失败且不增 streak | gate 不变 | convergence 矩阵、aggregate |
| 有效 claim | 保留 P1、失败且不增 streak；只记录 claim/actor | gate 不变，只记账 | producer→aggregate→terminal→ledger E2E |
| 重复 | 保留 P1、失败且不增 streak | gate 不变 | convergence 矩阵 |
| stale | 保留 P1、失败且不增 streak | gate 不变 | convergence 矩阵 |
| invalid | 保留 P1、失败且不增 streak | gate 不变 | convergence 矩阵 |
| 读取失败 | 保留 P1、失败且不增 streak | gate 不变并可见诊断 | aggregator 读取失败测试 |

基线反例已验证：在 `9c703093ee3d1491a94d5d0fbebbec16e01a9181`，真实 P1 + 有效 receipt 使 gate 错误地 pass；测试以“期望 fail、实际 pass”红灯。当前测试用真实 producer 子进程写出的 canonical bytes 锁定跨进程契约。

## 为何重开（2026-09-25）

gate-hub#1069 记录了生产现场：run `36004614034` 已签出回执，同一 head 重跑 run `36003941449` attempt 2 仍为 `code_fail / primary_findings`，并出现回执扫描 `URLError`。这证明 record-only 出口无法解决有证据误报和已开单跟踪项造成的持续阻断。

owner 于 2026-09-25 裁决重开 gate#226 的 gate 侧实现：接受 `triggering_actor` 不能证明操作者身份，不增加签名、nonce、撤销、Environment 审批或人机区分；改由反证或同仓跟踪 issue 约束，并在 terminal/ledger 留痕。唯一受控出口为 `gate-disposition` 两种逐 finding 回执：`false-positive` 必须有反证，`deferred` 必须有格式合法的同仓 issue 引用且仅允许 `personal`/`internal` tier。完整字段、消费规则和拒绝矩阵见 `docs/sessions/260925-disposition-exit/design.md`。
