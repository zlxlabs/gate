# review r2 — G3 ledger 消费投影修复复验

- **审查对象（H0 冻结）**：`2a1e4b1743b1e7126849e008201751dc77990006..08b8c81984c3c748f36cd0aa0697d78fc7885875`
- **修复增量**：`7ca8afc..08b8c81984c3c748f36cd0aa0697d78fc7885875`（`67ba1f4` / `4ad8d31` / `08b8c81`）
- **本轮新证据**：运行时探针（从 `gate-v2.yml` ledger job 真实 heredoc 抽出 resolver 铺 attempt 矩阵实跑；以不含 `terminal-path` 的输入集直调 `build_ledger.py` CLI）。r1 是静态对抗审；本轮换家 + 换证据源。同一份 diff 的再读不算新证据。
- **风险档**：`personal`（P1 红线 = 数据丢失、静默出错、崩溃；另含「agent 给自己开绿灯」）。本 diff 核心是失败路径 / 资源账本，infra 例外不影响本轮「有无新增 P1」的单轮判定。
- **OCR**：本轮规定证据源是运行时探针，未再跑 OCR 前置（r1 已静态审）。本机 `ocr-review` 未作为本轮证据。

## 增量四问（`7ca8afc..08b8c81`）

增量 stat：7 文件，133+/22-。生产代码只动 `action.yml` / `build_ledger.py` / `gate-v2.yml`；其余为测试与进度存档。

### ① 是否只修登记在案的 findings

**是。** 对照 r1 三条：

| 登记 finding | 增量落点 | 证据 |
|---|---|---|
| P1-1 resolver 与 consumer attempt 错配 | `gate-v2.yml` `select_artifact(..., exact_attempt=current)`；input/audit 仍 `attempt <= current` | `gate-v2.yml:1144-1176`；契约测试 `test_ledger_resolver_refuses_stale_terminal_when_current_attempt_is_missing` |
| P1-2 `terminal-path` required 打断 legacy `gate.yml` | `action.yml` `required: false, default: ""`；CLI `--terminal-path` 默认 `""`；空串不投影该键 | `action.yml:15-17`；`build_ledger.py:816,838-839,637-645`；`test_legacy_ledger_caller_input_set_stays_compatible_without_terminal_path`；`gate.yml:385-395` 仍不传该 input |
| P2-1 validator 缺负例矩阵 | `test_validator_rejects_malformed_consumption_shapes` 六格 | `tests/test_review_ledger.py:1190-1215` |

`08b8c81` 把负例从六个具名 mutator 收成 parametrize if/elif，并删掉 `test_ledger_resolver_selects_current_attempt_terminal_not_an_older_one`（「两者都有 → 选当前」正例）。删的是本增量自己刚加的测试、不是登记 finding 以外的行为改动。正例由本轮探针格 `c2-both` 补实跑（见下）。进度存档只追加 r1 修复段，不改被审行为。

### ② 是否新增未经批准的抽象

**否。** `exact_attempt` 是既有 `select_artifact` 的参数，用来落实 spec 2「terminal 只认当前 / input/audit 仍 `<= current`」，不是新接口或包装层。`_ledger_resolver_python` / `_run_ledger_resolver` 是测试从真实 heredoc 抽出脚本的辅助，第二消费者是契约测试本身（跨 job 真实 producer 要求）。未新增配置项、状态 store、或无第二消费者的通用层。

### ③ 状态 / 事实源 / fallback 是否无依据增加

**否。** 方向是减法：

- terminal 选择从 `attempt <= current` 收成 `attempt == current`，去掉「旧 attempt 回退」。
- `_disposition_receipt_consumption_from_terminal` 删除 `envelope is None → empty_disposition_receipt_consumption()` 这条会把「没 terminal」投影成「无消费」的缺省（`build_ledger.py` 增量：`terminal_envelope` 改为非 Optional 传入；`build_entry` 仅在 `is not None` 时写键）。
- `terminal-path` 可选是登记的 P1-2 / spec 4 三形态，**不是**损坏路径 fallback。空串与未提供同态；提供路径但文件不存在仍 `ValueError`。

### ④ 是否留下双路径

**否。** ledger job 里 terminal 只有一处 `select_artifact(..., exact_attempt=current)`，没有并行的旧 `<= current` 调用。consumer 侧身份校验仍要求 `envelope.run_attempt == 当前 run_attempt`（`build_ledger.py:531-540`），与 resolver 对齐，不是第二条选择路径。legacy `gate.yml` 与 gate-v2 是 spec 4 明文的两种 **caller 输入集**，不是同一 caller 的双实现。

**增量审结论：四问通过，不按新增 P1 计。**

## 全量复验（`2a1e4b1..08b8c81`）与 spec 对照

全量 9 文件 733+/9-。本轮以探针为新证据；静态只用来把探针结果溯源到 spec 条目，不重复 r1 对抗阅读。

| spec | 本轮判定 | 证据 |
|---|---|---|
| 1 结构化消费块，不反解析 G4 | 持有 | `aggregate.py:362-416` 从 `consumption.consumed_receipts` 投影；测试 `test_terminal_structured_block_does_not_parse_g4_display_strings`；`uv` 相关测试 469 passed |
| 2 terminal 只认当前 attempt，缺则 fail-loud；input/audit `<= current` | 持有 | 探针矩阵四格（见下）；`gate-v2.yml:1156-1176` |
| 3 条目顶层字段，不进 `_review_summary` / `_compact_attempts` | 持有 | `build_entry:637-645`；`test_disposition_consumption_stays_out_of_review_summary_and_compact_attempts`；探针 legacy 条目 keys 无该字段、`review.attempts` 仍为空列表 |
| 4 `terminal-path` 可选三形态 | 持有 | 探针 CLI 未提供 → 缺键 exit 0；提供缺失文件 → fail-loud；空块路径由 `test_ledger_empty_consumption_when_producer_had_no_receipts` 锁 |
| 5 dedupe/conflict、sticky、评论解析、receipt 生产/校验零改动 | 持有 | `git log -L`：`parse_dispositions` / `dedupe_entries` / `post_state_comment` 在范围内 **NO COMMITS**；`consume_dispositions` 调用未改，只把已有结果写入 `outcome.disposition_consumption` |
| 6 真实 producer 契约 + 负例矩阵 + 改坏即红 | 持有 | `_producer_terminal` 走 `evaluate`+`build_terminal_envelope`；负例六格；拒绝旧 terminal 的测试在去掉 `exact_attempt=current` 时会绿变选旧（改坏即红） |

补充测试（只读取证，非本轮主证据）：

```
uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q \
  tests/test_review_ledger.py tests/test_gate_v2_contract.py \
  tests/test_gate_contract.py tests/test_gate_aggregator.py
469 passed in 4.79s
```

## Findings

本轮无新增 P1 / P2。r1 三条均被修复增量覆盖，并由运行时探针复验。

已分诊 backlog 不重复报：OCR 对 `tests/test_review_ledger.py` 三条测试脆性意见（≤P3）。

观察（不占循环、不升级）：`08b8c81` 为压行预算删了「两者都有选当前」的 pytest 正例。剩余 `refuses_stale_terminal` + 源码标记 `exact_attempt=current` 仍锁「不回退」；本轮探针格 `c2-both` 实跑选 `terminal_artifact_id=202` / `terminal_source_attempt=2`。不单独开 finding。

## 运行时探针矩阵（原文）

抽取方式：对 `.github/workflows/gate-v2.yml` 做 `yaml.safe_load`，取 ledger job 步骤 `Resolve v2 ledger artifacts` 的 `run` 脚本中 `<<'PY'` … `PY` heredoc（即 GHA 实际执行的字节，不是带 YAML 缩进的仓库原文）。脚本落 `/tmp/gate-c-r2-probes/resolver.py`（不入库）。前缀使用 resolver argv 的短前缀 `review-ledger-input-v2-` / `gate-terminal-v1-`，与选择器逻辑相同。

抽取确认：`contains exact_attempt=current: True`；`contains eligible = attempt <= current: True`。

### 格 1 — current=1 只有 attempt1

**期望**：exit 0；input 与 terminal 均选 attempt 1。

```
command: python3 - /tmp/gate-c-r2-probes/c1-only-a1-listing.json review-ledger-input-v2- primary-audit-v2- gate-terminal-v1- 1 false /tmp/gate-c-r2-probes/c1-only-a1-github_output
artifacts: [{"name": "review-ledger-input-v2-1", "expired": false, "id": 101}, {"name": "gate-terminal-v1-1", "expired": false, "id": 201}]
current: 1
exit: 0
stdout_verbatim:
<empty>
stderr_verbatim:
<empty>
github_output_verbatim:
input_artifact_id=101
input_source_attempt=1
audit_artifact_id=
audit_source_attempt=
terminal_artifact_id=201
terminal_source_attempt=1
```

**结果**：与期望一致。

### 格 2 — current=2 只有 attempt1

**期望**：terminal 不回退旧 attempt，fail-loud；不写 `GITHUB_OUTPUT`（失败发生在三路 select 之后、写 output 之前）。

```
command: python3 - /tmp/gate-c-r2-probes/c2-only-a1-listing.json review-ledger-input-v2- primary-audit-v2- gate-terminal-v1- 2 false /tmp/gate-c-r2-probes/c2-only-a1-github_output
artifacts: [{"name": "review-ledger-input-v2-1", "expired": false, "id": 101}, {"name": "gate-terminal-v1-1", "expired": false, "id": 201}]
current: 2
exit: 1
stdout_verbatim:
<empty>
stderr_verbatim:
No matching required gate terminal artifact found

github_output_verbatim:
<empty>
```

**结果**：与期望一致。此格锁死 r1 P1-1：旧实现会选出 attempt1 terminal，再在 consumer 上 identity mismatch；现在 resolver 当场退出。

### 格 3 — current=2 两者都有

**期望**：input 取 `<= current` 的最大（attempt 2）；terminal 只取 attempt 2。

```
command: python3 - /tmp/gate-c-r2-probes/c2-both-listing.json review-ledger-input-v2- primary-audit-v2- gate-terminal-v1- 2 false /tmp/gate-c-r2-probes/c2-both-github_output
artifacts: [{"name": "review-ledger-input-v2-1", "expired": false, "id": 101}, {"name": "review-ledger-input-v2-2", "expired": false, "id": 102}, {"name": "gate-terminal-v1-1", "expired": false, "id": 201}, {"name": "gate-terminal-v1-2", "expired": false, "id": 202}]
current: 2
exit: 0
stdout_verbatim:
<empty>
stderr_verbatim:
<empty>
github_output_verbatim:
input_artifact_id=102
input_source_attempt=2
audit_artifact_id=
audit_source_attempt=
terminal_artifact_id=202
terminal_source_attempt=2
```

**结果**：与期望一致。未误选 201。

### 格 4 — terminal 缺失但 input 制品在

**期望**：input 存在不能让 terminal 变可选；fail-loud 且不写 output。

```
command: python3 - /tmp/gate-c-r2-probes/term-missing-input-present-listing.json review-ledger-input-v2- primary-audit-v2- gate-terminal-v1- 2 false /tmp/gate-c-r2-probes/term-missing-input-present-github_output
artifacts: [{"name": "review-ledger-input-v2-1", "expired": false, "id": 101}, {"name": "review-ledger-input-v2-2", "expired": false, "id": 102}]
current: 2
exit: 1
stdout_verbatim:
<empty>
stderr_verbatim:
No matching required gate terminal artifact found

github_output_verbatim:
<empty>
```

**结果**：与期望一致。

### CLI A — 不含 `terminal-path`（模拟 `gate.yml` caller）

直调 `build_ledger.py` argparse/`main()`。argv **不包含** `--terminal-path`，与 `.github/workflows/gate.yml:385-395` 的 input 集一致。`GH_TOKEN` 探测桩把 `fetch_prior_entries` / `fetch_comments` / `post_state_comment` 换成空实现，只隔离网络，不改 terminal-path 分支。

**期望**：exit 0；条目缺 `disposition_receipt_consumption` 键。

```
command: python3 .../build_ledger.py --audit-path ... --preflight-path ... --install-path ... --output .../legacy-omit-terminal-ledger.jsonl --repository zlxlabs/gate --pr-number 1 --run-id 99 --run-attempt 1 --head-sha abc123 --expected-repository-id 1 --expected-base-sha base --expected-caller-sha caller --expected-reusable-workflow-sha wf --codex-expected false --codex-waived false
extra_args: []
exit: 0
stdout_verbatim:
{"schema_version": 1, "recorded_at": "2026-08-30T18:26:10.816503+00:00", "repository": "zlxlabs/gate", "pr_number": 1, "run_id": 99, "run_attempt": 1, "head_sha": "abc123", "review_round": 1, "preflight": {"reviewable": false, "diff_lines": 10, "classification": "single", "review_plan": "single", "thresholds": {"single_turn_lines": 4000}}, "install": null, "primary_identity": null, "review": {"status": "blocked_by_size", "verdict": null, "result": null, "cost_usd": null, "tokens": null, "finding_count": 0, "finding_ids": [], "severity_counts": {}, "category_counts": {}, "coverage": null, "runtime": null, "shadows": {}, "reviewer": null, "attempts": [], "failover": false}, "comparison": {"kind": "first_review"}, "finding_dispositions": {}, "convergence_projection": {"source": "disposition-observation", "required_gate_effect": "none", "statuses": {}}, "false_positive_count": 0}

stderr_verbatim:
<empty>
has_disposition_receipt_consumption: False
entry_keys: ['comparison', 'convergence_projection', 'false_positive_count', 'finding_dispositions', 'head_sha', 'install', 'pr_number', 'preflight', 'primary_identity', 'recorded_at', 'repository', 'review', 'review_round', 'run_attempt', 'run_id', 'schema_version']
```

**结果**：与期望一致。未静默补空块。

### CLI B — 提供路径但文件不存在

**期望**：fail-loud，不写 ledger。

```
command: python3 .../build_ledger.py ... --terminal-path /tmp/gate-c-r2-probes/does-not-exist/gate-terminal.json
extra_args: ['--terminal-path', '/tmp/gate-c-r2-probes/does-not-exist/gate-terminal.json']
exit: 1
stdout_verbatim:
<empty>
stderr_verbatim:
UNCAUGHT ValueError: gate terminal artifact is missing or empty

ledger.jsonl: <missing>
```

**结果**：与期望一致。`UNCAUGHT` 前缀来自探针 harness 把未捕获异常打到 stderr；生产异常是 `load_gate_terminal_envelope` 的 `ValueError`（`build_ledger.py:452-455`）。无 ledger 文件，不是损坏路径 fallback。

## 熵增审查

对照 `REFACTOR-guide.md` 坏味道词表，逐项问「是不是熵 +1」：

| 增量新增 | 词表 | 判定 |
|---|---|---|
| `select_artifact(..., exact_attempt=None)` | 多余路径 / 投机通用性 | 否。单函数两谓词对应 spec 2 两种制品身份，不是第二套 resolver |
| `terminal-path` 改为可选 | 生命周期重复 / 错位防御 | 否。去掉 required 是恢复 legacy 第二消费者，不是新开关 |
| 删除 `None → 空块` | — | 熵 −1 |
| 测试 `_ledger_resolver_python` | 自建基础设施 | 否。抽出真实 heredoc 跑，禁止平行实现 |
| 负例 if/elif 相对具名 mutator | — | 熵 −1（`08b8c81` 收缩） |
| `empty_disposition_receipt_consumption` 在 aggregator 与 ledger 各一份 | 镜像事实 | 全量引入、增量只改 docstring。跨 job 禁止共享模块（进度存档已记录）。测试用它比对 producer 空块。不新开 finding |

未发现无第二消费者的新接口/包装层/状态/配置项。

## 本仓 P1 两问（无新 finding，存档用）

无候选 P1。若把「删掉两者都有正例测试」强行过两问：真实使用方式下 resolver 仍按 `exact_attempt=current` 运行（探针格 3 已触发）；后果可接受。→ 不是 P1。

verdict: pass
