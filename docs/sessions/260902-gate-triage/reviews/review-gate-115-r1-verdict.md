verdict: pass

# gate#115 r1 独立全量评审 verdict

第 1 轮。方向 = 正向全量（契约是否兑现：同名重试 + overwrite、quality job output 接到 ledger 缺失文案、仅 ocr 有 job 级 continue-on-error）+ 降层三问 + 反向抽查（有 input 时文案不出现；`success` 但 artifact 缺失的矛盾形态）+ 熵增。风险档 personal，失败路径按 internal 收敛条件审视。无 P1。P2/P3 接受不修，记 backlog。

## 本轮新证据

本轮是该 diff 的第一轮独立审查，证据不是「再读一遍同一份 diff」：

- OCR：`ocr-review --from 5123e3120ca6e9c4d84244528b74fe9346bd730c --to 7553379add08795be2be0be9633a4c7790bacc59`，`status=reviewed`，`profile=minimax` / MiniMax-M3，`coverage=complete`，2 条 finding（high + medium），复核器超时故 `verification.verdict=unverified`。不是 skipped 空数组。
- H0 临时 worktree `/tmp/review-115` @ `7553379`：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py` → **85 passed in 11.83s**。`SHELLCHECK_OPTS=--severity=warning actionlint .github/workflows/gate-v2.yml` → 0。`python3 scripts/check_pinned_uses.py` → OK。
- 反向抽查：用 `_run_ledger_resolver` 同款入口实测四格（见下，贴命令与输出）。
- `select_artifact` 回落探针：current=2、仅有 attempt-1 input、env=failure → resolver **成功** 且 `input_source_attempt=1`，缺失文案不出现。
- GitHub 官方文档（2026-09-02 拉取）`jobs.<job_id>.continue-on-error`：*Prevents a workflow run from failing when a job fails.* 与 Ken Muse 2024/2025 博文「overall workflow will report a failure / job has a red X」对 check 显示形态不一致；PR checks 列表里 `gate / ocr` 的 check run conclusion **待首个自然样本**。
- 冻结 pin `actions/upload-artifact@ea165f8d…` 的 `action.yml`：`overwrite: true` 先删同名再传，不存在也不失败。

审查对象冻结 `5123e3120ca6e9c4d84244528b74fe9346bd730c..7553379add08795be2be0be9633a4c7790bacc59`。spec = PR #115 正文 + issue #107 + issue #105 第二条 + `docs/sessions/260902-gate-triage/design.md` 不变式 3/4/5 与已否决方案。已否决（quality 因上传失败变红、draft 下 input optional、重试换 artifact 名后缀、bump upload-artifact pin）不作为 finding 重提。

## Findings

无 P1。P2 一条（接受不修）、P3 两条。

### P2-1 重试 `overwrite: true` 在「首步已落盘但 outcome=failure」时先删；重试再失败会丢掉本 attempt 的 input

- **位置**：`.github/workflows/gate-v2.yml:348-361`（重试步 `overwrite: true`）；选择逻辑 `.github/workflows/gate-v2.yml:1191-1212`。
- **违反**：不变式 3 的效果侧——重试应让解析器仍按 `<prefix>-<attempt>` 选到**本 attempt** 的 input；删完再传失败时，本 attempt 候选消失，解析器按 `(attempt, id)` 取 max，会回落到上一 attempt（见降层三问 ① 实测）。
- **复现 / 推理**：pin 的 `action.yml` 写明 overwrite 先 `delete` 再 upload。若首步 Finalize 在服务端已提交、客户端仍报 `failure`，重试会删掉这份已可被 listing 看到的 artifact。重试再失败 → 当前 attempt 无名；`select_artifact` 在 `attempt <= current` 的候选里取 max。
- **工具标注 / 本仓判定 / 两问**：OCR 未报此条。本仓判定 **P2，本轮接受不修**。①真实使用会被触发吗？#107 现场（Finalize ECONNRESET）artifact 清单里**没有** `review-ledger-input-v2-*`，该失败模式不触发删除。超时/响应丢失等「服务端已提交、客户端当失败」没有本仓样本。②触发后果能否接受？ledger 从「本可建账」变成缺失或旧 attempt 输入；ledger 不是门禁、不是用户数据，可接受。不升 P1。
- **建议修法**：重试前先按同名查 listing，已存在则跳过删除/重传，只在确认不存在时再传。

### P3-1 `QUALITY_LEDGER_INPUT_UPLOAD=success` 但 artifact 缺失时，文案只复读 `success`，没有更强矛盾提示

- **位置**：`.github/workflows/gate-v2.yml:1182,1214-1217`。
- **违反**：issue #107 期望「缺失文案能指回真实原因」；此形态下字面原因是「上传成功」，与「找不到 artifact」并列，不解释矛盾。
- **复现**：见反向抽查 R2。`returncode=1`，文案 `No matching required ledger input artifact found (quality upload outcome: success)`。
- **工具标注 / 本仓判定 / 两问**：OCR 未报。本仓 **P3**。①会触发吗？listing 滞后或 overwrite 换了新 id 后旧 listing 窗口，罕见。②后果：ledger 仍 fail-loud，文案里已有 `success` 字面量，人和 grep 能看出矛盾。不阻塞。
- **建议修法**：当 `quality_upload == "success"` 且 required input 缺失，把 missing_message 改成明确的矛盾句（upload reported success but artifact missing）。

### P3-2 outcome 步 `::error::` 固定写 `(network)`，文件缺失路径会被说成网络

- **位置**：`.github/workflows/gate-v2.yml:375`。
- **违反**：不是不变式 4（那条只锁缺失文案含上传结论字面量）；是 #107「指回真实原因」在 quality 注释上的精度。
- **复现 / 推理**：`if-no-files-found: error` 与 Finalize ECONNRESET 都会让首步 `outcome=failure`，重试同样失败后走 else，注解永远说 network。
- **工具标注 / 本仓判定 / 两问**：OCR medium，本仓 **P3**。①会触发：entry-mode 或 preflight 没写出文件时可能。②后果：quality 仍绿（符合「不让 quality 因上传失败变红」），ledger 缺失文案带 `failure`/`unknown`，不静默。不阻塞。
- **建议修法**：注解改成带上两个 step outcome 字面量，不要写死 network。

OCR high（「缺文件时也会重试」）不立 finding：重试同一失败是浪费不是静默错；OCR 建议的 `outcome == failure && conclusion == failure` 在 step 级 `continue-on-error: true` 下 **恒假**（官方 steps 上下文：COE 失败时 outcome=failure、conclusion=success），会把 #107 要的网络重试整条掐死。建议改 `if-no-files-found: warn` 会让缺文件时首步变 success、job output 报 success 但无 artifact，与 P3-1 矛盾形态对冲，更差。

## 正向：契约核对

| 查过什么 | spec / 不变式 | 为何没问题 |
|---|---|---|
| 重试同名 + overwrite | 不变式 3；已否决换后缀 | 两步 `with.name`/`with.path`/`uses` 相同，重试多 `overwrite: true`。`test_ledger_job_builds_and_uploads_v2_review_ledger_without_gating` 锁死。 |
| 双失败文案含上传结论 | 不变式 4 | quality `outputs.ledger_input_upload` → ledger env → `select_artifact` missing_message。参数化测试锁 `failure` 与缺 env→`unknown`。 |
| 仅 ocr 有 job 级 COE | 不变式 5；#105 二 | `test_only_ocr_job_has_continue_on_error` 集合恒等于 `{"ocr"}`。文件头 54-56 行写明不要读 `run.conclusion`。workflow 内无 `needs.ocr` 消费者。 |
| quality 不因上传失败变红 | PR 不做什么 / 已否决 | 上传与重试均 step 级 COE；outcome 步本身成功。 |
| 不 bump pin | 已否决 | 仍 `ea165f8d…`；pin 的 action.yml 已声明 overwrite。 |

## 反向抽查

命令（H0 worktree，`PYTHONPATH=tests`，import `tests/test_gate_v2_contract.py` 的 `_run_ledger_resolver`）：

**R1. 有 input artifact + env=failure → 文案不应出现**

```
artifacts = review-ledger-input-v2-1 + gate-terminal-v1-1, current=1,
extra_env QUALITY_LEDGER_INPUT_UPLOAD=failure
```

输出：

```
returncode=0
github_output:
input_artifact_id=101
input_source_attempt=1
terminal_artifact_id=201
terminal_source_attempt=1
contains "quality upload outcome"? False
```

只在缺失时才带上传结论。符合不变式 4 的附着点。

**R2. `QUALITY_LEDGER_INPUT_UPLOAD=success` 但 artifact 缺失**

```
artifacts = gate-terminal-v1-1 only, current=1,
extra_env QUALITY_LEDGER_INPUT_UPLOAD=success
```

输出：

```
returncode=1
No matching required ledger input artifact found (quality upload outcome: success)
```

矛盾形态 fail-loud，但提示偏弱 → P3-1。

**R3 / 降层 ①. current=2、仅 attempt-1 input、env=failure**

```
returncode=0
input_artifact_id=101
input_source_attempt=1
terminal_artifact_id=202
terminal_source_attempt=2
contains "quality upload outcome"? False
```

双失败若本 attempt 无名，会静默用上一 attempt 的 input 建账。选择逻辑是存量（注释 1203-1204：「input/audit/terminal may reuse an earlier attempt」），本 PR 只改 missing_message。记 backlog，不占本轮 P1。

**R4. 空 env → unknown**

```
returncode=1
No matching required ledger input artifact found (quality upload outcome: unknown)
```

与参数化测试 `{}` 格一致。

## 降层三问（infra 失败路径）

### ① 终态写入前的不可逆动作

重试步 `overwrite: true`（:361）会先删同名 artifact 再传。pin 的 `action.yml`：*If true, an artifact with a matching name will be deleted before a new one is uploaded. … Does not fail if the artifact does not exist.*

- **#107 样本（Finalize ECONNRESET）**：清单里没有该 artifact，删除是空操作，安全。
- **首步失败但服务端已有半成品且已进 listing**：删除 + 重传。重试成功则换新 id（MIGRATION.md：overwrite 得到全新 Artifact、id 不同）；重试再失败 → 服务端可能「已删未传」。ledger 读 listing：本 attempt 无名时 `select_artifact` 按 `(attempt, id)` 取 max（:1212），**会回落到上一 attempt 的 input**（R3 实测 `input_source_attempt=1`），用旧 preflight/install-result 给当前 attempt 的 terminal 建账。这是存量选择策略，不是本 diff 新引入的 max 键。
- 不可逆动作还有：outcome 步的 `::error::` 注解（不失败 job）、ocr 失败在 COE 下仍把该 job 标红（见 ③）。

### ② 守卫值在真实部署形态下是否唯一

守卫是 `steps.*.outcome`（:350, :367-368, :371）。官方 steps 上下文：可能值 `success` / `failure` / `cancelled` / `skipped`；COE 步失败时 outcome=failure、conclusion=success。本实现读 outcome 不读 conclusion，对。

| 路径 | 首步 | 重试 | outcome 步（`if: always()` :365） | job output |
|---|---|---|---|---|
| 首步成功 | success | skipped（if 为假） | 跑；`success \|\| skipped` → success | success |
| 首步失败 + 重试成功 | failure | success | 跑；success | success |
| 双失败 | failure | failure | 跑；else → failure + `::error::` | failure |
| 首步 `cancelled`（quality 被 cancel-in-progress 取消） | cancelled | 不跑（`cancelled != failure`）→ skipped | `always()` **会跑**（官方：*Causes the step to always execute, … even when canceled*）。else 分支 → failure | failure（若 runner 还在） |
| 关键失败导致连 always 都跑不了（取源失败 / runner 已没） | 未写入 | 未写入 | 可能不跑 | 空 → ledger 侧 `unknown` |

`steps.ledger-input-upload.outcome` 在单 job 内唯一。artifact 名含 `run_id`+`run_attempt`，跨 attempt 不碰撞。quality 的 concurrency group 按 PR 号，取消的是旧 head 的 quality，不是同 attempt 双写。

### ③ 保护覆盖的是「写入」还是「行为」

覆盖的是**行为 / 可观测性**，不是门禁写入：quality 仍绿；ledger 仍 `required=True`；`gate / gate` 仍是唯一 verdict check。

`needs.quality.outputs.ledger_input_upload`（:1160）：

- 官方：job outputs 在 **job 结束时**于 runner 上求值，经 `needs` 传给下游。ledger `if: always()`，quality 失败/取消后仍跑。
- quality **skipped**（本 workflow 里 quality 无 skip `if`，实际几乎不发生）：outputs 空。官方 needs 示例里失败 job 也可呈 `outputs: {}`。
- quality **failure**（测试红等）：上传与 outcome 都是 `always()`，outcome 步应写完 output；`needs.quality.outputs.ledger_input_upload` 应为 `success|failure`。
- quality **cancelled**：always 步在官方语义下仍跑，但 runner 被立刻回收时 output 求值可能落空 → env 空 → python `:1182` 归 `unknown`。此时真实情况是「quality 没跑完上传」，不是「上传状态不明」。ledger 仍因缺 artifact 红（attempt 1）或回落旧 input（attempt≥2）。误读面仅限文案，fail-loud 仍在。P3 级用词精度，不单独立条（与 P3-1 同类）。

**ocr job 级 COE 的 check run conclusion（PR checks 列表）**：

- 官方 workflow 语法（`jobs.<job_id>.continue-on-error`，2026-09-02）：*Prevents a workflow run from failing when a job fails. Set to `true` to allow a workflow run to pass when this job fails.* 管的是 **run 级 conclusion**，正是 #105 二 / 设计目标 ③。
- 同一段**没有**规定该 job 在 PR checks 列表里的 check run `conclusion`。Ken Muse（[How to Handle Step and Job Errors in GitHub Actions](https://www.kenmuse.com/blog/how-to-handle-step-and-job-errors-in-github-actions/)，文内 2025-07 更正）写 job 仍是红 X、且声称 overall workflow 仍 failure——与官方 run 级表述冲突，不能当定论。
- **待首个自然样本**（与 PR #115「已知边界」一致）：`gate / ocr` 这条 check 在 PR checks 里是 success、failure 还是中性。`statusCheckRollup` 若仍把该 check 当 failure，按 run 终态判读的下游已修好，按 checks 聚合的下游未必。文件头 54-56 已写「不要读 run.conclusion」。

## 熵增审查

对照坏味道词表（单实现接口、转发-only 层、与现有状态镜像、无第二消费者的通用化）：

| 新增项 | 是否熵 +1 | 判断 |
|---|---|---|
| 重试 step | 否 | #107 的最小机制；与首步同 pin 同名，不是第二套上传抽象。 |
| outcome step | 否 | 跨 job 传递必须有一步写成 `GITHUB_OUTPUT`；被 quality `outputs` 与 ledger env 两处消费。 |
| quality job output `ledger_input_upload` | 否 | 第二消费者是 ledger；不是无主配置。 |
| env `QUALITY_LEDGER_INPUT_UPLOAD` | 否 | 单点跨 job 电线，解析器一处读取。 |
| 解析器 `quality_upload` 一段 python | 否 | 只改 missing_message，不新增选择策略。 |
| ocr job 级 `continue-on-error` | 否 | #105 二指定的机制；契约测试把集合锁死为 `{ocr}`，防扩散。 |
| `_run_ledger_resolver(..., extra_env=)` | 否 | 测试夹具参数；新参数化测试是第二消费者。弹出该键再注入，避免宿主环境脏值。 |

没有单实现接口、没有转发-only 包装、没有与 step outcome 镜像的第二份状态机。

## OCR 对照表

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| high：缺文件时也会重试；建议 `conclusion == failure` 或 `if-no-files-found: warn` | 不立 finding（建议有害） | ①缺文件路径存在但不是 #107。②按 OCR 建议改 conclusion 判断会让 COE 下重试永不触发，直接打掉不变式 3 的网络重试。 |
| medium：`::error::` 写死 network | P3-2 | 见上。①会。②注解误导，ledger 仍红。 |

## backlog

- `gate-v2.yml` 本 diff 之外的存量不审。已知：`select_artifact` 对 input 也复用上一 attempt（:1203），rerun 上双失败走不到不变式 4 的缺失文案（R3）；#105 第一条（汇总被 quality 绑架）本批不做。
- P2-1 / P3-1 / P3-2 接受不修。
- ocr check run 在 PR checks 列表的显示形态、以及 Finalize 已提交但客户端报失败是否真实发生：等自然样本，不把「合并了」写成「验过了」。
- 契约测试未锁首步 `id: ledger-input-upload`（只锁了重试 if 字面量）、未锁 `timeout-minutes: 5`、未覆盖 success+缺失格。
