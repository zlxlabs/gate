FAIL

审查对象：`1d6a2f05b756052e77c155cf1f9db8fb156cefde..e9fed77adf1e0f9cb61221adef8f05544b8b4c02`

审查时间：2026-08-20 17:54（Asia/Shanghai）  
执行器与模型：Codex / GPT-5（`delegate --class big`）  
风险等级：未声明，按 internal；本轮按 infra/状态机类 diff 的 saas 收敛条件审查。  
审查范围：只审冻结 SHA 范围内 8 个文件的改动；未通电的增量 3 路径列入 backlog，不作为本轮 finding。

本轮新证据：读取了冻结范围内的 disposition control workflow、receipt producer、convergence consumer 的调用链；以 canonical audit fixture 验证 workflow 对 `.epoch` 的读取结果；用真实 subprocess 运行新增 producer，验证任意非空 evidence ref 可以产出 receipt，并将该 receipt交给纯 reducer 验证为可消费。OCR 前置扫描：第一次输入路径错误后修正；修正版主腿持续等待后手动中止，未得到 review 结果，按 `skipped/timeout` 记录，不把它当作已扫过。

## 降层三问

### 1. 终态写入成功前的不可逆动作与残留

签发路径先在 runner 临时目录读取 audit 原始字节、计算 digest，并由 `.github/actions/gate-disposition/issue_receipt.py:188-196,199-215` 写出 immutable 文件；真正的外部不可逆动作是 `.github/workflows/gate-v2-disposition.yml:149-156` 的 artifact upload。若 upload 失败，当前实现没有外部 nonce/receipt registry，runner 临时文件随 run 消失，下一次签发不会被本地残留阻塞；同 nonce 重发依赖后续 reducer 做幂等/冲突处理。

这条路径的残留风险不是“半写文件制造 clean”：同 payload 重发由文件内容相同而 no-op；同 nonce 异 payload 只有在两个 artifact 都交给 `consume_dispositions` 时才会由 `.github/actions/gate-aggregator/convergence.py:683-707` fail-closed。artifact upload 成功但控制 run 后续失败时，artifact 仍可能存在，后续 artifact 检索必须按 artifact id 保留全部候选；这属于增量 3 的接线前置项。

### 2. 守卫值与实际部署唯一性

纯消费层的 nonce 守卫在 `.github/actions/gate-aggregator/convergence.py:683-713`：同 nonce 同 payload no-op，同 nonce 异 payload `nonce_conflict`；`concurrency.group` 在 `.github/workflows/gate-v2-disposition.yml:29-31` 只按 repository/PR/nonce 串行化同 nonce 的 control runs。artifact 名在 `.github/actions/gate-disposition/issue_receipt.py:209-214` 为 epoch、audit digest 前缀、nonce，未包含 primary run/attempt；同 nonce 异 payload 会在不同 workflow run 中拥有不同 artifact id 但同名，不能把 artifact name 当全局身份。只要增量 3 保留 artifact id 并把同 nonce 候选一起交给 reducer，冲突会停机；若按名字去重，则守卫不成立，列为增量 3 前置项。

本轮已实测一个更早的唯一性/检索问题：primary workflow 实际上传名为 `.github/workflows/gate-v2.yml:454` 的 `primary-audit-v2-<repo>-<head>-<run>-<attempt>`，而 disposition workflow 在 `.github/workflows/gate-v2-disposition.yml:65` 用缺少 run/attempt 的短名下载。`gh run download --name` 的本地帮助明确是按 artifact name 匹配，因此合法目标 run 会找不到 audit，无法进入签发。

### 3. 保护的是写入还是行为

两层代码都存在但强度不等：写入层由 environment、原始 audit bytes digest 和 `issue_receipt.py` 的 `xb` 写入保护；行为层由 `.github/actions/gate-aggregator/convergence.py:529-616,660-736,1309-1427` 保护，只有当前 scope/audit/exact P1 的 `false-positive` 会从 P1 投影移除，`accepted`/`wont-fix`/`fixed` 不会清 gate。

但当前实际 caller `.github/actions/gate-aggregator/aggregate.py:666-673` 仍固定传 `waiver_receipts=()`，且增量 3 尚未下载 disposition artifact；因此行为保护尚未接到真实 Required Check。这个未通电事实按任务卡列为增量 3 backlog，不作为本轮 finding。写入层另外有两个当前 diff 内的缺口：evidence ref 只做字符串非空与哈希，issuer/approval ref 不做真实授权绑定，详见 findings。

## 12 条不变式核对表

| # | 结论 | 代码在哪 | 哪个测试锁死 |
|---|---|---|---|
| 1 | 成立（行为层） | `.github/actions/gate-aggregator/convergence.py:614-616` 仅 `false-positive` 可 consumable；`:720-729,1399-1407` 只按剩余 P1 算 streak | `test_only_false_positive_resolves_matching_current_finding`；`test_rejected_disposition_cannot_advance_streak`；`test_disposition_nonce_is_idempotent_and_conflict_safe` |
| 2 | 成立（消费层） | `.github/actions/gate-aggregator/convergence.py:590-593` 要求 finding 在当前 `primary.p1_ids`；`.github/actions/gate-disposition/issue_receipt.py:149-153` 要求 canonical finding 唯一且 P1 | `test_disposition_binding_rejects_head_generation_and_digest_mismatch`；`test_only_false_positive_resolves_matching_current_finding` |
| 3 | 成立 | `.github/actions/gate-disposition/issue_receipt.py:109-116` 从 audit 原始 UTF-8 bytes 重算并拒绝 dispatch digest 不一致；workflow `:68,136` 传入该重算值 | `test_disposition_producer_writes_bound_receipt_bytes_from_raw_audit`；`test_audit_digest_is_raw_bytes_digest` |
| 4 | 不成立（且未完整覆盖） | `.github/actions/gate-aggregator/convergence.py:194-218` 的 digest payload 没有完整 scope 字段，producer `:164-184` 接受调用方/审计值形式的 epoch；消费时虽在 `:577-583` 与当前 scope 比较 epoch/head/diff，但 control job 没有独立重算 scope | `test_disposition_requires_protected_issuer_and_exact_digest_binding` 只锁部分字段；无覆盖 base/policy/tier/classifier 全 scope 的 receipt binding |
| 5 | 成立（消费层） | `.github/actions/gate-aggregator/convergence.py:584-589` 绑定 primary run/attempt/audit digest，新的 primary round 不会匹配旧 receipt | `test_only_false_positive_resolves_matching_current_finding`；`test_disposition_binding_rejects_head_generation_and_digest_mismatch` |
| 6 | 成立（消费层；跨 artifact 唯一性待增量 3） | `.github/actions/gate-aggregator/convergence.py:683-713` 实现同 nonce 幂等与异 payload 冲突；workflow `:29-31` 同 nonce 串行 | `test_disposition_nonce_is_idempotent_and_conflict_safe` |
| 7 | 成立 | `.github/actions/gate-aggregator/convergence.py:508-525,607-611` 只读 revocation index；producer `:219-236` 以独立 revocation artifact append-only 写入 | `test_revocation_is_append_only_and_recomputed`；`test_disposition_revocation_producer_is_append_only` |
| 8 | 不成立（旧 epoch 分流未锁死） | 当前 target 的 malformed/mismatch 会在 `.github/actions/gate-aggregator/convergence.py:640-652,720-730` fail-closed；但 old epoch 也被归入 `epoch_mismatch_stale`，而不是在 consumer 层排除并仅保留 stale 诊断 | `test_disposition_binding_rejects_head_generation_and_digest_mismatch` 只断言 reason；无“旧 epoch 不参与、缺 receipt 才普通 blocked”的跨 artifact 测试 |
| 9 | 不成立 | `.github/workflows/gate-v2-disposition.yml:79-94` 只检查 approval ref 非空及 `maintainer:` 前缀，不查询 ref 对应审批人/maintainer，也不校验 issuer 是否 PR author/committer/reviewer/普通评论者；producer `issue_receipt.py:129-132` 只接受字符串 | 无对应 workflow actor/approval provenance 回归测试；现有 `test_disposition_requires_protected_issuer_and_evidence` 只测 receipt 字段形状 |
| 10 | 成立 | `.github/actions/review-ledger/build_ledger.py:455-479` 只新增 `convergence_projection`，并显式标 `required_gate_effect: none`；无 evaluator 读取路径 | `test_convergence_projection_is_observational_only` |
| 11 | 成立（代码检查；专门禁清单测试不足） | `.github/actions/review-ledger/build_ledger.py:112-150` comment 只渲染 commit/round/status/comparison；新增 projection 不进入 comment 的机器判定输入 | `test_state_comment_folds_machine_details_behind_human_navigation` 只部分锁 comment 形态；未找到逐项禁止清单测试 |
| 12 | 成立（增量 1 规则未被重写） | `.github/actions/gate-aggregator/convergence.py:1331-1427` 仅把当前 round 的剩余 P1 接入既有计数，epoch/idempotency/index 逻辑仍在 `:1224-1307,1566-1621` | `test_all_state_event_cells_are_callable`；`test_duplicate_round_is_idempotent_and_conflicting_payload_fails_closed`；`test_scope_digest_change_starts_zero_generation` |

## Findings

### F-1 — P1：control workflow 使用了不存在的 canonical audit artifact 名称

- 严重级别：P1
- 文件：`.github/workflows/gate-v2-disposition.yml:63-66`；真实 producer 名称为 `.github/workflows/gate-v2.yml:454`
- 违反 spec：§2.3 轴 C“签发申请/绑定”及增量 2“签发与证据契约”第 2 条；不变式 4（目标 primary run/attempt 必须绑定）。
- 具体失败场景：合法 primary run `77/1` 已上传 `primary-audit-v2-<repo>-<head>-77-1`，control job 执行 `gh run download 77 --name primary-audit-v2-<repo>-<head>`。`gh run download --help` 明确 `--name` 按名称匹配，短名不会匹配带 run/attempt 后缀的 artifact；`set -euo pipefail` 使 job 在签发前退出，不能生成任何 receipt。
- 建议修法：按目标 run/attempt 构造完整 artifact name，或用 API listing 过滤 exact repository/PR/head/run/attempt 后按 artifact id 下载；下载后仍须保留 source attempt 和原始 bytes digest。

### F-2 — P1：正常 canonical audit 缺少 `.epoch` 时，工作流会签发不可消费的 receipt

- 严重级别：P1
- 文件：`.github/workflows/gate-v2-disposition.yml:71-77`；`.github/actions/gate-disposition/issue_receipt.py:118-120`；`.github/actions/gate-aggregator/convergence.py:577-579,640-652,726-730`
- 违反 spec：增量 2“签发与证据契约”第 2 条（重新获取并绑定当前 policy scope）；不变式 4、8。
- 具体失败场景：当前仓 canonical primary audit 的既有 schema 没有 `epoch` 字段；本轮探针对 `tests/data/primary_review_v1_missing_runtime.json` 得到 `audit_has_epoch=False`、`workflow_jq_epoch=null`。workflow 把字符串 `null` 写进 receipt；producer 的 `_safe_component` 接受它。随后 reducer 对当前 `Scope` 重算出的 64 位 epoch，返回 `validate_reason=epoch_mismatch_stale`、`consume_fail_closed=True`。control job 可能显示签发成功，但该 exact false-positive 永远不能解除当前 P1，反而把状态推入 fail-closed。
- 建议修法：control job 必须独立获取并校验 base/head/diff/policy/tier scope，调用同一 canonical `derive_epoch(scope)`；缺字段或 scope 不一致直接拒绝签发，不从 audit 中盲读可选 `.epoch`。

### F-3 — P1：evidence ref 没有被读取或验证，任意字符串即可成为可消费授权

- 严重级别：P1
- 文件：`.github/workflows/gate-v2-disposition.yml:79-89`；`.github/actions/gate-disposition/issue_receipt.py:154-163`
- 违反 spec：增量 2“签发与证据契约”第 3 条；不变式 1、4、9。
- 具体失败场景：输入 `--evidence-ref not-an-immutable-reference`，producer 仅将字符串加入 list 并计算 list 的 SHA-256，从不读取引用、确认 blob/commit/artifact 是否存在，也不校验引用对应的内容摘要。本轮真实 subprocess 探针输出：`returncode=0`、`evidence_refs=['not-an-immutable-reference']`；将生成的 payload 转成 `DispositionReceipt` 后，reducer 输出 `status=(True, True, True, 'active_false_positive')`、`remaining_p1_ids=()`、`fail_closed=False`。因此没有证据的 P1 也能改变 required decision。
- 建议修法：把 evidence manifest 规范化为带类型、不可变定位符和内容 digest 的记录；control job 对每一项执行 allowlisted read/存在性/bytes digest 校验，任一缺失、不可读或摘要不一致都不得生成可消费 receipt。

### F-4 — P2：approval ref 与 issuer provenance 只是可伪造字符串

- 严重级别：P2
- 文件：`.github/workflows/gate-v2-disposition.yml:79-94`；`.github/actions/gate-disposition/issue_receipt.py:129-132`
- 违反 spec：增量 2“签发与证据契约”第 1、4 条；不变式 9。
- 具体失败场景：PR author 为 `alice`、dispatch actor 也是 `alice` 时，输入 `approval_ref=maintainer:fake` 即可通过唯一的自批检查；代码没有查询该 ref，也没有证明它属于另一名 maintainer。对非 PR author 的 committer/reviewer/普通有写权限用户，代码甚至不做角色检查。receipt 最终只记录 actor 字符串和未经验证的 approval ref，无法证明 protected environment 的实际批准人就是另一名 maintainer。环境审批仍可能阻止部分未授权 run，但不能弥补 artifact 内缺失的 provenance。
- 建议修法：从 GitHub protected environment/deployment 或经 API 校验的 approval ref 取得实际 approver login/user id/时间，要求 approver 是 maintainer 且与 PR author/committer/reviewer/dispatch actor 按契约区分；receipt digest 绑定该已验证记录。不要用 `maintainer:` 前缀作授权判断。

### F-5 — P2：receipt digest 不承载完整 scope，control job 也未独立证明 scope

- 严重级别：P2
- 文件：`.github/actions/gate-aggregator/convergence.py:194-218,437-443`；`.github/actions/gate-disposition/issue_receipt.py:164-184`
- 违反 spec：§2.3 轴 C“绑定与输出”；增量 2“签发与证据契约”第 2 条；不变式 4。
- 具体失败场景：同一 head 下 PR base、policy version/digest、tier 或 classifier 发生变化时，control job 只从 audit/dispatch 取 `diff_digest` 和 `epoch`，producer 仍能写出一个自洽的 `receipt_digest`；receipt payload 本身没有 base/policy/tier/classifier 等完整 scope 字段。后续 reducer 只能在拿到当前 scope 时用 opaque epoch 判 mismatch，结果是 stale/manual/fail-closed，不能把签发时绑定的完整 scope 作为可审计事实重算。
- 建议修法：control job 先形成唯一 canonical Scope 并重算 epoch；receipt payload/digest 明确携带 scope 或 scope digest 的全部字段，消费端验证 receipt 的 scope 与当前 scope 完全相等。补齐 base/policy/tier/classifier 变更矩阵测试。

## Backlog（不计入本轮 P1）

### 存量/既有边界

- 本仓未声明 `risk-tier`（open issue #75），按任务卡已采用 internal；不属于本次 diff 的实现 finding。
- 现有 artifact/ledger 历史检索的 retention、浅 checkout 等问题（open issue #38、#63）不在冻结 diff 内，本轮不计 P1。

### 增量 3 前置项

- `.github/actions/gate-aggregator/aggregate.py:666-673` 仍传 `waiver_receipts=()`；真实 Required Check 尚未从 GitHub artifact 下载 protected receipt 并交给 reducer。
- `.github/actions/gate-aggregator/convergence.py:1570-1617` 的 `replay_receipts` 只重放 canonical primary receipts，未定义 disposition artifact 的下载、解析、排序与传入方式；增量 3 必须补齐，且不得把 ledger projection 当恢复源。
- artifact listing 必须按 exact repository/PR/head/epoch/digest 过滤并保留 artifact id；同名 artifact 不能按 name 去重。旧 epoch artifact 只保留 stale 诊断，不得交给 `consume_dispositions` 触发当前 epoch 的 fail-closed；缺 receipt 才是 active P1 的普通 blocked。
- 需要真实 dispatch/canary 证明：签发、撤销、同 nonce 同 payload 重放、同 nonce 异 payload 冲突、expiry、evidence revoke、new epoch，以及 `gate/gate` 的 red/manual/green 终态。以上是增量 3 的“真实入口”验收，不把本轮 contract test 当作已通电。

## 越界意见

没有把“尚未通电的 aggregator artifact 下载、真实 `gate/gate` 变绿、state 外部重放、control workflow canary”列为本轮 finding；它们按设计文档 §3 的串行拆卡约束保留在增量 3 backlog。F-1 至 F-3 是当前 diff 内 control/producer 代码本身即可确定的失败路径，不依赖真实 workflow run。

## 探针与测试说明

本卡未运行测试套件，符合任务卡要求；只运行了只读/临时探针：`gh run download --help`（确认 `--name` 是按名称匹配）、canonical audit fixture 的 `.epoch` 检查，以及真实 subprocess producer/consumer 探针。临时文件仅位于 `/tmp`，不纳入提交。
