# Canonical clean-streak convergence 设计

> 状态：planning；本卡只新增本文档，不修改生产代码、测试或 `.github/`。
>
> 目标：把 gate#35 的三增量收敛为可直接拆实现卡的契约，并吸收 gate-hub#335 的 protected、digest-bound false-positive disposition 规格。
>
> 实现归属：`zlxlabs/gate` 的 reusable workflow / aggregator 路径。gate-hub 只提供 canonical primary policy、audit 数据和受保护控制面的输入，不另造 evaluator。

## 1. 结论与边界

`gate/gate` 的放行条件是：同一个 PR generation、同一个 evaluation scope 内，连续达到策略要求的 `N` 个 eligible clean round。一个 round 只来自当前 run 选出的 canonical primary audit；quality、OCR、shadow、review ledger、普通评论和本地投影都不能制造 round。

本设计把“clean”定义为：

```text
canonical primary audit 已通过 schema / identity / scope 校验
且 verdict ∈ {pass, fail}
且当前 P1 finding 集合减去本轮有效 false-positive disposition 后为空
```

`unavailable`、`not_expected`、reviewer 自报 `waived`、缺 artifact、digest 不匹配和无法验证的 waiver 都不是 clean。它们分别走不可用、预期跳过或 fail-closed/manual-required，不得借“没有看到 finding”放行。

本设计刻意不做跨轮 finding 身份推断。`finding_id` 只用于当前 canonical audit 内精确匹配授权 disposition；不保存前轮 finding 集合、lineage、文本 fingerprint 或行号。这样同一条 finding 反复出现、换 reviewer、换行号或换文本，都不会被误当作 clean。

### 1.1 当前基线（必须保留的事实）

| 现状 | 证据位置 | 对本设计的影响 |
|---|---|---|
| `aggregate.py:evaluate()` 是一次输入、一次输出的纯单轮判定，没有 streak/generation/cursor | `.github/actions/gate-aggregator/aggregate.py:299-425` | 增量 1 在 aggregator 侧增加 canonical reducer；不能把 advisory 结果冒充 state。 |
| aggregator 当前只验证 audit 的 identity quintuple，并拒绝不受支持的 `not_expected`/`waived` | `.github/actions/gate-aggregator/aggregate.py:87-108,207-265,371-414` | 新 evaluator 复用 canonical audit 校验；waiver 不是 reviewer verdict。 |
| canonical audit artifact 按 `repository_id/head_sha/run_id/run_attempt` 命名，gate 会选择不晚于当前 attempt 的最高来源 attempt | `.github/workflows/gate-v2.yml:596-669` | 读取必须同时保留 `source_attempt`、artifact id、audit digest；不能只看文件名。 |
| v2 `gate` job 没有 PR 级 concurrency；`ledger` 有独立 repository 级串行队列 | `.github/workflows/gate-v2.yml:560-577,728-735`；`tests/test_gate_v2_contract.py:test_concurrency_group_is_required_v2_and_defined_once_at_workflow_level` | 不能假定现有 ledger 锁保护 convergence；增量 3 必须显式验证 writer/receipt 语义。 |
| review ledger 会读取历史 artifact，并用 sticky comment 保存 epoch 游标，但读取失败目前 warning 后继续 | `.github/actions/review-ledger/build_ledger.py:556-603,692-730` | ledger 只能做观测投影；correctness replay 不能依赖它的 fail-open 路径。 |
| ledger 目前把 PR 评论中的 `Codex finding disposition` 解析进 `finding_dispositions` | `.github/actions/review-ledger/build_ledger.py:73-85,470-496` | 旧评论格式可以保留作审计输入，但不能直接解除 required 红；必须升级为受保护 receipt。 |
| 终态 envelope 是跨 job artifact，已有 schema/可见性测试 | `.github/actions/gate-aggregator/aggregate.py:189-205`；`tests/test_gate_aggregator.py:test_terminal_envelope_bytes_unchanged_by_rendering_work` | 新 decision 可扩展 versioned envelope，但不能用评论替代它或静默改变旧字段。 |

### 1.2 固定术语和唯一 source of truth

- `scope`：本次判定的完整身份，至少包括 `repository_id`、`pr_number`、`base_sha`、`head_sha`、`diff_digest`、`policy_version`、`policy_digest`、`tier`、`effective_tier`、`infra_classifier_version`、`infra_diff`、`caller_sha`、`reusable_workflow_sha`。
- `epoch`：`sha256(canonical_json(scope))`。scope 中任何字段变化都生成新 epoch；它是 generation 的不可变守卫值，不是评论游标。
- `audit_digest`：canonical primary audit 文件原始 UTF-8 字节的 SHA-256；先校验 JSON/schema，再将该 digest 绑定到 receipt 和 disposition。不得用 findings 子集 digest 代替。
- `processing_key`：`(repository_id, pr_number, run_id, run_attempt)`，同一 key 的重放必须幂等。
- `round_key`：`(epoch, run_id, audit_digest)`。同一 canonical audit 被 `rerun --failed` 的另一 attempt 重新消费时，不产生第二个 eligible round；不同 digest 才是同一 epoch 的新 round。
- `event_id`：`sha256(canonical_json(epoch, run_id, run_attempt, audit_digest, receipt_kind))`。同一 event id 的不同字节是冲突，必须 fail-closed。
- `P1`：由冻结的 policy 映射得到的 `major` / `blocker`（或未来版本明确列出的等价 severity）；aggregator 不从 finding 自由文本推断影响等级。

### 1.3 policy 矩阵

`N` 和 eligible `max_rounds` 必须由同一版本化 policy 计算，并验证 `1 ≤ N ≤ max_rounds`。`infra_diff=true` 只允许把 effective tier 向上提升一档；unknown tier、unknown classifier result、无效 cap 都 fail-closed。

| 输入 tier | 普通 diff：`N / max_rounds` | infra diff：effective tier 与 `N / max_rounds` |
|---|---:|---:|
| `personal` | `1 / 3` | `internal`，`2 / 5` |
| `internal` | `2 / 5` | `saas`，`2 / 8` |
| `saas` | `2 / 8` | `saas`，`2 / 8` |

`max_rounds` 只限制 eligible round；不可用事件使用同一 scope 的独立有限预算 `K_unavailable=max_rounds`，不增加 `eligible_rounds`，也不把 outage 变成 clean。policy 版本变化会改变 epoch，不能在旧 epoch 中途改 N/K。

### 1.4 运行时形状

未来实现的纯函数入口固定在 `.github/actions/gate-aggregator/convergence.py`，I/O 和 job-level quality/primary 判定仍在 `.github/actions/gate-aggregator/aggregate.py`：

```python
def replay_receipts(*, scope: Scope, receipts: Sequence[Receipt]) -> ConvergenceState: ...

def evaluate_round(
    *, state: ConvergenceState, scope: Scope,
    primary: CanonicalPrimary, audit_digest: str,
    waiver_receipts: Sequence[DispositionReceipt],
    processing_key: ProcessingKey,
) -> RoundDecision: ...
```

`replay_receipts` 只消费已通过 producer/schema/identity 校验的 immutable receipt；它按 `(run_id, run_attempt, event_id)` 稳定排序，按 `processing_key` 和 `round_key` 去重，检测同 key 异文冲突后再重算计数器。receipt 中携带的 `decision`、`clean_streak` 等派生字段只供人审计，不能作为输入。

派生 state 最小字段为：`schema_version`、`epoch`、`clean_streak`、`eligible_rounds`、`unavailable_streak`、已消费 `processing_key`/`round_key` 集合的 digest、当前 terminal decision。逐条 finding 只存在于当轮 evidence receipt，不进入跨轮 lineage；waiver receipt 只以当前 epoch + audit digest + exact finding id 消费。

### 1.5 设计级不变式台账（代码落点与测试锁死）

下表是本文后续五轴表的索引；实现卡不得以“有测试”代替具体测试名。

| ID | 不变式 | 代码落点 | 未来锁死它的测试 |
|---|---|---|---|
| INV-A1 | clean 只由当前合法 canonical primary 的 P1 为空产生；重复 P1 不因历史相同而 clean | `.github/actions/gate-aggregator/convergence.py:evaluate_round` | `tests/test_gate_convergence.py::test_nonempty_p1_resets_streak_even_when_finding_ids_repeat` |
| INV-A2 | 首个 clean 轮从 1 计数；达到 N 优先于 max cap | `convergence.py:transition_round` | `test_clean_threshold_wins_over_max_rounds_on_same_event` |
| INV-A3 | unavailable 不增 eligible；连续 K 次才 manual-required | `convergence.py:unavailable_budget` | `test_unavailable_budget_is_independent_and_bounded` |
| INV-A4 | scope 任一 guard 变化生成新 epoch，旧计数和 waiver 不继承 | `convergence.py:derive_epoch/replay_receipts` | `test_scope_digest_change_starts_zero_generation` |
| INV-A5 | 相同 processing/round key 重放是 no-op，异文冲突 fail-closed | `convergence.py:dedupe_receipts` | `test_duplicate_round_is_idempotent_and_conflicting_payload_fails_closed` |
| INV-B1 | source attempt、artifact id、audit digest、epoch 必须成组校验 | `aggregate.py` artifact resolver + `convergence.py:validate_receipt` | `tests/test_gate_convergence_artifact.py::test_producer_payload_preserves_all_attempt_guards` |
| INV-C1 | disposition 只能由受保护控制面签发，且 exact finding id + audit digest 绑定 | `convergence.py:validate_disposition_receipt` | `test_disposition_requires_protected_issuer_and_exact_digest_binding` |
| INV-C2 | 仅合法 `false-positive` 能移除当前同 digest 的 P1；accepted/wont-fix/fixed 不能假装 clean | `convergence.py:consume_dispositions` | `test_only_false_positive_resolves_matching_current_finding` |
| INV-C3 | 新 head、审计 digest、过期、证据撤销和 nonce 已消费都会失效 | `convergence.py:disposition_status` | `test_disposition_lifecycle_invalidates_on_head_digest_expiry_and_revocation` |
| INV-D1 | 三个降层问题在 receipt 写入前回答；保护的是写入和 gate 行为两层 | `aggregate.py` + `gate-v2.yml` wiring | `tests/test_gate_convergence_artifact.py::test_terminal_publish_has_verified_receipt_before_exit` |
| INV-E1 | convergence receipt 是 immutable replay source；ledger 只是观测；评论没有机器 state | `gate-v2.yml` artifact steps + `build_ledger.py` projection | `tests/test_gate_v2_contract.py::test_convergence_state_never_lives_in_pr_comment` |

