# 任务卡：ledger 的 gate-terminal 寻址允许回落到本 run 前序 attempt（带归因闸）

## 目标

修 zlxlabs/gate#101：`gate / ledger` job 因瞬时故障变红后，`gh run rerun --failed`
能把它修绿。做法是让 ledger 的 `gate-terminal` 工件寻址与同文件里 input/audit 工件
一样允许回落到本 run 编号 ≤ 当前的最新 attempt，并加一道归因闸区分「聚合器本 attempt
根本没跑（回落合法）」与「聚合器跑了却没写出终态（真异常，硬失败）」。

## 非目标

- 不改 `gate-terminal` 工件命名约定（attempt 后缀保留）。
- 不改 `gate` 聚合器的判决逻辑、不改 required status check 拓扑、不碰 `gate.yml`（legacy）
  与 `gate-shadow-v2.yml`。
- 不改 `templates/` 下的 caller 模板，不 bump 任何仓的 pin SHA。
- 不动私有 gate-hub 仓的任何文档（跨仓边界）。

## 基线与所有权

- **Task-Id**：
- **Fixes-Issue**：#101
- **Verify-Command**：python3 -m pytest tests/test_gate_v2_contract.py tests/test_review_ledger.py -q
- **Diff-Lines-Target**：300
- **Diff-Lines-Hard**：600
- **阶段**：implementing
- **锁定决策**：方案已拍板，执行器不得重开：`docs/sessions/gate101-ledger-rerun/design.md`
  （本卡 base 上已存在）。特别是：①归因闸必做，不得以「概率低」为由省略；
  ②不采用「只改报错文案提示需全量重跑」的替代路；③terminal 身份校验只放宽
  `run_attempt` 一项。对期望值有异议按「给执行器的一条要求」在 report.md 显式提出。
- **任务类型**：backend-logic
- **复杂度**：M
- **Base commit**：origin/feat/gate-101 HEAD（派发时以 `git rev-parse origin/feat/gate-101` 解析出的 sha 为准；该分支已含本卡与 design.md，勿改填 origin/main）
- **Branch**：由 delegate 分配（`card/<worktree 名>`），执行器不得另建分支
- **Worktree**：由 delegate `--worktree` 创建
- **当前唯一写入者**：本卡执行器
- **执行器与模型**：按 envelope 实际值回填
- **执行器角色声明**：本会话就是执行器（implementer 角色），全局 AGENTS.md「模型编排」段的
  主代理委派纪律**不适用于本卡**；不限制亲自落盘还是委派子代理，唯一硬约束是最终产物落在
  指定路径——子代理不返回就直接自己写完。
- **计划者与审查者**：主脑（fable5）拆卡与验收；合并前终审走独立 review 卡。

## 修改边界

- **允许**：
  - `.github/workflows/gate-v2.yml`（仅 `ledger` job 的 `Resolve v2 ledger artifacts` 步骤
    及其新增的归因查询；其余 job 一行不动）
  - `.github/actions/review-ledger/build_ledger.py`
  - `tests/test_gate_v2_contract.py`
  - `tests/test_review_ledger.py`
  - `docs/sessions/gate101-ledger-rerun/progress/gate-101-progress.md`（新建）
- **禁止**：`.github/workflows/gate.yml`、`.github/workflows/gate-shadow-v2.yml`、
  `.github/actions/gate-aggregator/**`、`templates/**`、`README.md`、
  `docs/sessions/gate101-ledger-rerun/design.md`（已拍板，不得改写）。
  验收产物（scorecard / verdict）由主脑写入，执行器不得自评。
- **验证根豁免**：.github/workflows/gate-v2.yml
- **Scope-Globs**：.github/workflows/gate-v2.yml .github/actions/review-ledger/build_ledger.py tests/test_gate_v2_contract.py tests/test_review_ledger.py docs/sessions/gate101-ledger-rerun/progress/gate-101-progress.md
- **高风险区域**：
  - `ledger` job 的 `Resolve v2 ledger artifacts` 是 heredoc 内嵌 Python（`<<'PY'`）。
    `tests/test_no_literal_gha_expression_in_run_blocks.py` 与
    `tests/test_gate_v2_contract.py` 会解析这段 run 块，改动必须保持它可被
    `run.index("<<'PY'\n")` / `run.index("\nPY\n")` 切出来、且不得在 run 块里写字面
    `${{ }}` 表达式（一切 GitHub 上下文值经 `env:` 传入）。
  - 归因查询新增的 `gh api` 调用需要 `actions: read`。同一 job 已有的工件列举调用证明
    该权限在位，但**新端点是否同样可读必须实测**，不得只靠推断（见完成条件）。

## 约束与假设

- **约束**（违反即拒收）：
  1. terminal 工件永不选择 attempt > 当前 attempt —— 检查：`tests/test_gate_v2_contract.py`
     新增/改写用例，构造 `gate-terminal-v1-3` 而当前为 2，断言不被选中。
  2. `gate` job 本 attempt 运行了却无对应 terminal 工件时必须硬失败，且 stderr 文案与
     「工件一个都没有」的原文案可区分 —— 检查：同文件用例断言两种文案各自出现、
     退出码非零。
  3. terminal 身份校验只放宽 `run_attempt`；`repository` / `pr_number` / `run_id` /
     `head_sha` 任一不符仍抛 `gate terminal identity mismatch` —— 检查：
     `tests/test_review_ledger.py` 逐字段参数化用例。
  4. terminal 来源 attempt 等于当前 attempt 时，账本条目字段集合与改动前**逐字节一致**
     —— 检查：`tests/test_review_ledger.py` 对同 attempt 路径断言条目 key 集合不含新字段。
  5. 归因查询只在「当前 attempt 无 terminal 工件」时发起，工件存在时不得多打这次 API
     —— 检查：`tests/test_gate_v2_contract.py` 用例在工件齐备场景下断言解析器不读取
     jobs 输入（把 jobs 数据源做成可注入的参数/文件，缺失时不报错）。
  6. `.github/workflows/gate-v2.yml` 的 `ledger` job 之外的任何 job 零改动 —— 检查：
     报告贴出 `git diff --stat` 与该文件的改动行号区间，人工核对；[人工裁决] 由主脑在
     验收记分卡 note 记录判定结果。
- **假设**（执行器可自行调整，调整须在 report.md 写明理由）：
  - 归因数据（当前 attempt 的 jobs 列表）用哪种形式喂给内嵌 Python：先用 `gh api` 落到
    临时文件再作为 `sys.argv` 传入，或直接在 Python 里 `subprocess` 调用——任选，但必须
    满足约束 5（可注入、缺失不报错）且保持测试可离线驱动。
  - 账本条目里标注来源 attempt 的字段名与放置层级（顶层可选字段，或
    `disposition_receipt_consumption` 内的可选键）自行决定，写进报告。
  - 报错文案的具体措辞。

## 不变式轴表

轴 A：当前 attempt 的 terminal 工件是否存在 × `gate` job 本 attempt 是否运行

| 当前 attempt 有 terminal 工件 | gate 本 attempt 运行 | 期望 | 检测点 |
|---|---|---|---|
| 有 | 是 | 选当前 attempt；不发起归因查询 | `test_gate_v2_contract.py` 用例：断言选中 id 与 `terminal_source_attempt=<current>`，且未消费 jobs 数据源 |
| 有 | —（不查） | 同上 | 同上（工件存在即以工件为准，归因查询不发起） |
| 无 | 否 | 回落到 ≤ current 的最大 attempt，输出 `terminal_source_attempt=<该 attempt>` | `test_gate_v2_contract.py` 用例：工件只有 `-1`、当前 2、jobs 列表不含 gate → 退出 0 且 `terminal_source_attempt=1` |
| 无 | 是 | 硬失败，文案点名「聚合器本 attempt 运行但未产出终态」 | `test_gate_v2_contract.py` 用例：工件只有 `-1`、当前 2、jobs 列表含 gate → 非零退出且文案匹配 |
| 一个 attempt 的 terminal 都没有 | 任意 | 硬失败，保留原文案 `No matching required gate terminal artifact found` | 改写 67ba1f4 加的 `test_ledger_resolver_refuses_stale_terminal_when_current_attempt_is_missing` |
| 只有 attempt > current 的工件 | 任意 | 硬失败（不得选未来工件） | 新增用例 |

轴 B：terminal envelope 身份字段 × 期望

| terminal.run_attempt | 其余身份字段 | 期望 | 检测点 |
|---|---|---|---|
| == 当前 | 全对 | 通过，条目不含来源 attempt 字段 | `test_review_ledger.py` |
| < 当前且 ≥ 1 | 全对 | 通过，条目标注来源 attempt | `test_review_ledger.py` |
| > 当前 | 全对 | 抛 `gate terminal identity mismatch` | `test_review_ledger.py` |
| 0 / 负数 / 非整数 / 布尔 | 全对 | 抛 `gate terminal identity mismatch` | `test_review_ledger.py` 参数化 |
| 合法 | repository 不符 | 抛 mismatch | `test_review_ledger.py` 参数化 |
| 合法 | pr_number 不符 | 抛 mismatch | 同上 |
| 合法 | run_id 不符 | 抛 mismatch | 同上 |
| 合法 | head_sha 不符 | 抛 mismatch | 同上 |

轴表刻意写得少。**如果你认为某一格的期望值可疑、或与「目标」段矛盾，必须在 report.md
里显式提出，不得默默按格实现。提出不算抗命，是本卡要的东西。**

## 完成条件

- **产物入库**：全部落盘产物提交到 delegate 分配的 `card/<worktree 名>` 分支，验收以该分支
  提交为准；报告贴出 `git log --oneline -1` 与 `git show --stat --format= HEAD` 实际输出。
  每完成一个能独立通过测试的单元就 commit 一次，不要攒到最后一起提交。若 pre-commit 守卫
  拦下提交，把守卫完整报错原样贴进报告并停下，保留现场。
- **行为验收**：从 CI 使用者视角——ledger job 在「本 attempt 无 terminal 工件且聚合器本
  attempt 未运行」时成功产出账本并在日志里显示 `terminal_source_attempt=<前序 attempt>`；
  在「聚合器本 attempt 运行了却无终态」时失败并给出点名该情形的报错。
- **相关测试**（全量入口，禁 `-k` 子集）：
  - `python3 -m pytest tests/test_gate_v2_contract.py tests/test_review_ledger.py -q`
  - 收尾 commit 之后另跑一次 CI 同款全量入口：`python3 -m pytest tests -q`
  - grep 引用被改符号的测试并全跑：至少
    `grep -rn "_disposition_receipt_consumption_from_terminal\|load_gate_terminal_envelope\|terminal_source_attempt\|Resolve v2 ledger artifacts" tests/`
    命中的每个文件都要跑。
  - 验证预算估算：本仓无 durations 基线文件，全量 pytest 实测约 1–3 分钟，单轮远低于
    30 分钟，不需分段跑；但仍按下方提交纪律小步提交。
- **跨发布边界验收**：terminal 是跨 job 的 artifact/envelope 发布边界。consumer 侧
  （`build_ledger.py`）的新增/改写用例必须至少有一条使用**真实 producer 产出**的 envelope
  ——复用 `tests/test_review_ledger.py` 已有的 `_producer_terminal()` helper（它调
  `aggregate.build_terminal_envelope`），把 `run_attempt` 造成前序 attempt 的场景也走这条
  真实 producer 路径，禁止只用手搓 dict 断言集成落地。
  producer artifact 名：`gate-terminal-v1-<repository_id>-<head_sha>-<run_id>-<run_attempt>`；
  入口：`.github/actions/gate-aggregator/aggregate.py` 的 `build_terminal_envelope`；
  schema：`schema_version: 1` / `kind: gate_terminal`。
- **红验有效性**：约束 1、2、3 各做一次红验（改坏对应实现确认测试转红），转红输出原文贴进
  报告，红的类型必须是断言失败。注入方式优先「只改判据本身那一行」。红验前先 commit 已
  验证的真修复；还原只还原刚改坏的那一处，禁止整文件 `git checkout -- <file>`。
- **归因查询权限实测**：报告必须写明用什么证据确认 `gh api` 读当前 attempt 的 jobs 列表在
  ledger job 的 token 权限下可行。**允许的证据只有两种**：①贴出该端点在本仓的真实调用
  与返回（可在本地用 `gh api repos/zlxlabs/gate/actions/runs/<某 run>/attempts/1/jobs --jq '.jobs|length'` 演示端点形状）；
  ②在代码里把该查询做成失败可降级并说明降级语义。**不得只写「推断可读」**。
  若你判断权限存在风险，在 report.md 用 `<!-- delegate-blocked: ... -->` 上行，不要自行
  改 caller 权限声明（那在业务仓，不在本卡边界内）。
- **lint / typecheck / build**：`python3 -m pytest tests -q` 全绿即可；本仓无独立 lint 入口。
  若仓内存在 `scripts/lint/`，按其现有入口跑一次并贴输出。
- **截图或探活**：不适用（真实 CI 上的行为验收由主脑在 PR 阶段完成，不在本卡范围）。
- **现场还原**：收工时 checkout 停在 `card/<worktree 名>` 分支，无未提交改动，无临时文件
  残留（红验注入必须全部还原），无全局配置改动；报告逐项确认。
- **提交纪律**（固定条款）：执行器必须在本卡分支上小步 commit，未提交的工作按未完成处理，
  不得把提交留给验收方。**本卡具体化**：至少分 4 次提交——①工件解析回落 + 其契约测试；
  ②归因闸 + 其契约测试；③`build_ledger` 身份校验放宽 + 单测；④账本条目来源 attempt 标注
  + 单测。每次提交前该部分测试必须绿。
- **进度存档**（固定条款，M 卡适用）：追加写
  `docs/sessions/gate101-ledger-rerun/progress/gate-101-progress.md`，每完成一个里程碑追加
  一段并与该里程碑的 commit 同一次提交；不回头改写历史段落。每段四项：`当前阶段`、
  `本段结论`（≤3 句）、`关键决策与已否决方案`（无则写「无」）、`下一步唯一动作`。
- **红验安全**（固定条款）：见上「红验有效性」，改坏前先 commit 真修复，还原只还原改坏处。
- **反熵条款**（固定条款）：禁止顺手新增抽象——新增接口/包装层/状态/配置项时，报告须写明
  第二个消费者是谁，或单消费者仍必要的理由；说不出即撤。禁止为通过测试顺手加
  fallback/兼容分支。
- **执行器自声明 outcome**（固定条款）：report.md 正文首个二级标题之前，恰好一行：

```
<!-- delegate-outcome: succeeded -->
```
或
```
<!-- delegate-outcome: failed -->
```

- **执行器在途 blocked 上行**：遇到卡面未交代清楚、无法自行决定的阻塞，在 report.md 正文
  首个二级标题之前写恰好一行（无阻塞写 0 行）：

```
<!-- delegate-blocked: 这里是阻塞问题原文 -->
```

## 背景指针（读，但不要改）

- 死锁现场：`.github/workflows/gate-v2.yml` `ledger` job 的
  `Resolve v2 ledger artifacts` 步骤，`select_artifact(..., exact_attempt=current)`。
- 收紧动作的出处：commit `67ba1f4`（"require current attempt for ledger terminal artifacts"），
  其提交信息说明了收紧理由，本卡即是对该理由的正解。
- 正确先例（照抄这个形状）：同文件 `gate` job 的 `resolve-audit-artifact` 步骤
  （`attempt <= current_attempt` 取 max + 输出 `source_attempt`），以及
  `build_ledger.py` 中 audit 的 `1 <= source_attempt <= current_attempt` 校验。
- 下游身份校验：`.github/actions/review-ledger/build_ledger.py` 的
  `_disposition_receipt_consumption_from_terminal`。
