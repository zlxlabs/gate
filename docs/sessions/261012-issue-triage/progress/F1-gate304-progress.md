# F1 卡 · gate#304 previous-round 改为按 PR 取最新一轮

## 段 1 · pickup 与方案落地

**当前阶段**：implementing

**本段结论**：根因是 `_previous_findings_from_silo` 对仓级 `codex-review-ledger-v2-` 前缀 `list --dest` 串行全下；改为写端覆盖写 `codex-review-ledger-latest-v1-<repo>-pr<pr>`，读端只 GET。写端不做 GET 再比较，旧 run 晚结束覆盖新内容的窗口留在报告。

**关键决策与已否决方案**：
- 读端不保留全扫回退；缺对象/校验不过走 `_degrade_previous_round`，枚举 `silo-ledger-latest-absent` / `silo-ledger-latest-not-prior` / `silo-ledger-latest-unreadable`。
- 已否决：调大 `SILO_CLI_CALL_TIMEOUT_SECONDS`；写端 GET+单调比较（步骤失败语义变复杂，卡面允许残余窗口）。
- PR 号两端都用 `github.event.pull_request.number` / ledger JSONL 首行 `pr_number`。

**下一步唯一动作**：改 `aggregate.py`、`gate-v2.yml` 与对应测试，再跑窄验。

## 段 2 · 实现与验收

**当前阶段**：verifying

**本段结论**：读端只 GET `d30/<repo>/codex-review-ledger-latest-v1-<repo>-pr<pr>/ledger.jsonl`；缺对象/非前序轮分别打 `silo-ledger-latest-absent` 与 `silo-ledger-latest-not-prior`。全量 1535 passed，pin 检查通过，diff 234 行。

**关键决策与已否决方案**：
- 跨边界测试走真实 `_silo_cli` 子进程 + `SILO_STORE` 假 store，断言 argv 仅一次 get、无 list。
- 已否决：单独 `tests/test_gate_previous_round_latest.py`（模块加载样板把预算顶过 250）。

**下一步唯一动作**：先 commit 真修复，再做红验（改回 list、去掉 run 序校验）。
