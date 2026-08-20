# Canonical clean-streak convergence 设计

> 状态：planning；本卡只新增本文档，不修改生产代码、测试或 `.github/`。
>
> 目标：把 gate#35 的三增量收敛为可直接拆实现卡的契约，并吸收 gate-hub#335 的 protected、digest-bound false-positive disposition 规格。
>
> 实现归属：`zlxlabs/gate` 的 reusable workflow / aggregator 路径。gate-hub 只提供 canonical primary policy、audit 数据和受保护控制面的输入，不另造 evaluator。

输入依据：gate#35（`https://github.com/zlxlabs/gate/issues/35`）、gate-hub#335（`https://github.com/zlxlabs/gate-hub/issues/335`）、gate-hub 既有方案 `/home/zlx/projects/personal/gate-hub/docs/design/gate-convergence-criterion.md`，以及本仓当前 `aggregate.py`、`build_ledger.py` 和 v2 workflow/contract tests。

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
| INV-A2 | 首个 clean 轮从 1 计数；达到 N 优先于 max cap | `convergence.py:transition_round` | `tests/test_gate_convergence.py::test_clean_threshold_wins_over_max_rounds_on_same_event` |
| INV-A3 | unavailable 不增 eligible；连续 K 次才 manual-required | `convergence.py:unavailable_budget` | `tests/test_gate_convergence.py::test_unavailable_budget_is_independent_and_bounded` |
| INV-A4 | scope 任一 guard 变化生成新 epoch，旧计数和 waiver 不继承 | `convergence.py:derive_epoch/replay_receipts` | `tests/test_gate_convergence.py::test_scope_digest_change_starts_zero_generation` |
| INV-A5 | 相同 processing/round key 重放是 no-op，异文冲突 fail-closed | `convergence.py:dedupe_receipts` | `tests/test_gate_convergence.py::test_duplicate_round_is_idempotent_and_conflicting_payload_fails_closed` |
| INV-B1 | source attempt、artifact id、audit digest、epoch 必须成组校验 | `aggregate.py` artifact resolver + `convergence.py:validate_receipt` | `tests/test_gate_convergence_artifact.py::test_producer_payload_preserves_all_attempt_guards` |
| INV-C1 | disposition 只能由受保护控制面签发，且 exact finding id + audit digest 绑定 | `convergence.py:validate_disposition_receipt` | `tests/test_gate_convergence.py::test_disposition_requires_protected_issuer_and_exact_digest_binding` |
| INV-C2 | 仅合法 `false-positive` 能移除当前同 digest 的 P1；accepted/wont-fix/fixed 不能假装 clean | `convergence.py:consume_dispositions` | `tests/test_gate_convergence.py::test_only_false_positive_resolves_matching_current_finding` |
| INV-C3 | 新 head、审计 digest、过期、证据撤销和 nonce 已消费都会失效 | `convergence.py:disposition_status` | `tests/test_gate_convergence.py::test_disposition_lifecycle_invalidates_on_head_digest_expiry_and_revocation` |
| INV-D1 | 三个降层问题在 receipt 写入前回答；保护的是写入和 gate 行为两层 | `aggregate.py` + `gate-v2.yml` wiring | `tests/test_gate_convergence_artifact.py::test_terminal_publish_has_verified_receipt_before_exit` |
| INV-E1 | convergence receipt 是 immutable replay source；ledger 只是观测；评论没有机器 state | `gate-v2.yml` artifact steps + `build_ledger.py` projection | `tests/test_gate_v2_contract.py::test_convergence_state_never_lives_in_pr_comment` |

## 2. 五轴穷举表

### 2.1 轴 A：streak 状态机

状态简写：`C` = collecting（`eligible_rounds < max_rounds` 且 `clean_streak < N`），`U` = collecting 但有不可用预算，`T` = converged/terminal replay，`M` = `manual_required`，`F` = fail-closed/state lost。每个事件先通过 identity、scope、audit 和 disposition 校验，再按以下格子转移；receipt 的 `decision` 字段不参与转移。本表未完整限定路径的 `test_*` 均位于 `tests/test_gate_convergence.py`，artifact 路径另写完整文件名。

| 状态 \ 事件 | 新 major / blocker | 无新 finding | waiver 通过 | waiver 拒绝 | rerun | 新 commit 改 digest |
|---|---|---|---|---|---|---|
| `C` | `eligible+1, streak=0 → C/M`（到 cap 进 M）；`test_nonempty_p1_resets_streak_even_when_finding_ids_repeat` | `eligible+1, streak+1 → T/C/M`（`streak=N` 先 T）；`test_clean_threshold_wins_over_max_rounds_on_same_event` | 当前 digest 的全部 P1 均被合法 receipt 覆盖时按“无 finding”，否则按“新 major”；`test_partial_disposition_stays_blocked` | 当前轮仍有 active P1，按“新 major”；畸形的 current-target receipt 直接 F；`test_rejected_disposition_cannot_advance_streak` | 同 `processing_key` 或 `round_key` → no-op；新 digest 按本行新 major/clean 重入；`test_rerun_same_audit_is_not_a_second_round` | 先丢弃旧 epoch，再把当前 audit 作为新 epoch 首轮：clean 则 `streak=1`，P1 则 `streak=0`；`test_scope_digest_change_starts_zero_generation` |
| `U` | 有效 primary 中断 unavailable streak，按 `C` 的新 major；`test_eligible_round_resets_unavailable_budget` | 中断 unavailable streak，按 `C` 的无 finding；`test_clean_round_resets_unavailable_budget` | 全部 P1 覆盖按 clean，否则按 major；`test_waiver_and_unavailable_counters_are_independent` | active P1 继续 blocked；非法 receipt F；`test_rejected_disposition_cannot_advance_streak` | 相同 key no-op；新的 audit 先清掉 unavailable streak 再按内容归类；`test_duplicate_unavailable_receipt_is_idempotent` | 新 epoch 的 unavailable streak 和 eligible/streak 全为零，再评估当前首轮；`test_new_epoch_drops_unavailable_history` |
| `T` | 不消费旧预算，当前 head 出现 active P1 → M；`test_terminal_replay_with_new_finding_requires_manual` | 保持 T，输出 `terminal_replay`，不加计数；`test_terminal_replay_does_not_consume_round` | 当前 digest 全覆盖 → T；部分覆盖仍 M；`test_terminal_replay_consumes_only_matching_disposition` | M；非法 current-target receipt F；`test_terminal_replay_rejects_invalid_disposition` | 同 digest replay 保持 T；新 digest 在同 epoch 不是“继续 streak”，而是 M，等待新 generation；`test_converged_state_cannot_be_extended_by_rerun` | 生成新 epoch，清零后按首轮重新开始；`test_converged_state_resets_on_head_change` |
| `M` | 旧 epoch 不再收新轮，保持 M；`test_manual_required_is_terminal_for_epoch` | 保持 M；不能用后来的 clean 证据偷偷复活；`test_manual_required_rejects_late_clean_round` | 保持 M；disposition 只能被审计，不能绕过人工恢复；`test_manual_required_rejects_waiver_shortcut` | 保持 M；`test_manual_required_is_terminal_for_epoch` | 同 processing/round key 仍 no-op；其它 rerun 也保持 M；`test_manual_required_is_idempotent` | 只有可信的新 epoch 初始化才离开 M；当前旧 state 不可信则 F；`test_manual_reinitialize_is_explicit_and_zero_based` |
| `F` | 保持 F，禁止用新 primary 掩盖 state 损坏；`test_fail_closed_never_consumes_primary` | 保持 F；`test_fail_closed_never_treats_missing_as_clean` | 保持 F；无法验证 waiver 不能修复 state；`test_fail_closed_rejects_waiver` | 保持 F；`test_fail_closed_is_sticky_until_reinitialize` | 同 key no-op 只记录诊断，不改变 F；`test_fail_closed_replay_is_deterministic` | head 变化也不能自动信任旧 state；必须受保护人工 reinitialize，且新起点为零；`test_untrusted_state_cannot_auto_reset_on_new_head` |

补充规则：`waiver 通过` 只表示 receipt 校验成功；它不单独增加 streak。只有当前 canonical audit 的 P1 全部有合法、exact-id、same-digest 的 `false-positive` receipt，才等价于“无新 finding”。`waiver 拒绝` 若只是控制面明确拒绝，按 active P1 阻塞；若是一个看起来针对当前轮却字段矛盾的 receipt，则是 F，不得降级为“没有 waiver”。

### 2.2 轴 B：部署形态与唯一性

所有 artifact producer 必须先写真实 payload，再由 aggregator 读取并重算 digest；shell 的 env/argv、artifact 名称和 workflow run metadata 是跨进程契约，不能用同进程构造的 dict 测试代替。

| 形态 | 允许的 canonical 输入 | 唯一守卫值与重放语义 | 检测点 |
|---|---|---|---|
| 同 PR 多 run、同 head | 每个 run 只能提交一个当前 canonical primary audit；不同 audit digest 是不同 round | `epoch + run_id + audit_digest` 唯一 round；同 digest 只计一次；`run_attempt` 只负责 producer 事件幂等 | `tests/test_gate_convergence_artifact.py::test_multiple_runs_same_head_replay_in_run_id_order` |
| `rerun --failed` | 当前 attempt 可以下载更早 `source_attempt ≤ current_attempt` 的 audit，但必须保留来源 attempt、artifact id 和原始 audit digest | 同 `run_id` 重跑：`processing_key` 去重；复用同一 audit：`round_key` 去重；只有新 canonical digest 才能成为新 round | `tests/test_gate_convergence_artifact.py::test_rerun_failed_reuses_audit_without_double_counting` |
| force-push 改 head/diff | 只接受新 scope 的 audit；旧 head 的 artifact 可被列出但不可消费 | `head_sha`、`diff_digest`、`epoch` 任一不符即旧 generation；旧 streak/waiver 全排除，不做“相似 diff”匹配 | `tests/test_gate_convergence.py::test_force_push_excludes_old_epoch_receipts_and_dispositions` |
| 并行 attempt / 并行 run | 每个 producer 写自己的 immutable receipt，不 PATCH 共享状态；相同 event id 异文是冲突 | `event_id` 含 epoch/run/attempt/audit digest；排序不依赖到达时间；同 round digest 去重，异 digest 按 run id 稳定排序；冲突 F | `tests/test_gate_convergence_artifact.py::test_parallel_receipts_are_order_independent_and_conflicts_fail_closed` |
| 跨 attempt artifact 检索 | artifact listing 必须分页、过滤 exact repo/PR/head/epoch 前缀，拒绝 expired/future attempt；canonical audit 的 source attempt 必须等于 resolver 输出 | `(artifact_id, source_attempt, artifact_name, audit_digest)` 四元组写入 receipt；同 source attempt 出现不同候选不可猜选，直接 F；同 digest 重复只作 duplicate | `tests/test_gate_v2_contract.py::test_cross_attempt_resolver_preserves_source_and_artifact_guards`；`tests/test_gate_convergence_artifact.py::test_ambiguous_same_attempt_artifacts_fail_closed` |
| 过期/缺失历史 artifact | 不能把“未列出”当 zero state；若无法证明当前 epoch 的完整 receipt 集，decision 为 F/M，不放行 | artifact retention 至少覆盖 `max_rounds + K_unavailable` 的 replay 窗口；listing/API/download 任一失败写 `history_incomplete`，不更新 clean streak | `tests/test_gate_convergence_artifact.py::test_missing_history_is_not_a_fresh_generation` |

这里的“唯一”不是依赖 PR 评论的 `If-Match`。artifact id 只是下载定位符；真正 correctness identity 是 epoch、run、attempt、audit digest 的组合。若两个 payload 声称同一组合却字节不同，reducer 必须停在 fail-closed。

### 2.3 轴 C：protected、digest-bound disposition 生命周期

`gate-hub#335` 的 exact finding id 是当前 audit 内的授权目标，不是跨轮 lineage。只有 `false-positive` 能影响 required gate；`accepted`、`wont-fix` 是人工风险记录，`fixed` 需要下一轮真实 audit 证明，不直接制造 clean。

| 阶段 | 必须发生的事 | 绑定/输出 | 失败与失效 | 检测点 |
|---|---|---|---|---|
| 签发申请 | owner/maintainer 通过 protected workflow dispatch 指定 PR、`finding_id`、`disposition=false-positive`、非空 reason 和至少一个 immutable evidence ref；PR author、committer、reviewer、普通评论者不能自批自己的 P1；owner 即 PR author 时需要另一名 maintainer | control job 重新下载当前 canonical audit，确认 exact finding id、scope 和 audit bytes；生成不可直接生效的 `disposition_receipt`，记录 issuer login/user id、control run id、approval ref、issued/expiry、nonce、evidence manifest digest | 缺 reason/evidence、unknown finding id、权限不合格、current audit 不可读：不产可消费 receipt；普通 PR 评论只作为 evidence ref，不是签发 | `tests/test_gate_convergence.py::test_disposition_requires_protected_issuer_and_evidence` |
| 绑定 | receipt 必须同时绑定 `repository_id/pr_number`、`epoch/generation`、完整 scope、`head_sha`、`diff_digest`、目标 `primary_run_id + primary_run_attempt`、完整 `audit_digest`、exact `finding_id` | canonical JSON 中的 binding 和 `receipt_digest` 进入 immutable artifact；不允许 wildcard、category-only 或“本类 finding 永久忽略” | 任一 guard mismatch 是 stale/mismatched；若 receipt 明确 targeting 当前 PR 但不匹配，当前 decision F/M，不能按“没有 waiver”继续算；旧 epoch receipt 不会污染新 epoch | `tests/test_gate_convergence.py::test_disposition_binding_rejects_head_generation_and_digest_mismatch` |
| 消费 | aggregator 只从当前 epoch/current audit digest 的 receipt 集合取 exact finding id；校验 issuer、nonce、expiry、evidence digest、revocation index 后，移除该当前 finding 的阻塞贡献 | receipt 被消费的事实写入当轮 convergence receipt 和 ledger projection；不得修改原 receipt；audit 的原始 finding 仍保留供人审计，终态显示 `finding_id`、reason、evidence ref | unknown/malformed/contradictory current-target receipt fail-closed；仅缺 receipt 则是普通 active P1 blocked；同一 receipt 不能跨 round 自动继承 | `tests/test_gate_convergence.py::test_only_false_positive_resolves_matching_current_finding` |
| 一次性 | `nonce` 只能对目标 audit digest 使用一次；同一 receipt 重放幂等，异 payload 同 nonce 是冲突 | replay 的 consumed set 是派生值；新 artifact 记录 accepted/rejected/duplicate 结果，不回写可变 comment | 重复同 payload no-op；同 nonce 不同 payload F；下一个 primary run 必须重新申请，即使 finding id/text 看起来相同 | `tests/test_gate_convergence.py::test_disposition_nonce_is_idempotent_and_conflict_safe` |
| 失效 | 新 head/base/diff/policy/tier/classifier 生成新 epoch；audit digest 变化、expiry 到时、evidence 撤销、issuer 权限撤销或显式 protected revocation 都使 receipt inactive | 通过 append-only revocation receipt 记录 nonce、原因、操作者、时间和 evidence ref；不得删除原 receipt 或覆盖旧状态 | inactive receipt 只可审计，不能解除 P1；若撤销发生在本轮消费前，current decision 重新变 blocked/manual；已发布的 gate 不回写为绿色 | `tests/test_gate_convergence.py::test_disposition_lifecycle_invalidates_on_head_digest_expiry_and_revocation` |

### 2.4 轴 D：降层三问（每条路径都要能回答）

| 路径/动作 | ①终态写入成功前发生了哪些不可逆动作 | ②守卫值在部署形态下自身唯一吗 | ③保护的是“写入”还是“行为” | 检测点 |
|---|---|---|---|---|
| primary audit producer | reviewer chain 执行和上传 audit 不可逆；producer 先写临时文件、fsync/close 后以唯一 artifact name 上传；上传失败不产生 eligible receipt | `repo/pr/epoch/run_id/run_attempt/audit_digest/source_attempt` 全量进入 payload 和 artifact name；同组合异文 F | 保护写入：只接受实际上传字节；保护行为：aggregator 不接受 quality/OCR/shadow 代替 canonical primary | `tests/test_gate_convergence_artifact.py::test_primary_producer_payload_is_the_bytes_aggregator_verifies` |
| convergence receipt | replay 完成前不发布 `gate/gate` green；receipt artifact upload 是不可逆发布，必须在 exit/terminal envelope 前成功 | `event_id`、epoch、round key、artifact id、source attempt 可重算；parallel writer 不靠 CAS | 同时保护写入（receipt 不可变、冲突停机）和行为（`gate_result=pass` 只来自重放后的 N）；不能只测文件写成功 | `tests/test_gate_convergence_artifact.py::test_terminal_publish_has_verified_receipt_before_exit` |
| disposition receipt | 受保护 workflow 读取 current audit/evidence 后才签发；原始 PR 评论不会自动变绿 | issuer identity、approval ref、control run id、epoch、audit digest、finding id、nonce 唯一；dispatch 不共享 PR writer | 保护写入（receipt 来源可信）和行为（只解除同 digest exact finding）；不保护“有人写了任意评论” | `tests/test_gate_convergence.py::test_comment_alone_cannot_change_required_decision` |
| terminal / Required Check | 终态 envelope 和 check context 发布不可逆；发布前必须完成 replay、waiver validation、receipt upload；失败只可 red/manual | `gate/gate` job、run id/attempt、epoch 和 terminal envelope 同源；future artifact、旧 head、旧 source attempt 均拒绝 | 既保护写入也保护行为：`gate` job 必须真的退出对应 code 并发布名为 `gate/gate` 的 check；Step Summary 不能替代 check | `tests/test_gate_v2_contract.py::test_gate_consumes_convergence_before_publishing_required_result` |

### 2.5 轴 E：介质约束

| 介质 | 允许承载 | 不允许承载 | 失败语义/检测点 |
|---|---|---|---|
| `gate-convergence-receipt-v1-*` immutable artifact（每个 producer event 一个） | scope/epoch、run/attempt、audit digest、当轮 P1 evidence、disposition consumption、producer metadata、event id、可重算的 decision 诊断 | 可被 PATCH 的累计 counter、唯一“当前 state”文件、跨轮 finding lineage；artifact 中的 counter 只能是诊断字段 | 按 artifact 全集纯函数 replay；listing/download/字节/digest 错误 F/M；`tests/test_gate_convergence_artifact.py::test_replay_uses_receipt_bytes_not_reported_counters` |
| canonical primary audit artifact | reviewer 原始 verdict、findings、scope、attempt chain、audit bytes | streak、waiver authorization、PR comment state | 只作为 canonical input；`aggregate.py` 先校验 identity/schema；`tests/test_gate_aggregator.py` 既有 canonical matrix + `tests/test_gate_convergence_artifact.py::test_audit_digest_is_raw_bytes_digest` |
| protected disposition/revocation artifact | issuer、approval、exact finding、reason/evidence manifest digest、binding、expiry、nonce、revocation | 直接 gate pass、全局忽略规则、可变“active=true”旗标 | reducer 重算 active；`tests/test_gate_convergence.py::test_revocation_is_append_only_and_recomputed` |
| `codex-review-ledger-v2` JSONL | 每轮观测、review status、finding/disposition 诊断、convergence decision/receipt ids 的 additive projection | correctness state、唯一 writer cursor、缺历史时的默认 clean | ledger 可 fail-open 但 required evaluator 不可依赖；`tests/test_review_ledger.py::test_convergence_projection_is_observational_only` |
| `gate-terminal-v1` / 新 versioned terminal envelope | 本 run 的最终 machine decision、epoch、streak snapshot、reason、receipt ids | 下一轮要修改的累计 state | envelope 是发布结果不是输入；`tests/test_gate_aggregator.py::test_terminal_envelope_bytes_unchanged_by_rendering_work` 与新增 convergence envelope golden test |
| PR 评论 / Step Summary / annotation | 面向人的当前 run 摘要、被 disposition 的 exact finding、reason/evidence 直达链接、manual action | 任意 `gate-convergence-state` marker、counter、epoch cursor、waiver active flag、CAS token、唯一 replay 输入 | 普通用户评论和 bot 评论都不参与 replay；`tests/test_gate_v2_contract.py::test_convergence_state_never_lives_in_pr_comment` |

绝不放进 PR 评论的状态清单：`clean_streak`、`eligible_rounds`、`unavailable_streak`、`last_run_id`、`last_run_attempt`、`epoch` 的唯一游标、`state_hash`、`round_key` 去重表、waiver nonce 的 consumed 标记、任何“当前有效 waiver”布尔值，以及用于 PATCH/If-Match 的版本号。评论可以展示这些值的**本 run 派生摘要**，但机器不能读回它们作判定。

## 3. 三增量拆卡草案

三张实现卡严格串行：增量 1 先冻结纯 reducer 和 state contract；增量 2 在该 contract 上接 protected disposition；增量 3 才接 workflow、artifact 检索和真实 Required Check。任何增量都不能在 gate-hub 侧新增 evaluator。

### 增量 1：aggregator canonical clean-streak evaluator

**目标**

在 gate 仓增加一个无 I/O、可重放的 canonical evaluator，使 `aggregate.py` 从“本轮 primary 是否 pass”升级为“当前 epoch 是否达到 N 个连续 clean round”。同一 evaluator 负责 tier/infra policy、canonical P1 投影、generation reset、attempt/round 幂等、unavailable budget 和 fail-closed parsing。

**实现边界**

- 新增 `.github/actions/gate-aggregator/convergence.py`：定义 `Scope`、`CanonicalPrimary`、`Receipt`、`ConvergenceState`、`RoundDecision`，实现 `derive_epoch`、`policy_for`、`validate_scope`、`evaluate_round`、`replay_receipts`、`dedupe_receipts`。纯 stdlib；不读 GitHub API，不写评论，不读取 ledger。
- 修改 `.github/actions/gate-aggregator/aggregate.py`：保留现有 `evaluate()` 的 quality/primary/audit 单轮 fail-closed 行为；在该行为成功后把 canonical audit、原始 digest、source attempt 和 `Scope` 转给 `convergence.py`。旧 `Outcome` 字段不被静默改义，新增 convergence envelope 使用明确 `schema_version`。
- 增量 1 不改 `.github/workflows/gate-v2.yml` 的 job wiring，不创建 waiver workflow，不改变 OCR/shadow、reviewer chain 或 ledger comment。测试先用 fixture receipts 驱动纯函数。
- 只把当轮 P1 evidence 放入 receipt 以便未来精确审计；不保存前轮 finding 集合、lineage、line、文本 fingerprint。增量 1 的 waiver 输入只能是显式的“无有效 disposition”结果；受保护 disposition 由增量 2 接入。

**核心契约**

1. `primary.verdict=pass/fail` 且 schema、identity、scope、job/audit 一致，才可能是 eligible；`unavailable` 不增任何 clean/eligible 计数。
2. `p1_ids` 非空时 `clean_streak=0`；不比较上一轮 ID。`p1_ids=[]` 时 streak 加一，`streak>=N` 优先于 `eligible>=max_rounds`。
3. `scope` 变化新建 epoch；旧 receipt 只作历史，不能继承 counter。可信 scope change 可自动从零开始；state/artifact 本身不可信时必须 manual reinitialize，不得自动清零。
4. 同 `processing_key` 或同 `round_key` 重放为 no-op；相同 event id 的不同 payload、同 run/attempt 的不同 audit digest、未知 schema/version、缺 guard 均 fail-closed。
5. `unavailable_streak` 只从同 epoch、同 scope、明确 reviewer/circuit unavailable 的合法 receipt 推导；达到 `K_unavailable=max_rounds` 返回 `manual_required`，但不伪造 clean round。

**验收与测试**

- `tests/test_gate_convergence.py` 必须包含轴 A 的完整参数矩阵：`test_state_event_matrix_is_exhaustive`、`test_nonempty_p1_resets_streak_even_when_finding_ids_repeat`、`test_clean_threshold_wins_over_max_rounds_on_same_event`、`test_unavailable_budget_is_independent_and_bounded`、`test_scope_digest_change_starts_zero_generation`、`test_duplicate_round_is_idempotent_and_conflicting_payload_fails_closed`、`test_manual_reinitialize_is_explicit_and_zero_based`。
- 同文件锁 tier × infra 六格策略（含 unknown tier/cap）、`line=null` 当前 finding、failover/shard 仍只形成一个 canonical round、terminal replay 不消费预算，以及 `not_expected`/reviewer `waived` 不计 clean。
- `tests/test_gate_aggregator.py` 增加 `test_single_round_gate_outcome_is_not_convergence_state`，确保既有 `evaluate()` 的质量/审计语义不被 streak 改写；既有 terminal envelope golden tests 必须继续通过。
- 跨边界 producer fixture 先在 `tests/test_gate_convergence_artifact.py` 固定，断言未来实际写出的 JSON 字节、argv/env 和 raw audit digest，不接受只在进程内拼 dict 的替代测试。

**行数预算**

实现约 550 行，纯函数测试与 fixture 约 750 行，合计约 1,300 行，低于本增量 3,500 行上限；不新增 retry/fallback/防御性吞错。

### 增量 2：绑定当前轮次的 false-positive disposition

**范围裁决（2026-08-20，owner）**

本节原设计包含 protected issuer provenance、evidence manifest 密码学校验、nonce 一次性、
append-only revocation、expiry 与完整 scope 绑定。经第一性原理复核：本仓是个人自用的多
Agent 开发门禁，write 权限只有 owner 与其派出的 agent，**上述机制防的「有预谋的内部
攻击者」在本项目中不存在**，属过度设计，已全部删除。判定依据与实测见
`docs/sessions/gate35-inc2/reviews/inc2-r1-verdict.md`、`inc2-r2-verdict.md`。

**目标**

让 owner 能对**当前这一轮 canonical audit 中的 exact finding** 声明 false-positive，
使该 finding 不再阻塞 required gate，并留下三个月后仍读得懂的理由。

**保留的绑定（与安全无关，与正确性有关）**

豁免绑定 `head_sha + audit_digest + epoch + finding_id`。换 head、换 audit 或换 epoch 即失效。
理由：门禁主审存在「挤牙膏」行为（每轮只报一条不代表只剩一条），一次豁免若能跨轮永久生效，
clean streak 就不再反映真实收敛。这条不是防攻击，是防自欺。

**receipt 形状**

`schema_version`、`disposition`（只允许 `false-positive`）、`repository_id`、`pr_number`、
`epoch`、`head_sha`、`audit_digest`、`finding_id`、`reason`。

**唯一的准入约束**

签发入口是人工 `workflow_dispatch`。agent 在 CI 中够不到该入口——这已满足本仓唯一的威胁模型
（agent 不应能给自己开绿灯）。不做签发人身份校验：本仓只有一个人类。

**若将来变成多人协作**

需要重新引入的是：issuer provenance（从 GitHub environment 的真实审批记录取 approver）与
canonical audit 的 run provenance 校验（核验 run 的 workflow identity/ref/event/conclusion，
而非只信 artifact 自报字段）。重开条件是**本仓出现第二个人类 write 用户**，不是「觉得更严谨」。

### 增量 3：workflow 接线与 canary 实证

**目标**

把增量 1/2 接到 pinned reusable workflow 的 `gate` job，使真实 Required Check `gate / gate`（API context `gate/gate`）消费 replay 结果；用 artifact 生产、跨 run 检索和 canary run 证明 clean streak、finding、waiver、unavailable/manual、new generation 全路径，而不是只跑 contract test 或 advisory projection。

**接线边界**

- `.github/workflows/gate-v2.yml` 的 `gate` job 继续是唯一 required-check producer：在调用 aggregator 前解析当前 canonical primary；分页列出并下载当前 PR/epoch 的 convergence receipts 和 protected disposition receipts；把原始文件目录、source attempt、artifact id、caller/workflow SHA 传给 `aggregate.py`。
- `aggregate.py` 负责先验证 producer 实际字节和 guard，再调用 `replay_receipts`/`evaluate_round`，生成当前 terminal envelope 和一条本 run immutable convergence receipt。receipt upload 必须在发布绿色 terminal/check 前成功；任一步失败都 fail-loud，不能静默降级成 first run。
- workflow 只允许一个 `gate` job 发布 `gate/gate`；quality/OCR/shadow/ledger 的结果只能出现在输入或观测投影。现有 `ledger` 的 repository-level queue 不得被误当作 convergence writer lock；如果实现选择 workflow concurrency，必须把其作用写进 receipt，且正确性仍由 immutable replay 保证。
- `.github/actions/review-ledger/build_ledger.py` 在本增量只消费本 run convergence receipt 做 additive report；它不能读取 PR 评论来恢复 counters。PR comment/Step Summary 只渲染当前 run 的人类 action、exact disposition 和直达 evidence。
- `templates/caller-gate-v2.yml` 和 `tests/test_check_pinned_uses.py` 只更新/锁 reusable workflow 的完整 SHA 与 source checkout contract；不重开已由 PR #46/#47 和 #31/#44 解决的可见性/可用性前置。

**producer / consumer 序列**

```text
canonical primary upload
  → gate resolver 分页列出 receipt，拒绝 future/expired/ambiguous artifact
  → 下载并校验每个真实文件字节、epoch、source attempt、audit digest
  → pure replay + 当前 round evaluator
  → 写本 run convergence receipt（immutable）
  → 写 terminal envelope / Step Summary / annotation
  → gate job 退出并发布 gate/gate
```

序列中任何“写入前已发生的不可逆动作”都要以 receipt 记录；没有 receipt 就没有 pass。`rerun --failed`、parallel attempt 和 force-push 按轴 B 处理，不能靠“选最新 artifact”或 PR 评论游标猜测。

**canary 方案与实证证据**

在 `gate-hub#38` 网络链路可用后，选一个可回滚的 personal canary caller，固定 reusable workflow SHA；先 contract/fixture，再真实 run。每个场景保存 workflow run URL、`gate/gate` check conclusion、terminal envelope、convergence receipt artifact id/name/digest、ledger projection 和人工摘要，形成可复核 evidence bundle。

| 场景 | 操作序列 | 必须观察到 |
|---|---|---|
| clean streak | personal `N=1` 一次无 P1；internal `N=2` 连续两次同 epoch 无 P1 | personal 首轮 `gate/gate=success`；internal 首轮非 pass、第二个 distinct round 才 success；OCR/shadow 不改变结果 |
| active finding | 同 epoch 连续提交含 major 的 audit，finding id 可相同或变化 | 每个 eligible round `streak=0`；达到 max 后 `manual_required`/red，不能因 finding “重复”而放行 |
| valid false positive | 先让唯一 P1 使 gate red，再走 protected disposition，再运行真实 gate | 只有 exact digest/finding 的 `false-positive` 消除该项；receipt id/reason/evidence 可见；不产生空 commit/admin bypass |
| invalid/stale disposition | 修改 head/diff、错误 audit digest、unknown id、过期、撤销、普通评论或 label | 旧 receipt 不消费；current-target malformed 走 fail-closed/manual；普通评论永不改变 Required Check |
| unavailable/manual | 连续产生 `K=max_rounds` 个同 epoch reviewer/circuit unavailable receipt；穿插一次 eligible round 再重复 | unavailable 不增 eligible；连续 K 次进入 `manual_required`；eligible round 清空 unavailable streak；不返回 pass |
| rerun / cross-attempt | 对同一 run 执行 `rerun --failed`，使 attempt 2 读取 attempt 1 audit；再产生不同 audit digest | 同 audit 不多一个 round；新 digest 只计一次；source attempt/selected artifact 都可在 payload 与 envelope 对上 |
| force-push / parallel | 同时触发两个 attempt，再 push 新 head | receipt replay 与到达顺序无关；旧 epoch 全排除；新 epoch 从零开始；同 event 异文 fail-closed |

实时 canary 的 gate-hub#38 网络链路是验收时点依赖，不是 evaluator 契约依赖：在链路未通前，增量 1/2 的纯函数、producer payload 和 workflow static contract 仍必须完成；但不得把 fixture 绿写成“真实 `gate/gate` 已证明”。

**验收与测试**

- `tests/test_gate_v2_contract.py` 增加 `test_gate_consumes_convergence_before_publishing_required_result`、`test_convergence_artifact_resolution_is_paginated_and_fail_closed`、`test_convergence_state_never_lives_in_pr_comment`、`test_required_context_remains_gate_slash_gate`。
- `tests/test_gate_convergence_artifact.py` 覆盖 subprocess producer 的真实 argv/env、写入文件字节、artifact name、跨 attempt listing/download、同 attempt ambiguity 和 upload-before-terminal barrier；保留每个真实 producer fixture/contract artifact。
- `tests/test_gate_aggregator.py` 增加 `test_main_consumes_replayed_receipts_and_writes_convergence_envelope`、`test_main_fails_closed_when_receipt_upload_or_history_is_incomplete`，并保持既有 #32/#43 visible terminal assertions。
- canary 不以 advisory PR comment、ledger artifact 或 contract-only 结果验收；必须拿到 pinned workflow 的真实 `gate/gate` check。#38 未恢复时只能标记 live-evidence pending，不得标 succeeded。

**行数预算**

workflow/resolver/aggregator wiring 约 750 行，跨边界测试与 canary fixture/记录工具约 1,200 行，合计约 1,950 行，低于 3,500 行上限；不修改 legacy `gate.yml` 的语义，不把 OCR/shadow 接入 required convergence。

## 4. 完成门槛与回滚

宣告实现完成前，逐条回看 INV-A1～INV-E1：每条都必须能指出代码文件和锁死它的测试名，且存在真实 producer payload fixture。尤其不能用以下证据替代：单轮 `aggregate.py` 绿、gate-hub advisory、PR 评论文本、ledger JSONL、或“管理员可以 bypass”的手工记录。

三增量的落地顺序和停线条件：

1. 增量 1 未通过轴 A 全格矩阵，禁止接 waiver 或 workflow。
2. 增量 2 未通过 exact digest/finding 生命周期，禁止在 required gate 中消费任何评论 disposition；只能继续保持 active P1 red/manual。
3. 增量 3 的静态 contract、payload boundary 和真实 canary 任一未通过，不能把 `gate/gate` 标为已收敛；先回滚到旧 fail-closed aggregator，保留 immutable receipts 供调查，不删除 evidence。
4. canary 后发现旧 epoch receipt 被错误消费、parallel order 影响结果、或 disposition 能跨 digest 解红，立即关闭 convergence consumer/feature pin；不得用空 commit、label、admin bypass 或手改评论补洞。

本设计最终冻结的核心判据是：**只对当前 epoch 的 canonical primary 做本轮 P1 机械判定；只对 protected、evidence-backed、exact finding + exact audit digest 的 false-positive 做当前轮授权；跨轮只重放 immutable artifact，不把 GitHub 评论当可变状态。**
