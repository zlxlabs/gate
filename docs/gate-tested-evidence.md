# GATE-TESTED-EVIDENCE-V1

quality job 在测试**真跑且通过**后，由独立步骤 `Emit tested evidence` 向步骤日志打印一行证据，供下游（agent-config 验收预检）区分「绿但没跑测试」与「至少跑过一个非 skipped 用例且无 failure/error」。

契约语义：**本次所选测试入口正常结束，且其报告证明至少一个非 skipped 用例通过、所有纳入判定的报告无 failure/error。**

## 证据行

仅在 Emit 步骤 stdout 打印一行（两个 sha 均为 40 位小写 hex）：

```text
GATE-TESTED-EVIDENCE-V1 head_sha=<pull_request.head.sha> merge_sha=<GITHUB_SHA> mode=<entry|pytest>
```

- `mode=entry`：caller 走 `scripts/gate-quality`；junit 目录为 `$GATE_ARTIFACT_DIR/junit/`，且 `gate-quality-result.json` 为 `schema=gate-quality-result/v1`、`stage=tests`、`outcome=passed`、`evidence=junit`。
- `mode=pytest`：legacy 模式且 gate **直接**调用 pytest（`uv run [--frozen] pytest -q --junitxml=...` 或 `pytest -q --junitxml=...`）。

## 判定（二者缺一不打）

1. 测试步骤整体成功：entry 看 `steps.run-quality.outcome == success`，legacy 看 `steps.run-tests.outcome == success`。junit 计数**不能**替代退出状态。
2. 解析本次专用目录下全部 `*.xml`：任一文件损坏或 suite 属性与 `<testcase>` 计数矛盾 → 不打。合计 `tests>=1`、`failures=0`、`errors=0`、`skipped<tests`。嵌套 `<testsuites>/<testsuite>` 按 `<testcase>` 元素计数，不按嵌套 suite 属性加总。

测试步骤开始前清空本次专用 junit 目录；entry 同时删除旧的 `gate-quality-result.json`。不用 mtime 判新鲜度。

## 不打的情形

证据不足时步骤仍 exit 0（quality 不因此变红），只向 `GITHUB_STEP_SUMMARY` 写一行 `tested-evidence: not_emitted reason=<枚举>`。

| reason | 含义 |
|---|---|
| `not_pull_request` | 不是 `pull_request` 事件（含 `pull_request_target` / push / workflow_dispatch） |
| `invalid_sha` | head 为空，或任一 sha 不是 40 位小写 hex |
| `step_not_success` | 对应测试步骤 outcome 不是 success |
| `result_unqualified` | entry 的 result JSON 缺失或不是 tests/passed/junit |
| `junit_missing` | 专用目录没有 xml（含 legacy `make test` / `npm test` / `no tests detected`） |
| `junit_invalid` | xml 损坏或计数矛盾 |
| `junit_empty` | 合计 0 条用例 |
| `junit_failed` | 存在 failure 或 error |
| `junit_all_skipped` | 用例全是 skipped |

脚本自身出错同样不把原本绿的 quality 打红，但必须出现可 grep 的 `::warning::GATE-TESTED-EVIDENCE-EMIT-FAILED`。

## 给 consumer

只认 **Emit tested evidence 步骤日志内唯一一条整行**匹配上述格式。多条或冲突判 unknown。不要用整份 job 日志做前缀扫描：测试输出里出现 `GATE-TESTED-EVIDENCE-V1` 字样不让 quality 失败，也不构成证据。

legacy 的 `make test` / `npm test` 不注入 reporter，不打证据行，consumer 侧 unknown 是预期。

## v2 抬升资格

跨仓读 canary job 日志需要 canary 的 `actions:read`，v2-tag-sync 的 `github.token` 做不到。因此 **Emit 区间与证据行的严格判定在 canary 仓内完成**：`ci-infra-canary` 的 `gate.yml` 另有 job `tested-evidence`，用 canary 自己的 token 读同 run 的 `gate / quality` 日志；判定失败则该 job 红、整个 run 红。

`scripts/v2_tag_promotion_evidence.py` 选 canary 验证过的 main 提交时，在 run conclusion=success、gate-v2 引用 sha 一致、primary job success 之后，只再查同一 jobs 列表：**恰好一个** job 名等于 `tested-evidence` 或以 ` / tested-evidence` 结尾，且 conclusion=success。gate 侧不再读日志。不满足则该 run 不合格，reason 区分 `tested-evidence job is absent` / `tested-evidence job count=<n>` / `tested-evidence job conclusion=<x>, required success`。

合并顺序：依赖 canary 仓落地 `tested-evidence` job，且其 PR 上该 job 已绿过；否则本仓 merge commit 自身没有合格 canary 运行，v2 不会抬升。
