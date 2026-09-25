# Gate disposition 受控出口契约（2026-09-25）

## 目标与决策

`gate-disposition` 是 gate-v2 唯一可以解除当前轮 P1 的受控出口。操作者身份仍不可证明；owner 接受这一限制，不加签名、nonce、撤销、Environment 审批或人机区分。出口由证据/跟踪引用、绑定校验、逐条生效和 terminal/ledger 留痕约束。

`codex-review-waived` 标签在 v2 无效；v2 caller 不监听 `labeled`。reviewer 自报 `not_expected` / `waived` 仍由 aggregator 无条件拒绝，回执不是 verdict 豁免。

## 回执 JSON

Artifact name 前缀为 `gate-disposition-receipt-v3-`。producer 写入 canonical JSON 字节；`kind` 与 schema 版本必须一致。共用字段及两种处置专属字段如下：

| 字段 | 类型 | 必需性 | 约束 |
|---|---|---|---|
| `kind` | string | 必需 | `gate-disposition-receipt-v3` |
| `schema_version` | integer | 必需 | `3`；v1/v2 回执拒收 |
| `disposition` | string | 必需 | `false-positive` 或 `deferred` |
| `repository_id` | string | 必需 | 当前仓库 ID |
| `pr_number` | integer | 必需 | 正整数 |
| `epoch` | string | 必需 | 当前 canonical scope 派生值 |
| `head_sha` | string | 必需 | 当前 PR head |
| `audit_digest` | string | 必需 | 当前 canonical primary audit digest |
| `finding_id` | string | 必需 | 当前 P1 finding 的人类可读 ID |
| `finding_key` | string | 必需 | 当前 P1 finding 的稳定精确 key；匹配歧义拒绝 |
| `reason` | string | 必需 | 非空；展示时截断至 500 字符 |
| `approver` | string | 必需 | producer 收到的 GitHub actor 名称，不代表已验证审批 |
| `approver_id` | integer | 必需 | 正整数；与 `approver` 同取 `github.actor` / `github.actor_id` 上下文 |
| `approved_at` | string | 必需 | ISO-8601 时间戳，保留原字段名，不表示人工审批事实 |
| `triggering_actor` | string | 必需 | GitHub actor 值及来源，仅作留痕 |
| `triggering_actor_source` | string | 必需 | `env`、`cli` 或 `envelope` |
| `counterevidence` | object | 仅 false-positive | 含 `command`、`output`、`result`、`pointer` 四个非空字符串；`result` 必须等于 `refuted` |
| `tracking_issue` | string | 仅 deferred | `#<正整数>` 或本仓 `/issues/<正整数>` URL；原值保留在 summary 和 ledger |

False-positive 示例：

```json
{
  "kind": "gate-disposition-receipt-v3",
  "schema_version": 3,
  "disposition": "false-positive",
  "repository_id": "123",
  "pr_number": 42,
  "epoch": "<sha256>",
  "head_sha": "<40-hex-sha>",
  "audit_digest": "<sha256>",
  "finding_id": "finding-1",
  "finding_key": "<stable-finding-key>",
  "reason": "counterexample refutes the finding",
  "approver": "octocat",
  "approver_id": 1,
  "approved_at": "2026-09-25T10:00:00Z",
  "triggering_actor": "octocat",
  "triggering_actor_source": "env",
  "counterevidence": {
    "command": "pytest -q tests/test_example.py",
    "output": "1 passed",
    "result": "refuted",
    "pointer": "tests/test_example.py::test_regression"
  }
}
```

Deferred 使用相同共用字段，把 `disposition` 设为 `deferred`，移除 `counterevidence`，并加入 `tracking_issue`。不允许两种专属字段同时出现。

## Workflow inputs

`workflow_dispatch` 和 `workflow_call` 的 input 类型均为 `string`。已有五个 required inputs 和 permissions 保持不变；新增输入全部 optional，因此旧 caller 仍能启动，但省略 false-positive 反证会在 artifact 上传前以 `counterevidence_required` 拒签。

| Input | 类型 | 必需性 | 用途 |
|---|---|---|---|
| `pr_number` | string | required（既有） | 当前 PR |
| `primary_run_id` | string | required（既有） | canonical primary run |
| `primary_run_attempt` | string | required（既有） | canonical primary attempt |
| `finding_id` | string | required（既有） | 精确目标 finding ID 或 stable key |
| `reason` | string | required（既有） | 非空处置理由 |
| `disposition` | string | optional（新增） | `false-positive` 或 `deferred`；省略时按 false-positive 处理并要求反证 |
| `counterevidence_json` | string | optional（新增） | false-positive 的 JSON 反证对象 |
| `tracking_issue` | string | optional（新增） | deferred 的同仓 issue 引用 |
| `gate_ref` | string | optional（既有，deprecated） | 兼容保留，不参与处置 |

Workflow 权限仍为 `actions: write`、`contents: read`、`pull-requests: read`；不得增加 `issues: read` 或 required input。producer 使用 `GITHUB_REPOSITORY` 做 URL 同仓校验，不请求 GitHub issue API。

## 签发与消费规则

| 处置 | Producer 约束 | Consumer 约束 |
|---|---|---|
| `false-positive` | 只签 inferred P1；反证 JSON 的四字段均非空且 `result=refuted` | 重验反证、tier 和当前绑定后，只移除精确指向的一条 P1 |
| `deferred` | 允许任意 trigger kind 的 P1；issue 只校验 `#N` 或本仓 `/issues/N` 格式，不查询 issue 存在/open | 重验格式、仓库、tier 和当前绑定；`saas` 拒收，reason 为 `deferred_not_allowed_for_tier` |

Tier 来自 canonical primary audit 的 `tier`。缺失或不在 `personal/internal/saas` 域内时按 `internal`。`personal` 和 `internal` 可使用两种处置；`saas` 仅允许 `false-positive`。

每张 active 回执绑定 `repository_id + pr_number + head_sha + audit_digest + epoch + finding_id/finding_key`。Consumer 先校验 `head_sha`，再校验 `epoch`、digest 和 finding，因此旧 head 明确诊断为 `head_sha_mismatch`；同 head 的旧 epoch 诊断为 `epoch_mismatch_stale`，旧 digest 为 `audit_digest_mismatch`。旧 schema、缺证据、无效 tier/issue、重复或歧义均不能扣减 P1。

一张回执只覆盖它指向的一条当前 P1。若所有 P1 都被有效回执覆盖，本轮按无 P1 计算，`gate_result=pass` 且 clean streak 正常累加；部分覆盖继续 `code_fail / primary_findings`，剩余 P1 保留在 terminal 投影中。已通过的 primary 不会因无效回执变 fail；原本 fail 的 primary 也不会被拒收回执变 pass。

被消费的回执在 Step Summary 和 ledger 各写一行，包含处置种类、finding 和证据 pointer。false-positive 的 `command`、`output`、`pointer`、`reason` 均按 500 字符上限投影；deferred 的原始 `tracking_issue` 必须可见。拒签在 artifact upload 前退出非零，并输出可 grep reason；消费侧拒收写入有界 rejected-reason 计数。

## 关键测试落点

- `tests/test_gate_disposition_issue_receipt.py`：真实 producer 子进程、artifact 字节、缺反证、tracking issue 格式/同仓和 SaaS 拒签。
- `tests/test_gate_convergence.py`：v3 证据校验、tier、精确逐条覆盖、重复与旧绑定拒收。
- `tests/test_gate_aggregator.py`：全覆盖 pass、部分覆盖 fail、终态 remaining P1 和拒收诊断。
- `tests/test_review_ledger.py::test_real_disposition_producer_receipt_flows_through_ledger`：真实 producer 字节经过 aggregator、terminal 到 ledger；另有未覆盖 P1 负例。
- `tests/test_review_ledger.py::test_saas_deferred_is_rejected_by_producer_and_aggregator`：producer 非零退出与手写回执 consumer 拒收。
- `tests/test_gate_v2_contract.py`：旧 caller 输入兼容、approver/ID 同上下文，权限和 trigger 不扩大。

gate-hub#1073 当前一条验收文字要求 internal deferred 被拒，与本契约及 owner 锁定决策冲突；执行按 internal 允许 deferred，主脑需修订该 issue 的判据。
