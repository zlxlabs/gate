# 任务卡：gate-v2 review ledger 生产者改「只写本次、零历史」——让 `Build v2` 步骤耗时不再随历史增长

## 目标

`gate-v2.yml` 的 `ledger` job（`Build v2 review effectiveness ledger`）现在是一个读—改—写全量回路：下载最近 3 份 `codex-review-ledger-v2` artifact（每份都是全仓历史，
实测 1.03 MB / 1286 条）、追加本次、整份重传。历史只用来算 `comparison` / `review_round`，而这两个字段在 CI 关键路径上**没有消费者**
（`ledger` 已 `continue-on-error`、不参与放行；读侧 gate-hub 两个脚本是人工手跑）。zlxlabs/gate#167 已定根治方向：**producer 改成只写不读**，
上传一份只含本次 run 的小 JSON，零历史下载，耗时 O(1)；`comparison`/`review_round` 等历史派生值移到分析侧（agent-config#2222 的采集层）离线算。

本卡就是那一刀。交付判据：`build_ledger.py` 不再有任何出网调用；`Build v2` 步骤墙钟只与本次 run 的输入体积相关。

- **Evidence-Commands**：
  ```sh
  # 无网络证明：脚本里不再 import urllib / 不再有 fetch_prior_entries；测试在断网 socket 下跑通
  grep -nE 'urllib|urlopen|fetch_prior_entries|archive_download_url|LEDGER_ARTIFACT_NAMES|STATE_MARKER' .github/actions/review-ledger/build_ledger.py ; echo "exit=$?"   # 应无命中、exit=1
  uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
  python3 scripts/check_pinned_uses.py
  ```

## 非目标

- **不做 `v2` 标签晋升**：本卡只合并到 main；标签前移走仓内既有晋升流程（canary 证据）。**晋升前置条件**（写进设计文档，由主脑把关）：
  agent-config 侧 `scripts/consult/consult_trigger.py` 改为从采集层读多轮 finding（它现在依赖 artifact 内的全量历史与 `review_round`，见 agent-config `consult_trigger.py:173-196`）。
  晋升早于这一步会让 consult 链路报 `DataUnavailable`。
- 不写收集器（跨仓聚合归 agent-config 采集层，gate 侧**不新增任何出网调用、不新增凭据、不新增内网依赖**——#167 硬约束）。
- 不改 `.github/workflows/gate.yml`（legacy，冻结）；不改 `gate` / `primary` / `quality` job 的 artifact 生产逻辑；不改 `templates/caller-gate-v2.yml`（调用方 input 集不变）。
- 不改 `review` 子结构（`_review_summary` 的字段集）——它不依赖历史，读侧契约保持。

## 基线与所有权

- **Task-Id**：
- **Fixes-Issue**：167
- **Verify-Mode**：ci-standard-v1
- **Verify-Command**：uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
- **Diff-Lines-Target**：1600
- **Diff-Lines-Hard**：2400
- **阶段**：implementing
- **锁定决策**：
  1. **artifact 名与文件名不变**：仍是 `codex-review-ledger-v2` / `ledger.jsonl`（`gate-v2.yml:1625-1631`）。变的是**内容**：恰好一行，只含本次 `(run_id, run_attempt)` 的条目。
     理由：读侧按 run id 取 artifact 时名字固定最省事（#167 已实测「按 PR 分名」对读侧不可行）；`tests/test_gate_v2_contract.py:1012-1013` 钉死的名字与路径保持。
  2. **条目 schema 升 `schema_version: 2`**，字段规则：保留一切**只由本次 run 输入**可得的字段（`recorded_at`、`repository`、`pr_number`、`run_id`、`run_attempt`、`head_sha`、`preflight`、`install`、`primary_identity`、`review`、
     `finding_dispositions`、`false_positive_count`、`disposition_receipt_consumption`、`terminal_source_attempt`、`disposition_status`）；**删除**一切依赖历史的字段：`review_round`、`comparison`、`history_status`、`ledger_conflict`、
     `convergence_projection`（若它读 prior entries；若纯本次可得则保留并在核销表写明）。执行器必须在 `docs/design/review-ledger-write-only.md` 给出**旧字段逐条核销表**（每个 v1 字段：保留 / 删除 + 理由 / 迁到分析侧），
     这是 `templates`「重构核销」纪律的要求；核销表与代码同一次提交。
  3. **删除全部历史来源**：`fetch_prior_entries`、`LEDGER_ARTIFACT_NAMES`、`HISTORY_SOURCES` 与 `_merge_history_status`、网络预算常量（`NETWORK_BUDGET_SECONDS`、`_request_fits_deadline`）、自建 `urllib` opener、
     sticky comment 游标的读与写（`STATE_MARKER`/`STATE_RE`/`parse_state_entries`/`render_state_comment`/`post_state_comment`）、`dedupe_entries` 的跨条目冲突逻辑、`--max-entries`；`write_ledger` 改为写单条。
     `action.yml` 去掉只为历史/评论服务的 input（如 `token`）——`gate-v2.yml` 是它唯一调用方，同 PR 同步改；`workflow_call` 的对外 input 集**零变化**。
  4. **零出网由测试锁死**：新增测试在 `socket.socket` 被 monkeypatch 为抛 `RuntimeError` 的环境下跑完整 `main()`（用现有 fixture 的 preflight/install/audit/terminal 输入）且产出恰好一行；
     另一条 AST 测试断言 `build_ledger.py` 的 import 集合不含 `urllib`、`http`、`socket`、`ssl`。
  5. **workflow 侧**：`ledger` job 保留 `always()` + `continue-on-error: true` + concurrency 不变；`Build` step 的 `timeout-minutes` 从 3 降到 1、job 从 5 降到 3（CPU 实测约 2 s）；`env` 里删掉不再需要的 token 传递；
     `Resolve v2 ledger artifacts` / 三个 download step 不动（它们是本次 run 的输入）。
  6. **测试改造原则**：`tests/test_review_ledger.py` 中历史/游标/comparison 相关用例**删除而非跳过**（列在 Explore 盘点：`:807,818,847,931,939,956,988,1027,1051,1101,1183,1234,1272,1284,1298` 与 `:1350,1412,1442,1487,1505,1524,1544,1752,1803,1826`、`:94,112` 附近，
     执行器以实际内容为准）；`tests/test_gate_v2_contract.py` 里 ledger 契约按新 workflow 文本更新。删除的每条用例在核销表里对应到「删除的功能」。
  7. 归档：把 `/home/zlx/.local/state/delegate/cards/260914-adlc-traces/2b-gate-ledger-write-only.md` 原样复制到 `docs/sessions/260915-ledger-write-only/cards/2b-gate-ledger-write-only.md`，作为首个 commit。
- **任务类型**：backend-logic
- **复杂度**：M
- **Base commit**：40faea1719472144e0d27c30511f56303d41a36f
- **Depends-On**：
- **Branch**：由 delegate 分配（card/<worktree 名>），执行器不得另建分支
- **Worktree**：是，由 delegate 创建独立 git worktree
- **当前唯一写入者**：本卡执行器
- **执行器与模型**：由 delegate 按 `--class big` 选池回填
- **执行器角色声明**（codex / grok 卡必带，原样抄）：本会话就是执行器（implementer 角色），全局 AGENTS.md「模型编排」段的主代理委派纪律**不适用于本卡**；不限制亲自落盘还是委派子代理，唯一硬约束是最终产物落在指定路径——子代理不返回就直接自己写完。
- **计划者与审查者**：主脑（Claude Fable）拆卡与验收

## 修改边界

- **允许**：`.github/actions/review-ledger/build_ledger.py`、`.github/actions/review-ledger/action.yml`、`.github/workflows/gate-v2.yml`（仅 `ledger` job 段，行 1345–1631 附近）、
  `tests/test_review_ledger.py`、`tests/test_gate_v2_contract.py`、`tests/fixtures/**`（仅新增本卡 fixture）、`docs/design/review-ledger-write-only.md`（新）、
  `docs/sessions/260915-ledger-write-only/cards/2b-gate-ledger-write-only.md`、`docs/sessions/260915-ledger-write-only/progress/ledger-write-only-progress.md`。
- **禁止**：`.github/workflows/gate.yml`、`.github/workflows/ci.yml`、`.github/workflows/gate-v2.yml` 的其他 job、`.github/actions/gate-aggregator/**`、`.github/actions/gate-disposition/**`、`.github/actions/pr-size-preflight/**`、
  `templates/**`、`scripts/**`、`retro/**`、`AGENTS.md`、`CONTRIBUTING.md`。
- **Scope-Globs**：.github/actions/review-ledger/build_ledger.py .github/actions/review-ledger/action.yml .github/workflows/gate-v2.yml tests/test_review_ledger.py tests/test_gate_v2_contract.py tests/fixtures/* tests/fixtures/*/* tests/fixtures/*/*/* docs/design/review-ledger-write-only.md docs/sessions/260915-ledger-write-only/cards/2b-gate-ledger-write-only.md docs/sessions/260915-ledger-write-only/progress/ledger-write-only-progress.md
- **高风险区域**：
  - 本仓 `risk-tier: personal`：P1 只有数据丢失、静默出错、崩溃。本卡的「数据丢失」形态是：新条目缺了读侧需要的本次字段（`head_sha`、`run_id`、`run_attempt`、`pr_number`、`review.finding_ids`/`review.result`），
    核销表里「保留」的字段每个都要有测试断言存在于输出行。
  - 改 workflow 的 PR **必须 merge commit**（`AGENTS.md:19-22`，squash/rebase 会重写 SHA 打断下游 pin）——主脑合并时负责；执行器只在报告里提醒。
  - `check_pinned_uses.py` 与 actionlint（`ci.yml` 的 actionlint job）都要过；不新增 `uses:`。
  - 读侧在途影响：`v2` 标签未前移前，舰队跑的还是旧脚本，本卡合并本身不改变线上行为；这是设计上的安全窗口，不要「顺手」动标签。

## 约束与假设

- **约束**（违反即拒收）：
  - 零出网：锁定决策 4 的两条测试存在且绿 —— 检查：`tests/test_review_ledger.py`（新用例名含 `no_network`）。
  - 单行输出：对 fixture 输入运行 `main()` 后 `ledger.jsonl` 恰好 1 行、`schema_version == 2`、无 `review_round`/`comparison`/`history_status` 键 —— 检查：同上。
  - 保留字段齐全：核销表标「保留」的每个字段在输出行存在且类型与 v1 一致 —— 检查：同上（参数化遍历核销表里的保留清单，清单以代码常量给出并被文档引用）。
  - workflow 契约：`tests/test_gate_v2_contract.py` 断言 artifact 名 `codex-review-ledger-v2`、path `ledger.jsonl`、`continue-on-error: true`、step `timeout-minutes: 1` —— 检查：该文件。
  - `grep -nE 'urllib|urlopen|fetch_prior_entries|archive_download_url|LEDGER_ARTIFACT_NAMES|STATE_MARKER' .github/actions/review-ledger/build_ledger.py` 零命中。
  - 全量 `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q` 绿；`python3 scripts/check_pinned_uses.py` 退出 0；`actionlint -color .github/workflows/*.yml templates/*.yml`（`SHELLCHECK_OPTS=--severity=warning`）退出 0。
- **假设**（执行器可自行调整，调整须在 report.md 写明理由）：
  - `convergence_projection` 是否依赖历史，以代码为准决定去留。
  - `disposition_status` 若只由本次 receipt 得出则保留。
  - `Build` step timeout 1 分钟若 fixture 实测不够，可设 2，写明实测值。

## 完成条件

- **产物入库**：本卡产生的全部落盘产物均提交到 delegate 分配的 `card/<worktree 名>` 分支，验收以该分支上的提交为准；报告中贴出 `git log --oneline -1`、`git show --stat --format= HEAD` 与 `git status --short --untracked-files=all` 的实际输出。**每完成一个能独立通过测试的单元就 commit 一次，不要攒到最后一起提交**。若 pre-commit 守卫拦下提交，处置权归主脑：执行器把守卫的完整报错原样贴进报告并就此停下，保留现场。
  具体节奏：至少分 4 次提交——① 归档本卡；② 设计文档 + 核销表；③ `build_ledger.py`/`action.yml` 改只写 + 测试删改 + 零出网测试；④ `gate-v2.yml` ledger job + 契约测试 + progress。
- **行为验收**：
  1. 贴出 Evidence-Commands 三条的原始输出。
  2. 贴出 `wc -l .github/actions/review-ledger/build_ledger.py`（改前 1155 行，改后应明显减少）与 `git diff --stat <base>..HEAD`。
  3. 贴出 `git merge-base --is-ancestor origin/main HEAD; echo $?`（应为 0）。
- **相关测试**：过程中可窄范围 `python -m pytest tests/test_review_ledger.py -q`；收尾必须全量（Verify-Command）+ pin 检查 + actionlint；本仓 CI（`ci.yml`）在 PR 上跑同款。
- **lint / typecheck / build**：actionlint、`check_pinned_uses.py`。
- **交付文档**：`docs/design/review-ledger-write-only.md`（为什么只写不读、字段核销表、读侧迁移说明：多轮/comparison 由 agent-config 采集层离线算、晋升前置条件）。
- **现场还原**：收工时 checkout 停在 `card/<worktree 名>`。
- **进度存档**（固定条款，原样保留）——路径 `docs/sessions/260915-ledger-write-only/progress/ledger-write-only-progress.md`，追加式，每段四项：`当前阶段`、`本段结论`、`关键决策与已否决方案`、`下一步唯一动作`，与对应 commit 同一次提交。
- **红验安全**（固定条款，原样保留）：改坏前先 commit 同文件里已验证的真修复；还原只许还原刚改坏的那一处，禁止整文件 `git checkout -- <file>`。
- **红验有效性**（固定条款，原样保留）：反向验证的转红输出必须原文贴进报告，红的类型必须是断言失败；注入优先只改判据本身那一行。本卡至少两条红验：① 在 `main()` 里临时加回一次 `urllib.request.urlopen` 调用 → 零出网测试转红；② 输出里临时删掉 `head_sha` → 保留字段测试转红。
- **反熵条款**（固定条款，原样保留）：本卡是做减法的卡，禁止新增抽象、开关或兼容分支（如「`--with-history` 兼容模式」）；说不出第二个消费者的东西一律不加。
- **执行器自声明 outcome**（固定条款，原样保留）：report.md 正文首个二级标题之前恰好一行 `<!-- delegate-outcome: succeeded -->` 或 `<!-- delegate-outcome: failed -->`。
- **执行器在途 blocked 上行**：遇到卡面未交代清楚、无法自行决定的阻塞问题时，在 report.md 正文首个二级标题之前写恰好一行 `<!-- delegate-blocked: 这里是阻塞问题原文 -->`（无阻塞时写 0 行）。

## 当前状态

- **现场事实（主脑预取）**：
  - `gate-v2.yml:1345-1354` ledger job：`needs [quality, primary, gate, classify_pr_paths]`、`always()`、`continue-on-error: true`、job `timeout-minutes: 5`；build step `:1608-1623` `timeout-minutes: 3`；上传 `:1625-1631` 固定名 `codex-review-ledger-v2`、`retention-days: 30`。
  - `build_ledger.py`（1155 行）：`main()` `:1037`；历史抓取 `fetch_prior_entries` `:863-933`（REST `actions/artifacts?name=…&per_page=3` + zip 自解压）；`comparison` `:690-717`；`review_round` `:742`（= 历史里去重 `(run_id, run_attempt)` 数 + 1）；
    `HISTORY_SOURCES` `:72-76`；网络预算 `:55-71`；sticky comment `:87-88,165-180,191-225,949-981`（`main()` 调用 `:1101-1109,1140-1147`）；`write_ledger` `:777-784`；`dedupe_entries` `:787-809`；行字典字面量 `:734-761`。
  - 仓内「只写本次 + 下游按 artifact id 收」已是主流范式：gate terminal envelope（`gate-v2.yml:1219-1281`）、primary audit（`:670-686`）、convergence receipt（`:1251-1258`）、ledger input（`:408-435`）。
  - #167 实测：build 步骤 251 s，其中 CPU 约 2 s，其余是 Azure artifact blob 下行；止血批次 PR #168 已合并（artifact_limit 10→3、120 s 网络预算、continue-on-error），根治后其中大半失去意义——删掉即可，不必保留。
  - 跨仓读侧：agent-config `scripts/consult/consult_trigger.py:250-323` 下载同名 artifact 并按 `review_round` 做多轮 finding 对比（`:173-196`）；gate-hub `scripts/review/ledger_reader.py`、`review-ledger-report.py`、`review-ledger-replay.py` 人工手跑。它们都在标签晋升后才受影响，且都不在本仓。
  - 本仓 `risk-tier: personal`；无 Makefile；CI 全量 = `python -m pytest -q`（`ci.yml:20`）+ `check_pinned_uses.py`（`:23`）+ actionlint。
- **机理/根因陈述**：#167 评论已实测：慢在 artifact blob 下行（30–88 KB/s），且每份 artifact 装全量历史 → 读侧 O(t)、写侧重传全量 → 累计 O(t²)；历史唯一用途 `comparison` 在 CI 里无消费者。架构错位，不是性能问题。
- **已完成**：无
- **未完成**：全部
- **关键决策**：见锁定决策；名字不变、内容单条、schema_version 2、零出网由测试锁死、不做晋升。
- **已否决方案**：artifact 按 PR / run 分名（读侧无前缀查询，#167 实测推翻）；用一次 `runs?branch=` 列表调用保留 `review_round`（仍是关键路径出网依赖，违反 #167 硬约束；多轮由分析侧按 run 时间顺序离线算）；保留 sticky comment（它只为 producer 自读服务，且是 bot 评论通知洪水来源之一）；`--with-history` 兼容开关（反熵）。
- **修改文件**：见修改边界
- **测试及结果**：无
- **已知问题**：无
- **下一步唯一动作**：先归档本卡并提交，再写设计文档与字段核销表
