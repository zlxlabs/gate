# gh599-defect3-panel review-d2-r2 verdict

## 结论

结论：PASS。被审仓 risk-tier 为 personal，本轮没有 P1，发现 1 条 P2 测试覆盖缺口；生产判定和当前对外文案行为正确，P2 不改变合并判定，可由主脑分诊接受不修或补测。

固定范围（均为 40 位 SHA）：

- 全量：6c6542473be97d8c4bffc5c4b09db86bcf9c91ca..86892856c2d54d0a92cde6a033106aad18ff8988
- 增量：6505511b6ca74763d89a0670dea96100dcac5397..86892856c2d54d0a92cde6a033106aad18ff8988
- H0：86892856c2d54d0a92cde6a033106aad18ff8988

卡面短 ref 8689285d 的实际核对输出是：

~~~text
HEAD=86892856c2d54d0a92cde6a033106aad18ff8988
BASE=6c6542473be97d8c4bffc5c4b09db86bcf9c91ca
INCREMENTAL_BASE=6505511b6ca74763d89a0670dea96100dcac5397
H0=8689285d
fatal: ambiguous argument '8689285d': unknown revision or path not in the working tree.
~~~

本轮以当前 HEAD 的 40 位 SHA 作为 H0，未以分支名替代冻结对象。

## Findings

### P2-1：预算耗尽全称判定的三个负向条件没有独立回归锁定

- 文件:行号：.github/actions/gate-aggregator/aggregate.py:1067-1079；tests/test_gate_aggregator.py:2228-2309。
- 触发路径：面板终态为 unavailable / review_unavailable / primary_unavailable，进入 _panel_action() 后由 _primary_budget_exhausted_action() 消费 primary_audit。当前参数化只有全链预算成功、混合失败（同时改 exit_code 与 reason）、空 attempts 三格；没有独立的非 unavailable verdict、bool/字符串 exit_code、或 exit_code 保持 22 但 reason 变化的负例。
- 违反 spec：不变式 2 要求 verdict、attempts 非空、严格 int exit_code、reason 精确匹配四项全称成立；不变式 6 要求断言落在实际渲染字符串上。生产代码满足条件，但测试不能分别证明其中三项仍存在。
- 实测变异：删除 verdict 守卫、删除严格整数检查、删除精确 reason 检查后，-k budget_exhaustion 都仍为 4 条全绿；删除 attempts 非空检查则 no-attempts 变红。后三条不是不可达路径，而是测试输入缺失或把两个条件绑在同一格。
- 修复方向：在现有参数化中补三格，逐格断言完整的 render_status_panel() 返回字符串回落为「当前状态：**unavailable** · **修基础设施**」：非 unavailable verdict；exit_code=True 或 "22" 且 reason 保持精确；exit_code=22 且 reason 仅多/少一个字符。不要改生产判定、verdict 取值或新增状态。

本 finding 是 P2 而非 P1：当前实现对这些输入实际回落正确，缺陷是回归测试盲区，不会直接造成数据丢失、静默放行、崩溃或错误合并结论。因此 P1 两问不适用。

## 角度 1：删减测试带走的行为

实际命令：

~~~text
git show --format=fuller --find-renames 86892856c2d54d0a92cde6a033106aad18ff8988 -- .github/actions/gate-aggregator/aggregate.py tests/test_gate_aggregator.py
git show 6505511b6ca74763d89a0670dea96100dcac5397:tests/test_gate_aggregator.py | nl -ba | sed -n '2228,2326p'
git show 86892856c2d54d0a92cde6a033106aad18ff8988:tests/test_gate_aggregator.py | nl -ba | sed -n '2228,2321p'
~~~

实际删减 diff 的关键原文：

~~~text
commit 86892856c2d54d0a92cde6a033106aad18ff8988
Author:     zj1123581321 <codex-executor@invalid>
CommitDate: Fri Sep 4 23:34:22 2026 +0800

    fix(aggregator): remove dead coverage panel branch

@@ -1078,19 +1078,6 @@ def _primary_budget_exhausted_action(primary_audit: Any) -> Optional[str]:
-    coverage = primary_audit.get("coverage")
-    coverage_fields = ("diff_lines", "reviewable_chars", "shards")
-    if isinstance(coverage, dict) and all(
-        _is_strict_int(coverage.get(field))
-        and coverage[field] >= (1 if field == "shards" else 0)
-        for field in coverage_fields
-    ):
-        return (
-            "本 PR 规模超出单次评审预算（"
-            f"{coverage['diff_lines']} 行 / {coverage['reviewable_chars']} 字符，"
-            f"需 {coverage['shards']} 个审查分片），本次未能评审完。"
-            "请拆成更小的增量 PR 后重试。"
-        )
@@ -2277,18 +2275,6 @@
-            "本 PR 规模超出单次评审预算（401 行 / 12003 字符，需 3 个审查分片），本次未能评审完。请拆成更小的增量 PR 后重试。",
-        ),
-        (
-            _budget_exhausted_audit(coverage=None),
-            "本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。",
-        ),
-        (
-            {**_budget_exhausted_audit(), "coverage": {"diff_lines": 401, "reviewable_chars": 12003, "shards": 0}},
-            "本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。",
-        ),
-        (
-            {**_budget_exhausted_audit(), "coverage": {"diff_lines": 401, "shards": 3}},
-            "本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。",
-        ),
~~~

物理 diff 删除了 4 个旧参数行，但第一个有 coverage 的全链成功格被无 coverage 的 all-legs-exhausted 格等价替换，所以净删除的是 3 个行为格：

| 被删用例 | 原来断言的事情 | 原断言数量 | 现在锁点 | 是否带走仍存在的唯一行为 |
|---|---|---:|---|---|
| all-legs-exhausted-without-coverage | 无数字拆分文案；终态三元组 unavailable / review_unavailable / primary_unavailable；正文不含 0 个审查分片 | 3 | tests/test_gate_aggregator.py:2276-2309 的 all-legs-exhausted；:2312-2320 的 Summary 测试 | 否 |
| zero-shards-falls-back | shards=0 时无数字拆分文案；同一终态三元组；正文不含 0 个审查分片 | 3 | coverage 分支已删除；当前 all-legs-exhausted 仍锁完整无数字文案、三元组和不含零分片文字 | 否；独有输入属于已删除分支 |
| missing-reviewable-chars-falls-back | 缺 reviewable_chars 时无数字拆分文案；同一终态三元组；正文不含 0 个审查分片 | 3 | 当前 all-legs-exhausted 和 Summary 测试锁定同样三件保留行为 | 否；独有输入属于已删除分支 |

被物理删除但等价替换的 all-legs-exhausted-with-coverage 原来也有 3 项断言；其中唯一独有的数字 coverage 文案随生产分支一并删除，其余两项由当前 all-legs-exhausted 保留。没有发现删减带走仍存在且无其他锁点的行为；真正留下的是 P2-1 的四条件独立负例缺口。

删减后预算测试实际输出：

~~~text
uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.21s
~~~

## 角度 2：四个条件逐条变异

每次注入前都用 sed -n 确认目标行；每次还原后清理 __pycache__ 并复跑。四次变异的实际输出如下。

### 2.1 顶层 verdict

改坏前：

~~~text
$ sed -n '1067p' .github/actions/gate-aggregator/aggregate.py
    if not isinstance(primary_audit, dict) or primary_audit.get("verdict") != "unavailable":
~~~

改坏后确认与测试：

~~~text
$ sed -n '1067p' .github/actions/gate-aggregator/aggregate.py
    if not isinstance(primary_audit, dict):
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.06s
~~~

还原、清缓存、复跑：

~~~text
$ find . -type d -name __pycache__ -prune -exec rm -rf {} +
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.20s
~~~

归因：没有非 unavailable verdict 负例；测试弱，不是路径不可达。

### 2.2 attempts 非空

改坏前：

~~~text
$ sed -n '1070p' .github/actions/gate-aggregator/aggregate.py
    if not isinstance(attempts, list) or not attempts:
~~~

改坏后确认与测试：

~~~text
$ sed -n '1070p' .github/actions/gate-aggregator/aggregate.py
    if not isinstance(attempts, list):
..F.                                                                     [100%]
=================================== FAILURES ===================================
_ test_budget_exhaustion_panel_action_is_rendered_from_primary_audit[no-attempts] _
E       AssertionError: assert '修基础设施' in '...本 PR 规模超出单次评审预算，本次未能评审完...'
1 failed, 3 passed, 221 deselected in 0.10s
~~~

还原、清缓存、复跑：

~~~text
$ find . -type d -name __pycache__ -prune -exec rm -rf {} +
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.20s
~~~

以上失败项名称、断言两侧和统计均为实际输出原文，长 body 以 pytest 输出中的实际省略片段表示。

### 2.3 严格 int

改坏前：

~~~text
$ sed -n '1074p' .github/actions/gate-aggregator/aggregate.py
        and _is_strict_int(attempt.get("exit_code"))
~~~

改坏后确认与测试：

~~~text
$ sed -n '1074p' .github/actions/gate-aggregator/aggregate.py
        and True
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.06s
~~~

还原、清缓存、复跑：

~~~text
$ find . -type d -name __pycache__ -prune -exec rm -rf {} +
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.20s
~~~

归因：没有 bool 或字符串 exit_code 负例；测试弱，不是路径不可达。

### 2.4 reason 精确匹配

改坏前：

~~~text
$ sed -n '1076p' .github/actions/gate-aggregator/aggregate.py
        and attempt.get("reason") == PRIMARY_BUDGET_EXHAUSTED_REASON
~~~

改坏后确认与测试：

~~~text
$ sed -n '1076p' .github/actions/gate-aggregator/aggregate.py
        and True
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.07s
~~~

还原、清缓存、复跑：

~~~text
$ find . -type d -name __pycache__ -prune -exec rm -rf {} +
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py -k budget_exhaustion
....                                                                     [100%]
4 passed, 221 deselected in 0.20s
~~~

归因：现有 mixed-failure 同时把 reason 改成认证或网络暂不可用、把 exit_code 改成 21；即使删除 reason 检查，exit_code 检查仍会回落。测试弱，不是路径不可达。

## 角度 3：增量审四问

实际命令：

~~~text
git diff --name-status 6505511b6ca74763d89a0670dea96100dcac5397..86892856c2d54d0a92cde6a033106aad18ff8988
git diff --unified=0 6505511b6ca74763d89a0670dea96100dcac5397..86892856c2d54d0a92cde6a033106aad18ff8988 -- .github/actions/gate-aggregator/aggregate.py tests/test_gate_aggregator.py | sed -n '1,220p'
~~~

实际输出原文：

~~~text
M	.github/actions/gate-aggregator/aggregate.py
A	docs/sessions/gh599-defect3-panel/reviews/review-d2-panel-action-verdict.md
M	tests/test_gate_aggregator.py

@@ -1081,13 +1080,0 @@ def _primary_budget_exhausted_action(primary_audit: Any) -> Optional[str]:
-    coverage = primary_audit.get("coverage")
-    coverage_fields = ("diff_lines", "reviewable_chars", "shards")
-    if isinstance(coverage, dict) and all(
-        _is_strict_int(coverage.get(field))
-        and coverage[field] >= (1 if field == "shards" else 0)
-        for field in coverage_fields
-    ):
-        return (
-            "本 PR 规模超出单次评审预算（"
-            f"{coverage['diff_lines']} 行 / {coverage['reviewable_chars']} 字符，"
-            f"需 {coverage['shards']} 个审查分片），本次未能评审完。"
-        )
@@ -1103 +1090 @@ def _panel_action(row: dict[str, Any]) -> str:
-                row.get("primary_audit") or row.get("audit")
+                row.get("primary_audit")

@@ -2240 +2240 @@ def _budget_exhausted_audit(*, coverage="present", attempts=None):
-def _budget_exhausted_audit(*, coverage="present", attempts=None):
+def _budget_exhausted_audit(*, attempts=None):
@@ -2280,4 +2277,0 @@
-            "本 PR 规模超出单次评审预算（401 行 / 12003 字符，需 3 个审查分片），本次未能评审完。请拆成更小的增量 PR 后重试。",
-        ),
-        (
-            _budget_exhausted_audit(coverage=None),
-        ),
-        (
-            {**_budget_exhausted_audit(), "coverage": {"diff_lines": 401, "reviewable_chars": 12003, "shards": 0}},
-        ),
-        (
-            {**_budget_exhausted_audit(), "coverage": {"diff_lines": 401, "shards": 3}},
-        ),
@@ -2301,2 +2295 @@
-"all-legs-exhausted-with-coverage", "all-legs-exhausted-without-coverage",
-"mixed-failure", "no-attempts",
+"all-legs-exhausted", "mixed-failure", "no-attempts",
~~~

四问结论：

1. 是。56670d6 的增量对应上一轮登记的 shards >= 1 回落断言和 row.get("audit") 无生产者 fallback 删除；8689285 删除声明要删的 coverage 渲染分支及测试格。范围内另有 619626b，仅新增上一轮审查 verdict 文档，是审查产物，不是未经批准的生产行为。
2. 否。没有新增生产抽象；本增量只删除 coverage 分支、缩窄已有 primary_audit 消费路径和调整对应测试。
3. 否。没有新增 gate 状态、verdict、事实源、fallback、重试或防御式 catch；coverage 分支被删除，primary_audit 是前一轮已存在的当前运行审计输入。
4. 否。当前 rg 检查 row.get("audit") 和 row["audit"] 没有输出；panel 只从 primary_audit 消费同一条已验证输入，没有两条并存路径。

## 角度 4：反向穷举实际输出

实际命令：

~~~text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 构造控制格、空/缺失/非 list attempts、单腿/混合失败、bool/字符串 exit_code、
# reason 多/少字符、非 dict attempt、各种非 unavailable verdict、非 dict audit，
# 以及外层 gate/classification/reason_code 不匹配；逐格调用 render_status_panel。
PY
~~~

实际输出原文：

~~~text
valid-control: 当前状态：**unavailable** · **本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。**
attempts-empty: 当前状态：**unavailable** · **修基础设施**
attempts-missing: 当前状态：**unavailable** · **修基础设施**
attempts-not-list: 当前状态：**unavailable** · **修基础设施**
single-non-budget-leg: 当前状态：**unavailable** · **修基础设施**
mixed-budget-and-non-budget: 当前状态：**unavailable** · **修基础设施**
exit-code-bool: 当前状态：**unavailable** · **修基础设施**
exit-code-string: 当前状态：**unavailable** · **修基础设施**
reason-extra-space: 当前状态：**unavailable** · **修基础设施**
reason-one-character-short: 当前状态：**unavailable** · **修基础设施**
attempts-non-dict: 当前状态：**unavailable** · **修基础设施**
verdict-pass: 当前状态：**unavailable** · **修基础设施**
verdict-fail: 当前状态：**unavailable** · **修基础设施**
verdict-null: 当前状态：**unavailable** · **修基础设施**
audit-non-dict: 当前状态：**unavailable** · **修基础设施**
outer-gate-mismatch: 当前状态：**fail** · **要修代码**
outer-classification-mismatch: 当前状态：**unavailable** · **修基础设施**
outer-reason-mismatch: 当前状态：**unavailable** · **修基础设施**
~~~

要求的所有不应说 PR 太大的形态都实际回落为修基础设施；唯一控制格实际输出拆小 PR。outer gate_result=fail 时按既有 gate bucket 输出要修代码，没有被 audit 误导。

## 角度 5：降层审查

### 5.1 面板发布前的动作顺序

实际命令以 fake 网络函数记录 _post_status_panel_fail_open_with_budget()：

~~~text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# fake _github_identity/_fetch_panel_comments/_fetch_terminal_history/_post_issue_comment，
# 调用真实发布编排函数，记录所有外部动作和最终 receipt。
PY
~~~

实际输出原文：

~~~text
events= ['GET /user', 'GET /issues/599/comments', 'GET /actions/artifacts + terminal ZIP', 'POST /issues/599/comments', 'GET /issues/599/comments']
body_action= 当前状态：**unavailable** · **本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。**
delivery= created operation= POST completed= ['IDENTITY', 'COMMENT_LOOKUP', 'HISTORY_RECONSTRUCTION', 'COMMENT_PUBLISH', 'POST_VERIFY']
~~~

在评论 POST/PATCH 成功之前，只有身份、既有评论和历史制品读取，没有评论写入，也没有 panel delivery receipt 制品写入。评论成功后才做 POST_VERIFY；_publish_only() 随后追加诊断并按参数持久化 receipt。_finish() 既有顺序仍是先渲染/写 Summary，后写 gate_terminal；本增量未改这些顺序。删减提交只删除 helper 内的 coverage 文案分支，没有改变不可逆动作或失败模式。

### 5.2 跨仓常量与漂移方向

实际命令调用 gate-hub/scripts/review/primary_orchestrator.py 的真实 build_primary_audit：

~~~text
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
# 传入两条预算耗尽 ChainAttempt，打印 producer 实际 record 的关键字段。
PY
~~~

实际输出原文：

~~~text
normalized= False
record_keys= ['attempts', 'base_sha', 'caller_sha', 'cost', 'diff_digest', 'expected_shadows', 'head_sha', 'job_id', 'kind', 'policy_digest', 'policy_version', 'pr', 'pr_body_digest', 'registry_commit', 'repository', 'repository_id', 'result', 'reusable_workflow_sha', 'reviewer', 'run_attempt', 'run_id', 'runtime', 'schema_version', 'shadow_mode', 'spec_source', 'tokens', 'verdict']
verdict= unavailable
attempts= [{"cost_usd": null, "diag_snippet": null, "duration_s": 1.0, "exit_code": 22, "model": "model-a", "reason": "评审总预算已耗尽，保留收尾空间", "reviewer": "reviewer-a", "tokens": null}, {"cost_usd": null, "diag_snippet": null, "duration_s": 1.0, "exit_code": 22, "model": "model-b", "reason": "评审总预算已耗尽，保留收尾空间", "reviewer": "reviewer-b", "tokens": null}]
coverage_present= False
~~~

消费者副本在 aggregate.py:195-196；上游 reason 常量在 /home/zlx/projects/personal/gate-hub/scripts/review/job_budget.py:23，上游 exit code 22 的实际写入点在 primary_orchestrator.py:430,449,484 和 legacy review-gate.sh:1567,1587。当前 reason/exit_code 均精确匹配。模拟上游 reason 多一个字符时，反向探针实际输出为 当前状态：**unavailable** · **修基础设施**。副本漂移默认是安全回落、丢失拆小 PR 提示，不是误触发 PR 太大。

### 5.3 保护的是行为还是写入

保护的是行为：测试断言落在 render_status_panel() 返回字符串，位于 tests/test_gate_aggregator.py:2307-2309；Summary 也在 :2318-2320 断言完整字符串。发布函数只把渲染结果送入既有 POST/PATCH fail-open 流程，不改变 gate_result/classification/reason_code，不把评论写入当作合并判定事实源，也没有新增写入条件。

## 角度 6：熵增与新增符号

实际命令：

~~~text
rg -n "PRIMARY_BUDGET_EXHAUSTED|_primary_budget_exhausted_action|_action_sentence|render_summary|primary_audit" .github/actions/gate-aggregator/aggregate.py tests/test_gate_aggregator.py
~~~

关键实际输出：

~~~text
.github/actions/gate-aggregator/aggregate.py:195:PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE = 22
.github/actions/gate-aggregator/aggregate.py:196:PRIMARY_BUDGET_EXHAUSTED_REASON = "评审总预算已耗尽，保留收尾空间"
.github/actions/gate-aggregator/aggregate.py:944:def _action_sentence(
.github/actions/gate-aggregator/aggregate.py:989:            budget_action = _primary_budget_exhausted_action(primary_audit)
.github/actions/gate-aggregator/aggregate.py:1002:def render_summary(
.github/actions/gate-aggregator/aggregate.py:1065:def _primary_budget_exhausted_action(primary_audit: Any) -> Optional[str]:
.github/actions/gate-aggregator/aggregate.py:1089:            budget_action = _primary_budget_exhausted_action(
.github/actions/gate-aggregator/aggregate.py:2042:        primary_audit, _, _ = _read_audit_file(audit_dir)
.github/actions/gate-aggregator/aggregate.py:2044:            current["primary_audit"] = primary_audit
~~~

上面两行路径是实际命令输出的手工摘要；完整 rg 输出中的路径为 .github/actions/gate-aggregator/aggregate.py:1089、:2042、:2044。

- _primary_budget_exhausted_action 有两个真实生产消费者：_action_sentence:989（Summary）和 _panel_action:1089（面板）。
- 两个常量在 helper 的生产比较中消费，并在测试 producer-shaped fixture 中消费；测试引用是第二个契约消费者，锁定跨仓实际 payload 的精确值。
- primary_audit 沿 _finish → render_summary → _action_sentence 传递，同时由 _publish_only 投影到 panel row；这是必要的数据传递，不是新增状态或第二事实源。
- 测试 helper _budget_exhausted_attempt / _budget_exhausted_audit 被多个参数格和 Summary 测试复用。coverage 参数、coverage_fields 和数字 coverage 分支在删减后不存在。

删减后仍有 2 个新常量、1 个 helper 和 primary_audit 的必要传递参数；没有新增 verdict、状态、配置项、fallback 或双路径。熵增审查不产生额外 finding。

## OCR 与图谱前置

审查纪律要求的 OCR 已执行：

~~~text
ocr-review --repo /home/zlx/projects/personal/gate-worktrees/gh599-defect3-panel --from 6c6542473be97d8c4bffc5c4b09db86bcf9c91ca --to 86892856c2d54d0a92cde6a033106aad18ff8988 --audience agent --concurrency 4 --background-file <(printf ...)
OCR failover progress: leg=primary event=start
OCR failover progress: leg=primary elapsed_s=110.884
{"status":"reviewed","profile":"minimax","model":"MiniMax-M3","reason":"primary_selected","findings":[...],"cli_status":"complete","coverage":"complete","verify":{"verify_status":"partial","verifier":"codex-sub","concurrency":4,"counts":{"total":5,"verified":2,"confirmed":2,"refuted":0,"unverifiable":0,"unverified":3},"reason":"..."},"attendance_ledger_write":"ok"}
~~~

OCR 候选中，关于 verdict/严格 int/reason 独立变异覆盖的意见经本轮实测确认，合并为 P2-1；跨仓常量漂移和未使用 audit payload 的候选经源码、真实 producer payload 与发布轨迹核对，未升级为本轮 finding。

图谱技能已读取，但仓内没有预建 graphify-out/graph.json；完整流程会生成超出 Scope-Globs 的副产物。本轮按清洁回退使用 git/rg/源码和实际探针，没有生成图谱文件。

## 全量验证

实际命令与输出：

~~~text
uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
........................................................................ [  8%]
........................................................................ [ 17%]
........................................................................ [ 26%]
........................................................................ [ 35%]
........................................................................ [ 43%]
........................................................................ [ 52%]
........................................................................ [ 61%]
........................................................................ [ 70%]
........................................................................ [ 78%]
........................................................................ [ 87%]
........................................................................ [ 96%]
.............................                                            [100%]
821 passed in 18.49s

python3 scripts/check_pinned_uses.py
OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative

git diff --check 6c6542473be97d8c4bffc5c4b09db86bcf9c91ca..86892856c2d54d0a92cde6a033106aad18ff8988
OK (no output)

git status --short --branch
## feat/gh599-defect3-panel
~~~

本轮没有运行 .github/workflows/ 下的验证命令，符合卡面限制。

## Backlog（存量，不计入本轮 findings）

接手现场 gh issue list --state open 的实际输出：

~~~text
117	OPEN	ledger input 上传重试的 overwrite 在「Finalize 失败但服务端已存半成品」时先删再传，双失败回落上一 attempt（PR #115 r1 P2-1，接受不修）
116	OPEN	aggregator _fetch_pr_draft 对非 JSON 200 等非重试类异常会崩而非落 pr_state_unverifiable（PR #113 r2 P2，接受不修）
105	OPEN	gate-v2: 汇总 job 被最慢的 quality 绑架；advisory ocr 失败污染 run 级 conclusion（自 gate-hub#580）
~~~

这些是存量 issue，没有与本轮固定 diff 混入 findings。
