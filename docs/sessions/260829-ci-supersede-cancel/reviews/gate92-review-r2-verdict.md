# gate PR #92 R2 verdict

- 审查对象：H0 = `4c52f82e122e06b1eec4d5dfd6e2bc4e3d3cc58c`；H1 = `2bab0fecd92f49d1bf447ed4251983d1c9640499`；base = `d67aa0c03f201bf074557007c0d7d9580a64916c`。
- 本轮新证据（R1 结论生成之后才有）：H0..H1 修复增量（文档限制声明 + 契约精确相等）；OCR `status=reviewed`（minimax / MiniMax-M3，3 条 finding，复核 1 confirmed / 1 refuted / 1 unverified）；本 worktree 契约抽跑 `141 passed`；PyYAML 解析比对 base/H0/H1 的 `gate`/`ledger` mapping；PR #92 正文与 H1 设计文档逐句对照。
- 本轮视角：误拒向 + 文档-实现一致性向。不重开两把锁形态、F1 只做文档披露、OCR 第一版不加锁、转 draft 不取消旧 primary。
- 风险等级：personal；infra 类按 internal 档收敛。P1 红线：数据丢失、静默出错、崩溃。

## H0..H1 增量审四问

H0..H1 只动三份文件：`docs/design/gate-convergence-criterion.md`、`docs/sessions/260829-ci-supersede-cancel/progress/gate-cancel-two-locks-progress.md`、`tests/test_gate_v2_contract.py`。`gate-v2.yml` 字节与 H0 相同。

1. **是否只修登记在案的 F1/F2/F3？** 是。F1 → 设计文档新增「已知限制」节；F2 → `_assert_expensive_job_cancel_lock` 改为完整 group 表达式相等（锁死 `pull_request.number || github.run_id` 顺序）；F3 → `gate`/`ledger` 改为完整 mapping 与 `_WRITER_CONCURRENCY` 相等。进度文件只记收口，不是产品改动。
2. **是否新增未经批准的抽象？** 否。`_WRITER_CONCURRENCY` 替换被删的 `_FORBIDDEN_EXPENSIVE_GROUP_PREFIXES`，仍是测试夹具常量，无运行时状态/包装层/配置项。符合 F3「精确常量」处方，不走结构性例外六项。
3. **状态/事实源/fallback 是否无依据增加？** 否。未改 workflow，无新 fallback/重试。文档只披露已有行为（见下「已知限制」与 `ledger` 解析步骤互证）。测试常量是 YAML 现网字面量的镜像，不是第二事实源。
4. **是否留下双路径？** 否。旧形状探针（子串包含 `number`/`run_id`/`||`、`assert "shadow" not in group`、forbidden 前缀表、`quality != primary`）已删；现仅保留精确相等。`test_all_workflow_concurrency_mappings_use_only_github_supported_keys` 管的是全仓允许键集合，不是同一不变式的旧形状断言。

四问通过。F1/F2/F3 处置质量：F2/F3 落到测试上且对改坏敏感（R1 已红验；本轮不重复注入）。F1 权威文档已披露；PR 正文未同步，记为下方 P3，不重报 F1 原意见。

## 全量复验（base..H1）

### 误拒向

精确相等断言的对象是 **仓库 YAML 经 PyYAML 解析后的 mapping**，不是 GitHub 运行时求值结果。

- 会误拒的「合法演进」：`${{ }}` 内空白、`||` 两侧空格、把表达式拆行、换成求值等价的另一种写法。这些在 GitHub 表达式层等价，但文件字面量不同，测试会红。
- 不会误拒的：`group` / `cancel-in-progress` 键对调（`dict ==` 对键序不敏感）；YAML `true`/`false` 的常见大小写同义（解析成 Python `True`/`False`）。
- 引号差异：`cancel-in-progress: "false"` 解析成字符串，与 `False` 不相等会红。这是真差异，不是误拒。

精度落点正确：契约锁的是 pin 进下游的 workflow 文件形状，锁不住、也不该冒充运行时求值。F2 正是因为「子串形状探针拦不住操作数对调」才改成精确相等；把断言退回形状探针会重开已锁定的 F2。空白级误拒是该锁的代价，接受。

OCR 两条 medium（`tests/test_gate_v2_contract.py:301-302`、`:305-314`）主张「过约导致格式化误红」。工具标注 medium；本仓判定不成立（与已锁定 F2 冲突，且不命中 P1 红线）。第三条 OCR（job keyset）复核器已驳回：`test_all_required_jobs_present`（`:179-183`）已锁死七个 job 名。

### 文档-实现一致性向

三方（`docs/design/gate-convergence-criterion.md` / `.github/workflows/gate-v2.yml` / `tests/test_gate_v2_contract.py`）对照：

| 文档陈述 | YAML | 测试 | 结论 |
|---|---|---|---|
| 顶层无 `concurrency` | H1 顶层无该键 | `test_required_v2_has_no_workflow_level_concurrency`（`:291-293`）；shadow 契约 `:126` 再锁一次 | 一致。文档 `:16` 只点了 shadow 文件，同页 `:46-50` 已列出 required 测试名，不另报 |
| quality/primary 独立组、`number \|\| run_id`、cancel true | `:125-127`、`:379-381` 字面量与文档示例块一致 | `_assert_expensive_job_cancel_lock` 按 job 名拼出同一表达式后 `==` | 一致 |
| gate/ledger cancel false、组名不动 | 解析后 mapping 与 base 相等（本轮实测 `base==H0==H1`） | `_WRITER_CONCURRENCY`（`:279-288`）与 YAML 字面量相同后 `==` | 一致。测试比的是常量不是 `git show base:`，但常量与 base 解析结果相同；这是 F3 允许的「精确常量」路径 |
| OCR / resolve_advisory / notify 无锁 | 三 job 均无 `concurrency` 键 | `test_non_writer_non_expensive_jobs_have_no_concurrency`（`:325-329`） | 一致 |
| 组名前缀不冲 panel/ledger/shadow、不含 `shadow` | 贵任务组名为 `gate-required-v2-{quality,primary}-…` | 精确相等已蕴含；独立 forbidden 表已删（增量四问第 4 问） | 一致 |
| 已知限制：旧 run 可能有 panel 无 ledger | `gate` `if: always()`（`:872`）仍发状态条；`ledger` `needs: [quality, primary, gate]`（`:1096`），解析步骤对 ledger input 必选（`:1165`）、非 draft 时 audit 必选（`:1166`） | 无新测试（F1 只披露、不加机制） | 设计文档与实现一致 |

`docs/design/clean-streak-convergence.md:34` 新测试名：

- 文档：`test_gate_and_ledger_writer_locks_remain_cancel_false`、`test_quality_and_primary_have_independent_cancel_true_pr_locks`
- 实际：同名，分别在 `tests/test_gate_v2_contract.py:296`、`:317`

核对通过。该行未列出另外两个并发测试，与该行只描述锁类型的范围相符。

### PR 正文逐句对照

来源：https://github.com/zlxlabs/gate/pull/92 （draft，head = H1）

- 「quality 与 primary 各加 job 级 concurrency、cancel true、组名 `gate-required-v2-<job>-${{ github.repository_id }}-${{ github.event.pull_request.number || github.run_id }}`、两组独立」→ 与 YAML `:126`、`:380` 一致。
- 「顶层仍无 concurrency；gate/ledger cancel-false 锁逐字节未动」→ 顶层无键；解析后 mapping 与 base 相同。文件级 diff 只在 quality/primary 旁插入块 + 文件头注释，未改 writer 的 `group`/`cancel-in-progress` 行。
- 四个具名测试 → 均存在。
- OCR / resolve_advisory / notify 不加锁 → 实现如此。
- 转 draft：新 run `primary` 被 `:369` draft guard skip；skipped job 不入并发组、旧 primary 不因此被取消 → 与锁定决策一致，不重开。官方 concurrency 文档本轮未写明 skip 与组占用的关系；该句按已锁定限制接受，不以本轮文档页缺失升 P1。
- 「取消不是瞬时的」→ 未声称已由静态测试锁死，与设计文档「静态契约锁不住运行时语义」一致。
- 「655 passed / 改 primary cancel 为 false 则指定测试转红」→ 属验证记录，不是实现承诺。本轮抽跑契约 141 passed，不否定全量数字。
- 「合并后用真实 PR 连推验证运行时语义」→ 明确是合并后动作，未把未做的运行时验证写成已完成。

超出实现的承诺见 F4。

## Findings

### F4 — P3：PR 正文仍写「写入型 job 不受影响」，未同步 H1 已知限制

- 严重度：P3。工具标注：无（本轮文档对照，非 OCR）。本仓判定：P3，接受不修、不阻塞合并。
- 违反：不变式 7（文档只陈述已实现事实与已知限制）；卡面要求 PR 正文即 spec、标出超出实现的承诺。
- 位置：PR #92 首段「写入型 job 不受影响」；对照 `docs/design/gate-convergence-criterion.md:55-59` 与 `.github/workflows/gate-v2.yml:1096-1166`。
- P1 两问：真实路径会触发吗？会——supersede 后旧 run 的 `ledger` 会因缺 artifact 硬失败。后果能否接受？能——required 结论由新 run 决定，缺的是观测账本不是门禁结论；不是数据丢失/静默错/崩溃。无法升 P1。
- 说明：不重报 R1 F1（机制缺口已按锁定决策做文档披露）。本条只记录 **PR 正文这一层 spec 仍宽于权威设计文档**。设计文档与 YAML 已一致。

### 未采纳 / 不重复

- OCR 精确相等「过约」：见误拒向；退回形状探针 = 重开 F2。
- R1 F1/F2/F3 原意见：已处置，不重报。

## 熵增

H0..H1 净减法：删 forbidden 前缀常量与重复形状断言，换一个测试期望 dict。无单实现接口、无转发层、无第二消费者问题。

## 降层三问（infra 复验，不新开机制）

1. 终态写入前的不可逆动作：旧 quality/primary 被取消（进行中的模型请求停不干净，文档已写）。`gate` 仍发 panel；`ledger` 可能硬失败。已披露。
2. 守卫唯一性：组名含 workflow 身份前缀 + `github.repository_id`（整仓命名空间），贵任务再加 PR 号或 `run_id`。单仓 GitHub 并发模型下够用。
3. 保护的是写入还是行为：cancel true 保护「不要继续跑过期 lint/主审」；cancel false 保护 writer 跑完。ledger 在上游被取消时写不进去，是 F1 已接受的行为缺口，不是本轮新 P1。

## 证据

- `git fetch origin card/gate-20260829-01 card/gate-20260829-02`；R1 verdict 来自 `origin/card/gate-20260829-02`。
- OCR：`ocr-review --from d67aa0c --to 2bab0fe`，`status=reviewed`，非 skipped。
- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_shadow_v2_contract.py` → 141 passed。
- 本仓无 `ocr-review` 缺失问题（`~/.local/bin/ocr-review` 可用）。

## Verdict

无新增 P1。F4 为 P3，接受不修。

verdict: pass
