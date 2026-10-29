# consumer runner.temp 落点 H0 独立审查

review-verdict: pass
delivery-blocking: no
failure-visibility: clean
risk-tier: personal

冻结 base：`45daae5b844c66b470a9d85f7f0483f1a176b1cc`
冻结 H0：`d0831eb698ec98a30fc9e6612be06c9719930b7e`
Dispatch-Id：`dlg-20261029-042438-27d338`

<!-- delegate-outcome: succeeded -->
## 结论

**通过，不阻断本 diff 交付；未发现本次新增的 P1/P2/P3 finding。**
consumer 只物化到 runner.temp，后续 caller 工作根和同名普通目录不变；pin、四文件和执行顺序保持。
这是发布前的独立审查结论，不是发布授权，也不证明 gate-hub#1917 已闭环。
本地全量测试不是全绿：历史 workflow 守卫 1 红、全局 Git hook 导致夹具初始化 12 错，归因及反证分别如下；没有用历史守卫掩盖其他错误。

## 冻结范围与输入隔离

工作树：本派发独立 review worktree，HEAD 初始精确等于冻结 H0。
规格：派发卡 `gate-261029-1917-consumer-runner-temp.md`；只读取规格，不读取其链接的顾问报告。
完整审查 `base..H0` 的全部 3 文件、65 增/14 删；未把新提交或分支移动纳入本轮。

- `.github/workflows/gate-v2.yml`
- `tests/test_gate_v2_contract.py`
- `tests/test_gate_v2_ledger_contract_source.py`

未读取实现报告、实现会话或其他 reviewer 结论；未修改 workflow、测试、source、scanner 或忽略规则。
新增产物仅本 verdict。熵增审查：实现未新增状态、抽象、配置项、依赖、取码路径或重试机制。

## 关键不变式：代码位置与锁定测试

下表路径均相对仓库根；测试名来自冻结 H0，而非本报告新造的测试。

| 不变式 | 实现位置 | 锁定测试/独立证据 |
| --- | --- | --- |
| export 只在 consumer run 子进程 | `gate-v2.yml:752-758`，先 export 再 bootstrap/helper；无 GITHUB_ENV 写入 | `test_quality_provisions_the_consumer_before_the_quality_entry`；真实 producer env/argv 与空 GITHUB_ENV 字节 |
| dest/env 均在 runner.temp | consumer 的相对 PATH 不变，quality env 在 `gate-v2.yml:765` | `test_consumer_step_materializes_the_published_files_at_workflow_sha`；独立实际 H0 checkout |
| 后续 caller 工作根/cwd 不变 | `Run scripts/gate-quality` 仍 `working-directory: github.workspace` | 同一 E2E 的新 caller 子进程；独立执行真实 quality run、接收 env/cwd、git status |
| caller 同名普通目录不删除、不豁免 | stale cleanup 仅 action/aggregator；pre-caller cleanup 无 consumer | `test_pre_caller_cleanup_never_removes_the_consumer_dir`、`test_quality_removes_gate_sources_before_any_caller_check`；实际预放目录字节保留 |
| repository/ref/sparse/顺序不变 | consumer env 仍 job.workflow_repository/job.workflow_sha、原四文件、质量入口前 | `_assert_contract`、11 项反向契约用例、实际 HEAD 和四文件字节比对 |
| source、scanner、其他 checkout 不动；凭据/退出码/artifact 语义不变 | 冻结 3 文件 diff；既有 bootstrap/bounded checkout/quality run 未改 | 346 项窄测含 source/退出码/artifact 契约；实际 consumer 无 token/extraheader、输出不含 token |

## 独立执行命令与结果

以下命令均从冻结工作树执行，使用 Python 3.12.3；临时环境目标不指向主仓 `.venv`。

```sh
git diff --check d0831eb698ec98a30fc9e6612be06c9719930b7e..HEAD
git diff --check 45daae5b844c66b470a9d85f7f0483f1a176b1cc..d0831eb698ec98a30fc9e6612be06c9719930b7e
python3 scripts/check_pinned_uses.py
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_ledger_contract_source.py tests/test_gate_v2_contract.py tests/test_gate_source.py
SHELLCHECK_OPTS=--severity=warning actionlint -color .github/workflows/*.yml templates/*.yml
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
```

前两条及 pin 检查退出 0；pin 检查覆盖 10 个 live workflow/action 文件。
窄测 **346 passed / 131.66s**；本机 actionlint 1.7.7 退出 0；CI 的钉定 1.7.12 也实际成功。
全量原环境：**1986 passed、1 failed、12 errors / 413.54s**，没有宣称本地全绿。

## 真实 producer 与跨子进程边界

独立探针：本次私有派发报告附带的 `review-evidence/probe.py`；完整本机路径仅保留于私有报告。
它从 `git show H0:.github/workflows/gate-v2.yml` 取真实 step/run/env，使用本地真实 Git upstream/mirror 与仓库既有 host-client 协议夹具；不访问网络、不冒充部署后验证。
比仓库 parentless snapshot 用例更强：upstream 保留真实 H0 历史，最终实际 `git rev-parse HEAD` **等于 H0 本身**，不是替身 snapshot SHA。
预放 caller 的已跟踪同名普通目录及二进制哨兵、runner.temp 的旧 consumer 残留；实际执行 stale cleanup、termination helper 安装、consumer checkout、quality run。
quality 使用 caller 的可执行测试入口接收环境，因此保留真实 workflow producer 和 shell/helper 边界，但不冒充 gate-hub 的实际业务 quality。
记录实际 `bash --noprofile --norc -e -o pipefail -c <step.run>` argv、cwd、白名单 env；host-client 的 repository/commit/deadline argv 和 Git argv 也逐条落盘。

正向结果：consumer 旧残留被重建；caller 字节与干净 git status 保持；同名目录无嵌套 `.git`；GITHUB_ENV 仍为 0 字节。
真实 quality run 收到 caller 的 GITHUB_WORKSPACE/cwd，以及 runner.temp 的绝对 consumer/artifact 路径。
service 模式 2 次 host-client 调用均收到 runner.temp 工作根和 H0；共 20 条实际 argv 记录；无 curl/网络 fetch。
consumer 含 `.git` 在内无测试 token 或其编码凭据，local config 无认证 extraheader，标准输出/错误不含 token。
额外 origin 模式复用已引导 helper、以本地 origin URL 验证同一真实 step，也得到实际 H0/四文件/caller 不变；未改生产 source 入口。

| 实际物化文件 | SHA-256（与冻结 H0 blob 字节一致） |
| --- | --- |
| `.github/workflows/gate-v2.yml` | `96ed996b8657719f8bc3c2f2068fbe4b40f34dd13bf661473d622f5ce14ba4d0` |
| `.github/actions/review-ledger/action.yml` | `6827677c84a549ed629cde348429025c91815015e414a746638d1375931c2e69` |
| `.github/actions/review-ledger/build_ledger.py` | `e7be9461e1033420923a41c01469f5e8ebe05f41348b6f90409308d90fe96851` |
| `scripts/scrub_outbound.py` | `d9bdbea321fd54e51e940c3ec525ccf8c91d30f74250b49a8d2451c64198d438` |

## 独立变异：先确认注入，再观察转红

全部注入只发生在隔离 fixture 的 workflow 副本；保存原始/变异 YAML，断言步骤结构确实改变，未改审查树。
正向先绿；以下 4 个独立真实执行探针全部退出 1，不是把 pytest 的“预期失败”计作行为证据。

| 注入 | 实际触发及拒绝证据 |
| --- | --- |
| 去掉 export | checkout 写入 caller、删除 caller 哨兵并产生嵌套 `.git`，runner 残留仍在；真实 quality 因 runner consumer 未物化退出 1 |
| env 改回 caller | runner checkout 成功，但 caller 同名目录是普通目录；真实 quality 拒绝未物化 consumer，退出 1 |
| ref 改 caller SHA | host-client 实际收到 caller SHA；bootstrap 报 `SOURCE-CLIENT-FAILED` / `SOURCE-PREPARE-FAILED`，质量入口前失败 |
| checkout 移到质量入口后 | 实际按变异步骤列表执行；quality 在 checkout 之前发现 consumer 无 `.git`，退出 1 |

仓库既有 11 项契约变异也全部通过“必须拒绝”断言，覆盖 main/caller/registry ref、仓库、路径、漏 sparse 文件、丢 env 和顺序。

## 环境核对、历史守卫与红项分账

真实消费上下文已核对：本次 systemd 执行器 unit 为 active/running；同 unit 正向探针通过；`env -i` 裸 shell 的 service/origin 正向探针也通过。
CI：独立读取 run/job `conclusion`，不是 aggregator reason；run **40815815969** 的 `test` 与 `actionlint` 均 success，两个作业均非 skipped。
PR #388 仍 draft；该绿只证明自测/语法检查，不证明完整主审门禁或 ready/发布状态。
CI 测试日志明确 **1999 passed / 329.22s**，`GATE_CONTRACT_GUARD_REQUIRED=1`；实际测试合并提交 `d92fb09cb073f3cb8d5051aa116e5c2fa167270c`。
通过 GitHub commit API 核对其 tree 与 H0 相同：`309716aedc337bbecc4d502b6053bb12b88c9db6`，不是拿相邻提交的绿充数。

- **继承红**：派发时无主干基线，原口径未能判定；本次补查 main=base 的 CI run **40805724573**，test/actionlint 均 success，可比 CI 作业未观察到继承红。
- **本地继承环境红**：12 errors 均发生在未改的 `test_neighbor_evidence.py` fixture checkout，真实复现 stderr 为全局 `post-checkout: detached HEAD checkout`，不是 consumer 行为失败。
- 在 scratch-worktree 的 **base** 上运行同一失败用例也得到同一 fixture error；仅对子测试进程设置 `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null` 后该模块 **15 passed / 1.30s**。未改全局配置、未对正式 commit/push 绕 hook。
- 曾尝试 `GIT_CONFIG_GLOBAL=/dev/null`：消除了 hook，却同时屏蔽 fixture 的 HOME Git URL 重写，产生 6 个 fetch 失败；已归因并保留该日志，不把它算新实现缺陷。
- **历史守卫红**：`test_gate_v2_yml_untouched` 仅要求相对 origin/main 的 workflow diff 为空；base 对比为空、H0 非空，单跑稳定失败。它拒绝任何 workflow 修改，不判断 runner.temp 行为；属旧测试的任务边界守卫，不是本实现行为回归，本卡不扩 scope 删除它。
- **本 diff 新红**：未发现；上述各类红分开归因，未豁免真实 CI 或其他新增缺陷。

## Findings、自动初筛与证据保存

本 diff 无 finding，故没有需要逐项列“触发/后果/修复代价/personal P1 两问”的修复建议；严重度与交付阻断均独立裁决为无。
OCR 初筛实际调用了 minimax/deepseek，各 150 秒超时，完整 envelope 为 `status=skipped`、`reason=primary=leg_timeout; backup:deepseek=leg_timeout`；**不算自动扫描 clean**，未用空 findings 代替独立全量审查。
完整证据目录：本次私有派发报告附带的 `review-evidence/`；通过文首 Dispatch-Id 关联，公开 verdict 不发布本机路径。
其中保留 `probe.py`、真实 host-client fixture、冻结/变异 workflow、实际 subprocess env/argv、四文件最终字节、正向 summary、变异观测、CI run/job/tree JSON、全量/窄测及环境归因日志。

## 发布前与发布后边界

本次 `git ls-remote origin refs/heads/main refs/tags/v2` 实测两者仍为 base；没有推实现、合并、改 tag、开 PR 或评论 issue。
本 verdict 不证明 #1917 闭环；发布后必须确认远端 v2 包含修复，再重跑 gate-hub#2002 的真实 required quality，覆盖 secret scan 与真实 local-agent-review 洁净检查。
本卡未执行、也未声称取得上述发布后证据。

## 过程记录

### 踩到的坑
本地全量受旧守卫与全局 Git hook 两类独立因素影响，逐一实测归因，未只用旧守卫解释全部失败。

### 闸与绕过
无正式门禁绕过；pending 旧工具被 stale-tool 闸拒绝后使用其指定运行时入口，查询部分超时；本仓 #388 已有负责人。两处只读调用失误（cwd、shell 提取器运行时）已纠正。首次 push 被公开内容扫描拒绝本机路径，先确认远端分支不存在，再删除公开副本中的私有路径并 amend 未推提交；未绕扫描。

### 与卡面的偏差
无实现或发布范围偏差；OCR 超时已显式标 skipped；所有独立探针和暂存证据在仓外，仓内仅新增 verdict。

### 最贵的一步
本地全量测试 413.54 秒，其次 OCR 两腿合计约 300 秒；窄测 131.66 秒。

### 新增测试判据
无（本卡不新增仓库测试；独立真实 step/变异探针及边界产物已随报告保留）。

## pickup 巡检

open issue 总数：6
存活探针不可用
memory 候审 4 条（死指针 0 / 超龄 0 / 探针失败 4 / 探针出错 0），报告 `memory-doctor/latest.json` @ 2026-10-26T01:01:52Z（完整原文含本机路径，仅存私有派发报告）
