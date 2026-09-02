verdict: pass

# review gate PR #122 第 1 轮 verdict — quality 短路时 ledger 仍写出账本行（gate#121）

- 审查对象（冻结）：`55f31f18c431af7f4b9e25f3182ee12ac1c9c2e3..8943d2861d7ed1b4ae10e3927b21baa06b2369e9`
  （3 commits，6 文件 +323/−23）。审查期间核对远端 `refs/pull/122/head` 仍为 `8943d28`，无新提交。
- spec 指针：任务卡 S1（显式布尔判据）/ S2（传递链三跳）/ S3（取值域）/ S4（行为矩阵）/ S5（非目标）。
- 风险档：本仓 personal，按 infra 例外提档 internal，收敛条件 = 连续 2 轮无新增 P1。本轮为第 1 轮。
- 基线：H0 临时 worktree 全量测试 `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q` → **800 passed in 11.52s**（复现实现方里程碑 3 的 800 passed）。

## 本轮新证据清单

### 证据 1：真跑 S4 矩阵前四格（生产同一条 main() 序列）

方法：H0 detached worktree（`/tmp/gate122/H0`），import 该 worktree 的 `build_ledger.py`，
仅把 `fetch_prior_entries` / `fetch_comments` / `post_state_comment` 打成空操作（GH_TOKEN 置位），
逐格构造真实 argv 调 `main()`。audit 用 canonical primary `verdict=fail`（2 findings：
measured/major + inferred/blocker）。驱动脚本 `/tmp/gate122/drive_cells.py`。

- **格 1（short 缺省=false，preflight 合法）**：rc=0，jsonl 行写出，`"coverage": {"complete": true, "diff_lines": 100, "mode": "single", "shards": 1}`。
- **格 2（short 缺省=false，preflight 文件缺失）**：抛 `ValueError: canonical primary preflight has invalid coverage shape`（traceback 经 `main()` → `build_entry` → `_review_summary` line 252），未写出任何行。**未放松** ✓。
- **格 3（short=true，preflight 文件缺失）**：rc=0，jsonl 行原文（sort_keys 后）关键字段：
  `"preflight": null, "install": null`，`review` 投影完整——`"coverage": null`，
  `"severity_counts": {"blocker": 1, "major": 1}`，`"category_counts": {"correctness": 1, "security": 1}`，
  `"trigger_kind_counts": {"inferred": 1, "measured": 1}`，`"inferred_p1_count": 1`，
  `"finding_ids": ["correctness.bad-state", "security.leak"]`，`"finding_count": 2`，
  `"runtime": {"duration_s": 9.5}`，`"reviewer": "codex-sub"`，`"verdict": "fail"`，`"status": "fail"`，
  `"shadows": {}`，`"result"` 原样透传。**与 S4 第三格逐字段一致** ✓。这正是线上两次复现
  （run 33632989004 / 33633474736）的失败形态：primary fail → quality skipped → 无 input artifact →
  preflight 缺失，现在行能写出了。
- **格 4（short=true，preflight 合法）**：rc=0，coverage 照常算出（与格 1 完全相同）。
  短路标志没有变成「忽略 preflight」的开关 ✓。

### 证据 2：降层三问

**① 终态写入前的不可逆动作**：`main()` 在 `build_entry` 之前只做 GitHub API **读**
（`fetch_prior_entries` 读 artifacts、`fetch_comments` 读评论），无任何写。
写在 `write_ledger` 之后：`post_state_comment`（PR 评论）与 artifact 上传（workflow 步）。
短路格下这些读的行为不变；变化的是「以前 ValueError 整步失败、评论与上传都不发生，
现在行写出、评论照常发」。评论渲染路径 `build_state_comment` 与 `_append_summary`
只读 `status/finding_count/reviewer/failover/comparison/install`，均不读 `coverage`，
`coverage: null` 对其无影响（源码核对：build_ledger.py:140-169、834-874）。

**② 守卫值在真实部署形态下可信**：把 `gate-v2.yml` 的 resolver 内嵌 Python 原样抽出
（yaml 解析 → heredoc 切片，驱动 `/tmp/gate122/drive_resolver.py`），自造 artifact listing 真跑三场景：

- A（quality=skipped + primary=failure + 无 input artifact）：rc=0，
  输出 `input_artifact_id=`（空）+ `input_short_circuited=true`，并打印 notice。
- B（quality=success + primary=success + input artifact 缺失，即「quality 成功但上传失败」）：
  rc=1，`stderr: No matching required ledger input artifact found (quality upload outcome: failure)`，
  **output 一个字节都没写** —— resolver 在 Build 之前 fail-loud，不存在「写入侧 false、读取侧文件缺失」的错位。
- C（quality=skipped + primary=failure + 早先 attempt 留有 input artifact，current=2）：rc=0，
  `input_artifact_id=101`、`input_short_circuited=false` → 走下载、按格 1 算 coverage。

四组合穷举：短路∧无 artifact → true+不下载（A）；短路∧有 artifact → false+下载（C）；
非短路∧有 artifact → false+下载；非短路∧无 artifact → resolver 直接 SystemExit（B）。
`input_short_circuited=true` 与「Download 步被 `input_artifact_id != ''` 守卫跳过」由同一个
`input_artifact is None` 派生，**两侧判据不可能不一致**。剩余理论缝（artifact 存在但 zip 内缺
preflight 文件）落在「false + 文件缺失」→ ValueError fail-loud，是 S4 第二格明文要求的行为。

**③ 保护覆盖的是「写入」还是「行为」**：放行后账本多了一类 `coverage: null` 的行。
下游消费者逐一去代码核对（非推断）：
- gate-hub `scripts/review-ledger-report.py:129-149`：只读 `status/finding_count/severity_counts/category_counts/runtime`，**不读 coverage**。
- gate-hub `scripts/review-ledger-replay.py:243-428`：只读 `severity_counts/result.findings/status`，**不读 coverage**。
- 全 gate-hub 仓 `coverage` 检索（type=py，228 命中）无一条落在 ledger 消费者上。
- 本仓其它读取点：`_append_summary`、`build_state_comment` 均不读 coverage（见 ①）；
  `coverage: null` 行同时带 `preflight: null`，与既有「missing-audit」行的形态一致
  （`_review_summary` 早退路径本来就产 `coverage: None`），不是新形态。

结论：`coverage: null` 对所有现存消费者惰性。

### 证据 3：反向查误放行

构造「short=true + preflight 文件存在且非法（非空 dict、缺 `thresholds`）」真跑
（drive_cells.py 格 5：`{"diff_lines": 5, "classification": "single", "review_plan": "single"}`）：
**抛 `ValueError: canonical primary preflight has invalid coverage shape`，未放行**。

判据 `input_short_circuited and (preflight is None or preflight == {})` 的两个合取项都不可省：
显式布尔是第一判据（S1 满足——不是靠空 dict 嗅探短路），`== {}` 是把放行收窄到
「文件缺失经 `_load_json`→None→`or {}` 归一化」的恰好形态的第二判据。这不是
「用空 dict 嗅探」的形态：空 dict 单独（short=false）仍抛（格 2 实证），
非空非法 dict 即使 short=true 也抛（格 5 实证）。且由证据 2②，生产链路上 short=true
时 preflight 文件物理上不可能存在，第二判据在真实环境恒为真，只在 API 误用时起收窄作用。

### 证据 4：变异验证（`_review_summary` 全 return 穷举 + 新增分支）

`_review_summary` 全函数 grep 确认**恰好 2 条 return**（line 213 早退、line 287 主返回）。
在 H0 worktree 注入，每处先 `sed -n` 打印改坏行留痕，跑全量 800 测，结束后 `git checkout` 还原：

| # | 注入点 | 改坏行留痕 | 结果 |
|---|---|---|---|
| M1 | return@213（`if not audit` 早退） | L219 `"finding_count": 0,` → `1,` | **不红（800 passed）** |
| M2 | return@287（主返回） | L299 `"coverage": coverage,` → `None,` | 红：3 failed（`test_v2_review_preserves_result_and_recomputes_legacy_coverage` ×2、`test_short_circuited_valid_preflight_still_computes_coverage`） |
| M3 | 新增格判据@236 | `if input_short_circuited and (...)` → `if preflight is None or preflight == {}:`（已否决的纯空 dict 嗅探） | 红：2 failed（`test_primary_review_empty_preflight_without_short_circuit_still_raises`、`test_omitted_input_short_circuited_matches_explicit_false`） |

M1 不红归因（二选一纪律）：该路径**可达**（draft PR / 无 audit 时早退），不红原因是
**测试弱**——唯一起作用的测试 `test_review_summary_defaults_when_audit_missing`
（tests/test_review_ledger.py:1509-1520）断言了 reviewer/failover/attempts/trigger_kind_counts/
inferred_p1_count，**未断言 finding_count**。该 return 是存量代码（非本 diff 引入），
记 backlog，不占用本轮（见 Findings F1）。还原后 H0 worktree `git status` 干净。

### 证据 5：熵增审查（对照 agent-config `templates/REFACTOR-guide.md` 坏味道词表）

对 diff 每个新增元素逐条过词表：

- `_review_summary` keyword-only 形参 `input_short_circuited`：消费者 = `build_entry` 调用点；
  S1 明令要求的显式布尔，非投机通用性。
- `build_entry` 形参透传：消费者 = `main()`；同一根链条的第二跳，非转发-only 包装层。
- CLI `--input-short-circuited`：消费者 = action.yml 转发行；取值域 fail-loud 有 W4 七参测试锁。
  `default=None` + 显式三分支与 `default="false"` 行为完全等价（已有测试锁死等价性），
  轻微命中词表 4「多余路径」，见 Findings F2（≤P3，不阻塞）。
- action input `input-short-circuited`（default "false"）：消费者 = gate-v2.yml:1409；
  default 保护 legacy `gate.yml:388` 调用方（该 caller 省略此 input，实证其 ledger 步未传
  且 audit 为 legacy 形态走 else 分支，行为不变）。
- gate-v2.yml:1409 一行 `with:`：消费者 = action input。非镜像事实（唯一事实源是
  resolver 的 `input_artifact is None` 派生）。
- 新增分支格：S4 第三格的直接实现，无第二判定路径（`== {}` 是收窄不是并行判定，见证据 3）。
- 无新增类 / 基类 / 配置层 / 中间件 / fallback / 重试 / 防御式 catch（S5 全守）。

## Findings（每条：工具标注 / 本仓判定 / P1 两问）

### F1：`if not audit` 早退路径的 `finding_count` 无断言（变异 M1 存活）

- 工具标注：无（本轮变异验证自产）。
- 本仓判定：**P3 backlog**。该 return 是存量代码，非本 diff 引入（diff 只动 signature 与
  primary 分支）；属「测试弱」不是代码缺陷。修复方向：给
  `test_review_summary_defaults_when_audit_missing` 补 `finding_count == 0` /
  `finding_ids == []` / `coverage is None` 断言。
- P1 两问：① 该路径在真实环境可达（draft PR 无 audit），但**当前代码行为正确**，缺陷只在
  注入后存在——真实环境无可触发缺陷；② 无后果。两问都不过 → 非 P1。
- spec 溯源：无法溯源到本卡 spec（S1-S5 均未覆盖早退路径断言完备性），按纪律降级处理。

### F2：`--input-short-circuited` 的 `default=None` 三分支与 `default="false"` 行为等价

- 工具标注：无（人工熵增审查）。
- 本仓判定：**P3 backlog**。`default=None`+显式 None 分支 与 `default="false"` 在全部输入下
  行为一致（`test_omitted_input_short_circuited_matches_explicit_false` 已锁死等价），
  轻微命中坏味道词表 4「多余路径」。注释虽写明是刻意选择，但省掉 None 分支可少 2 行。
  不阻塞合并；若下一轮顺手可做减法。
- P1 两问：① 无缺陷可触发（行为等价有测试锁）；② —。非 P1。
- spec 溯源：与 S3（缺省等价 false）不冲突——两种写法都满足 S3。

## S1–S5 对照结论

- **S1** ✓：判据是上游显式布尔；空 dict 只在布尔为真时起收窄作用（证据 3 双向实证）。
- **S2** ✓：三跳齐备且有契约测试锁——`gate-v2.yml:1361`（写 output）→ `:1409`（with 传入）
  → `action.yml`（input 声明 + composite 字面量转发，实测 `${x:-` / `:-}` 零命中）
  → `build_ledger.py` `--input-short-circuited`。W5 两条契约测试分别锁 workflow 跳与 action 跳。
- **S3** ✓：CLI 严格 `{true,false}`，W4 七参（yes/1/TRUE/True/空/maybe/"false "）全 SystemExit；
  缺省 ≡ false 有专项测试；resolver 写入侧只写 `true`/`false` 两个字面量（gate-v2.yml:1361）。
- **S4** ✓：前四格真跑逐字段核对（证据 1）；第五格（非 primary_review）无代码路径变化
  （else 分支逐字段未动，diff 纯缩进迁移）。
- **S5** ✓：未改 #119 短路语义（gate-v2.yml 仅 +1 行 with）、未动 aggregate.py、未动
  gate/gate 判定与 concurrency、无新类/包装层/配置项/第二判定路径、无 fallback/重试/防御式 catch。

## 收敛判定

- 新增 P1 = 0，新增 P2 = 0；P3 backlog 两条（F1 存量测试缺口、F2 轻微多余路径）。
- 按提档后 internal 收敛条件「连续 2 轮无新增 P1」：本轮为第 1 轮达标，**仍需第 2 轮**
  （建议换角度：正向四格已查，下轮可查 ledger 消费端跨 rerun 连续性——`post_state_comment`
  状态游标在短路行与非短路行交替时的 dedupe/conflict 行为）。
- 红验抽查：实现方里程碑 3 声称的 4 处注入中，与 `_review_summary` 相关的 2 处
  （新格翻 raise、判据退化嗅探）已由本轮 M3/M2 独立复现翻红；进度文档所述
  「还原后 git diff 为空、800 passed」与本轮实测一致。
