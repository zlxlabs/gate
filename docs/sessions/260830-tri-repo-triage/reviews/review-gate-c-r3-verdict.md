# review-gate-c r3 verdict —— G3 ledger 消费投影降层审查

- 审查对象（H0 冻结）：`2a1e4b1743b1e7126849e008201751dc77990006..08b8c81984c3c748f36cd0aa0697d78fc7885875`
- 审查者：kimi（独立会话，dispatch `dlg-20260830-183126-948921`）；r1=codex 静态对抗审，r2=cursor 运行时探针矩阵，本轮 r3=部署形态/生命周期抽象层（降层三问），与实现/r1/r2 会话均隔离。
- 风险档：`personal`（AGENTS.md 首屏）——P1 红线 = 数据丢失、静默出错、崩溃；另含威胁模型「agent 给自己开绿灯」。
- 审查日期：2026-08-31。

## 本轮新证据声明

本轮不重复 r1/r2 的证据源（静态对抗、attempt/legacy 矩阵、validator 负例）。新证据为：

1. **降层取证**：沿 gate-v2.yml job 依赖图与 GitHub Actions 官方语义文档，逐形态推演 run_id/run_attempt/artifact 命名的生命周期（此前两轮未引用官方语义出处）。
2. **新探针矩阵（6 格，真实执行，原文贴下）**：resolver 在 rerun-failed-jobs / 全量 rerun / 异 run_id 污染三种部署形态下的行为；`main()` 级端到端探针覆盖损坏 terminal 的写前失败、legacy 空 `terminal-path`、真实 producer 全链路三格。探针脚本在 `/tmp/gate-r3-probes/probe.py`，未入库。
3. **独立红验**：在 `/tmp/gate-r3-redverify` 临时副本中删掉 `validate_disposition_receipt_consumption` 的 `consumed_count != len(projected)` 校验，确认对应负例测试转红（原文贴下）。
4. **全量测试与 pin 检查实跑**：`709 passed in 8.81s`；`check_pinned_uses.py` 输出 `OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative`。

## 降层三问

### 问 1：终态写入成功之前已发生哪些不可逆动作？ledger 落盘失败后重跑能否收敛？

**Job 依赖图**（`.github/workflows/gate-v2.yml`）：`quality`(:109)、`primary`(:361) → `gate`（:864, `needs: [quality, primary]`, `if: always()`）→ `ledger`（:1095-1097, `needs: [quality, primary, gate]`, `if: always()`）；`notify`（:1238-1240, `needs: gate`, `if: failure()`）与 ledger **并行**。

ledger 条目落盘（`build_ledger.py` `write_ledger`，main() 倒数第 6 行）失败时，以下动作**已经发生**，按不可逆程度排：

1. **terminal artifact 已上传**：gate job `Upload gate terminal envelope`（gate-v2.yml:1040-1047，`if: always()`）。artifact v4 不可变，无法收回；但这恰是消费事实的持久副本，不算损失。
2. **sticky comment / 状态面板已发布**：`Publish gate status panel`（:1049-1083）在 ledger job 启动前就把面板 POST/PATCH 到 PR（`_post_status_panel_fail_open`，aggregate.py `_publish_only` :1883+）。外部可见；可自愈（下次发布覆盖同一 sticky），不算严格不可逆。
3. **notify webhook POST**：`notify` job 只 `needs: gate`，gate 失败即触发（:1238-1240），与 ledger 是否落盘**完全无关**。这是真正不可逆的外部动作（webhook 已送达）。
4. **PR 合并决策本身**：required check 由 gate job 结论决定，不等 ledger。即「terminal 说消费了、gate 已放行、PR 可合并」与「ledger 没记」可以同时成立——这是观测投影的固有位置，不是本 diff 引入的。

**失败后重跑能否收敛**（结合问 2 的 attempt 语义）：

- **全量 rerun（Re-run all jobs）**：attempt+1，gate 重跑并上传 `gate-terminal-v1-...-<attempt+1>`，resolver `exact_attempt=current`（gate-v2.yml:1173-1176）选中它，ledger 收敛。消费内容由同一批 receipt + 同一 head_sha（run 内 `github.event.pull_request.head.sha` 固定）重算，内容一致；dedupe 键为 `(repository, run_id, run_attempt)`（build_ledger.py:663），新 attempt 生成新条目，账面内容层面收敛。
- **rerun-failed-jobs（仅重跑失败 job 及其下游）**：若 gate 已成功、仅 ledger（或并行的 notify）失败，gate **不重跑**，当前 attempt 的 terminal 不存在，resolver 直接 `SystemExit("No matching required gate terminal artifact found")`——ledger 在此 run 上**永久 fail-loud**，只能 full rerun 解锁。见探针 A。这是 spec 第 2 条明文要求的行为（「当前缺失即 fail-loud 不回退旧 attempt」），其操作代价记为 finding F1。
- **无 rerun**：terminal artifact 仍在（含完整消费块），且 main() 在 `write_ledger` 后、`post_state_comment` 前 `print(json.dumps(entry))`（build_ledger.py main 尾部）——构建成功但上传失败时条目可从 job log 回收；构建期校验失败则条目本就不该存在（fail-loud 即设计意图）。
- **写前失败不污损存量**：terminal 加载在 `fetch_prior_entries`（网络）与 `write_ledger` 之前（build_ledger.py:838-839），损坏 terminal 时已有 output 文件原样保留——探针 D 实证。

**结论**：「terminal 说消费了、ledger 没记」的缺口可达，但 (a) 它一定伴随 workflow 红（非静默），(b) 事实有 terminal artifact + job log 两个副本，(c) full rerun 可收敛。不命中 personal 档 P1 红线。

### 问 2：守卫值（run_id + run_attempt + artifact 命名）在实际部署形态下自身唯一吗？

官方语义出处（[GitHub Docs: contexts — github context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#github-context)，本次实取原文）：

- `github.run_id`：「A unique number for each workflow run within a repository. This number does not change if you re-run the workflow run.」
- `github.run_attempt`：「A unique number for each attempt of a particular workflow run in a repository. This number begins at 1 for the workflow run's first attempt, and increments with each re-run.」——**任何 re-run（含 re-run failed jobs）都 +1，不存在 attempt 不变的 rerun**。
- [actions/upload-artifact README](https://github.com/actions/upload-artifact/blob/main/README.md)：「Artifact names must be unique since each created artifact is idempotent…Artifacts created by upload-artifact@v4 are immutable.」同 run 同名上传直接 conflict 失败（本 workflow 未用 `overwrite:`）。

逐形态对照（artifact 名模板 `gate-terminal-v1-<repository_id>-<head_sha>-<run_id>-<attempt>`，gate-v2.yml:1045；resolver listing 按 run 作用域 `repos/.../runs/${{ github.run_id }}/artifacts`，:1125）：

| 形态 | artifact 名撞？ | attempt 语义漂？ | 证据 |
|---|---|---|---|
| 同一 PR 并发两个 workflow run | 否：run_id 仓内唯一且进名字；listing 本身按 run_id 隔离 | 不漂 | 官方 run_id 定义 + gate-v2.yml:1125；ledger 另有仓级 concurrency group（:1098-1100, `cancel-in-progress: false`）串行化 |
| rerun-failed-jobs（gate 已成功不重跑） | 不撞（新 attempt 无 terminal 可传） | attempt+1；当前 attempt terminal 缺失 → fail-loud（设计如此） | 探针 A；官方 run_attempt 定义 |
| 全量 rerun | 不撞：attempt 后缀区分 | attempt+1，gate 重跑产出新 terminal，resolver 精确选中 | 探针 B |
| fork PR | 否：repository_id 为 base 仓、head.sha 为 fork head、run_id 唯一 | 不漂 | gate-v2.yml:1118 前缀构成；fork 时 REVIEW_EXPECTED=false 但 gate job `if: always()` 仍上传 terminal |
| workflow_call 嵌套 | 否：reusable workflow 与 caller 同一 run，run_id/attempt 共享 | 不漂 | templates/caller-gate-v2.yml:89（`uses: zlxlabs/gate/.github/workflows/gate-v2.yml@__PINNED_GATE_SHA__`） |
| 同 attempt 两个同名 terminal | 不可能：v4 同名 conflict 失败 | — | upload-artifact README（上引） |
| listing 混入异 run_id 同名制品（假设性污染） | 前缀含本 run_id，直接不匹配 → fail-loud | — | 探针 C |

**结论**：守卫值在全部五种真实部署形态下唯一且语义不漂。rerun-failed-jobs 形态的唯一代价是 F1 记录的 fail-loud 死锁（spec 明文选择），不是唯一性缺陷。

### 问 3：保护覆盖的是「写入」还是「行为」？

**已覆盖（写入侧）**：`validate_disposition_receipt_consumption`（build_ledger.py 新增）对块做结构 + 计数一致性 + 类型校验；`_disposition_receipt_consumption_from_terminal` 另做五字段身份比对（repository/pr_number/run_id/run_attempt/head_sha，与 envelope 逐一相等，否则 `identity mismatch`）。校验对象就是**将被逐字投影进 ledger 条目的那个块本身**（`entry["disposition_receipt_consumption"] = ...`，build_ledger.py:637-645），不是中间代理产物——量纲与「ledger 条目内容损坏」这一失败量纲一致。

**未覆盖（行为/语义侧）**：「resolved 列表 == aggregator 实际放行的 finding 集合」这一语义等价，**运行时没有任何一道防线**：

- 块由第一趟 `consume_dispositions` 的结构化对象直接投影（aggregate.py:767-770 注释明示 rejected 不到 `evaluate_round`），与最终 gate 放行之间只共享 producer 同一份内存对象；若 producer 写出结构合法但内容错误的块（如 finding_id 张冠李戴、consumed_count 与实际放行不符），ledger 侧校验全部放行。
- ledger job 手里有独立事实源（audit artifact 的 findings 列表）可做交叉校验（resolved finding_ids ⊆ audit P1 ids、consumed_count>0 与 envelope gate 结果的一致性），**未实现**。
- 现有防线只有 producer 侧测试（tests/test_gate_aggregator.py:2177+ 四条）与本仓 review。

**判定**：按「防线量纲必须与失败量纲一致」（review-discipline #617）这是一处结构性缺口，但失败量纲要认清——ledger 是**观测投影**，不反喂 gate 放行；威胁模型关心的「agent 给自己开绿灯」路径（gate job 结论 → required check）不经过 ledger。producer 写错内容需要 producer 代码本身出 bug，而该代码走同一门禁。后果可接受 → 记 F2（P3），不修。

## 探针矩阵（真实执行，原文）

执行环境：`/tmp/gate-r3-probes/probe.py`（临时目录，未入库），`uv run --with PyYAML python probe.py`。resolver 探针从 gate-v2.yml 实时抽取 heredoc 内嵌 Python 执行（与 tests/test_gate_v2_contract.py 的 `_ledger_resolver_python` 同法）；main() 探针掐掉与本 diff 无关的网络出口（`fetch_prior_entries`/`fetch_comments`/`post_state_comment` stub 为空）。

```text
=== Probe A: rerun-failed-jobs 形态 —— current attempt=2, terminal 只有 attempt=1 ===
exit=1
stdout+stderr: No matching required gate terminal artifact found
outputs file: ''

=== Probe B: 全量 rerun 形态 —— current attempt=2, terminal 在 attempt=2 ===
exit=0
outputs file: input_artifact_id=102
input_source_attempt=2
audit_artifact_id=
audit_source_attempt=
terminal_artifact_id=202
terminal_source_attempt=2

=== Probe C: 异 run_id 同名污染 —— listing 里混入 run 777 的 terminal ===
exit=1
stdout+stderr: No matching required gate terminal artifact found

=== Probe D: terminal 损坏 → main() 在写 output 之前失败，已有 output 不被触碰 ===
main() -> ValueError: gate terminal artifact is not valid JSON
output file after failure: 'SENTINEL\n'

=== Probe E: legacy 形态 —— terminal-path 空串 → 条目无该字段、exit 0 ===
{"schema_version": 1, "recorded_at": "...", "repository": "zlxlabs/gate", "pr_number": 42, "run_id": 555, "run_attempt": 1, ..., "false_positive_count": 0}
main() -> 0
entry has disposition_receipt_consumption: False

=== Probe F: 真实 producer terminal 走 main() 全链路 → 不误拒 ===
producer gate_result: pass | consumed: 1
main() -> 0
ledger block: {"consumed_count": 1, "fail_closed": false, "rejected_count": 0, "rejected_reasons": {}, "resolved": [{"approved_at": "2026-08-30T12:00:00Z", "approver": "octocat", "approver_id": 1, "finding_id": "p1", "reason": "locked upstream behavior", "receipt": "gate-disposition-receipt-v2-59356e...-aaaaaaaaaaaa-p1"}]}
```

（Probe E/F 的完整 JSON 行较长，关键字段已保留；Probe F 同时实证语义一致性 happy path：consumed=1 时 producer `gate_result: pass`。）

## 独立红验（改坏即红抽查）

在 `/tmp/gate-r3-redverify`（临时副本）删除 `validate_disposition_receipt_consumption` 的 `consumed_count != len(projected)` 校验（注入已生效：`grep -c "consumed_count does not match resolved"` 由 1 → 0）。首次运行撞出 `ModuleNotFoundError: No module named 'scripts'`（副本漏拷 `scripts/`，注入方式问题，按红验有效性条款换最小注入重验）；补拷后重跑：

```text
>       with pytest.raises(ValueError, match=match):
E       Failed: DID NOT RAISE ValueError

tests/test_review_ledger.py:1214: Failed
FAILED tests/test_review_ledger.py::test_validator_rejects_malformed_consumption_shapes[consumed_count-consumed_count does not match resolved]
1 failed, 5 passed in 0.15s
```

红的类型为明确的期望失败（`DID NOT RAISE`），且**只有被改坏的那一格转红**、其余 5 格保持绿——该校验非恒真，负例矩阵在测真实对象。

## 全量测试与 pin 检查

```text
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
709 passed in 8.81s
$ python3 scripts/check_pinned_uses.py
OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative
```

## 逐条 finding

### F1（P2，溯源 spec 第 2 条——明文设计代价，记录不修）

**rerun-failed-jobs 在「gate 已成功、仅 ledger/notify 失败」形态下对 ledger job 形成永久 fail-loud**：attempt+1 后 gate 不重跑、当前 attempt 无 terminal，resolver `SystemExit`（gate-v2.yml:1173-1176）；run 保持红直到 owner 手动 full rerun（full rerun 会重跑模型主审，有成本）。

- 证据：探针 A 原文；官方 run_attempt 语义（问 2 引用）；octokit/API 文档「Re-run all of the failed jobs **and their dependent jobs**」（gate 是 ledger 的上游不是下游，故不重跑）。
- P1 两问：①真实使用方式下会被触发吗？——会：ledger job 任何瞬态失败（gh api 抖动、runner 掉线）后点「Re-run failed jobs」是 GitHub 默认 UI 动作。②后果能接受吗？——能：红色是显式信号（非静默出错）；terminal artifact 与 job log 各留一份消费事实（非数据丢失）；full rerun 可收敛；不崩溃。两问不同时过 → 非 P1。
- 与 spec 关系：spec 第 2 条明文「当前缺失即 fail-loud 不回退旧 attempt」，本 finding 是该条款的代价面而非违例；按评审纪律不判 fail，留档供后续轮次/owner 决策（如需缓解，方向是 caller 文档写明「ledger 红请用 Re-run all jobs」，不是给 resolver 加回退）。

### F2（P3，溯源降层问 3；无 spec 条款对应，已按规降一级）

**消费块语义正确性无运行时防线**：结构合法但内容错误的块（producer bug）会穿透全部校验落进 ledger；ledger job 持有 audit artifact 却不做 resolved⊆audit-findings 之类的交叉校验。防线量纲分析见问 3。不影响 gate 放行路径，ledger 为纯观测投影，personal 档后果可接受。

### F3（P3，熵增审查；溯源 spec 第 1/3 条的实现方式）

**`build_ledger.py` 的 `empty_disposition_receipt_consumption()` 无生产 caller**：生产路径的空块由 producer 写入 terminal、ledger 只复制；该函数的唯一消费者是 tests/test_review_ledger.py:1080 的形状 canary（producer 空块形状漂移即红）。进度存档里程碑③记录了理由（跨 job 发布边界禁止共享模块）。属「单（测试）消费者辅助函数」，有已发生失败之外的明确理由，可保留；记录以防后续轮次重复报。

## 误拒方向扫描（本轮新角度：正确的 receipt 会不会被新校验错杀）

结论：**不存在误拒路径**。逐条核对：

1. **resolved 项校验 ⊆ producer 准入**：能进 `consumed_receipts` 的 receipt 必过 `validate_disposition_receipt`（convergence.py:528-535：`reason`/`approver` 非空、`approver_id` 严格正 int、`approved_at` 含时间、`finding_id` 非空）——ledger validator 的逐项要求（build_ledger.py 新增 `for key in (...)` 循环）是其**严格子集**，已消费的合法 receipt 不可能被投影层拒。
2. **rejected_reasons 键全非空常量**：来源只有 `validate_disposition_receipt` 的固定 reason 串 + `"finding_already_consumed"`（convergence.py:609-641；`absent_legacy_stub` 与 `duplicate_receipt_noop` 走 `continue` 不进 rejected）——不会出现空键触发 `rejected_reasons must map reasons to positive counts` 的误杀。
3. **身份五字段类型一致**：producer 写 int 的 run_id/run_attempt/pr_number 与 ledger CLI `type=int` 解析对齐（探针 F 全链路实证，`identity mismatch` 未触发）。
4. **legacy 三形态**：`terminal-path` 缺省 `""` → `terminal_envelope=None` → 条目缺键、exit 0（探针 E）；gate.yml:385-395 legacy caller 不传该参数且带 `continue-on-error: true`，契约测试 tests/test_gate_contract.py:362-374 锁死。
5. **正向对照**：真实 producer 产物走 `main()` 全链路 exit 0、块逐字投影（探针 F）；`tests/test_review_ledger.py` 等 4 文件 469 条相关测试全绿，全量 709 绿。

## 熵增审查

对 diff 中每个新增抽象/辅助逐项过一遍「是不是熵 +1」：

| 新增物 | 第二消费者/必要性 | 判定 |
|---|---|---|
| `aggregate.py` `project_disposition_receipt_consumption` | ledger 是第二消费者（docstring 明示） | 非熵 |
| `aggregate.py` `empty_disposition_receipt_consumption` | producer 自身 :370 调用 + 测试对照 | 非熵 |
| `build_ledger.py` `empty_disposition_receipt_consumption` | 无生产 caller，仅测试 canary（F3） | P3 记录 |
| `build_ledger.py` `_strict_int` | validator 内 6+ 处调用 | 非熵 |
| `load_gate_terminal_envelope` / `validate_*` / `_from_terminal` 三段链 | 分别服务 CLI 加载、纯校验、build_entry 投影三入口 | 非熵 |
| schema_version/kind 双重检查（load 与 `_from_terminal` 各查一次） | `_from_terminal` 可被未过 loader 的调用方直达（测试即如此），3 行 | 可接受，记录 |
| 测试辅助 `_terminal_for`/`_producer_terminal`/`_write_terminal`/`_run_ledger_resolver` | 前三个各被 ≥4 条测试复用；`_run_ledger_resolver` 当前单调用方但承载 resolver 探针族 | 边界可接受 |

修复增量（`7ca8afc..08b8c81`，3 提交 133+/22-）无新增未经批准的抽象、无 fallback/兼容分支引入（`terminal-path` 可选是输入可选性，legacy caller 是真实第二消费者，进度存档 r1 修复段已记此区分）。

## spec 符合性对照

1. ✅ 结构化消费块持久化：aggregate.py:351-389（投影函数）、:414-416（envelope 恒写）、:767-770（取自第一趟 `consume_dispositions` 结构化对象，不反解析 G4 字符串；测试 `assert "resolved by receipt" not in json.dumps(block)` 锁死）。
2. ✅ resolver 只接受当前 attempt、缺失 fail-loud；input/audit 保持 `attempt <= current`：gate-v2.yml:1156-1161, :1173-1176。
3. ✅ 顶层字段、不进 `_review_summary`/`_compact_attempts`：build_ledger.py:637-645；测试含 `inspect.getsource` 源码级断言。
4. ✅ 三形态可区分：探针 D/E/F + 6 格 validator 负例矩阵。
5. ✅ dedupe/conflict、sticky comment、评论解析通道、receipt 生产/校验零改动（diff 全量核对：仅 4 个实现文件 + 3 个测试文件 + 进度存档）。
6. ✅ 真实 producer 契约测试 + 负例矩阵 + 改坏即红（本轮独立红验复证一格）。

## 存量观察（backlog，不占本轮循环）

- `codex-review-ledger-v2` 上传名固定无 run/attempt 后缀（gate-v2.yml:1226），在「上传成功但后续 step 失败再 rerun」形态下与 v4 同名不可变规则的交互属存量行为，本 diff 未触碰，留 backlog 供单独核实。
- OCR 对 tests/test_review_ledger.py 的三条测试脆性意见已按卡面要求不重复报。

verdict: pass
