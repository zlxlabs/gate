# Canonical clean-streak convergence 设计

> 状态：历史设计；2026-09-15 的 gate-hub#810 裁决及实现契约以 `docs/sessions/260915-disposition-record-only/design.md` 为准。
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
且 canonical primary audit 的当前 P1 finding 集合为空
```

`unavailable`、`not_expected`、reviewer 自报 `waived` 和非空 P1 集合都不是 clean。Disposition receipt 只记录提交者的主张，不移除 P1、不制造 clean round，也不单独令门禁 fail；缺失、digest 不匹配、畸形或不可读取的 receipt 只影响诊断记录。canonical primary / audit 自身仍按既有规则 fail-closed。

本设计刻意不做跨轮 finding 身份推断。`finding_id` 只用于把 receipt claim 定位到当时的 canonical finding；不保存前轮 finding 集合、lineage、文本 fingerprint 或行号。这样同一条 finding 反复出现、换 reviewer、换行号或换文本，都不会被误当作 clean。

### 1.1 当前基线（必须保留的事实）

| 现状 | 证据位置 | 对本设计的影响 |
|---|---|---|
| `aggregate.py:evaluate()` 是一次输入、一次输出的纯单轮判定，没有 streak/generation/cursor | `.github/actions/gate-aggregator/aggregate.py:299-425` | 增量 1 在 aggregator 侧增加 canonical reducer；不能把 advisory 结果冒充 state。 |
| aggregator 当前只验证 audit 的 identity quintuple，并拒绝不受支持的 `not_expected`/`waived` | `.github/actions/gate-aggregator/aggregate.py:87-108,207-265,371-414` | 新 evaluator 复用 canonical audit 校验；waiver 不是 reviewer verdict。 |
| canonical audit artifact 按 `repository_id/head_sha/run_id/run_attempt` 命名，gate 会选择不晚于当前 attempt 的最高来源 attempt | `.github/workflows/gate-v2.yml:596-669` | 读取必须同时保留 `source_attempt`、artifact id、audit digest；不能只看文件名。 |
| v2 `gate` 状态条持 PR 级 panel 锁、`ledger` 持 repository 级串行队列（均 cancel false 排队不取消）；`quality`/`primary` 各持 job 级 cancel true 的 PR 取消锁 | `docs/design/gate-convergence-criterion.md`；`tests/test_gate_v2_contract.py:test_gate_and_ledger_writer_locks_remain_cancel_false`、`test_quality_and_primary_have_independent_cancel_true_pr_locks` | 不能假定现有 ledger 锁保护 convergence；增量 3 必须显式验证 writer/receipt 语义。 |
| review ledger 会读取历史 artifact，并用 sticky comment 保存 epoch 游标，但读取失败目前 warning 后继续 | `.github/actions/review-ledger/build_ledger.py:556-603,692-730` | ledger 只能做观测投影；correctness replay 不能依赖它的 fail-open 路径。 |
| ledger 目前把 PR 评论中的 `Codex finding disposition` 解析进 `finding_dispositions` | `.github/actions/review-ledger/build_ledger.py:73-85,470-496` | 旧评论格式只作观察数据；receipt claim 也只作审计记录，二者都不能解除 required 红。 |
| 终态 envelope 是跨 job artifact，已有 schema/可见性测试 | `.github/actions/gate-aggregator/aggregate.py:189-205`；`tests/test_gate_aggregator.py:test_terminal_envelope_bytes_unchanged_by_rendering_work` | 新 decision 可扩展 versioned envelope，但不能用评论替代它或静默改变旧字段。 |

### 1.2 固定术语和唯一 source of truth

- `scope`：本次判定的完整身份，至少包括 `repository_id`、`pr_number`、`base_sha`、`head_sha`、`diff_digest`、`policy_version`、`policy_digest`、`tier`、`caller_sha`、`reusable_workflow_sha`。
- `epoch`：`sha256(canonical_json(scope))`。scope 中任何字段变化都生成新 epoch；它是 generation 的不可变守卫值，不是评论游标。
- `audit_digest`：canonical primary audit 文件原始 UTF-8 字节的 SHA-256；先校验 JSON/schema，再将该 digest 绑定到 receipt 和 disposition。不得用 findings 子集 digest 代替。
- `processing_key`：`(repository_id, pr_number, run_id, run_attempt)`，同一 key 的重放必须幂等。
- `round_key`：`(epoch, run_id, audit_digest)`。同一 canonical audit 被 `rerun --failed` 的另一 attempt 重新消费时，不产生第二个 eligible round；不同 digest 才是同一 epoch 的新 round。
- `event_id`：`sha256(canonical_json(epoch, run_id, run_attempt, audit_digest, receipt_kind))`。同一 event id 的不同字节是冲突，必须 fail-closed。
- `P1`：由冻结的 policy 映射得到的 `major` / `blocker`（或未来版本明确列出的等价 severity）；aggregator 不从 finding 自由文本推断影响等级。

### 1.3 policy 矩阵

`N` 和 eligible `max_rounds` 必须由同一版本化 policy 计算，并验证 `1 ≤ N ≤ max_rounds`。`tier` 直接选择对应的收敛档位；unknown tier、无效 cap 都 fail-closed。

| 输入 tier | `N / max_rounds` |
|---|---:|
| `personal` | `1 / 3` |
| `internal` | `2 / 5` |
| `saas` | `2 / 8` |

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
| INV-C1 | producer 的 `github.triggering_actor` / `actor_id` 只能标识 GitHub 账号；owner-only Environment 且 `prevent_self_review=false`、共享 owner 凭据不能证明是人工操作。Receipt 只记录 `false-positive` 主张，不授权放行 | `convergence.py:record_dispositions` | `tests/test_review_ledger.py::test_real_disposition_producer_receipt_is_record_only_through_ledger` |
| INV-C2 | 收据技术校验只决定记录状态；任何值都不能移除当前 P1 或触发 fail-closed gate state | `convergence.py:record_dispositions/evaluate_round` | `tests/test_gate_convergence.py` disposition 状态矩阵 |
| INV-C3 | `head_sha + audit_digest + epoch + finding_id` 仍绑定审计记录，保证记录对应具体 primary finding；不再授予 gate 权限 | `convergence.py:validate_disposition_receipt` | `tests/test_gate_convergence.py::test_disposition_binding_rejects_head_epoch_digest_and_finding_mismatch` |
| INV-D1 | 三个降层问题在 receipt 写入前回答；保护的是写入和 gate 行为两层 | `aggregate.py` + `gate-v2.yml` wiring | `tests/test_gate_convergence_artifact.py::test_terminal_publish_has_verified_receipt_before_exit` |
| INV-E1 | convergence receipt 是 immutable replay source；ledger 只是观测；评论没有机器 state | `gate-v2.yml` artifact steps + `build_ledger.py` projection | `tests/test_gate_v2_contract.py::test_convergence_state_never_lives_in_pr_comment` |

## 2. 五轴穷举表

### 2.1 轴 A：streak 状态机

状态简写：`C` = collecting（`eligible_rounds < max_rounds` 且 `clean_streak < N`），`U` = collecting 但有不可用预算，`T` = converged/terminal replay，`M` = `manual_required`，`F` = fail-closed/state lost。此表原本把 disposition 当作 gate 输入，属于 gate-hub#810 已推翻的假设。当前 primary 收敛只按 canonical primary P1 计算；receipt 状态仅影响审计记录，完整更新后的 receipt 轴见 `docs/sessions/260915-disposition-record-only/design.md`。本段状态机的非-disposition格子仍用于解释收敛状态。

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

### 2.3 轴 C：false-positive 主张的记录生命周期

本轴记录数据的来源与有效性，不给 receipt 授权效力。`workflow_dispatch` 提交的账号身份不等于人工审批身份。

| 阶段 | 必须发生的事 | 记录语义 | 失败与失效 | 检测点 |
|---|---|---|---|---|
| 签发申请 | producer 读取当前 canonical audit，要求非空 reason 并确认目标是 inferred P1 | immutable artifact 保存既有 bytes；`disposition` 是提交者的 `false-positive` 主张 | 缺 reason、unknown finding id 或 audit 不可读时不能生成 artifact；Required Check 仍只按 primary / quality 结果判定 | `tests/test_gate_v2_contract.py::test_disposition_workflow_is_protected_and_cannot_publish_gate_result` |
| 绑定 | receipt 校验 `repository_id/pr_number`、`epoch`、`head_sha`、完整 `audit_digest` 与 exact `finding_id` | binding 让审计记录可定位到当时的 primary finding，不授予放行权 | 不允许 wildcard/category-only；任一绑定不匹配只把该 receipt 记为 stale/invalid diagnostic | `tests/test_gate_convergence.py::test_disposition_binding_rejects_head_epoch_digest_and_finding_mismatch` |
| 记录 | 通过技术校验的 receipt 写入 terminal / ledger record | 输出 `recorded`, `claim`, `triggering_actor`；不输出 `resolved` 或人工 `approved` | 不论当前或旧绑定，receipt 均不得移除 P1 或改变 gate / streak | `tests/test_review_ledger.py::test_real_disposition_producer_receipt_is_record_only_through_ledger` |
| 非法 / 重复 / stale | consumer 保留可诊断的校验状态 | 错误只影响 receipt 记录，不参与 primary 或 convergence 状态 | invalid / duplicate / stale / read error 都不能使原本 pass 变 fail，也不能使原本 fail 变 pass | `tests/test_gate_convergence.py` disposition 状态矩阵；`tests/test_gate_aggregator.py` receipt read-error test |

### 2.4 轴 D：降层三问（每条路径都要能回答）

| 路径/动作 | ①终态写入成功前发生了哪些不可逆动作 | ②守卫值在部署形态下自身唯一吗 | ③保护的是“写入”还是“行为” | 检测点 |
|---|---|---|---|---|
| primary audit producer | reviewer chain 执行和上传 audit 不可逆；producer 先写临时文件、fsync/close 后以唯一 artifact name 上传；上传失败不产生 eligible receipt | `repo/pr/epoch/run_id/run_attempt/audit_digest/source_attempt` 全量进入 payload 和 artifact name；同组合异文 F | 保护写入：只接受实际上传字节；保护行为：aggregator 不接受 quality/OCR/shadow 代替 canonical primary | `tests/test_gate_convergence_artifact.py::test_primary_producer_payload_is_the_bytes_aggregator_verifies` |
| convergence receipt | replay 完成前不发布 `gate/gate` green；receipt artifact upload 是不可逆发布，必须在 exit/terminal envelope 前成功 | `event_id`、epoch、round key、artifact id、source attempt 可重算；parallel writer 不靠 CAS | 同时保护写入（receipt 不可变、冲突停机）和行为（`gate_result=pass` 只来自重放后的 N）；不能只测文件写成功 | `tests/test_gate_convergence_artifact.py::test_terminal_publish_has_verified_receipt_before_exit` |
| disposition receipt | workflow 读取 current canonical audit 后记录提交者主张；PR 评论不会作为 receipt 输入 | 9 个字段和 `head_sha + audit_digest + epoch + finding_id` 用于校验记录归属；账号字段不能证明人工身份 | 仅保护记录完整性；receipt 不解除 finding，也不改变 Required Check | `tests/test_review_ledger.py::test_real_disposition_producer_receipt_is_record_only_through_ledger` |
| terminal / Required Check | 终态 envelope 和 check context 发布不可逆；发布前必须完成 replay、waiver validation、receipt upload；失败只可 red/manual | `gate/gate` job、run id/attempt、epoch 和 terminal envelope 同源；future artifact、旧 head、旧 source attempt 均拒绝 | 既保护写入也保护行为：`gate` job 必须真的退出对应 code 并发布名为 `gate/gate` 的 check；Step Summary 不能替代 check | `tests/test_gate_v2_contract.py::test_gate_consumes_convergence_before_publishing_required_result` |

### 2.5 轴 E：介质约束

| 介质 | 允许承载 | 不允许承载 | 失败语义/检测点 |
|---|---|---|---|
| `gate-convergence-receipt-v1-*` immutable artifact（每个 producer event 一个） | scope/epoch、run/attempt、audit digest、当轮 P1 evidence、disposition consumption、producer metadata、event id、可重算的 decision 诊断 | 可被 PATCH 的累计 counter、唯一“当前 state”文件、跨轮 finding lineage；artifact 中的 counter 只能是诊断字段 | 按 artifact 全集纯函数 replay；listing/download/字节/digest 错误 F/M；`tests/test_gate_convergence_artifact.py::test_replay_uses_receipt_bytes_not_reported_counters` |
| canonical primary audit artifact | reviewer 原始 verdict、findings、scope、attempt chain、audit bytes | streak、waiver authorization、PR comment state | 只作为 canonical input；`aggregate.py` 先校验 identity/schema。audit **文件字节不稳定**（含 duration/tokens/timestamps）；disposition `audit_digest` 对 `canonical_audit_digest` 的字段子集取哈希，见 `tests/test_gate_convergence.py::test_canonical_audit_digest_ignores_runtime_noise` |
| disposition receipt artifact | 既有字段与 producer bytes，包括提交者的 `false-positive` 主张 | 直接 gate pass、全局忽略规则、可变“active=true”旗标 | reducer 只校验并记录当前/失效状态，绝不移除 P1；`tests/test_gate_convergence_artifact.py::test_disposition_producer_writes_minimal_receipt_bytes_from_raw_audit` |
| `codex-review-ledger-v2` JSONL | 每轮观测、review status、finding/disposition 诊断、convergence decision/receipt ids 的 additive projection | correctness state、唯一 writer cursor、缺历史时的默认 clean | ledger 可 fail-open 但 required evaluator 不可依赖；`tests/test_review_ledger.py::test_convergence_projection_is_observational_only` |
| `gate-terminal-v1` / 新 versioned terminal envelope | 本 run 的最终 machine decision、epoch、streak snapshot、reason、receipt ids | 下一轮要修改的累计 state | envelope 是发布结果不是输入；`tests/test_gate_aggregator.py::test_terminal_envelope_bytes_unchanged_by_rendering_work` 与新增 convergence envelope golden test |
| PR 评论 / Step Summary / annotation | 面向人的当前 run 摘要、被 disposition 的 exact finding、reason/evidence 直达链接、manual action | 任意 `gate-convergence-state` marker、counter、epoch cursor、waiver active flag、CAS token、唯一 replay 输入 | 普通用户评论和 bot 评论都不参与 replay；`tests/test_gate_v2_contract.py::test_convergence_state_never_lives_in_pr_comment` |

绝不放进 PR 评论的状态清单：`clean_streak`、`eligible_rounds`、`unavailable_streak`、`last_run_id`、`last_run_attempt`、`epoch` 的唯一游标、`state_hash`、`round_key` 去重表、waiver nonce 的 consumed 标记、任何“当前有效 waiver”布尔值，以及用于 PATCH/If-Match 的版本号。评论可以展示这些值的**本 run 派生摘要**，但机器不能读回它们作判定。

## 3. 四张实现卡拆分草案

四张实现卡严格串行：增量 1 先冻结纯 reducer 和 state contract；增量 2 在该 contract 上接 protected disposition；增量 3 拆成 3a 写出侧和 3b 读回侧。任何增量都不能在 gate-hub 侧新增 evaluator。

### 增量 3a：convergence receipt 写出侧（本卡）

本卡只把 producer 侧通电：canonical primary 经过当前轮 `evaluate_round()` 后，写出一条
`Receipt.as_dict()` 的 canonical JSON（固定文件名 `convergence-receipt.json`），并以不可忽略
失败的 artifact upload 作为 `gate/gate` 的写出 barrier。receipt upload 位于 aggregate 之后、
`gate-terminal.json` upload 和 PR status panel 发布之前；upload 失败使 job 失败。draft、fork、
hosted、primary skipped 或 audit 不可用等没有 canonical primary 的轮次不写占位 receipt，
只在 Step Summary 记录未产出原因并跳过该 upload。

3a 不做 artifact 分页/下载/历史 replay，不消费 `convergence_state` 或 disposition receipt，
也不落盘 convergence envelope；当前判定仍保持首轮 `initial_state(scope)` 语义。3a 的跨进程
验收必须读取 aggregate CLI 实际写出的字节，再喂给 `validate_receipt()` 和
`replay_receipts()`；跨 run 检索与真实 canary 留在 3b。

### 增量 1：aggregator canonical clean-streak evaluator

**目标**

在 gate 仓增加一个无 I/O、可重放的 canonical evaluator，使 `aggregate.py` 从“本轮 primary 是否 pass”升级为“当前 epoch 是否达到 N 个连续 clean round”。同一 evaluator 负责 tier policy、canonical P1 投影、generation reset、attempt/round 幂等、unavailable budget 和 fail-closed parsing。

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
- 同文件锁三个 tier 档位（含 unknown tier/cap）、`line=null` 当前 finding、failover/shard 仍只形成一个 canonical round、terminal replay 不消费预算，以及 `not_expected`/reviewer `waived` 不计 clean。
- `tests/test_gate_aggregator.py` 增加 `test_single_round_gate_outcome_is_not_convergence_state`，确保既有 `evaluate()` 的质量/审计语义不被 streak 改写；既有 terminal envelope golden tests 必须继续通过。
- 跨边界 producer fixture 先在 `tests/test_gate_convergence_artifact.py` 固定，断言未来实际写出的 JSON 字节、argv/env 和 raw audit digest，不接受只在进程内拼 dict 的替代测试。

**行数预算**

实现约 550 行，纯函数测试与 fixture 约 750 行，合计约 1,300 行，低于本增量 3,500 行上限；不新增 retry/fallback/防御性吞错。

### 增量 2：绑定当前轮次的 false-positive disposition（原授权方案已撤销）

2026-09-15，gate-hub#810 证明原来的身份前提不成立：disposition workflow 记录 `github.triggering_actor` / `github.actor_id`，但 owner-only Environment 没有禁止自批，共用 owner 凭据下无法据此区分人和执行器。用户裁决为“收据只记账，不再改变门禁结论”。

Receipt 仍绑定 `repository_id/pr_number`、`epoch`、`head_sha`、完整 `audit_digest` 和 exact `finding_id`；这些字段用于校验和解释所记录的主张，不提供放行权。`audit_digest` 继续按 `canonical_audit_digest` 计算，producer 对 canonical audit 中 inferred P1 的目标真实性检查照常执行。

Input artifact 保留既有 JSON bytes 与字段，包括 `disposition=false-positive`、`approver`、`approver_id`、`approved_at`。其中 disposition 是提交者主张；身份/时间字段表示 producer 收到的 GitHub 账号与时间，不能呈现为已验证的人工审批。Terminal / ledger 只写 `recorded`、`claim` 和 `triggering_actor` 语义，不再写“approved”或“resolved”。

当前没有人工放行路径。若未来确实需要人工放行，必须先提供执行器拿不到的独立审批身份，再由新设计决定如何消费；不能恢复 owner 共用凭据下的旧 shortcut。

### 增量 3b：workflow 读回、replay 接线与 canary 实证

**目标**

在 3a 的真实 receipt producer 之上，把增量 1/2 接到 pinned reusable workflow 的 `gate` job，使真实 Required Check `gate / gate`（API context `gate/gate`）消费 replay 结果；用 artifact 生产、跨 run 检索和 canary run 证明 clean streak、finding、waiver、unavailable/manual、new generation 全路径，而不是只跑 contract test 或 advisory projection。

**接线边界**

- `.github/workflows/gate-v2.yml` 的 `gate` job 继续是唯一 required-check producer：在调用 aggregator 前解析当前 canonical primary；分页列出并下载当前 PR/epoch 的 convergence receipts 和 protected disposition receipts；把原始文件目录、source attempt、artifact id、caller/workflow SHA 传给 `aggregate.py`。
- `aggregate.py` 负责先验证历史 producer 实际字节和 guard，再调用 `replay_receipts`/`evaluate_round`，生成当前 terminal envelope；3a 已完成的本 run receipt 只作为 replay 输入，不在 3b 重新定义写出格式。receipt listing/download/replay 任一步失败都 fail-loud，不能静默降级成 first run。
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
| invalid/stale disposition | 修改 head/diff、错误 audit digest、unknown id、过期、撤销、普通评论或 label | receipt 只留下诊断；无论是否 current-target 都不改变 P1、clean streak 或 Required Check；普通评论永不改变 Required Check |
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
2. disposition 绑定校验或 receipt 记录测试未通过时，禁止将 receipt claim 写成 resolved/approved；Required Check 仍只看 canonical primary 与 quality。
3. 增量 3 的静态 contract、payload boundary 和真实 canary 任一未通过，不能把 `gate/gate` 标为已收敛；先回滚到旧 fail-closed aggregator，保留 immutable receipts 供调查，不删除 evidence。
4. canary 后发现 receipt 状态影响 primary P1、clean streak 或 Required Check，立即关闭 convergence consumer/feature pin；不得用空 commit、label、admin bypass 或手改评论补洞。

本设计的收敛判据仍是：**只对当前 epoch 的 canonical primary 做本轮 P1 机械判定；disposition receipt 仅为绑定到具体 finding 的审计主张，任何 receipt 均无当前轮授权；receipt 校验和读取失败不进入 Required Check 判定；跨轮只重放 convergence immutable artifact，不把 GitHub 评论当可变状态。**
