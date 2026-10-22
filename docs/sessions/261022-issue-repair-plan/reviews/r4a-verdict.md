failure-visibility: p2-only

# R4a 独立审查 verdict：absence 反证绑定签发 head

- 审查仓：`zlxlabs/gate`；风险档：`personal`
- 审查对象：PR #367；冻结代码范围：`f144ece04a1c26e9b63dde8409ab403e0f026dcc..40b9de9b1d2f34f5d7989d78711c20ce995805dd`
- 审查范围：完整 diff，以及 disposition producer → serialized receipt → consumer 的实际路径
- 结论：无 P1；有一项已裁为不阻断的 P2 候选。PR 保持 draft，等待仓库检查。

## 关键不变式

1. **absence 证据只在签发它的 head 上有效。** `convergence.py::_consume_neighbor_disposition` 对 `match_expectation=absent` 立即返回 expired。任何后续 head 的 neighbor 消费都会过期，不会把后来加回来的引用重新解释为原始 head 上的 absence 证据；本实现不尝试用 absence 证据触发 neighbor rerun。
2. **base/head 反证成对成立。** producer 从当前 scope 的 `base_sha` 为 allowlisted grep 生成 base 证据；base 必须有匹配，签发 head 必须零匹配，两个 commit 对象都必须完整。producer 写出的序列化 receipt 保留 `result=refuted` 与非空摘要；consumer 通过已有 scope epoch 绑定 base 身份。
3. **历史投影保留证据模式。** 聚合历史投影仅在原回执声明 absence 时透传 `match_expectation=absent`，不会把 absence 证据投影成默认 present。

## Finding 裁决

独立审查 `review_absence_367` 全量检查了冻结 diff 和 producer → consumer 链路，没有发现 P1。审查提出一项 P2 候选：consumer 不再单独把 `base_rerun.argv[6]` 与当前 `scope.base_sha` 二次比较。

该意见不阻断，也不修复。生产 producer 从同一 canonical scope 读取 base SHA 并生成 `base_rerun.argv`；当前 scope 的 `base_sha` 已参与 epoch，换 base 会被现有 epoch 检查判 stale。receipt 经 workflow 写入受信任的 Silo 路径，正常生产路径没有手工改 JSON 的消费者或触发点。单独增加 consumer base 字段比较会重建已由 epoch 覆盖的重复身份校验，也会恢复本卡裁掉的 `expected_base_sha` 传递链。主脑结合 Opus 的减法裁决接受此 P2，不增加重复校验或状态。

## 执行验证证据

- 本卡相关测试在最终 head 上运行：`tests/test_gate_convergence.py`、`tests/test_gate_disposition_issue_receipt.py` 与更新后的 workflow 契约测试，共 **231 passed**。
- 全量 CI 等价测试首跑：**1930 passed、1 failed**。唯一失败是 `tests/test_gate_v2_contract.py` 仍期待旧的 head-only fetch 命令；更新契约期望为 absence 模式实际 fetch argv 后，相关 231 项重跑全绿。
- 随后的 GitHub Actions run `39763810528` 终态为 `conclusion=success`；job `test` 与 `actionlint` 均为 `conclusion=success`。这是过时期望修正后的完整托管检查证据。
- `python3 scripts/check_pinned_uses.py` 通过；CI 固定版本 actionlint `1.7.12` 对 workflow 和 templates 检查通过。
- 最小故障注入：破坏 workflow fetch argv 后 workflow 契约测试变红；将 producer 的 base argv SHA 改为错误 SHA 后真实 absent receipt 往返测试变红。恢复后目标测试全绿。neighbor 反例也在添加过期守卫前实测返回 `active_false_positive`，修复后断言 expired。

## 静态审查与 OCR 前置

静态全 diff / producer → consumer 审查未发现 P1。对 P2 候选的接受依据是当前 scope/epoch 与实际 producer 的静态链路核对；它不替代上述执行测试证据。

OCR 前置 envelope 状态为 **`skipped`，不是 clean**，总链预算 240 秒：

- MiniMax 主腿实际失败为 HTTP 429 rate limit（六次请求均受限），envelope 记 `content_error:all_file_reviews_failed`。
- DeepSeek 备用腿到 120.016 秒超时；实际 stderr 显示其 `code_search` 调用的本地 `git grep` 参数正则失败，错误为 `missing closing parenthesis`。
- 最终 envelope 的 `status=skipped`、`findings=[]` 只表示未完成 OCR 审查，不作为无 finding 的证据，也不改变独立审查的 P2-only 裁决。

## 交付状态

PR #367 保持 draft；run `39763810528` 的完整 test/actionlint 检查已成功。本 verdict 固定在上述代码 SHA 范围；本文件与 PR 描述更新不改变审查对象。未执行 ready、合并或发布。
