# B1 R2 verdict — gate PR 179 Silo producers

## 结论

固定范围 `598b939f00ad5f2df870d614294162526a963b59..bcfa0e5d414c540d2e8e3e80ad4c628ee2d49c51`（9 commits：实现 4 + R1 修复 4 + R1 verdict 1）：**pass**。无 P1。R1 F1/F2 已按登记修复且契约锁死。1 条 P3 接受不修。risk-tier: personal（AGENTS.md）。

## 对象与隔离

- Spec = 实现卡锁定决策/约束/行为验收 + head 上 R1 verdict findings/不变式表。未读实现/修复报告、对话、R1 推理过程。
- 先审增量 `99edeb9b186dd4a2e826cef8edfbd71a6928f55f..bcfa0e5d414c540d2e8e3e80ad4c628ee2d49c51`，再全量 base..head。
- OCR：`ocr-review` `status=reviewed`（primary MiniMax-M3，`reason=primary_selected`），26 条 findings，复核腿全 unverified。工具 severity 只当输入，下面按 personal 两问重判。

## 增量四问（修复段）

1. 只修已登记 finding：是。gate/quality 两处 MagicDNS `if`（F1）、`cmd_put` 多文件跳过（F2）、三态测试、DNS/`SILO_STORE` 契约锁、R1 verdict 文档、progress 段。无范围外应用改动。
2. 无新增模块/配置项/GitHub artifact fallback。`cmd_put` 单文件/多文件分支是 F2 最小机制。
3. 无双路径：`gate-v2.yml` 仍零 `upload-artifact`/`download-artifact`；hosted 仍 fail-loud。
4. 无新增状态或事实源。`test_silo_store_env_aligns_with_job_checkout_path` 补的是 R1 不变式 6 的锁，不是新抽象。

## F1 / F2 复核

### F1 关闭

- 条件与 S3 门槛：gate DNS `if: ${{ needs.primary.result != 'skipped' }}` 与 audit resolve/download 同条件。quality DNS `if: always() && env.AWS_ACCESS_KEY_ID != ''`；S3 put 仍 `always()` + continue-on-error，但首行空密钥即 `SILO_ACCESS_KEY 未传入` 退出，不发起网络，故 DNS 跳过与「会 connect 才解析」并集对齐。hosted 有 secret 时 DNS 仍 fail-loud。
- fork PR 逐 job：quality 落 hosted、无 secret → DNS skip、put 密钥检查失败被 continue-on-error 吸收。primary 整 job skip。ocr 因 resolve_advisory skip 而不跑。gate 因 primary skipped 跳过 DNS 与 audit 消费；aggregate 仍跑（skipped 步不算失败）；terminal/panel put 密钥检查 + continue-on-error；convergence 需 primary audit，fork 无 receipt。ledger job continue-on-error 吸收 hosted MagicDNS 失败。required `gate / gate` 不再因 DNS 误拒。
- 契约：`test_silo_touching_jobs_resolve_magicdns_before_s3` 锁五 job `if` 字面量。红验把 quality `if` 改为 `always()` → AssertionError（非 ImportError）；还原后 `git diff` 空。

### F2 关闭

- 三态与旧 `if-no-files-found: error`：单文件缺失 exit 1；多文件部分缺失 stderr `put skipping missing source file` 并上传已存在文件 rc=0；全缺失逐个明示后 `all sources missing` exit 1。非静默。
- 测试三态锁死。红验把多文件 skip 改成 `fail()`：partial 路径 SystemExit 1（`fail()` 生产出口，非 ImportError/SyntaxError）；all-missing 对 skip 文案 AssertionError；single-missing 仍绿。还原后 `git diff` 空。

## 不变式核对（全量）

| # | 结果 |
|---|---|
| 1 键布局 / 现行名 / ledger 后缀 | 通过。`build_key`=`d<tier>/<repo_id>/<name>/<rel>`；diagnostics 仍 `primary-review-diagnostics-v2-…`，advisory 仍 `advisory-event-<reviewer>-…`；ledger 键 `codex-review-ledger-v2-<repo>-<sha>-<run>-<attempt>`。 |
| 2 零 artifact 动作 / 10 上传 + 4 消费 | 通过。无 upload/download-artifact。10 个 put/put-dir（含 2 次 retry）与 4 个 get + resolve 走 `silo_store.py`。 |
| 3 resolve ≤ attempt | 通过。`select_attempt` 取 ≤ current 最大 int；miss rc=2、list 失败 rc=1。gate miss 后 `exit 0`。 |
| 4 fail-loud / secrets | 通过。S3 步无新增 continue-on-error、无 `\|\| true`；缺密钥文案含「SILO_ACCESS_KEY 未传入」；`required: false`。 |
| 5 DNS 在首个 S3 前 | 通过。五 job 均有 MagicDNS + `id -u`，`100.100.100.100` 15 次。F1 条件见上，不是缺步。 |
| 6 checkout ↔ `SILO_STORE` | 通过。quality/primary/ocr → `_gate-silo-src`；gate/ledger → `_gate-aggregator-src`；`job.workflow_repository`/`job.workflow_sha`。本轮契约已锁。 |
| 7 v2 hold | 通过。hold 文件写明推广期冻结；`test_enabled_clean_commit_advances` 指向不存在 hold。 |
| 8 凭据 / 范围 | 通过。diff 无凭据字面量。范围 = 允许清单 + 已披露 `tests/test_v2_tag_sync.py`。aggregator / disposition / shadow / legacy / `v2-tag-sync.yml` 无 diff。 |

## Findings

### P3 — 契约测试含恒真 Python 断言（接受不修）

- 违反：无独立 spec 条款（无法溯源 → 降级）。`test_silo_touching_jobs_resolve_magicdns_before_s3` 在锁 `if` 字面量之外还有 `("skipped" != "skipped")` / `("" != "")` 四条恒真断言。
- 路径：改坏 `if` 时真正变红的是字符串相等（红验 1）；这四条仍绿。
- 后果：死覆盖，不改变 F1 锁。P1 两问：会被看到（测试文件在 diff 里），后果不是误拒/丢数据/崩溃。接受不修。

OCR 其余 25 条不落地（工具标注 → 本仓）：`cmd_get` 优先级「prefix 被 trailing `/` 打扁」证伪（`args.key and (...)`，prefix 模式走 else 保留相对路径）；gate/primary/ocr/ledger 再加空密钥跳过 DNS 或给 hosted 开兜底 = 反着「hosted fail-loud / 禁止静默跳过 / 无 GitHub fallback」，且 fork 上 primary/ocr 整 job skip、ledger `continue-on-error` 吸收；`read_bytes`/rglob/basename 碰撞/IDNA/`..`/attempt 正则 = 当前八类产物与常量 hostname 下未触发或 fail-closed 已够；caller `vars:` fallback 反着锁定决策。不派修复。

## 证据

```
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q \
  tests/test_silo_store.py tests/test_gate_v2_contract.py tests/test_v2_tag_sync.py
# 173 passed in 10.98s
```

红验 1（HEAD 干净）：quality MagicDNS `if` → `always()  # RED-VERIFY`，`rg` 见标记。`test_silo_touching_jobs_resolve_magicdns_before_s3` 退出 1，AssertionError：`always()` vs `always() && env.AWS_ACCESS_KEY_ID != ''`。只还原该行，`git diff .github/workflows/gate-v2.yml` 空。

红验 2：`cmd_put` 多文件 skip → `fail(...)  # RED-VERIFY`。`test_put_multi_file_partial_missing_uploads_existing_and_logs_skip` SystemExit 1；`test_put_multi_file_all_missing_fails` AssertionError（skip 文案缺失）；`test_put_single_file_missing_fails` 仍绿。只还原该处，`git diff scripts/silo_store.py` 空。

`git diff --check` 工作区退出 0。冻结范围对 `progress/gate-silo-producers-progress.md:40` 报 `new blank line at EOF`（文档尾空行，非本轮行为缺口）。

OCR envelope：`status=reviewed`，`findings=26`，`verify_status=failed`（复核腿不可用，未把工具 severity 当结论）。

## 未知

- 未跑 canary E2E（artifact 总数 0、Silo 出现 d14 键）；卡面规定主脑在 B1.5 后补。
- 未在真实 hosted/fork runner 上量 MagicDNS 与 `env.AWS_ACCESS_KEY_ID`；F1 关闭由 workflow `if`/`runs-on`/密钥空串规则推出。
- runner 是否常驻 `uv`/能否拉 boto3：卡面假设，本轮未在 self-hosted 上 spike。
