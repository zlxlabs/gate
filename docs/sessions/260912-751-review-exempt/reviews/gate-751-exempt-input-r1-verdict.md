# VERDICT — gate-751-exempt-input r1（独立审查）

- 审查对象：`2eed736e8605dace6ff873056d65b30bd7ccc68e..03da4a98dced7e68a4439b84caf85dcd52d253e0`（三笔：`417f08f` 脚本、`57431dc` 工作流 input、`03da4a9` 进度档；审查期间未跟新提交）
- 被审 worktree：`/home/zlx/projects/personal/gate-worktrees/751-g1-exempt-input`（`card/751-g1-exempt-input`，HEAD 与冻结 SHA 一致）
- 规格：`/home/zlx/scratchpad/751-review-exempt/design.md` 要点 1–4、关键不变式 1–4；任务卡 G1「锁定决策」与「约束」
- 风险档：卡面要求按 **internal** 审（P1：数据丢失、静默出错、崩溃、越权、损坏他人数据）。本卡语境下静默出错 = **混入非豁免路径却输出 `false`**，或 **声明非法却无任何可见告警**。
  - 提醒：卡面写「gate 仓未声明 risk-tier」，但被审树 `AGENTS.md:3` 已有 `risk-tier: personal`。不必再补声明；本轮仍按卡面 internal 红线审。
- 结论：**PASS**（P1=0，P2=0，P3=2，均接受不修）

OCR 前置：`ocr-review` status=`reviewed`（minimax / MiniMax-M3，非 skipped）。1 条 style/low，落地为 P3-2；其 `suggestion_code` 会把 ⊆ 判定改坏，禁止按它修。

被审树全量入口末行：`911 passed in 29.48s`。被审树 `git status --short` 为空。

---

## Findings

无 P1 / P2。P3 接受不修：

### P3-1 `docs/` 等「合法但永远匹配不到」的条目静默无效

- **级别**：P3（接受不修）
- **溯源**：设计要点 2 只把含 `*`/`?`/`[`、以 `/` 或 `./` 开头、含 `..`、以 `.github/` 开头列为非法；`docs/` 按「精确路径」合法。方向是**多审不是漏审**，不命中本卡 P1「混入非豁免却 `false`」。
- **位置**：`scripts/classify_pr_reviewable_paths.py:83-95`（`_is_legal_exempt_entry`）、`:115-119`（精确路径 `==`）
- **复现**：

```sh
cd /home/zlx/projects/personal/gate-worktrees/751-g1-exempt-input
python3 - <<'PY'
import json, os, subprocess, sys, tempfile
from pathlib import Path
script = Path('scripts/classify_pr_reviewable_paths.py')
payload = [{'sha':'0'*40,'filename':'docs/a.md','status':'modified','additions':1,'deletions':0,'changes':1}]
env = os.environ.copy(); env['REVIEW_EXEMPT_PATHS'] = 'docs/'
td = tempfile.mkdtemp(); listing = Path(td)/'f.json'; out = Path(td)/'o'
listing.write_text(json.dumps(payload))
p = subprocess.run([sys.executable, str(script), '--github-output', str(out), str(listing)],
                   capture_output=True, text=True, env=env)
print('stdout', p.stdout.strip()); print('stderr', repr(p.stderr)); print('file', out.read_text().strip())
PY
```

- **实际输出**：`review_expected=true`；stderr 空（无 `::warning::`）。同声明下只改账本仍 `false`（舰队默认仍生效）。
- **建议修法**：不必修。若以后要 fail-loud，把「精确路径以 `/` 结尾」收进非法表即可；本轮不要为它加 glob/兼容分支。

### P3-2 类型标注 `exempt: list[str] = ()`（OCR confirmed）

- **级别**：P3（接受不修；无法溯源到不变式 1–4，按规则降级）
- **溯源**：无。OCR 工具标注 style/low；本仓判定 P3。
- **位置**：`scripts/classify_pr_reviewable_paths.py:129-133`
- **两问**：真实调用只走 CLI/`REVIEW_EXEMPT_PATHS`，默认空 tuple 运行正确，`_path_is_exempt` 接受 `Sequence[str]`。触发了也只是标注不准，不改判定。
- **建议修法**：可改成 `exempt: Sequence[str] = ()`。**禁止**采用 OCR `suggestion_code`：它把 `not all(_path_is_exempt…)` 换成「每个文件对每个条目都 `_path_matches_entry`」的双重 `all`，会把 ⊆ 判坏。

---

## 问 1：⊆ 判定与 fail-closed

`review_expected()`（`:129-138`）唯一 `false` 出口：`filenames` 非空、且无截断、且 `all(_path_is_exempt)`。`main()`（`:187-213`）在声明非法时**不调用** `review_expected`，直接 `expected=True`。逐分支（均在被审树用真实 subprocess 量过，混入 `scripts/x.py` 时从未出现 `false`）：

| 分支 | 落点 | 实测 `review_expected` / rc |
|------|------|------------------------------|
| 空列表 `[]` | `review_expected`: `not filenames` → True | `true` / 0 |
| 空列表 + 合法声明 `docs/**` | 同上，声明救不了空列表 | `true` / 0 |
| compare `truncated: true`（即便 files 全豁免） | `has_next_page or truncated` → True | `true` / 0 |
| `--has-next-page`（账本 only） | 同上 | `true` / 0 |
| listing 失败（工作流：`gh api` 非零；脚本未启动） | shell 预写 `true` 后 `exit 0` | 模拟：文件仍 `true`，rc 0 |
| JSON 不可用 `{` / 缺文件 | `except` 写 `true`，`return 1`；工作流再补 `true` | `true` / 1 |
| 声明非法 `*.md`（即便只改账本） | `illegal is not None` → True，且打 warning，rc 0 | `true` / 0 |
| 混入 `docs/a.md` + `scripts/x.py`，声明 `docs/**` | `not all(exempt)` → True | `true` / 0 |
| 非法声明 + 混入非豁免 | 仍 True | `true` / 0 |

**不存在**「混入非豁免路径却输出 `false`」的输入组合：`all(_path_is_exempt)` 任一路径未命中即 `true`。独立红验（**拷贝**脚本到临时目录注入，未改被审树）：把 `not all` 改成 `not any` 后，同一混入用例变成 `false`——证明这条断言锁的就是 ⊆ 而不是恒真。

工作流 listing 失败路径（`gate-v2.yml:154-175` / shadow `:160-181`）预写 `true`、`gh api` 失败则 notice + exit 0，脚本根本不跑。这是既有 fail-closed，本 diff 未改。

---

## 问 2：条目词汇边界（卡面未列形态实测）

`_is_legal_exempt_entry`（`:83-95`）放行条件：非 `/`、非 `./`、非 `.github/` 前缀、无 `..` 段；若以 `/**` 结尾则目录非空且目录内无 `*?[`，否则整串无 `*?[`。恰好对应要点 2 的两种形式。卡面十种非法形态见问 6，全部覆盖。

下列为卡面未列、真实 subprocess / 函数实测：

| 形态 | `_is_legal` | 改 `docs/a.md` | 改账本 | 有 `::warning::` | 是否合理 |
|------|-------------|----------------|--------|------------------|----------|
| `docs/` | 合法（精确路径） | `true`（匹配不到） | `false`（舰队默认） | 无 | 静默无效，多审；可接受，见 P3-1 |
| `docs` | 合法（精确文件名 `docs`） | `true` | `false` | 无 | 合理：`docs/**` 才是目录 |
| `docs/**/` | 非法（不以 `/**` 结尾且含 `*`） | `true` | `true` | 有 | 合理 |
| `a//b` | 合法（精确） | `true` | `false` | 无 | GitHub 不会报 `a//b`，多审 |
| `foo bar.md` | 合法（精确，允许空格） | `true` | `false` | 无 | 合理 |
| `docs\foo`（反斜杠） | 合法（精确） | `true` | `false` | 无 | 不认 Windows 分隔符，多审 |
| `.github` | 合法（**不是** `.github/` 前缀） | `true` | `false` | 无 | 符合要点 3 字面；不会豁免 `.github/workflows/…` |
| `.githubx/**` | 合法（目录 `.githubx`） | `true` | `false` | 无 | 合理，不是禁区 |
| `docs\**` | 非法（含 `*`） | — | `true` | 有 | 合理 |
| `**` / `/**` | 非法 | — | `true` | 有 | 合理，无万能匹配 |
| `.Github/**` | 合法（大小写敏感） | `true` | `false` | 无 | 合理 |

`docs/` 静默无效：**可接受**。调用方若把 `docs/` 当成目录，结果是模型腿照跑，不是漏审。

---

## 问 3：前缀匹配与舰队默并集

`_path_matches_entry`（`:115-119`）：`docs/**` → `filename.startswith("docs/")`；精确路径 → `filename == entry`。实测：

| 声明 | 路径 | 输出 |
|------|------|------|
| `docs/**` | `docs/a.md` / `docs/readme.md` | `false`（跳过） |
| `docs/**` | `docs-old/x.md` / `docs` / `xdocs/a` / `Docs/a.md` | `true` |
| `docs/**` | `docs/`（API 不会报目录） | `false`（`startswith("docs/")`） |
| `docs/readme.md` | 自身 | `false` |
| `docs/readme.md` | `docs/readme.md/nested` / `docs/readme` / `docs/readme.mdx` | `true` |

**并集**：

- 声明为空 / 未设 env：只改账本 → `false`（与 PR 146 相同）；混入其它 → `true`。
- 声明非法：`main()` 整份作废，**舰队默认也不再生效**（只改账本也 `true`，并打 warning）。`test_illegal_declaration_voids_fleet_default_skip` 锁住。

与设计「整份声明作废、`review_expected=true`」（要点 4、锁定决策 4、不变式 3）**一致**。若非法时仍保留舰队默认，误配 caller 的纯账本 PR 会继续跳过——那是更松的语义，不是当前规格。

---

## 问 4：跨发布边界

| 检查 | 结果 |
|------|------|
| classify `env: REVIEW_EXEMPT_PATHS: ${{ inputs.review_exempt_paths }}` | 两份工作流都有（`gate-v2.yml:151`、`gate-shadow-v2.yml:157`） |
| `run:` 块无字面 `${{ }}` / 无 `inputs.review_exempt_paths` | 实测 False；契约测试钉死 |
| 四处模型腿 `if:` 与 `REVIEW_EXPECTED` env | `git diff -U0 2eed736e..03da4a9` 对 `review_expected != 'false'` / `REVIEW_EXPECTED:` / draft `if:` **零命中** |
| `workflow_call.inputs` 集合 | `test_workflow_call_inputs_include_review_exempt_paths` 两边都恰好多 `review_exempt_paths`（string / default `""`） |
| caller 模板 | 只加注释，无默认豁免路径 |

不变式 4 的既有契约测试未改断言（diff 仅新增 49 行 inputs/env 断言）。

---

## 问 5：`::warning::` 与 runner

**stderr 会被识别为 annotation。** 出处不是文档示例（文档与 `@actions/core` 都写「命令走 stdout」），而是 runner 实现：

1. `ScriptHandler.cs`（actions/runner v2.328.0）对 stdout **和** stderr 各挂一个 `OutputManager`：`StepHost.ErrorDataReceived += stderrManager.OnDataReceived`。
2. `OutputManager.OnDataReceived`（main，`:77-90`）对每一行调用 `_commandManager.TryProcessCommand`。`run:` 步的 Python stderr 因此与 stdout 同等解析 `::warning::`。
3. 官方文档仍把 stdout 写成工具通道（[Workflow commands for GitHub Actions](https://docs.github.com/en/actions/reference/workflow-commands-for-github-actions)：「sent to the runner over `stdout`」）；`@actions/toolkit` `command.ts` 的 `issueCommand` 写 `process.stdout`。实现比文档宽，本实现走 stderr **有效**。

工作流未把 python 的 stderr 重定向丢掉（`2>/dev/null` 只包着 `gh api`）。

| 输入 | 警告行 | 换行进 annotation？ | 控制字符？ |
|------|--------|---------------------|------------|
| `*.md` | `::warning::classify_pr_paths: invalid review_exempt_paths entry: *.md` | 无 | 无 |
| `*.md` + 第二行 `::error::injected` | 只警告首个非法 `*.md`；整份作废 | `splitlines` 切开，不进同一 annotation | 无 |
| `foo\t*\tbar` | `foo * bar`（`str.split()` 压空白） | 无 | 无 |
| `x`×300 + `*` | 一行，总长 265 = `::warning::`(11)+前缀(54)+200；`*` 被截掉 | 无 | 无 |
| `*.md\x1b[31mRED` | ANSI **原样进入** 警告行 | 无 | **有** ESC |
| env 内嵌 `\0` | Python 拒绝 `embedded null byte`（env 不能带 NUL） | — | 不可达 |
| `foo\r*.md` | 按 `splitlines` 拆成 `foo` + `*.md`，警告后者 | CR 不当成正文换行 | 无 |

压空白 + 200 字截断对「撑爆一行 annotation」够用。C0/ANSI 未剥，见上表；非法时仍有可见 `::warning::` 且 `review_expected=true`，不构成 P1「无任何可见告警」。

---

## 问 6：测试约束力

卡面十种非法形态与 `test_illegal_exempt_entry_forms_force_review` 参数集**逐项相同**：`*.md`、`docs/*`、`docs/**/x`、`a?b`、`[ab]`、`/abs`、`./rel`、`a/../b`、`.github/workflows/x.yml`、`.github/**`。每条还断言 stderr 含 `::warning::`、不含 `::error::`、`parse_exempt_declaration` 返回空列表。

三条红验（实现方报告原文为 AssertionError `'false' == 'true'`；本轮在临时拷贝上独立复验，被审树未改）：

| # | 注入 | 锁住的不变式 | 拷贝上混入/禁区结果 | 原脚本 |
|---|------|--------------|---------------------|--------|
| ① `not all` → `not any` | 不变式 1 ⊆ | 混入 `scripts/x.py` → `false` | `true` |
| ② 删 `.github/` 拒绝 | 要点 3 / 不变式 3 | `.github/workflows/x.yml` 与 `.github/**` → `false`；`*.md` 仍 `true`（其它规则还在） | 均为 `true` |
| ③ `startswith(directory+"/"` → `startswith(directory)` | 高风险前缀边界 | `docs-old/x.md` + `docs/**` → `false` | `true` |

新增用例无恒真断言：`assert SCRIPT.is_file()` 只是入口卫兵；警告长度上界 `<= 11+80+200` 在 301 字符非法条目上会因未截断而红。跨进程 `test_subprocess_env_declaration_writes_github_output_false` 断言 `--github-output` 整行 `review_expected=false\n`，喂的是工作流同名 env。

---

## 不变式对照

| # | 不变式 | 结果 |
|---|--------|------|
| 1 | 路径 ⊆ 生效集合才跳；混入其它照审 | ✅ 问 1 + 红验 ① |
| 2 | 生效集合恒含账本；空声明与 PR 146 相同 | ✅ 空 env 账本 `false`；既有 16 例表驱动仍在 |
| 3 | 非法条目整份作废且 `true` | ✅ CLI + warning；舰队默认一并作废 |
| 4 | 四处 `if:` 逐字节未变 | ✅ diff 不含这些行；既有契约测试未改断言 |

要点 1（input 名/类型/默认、并集而非覆盖）与锁定决策 1–5、约束 1–7 均对齐。模板未给默认豁免。无 glob 引擎、无 AGENTS.md 锚点、无 `.gitattributes`（已否决方案未重开）。无新增无第二消费者的抽象：`parse_exempt_declaration` / `REVIEW_EXEMPT_PATHS` 的消费者是两份 workflow classify env + 跨进程测试。

## 范围与合并提醒

- 只审冻结 SHA；diff 9 文件（脚本 + 两工作流 + 两模板注释 + 三类测试 + 进度档）。
- 本仓 workflow 被下游 `@<40hex>` pin：**合并必须用 merge commit**，禁止 squash/rebase。
- 熵：无额外包装层。OCR 建议的「按文件 hoist 并集」是优化不是缺陷。
