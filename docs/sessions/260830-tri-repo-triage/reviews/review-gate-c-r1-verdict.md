# G3 ledger 消费投影：第 1 轮全量对抗审查 verdict

审查对象固定为 `2a1e4b1743b1e7126849e008201751dc77990006..7ca8afc19527403b88712a566c647da1ca164921`，不包含审查期间的新提交。仓级风险为 `personal`；本轮重点是失败路径、跨 job artifact 身份和 terminal schema 兼容。

## 结论摘要

发现 2 条 P1、1 条 P2。P1 都满足本仓两问：真实 rerun/legacy workflow 会触发，后果分别是 ledger job 崩溃丢账、legacy ledger 静默不产出。主门禁 `gate / gate` 不会被 ledger job 失败误判，但这不抵消 ledger 持久化数据丢失。

## 轴 A：fail-loud 方向与作用域

`gate` 仍是唯一 required-check-bearing job：`.github/workflows/gate-v2.yml:32-44,863-872`，其依赖只有 `quality, primary`。`ledger` 是 `needs: [quality, primary, gate]` 且 `if: always()` 的独立持久化 job（`:1094-1097`），`notify` 只依赖 `gate`（`:1223`）。因此 ledger 失败不会回写 gate 的 `gate_result`，不会把“记账失败”升级成“门禁误判”；它会让 ledger artifact 不产出。

本仓 `main` 当前没有 branch protection，`gh api repos/zlxlabs/gate/branches/main/protection/required_status_checks` 原文返回 `{"message":"Branch not protected",...}`；外部 caller 的 required contexts 不能由本仓 API 直接实证。本 workflow 自身的 required 归属仍由静态契约明确写死为 `gate / gate`。

新增的“缺失/损坏 terminal 不投影为空块”方向正确：resolver 对 terminal required（`:1111-1168`），下载和 build 没有 `continue-on-error`（`:1193-1206`），consumer 对文件/JSON/块形状 fail-loud（`.github/actions/review-ledger/build_ledger.py:452-545`）。但以下两个 P1 使实际持久化仍不成立。

## 轴 B：terminal schema 前后兼容

| producer / consumer | 实际结果 | 裁决 |
|---|---|---|
| 新 producer / 新 ledger | `build_terminal_envelope` 产出块，`build_entry` 顶层复制；相关测试通过 | 通过 |
| 旧 terminal（无消费块）/ 新 ledger | `schema_version/kind` 可读，但 `:543-545` 因缺块抛 `ValueError`，不会静默变成空消费 | 通过，fail-loud |
| 新 terminal / 旧 ledger | 旧 ledger 不读取 terminal，抽象混搭时会丢新顶层字段 | 理论上有静默路径；但 v2 gate 与 ledger 都按同一 `job.workflow_sha` checkout（`:894-899`、`:1104-1109`），本 workflow 内没有正常混搭入口 |
| 缺失/损坏 terminal / 新 ledger | resolver 或 `load_gate_terminal_envelope` 失败，不落空块 | 通过 |

因此 schema 兼容没有另列 P1：v2 同 SHA 约束封住正常版本错配，旧 terminal 喂新 ledger 也显式失败。shared action 在 legacy workflow 的实际回归另列 P1-2。

## Findings

### P1-1：terminal resolver 允许旧 attempt，consumer 却按当前 attempt 严格拒绝

- 违反：spec 2（ledger 必须消费本 run 的唯一 terminal artifact）、spec 4（缺失/解析失败与合法无消费可区分）；不变式是“resolver 选出的 terminal artifact 的 payload 身份必须与传给 consumer 的 source 身份一致”。
- 证据：`.github/workflows/gate-v2.yml:1118-1119` 的 terminal prefix 没有 attempt；`:1155-1164` 接受 `attempt <= current` 并取最大值；`:1168,1174-1175` 写出 `terminal_source_attempt` 却没有把它传给 ledger action。`.github/actions/review-ledger/build_ledger.py:533-542` 却把 `envelope.run_attempt` 与当前 `run_attempt` 精确比较。
- 独立探针原文：`ValueError: gate terminal identity mismatch: ['run_attempt']`。构造 payload attempt=1、consumer current attempt=2 即复现；当前 attempt terminal 缺失而旧 artifact 尚存时，会先错误选旧 artifact，再在 build 阶段崩溃，不会得到预期的“无匹配 required terminal”诊断。
- P1 两问：①真实使用会触发吗？会，GitHub rerun/当前 attempt artifact 未发布或 gate 先失败而旧 artifact 尚存时，路径可达。②后果能接受吗？不能；ledger job 失败且 ledger artifact 不上传，形成持久化丢账/崩溃。它不改变 `gate / gate` 主门禁判定，但命中 personal P1 数据丢失/崩溃红线。
- 修复方向（本轮不实施）：terminal 要么只允许当前 attempt，要么显式传递并校验被选中的 source attempt；不能复用允许旧 attempt 的 input/audit 选择语义而丢弃 `terminal_source_attempt`。

### P1-2：新增 required `terminal-path` 破坏 legacy ledger caller，并被吞成静默丢账

- 违反：spec 3（ledger 条目必须持久化顶层消费投影）与 spec 5 的既有 ledger 行为零回归要求；不变式是新增 G3 输入不得让既有 workflow 的 ledger writer 失效。
- 证据：`.github/actions/review-ledger/action.yml:15-17,32-36` 新增 required `terminal-path` 并无条件传给 CLI；`.github/workflows/gate.yml:385-395` 的 live legacy caller 没有该 input。缺失 input 展开为空路径时，`.github/actions/review-ledger/build_ledger.py:452-455,816-838` 抛 `ValueError: gate terminal artifact is missing or empty`。
- 独立 YAML 取证：`missing_from_legacy=expected-base-sha,expected-caller-sha,expected-repository-id,expected-reusable-workflow-sha,terminal-path`；其中 `terminal-path` 是本次新增的直接回归。CLI 空路径探针末行为 `ValueError: gate terminal artifact is missing or empty`、`exit=1`。
- 失败为何静默：legacy step 在 `:385-388` 有 `continue-on-error: true`，ledger artifact upload 在 `:411-417` 为 `if-no-files-found: ignore`，所以旧 gate 仍可能绿色但 ledger.jsonl 不生成。
- P1 两问：①真实使用会触发吗？会；legacy workflow 明确 checkout 并调用同一个 shared action，文件头声明它继续服务现有 fleet。②后果能接受吗？不能；每次 legacy run 的 ledger 记录可静默缺失，命中 personal P1 数据丢失/静默出错红线。

### P2-1：新 terminal validator 的多数拒绝分支没有“改坏即红”测试

- 违反：spec 4 的损坏 payload 矩阵与 spec 7“每处新校验有改坏即红测试”。
- 证据：`.github/actions/review-ledger/build_ledger.py:467-522` 新增 resolved 数组、item 对象/必填字段/文本类型、approver_id、rejected_count、计数一致性、rejected_reasons 形状/总数、fail_closed 等分支；`tests/test_review_ledger.py:1147-1228` 只覆盖缺失/空文件、非法 JSON、数组、缺消费块、`consumed_count` 字符串、run_id 身份错和 unsupported schema，未覆盖 `resolved` 非数组、item 缺字段、approver_id 非正整数、计数不一致、reasons 总数不一致、fail_closed 非布尔等。
- P1 两问：①真实使用会触发吗？损坏 payload 可能触发，但缺测试本身不是生产执行路径。②后果能接受吗？当前 validator 仍拒绝，运行结果暂可接受；但显式验收标准未满足，未来改宽会假绿，故判 P2。

## 工具标注 / 本仓判定 / P1 两问

| OCR 标注 | 本仓判定 | 两问（真实触发 / 后果） |
|---|---|---|
| action comment placement，low | 不成立，纯注释排版 | 否 / 可接受 |
| cross-attempt terminal，high，confirmed | P1-1，已独立复现 | 是 / 不可接受 |
| producer `None` 等同损坏，high，confirmed | 不成立；draft、skip、审计不可用等“无消费”合法为空块，spec 只要求下载/解析失败与空块区分 | 是 / 可接受 |
| producer/Outcome 使用 `Any`，medium/low | P3 backlog；当前是可信内部 typed hand-off | 否 / 可接受 |
| `inspect.getsource` 测试脆弱，medium | 并入 P2-1，不单列 | 仅测试回归 / 可接受 |
| fixture 未 pin receipt schema，high | P2 backlog，真实 producer fixture 仍喂入实际 terminal 输出 | 未由当前路径触发 / 可接受 |
| malformed count 缺少 int mismatch negative control，medium | 并入 P2-1 | 仅测试回归 / 可接受 |

OCR envelope 为 `status=reviewed`、MiniMax-M3，8 条标注中 2 条 confirmed；confirmed 的 producer-`None` 结论已按 spec 语义复核后否决，不能把工具 severity 直接当本仓 verdict。

## 红验抽查证据（原文）

在 `e0bd30f88e99decb1e35a6601768d41615defbf8` 临时 worktree 仅拷入当前 `tests/test_gate_aggregator.py`，运行修改后的 terminal golden contract；以下为原始输出关键片段，失败类型为断言失败：

```text
Preparing worktree (detached HEAD e0bd30f)
HEAD is now at e0bd30f Merge pull request #92 from zlxlabs/card/gate-20260829-01
============================= test session starts ==============================
platform linux -- Python 3.14.3, pytest-9.1.1, pluggy-1.6.0 -- /home/zlx/.cache/uv/builds-v0/.tmpIV3Z0O/bin/python
cachedir: .pytest_cache
rootdir: /tmp/gate-g3-red-vv-Zjpv4Y
collecting ... collected 1 item

tests/test_gate_aggregator.py::test_terminal_envelope_bytes_unchanged_by_rendering_work FAILED [100%]

=================================== FAILURES ===================================
___________ test_terminal_envelope_bytes_unchanged_by_rendering_work ___________

>       assert json.dumps(envelope, ensure_ascii=False, indent=2) + "\n" == _TERMINAL_GOLDEN
E       assert '{\n  "schema_version": 1,\n  "kind": "gate_terminal",\n  "repository": "zlxlabs/gate",\n  "repository_id": 123,\n  "pr_number": 42,\n  "run_id": 999,\n  "run_attempt": 1,\n  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n  "quality_result": "success",\n  "primary_result": "success",\n  "review_expected": true,\n  "is_draft": false,\n  "runner": "self",\n  "gate_result": "pass",\n  "classification": "code_pass",\n  "reason_code": "primary_pass",\n  "audit": {\n    "available": true,\n    "source_attempt": 1,\n    "artifact_name": "primary-audit-v2-1"\n  }\n}\n' == '{\n  "schema_version": 1,\n  "kind": "gate_terminal",\n  "repository": "zlxlabs/gate",\n  "repository_id": 123,\n  "pr_number": 42,\n  "run_id": 999,\n  "run_attempt": 1,\n  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",\n  "quality_result": "success",\n  "primary_result": "success",\n  "review_expected": true,\n  "is_draft": false,\n  "runner": "self",\n  "gate_result": "pass",\n  "classification": "code_pass",\n  "reason_code": "primary_pass",\n  "audit": {\n    "available": true,\n    "source_attempt": 1,\n    "artifact_name": "primary-audit-v2-1"\n  },\n  "disposition_receipt_consumption": {\n    "resolved": [],\n    "consumed_count": 0,\n    "rejected_count": 0,\n    "rejected_reasons": {},\n    "fail_closed": false\n  }\n}\n'

E           {
E             "schema_version": 1,
E             "kind": "gate_terminal",
E             "repository": "zlxlabs/gate",
E             "repository_id": 123,
E             "pr_number": 42,
E             "run_id": 999,
E             "run_attempt": 1,
E             "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
E             "quality_result": "success",
E             "primary_result": "success",
E             "review_expected": true,
E             "is_draft": false,
E             "runner": "self",
E             "gate_result": "pass",
E             "classification": "code_pass",
E             "reason_code": "primary_pass",
E             "audit": {
E               "available": true,
E               "source_attempt": 1,
E               "artifact_name": "primary-audit-v2-1"
E         -   },
E         -   "disposition_receipt_consumption": {
E         -     "resolved": [],
E         -     "consumed_count": 0,
E         -     "rejected_count": 0,
E         -     "rejected_reasons": {},
E         -     "fail_closed": false
E             }
E           }
tests/test_gate_aggregator.py:826: AssertionError
============================== short test summary info ==============================
============================== 1 failed in 0.33s ===============================
red_worktree=/tmp/gate-g3-red-vv-Zjpv4Y
exit=1
```

## 熵增审查

- `Outcome.disposition_consumption`：必须把第一次 `consume_dispositions` 的结构化结果带到 `_finish/build_terminal_envelope`；terminal 与 ledger 是两个实际消费者，非无消费者抽象。
- terminal projection block 与 ledger 侧空块/validator：这是 producer/consumer 跨 job 序列化边界，不能靠共享运行时模块；两份 schema 复制有明确第二消费者与 fail-loud 需要，不判熵增。
- `terminal-path` CLI/action input 与 terminal artifact download：连接 workflow→action→CLI 和 gate→ledger 两条真实边界，必要。
- `terminal_source_attempt`：新增但没有 consumer，且直接参与 P1-1 的身份错配；这是本 diff 的死状态/熵 +1，应删除或真正传入校验。
- 测试 helper、`gate-c-progress.md`：仅为真实 producer fixture 与进度记录服务，无运行时抽象。
- `tests/test_review_ledger.py:13` 重复 `import pytest`：无行为影响的 P3 hygiene 熵 +1，不阻塞本轮。

## 验证记录

- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py tests/test_review_ledger.py tests/test_gate_v2_contract.py`：`440 passed in 6.36s`。
- `python3 scripts/check_pinned_uses.py`：`OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative`。
- `git diff --check 2a1e4b1743b1e7126849e008201751dc77990006 7ca8afc19527403b88712a566c647da1ca164921`：无输出。

verdict: fail
