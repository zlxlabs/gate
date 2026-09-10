# VERDICT — gate-734-classify r1（独立审查）

- 审查对象：`c67682f0eeefc9484308acca30e011d00b631a99..4e29a386023ad62b83c9833e9145ee47bed9fe05`（draft PR #146，head 已与冻结 SHA 核对一致）
- 设计真身：`docs/sessions/260910-734-ledger-skip/design.md`
- 风险档：personal（P1 红线：数据丢失、静默出错、崩溃）
- 结论：**PASS**（无 P1/P2；3 条 P3 接受不修，见下）

## 本轮焦点：compare 续修后的两条高风险问

1. **真代码 PR 会被判成不应审吗（静默漏审）？不会。** `review_expected=false` 的唯一出口是
   路径集合恰好等于 `{retro/acceptance-log.jsonl}`（`scripts/classify_pr_reviewable_paths.py`
   `review_expected()`，Python set 相等比较）。fail-closed 面逐点核过：
   BASE/HEAD SHA 为空 → true；`gh api` 非零退出/超时（30s）→ shell 预写的 true 保留、exit 0；
   分类器自身异常 → 先写 true 再 exit 1，shell 再补 true；空列表 / 任何其它路径 /
   `truncated:true` / `--has-next-page` → true。下游 `!= 'false'` 对空输出仍审；classify job
   整体失败时 primary 被 `needs` 跳过而 aggregator `REVIEW_EXPECTED` 为 true → 判「主审异常失踪」，
   门禁红（fail-closed，非静默绿）。个人档 P1 两问：真实触发路径不存在；不触发。
2. **影子腿会因 403 继续烧模型吗？不会。** 现网影子 caller 权限天花板是
   `actions: read + contents: read`（`templates/caller-gate-shadow-v2.yml` 与 callee 顶层
   permissions 交集）。GitHub 官方文档（REST API endpoints for commits, "Compare two commits"）
   明确该端点细粒度权限为 **Contents (read)**；旧 `pulls/{n}/files` 需 Pull requests (read)，
   正是 403 根因。续修命中根因。即便未来再遇 403，fail-closed=true：模型照跑（烧额度但不漏审），
   金丝雀会以「模型仍被调用」暴露，符合 design 待验证前提的失败语义。

## 降层三问（卡面强制）

1. **跳过模型之前已发生哪些不可逆动作？** 无。classify job 只 checkout gate 仓自身脚本
   （`job.workflow_repository`@`job.workflow_sha`，gate 仓 public，caller token 可读）+ 一次只读
   compare API。跳过以模型 job 的 `if:` 实现，primary / resolve_advisory / 影子 resolve 根本不启动；
   判定之前无通知、无 PR 状态写入、无发布。aggregator 对 false 记「预期跳过」，全程可查可逆。
2. **守卫的路径集合在 compare API / 截断下仍是「这次 PR 的完整改动」吗？是。** 三点 compare
   （`base.sha...head.sha`）自 merge-base 起 diff，与 PR files 语义一致，且不受 base 分支前进影响；
   compare 不分页、上限 300 文件、超出置 `truncated:true`，分类器对 truncated 强制 true；
   gh 失败 / 空输出 / 非 JSON 同样 true。残余风险（GitHub 返回与 PR 实际不一致）不在本 diff 可控面。
3. **保护的是「模型被调用」还是「写了某份文件」？** 「模型被调用」。账本文件照常提交、
   quality 照跑、聚合器照常落账（记预期跳过）；本增量不阻止任何文件写入，与 design 非目标一致。

## design.md 关键不变式逐条核验

| # | 不变式 | 核验 | 结果 |
|---|--------|------|------|
| 1 | 主审 `if:` 与聚合 `REVIEW_EXPECTED` 逐字节相同，含新子句 | `test_model_jobs_and_review_expected_copies_need_classify_and_match_primary_if` 断言 5 处副本 == primary.if，全文 CLASSIFY_GUARD 恰好 7 处 | ✅ |
| 2 | 分类器仅「只有 jsonl」→ false，其余全 true；夹具为真实 API 形状 | 表驱动 11 例 + 不可用输入 9 例，含 PR201 脱敏真实夹具与 compare `{files,truncated}` 形状 | ✅ |
| 3 | 跳过 + false 时 aggregator 记预期跳过 | `tests/test_gate_aggregator.py` 238 passed（既有 review_expected=False + skipped 用例未动） | ✅ |
| 4 | 影子 resolve `if:` 与主审逐字节相同 / 同脚本 | `test_resolve_if_is_byte_identical_to_gate_v2_primary_if` + `test_shadow_classify_job_matches_required_gate_classifier`（run 脚本逐字符相等） | ✅ |

独立复验（非引用实现方报告）：冻结 SHA 临时 worktree 上
`test_classify_pr_reviewable_paths.py + test_gate_v2_contract.py + test_gate_shadow_v2_contract.py`
= 235 passed；aggregator 238 passed。红验抽查：把 gate-v2.yml 分类 URL 变异为 `pulls/1/files`，
上述两条契约测试均以 AssertionError 转红（非恒真断言），单文件单行还原，工作区已净。

## Findings

无 P1 / P2。以下 P3 接受不修：

- **P3-1 design 文档过期**（`design.md` 待验证前提 1）：仍写「`gh api --paginate` 列拉取请求
  files」，compare 续修后待验证对象已变为「compare 在 contents:read 下可用」。溯源：四条关键
  不变式均未 pin 列路径 API（不变式 2 的锁是表驱动测试，已含 compare 形状），无法溯源到不变式
  违反，按规则降一级（本就 P3）。
- **P3-2 注释漂移**：`gate-v2.yml` 顶部 job-layout 注释仍说 "via the GitHub files API"；
  分类器 argparse help 仍写 "pulls files API JSON"（模块 docstring 已正确描述两种形状）。
  同样无法溯源到不变式，P3。
- **P3-3 OCR confirmed 项复核**（personal 档两问重判，维持 P3）：① classify job 未收窄 job 级
  permissions（gate-v2 下继承 `pull-requests: write`）；② checkout 未设 `persist-credentials: false`。
  两问：该 job 不 checkout PR 代码、不执行不可信输入（compare JSON 仅在 Python 内做 set 比较，
  无 shell 插值），真实使用方式下无滥用触发路径 → 均非 P1。③ 契约测试把 CLASSIFY_GUARD 计数
  锁为恰好 7：偏脆，但配合 5 处逐字节相等断言，漏接线一处即红，可接受。

## 范围确认

- 只审冻结 SHA 范围 diff（9 文件 +775/-34）；未改 caller 模板、未把 paths-ignore / skip quality /
  全局 markdown 重提；无新增抽象（diff 仅 1 个新脚本 + 2 个 workflow job + 测试与文档）。
- 合并方式提醒（非 finding）：本仓 workflow 被下游 immutable SHA pin，合并必须用 merge commit。
