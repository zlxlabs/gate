verdict: pass

# review gate PR #122 第 2 轮（H0..H1 增量四问 + 账本消费端跨 rerun 连续性）

- 审查对象（冻结，禁止用分支名）：
  - 增量 `H0..H1` = `8943d2861d7ed1b4ae10e3927b21baa06b2369e9..10fa027227c577326bc0c2d32a6f710447e6f996`
  - 全量 `base..H1` = `55f31f18c431af7f4b9e25f3182ee12ac1c9c2e3..10fa027227c577326bc0c2d32a6f710447e6f996`
- spec：卡面 S1–S5 + S4 行为矩阵；被修缺陷 = PR #119 短路后 ledger Build 仍跑、preflight 缺失、`_review_summary` 对 `kind == "primary_review"` 强制 coverage 形状，账本行写不出
- 风险档：gate `risk-tier: personal`；失败路径/资源账本按 infra 例外提档 internal（连续 2 轮无新增 P1）
- 执行器：grok（dispatch `dlg-20260902-141559-c486a2`）；模型 grok-4.6
- OCR：`status=reviewed` profile=minimax MiniMax-M3；1 条 severity=low（见 Findings，本仓判 P3 backlog）

## 本轮新证据（第 1 轮未做）

1. H0..H1 增量四问：`git diff --name-only` + 对照登记四条，逐条 `文件:行`
2. 生产 `main()` 三次真跑混合序列（只 no-op `fetch_prior_entries` / `fetch_comments` / `post_state_comment`），三行 jsonl 原文
3. 短路行上 `dedupe_entries` / `comparison` / `review_round` / `convergence_projection` / `finding_dispositions` 源码行 + 真跑输出
4. `render_state_comment` 对 `coverage: null` 真跑渲染
5. 解析器跨 attempt 五格 + `build_entry` 在 short=true 下的 `terminal_source_attempt`
6. H0..H1 新增三条断言的变异注入（各红）+ 4×4 正向对照收窄 `RESULT_DOMAIN` 非恒真
7. base..H1 熵增对照 `templates/REFACTOR-guide.md` 坏味道词表

---

## A. H0..H1 增量四问

`git diff --name-only H0..H1` 只有：

- `tests/test_gate_v2_contract.py` (+27/−2)
- `tests/test_review_ledger.py` (+28/−5)

生产文件零改动。

① **只修了登记在案的四条（含可选项）——是。**

| 登记项 | 落点 |
|---|---|
| 1. 删 `MODULE_PATH.read_text()` 恒真断言，改 argparse 真注册 | `tests/test_review_ledger.py:433-450` 新增 `test_input_short_circuited_option_is_registered_on_parser`；原 `assert "--input-short-circuited" in MODULE_PATH.read_text(...)` 已从 `test_main_short_circuited_missing_preflight_writes_jsonl` 删除 |
| 2. RESULT_DOMAIN 4×4 正向对照（反例组保留） | `tests/test_gate_v2_contract.py:1204-1222` `test_ledger_resolver_result_domain_accepts_legal_values`；反例组 `test_ledger_resolver_result_domain_rejects_illegal_values` 仍在 `:1177-1201` |
| 3. 非法值断言完整固定文案 | `tests/test_review_ledger.py:516` `assert exc.value.code == "--input-short-circuited must be one of true, false"` |
| 4.（可选项）Build 步恰好一个匹配 | `tests/test_gate_v2_contract.py:1146-1151` `assert len(matches) == 1` |

② **未新增未经批准的抽象。** 无新类、无包装层、无配置项、无第二条判定路径。H0..H1 只加测试与 `import argparse`。

③ **状态 / 事实源 / fallback 无依据增加。** 增量不碰生产；无新 fallback / 重试 / 防御式 catch。

④ **未留下双路径。** 解析器注册探测从「源码文本包含字符串」换成「`ArgumentParser._actions` 的 option_strings」，旧恒真断言已删，不是两条并行。

增量审通过，不计入新增 P1。

---

## B. 账本消费端跨 rerun / 跨 attempt

### B.1 混合序列：同一 PR 上 `main()` 连跑三次

网络函数 no-op：`fetch_prior_entries` 返回上一轮写出的 jsonl 反序列化结果（模拟 artifact 累计）；`fetch_comments` 返回一条 `security.leak = false-positive` disposition；`post_state_comment` 只捕获 `render_state_comment` 产物。

三次 CLI 都走生产 `module.main()`：

1. run_id=100 attempt=1 head=`head-aaa` 合法 preflight diff_lines=120，无 `--input-short-circuited`（缺省=false），findings=`[correctness.bad-state]`
2. run_id=101 attempt=1 head=`head-bbb` 缺失 preflight + `--input-short-circuited true`，findings=`[correctness.bad-state, security.leak]`
3. run_id=102 attempt=1 head=`head-ccc` 合法 preflight diff_lines=80，缺省 false，findings=`[security.leak, perf.n-plus-one]`

三次 `rc=0`。写出的 `ledger.jsonl` 原文三行：

```jsonl
{"comparison": {"kind": "first_review"}, "convergence_projection": {"required_gate_effect": "none", "source": "disposition-observation", "statuses": {}}, "false_positive_count": 0, "finding_dispositions": {}, "head_sha": "head-aaa", "install": null, "pr_number": 7, "preflight": {"classification": "single", "diff_lines": 120, "review_plan": "single", "thresholds": {"single_turn_lines": 4000}}, "primary_identity": {"base_sha": "base", "caller_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "candidate_commit_sha": "candidate-commit", "candidate_tree_sha": "candidate-tree", "diff_digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "head_sha": "head-aaa", "job_id": 99, "merge_base_sha": "merge-base", "policy_digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "policy_version": "v1", "pr": 7, "registry_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "repository": "zlxlabs/app", "repository_id": 123, "reusable_workflow_sha": "cccccccccccccccccccccccccccccccccccccccc", "reviewer": "codex-sub", "run_attempt": 1, "run_id": 100, "run_mode": "PAYLOAD_ONLY"}, "recorded_at": "2026-09-02T14:21:21.694981+00:00", "repository": "zlxlabs/app", "review": {"attempts": [{"cost_usd": 0, "diag_snippet": null, "duration_s": 9.5, "exit_code": 0, "reason": "", "reviewer": "codex-sub"}], "category_counts": {"correctness": 1}, "cost_usd": 0.01, "coverage": {"complete": true, "diff_lines": 120, "mode": "single", "shards": 1}, "failover": false, "finding_count": 1, "finding_ids": ["correctness.bad-state"], "inferred_p1_count": 0, "result": {"findings": [{"category": "correctness", "id": "correctness.bad-state", "severity": "major", "trigger_kind": "measured"}], "summary": "result", "verdict": "fail"}, "reviewer": "codex-sub", "runtime": {"duration_s": 9.5}, "severity_counts": {"major": 1}, "shadows": {}, "status": "fail", "tokens": [{"input": 3}], "trigger_kind_counts": {"measured": 1}, "verdict": "fail"}, "review_round": 1, "run_attempt": 1, "run_id": 100, "schema_version": 1}
{"comparison": {"kind": "new_head", "new_finding_ids": ["security.leak"], "persistent_finding_ids": ["correctness.bad-state"], "previous_head_sha": "head-aaa", "previous_run_id": 100, "resolved_finding_ids": []}, "convergence_projection": {"required_gate_effect": "none", "source": "disposition-observation", "statuses": {"security.leak": {"reason": "owner ack", "status": "false-positive"}}}, "false_positive_count": 1, "finding_dispositions": {"security.leak": {"author": "owner", "disposition": "false-positive", "reason": "owner ack", "recorded_at": "2026-09-02T00:00:00Z", "url": "https://example.test/disposition"}}, "head_sha": "head-bbb", "install": null, "pr_number": 7, "preflight": null, "primary_identity": {"base_sha": "base", "caller_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "candidate_commit_sha": "candidate-commit", "candidate_tree_sha": "candidate-tree", "diff_digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "head_sha": "head-bbb", "job_id": 99, "merge_base_sha": "merge-base", "policy_digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "policy_version": "v1", "pr": 7, "registry_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "repository": "zlxlabs/app", "repository_id": 123, "reusable_workflow_sha": "cccccccccccccccccccccccccccccccccccccccc", "reviewer": "codex-sub", "run_attempt": 1, "run_id": 101, "run_mode": "PAYLOAD_ONLY"}, "recorded_at": "2026-09-02T14:21:21.696345+00:00", "repository": "zlxlabs/app", "review": {"attempts": [{"cost_usd": 0, "diag_snippet": null, "duration_s": 9.5, "exit_code": 0, "reason": "", "reviewer": "codex-sub"}], "category_counts": {"correctness": 1, "security": 1}, "cost_usd": 0.01, "coverage": null, "failover": false, "finding_count": 2, "finding_ids": ["correctness.bad-state", "security.leak"], "inferred_p1_count": 1, "result": {"findings": [{"category": "correctness", "id": "correctness.bad-state", "severity": "major", "trigger_kind": "measured"}, {"category": "security", "id": "security.leak", "severity": "blocker", "trigger_kind": "inferred"}], "summary": "result", "verdict": "fail"}, "reviewer": "codex-sub", "runtime": {"duration_s": 9.5}, "severity_counts": {"blocker": 1, "major": 1}, "shadows": {}, "status": "fail", "tokens": [{"input": 3}], "trigger_kind_counts": {"inferred": 1, "measured": 1}, "verdict": "fail"}, "review_round": 2, "run_attempt": 1, "run_id": 101, "schema_version": 1}
{"comparison": {"kind": "new_head", "new_finding_ids": ["perf.n-plus-one"], "persistent_finding_ids": ["security.leak"], "previous_head_sha": "head-bbb", "previous_run_id": 101, "resolved_finding_ids": ["correctness.bad-state"]}, "convergence_projection": {"required_gate_effect": "none", "source": "disposition-observation", "statuses": {"security.leak": {"reason": "owner ack", "status": "false-positive"}}}, "false_positive_count": 1, "finding_dispositions": {"security.leak": {"author": "owner", "disposition": "false-positive", "reason": "owner ack", "recorded_at": "2026-09-02T00:00:00Z", "url": "https://example.test/disposition"}}, "head_sha": "head-ccc", "install": null, "pr_number": 7, "preflight": {"classification": "single", "diff_lines": 80, "review_plan": "single", "thresholds": {"single_turn_lines": 4000}}, "primary_identity": {"base_sha": "base", "caller_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "candidate_commit_sha": "candidate-commit", "candidate_tree_sha": "candidate-tree", "diff_digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "head_sha": "head-ccc", "job_id": 99, "merge_base_sha": "merge-base", "policy_digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "policy_version": "v1", "pr": 7, "registry_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "repository": "zlxlabs/app", "repository_id": 123, "reusable_workflow_sha": "cccccccccccccccccccccccccccccccccccccccc", "reviewer": "codex-sub", "run_attempt": 1, "run_id": 102, "run_mode": "PAYLOAD_ONLY"}, "recorded_at": "2026-09-02T14:21:21.698130+00:00", "repository": "zlxlabs/app", "review": {"attempts": [{"cost_usd": 0, "diag_snippet": null, "duration_s": 9.5, "exit_code": 0, "reason": "", "reviewer": "codex-sub"}], "category_counts": {"performance": 1, "security": 1}, "cost_usd": 0.01, "coverage": {"complete": true, "diff_lines": 80, "mode": "single", "shards": 1}, "failover": false, "finding_count": 2, "finding_ids": ["perf.n-plus-one", "security.leak"], "inferred_p1_count": 1, "result": {"findings": [{"category": "security", "id": "security.leak", "severity": "blocker", "trigger_kind": "inferred"}, {"category": "performance", "id": "perf.n-plus-one", "severity": "minor", "trigger_kind": "measured"}], "summary": "result", "verdict": "fail"}, "reviewer": "codex-sub", "runtime": {"duration_s": 9.5}, "severity_counts": {"blocker": 1, "minor": 1}, "shadows": {}, "status": "fail", "tokens": [{"input": 3}], "trigger_kind_counts": {"inferred": 1, "measured": 1}, "verdict": "fail"}, "review_round": 3, "run_attempt": 1, "run_id": 102, "schema_version": 1}
```

### B.2 跨行逻辑在短路行上不塌

**去重键** — `build_ledger.py:713`：`key = (entry.get("repository"), entry.get("run_id"), entry.get("run_attempt"))`。不读 `preflight` / `coverage`。

真跑：`keys=[('zlxlabs/app', 100, 1), ('zlxlabs/app', 101, 1), ('zlxlabs/app', 102, 1)]`，`unique=True`，`ledger_conflict` 均无。人为把正常行改成与短路行同一 `(repo, run_id, run_attempt)` 再 `dedupe_entries`：`deduped_count=2`，两条都打上 `variant_count=2` 的 conflict marker——说明短路行缺 preflight 不会让键塌掉，只在真正同键异 payload 时走既有 conflict 路径。

**comparison / finding_ids 差分** — `build_ledger.py:621-641`：`current_ids = set(review["finding_ids"])`，`previous_ids` 来自 `previous["review"]["finding_ids"]`。finding_ids 在 `_review_summary:294` 从 audit findings 投影，短路格不跳过。

真跑：

- 行 2（短路，上一条正常）：`kind=new_head`，`persistent=["correctness.bad-state"]`，`resolved=[]`，`new=["security.leak"]`，`previous_run_id=100`
- 行 3（正常，上一条短路）：`kind=new_head`，`persistent=["security.leak"]`，`resolved=["correctness.bad-state"]`，`new=["perf.n-plus-one"]`，`previous_run_id=101`

短路行的 audit finding_ids 照常参与差分；下一行也以短路行为 previous。

同 run 跨 attempt 再跑一次（run_id=200 attempt1 正常 → attempt2 短路，同一 head）：attempt2 `comparison.kind=same_head_rerun`，`persistent=["correctness.bad-state"]`，`appeared=["security.leak"]`，`missing=[]`。`first_review` 只出现在序列首行；`prior_conflict` 本序列未触发（无 `ledger_conflict` previous）。

**review_round** — `build_ledger.py:666`：`len({(run_id, run_attempt) for entry in relevant}) + 1`。不读 coverage/preflight。真跑：1 → 2（短路）→ 3；同 run attempt1→2 为 1 → 2。连续。

**convergence_projection / finding_dispositions** — `build_ledger.py:642-657`：只扫 `dispositions` 与 `review.finding_ids`。块内零 `coverage` / `preflight`。真跑短路行：`finding_dispositions` 含 `security.leak`（来自 comment），`convergence_projection.statuses.security.leak.status=false-positive`，`false_positive_count=1`。未因 `coverage: null` 变空或抛。

### B.3 `render_state_comment` 对 `coverage: null`

源码 `build_ledger.py:135-169`：人类可见字段是 `head_sha` / `review_round` / `review.status` / `finding_count` / `reviewer` / `comparison`。不读 `coverage` 或 `preflight`。`json.dumps(relevant)` 把 `coverage: null` 编进游标，`None` → JSON `null`，不抛。

对混合序列以行 2（短路）为 `current` 真跑，`RENDER_OK`。人类可见输出：

```
<!-- codex-review-ledger-state:v2 -->

### ⚙️ Review ledger state（机器状态记录，非评审结论）

> 这是 review ledger 的**机器状态记录**，不代表评审结论，通常无需任何操作。

<details><summary>机器状态明细</summary>

- Commit: `head-bbb`
- Round: **2**
- Status / findings: **fail / 2**
- Reviewer: **codex-sub**
- Comparison: `new_head; persistent/resolved/new = 1/0/1`

完整数据保存在 `codex-review-ledger-v2` artifact；此 sticky comment 仅保存 v2 epoch 的跨 rerun 连续游标。

</details>
```

`coverage` 不出现在人类可见行（`coverage_in_human_lines=False`）。解码游标 3 行：`cursor_coverages=[{complete: True, diff_lines: 120, mode: single, shards: 1}, None, {complete: True, diff_lines: 80, mode: single, shards: 1}]`，`cursor_preflights_none=[False, True, False]`。生产 `post_state_comment`（`build_ledger.py:815-817`）在 `len(relevant)>1` 时才会发评论，短路行是 round 2，会进入渲染路径。

### B.4 跨 attempt：`terminal_source_attempt` / `input_source_attempt`

解析器（`gate-v2.yml`）与 `build_ledger.py` 对照，五格真跑 `_run_ledger_resolver`（抽的是 H1 workflow 内嵌 Python）：

| 场景 | QUALITY/PRIMARY | artifacts | 输出 |
|---|---|---|---|
| attempt1 有 input，attempt2 短路 | skipped/failure | input@1 + terminal@1,2 + audit@1,2 | `input_source_attempt=1` `input_short_circuited=false` `audit_source_attempt=2` `terminal_source_attempt=2` |
| 两 attempt 都无 input | skipped/failure | terminal@1,2 | `input_artifact_id=` `input_source_attempt=` `input_short_circuited=true` `terminal_source_attempt=2` |
| attempt1 无 input，attempt2 正常 | success/success | input@2 + terminal@1,2 | `input_source_attempt=2` `input_short_circuited=false` `terminal_source_attempt=2` |
| 当前 attempt2 短路且无任何 input | skipped/failure | terminal@2 | `input_short_circuited=true` `terminal_source_attempt=2` |
| attempt2 短路、复用 attempt1 terminal | skipped/failure | terminal@1 only | `input_short_circuited=true` `terminal_source_attempt=1` |

公式在 `gate-v2.yml:1361`：`input_short_circuited={'true' if quality_short_circuited and input_artifact is None else 'false'}`。`select_artifact`（`:1223-1244`）按 `attempt <= current` 取最大，所以 attempt2 短路但 attempt1 留下 input 时**不**置短路线——复用先前 preflight，走 S4「合法 preflight → 算 coverage」格。这与 #119「input/audit/terminal 可复用更早 attempt」一致，不是嗅探 preflight 形状（S1）。

`build_ledger.py:693-696`：`terminal_source_attempt` 只在 `terminal_envelope.run_attempt != run_attempt` 时写入，与 `input_short_circuited` 正交。真跑 `build_entry(..., input_short_circuited=True, preflight={}, audit=None, terminal.run_attempt=1, current run_attempt=2)`：`terminal_source_attempt=1`，`coverage is None`，`preflight is None`；同 attempt 终端则字段缺席。短路格下归属逻辑仍成立。

---

## C. 变异验证

注入前均 `sed -n '<行>p'` 留痕；注入后跑目标测试；每条结束后 `git checkout` 还原。收工工作树除 verdict 外干净。

### C.1 删 argparse 注册行 → 必修 1 必须红

- 改前 `build_ledger.py:898`：`parser.add_argument("--input-short-circuited", default=None)`
- 改后同位置变成 `args = parser.parse_args()`
- `tests/test_review_ledger.py::test_input_short_circuited_option_is_registered_on_parser` **FAILED**：`assert '--input-short-circuited' in captured`，captured 只剩 `-h/--help/--audit-path/...`

### C.2 `primary_result.lower() not in RESULT_DOMAIN` → 反例组 SUCCESS 必须红

- 改前 `gate-v2.yml:1208`：`if primary_result not in RESULT_DOMAIN:`
- 改后：`if primary_result.lower() not in RESULT_DOMAIN:`
- `test_ledger_resolver_result_domain_rejects_illegal_values[PRIMARY_RESULT-SUCCESS]` **FAILED**：`assert "must be one of" in combined`，实际 stderr 是 `No matching required ledger input artifact found...`（SUCCESS 被当成 success 过了域检查）

### C.3 SystemExit 文案改一个字 → 必修 3 必须红

- 改前 `build_ledger.py:903`：`raise SystemExit("--input-short-circuited must be one of true, false")`
- 改后：`... true, False`
- `test_input_short_circuited_illegal_value_is_fail_loud[yes]` **FAILED**：`false` vs `False` 逐字不等

### C.4 4×4 正向对照非恒真

- 改前 `gate-v2.yml:1205`：`RESULT_DOMAIN = {"success", "failure", "cancelled", "skipped"}`
- 改后：`RESULT_DOMAIN = {"success"}`
- `[success-failure]` / `[failure-success]` / `[cancelled-skipped]` **FAILED**（`QUALITY_RESULT`/`PRIMARY_RESULT must be one of...`，returncode=1）
- `[success-success]` **PASSED**（对照格仍绿，说明正向组约束的是值域本身，不是恒真）

三条新断言都能红；正向 4×4 有约束力。

---

## D. 熵增审查（base..H1，对照坏味道词表）

| 新增 | 词表 | 裁决 |
|---|---|---|
| `_review_summary(..., *, input_short_circuited: bool = False)` | 非「投机通用性」：S4 第三格的唯一开关 | 保留 |
| `build_entry(..., input_short_circuited=False)` 透传 | 非转发-only 包装层，无新类 | 保留 |
| CLI `--input-short-circuited` + action input + workflow `with:` 一行 | S2 规定的三跳，有三个生产消费者 | 保留 |
| `_review_summary` 一格 `if input_short_circuited and (preflight is None or preflight == {})` | 不是第二条判定路径：布尔选格，空 preflight 是矩阵单元格条件（S1/S4；第 1 轮已排除空 dict 嗅探） | 保留 |
| `default=None` 再分三支 vs `default="false"` | 词表 4「多余路径」 | 第 1 轮已登记 P3 backlog，本轮不重开 |
| 测试 helper `_short_circuit_fail_audit` / `_assert_short_circuit_audit_projection` / `_run_ledger_main` / argparse `grab` | 只锁契约，无生产消费者要求 | 保留 |
| `docs/sessions/260902-gate-triage/progress/gate-121-progress.md` | 文档，非抽象 | 保留 |

无新无消费者导出、无镜像事实源、无单实现接口、无 fallback/重试。H0..H1 净增测试强度，生产零熵。

---

## Findings

| ID | 摘要 | 违反 spec | 工具标注 | 本仓判定 | P1 两问 |
|---|---|---|---|---|---|
| OCR-1 | `test_review_ledger_action_declares_and_forwards_input_short_circuited` 的 `"${x:-"` / `":-}"` 守卫盖不住 bash `${VAR-default}` | 无法溯源到 S1–S5（测试守卫形态）；且同测试已断言 `"${{ inputs.input-short-circuited }}" in run` | OCR severity=low，verify unverified（复核超时） | **P3 backlog**，不阻塞 | ① 量过：`action.yml:52` 生产行是 `--input-short-circuited "${{ inputs.input-short-circuited }}"`，没有 bash 默认值；该测试洞不会在当前真实路径写出错误账本。② 不触发则无后果。两问不过，不是 P1。 |

本轮 **新增 P1 = 0**。第 1 轮两条 P3（`if not audit` 早退断言不足；`default=None` 三分支与 `default="false"` 等价）仍是 backlog，不重开。

## 收敛判定

- 第 1 轮：pass，P1=0
- 第 2 轮（本轮，换证据源：增量四问 + 消费端跨 rerun/attempt + 变异）：pass，**新增 P1 = 0**
- infra 例外按 internal：连续 2 轮无新增 P1 → **满足收敛条件**
