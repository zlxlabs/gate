# B1 R1 verdict — gate PR 179 Silo producers

## 结论

固定范围 `598b939f00ad5f2df870d614294162526a963b59..99edeb9b186dd4a2e826cef8edfbd71a6928f55f`（4 commits，+1284/−330）：**fail**。无 P1。2 条 P2 改变了「失败语义与现状逐一相同」的锁定决策，建议修复后再合。risk-tier: personal（AGENTS.md）。

## 对象与隔离

- Spec = 实现卡锁定决策/约束/行为验收 + head 上 `tests/test_silo_store.py`、`tests/test_gate_v2_contract.py`。
- 全量审 base..head，不以增量代替。未读实现报告、对话、既有 reviews。
- OCR：`ocr-review` 在约 15 min 仍停在 primary leg、无 JSON envelope；记 timeout/incomplete，不得写成 skipped 或 clean。

## Findings

### F1 P2 — fork/hosted 上 required `gate` 被 MagicDNS 误拒

- 违反：锁定决策「各 artifact 的失败语义与现状逐一相同」；实现卡现场事实「fork PR 时 quality 落 hosted，上传步 continue-on-error 语义保留」。与不变式 5「查询失败即红」字面一致，但把 DNS 做成 job 级硬失败，覆盖了「本 job 本轮不必碰 S3」的路径。
- 路径：fork PR（`head.repo != this.repo`）→ quality/gate `runs-on` 落到 `ubuntu-latest`。primary 整 job skip。gate 的 audit resolve/download 因 `needs.primary.result == 'skipped'` 本不执行。MagicDNS 步 `if: always()` 且无 `continue-on-error`，查 `100.100.100.100` 失败即把 required `gate / gate` 打红；aggregate 默认 `success()` 被跳过。
- 后果：fork PR 即使 quality 检查通过、也不需要 primary-audit，门禁仍红（误拒）。`runner: hosted` 整链红与「已知问题：hosted 有意 fail-loud」一致；fork-guard 这条是默认 `runner: self` 的既有路径，不是 caller 传 hosted。
- P1 两问：会被触发（公开仓 fork PR；v2 hold 期间 fleet 钉旧 tag 不中招，canary `@main` 会）。后果是误拒不是静默放行/丢数据/崩溃 → 非 P1。
- 工具/本仓：自审 P2。建议：无后续 S3 必要（primary skipped）时不要让 DNS 失败跳过 aggregate；或 DNS/S3 失败语义与旧 hosted 路径对齐。

### F2 P2 — ledger-input 双文件 put 比旧 upload-artifact 更严，entry 模式不再落盘

- 违反：锁定决策「失败语义与现状逐一相同」。旧 `actions/upload-artifact` 多 path 时缺文件忽略、至少一份即成功（`if-no-files-found: error` 只在零文件时红）。新 `silo_store put --file PREFLIGHT --file INSTALL` 任一不是文件则在 connect 前 `fail`，两份都不上传。
- 路径：Install 步仅 `legacy && personal`。`scripts/gate-quality` 存在时走 entry，Install 跳过，`$RUNNER_TEMP/install-result.json` 不存在；非 personal 的 legacy 同样跳过。preflight 仍会写 `pr-size-preflight.json`。上传 `continue-on-error: true`，quality 仍绿，`ledger_input_upload=failure`。ledger resolve 在非 short-circuit 下 required input 缺失 → 不写 `ledger.jsonl`。
- 后果：entry/非 personal 仓丢失本会写成的 ledger 行。required gate 不变。ledger job 本身 `continue-on-error`。
- P1 两问：会被触发（workflow 一等入口就是 entry）。丢失的是 continue-on-error 的效果账本，不是 canonical audit/terminal → 非 P1。
- 建议：缺文件时仍 put 已存在的那份（对齐旧多 path），或 entry 也写 install-result。

## 不变式核对（无 P1 缺口）

| # | 结果 |
|---|---|
| 1 键布局 / 现行名 / ledger 后缀 | 通过。`build_key`=`d<tier>/<repo_id>/<name>/<rel>`；diagnostics 仍 `primary-review-diagnostics-v2-…`，advisory 仍 `advisory-event-<reviewer>-…`；ledger 键为 `codex-review-ledger-v2-<repo>-<sha>-<run>-<attempt>`。 |
| 2 零 artifact 动作 / 10 上传 + 4 消费 | 通过。`gate-v2.yml` 无 `upload-artifact`/`download-artifact`。10 个 put/put-dir（含 2 次 retry）与 4 个 get + 2 个 resolve 走 `silo_store.py`。 |
| 3 resolve ≤ attempt | 通过。`select_attempt` 取 ≤ current 的最大 int；miss 退出 2，list 失败退出 1。gate 旧解析器 miss 亦 `SystemExit(0)`，新 rc=2 后 `exit 0`，回落等价。 |
| 4 fail-loud / secrets | 通过。S3 步无新增 continue-on-error、无 `\|\| true`；缺密钥文案含「SILO_ACCESS_KEY 未传入」；`required: false`。 |
| 5 DNS 在首个 S3 前 | 通过。五 job 均有 MagicDNS + `id -u`，`100.100.100.100` 出现 15 次；契约测锁顺序。F1 是「失败是否应打红 job」而非缺步。 |
| 6 checkout ↔ `SILO_STORE` | 实现通过。quality/primary/ocr → `_gate-silo-src` + `job.workflow_repository`/`job.workflow_sha`；gate/ledger → `_gate-aggregator-src`。契约测试未锁这对齐（换 env 路径不一定红）。 |
| 7 v2 hold | 通过。`.github/v2-tag-sync.hold` 写明推广期冻结；`test_enabled_clean_commit_advances` 改指不存在 hold；`tmp_path` 用例仍覆盖 hold 阻断。 |
| 8 凭据 / 范围 | 通过。diff 无凭据字面量。范围 = 允许清单 + 已披露 `tests/test_v2_tag_sync.py`。aggregator / disposition / shadow / legacy / `v2-tag-sync.yml` 无 diff。 |

## 证据

```
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q \
  tests/test_silo_store.py tests/test_gate_v2_contract.py tests/test_v2_tag_sync.py
# 169 passed in 12.51s
```

红验（HEAD 树，注入前已是提交态）：`scripts/silo_store.py:55` `return f"{tier}/..."` → `f"x{tier}/..."`，`rg` 见 `# RED-VERIFY`。pytest 退出 1，断言失败（非 ImportError）：

- `test_build_key_joins_tier_repo_name_and_relative_path`：`d14/99/...` vs `xd14/99/...`
- `test_put_get_and_put_dir`：put 键同样带 `x` 前缀

随后只还原该行，`git diff scripts/silo_store.py` 空。`test_gate_v2_contract.py` 在这次变异中仍绿（它锁 workflow `--tier`/名字表达式，不调用 `build_key`）；两层锁的是不同切面。

`git diff --check 598b939f00ad5f2df870d614294162526a963b59..99edeb9b186dd4a2e826cef8edfbd71a6928f55f` 退出 0。

## 未知

- 未跑 canary E2E（artifact 总数 0、Silo 出现 d14 键）；卡面规定主脑在 B1.5 后补。
- 未在真实 hosted/fork runner 上量 MagicDNS；F1 由 workflow `if`/`runs-on` 与 GitHub `if:` 替换 `success()` 的规则推出。
- runner 是否常驻 `uv`/能否拉 boto3：卡面假设，本轮未在 self-hosted 上 spike。
