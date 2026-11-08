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

`scripts/v2_tag_promotion_evidence.py` 选 canary 验证过的 main 提交时，在 run conclusion=success、gate-v2 引用 sha 一致、primary job success 之后，还要求该 canary run **恰好一个** `quality` job（job 名等于 `quality` 或以 ` / quality` 结尾）conclusion=success，且其 job 日志的 Emit 步骤输出区间内**恰有一行**整行匹配

```text
GATE-TESTED-EVIDENCE-V1 head_sha=<40hex> merge_sha=<40hex> mode=pytest
```

`head_sha` 必须等于该 run 详情 API 的 `head_sha`（canary PR head，独立元数据，不从日志取）。`merge_sha` 只校验 40 位 hex 格式。只接受 `mode=pytest`（canary 走 gate 直跑 pytest）。

日志定位与 consumer 匹配规则一致：按 `\n` 分行、去掉单个尾部 `\r`、剥离可选时间戳前缀、首行去 BOM；Emit 区间由唯一一行 `##[group]Run python3 - <<'PY' || echo "::warning::GATE-TESTED-EVIDENCE-EMIT-FAILED"` 起，第一个 `##[endgroup]` 之后、下一个 `##[group]Run ` 之前。不对整份 job 日志做宽泛搜索。区间外（含测试输出）出现的同格式行不计。

不满足则该 run 不合格，reason 写明：缺 quality job / 日志无 Emit 区间（区间头 0 或 2 次）/ 0 条 / 多条 / head 不符 / mode 不符 / 区间内出现 EMIT-FAILED。日志读取失败 fail-loud（`EvidenceQueryError`），不重试、不吞错。

合并顺序：本判定依赖 ci-infra-canary 改走 gate 直跑 pytest 且其 PR 上已出现证据行；否则本仓 merge commit 自身没有合格 canary 运行，v2 不会抬升。
