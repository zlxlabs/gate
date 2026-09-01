# gate#101 ledger terminal 回落：r2 独立评审 verdict

## 总体 verdict

**pass**。固定审查范围为 `origin/main..79b1fc14303283ee662f4fa9a83af6b7c0d1d431`。本轮从对抗视角与下游消费者视角取证，未发现 personal 档 P1（数据丢失、静默出错、崩溃）。不阻塞。

风险口径：仓级 `risk-tier: personal`；改动核心是失败路径 + 资源账本寻址，按 infra/状态机类 diff 例外，收敛条件按 internal（连续 2 轮无新增 P1）。P1 红线仍按 personal。本轮 0 条新增 P1。

## 本轮新证据（相对 r1）

r1 已按 design.md 五条不变式对表，并做了 Jobs API / `started_at` 形态扫描。本轮不重复那条链。本轮新证据源：

1. 从 live workflow 抽出 resolver 字节，构造对抗输入序列实跑（过期工件、分页、中间 attempt、名字碰撞、input/audit/terminal 混 attempt）。
2. 真实 Artifacts API：issue #101 的目标 run `zlxlabs/agent-config` `33462777858`（attempt 2，`rerun --failed` 形态）以及另外 4 个 gate run 的工件清单、过期标记、数量。
3. 下载该 run 的真实 `gate-terminal` zip，核对工件名后缀与 envelope `run_attempt`。
4. 全仓 + gate-hub 下游消费者 grep，并对 sticky comment / `dedupe_entries` / gate-hub `parse_ledger_jsonl_line` 喂入带新字段的条目实测。

审查对象未纳入本轮之后的新提交。未改被审代码。OCR 本轮未重跑：同一 H0 已由 r1 扫过，再扫同一份 diff 按 review-discipline 不算新证据。

## 路径 A：对抗视角——怎样让它静默产出错账本

问的是「悄悄写出一条错的账本条目」，不是「代码对不对」。能静默写错才构成 P1 红线里的静默出错；fail-loud 不算。

### A1. 有没有一条路径能让 ledger 选中属于另一次判决的 terminal，而全程不报错？

**结论：在本仓真实使用方式下，不能。** 能构造出「选到不是当前 attempt 的 terminal 且不报错」，但那正是 spec 允许的回落（前序 attempt 的现行判决），不是错账本。要把「另一次判决」做成错误判决，必须再叠加工件消失/删除或分页+归因双故障；这些在真实 API 窗口里没出现，叠上去之后要么 fail-loud，要么依赖 operator 删工件（威胁模型排除内部攻击者）。

逐项：

| 构造 | 能否静默选错判决 | 证据 |
|---|---|---|
| 同 run 多次 `rerun --failed`，gate 只在 attempt 1 跑过 | 否。选 max ≤ current 的 terminal，即最后一次真实判决。 | 真实 run `33462777858` attempt 2 工件清单：`gate-terminal-v1-…-33462777858-1` 仍在且 `expired=false`；attempt 2 没有新 terminal。attempt 1 jobs：`gate / gate` conclusion=success，`gate / ledger` failure。resolver 探针 `A1f`（current=3，T1+T2 都在，copied gate）选中 T2 / `terminal_source_attempt=2`。 |
| gate 在中间 attempt 跑过、后续 attempt 没跑 | 否。选仍在清单里的最大 attempt。 | 探针 `A1f`。Jobs API 查的是**当前** attempt 是否跑过 gate，不要求中间 attempt 的 jobs；中间判决只要工件还在就会被 max 选中。 |
| 工件 `expired=true` 与 attempt 编号组合 | 单独过期当前、gate 本 attempt 真跑过 → **fail-loud**，不写账本。单独过期当前、gate 本 attempt 未跑 → 回落到仍活着的最大前序，这是「清单里还看得到的现行判决」，不是另一 run/PR 的判决。两者都过期 → 无候选，fail-loud。 | 探针 `A1`：expired T2 + 活 T1 + copied gate → `terminal_artifact_id=201` / `terminal_source_attempt=1`，rc=0。`A1b`：同样过期组合但 gate `started_at >= run_started_at` → rc=1，文案 `Aggregator ran on this attempt but did not produce a terminal artifact`。`A1c`：两个 terminal 都 expired → `No matching required gate terminal artifact found`。真实窗口：#101 run 与另外 4 个 gate run（`33469651855`/`33470600099`/`33397493825`/`33394949734`）全部 `expired_true=[]`。retention 都是 90 天、同一步骤上传，T1 比 T2 先到期，真实方向是「新的还在、旧的先没」，不是「新的没了、旧的还在」。 |
| artifact 列表分页边界 | 不能单独造成静默错账本。`--paginate --slurp` 后按页拼列表；当前 terminal 在第 2 页时仍被选中。若分页丢了当前 terminal **且** gate 本 attempt 真跑过，归因闸会硬失败。真实 run 工件数 4–7，远低于默认 30/页。 | 探针 `A1d`：两页 listing，T2 在第 2 页 → `terminal_artifact_id=202` / `terminal_source_attempt=2`。畸形页 → `current run artifact listing has invalid JSON shape`。真实 `33462777858`：`total_count=6`，1 页。 |
| 选到**另一 run / 另一 PR** 的 terminal | 不能。前缀钉死 `repository_id` + `head.sha` + `run_id`。 | `.github/workflows/gate-v2.yml:1120` `TERMINAL_PREFIX: gate-terminal-v1-${{ github.repository_id }}-${{ github.event.pull_request.head.sha }}-${{ github.run_id }}-`。API 也是 `runs/{run_id}/artifacts`，不会列出别的 run。 |

附加观察（不构成静默错账本，也不升格 finding）：

- input / audit / terminal **独立** `select_artifact`。探针 `mix input1 audit2 terminal1` 退出 0，输出 `input_source_attempt=1`、`audit_source_attempt=2`、`terminal_source_attempt=1`。这在 `gh run rerun --job` 只重跑 primary 时可以发生。spec 要点 2 写明「gate 本 attempt 没跑 → 上一 attempt 的判决仍是本 run 的现行判决」；audit 选择器在本 diff 之前就已经是 ≤ current。真实 #101 路径是 `rerun --failed` 只重跑失败的 ledger：该 run 的 input/audit/terminal 三个工件后缀全是 `-1`，没有混 attempt。
- 全量 UI Re-run 可能清掉 attempt 1 工件。对照 run `33469651855`（`run_attempt=2`）只剩 `*-2` 工件、没有 `*-1`。那种形态下当前 attempt 自己会再产 terminal，走「有当前 terminal」分支，不回落。README 里「点击 Re-run 会删旧 artifact」说的是这条，不是 #101 的 `--failed`。

### A2. `terminal_source_attempt` 会不会写错值、该写不写、不该写却写？写错时下游看得出来吗？

**结论：按当前 producer 契约，不能把错值写进账本条目还不报错。** GITHUB_OUTPUT 与账本字段是两条记录，语义不同；账本字段的对错下游**能**从条目本身看出来（复用时出现、同 attempt 时缺席）。GITHUB_OUTPUT 没有步骤消费者，写错也只出现在 step log。

两条写入点：

1. **GITHUB_OUTPUT**（`.github/workflows/gate-v2.yml:1287`）：`terminal_source_attempt={工件名后缀}`，**每次都写**，包括等于当前 attempt。
2. **ledger 条目**（`.github/actions/review-ledger/build_ledger.py:657-659`）：`source_attempt = terminal_envelope.get("run_attempt")`，仅当 `source_attempt != run_attempt` 时写入。这是不变式 5。

实测：

- 同 attempt：`build_entry` 探针 `same attempt` → 字段 `<ABSENT>`。
- 复用 attempt 1、当前 2：字段 `1`。
- envelope `run_attempt=3`、当前 2：`gate terminal identity mismatch: ['run_attempt']`，写不进去。
- 非法 `run_attempt`（`0`/`"1"`/`True`）：同样 identity mismatch。测试 `tests/test_review_ledger.py:1612-1618` 锁死。
- 缺 `run_attempt` 键：identity 校验 `_strict_int(None)` 失败，进不了写字段分支。

真实 producer：从 `33462777858` 下载 artifact `9784169279`（名 `gate-terminal-v1-…-33462777858-1`），envelope 为 `run_attempt=1`、`run_id=33462777858`、`head_sha=d68fc5969715062e49e7b8d40fff9eca492a5a41`、`repository=zlxlabs/agent-config`、`pr_number=1087`。**名字后缀与 envelope 一致。** 因此「GITHUB_OUTPUT 用名字、账本用 envelope」在生产工件上不会分叉。

若有人伪造「名字是 `-2`、envelope 是 `1`」：resolver 会当成当前 terminal、跳过归因闸（见 A4），但 `build_entry` 仍会写入 `terminal_source_attempt=1`。账本条目**看得出来**这是复用。这不是静默。伪造需要改 aggregator 的上传名或 envelope，不是本 diff 的输入面。

下游能不能看出来：

- 账本字段：复用时顶层有 `terminal_source_attempt`；同 attempt 没有。`tests/test_review_ledger.py:1588-1601` 锁死 key 集合。sticky comment 的人类可见块不渲染该字段，但机器游标 base64 会原样带回（本轮探针：`cursor keeps extra field 1`，`human-visible includes terminal_source_attempt? False`）。
- GITHUB_OUTPUT：后续步骤只用 `terminal_artifact_id`，不用 `terminal_source_attempt`（见路径 B1）。step log 里能看到，程序消费者没有。

### A3. `build_ledger` 放宽后的 `1 <= run_attempt <= current`，有没有输入能落在区间内却语义上属于别的 run 或别的 PR？其余身份字段能不能被绕过？

**结论：不能。** `run_attempt` 只放宽上下界；`repository` / `pr_number` / `run_id` / `head_sha` 仍是严格等值。落在区间内但身份是别人的 envelope，会在非 attempt 字段上爆掉。

代码：`.github/actions/review-ledger/build_ledger.py:542-551`。锁死：`tests/test_review_ledger.py:1621-1631`。

本轮探针：

- 错 `repository` / `pr_number` / `run_id` / `head_sha`：各自 `identity mismatch`，只报那一个字段。
- 把另一仓/另一 PR/另一 run 的 envelope（`run_attempt=1` 落在 `1..2` 内）喂给当前身份：`gate terminal identity mismatch: ['head_sha', 'pr_number', 'repository', 'run_id']`。attempt 放宽没有放行。
- `run_attempt=0`、字符串 `"1"`、布尔 `True`：全部 mismatch。`_strict_int` 排除 bool。

工件选择侧的前缀已经含 `run_id` 和 `head.sha`，所以「选到别人的工件再靠放宽 attempt 混进去」这条链在选择阶段就被掐断，身份校验是第二道。

### A4. 归因闸只在「当前 attempt 无 terminal」时才发起。有没有办法让「当前 attempt 有 terminal」为真，但取到的是错工件？

**结论：不能在不报错的情况下取到另一判决的工件。** 「有当前 terminal」的判定是工件**名后缀 == current**，随后按 artifact id 下载，envelope 再做身份校验。

| 构造 | 结果 | 证据 |
|---|---|---|
| 当前与前序都在 | 选当前，不读 Jobs API | 探针 `A4`：`terminal_artifact_id=202` / `terminal_source_attempt=2`。契约测试 `test_ledger_resolver_skips_jobs_listing_when_current_terminal_exists` 用 poison jobs 文件仍退出 0。 |
| 两个同名 `-2`、不同 id | 选更大的 id | 探针 `A4b`：id 202 与 999 → 选 999。GitHub Actions artifact v4 同 run 同名通常直接拒上传，这条更多是选择器稳定性，不是生产输入。 |
| 额外后缀 `gate-terminal-v1-2-evil` | 不匹配 `fullmatch([0-9]+)`，不当成当前 | 探针 `A4c` 仍选真正的 `-2`；`A4d` 只有 evil 后缀时回落到 `-1` 并走归因。 |
| 布尔 `id: true` 冒充当前 | 被 `isinstance(artifact_id, int) and not isinstance(..., bool)` 丢掉 | 探针 `bool id skipped`：回落到 T1 + 归因，不把 True 当 id。 |
| 未来 attempt `-3` | 永不入选 | 探针 `future T3 ignored`；不变式 3。 |

「判定为真但内容是错工件」还需要名字是当前 attempt、zip 里却是别人的 envelope。真实 zip（上节）名字 `-1` 对 envelope `run_attempt=1`，且 `run_id`/`head_sha`/`pr_number` 与该 run 一致。若 zip 里身份对不上，`build_ledger` 会 `identity mismatch`，ledger job 失败，不会静默落盘。

因此：归因闸被跳过本身不是漏洞——跳过的前提是清单里已经有当前 attempt 的合法命名工件；后续仍有 id 下载 + envelope 身份两道闸。

## 路径 A 小结

没有找到一条在真实使用方式下「全程不报错、写出属于另一次判决/另一个 run/PR 的账本」的路径。能不报错选出前序 terminal 的，都是 spec 不变式 1 允许的回落，并且会被账本可选字段标出来（不变式 5）。
