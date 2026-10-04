# 卡 B 发布验收：PR280 合并后的 canary / v2 标签推广

- dispatch：`dlg-20261004-020045-5e8eae`
- fix commit：`ad80149d23c7c70d86ebac396ebdb05a584ea33f`（`fix(gate): preflight disposition inputs before Silo access`）
- 合并 commit：`6fd21e0ab5a27a279fe5cd15dd74edaa0b079fe9`（Merge PR #280，merge commit）
- Fixes-Issue：#275（**保持 open**，见「未闭合」）
- 状态：**发布已完成并四层核实**；本卡只做验收与留证，未改发布机制、未手移 tag。

## 派发时的失败现场

`v2-tag-sync` run `37169377414`（push，01:53:38Z–01:54:52Z）失败在
「Select newest canary-verified main commit」，字面错误：

```
::error::no eligible canary-verified main commit after 9.3h; current v2 commit is older than the 4h threshold
```

派发时远端 `v2` 仍为 `98a3ed241294a0671ac95bee7e0b987147e7adf4`。根因是**时序**：
合并发生在 01:54Z，而当时最新的一次能提供 v2 证据的 canary 运行早于合并，
还没有任何一次运行在 `referenced_workflows` 里钉住新 SHA。这是排队问题，不是实现缺陷。

## 证据脚本的真实读取来源（读源码确认，非猜测）

`scripts/v2_tag_promotion_evidence.py` 的候选判定按下面三处 API 字段串联，缺一不可：

| 用途 | 端点 | 判定字段 |
| --- | --- | --- |
| 取运行清单 | `repos/zlxlabs/ci-infra-canary/actions/workflows/gate.yml/runs?per_page=100` | `conclusion == "success"` |
| 取钉住的 gate SHA | `repos/zlxlabs/ci-infra-canary/actions/runs/<id>` | `referenced_workflows[].path` 形如 `zlxlabs/gate/.github/workflows/gate-v2.yml@<sha>`，且每条 `.sha` 都要等于候选 |
| 确认真的执行了 | `repos/zlxlabs/ci-infra-canary/actions/runs/<id>/jobs` | 名为 `primary` 或 `… / primary` 的 job `conclusion == "success"` |

候选集是 `git log -n 20 refs/remotes/origin/main`，且候选的 `.github/workflows`
树必须与 main tip 一致（`git diff --quiet <cand> <main-tip> -- .github/workflows`）。
只读 `.github/workflows/gate.yml` 这条线上有引用记录的运行，不是任何别的 workflow。

**关键区分**：`ci-infra-canary` 仓里名字带 canary 的运行分属两个不同 workflow，证据价值完全不同：

- `.github/workflows/canary.yml`（workflow 名 `ci-infra-canary`，`workflow_dispatch` 触发）
  —— 实测 `referenced_workflows` 为**空数组**，它不调用 gate-v2，**不构成 v2 推广证据**。
  派发时提到的 `37169218427` 属于这一类（01:50:26Z 起，早于 01:54Z 合并，且无 gate-v2 引用），
  不能冒充新 SHA 的实测。
- `.github/workflows/gate.yml`（workflow 名 `gate`，`pull_request` 触发，常驻
  `ci/self-probe` PR）—— job 为 `uses: zlxlabs/gate/.github/workflows/gate-v2.yml@main`，
  每次运行都在 `referenced_workflows` 里记录当时 `@main` 解析到的 40 位 SHA。**只有这一条线算数。**

## 四层核实

| 层 | 运行 | URL | 证据 |
| --- | --- | --- | --- |
| 1. 新 main 的 CI | `37169377410`（push，`6fd21e0`，01:53:38Z–01:56:24Z） | https://github.com/zlxlabs/gate/actions/runs/37169377410 | `status=completed` / `conclusion=success` |
| 2. canary 覆盖固定 SHA | `37169781366`（`gate.yml`，02:01:37Z–02:05:49Z） | https://github.com/zlxlabs/ci-infra-canary/actions/runs/37169781366 | `conclusion=success`；`referenced_workflows` = `zlxlabs/gate/.github/workflows/gate-v2.yml@main` → **`6fd21e0ab5a27a279fe5cd15dd74edaa0b079fe9`**；jobs 中 `gate / primary` = `success` |
| 3. tag-sync 真的 Move | `37170013779`（`repository_dispatch`，02:05:58Z–02:06:19Z） | https://github.com/zlxlabs/gate/actions/runs/37170013779 | 「Move v2 to selected canary-verified main commit」`conclusion=success`；「Report v2 tag sync state」输出 `V2-TAG-SYNC-STATE: promoted` |
| 4. 远端标签 | — | — | `git ls-remote --tags origin v2` → `6fd21e0ab5a27a279fe5cd15dd74edaa0b079fe9\trefs/tags/v2`（stdout 非空） |

第 3 层的时间戳（02:05:58Z 起）紧随第 2 层 canary 完成（02:05:49Z），是既有的
`repository_dispatch: [canary-verified]` 自动派发，**不是**本卡手动 `workflow_dispatch`。
本卡在 canary 变绿后只做了一次 `gh run list --workflow v2-tag-sync.yml` 读取，
确认对应运行已存在且已结束，**因此没有再触发任何 workflow_dispatch**。

祖先关系（用 `git ls-remote` 拉回的远端标签值，不是本地副本）：

```
git fetch --force origin refs/tags/v2:refs/tags/v2
git rev-parse 'refs/tags/v2^{commit}'                       → 6fd21e0ab5a27a279fe5cd15dd74edaa0b079fe9
git merge-base --is-ancestor 6fd21e0… 'refs/tags/v2^{commit}' → rc=0（含合并 commit）
git merge-base --is-ancestor ad80149… 'refs/tags/v2^{commit}' → rc=0（含 fix commit）
```

标签推进方向单调：旧值 `98a3ed2` 是 `6fd21e0` 的祖先，未回退。

## 本卡做了什么 / 没做什么

- 只新增本文件；未改任何 workflow、脚本、模板、业务仓文件、权限/密钥/部署。
- 未 `git push tag`、未改 4h 阈值、未改任何发布机制。
- 未对 `ci-infra-canary` 做任何写操作（只读 workflow 定义 + 只读运行详情）。
- 未签任何真实处置回执，也**不声称**处置通道已全链恢复。

## 未闭合 / 未能判定

- **#275 保持 open**：`v2` 标签推广只说明 gate 仓自身的 canary 实测通过并可被下游钉到，
  并不代表处置业务通道端到端恢复。旧业务 caller 的 8 项输入迁移尚未完成，
  「通道全链已恢复」这句话在当前证据下不成立。
- **真实处置回执**：本卡未跑、未签，也不以 canary 绿推导回执可用性。
- **继承红**：派发时未取到主干基线（`gh api request failed`），本次与基线同名的红无法判定归属；
  本卡观察到的唯一红是 `37169377414`，已定位到时序原因并由后续运行消解，不属实现红。
- **ci-flow 技能未读**：agent 技能目录（`$HOME/.pi/agent/skills`）权限为 `d---------`，
  当前会话与属主同 uid 仍被拒绝读，`SKILL.md` 读取返回 `EACCES`，故本卡未能按技能约定执行。
  等待改用有界 `sleep` + 定次 API 轮询（单次最长 300s，全程约 13 次只读调用，预算 ≤20）。
  **ci-watch 在本机不存在**（`command -v ci-watch` 无结果，本地 bin 目录无同名可执行文件），
  所以没能走技能推荐的等待入口。这一条是本卡的已知减项，交回主脑复核。

## 复核命令

```bash
git ls-remote --tags origin v2
gh api repos/zlxlabs/ci-infra-canary/actions/runs/37169781366 \
  --jq '{status,conclusion,referenced_workflows}'
gh api "repos/zlxlabs/ci-infra-canary/actions/runs/37169781366/jobs" \
  --jq '.jobs[] | {name,conclusion}'
gh api repos/zlxlabs/gate/actions/runs/37170013779/jobs \
  --jq '.jobs[].steps[] | {name,conclusion}'
gh run view 37170013779 --repo zlxlabs/gate --log | grep 'V2-TAG-SYNC-STATE'
```