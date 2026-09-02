verdict: pass

# gate PR #119 第 2 轮独立审查 verdict（H0..H1 增量四问 + 全量换视角）

审查对象冻结 H1 = `f9301a32a453d36f513b824c7bf64629d75df5a8`（相对 H0 = `4af1459e18f1340020005e4b7f6fa2ccfca1c329`）。
spec = PR #119 正文（含「修复轮 1」）+ issue #105 第一条 + 本分支 `docs/sessions/260902-gate-triage/design.md`「已裁决」节。
风险档 personal，失败路径按 internal 收敛（连续 2 轮无新增 P1）。
本轮新证据：H1 上 `_run_ledger_resolver` 真跑、`build_ledger.py` 缺文件真跑、cancelled 组合真跑、GitHub 官方文档原文、Download `if` / `RESULT_DOMAIN` 两处变异。不重审 r1 已结论项；`origin/card/gate-20260902-14` 远端已不存在，r1 verdict 未读、未抄。OCR 按卡不重跑。

## 本轮新证据

1. 临时 worktree `/tmp/review-119-r2` @ H1。增量 `git diff 4af1459e18f1340020005e4b7f6fa2ccfca1c329..f9301a32a453d36f513b824c7bf64629d75df5a8`：3 文件 +78/−6。
2. `_run_ledger_resolver` 同款：`(QUALITY_RESULT=skipped, PRIMARY_RESULT=failure)` + 无 input + 有 terminal + 有 audit → GITHUB_OUTPUT 全量如下；cancelled 四格同法真跑。
3. `build_ledger.py`：裸 CLI 先撞 GitHub API；把 `fetch_prior_entries`/`post_state_comment` 打成空操作后走与 `main()` 相同的 `_load_json` → `build_entry` 序列。
4. 变异：删 Download 步 `if:`；删解析器 `RESULT_DOMAIN` 校验。注入后 `rg` 确认，测完 `git checkout --` 还原。
5. 指定测试：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_aggregator.py tests/test_review_ledger.py` → **516 passed in 16.38s**。

---

## A. H0..H1 增量四问

### ① 是否只修登记在案的 findings（P2-1、P3 header、P3 design.md 非目标）

**是。** 增量只动三文件，逐项对得上修复轮 1 登记项：

| 登记项 | 落点 |
|---|---|
| P2-1 解析器把设计内 skip 当 unknown 并 SystemExit | `gate-v2.yml:1180-1181` 两键 env；`:1205-1213` 四值域 + `quality_short_circuited`；`:1249` `required=not quality_short_circuited`；`:1251-1252` notice；`:1359-1361` 空安全输出 + `input_short_circuited`；`:1373` Download `if`；契约测试 `tests/test_gate_v2_contract.py:1095-1141`（3 条）+ `_run_ledger_resolver` 默认注入 `success/success`（`:878-881`） |
| P3 header | `gate-v2.yml:1-2` 由 `quality ∥ primary` 改为 `primary → quality (skipped on primary failure, gate#105 A)` |
| P3 design.md 非目标 | `design.md:13` 由「本批不做 / 待裁决」改为「按已裁决 A 落地于 PR #119」 |

未改 aggregator、concurrency、gate 汇总 `needs`/`if`、draft 下 input optional。

### ② 是否新增未经批准的抽象

**否。** `RESULT_DOMAIN` 是解析器内局部集合，与 aggregator 既有 `QUALITY_RESULT_DOMAIN`/`PRIMARY_RESULT_DOMAIN`（`aggregate.py:141-142`）同四值。`quality_short_circuited` 是局部布尔。`input_short_circuited` 是修复轮 1 正文明文要求的 step output（「写空 `input_artifact_id` + 新 output `input_short_circuited=true` + `::notice::`」），不是执行器自造接口。

### ③ 状态 / 事实源 / fallback 是否无依据增加

**否。** 新事实源 `QUALITY_RESULT`/`PRIMARY_RESULT` 来自 `needs.*.result`（GitHub 文档四值域，见 B.3）。短路 fallback「仅 `(skipped, failure)` 把 input 降为可选」有 P2-1 + 修复轮 1 依据。

`quality_short_circuited` 在 aggregator 与 ledger 解析器各一份，**判据字面相同**：

- `aggregate.py:666`：`quality_short_circuited = quality_result == "skipped" and primary_result == "failure"`
- `gate-v2.yml:1213`：`quality_short_circuited = quality_result == "skipped" and primary_result == "failure"`

`input_short_circuited` 全文消费者：仅 `tests/test_gate_v2_contract.py:1116` 断言。生产 Download 步读的是 `input_artifact_id != ''`（`:1373`），ledger job 无 job-level outputs。该键是修复轮 1 点名的观测输出，不是无依据的第二事实源。

### ④ 是否留下双路径

**否。** 生产决策只有一处：解析器按 `(skipped, failure)` 决定 input 是否 required。Download 的 `if` 是「空 id 不调 download-artifact」的守卫，不是第二套短路判定。aggregator 与解析器的短路谓词字面相同，目前不会因漂移走出两条语义。Build 步未改——这不是双路径，是修复轮 1 声称「缺文件归 null」的下游，B.1 单独取证。

增量四问通过，不按新增 P1 计入。

---

## B. 全量换视角

### 1. ledger 短路路径真跑

命令（H1 `/tmp/review-119-r2`，与测试同款 `_run_ledger_resolver`）：

```
extra_env={"QUALITY_RESULT": "skipped", "PRIMARY_RESULT": "failure"}
artifacts=[{gate-terminal-v1-1, id=201}, {primary-audit-v2-1, id=301}]  # 无 input
current=1, review_expected=true
```

解析器 stdout / GITHUB_OUTPUT 原文：

```
returncode=0
--- stdout ---
::notice::ledger input skipped: quality was short-circuited by primary failure (gate#105 A); preflight/install fields will be null
--- stderr ---
--- GITHUB_OUTPUT ---
input_artifact_id=
input_source_attempt=
input_short_circuited=true
audit_artifact_id=301
audit_source_attempt=1
terminal_artifact_id=201
terminal_source_attempt=1
```

解析器这一格按修复轮 1 契约通过。

`build_ledger.py` 直接跑（`--preflight-path`/`--install-path` 指向不存在文件，审计用测试夹具 `_v2_audit("fail")`，terminal 用 `kind=gate_terminal` 夹具）：

裸 CLI 在写 ledger 之前先打 GitHub API，dummy token 得到：

```
urllib.error.HTTPError: HTTP Error 401: Unauthorized
CLI_EXIT:1
no ledger file
```

（`main():910` `fetch_prior_entries` 在 `_load_json` 之后、`build_entry` 之前，401 盖住了缺文件路径。）

把网络调用打成空操作后走与 `main()` 相同的加载序列（`preflight = _load_json(missing) or {}`，`install = _load_json(missing)`，审计为 `kind=primary_review` 的 fail 夹具）：

```
load missing preflight: None
load missing install: None
main() would pass preflight={} install=None
BUILD_ENTRY_CRASH
ValueError: canonical primary preflight has invalid coverage shape
  File ".../build_ledger.py", line 919, in main
    entry = build_entry(...)
  File ".../build_ledger.py", line 612, in build_entry
    review = _review_summary(audit, fallback_status, preflight)
  File ".../build_ledger.py", line 247, in _review_summary
    raise ValueError("canonical primary preflight has invalid coverage shape")
```

`_load_json:816-818` 对缺文件确返回 `None`；`main():893` 把 None 收成 `{}`；v2 fail 审计要求 preflight 带 `diff_lines`/`classification`/`review_plan`/`thresholds.single_turn_lines`，空对象在 `:247` 崩。ledger 行**没有写出**，不存在「preflight/install 为 null」的终态。notice 文案「fields will be null」与 Build 实跑不一致。见 Findings P2-1。

### 2. cancelled 组合

方案 A 下 `quality.if` = `always() && needs.primary.result != 'failure'`（`gate-v2.yml:130`，契约锁在 `tests/test_gate_v2_contract.py:484`）。primary `cancelled` 不是 `failure`，quality **应当仍跑**，`(QUALITY_RESULT=skipped, PRIMARY_RESULT=cancelled)` 不是设计内短路格。quality 自己被 concurrency 取消则 `QUALITY_RESULT=cancelled`。

解析器真跑（均无 input、有 terminal+audit）：

| 组合 | 解析器路径 | 文案 |
|---|---|---|
| `(skipped, failure)` 设计内 | 短路，exit 0 | `::notice::... short-circuited by primary failure`；`input_short_circuited=true` |
| `(skipped, cancelled)` | **非**短路，exit 1 | `No matching required ledger input artifact found (quality upload outcome: unknown)` |
| `(cancelled, failure)` | 非短路，exit 1 | 同上 `unknown` |
| `(cancelled, success)` | 非短路，exit 1 | 同上 |
| `(cancelled, cancelled)` | 非短路，exit 1 | 同上 |

aggregator `evaluate()` 同格：

| 组合 | classification / reason | problems 原文要点 |
|---|---|---|
| `(skipped, failure)` | `code_fail` / `primary_findings` | `quality job was skipped because primary already failed (short-circuit; the gate result is decided by primary)` |
| `(skipped, cancelled)` | `ci_failure` / `quality_skipped` | `quality job result is 'skipped' (required: success)` + primary cancelled |
| `(cancelled, *)` | `ci_failure` / `quality_cancelled` | `quality job result is 'cancelled' (required: success)` |

没有一格把「设计内短路」notice 写到 cancelled 路径上。`(skipped, cancelled)` 走的是事故模板 `quality_skipped`，与设计内短路面不同。无混写。

### 3. GitHub 语义取证

**`always()` 与 `needs` 组合的官方例句**

URL：<https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow>（与 workflow-syntax `jobs.<job_id>.needs` 同文）

原文句：「If you would like a job to run even if a job it is dependent on did not succeed, use the `always()` conditional expression in `jobs.<job_id>.if`.」

官方例句：

```yaml
jobs:
  job1:
  job2:
    needs: job1
  job3:
    if: ${{ always() }}
    needs: [job1, job2]
```

原文句：「In this example, `job3` uses the `always()` conditional expression so that it always runs after `job1` and `job2` have completed, regardless of whether they were successful.」

文档**没有** `always() && needs.<job>.result != 'failure'` 这一整句例句。`jobs.<job_id>.if` 的可用上下文含 `needs`，可用函数含 `always`（<https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability>）。本 PR 的 quality `if` 是这两项的组合，不是单句官方样例。

**skipped job 的 `outputs` 在 needs 里是否为空对象**

URL：<https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#needs-context>

- `needs.<job_id>.result` 原文：「Possible values are `success`, `failure`, `cancelled`, or `skipped`.」
- 例里 **failure** 作业给出 `"outputs": {}`（`deploy.result = failure`），**不是** skipped 作业的例句。
- 同页：「If you attempt to dereference a nonexistent property, it will evaluate to an empty string.」

文档无「skipped job 的 outputs 在 needs 里是空对象」这句。未定义，待样本。本修复读 `needs.quality.result`（四值之一应为 `skipped`）和空的 `needs.quality.outputs.ledger_input_upload`（解引用空串），与「不存在属性 → 空串」相容，但不构成 skipped→空对象的官方证明。

**`timeout-minutes` 触发后 `needs.<job>.result`**

URL：<https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idtimeout-minutes>

原文句：「The maximum number of minutes to let a job run before GitHub automatically cancels it. Default: 360」

`needs.<job_id>.result` 的官方四值无 `timed_out`。REST 的 job `conclusion` 另有 `timed_out`。文档未把 `timeout-minutes` 映射到 `needs.*.result` 的哪一个。**未定义，待样本。**

### 4. 契约变异

**删 Download 步 `if:`**

注入确认：`rg` 原 `1373: if: steps.resolve-ledger-artifacts.outputs.input_artifact_id != ''`；替换后该步直接 `uses: actions/download-artifact`。

红的测试：`tests/test_gate_v2_contract.py::test_ledger_resolver_step_env_and_download_guard_literals`

```
E       KeyError: 'if'
tests/test_gate_v2_contract.py:1141: KeyError
1 failed, 4 passed
```

同文件另外 3 条解析器行为测试仍绿（它们不读 YAML `if`）。测完已还原，`git status` 干净。

**删解析器 `RESULT_DOMAIN` 校验**

注入确认：`1205: # RED-VERIFY: RESULT_DOMAIN validation deleted`，`rg RESULT_DOMAIN` 无代码命中。

`pytest -q tests/test_gate_v2_contract.py -k ledger_resolver` → **20 passed, 69 deselected**。不红。

归因：**测试弱。** 没有任何测试喂非法 `QUALITY_RESULT`/`PRIMARY_RESULT` 或断言 `QUALITY_RESULT must be one of ...`。见 Findings P3-1。已还原。

---

## Findings

### P2-1 Build 步在短路路径上仍崩，ledger 行写不出

违反：修复轮 1「Build 步不改（`build_ledger._load_json` 对缺失文件返回 None，preflight/install 归 null）」；design 不变式 4 的精神（primary 失败的 run 仍应留下 ledger 行，而不是在下一跳再死）。不反着「不改 Build」这条禁改——问题是禁改所依赖的「缺文件归 null」在 v2 fail 审计下不成立。

工具标注 / 本仓判定 / 两问：

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| 本轮自测（H1 真跑 `build_entry`/`main`） | **P2** | ①会被触发吗？**会。** 方案 A 下 primary failure → quality skipped → 无 input artifact → Download `if` 跳过 → Build 仍跑，路径指向不存在的 preflight/install，审计是 canonical `primary_review` fail。已在 H1 用测试夹具的 v2 fail 审计 + 缺文件跑通 `ValueError: canonical primary preflight has invalid coverage shape`。issue #105 数据：primary 有结论的 19 个里失败 5 个（26%），正是这条路径。②后果能否接受？**能接受为 P2、不升 P1。** `gate / gate` 已由 primary 判红，合并本身已被挡住；ledger 是独立 persistence 检查，崩的是账本行而不是门禁结论。不是静默错（job 红、栈在日志里）。personal 红线的「崩溃/丢数据」指门禁结论或用户数据，不是已红 PR 上账本 job 再红一次。与 r1 P2-1 同一用户可见后果，只是死在下一跳；本轮新证据证明修复轮 1 的「归 null」断言不成立，故另记，不重抄 r1 结论。 |

### P3-1 `RESULT_DOMAIN` 校验没有红测

违反：修复轮 1「四值域校验，非法 fail-loud」——防线写了，测试锁不住。变异删校验后 20 条 resolver 测试全绿。

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| 本轮变异 | **P3** | ①会被触发吗？生产里 `needs.*.result` 官方四值，非法值不是 GitHub 会发出的常态。②后果？缺测不改变现网行为；非法值即便漏过，`== "skipped" and == "failure"` 仍不会误开短路。不中 P1。 |

无 P1。不重提已否决方案 B/C、改汇总 needs/if、改 concurrency、draft 下 input optional。

---

## 收敛判定

本轮**无新增 P1**。增量四问通过。r1（Cursor）pass、本轮（Grok）换证据源后仍 0 P1，满足失败路径按 internal「连续 2 轮无新增 P1」。P2-1 / P3-1 记 backlog，不阻塞。
