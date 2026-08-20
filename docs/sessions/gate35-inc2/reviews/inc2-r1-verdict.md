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

