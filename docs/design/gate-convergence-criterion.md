# gate-v2 并发契约（两把锁）

本文件是 `zlxlabs/gate` 里 pinned reusable `.github/workflows/gate-v2.yml`
的并发权威。完整收敛判据设计仍在 gate-hub；本仓原先没有同名文件。规划卡引用的
「pinned reusable 层顶层 `cancel-in-progress: true`」段落（hub 仓约 280–288 行）
与现网实现相反，**以本节两把锁为准，不许再要求顶层一把锁。**

## 为什么不是顶层一把锁

顶层 `cancel-in-progress: true` 会取消同一组里整个旧 run，包括正在写 PR 状态条
的 `gate` 和整仓排队写账本的 `ledger`。v2 拆掉顶层锁，就是为了保住这两处写入。
该方案已否决，不得恢复。

## 两把锁（现网）

顶层无 `concurrency` 键（`tests/test_gate_shadow_v2_contract.py` 锁死）。锁只在
job 上，且贵任务与写入任务拆开：

```yaml
# 贵任务：同 PR 新 head 取消旧 job。两组必须分开，否则同一 run 内
# quality 与 primary 会互相排队或互杀。非 PR 上下文用 run_id 兜底。
# quality:
concurrency:
  group: gate-required-v2-quality-${{ github.repository_id }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: true
# primary: 同构，前缀改为 gate-required-v2-primary-

# 写入任务：排队写完，不取消。组名、cancel false 均不动。
# gate:
concurrency:
  group: gate-required-v2-panel-${{ github.repository_id }}-${{ github.event.pull_request.number }}
  cancel-in-progress: false
# ledger:
concurrency:
  group: gate-required-v2-ledger-${{ github.repository_id }}
  cancel-in-progress: false
```

组名必含工作流身份前缀 + `github.repository_id`（GitHub 并发组是整仓命名空间，
漏工作流身份会与其他 workflow 互杀，见 zlxlabs/agent-config#68）。新组名不得以
`gate-required-v2-panel-`、`gate-required-v2-ledger-`、`gate-shadow-v2-` 为前缀，
且不含子串 `shadow`。

OCR / `resolve_advisory` / `notify` 无 concurrency（第一版范围）。

契约测试：`tests/test_gate_v2_contract.py` 的
`test_required_v2_has_no_workflow_level_concurrency`、
`test_quality_and_primary_have_independent_cancel_true_pr_locks`、
`test_gate_and_ledger_writer_locks_remain_cancel_false`、
`test_non_writer_non_expensive_jobs_have_no_concurrency`。

Job 级取消只停旧 run 里同组的那个 job，不是整个旧 run；运行时语义由真实 PR
连推验证，静态契约锁不住。

## 已知限制

被 supersede 的旧 run 中 quality/primary 被取消后，旧 run 的 `gate` 仍会发布
fail-closed 状态条，但 `ledger` 因缺上游 artifact 会硬失败——旧 run 可能
「有 panel、无 ledger 记录」。required 结论由新 run 决定，不受影响。
