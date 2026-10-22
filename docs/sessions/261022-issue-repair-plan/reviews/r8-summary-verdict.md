# R8 Summary Verdict

failure-visibility: clean

## 固定审查对象

- Base: `d565a5207cd608993887f01081ba68940fe489ef`
- Head: `722f6ec6d035b64f56ce96e2ba11219947e1f3cc`
- 实际 diff：`.github/actions/gate-aggregator/aggregate.py` 与 `tests/test_gate_aggregator.py`，35 insertions、62 deletions。

## 主脑审查与测试证据

主脑对本 PR 根 diff 的 failure-visibility 判定为 clean：本次只改摘要说明和对应断言，`evaluate` 与终态分类没有变化，真实 main summary 消费测试仍断言 `integration_error` / `audit_missing` / `unavailable`，未发现 P1 或交付阻断。OCR 另有一条 low finding，主脑判为 P2 并接受不修；这是行为减法后的未使用参数可维护性意见，本卡不改既有调用签名。

- 固定 head 上 `tests/test_gate_aggregator.py`：434 passed；受影响的 13 个场景：13 passed。
- 三项最小红验均按预期失败后已恢复：给 missing-audit explanation 加日志指引，`test_reason_code_wording_cannot_be_misread` 失败；重新加入 reviewer-rejection 猜测，`test_audit_missing_wording_does_not_infer_from_primary_job_result` 的 failure/success 两态均失败；给 synthetic null explanation 加日志指引，`test_synthetic_verdict_null_is_explained_before_the_json` 的 5 个场景均失败。

## OCR 前置扫描

- 命令固定审查 `d565a5207cd608993887f01081ba68940fe489ef..722f6ec6d035b64f56ce96e2ba11219947e1f3cc`，`--audience agent --concurrency 4`，单腿预算 120 秒、链路预算 240 秒；background 为 406 bytes。
- Envelope: `status=reviewed_fallback`，profile `deepseek`，model `deepseek-v4-flash`，`coverage=complete`，`verify_status=completed`。
- Reason: `primary=content_error:all_file_reviews_failed; backup:deepseek=success`。Primary leg 失败，DeepSeek 备腿完成扫描；不能记作 primary leg clean。
- 共 1 条 finding：工具标注 `severity=low`、`category=maintainability`、`verification.verdict=confirmed`，由 `codex-sub` 验证。内容指出 `render_summary` 的 `primary_result` 参数在删除特殊分支后未使用，声明点为 `aggregate.py:1954`，调用点为 `aggregate.py:3856`。主脑按仓库风险档判为 P2，接受不修；本卡是摘要行为减法，不改既有调用签名。

## 问题与 CI 状态

`zlxlabs/gate-hub#1752` 所涉真实 primary job/provider 故障原因仍未知；本 PR 只消除了摘要猜测，正文使用 `Refs`，不关闭 issue。

PR #369 首轮检查 run `39772649598` 已终态成功：`actionlint` pass，`test` pass（5m24s）。
