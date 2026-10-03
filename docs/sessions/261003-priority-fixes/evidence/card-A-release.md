# 卡 A 发布证据：gate#278 授权发布后的 canary、v2 推广与真实处置回执

- dispatch：`dlg-20261003-163638-86f789`
- 仓库：`zlxlabs/gate`
- 验收时点：2026-10-03 16:44Z – 17:05Z（UTC）
- 结论一句话：**源码合并、正式 main CI、canary、v2 标签发布四段全绿；真实处置回执一段
  因当前不存在可签的有效 finding 而 blocked（未伪造、未代签）。**

## 0. 身份与常量

| 项 | 值 |
| --- | --- |
| issue | [#278](https://github.com/zlxlabs/gate/issues/278) —— 验收结束时仍为 `OPEN` |
| 修复提交（fix） | `b951586217bc26705b07be77d2d7c3f1ef13209a` |
| 合并提交（main tip） | `98a3ed241294a0671ac95bee7e0b987147e7adf4`（PR [#279](https://github.com/zlxlabs/gate/pull/279)，merge commit） |
| 派发时刻的远端 v2 | `f8c16beaeddfe8acc811a6664658e5d457da47dc` |
| 验收结束的远端 v2 | `98a3ed241294a0671ac95bee7e0b987147e7adf4` |

## 1. 源码合并

已由主脑完成，本卡不重做。`git log` 在 worktree `card/disposition-release-261004` 上可见
`98a3ed2 Merge pull request #279 from zlxlabs/feat/disposition-path-261003`，其父链包含
fix 提交 `b951586`。

## 2. 正式 main CI

| 项 | 值 |
| --- | --- |
| workflow | `ci`（`.github/workflows/ci.yml`） |
| run | [37137317244](https://github.com/zlxlabs/gate/actions/runs/37137317244) |
| 触发 | `push`，headSha `98a3ed2…` |
| 结论 | `success` |

工作流本地复跑命令（与 CI 一致）：
`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`。
本卡未改任何测试或源码（见第 6 节），故未重跑全仓 pytest。

## 3. Canary（v2 推广的真实证据来源）

证据读取方是 `scripts/v2_tag_promotion_evidence.py`：它不看 gate 仓自己的 run，而是读
`zlxlabs/ci-infra-canary` 的 `gate.yml`（id `330565146`），要求 run `conclusion == success`、
`referenced_workflows` 里 `zlxlabs/gate/.github/workflows/gate-v2.yml@<sha>` 的 sha 全部一致、
且该 run 的 `primary` 作业 `conclusion == success`（`skipped` 不算——draft / fork / hosted
runner 会让整个 job skip，规则显式拒绝）。

| 项 | 值 |
| --- | --- |
| canary run | [37137964734](https://github.com/zlxlabs/ci-infra-canary/actions/runs/37137964734) |
| 事件 | `pull_request`（canary 仓自己的 PR，headSha `10a90e68…`，标题 `[self-probe] Real Gate canary`） |
| referenced workflow | `zlxlabs/gate/.github/workflows/gate-v2.yml@main` → 解析到 `98a3ed241294a0671ac95bee7e0b987147e7adf4` |
| run 结论 | `success`（attempt 1） |
| `gate / primary` | `SUCCESS` |

**没有新 dispatch canary。** 本卡开始时（16:45Z）该 run 已存在且 `in_progress`，其
`referenced_workflows[].sha` 恰好等于本卡的固定 main SHA，因此符合「仅在没有覆盖固定
main SHA 的有效运行时才触发一次」的例外条件，dispatch 预算 1 次未消耗。等待用
`ci-watch.sh`（agent-config `scripts/ci/ci-watch.sh`），未手搓轮询：

```
CI-WATCH-RESULT: sha=10a90e68… workflow=gate run=37137964734 conclusion=success status=SUCCESS run_attempt=1
```

对照：上一条 canary 成功 run 37126117667 引用的 gate sha 是 `f8c16be…`（旧的 v2），
不是本次修复，所以**不能**拿它当本次修复已验。

## 4. v2 标签推广（发布完成）

派发时刻已知的 run 37137317200（`push`，`success`）中 `Move` 步骤被 skip —— 那是
「流水线绿但没有可推广候选」，不等于已发布。真正的发布发生在 canary 绿之后由既有
`repository_dispatch: canary-verified` 入口自动触发的 run：

| 项 | 值 |
| --- | --- |
| workflow | `v2 tag sync` |
| run | [37138246009](https://github.com/zlxlabs/gate/actions/runs/37138246009) |
| 触发 | `repository_dispatch` / `canary-verified`，headSha `98a3ed2…` |
| `Select newest canary-verified main commit` | `selected canary-verified main commit: 98a3ed241294a0671ac95bee7e0b987147e7adf4` |
| `Move v2 to selected…` | 真实执行（`TARGET_SHA=98a3ed2…`），非 skipped |
| `Report v2 tag sync state` | `V2-TAG-SYNC-STATE: promoted` |
| run 结论 | `success` |

远端真实状态（不是本地 tag 副本）：

```
$ git ls-remote --tags origin v2
98a3ed241294a0671ac95bee7e0b987147e7adf4	refs/tags/v2

$ git fetch --force origin refs/tags/v2:refs/tags/v2 && \
  git rev-parse 'refs/tags/v2^{commit}' && \
  git merge-base --is-ancestor b951586217bc26705b07be77d2d7c3f1ef13209a 'refs/tags/v2^{commit}'
98a3ed241294a0671ac95bee7e0b987147e7adf4
（exit 0 → fix 提交是 v2 的祖先）
```

`ls-remote` rc=0 且 stdout 非空；「查询报错 / 查询为空 / 值不同」三态在本卡里是分开的
（报错→非零退出，为空→stdout 空，均未被当作成功）。本卡没有手工移动 tag、没有新增门禁、
没有改 caller pin（`@v2` 是移动标签，调用方无需推广动作）。

## 5. 真实处置回执（blocked）

### 5.1 入口与调用方现状

- 签发入口：`agent-config` 仓 `scripts/git/gate_disposition.py`（`issue` 子命令 →
  `gh workflow run gate-disposition.yml --repo <业务仓>`，可选 `--wait`，终态打印
  `DISPOSITION_RESULT`）。
- 业务调用方 `zlxlabs/agent-config` 的 `.github/workflows/gate-disposition.yml` 已有
  **9 个输入**（`pr_number` / `primary_run_id` / `primary_run_attempt` / `finding_id` /
  `reason` / `disposition` / `counterevidence_json` / `tracking_issue`），并以
  `uses: zlxlabs/gate/.github/workflows/gate-v2-disposition.yml@v2` 调用。
  即：本卡担心的「调用方仍停在 5 输入」阻塞**不存在**；`@v2` 现在解析到的正是含本卡
  修复的 `98a3ed2…`。

### 5.2 原始出处 PR 3899 已失效，不复用

| 项 | 值 |
| --- | --- |
| PR | [zlxlabs/agent-config#3899](https://github.com/zlxlabs/agent-config/pull/3899) `docs: record B0 host-PI login progress through smoke probes` |
| head | `f04739f9924652ceedd2492c221c3a72aabb0e80` |
| 状态 | **`MERGED`**，mergedAt `2026-10-03T16:14:16Z` |
| 其 gate run | [37129898935](https://github.com/zlxlabs/agent-config/actions/runs/37129898935)，`gate / primary` = `failure` |

该 head 上确实还挂着一个 primary 失败，但 PR 已合并：finding 身份（PR / head /
primary run / attempt）已不再代表任何待处置对象。按本卡锁定决策，**不复用旧身份**，
不对它签回执。

### 5.3 全仓扫描：当前没有任何可签的有效 finding

`gate_disposition.py` 签发的前置条件（`cmd_issue` → `primary_run`）：目标 PR 的**最新**
`gate / primary` check-run 必须 `conclusion == failure`；`primary_finding_severity` 还要求
该 finding 在 primary 作业日志里出现且 severity ∈ `{major, blocker}`。

扫描结果（`gate.yml` 最近 40 条 run × 全量 open PR 的 head 对齐）：

| 仓 | 扫描结论 |
| --- | --- |
| `zlxlabs/agent-config` | 5 条失败 gate run（head `f04739f9` / `379b23a5` / `0ddda1e6` / `abb35d27` / `d2dbb0d3`）**全部**属于已合并/已关闭 PR；18 个 open PR 无一命中 |
| `zlxlabs/gate-hub` | 失败 head **全部**不属于任何 open PR；4 个 open PR 全是 draft |
| `zlxlabs/ci-templates` | 无 `gate.yml` run |

agent-config 三个**非 draft** 的 open PR 的实际取值：`#3915`（head `a5a1ad98…`）
`gate / primary` = `success`；`#3893`（`efac62df…`）与 `#3621`（`6779769d…`）=
`skipped`。`success` 与 `skipped` 都不是 `failure`；对这三条身份跑 `issue` 会在任何 gh
写请求之前被 `primary_run()` 拒掉（exit 2，`gate / primary conclusion is 'success'/
'skipped', expected failure — there is no failure to dispose`），**不会**产生回执。

### 5.4 判定

- 没有可客观证伪（refuted）的有效 finding：连 finding 都不存在。
- 没有确属 out-of-scope 的有效 finding：同上。
- 需要人类裁决的 security / data / deployment 风险不代签；本卡目标「#278 已授权发布」
  也**不是**风险裁决授权。
- 造 finding、或对已合并 PR 的旧身份滥签回执，属锁定决策明令禁止的「胡造 finding /
  滥签回执」。

→ **处置回执一段判 blocked**，disposition workflow dispatch 预算 1 次未消耗。
`gate_disposition.py` 未被以任何会写的方式调用（本卡只读源码）。

**这不是说处置链坏了。** 已取得的正面证据是：`@v2` 已含修复、调用方输入 schema 与
reusable workflow 对齐；但「checkout producer 进工作区子目录 → 签发 → 出回执」这条
真实链路本卡**没有**跑出一次成功回执，不能据此宣称全链恢复。

## 6. 本卡改动范围

只有两份文档（`evidence/card-A-release.md`、`progress/card-A-release-progress.md`），
无源码、无测试、无 workflow 改动，因此：

- 不跑全仓 pytest（无测试代码变更）；
- 只跑本卡的 Verify 命令 `git diff --check`（文档 diff 洁净性）。

## 7. 给主脑的关单判据

1. `git ls-remote --tags origin v2` → `98a3ed241294a0671ac95bee7e0b987147e7adf4`（非空）；
2. `git fetch` 精确 ref 后 `git merge-base --is-ancestor b9515862… refs/tags/v2` → exit 0；
3. tag-sync run 37138246009 的 `Report v2 tag sync state` 输出 `V2-TAG-SYNC-STATE: promoted`
   （不是 `no_eligible_candidate` / `already_current`）。

三条同时成立 → **#278 的「发布」部分已满足**，可以关闭（源码侧已合并 + main CI 绿 +
canary 实测绿 + v2 已含 fix SHA）。第 5 节的处置回执 blocked 与 #278 的发布闭环无关，
不应、也没有被写成「已解决」。

本卡没有关闭 issue、没有改任何标签、没有开 PR、没有自行 merge。

## 8. 预算与调用计数

- 只读 GH API 约 20 次（上限 24）；ci-watch 的轮询调用另计。
- workflow dispatch **0 次**（canary 覆盖运行已在跑未重复派发；处置因 blocked 未派发）。
- tag-sync workflow_dispatch **0 次**（发布由既有 `repository_dispatch: canary-verified`
  自动入口完成）。
- 未 SSH、未整段回显生产日志（只 grep `V2-TAG-SYNC-STATE` / `selected canary-verified`
  两类白名单行）、未读取任何凭据值。
- 结果缓存（JSON）在仓外本卡 report 目录：`runs-main.json`、`canary-gate-runs.json`、
  `ac-gate-runs.json`、`ac-open-prs.json`、`gh-open-prs.json`。
