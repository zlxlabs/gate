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
