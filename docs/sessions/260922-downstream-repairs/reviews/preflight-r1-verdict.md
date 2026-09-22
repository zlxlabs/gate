<!-- delegate-outcome: succeeded -->

## Verdict

- verdict: fail
failure-visibility: p1-found
- Task-Id: gate#217；Fixes-Issue: gate#217
- Verify-Mode: review-local-v1
- base: `514f6c8cb40400123845a212c2844dc7846a966c`
- H0: `22e88da89a5361714d6b5e6cd1627e872f3ced2e`（固定审查对象）
- risk-tier: personal；只审 `base..H0`，未读取实现方报告，未修改实现/测试/workflow。

## 结论

H0 不能通过预检三态独立审查。两条 P1 分别是最终聚合边界对缺失/旧态证据可放行，以及测量校验异常不产出 `unavailable`；当前正常 producer→workflow→aggregator 路径的三态接线本身成立。

## P1 findings

1. `.github/actions/gate-aggregator/aggregate.py:148-152,686-693,820-876`：`PREFLIGHT_RESULT_DOMAIN` 接受空、`skipped`、`cancelled`，但 `evaluate()` 只对 `blocked`/`unavailable` 分支处理；`quality=success`、合法 primary pass、`preflight_result` 为空/旧态时实测返回 `code_pass/pass`。违反规范 1、2、5（缺失/损坏/不一致不得默认放行；quality=success 但预检不可用必须闭门）。真实探针输出：`'' True code_pass ... pass`、`'skipped' True ... pass`、`'cancelled' True ... pass`。影响是最终门禁可把没有 producer 结果的输入当成功；gate-v2 当前 `*:unavailable` 只能保护工作流接线，不能替代 aggregator 的事实校验。
2. `.github/actions/pr-size-preflight/preflight.py:77-80,154-162,381-396`：`measure()` 的 malformed numstat、path/section mismatch 等 `ValueError` 未被主入口捕获，主入口只捕获 `CalledProcessError`。目标提交真实探针输出 `ValueError git numstat has no entry for diff path 'app.py'`；未写 result JSON，也未写 `preflight-result` 的 `GITHUB_OUTPUT` 字节。违反规范 1、2、5（测量失败必须发布 `unavailable` 并失败退出，且 producer 结果与查询失败要可区分）。工作流兜底会把缺失状态映射为 unavailable，故当前消费侧闭门，但 producer 已崩溃、结构化事实丢失。

## P2 findings（不阻断当前触发，但回归保护不足）

- `.github/workflows/gate-v2.yml:550-555` 的 catch-all 是关键闭门分支；`tests/test_gate_v2_contract.py:2631-2647,2701-2722` 只做子串检查，未锁死 `*)`，也未覆盖 `success:` 空 payload、`skipped/cancelled`。违反规范 2、4；应保留真实 `GITHUB_OUTPUT` 边界测试并精确断言四态映射。
- `tests/test_gate_v2_contract.py:2692-2698` 以文本子串代替 YAML 结构断言 action output；`.github/actions/pr-size-preflight/action.yml:17-19` 的接线当前正确，但该测试不能防止字段落入注释/描述。违反规范 4 的生产 serialization 回归保护要求。

## 全量 diff 与不变式核对

- 固定 diff 为 7 个文件，`330 insertions(+), 38 deletions(-)`；`git diff --check` clean。当前质量/硬预算分类与 warning/sharded/blocked 分片语义未被改坏；warning 在硬审查预算内映射 success 是符合既有分类契约的，OCR 对此的候选不成立。
- `action.yml:17-19`、`preflight.py:358-365`、`gate-v2.yml:538-556` 及 `tests/test_gate_v2_contract.py:2732-2782` 已锁定 producer 字节、失败退出、workflow 映射；gate-v2 aggregate 与 publish-only 两处调用均转发 `QUALITY_PREFLIGHT_RESULT`。旧 `gate.yml` 仅把 JSON 交给 legacy ledger，无漏迁移的 aggregator 调用。
- 未发现新增未经批准的 retry/fallback 或单消费者通用化；新增 builder 仅承载本卡要求的 unavailable envelope。未发现其他违反规范的实现意见。

## 验证与未知

- 目标 SHA scratch worktree：`1108 passed in 79.98s`；`scripts/check_pinned_uses.py`：OK（9 个 live workflow/action metadata 文件）。
- 缺失 Git 对象探针：返回码 1，payload/`GITHUB_OUTPUT` 均为 `preflight_result=unavailable`，摘要提示 infrastructure recovery；损坏 numstat 探针如 P1-2 所述崩溃。
- OCR 原始 envelope：`/tmp/gate217-preflight-r1-ocr.json`，JSON parse OK，`status=reviewed`、`reason=primary_selected`、11 findings；stderr 原文在 `/tmp/gate217-preflight-r1-ocr.stderr`。OCR 多条 verifier 针对旧 checkout，已逐条与 H0 复核，仅采纳与 P1-2一致的事实。
- `budget_status=fail_loud` 且 `budget_base_sha/head_sha=None`；仅记录上述固定 diff 事实，不凭缺失预算源推断预算通过/失败。未在真实 GitHub runner 执行 composite action，故 failed-step 输出文件由 runner 解析的最后一跳仍是未知；本地 producer 写入字节及 workflow shell 消费已实测。
