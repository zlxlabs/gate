# B 卡 · gate#302 反证 git grep 30s 超时

## 段 1 · 取证与根因

**当前阶段**：implementing（根因已在本机与 runner 日志双向证实，修复已落盘并通过窄验）

**本段结论**：根因是 disposition 准备步骤的 `git fetch --depth 1 --filter=blob:none`
留下 partial clone——commit/tree 在、blob 全缺，`git grep <sha> -- scripts/herdr`
按 promisor 协议逐 blob 现场取，对 agent-config#4492 的 64 个 blob 就是 64 次串行
HTTPS 往返，稳定吃满 30s 预算。改用无过滤的 depth-1 取数后同一命令 5.6ms 命中 3 行。

**关键决策与已否决方案**：
- 取对象与可区分原因**同时**做（卡面优先级第 1、2 项都要）：workflow 侧取全量并在
  步骤内自检缺对象（`rev-list --objects --missing=print`，缺则 `::error::` 显式失败，
  外层 `timeout 300`），producer 侧 `rerun_counterevidence` 前置同样口径的 rev-list
  预检，防的是 repo_dir 由别处准备时再次退化成 30s 静默超时。
- 超时单独报 `counterevidence_rerun_timeout`；对象缺失报
  `counterevidence_head_object_missing`；OSError / 非 0 退出仍报
  `counterevidence_rerun_failed`。三者都不写回执（fail-closed 不变）。
- 已否决：调大 `COUNTEREVIDENCE_RERUN_TIMEOUT_S`（若根因是缺对象就是掩盖）；保留
  `--filter=blob:none` 只加预检（预检只能把 30s 超时换个名字，不解决取数）；预检失败
  后放行 grep 当 fallback（违反 fail fast）；改 `counterevidence_argv_reason` 白名单
  （卡面禁止）。

**下一步唯一动作**：红验（注入旧折叠行为与去掉预检，证明新断言会红），然后 commit+push。

## 段 2 · 复现原文（外部时变状态）

**当前阶段**：implementing

**本段结论**：run 38142684385（agent-config，2026-10-11）里
「Fetch caller head for counterevidence rerun」13:19:32→13:19:34 成功，
「Issue immutable disposition artifact」13:19:34→13:20:10 失败，
日志末尾 `subprocess.TimeoutExpired: Command '['git','grep',...,'dc5f31d4…','--','scripts/herdr']' timed out after 30 seconds`，
收尾时 `Terminate orphan process: pid (452) (git)` / `pid (454) (git-remote-https)`
——被杀时正在跑网络取对象。本机照抄同一组参数（depth 1 + `--filter=blob:none` + 按 sha fetch）
对同一 commit 复现：`exit=124 elapsed=30.003s match_lines=0`；GIT_TRACE=2 计到 28.7s 内 34 次
子进程拉起（每次一个 `git remote-https`）。改成无过滤 depth-1 后同一命令
`fetch_elapsed=9.22s`、缺对象 0、`grep exit=0 elapsed=0.0056s match_lines=3`。

**关键决策与已否决方案**：复现用真仓真 sha，不用 monkeypatch 造场景——卡面要求跨边界
测试必须真 git 仓 + 与 workflow 相同 clone 参数。

**下一步唯一动作**：见段 1。

## 段 3 · 修复落盘与红验

**当前阶段**：implementing

**本段结论**：`issue_receipt.py` 加 `_require_head_objects` 前置预检并拆开超时/缺对象/
命令失败三种原因字面量；`gate-v2-disposition.yml` 的取数步骤去掉 blob 过滤、加
`timeout 300` 与缺对象自检；测试同步改口径并新增两条真仓用例（partial clone → 可区分
原因且不走满 30s；depth-1 全量 → 真实回执 payload 与真实 git grep 逐字节一致），
workflow 契约测试锁住「取数行不得带 --filter」。

**关键决策与已否决方案**：
- 本地 file:// origin 默认不认 `--filter` 也拒绝按 sha 取，必须显式打开
  `uploadpack.allowFilter` / `uploadpack.allowAnySHA1InWant` 才等于 GitHub；不打开时
  fixture 会静默退化成全量，测试变成假绿（实测踩到，已在 helper 注释里写明）。
- 窄验：`tests/test_gate_disposition_issue_receipt.py` 39 passed；
  `tests/test_gate_v2_contract.py` 同批 256 passed；`scripts/check_pinned_uses.py` OK。
- `docs/design/counterevidence-rerun.md` 的原因表仍写「超时 → counterevidence_rerun_failed」，
  该文件不在本卡 Scope-Globs 内，未改，留给主脑裁决。

**下一步唯一动作**：commit + push，报主脑复核。