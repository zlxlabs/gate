# 公开 PR 作者策略进度

## 基线
- 独立 worktree 起点 `origin/main`/`v2` 均为 `bb443ef2b8a8b428565fa69fdb3f0106ab6fa4d1`；旧 main 的 retro 改动未触碰。作者取 `pull_request.user.login`，不取可因人工重跑变化的 `github.actor`。REST PR #252 的真实字段值 `zj1123581321`（https://api.github.com/repos/zlxlabs/gate/pulls/252，2026-09-26）仅作对象字段 fixture；未找到真实 Dependabot PR/Actions run。

## 作者矩阵
| 情形 | Quality / 可信评审 | Required Gate |
|---|---|---|
| 同仓人工作者 ready | 现有路由 / 正常主审 | 按质量和主审结果 |
| 人工或 Bot draft→ready | 现有路由 / draft复核后跳过，ready再跑 | 仅仍为draft时保留旧skip |
| 人工作者 fork | hosted / 跳过 | 保留旧兼容skip，明确未主审 |
| Dependabot ready/人工重跑 | hosted / 跳过 | `unavailable`，拒绝accepted no-op |
| 作者缺失、空或非字符串 | hosted / 跳过 | `unavailable`，不回退actor/classifier |
| 合法作者命中classifier豁免 | 现有路由 / 按旧豁免 | 保留accepted skip，不豁免Dependabot |

## 实施与验证
- `evaluate` 现在要求作者入参；workflow用JSON保真传到CLI，测试锁作者谓词、producer argv及终态。真实PR作者只证明REST字段，不冒充Actions/Bot实测。
- 全仓 AST/RG manifest 共71处 `evaluate`：生产1、测试70（aggregator61、convergence5、redline1、review_ledger3）；PR #252 `user.login` 仅为真实REST对象字段fixture。
- 首轮全套1197 passed/35 failed：32项由ledger三处共享调用缺作者引发，3项是shadow条件副本；收口定点组876 passed/1暴露classifier selector遗漏，补齐后shadow合同84 passed。最终full 1232 passed/118.61s，pinned-use检查8文件OK，官方CI actionlint命令exit0。hosted依赖、runner镜像/ACL/隔离与真实Bot run仍未知。
