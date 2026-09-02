verdict: pass

# gate PR #119 r1 独立全量评审 verdict

第 1 轮。方向 = 正向全量（quality `needs`/`if` 字面量、aggregator 短路格、契约测试、design.md 已裁决 A）+ **降层三问** + 反向抽查（`evaluate` 四格）+ 熵增。风险档 personal（`AGENTS.md:3`），改动核心是 job 依赖图 / 失败路径，按 core-lead.md infra 例外提一档用 internal 收敛条件审视。无 P1。P2 一条（ledger 把设计内 skip 写成 `unknown` 上传失败），不阻塞。

审查对象冻结 `dac7c9c73629f056270adba9158866f31238f97f..4af1459e18f1340020005e4b7f6fa2ccfca1c329`。spec = PR #119 正文 + issue #105 第一条 + 本分支对照 H0 `docs/sessions/260902-gate-triage/design.md`「已裁决」节。已否决 B/C、改 gate 汇总 job 的 needs/if、改 concurrency 锁形态，不作为 finding 重提。

## 本轮新证据

本轮是该 diff 的第一轮独立审查，证据不是「再读一遍同一份 diff」：

- OCR：`ocr-review --from dac7c9c73 --to 4af1459e18`，`status=reviewed`，`profile=minimax` / MiniMax-M3，`coverage=complete`，3 条 medium/low（复核器超时未核实）。逐条对照表见下，无一升 P1。
- H0 临时 worktree `/tmp/review-119` @ `4af1459e18`：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_aggregator.py` → **307 passed in 14.36s**。`SHELLCHECK_OPTS=--severity=warning actionlint .github/workflows/gate-v2.yml` → exit 0。
- 红验：base worktree `/tmp/review-119-base` @ `dac7c9c73`，仅拷入 H0 的两个测试文件。注入确认：`grep` 见到新测试名；`yaml.safe_load` 确认 base 上 `quality` 无 `needs`/`if`。三条新断言全红：`test_quality_needs_primary_and_short_circuits_only_on_primary_failure`（`KeyError: 'needs'`）、`test_quality_skipped_by_primary_failure_reports_short_circuit_not_quality_problem`（`assert False`，找不到 short-circuit 文案）、`test_terminal_classification_matrix[kwargs9-expected9]`（`('ci_failure','quality_skipped','fail') != ('code_fail','primary_findings','fail')`）。
- 直调 H0 `evaluate` 四格 + CLI 真写 `gate-terminal.json`（命令与输出见「反向抽查」「降层三问③」）。
- GitHub 文档原文：`needs.<job_id>.result` 四值、`timeout-minutes`「automatically cancels」、`jobs.<job_id>.if` skipped 例句、concurrency「queued / pending」。

## Findings

### P2-1. ledger 把「设计内跳过 quality」误报成 `quality upload outcome: unknown`

- **违反**：design.md 不变式 4 的文案契约（「缺失文案含 quality 的上传结论字面量」）在本 PR 新开的 skipped 路径上落到空 output → 默认 `unknown`；PR #119 正文「判定语义不变」只锁了 `gate / gate`，ledger 解析器（PR #115）未随短路更新。
- **代码**：`gate-v2.yml:1150-1152` ledger `needs: [quality, primary, gate]` + `if: always()`（quality skipped 仍跑）；`:1178` `QUALITY_LEDGER_INPUT_UPLOAD: ${{ needs.quality.outputs.ledger_input_upload }}`；`:1200` `(os.environ.get(...) or "").strip() or "unknown"`；`:1232-1236` `select_artifact(..., required=True)` → `SystemExit("No matching required ledger input artifact found (quality upload outcome: {quality_upload})")`。
- **工具标注**：OCR finding 3（low）指向空 `ledger_input_upload` 与缺端到端断言。本仓判定 **P2**。
- **两问**：①真实使用会触发吗？会。方案 A 下 primary 失败（样本 26%）quality 被 skip，skipped job 不产生 outputs，env 为空 → `unknown`，再因无 input artifact 必 `SystemExit`。这是读 H0 解析器真实行、不是从形态推断。②后果能否接受？`gate / gate` 仍按 primary 终态红（CLI `ok` 恒 False），不静默绿、不丢源码。但失败路径（本 PR 存在的理由）会缺一条 ledger，并把「没跑」写成「上传结果未知」。不构成 personal P1（数据丢失 / 静默出错 / 崩溃），故 P2，可接受不修、记 backlog。

无 P1。OCR 另两条见对照表，不立 finding。

### 正向：契约核对

| 查过什么 | spec / 不变式 | 为何没问题 |
|---|---|---|
| quality `needs`/`if` | PR 正文 / 已裁决 A：`needs: [primary]` + `if: always() && needs.primary.result != 'failure'` | H0 `gate-v2.yml:128-129` 逐字；`test_quality_needs_primary_and_short_circuits_only_on_primary_failure` 锁整句字面量。 |
| `always()` 必要 | cancelled / skipped 仍跑 quality，只 skip failure | 注释 `gate-v2.yml:118-127` 与 `:58-62` 写明；默认 `success()` 会连 cancelled/skipped 一起跳过，与官方 `jobs.<job_id>.needs`「unless … conditional」一致。 |
| 不改 gate needs/if | 已否决 B；卡面禁止 | `gate:919-927` 仍 `needs: [quality, primary]` + `if: always()`。 |
| 不改锁形态 | 已否决；不变式 6 | quality `:147-149` / primary `:433-435` 仍独立 per-PR `cancel-in-progress: true`；契约测试既有锁字节继续绿（H0 307 passed）。 |
| aggregator 短路 | skipped+failure 不打 `quality_skipped`，classification/reason 由 primary 决定 | `aggregate.py:666-676` `quality_short_circuited`；矩阵 `kwargs9` + 专测锁文案；CLI 见下。 |
| `(skipped, success)` 维持 | 「其它 skipped 组合语义不变」 | 反向抽查第二格仍 `ci_failure`/`quality_skipped`。 |
| design.md 已裁决 A | 待裁决 → 已裁决，数据 + 否决 B/C | H0 `:46-54`。非目标第 3 条仍写「本批不做」（P3 backlog）。 |

### OCR 对照表

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| medium：建议补 `(skipped, cancelled/skipped)` 矩阵锁不对称 | 不立 finding。反向抽查已跑这两格：都不走短路文案。专测只锁 failure 格是 spec 范围；缺覆盖 ≤P3 backlog。 | ① `(skipped, cancelled)` 在方案 A 下不是设计路径（cancelled 应仍跑 quality）。② 若误走该格，既有 `quality_skipped`/`unexpected_primary_skip` 仍红，不假绿。 |
| medium：新 head 取消 primary 后旧 quality 因 `always()` 起跑，窗口变宽；建议 `cancel-in-progress: false` | 不立 finding。改锁形态已否决。窗口分析见降层①：变坏但不假绿。 | ① 新 head 时会发生。② 旧 run 的 `gate / gate` 对 `primary=cancelled` fail-closed；artifact 名含 `run_id`，不污染新 SHA。 |
| low：skipped 时 `ledger_input_upload` 为空 | **P2-1**（上）。aggregator 不读该 output，只读 `quality_result`。 | 见 P2-1。 |

## 降层三问（infra 失败路径）

### ① 终态写入前的不可逆动作 / 两把 cancel 锁交互

quality 现串行在 primary 之后（`gate-v2.yml:128-129`）。绿路径上 self-hosted `codex` 池（primary `:428`）排队时间叠加到 quality 开始时刻——这是已裁决写明的代价（中位 ~1.8 min），不是缺陷。

两把 job 级 cancel 锁仍独立：primary `:433-435` `gate-required-v2-primary-…`、quality `:147-149` `gate-required-v2-quality-…`，均为 `cancel-in-progress: true`。新 head 时序：

1. 新 primary 立即入组，取消旧 primary → 旧 `needs.primary.result = cancelled`。
2. 旧 quality 的 `if: always() && != 'failure'` 为真（cancelled ≠ failure），**起跑**。
3. 新 quality 仍 `needs: [primary]`，官方 concurrency 条文针对的是「**queued**」：「When a concurrent job or workflow is queued, if another job or workflow using the same concurrency group … is in progress, the queued job or workflow will be `pending`.」（Workflow syntax · `concurrency`）。needs 未满足时新 quality 尚未 queued，不能立刻取消旧 quality。
4. 新 primary 结束后新 quality 才 queued，此时才 cancel 旧 quality。

窗口相对改前：**变坏**。改前新 quality 与新 primary 并行、立刻入组，旧 quality 存活约等于取消延迟（秒）；改后窗口拉长到新 primary 墙钟（中位 1.8 min + codex 排队）。旧 quality 若在此窗口跑完：会写出带 **本 run_id** 的 ledger input，旧 gate（`:919-927` `always()`）会跑。evaluate 对 `primary=cancelled` 走 `review_unavailable`/`primary_cancelled`（`aggregate.py:713-716`），`ok` 为 False。新 SHA 的 artifact 前缀含自己的 `run_id`/`head.sha`，不会吃到旧 input。故存在「旧 quality 跑完并触发旧 gate」的窗口，比改前更大，但终态仍 fail-closed，不是假绿。不可逆动作仍是旧 run 的 runner 时间、面板写入（writer 锁 `cancel-in-progress: false` 本就排队）。不立 P1。

### ② 守卫值在真实部署形态下是否唯一

守卫是 `needs.primary.result`（`gate-v2.yml:129`）。官方：`needs.<job_id>.result` — 「Possible values are `success`, `failure`, `cancelled`, or `skipped`.」（Contexts reference · `needs` context）。没有第五值 `timed_out`。本仓 primary **不是 matrix**（无 `strategy`）。

| 形态 | `needs.primary.result` | `!= 'failure'` 时 quality |
|---|---|---|
| 单 job 成功/失败 | `success` / `failure` | 跑 / skip |
| 被 `if:` 跳过 | `skipped`。官方 if 例句：「Otherwise, the job will be marked as _skipped_.」（Workflow syntax · `jobs.<job_id>.if`）。primary 自己的 skip 在 `:423`（draft / fork / hosted）。 | 跑（已裁决要求） |
| `timeout-minutes`（primary `:430`） | 官方：`jobs.<job_id>.timeout-minutes` — 「The maximum number of minutes to let a job run before GitHub automatically **cancels** it.」四值里最贴近 `cancelled`；文档未另给 timeout→`failure` 的 `needs.result` 句。 | 若为 `cancelled`：跑（与 concurrency 取消同分支）；若实现上映射成 `failure`：skip。两条路 `gate / gate` 都不会绿（cancelled → `primary_cancelled`；failure → 短路且无 pass 审计）。 |
| 若 primary 是 matrix（当前不是） | 仍是上述四值之一。官方 fail-fast：「GitHub will cancel all in-progress and queued jobs in the matrix if any job in the matrix fails. This property defaults to `true`.」未单列「聚合=最坏结果」句，不能从文档断言 mix 时一定是 `failure` 而非 `cancelled`。 | 同四值表 |

守卫在部署上是「这一个 named job 的聚合 result 字符串」，不是唯一 id；对本 `if:` 足够。超时映射文档只写到 cancel，实测样本本轮没有。

### ③ 保护覆盖的是「写入」还是「行为」

覆盖的是 **行为**（quality 起跑条件 + aggregator 文案），不是新的事实源。`gate / gate` 仍是唯一 verdict check。

CLI 真跑（H0 `aggregate.py` `main()`，`--terminal-path`，primary audit `verdict=fail`）：

`(quality=skipped, primary=failure)` rc=1，terminal：

```json
{"schema_version": 1, "kind": "gate_terminal", "quality_result": "skipped", "primary_result": "failure", "gate_result": "fail", "classification": "code_fail", "reason_code": "primary_findings", "audit": {"available": true, "source_attempt": 1, "artifact_name": "primary-audit-v2-1"}}
```

`(quality=success, primary=failure)` rc=1，terminal：

```json
{"schema_version": 1, "kind": "gate_terminal", "quality_result": "success", "primary_result": "failure", "gate_result": "fail", "classification": "code_fail", "reason_code": "primary_findings", "audit": {"available": true, "source_attempt": 1, "artifact_name": "primary-audit-v2-1"}}
```

`classification` / `reason_code` / `gate_result` 逐字相同。`Outcome.ok` 由 `gate_result in ("pass", "skipped")` 导出（`aggregate.py:784`）→ 两格都是 **恒 False**。`quality_result` 字段不同是输入回显，不是分类。

ledger：quality skipped 时仍跑（`if: always()`）。resolver 对缺 input 文案把这次设计内跳过写成 `quality upload outcome: unknown`（见 P2-1）。**P2**。

## 反向抽查

直调 H0 `evaluate(**_base_kwargs(...))`（identity / audit 与测试助手相同）。命令：在 `/tmp/review-119` import `aggregate.py` 后对四格调用。输出：

| 格 | classification | reason | problems 首句 | 走短路文案？ |
|---|---|---|---|---|
| `(skipped, failure)` | `code_fail` | `primary_findings` | `quality job was skipped because primary already failed (short-circuit; the gate result is decided by primary)` | **是** |
| `(skipped, success)` | `ci_failure` | `quality_skipped` | `quality job result is 'skipped' (required: success)` | 否 |
| `(skipped, cancelled)` | `ci_failure` | `quality_skipped` | 同上 | 否 |
| `(skipped, skipped)` | `integration_error` | `unexpected_primary_skip` | 同上（随后才是 primary skip 句） | 否 |

只有第一格走短路文案。第四格因 `primary_classification == integration_error` 优先（`aggregate.py:776-777`），reason 不是 `quality_skipped`，但 problems 首句仍是旧的 skipped 句、不含 short-circuit。与 spec「只有 skipped+failure 改文案」一致。

## 熵增审查

对照 REFACTOR-guide 坏味道词表，diff 中每个新增项：

| 新增项 | 是否熵 +1 | 判断依据 |
|---|---|---|
| `quality_short_circuited` 局部变量（`aggregate.py:666`） | 否 | 布尔派生，两处消费（reason 映射 + problems 分支），不是第二套事实源 / 无消费者配置 / 转发层。 |
| 文件头 Job graph note（`gate-v2.yml:58-62`） | 否 | 把已裁决写进 workflow 头，第二消费者是读文件的人与后续 review；与 job 注释略有重复但不是镜像状态。 |
| quality job 注释（`:118-127`） | 否 | 锁 `always()` 为何必须，契约测试不读注释；必要说明不是抽象。 |
| design.md 待裁决→已裁决 | 否 | spec 要求的决策落盘，删候选表、留 A + 数据 + 否决 B/C，是减法。 |

未新增 fallback、未改锁、未给 gate 加第二写入者。L1 仍写 `quality ∥ primary`（并行符号）与 L58 串行说明打架，属注释滞后，P3 backlog。

## Backlog（存量 / 不阻塞）

- **P2-1**（可接受不修）：quality 设计内 skip 时 ledger 缺失文案为 `unknown`，且 `required=True` 让失败路径缺 ledger 行。修法若做：skip 时把 output 写成 `skipped` 或 resolver 见 `needs.quality.result==skipped` 把 input 标 optional——但「draft 下 input 改 optional」已否决，需另裁「failure 短路」这一格，且禁止为 P2 新机制。
- 判定矩阵未单列 `(skipped, cancelled)` / `(skipped, skipped)` 的短路否定（反向抽查已人工跑过；OCR medium-1）。
- `design.md` 非目标第 3 条仍写「gate#105 第一条本批不做」，与文末已裁决 A 矛盾。
- 文件头 L1 `quality ∥ primary ∥ ocr` 未改。
- 不审本 diff 之外的 `gate-v2.yml` 存量。

## 结论

H0 兑现方案 A：primary 失败时 quality skipped，`gate / gate` 不再等 quality；cancelled/skipped primary 仍跑 quality；aggregator 只在 `(skipped, failure)` 改文案，classification/reason 与 `(success, failure)` 逐字相同，`ok` 恒 False。两把 cancel 锁交互让旧 quality 的存活窗口变坏，但不造成新 SHA 假绿。ledger 对设计内 skip 误报 `unknown` 为 P2。OCR 扫过且干净到「无 P1」。红验三条在 base 上红。verdict：**pass**。
