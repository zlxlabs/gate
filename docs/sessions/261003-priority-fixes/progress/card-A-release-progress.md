# 卡 A 进度：gate#278 授权发布后的 canary / v2 / 真实处置回执验收

- dispatch：`dlg-20261003-163638-86f789`
- worktree：`disposition-release-261004`，分支 `card/disposition-release-261004`
- base / main tip：`98a3ed241294a0671ac95bee7e0b987147e7adf4`
- fix：`b951586217bc26705b07be77d2d7c3f1ef13209a`
- 阶段：verifying（发布链已验；处置回执 blocked）
- 详细证据：`../evidence/card-A-release.md`

## 本段结论

四段全绿、两段阻塞：

1. **源码合并** ✅ 主脑已完成（PR #279 merge commit `98a3ed2`，父链含 `b951586`）。
2. **正式 main CI** ✅ run 37137317244（`ci`，push，`success`）。
3. **canary** ✅ `zlxlabs/ci-infra-canary` 的 `gate.yml` run 37137964734 成功，
   `referenced_workflows` 解析到 gate sha `98a3ed2…`，`gate / primary` = SUCCESS。
   本卡开始时该 run 已在跑且正好覆盖固定 main SHA，**没有重复 dispatch**。
4. **v2 发布** ✅ canary 绿后既有 `repository_dispatch: canary-verified` 自动触发
   tag-sync run 37138246009，日志 `V2-TAG-SYNC-STATE: promoted`；远端
   `git ls-remote --tags origin v2` = `98a3ed241294a0671ac95bee7e0b987147e7adf4`，
   fetch 精确 ref 后 `merge-base --is-ancestor b951586… v2` exit 0。
5. **真实处置回执** ⛔ **blocked**：当前不存在可签的有效 finding（见下）。
6. **caller 输入 schema** ✅ `zlxlabs/agent-config` 的 `gate-disposition.yml` 已是 9 输入，
   不存在「caller 仍 5 输入」的阻塞。

## 处置回执为什么 blocked

原始出处 `zlxlabs/agent-config#3899` 已于 `2026-10-03T16:14:16Z` **合并**，其 head
`f04739f9…` 上残留的 `gate / primary` failure 不再代表任何待处置对象，按锁定决策不复用。

全量扫描（agent-config 最近 40 条 gate run × 18 个 open PR；gate-hub 40 条 × 4 个
open PR；ci-templates 无 gate run）：所有 primary 失败的 head 都属于已合并/已关闭的 PR。
agent-config 三个非 draft 的 open PR 里，`gate / primary` 分别是 `success` / `skipped` /
`skipped`——都不是 `failure`，`gate_disposition.py` 的 `primary_run()` 会在任何写请求前
拒掉（exit 2）。

没有 finding 就没有可证伪对象，也没有 out-of-scope 对象；需要人类裁决的
security / data / deployment 风险不代签。本卡目标「#278 已授权发布」不是风险裁决授权。
→ 不造 finding、不签旧身份，dispatch 预算 1 次未消耗。

## 明确没做的事

- 没有手工移动 `v2`、没有新增门禁、没有改 caller pin（`@v2` 是移动标签，调用方无需推广）。
- 没有关闭 issue、没有改任何标签、没有开 PR、没有自行 merge。
- 没有以任何会写的方式调用 `gate_disposition.py`（本卡只读其源码确认规则）。
- 没有 SSH、没有整段回显生产日志、没有读取任何凭据值。

## 关键决策

- 「tag-sync run 37137317200 SUCCESS」不作为发布证据：它的 `Move` 步骤被 skip，
  状态是 `no_eligible_candidate`。发布证据只认 run 37138246009 的
  `V2-TAG-SYNC-STATE: promoted` + 远端 `ls-remote` 的真实值 + `merge-base --is-ancestor`。
- 不拿 canary run 37126117667 顶账：它引用的 gate sha 是旧的 `f8c16be…`。

## 下一步唯一动作

主脑按 `../evidence/card-A-release.md` 第 7 节三条判据复核 v2，即可关闭 #278 的发布
闭环。处置回执一段保持 blocked，等真实业务仓出现一条**未合并 PR 上 primary 失败且
severity ∈ {major, blocker}** 的 finding 时，由持有该 finding 的一方按
`gate_disposition.py` 的现有规则处置——本卡不代为触发。
