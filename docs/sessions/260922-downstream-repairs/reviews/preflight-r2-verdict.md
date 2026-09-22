<!-- delegate-outcome: succeeded -->

## Verdict

- verdict: fail
failure-visibility: p2-only
- Task-Id: gate217 预检三态 H1 独立审查 r2
- Fixes-Issue: gate#217
- Verify-Mode: review-local-v1
- base: `514f6c8cb40400123845a212c2844dc7846a966c`
- H0: `22e88da89a5361714d6b5e6cd1627e872f3ced2e`
- H1: `cb306b567b9bf3761885f526fbbcb62302396e96`
- risk-tier: personal
- diff: 只审 `base..H1`，并先审 `H0..H1` 四问；未修改实现、测试或 workflow。

## 结论

H1 的三态 producer/action/workflow/aggregator 链路和真实负例判据正确：success 是唯一放行预检状态，blocked 只来自可测超预算，unavailable 覆盖测量失败、未知和缺失；primary code-fail/integration-error 优先级、draft expected skip、quality short-circuit 未被改坏。

当前 verdict 为 fail，原因是全量测试存在真实回归：`tests/test_gate_convergence_artifact.py:133-143` 的 aggregator CLI fixture 缺少显式 `--preflight-result success`。该测试在 H1 下稳定返回 `review_unavailable/quality_infra/unavailable`，导致全量命令 `1115 passed, 1 failed`；应补 fixture 参数，不能放宽缺失证据的闭门逻辑。

## Findings

1. **P2 / 当前落地阻断：旧 CLI fixture 未声明合法预检 success。** `test_aggregate_cli_receipt_bytes_validate_and_replay` 构造的是合法 primary/convergence 正例，却未传新契约要求的 `--preflight-result success`。全量测试和孤立重跑均失败。这个问题不是 personal 风险档 P1（没有数据丢失、静默错误或崩溃），但在修复测试 fixture 前不能合并。

## H0→H1 四问

- 仅修已登记的 ValueError payload 与 quality-success 缺证据 fail-closed：通过。
- 未新增未经批准抽象：通过。
- 未新增无依据状态 fallback、重试或兼容放行：通过。
- producer→action output→workflow shell→aggregator CLI 没有双路径：通过。

## 证据

- 定向测试：`484 passed in 41.02s`。
- 全量测试：`1115 passed, 1 failed in 46.43s`；唯一失败为上述 convergence artifact 测试。
- producer CLI success / blocked / malformed-numstat ValueError 三态均真实写出 JSON 与 `GITHUB_OUTPUT`，分别为 success / blocked / unavailable。
- workflow shell 对缺失、unknown、skipped、cancelled 等不一致输入均输出 `preflight_result=unavailable`。
- aggregator CLI 对缺失、非法、blocked、unavailable、skipped、cancelled 均非零或不可用；合法 success 才 pass。
- `python3 scripts/check_pinned_uses.py` 通过（9 个 live workflow/action metadata 文件）；`git diff --check` 通过。
- H1 原始 OCR 已读取并逐条复核；理论上的 quality skipped/cancelled 归因意见未发现当前 workflow producer→job output 的可达 P1 路径，且不改变 gate fail-closed 结果。

