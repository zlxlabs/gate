# B2 R1 verdict — gate PR 180 跨 run 消费侧迁 Silo

## 结论

固定范围 `f61ef54ef21b41e3addbd2ae3ef796b7e45cb524..9dd653588a68ea8dc21a34bc5486069ed854cd02`（4 commits，+725/−20）：**fail**。无 P1。2 条 P2：一条让「Silo 非缺失错误 fail-loud、禁止回退 GitHub」在真实 boto3 错误类上落空；一条让 CI 实际走的 `_silo_objects_under` 双路径没有测试锁。建议修复后再合。risk-tier: personal（AGENTS.md）。

## 对象与隔离

- Spec = 实现卡锁定决策/约束/行为验收 + 设计文档 `docs/sessions/260916-pwrs-silo-migration/design.md`（该文件仍是 91 行原始 DESIGN-note，未见卡面所称落地补记）。
- 全量审 base..head，不以抽查代替。未读实现报告、对话、实现者推理。
- OCR：`ocr-review` `status=reviewed`（primary MiniMax-M3，`reason=primary_selected`），13 条 findings，复核腿全 unverified。工具 severity 只当输入，下面按 personal 两问重判。

## Findings

### F1 P2 — `_silo_objects_under` 双路径无测试；`except ImportError` 宽于 import 探针

- 违反：不变式 6（CI 真走的 CLI 路径要有测试；`except ImportError: pass` 不得吞 `connect()` 的 ImportError）。
- 路径：gate job 是 `python3 aggregate.py`。表驱动 6×2 全部 `monkeypatch` `_silo_objects_under`，不进入 `import boto3` / `store.connect()` / `_silo_cli(["list", "--prefix", …, "--dest", dest])`。本机 `python3` 能 `import boto3`（1.34.46）；B1 的 `uv run --with boto3` 暗示 runner 不依赖系统 boto3——两条路径都可能是 CI 真身，都没锁。
- `connect()` 把 boto3 ImportError 转成 `fail()`/`SystemExit`，**不会**被这条 `except ImportError` 吞掉。过宽处是 `list_keys`/`get_object`/`_body_bytes` 若抛 ImportError 会静默改走 CLI，而不是 fail-loud。CLI 再失败会 `RuntimeError` → `::warning::` 降级，不是静默空成功。
- P1 两问：覆盖缺口在每次 aggregate 都会走到（触发：是）。后果是改坏 CLI argv/四段键/`--dest` 布局现有测试仍绿；真坏时走已设计的 warning 降级，不是丢账本/崩溃 → 非 P1。
- 建议：收窄 `except ImportError` 到 `import boto3`；补一条不 mock `_silo_objects_under`、强制 ImportError 走 CLI（FakeS3 或录制 dest 布局）的测试。

### F2 P2 — disposition 把 get 退出码 2 当「键不存在」，boto3 `ClientError` 全是 2

- 违反：不变式 3 / 锁定决策「`gh run download` 只允许 Silo 键不存在（退出码 2）；Silo 其他非零必须 fail-loud，不许回退 GitHub 掩盖故障」。
- 路径：新控制流 `.github/workflows/gate-v2-disposition.yml` 在 `silo_rc==2` 时 `gh run download`。`scripts/silo_store.py` `cmd_get`（本卡未改，但是这个新判据的callee）把 `type(err).__name__ in {"NoSuchKey", "ClientError"}` 映射成 `EXIT_NOT_FOUND=2`。真实 boto3 `get_object` 的 404/403/5xx 都是 `ClientError`，于是 S3 协议层错误几乎全走 GitHub 回退；`elif silo_rc != 0` 的 fail-loud 只覆盖缺密钥、缺 uv、`connect()` 失败等连接前错误。本卡新增的 `cmd_list` 复制了同一映射；aggregate 把任意非零当 `RuntimeError`，list 侧不构成掩盖。
- 后果：Silo 5xx/403 时日志先表现为 `gh run download` 失败。迁移后新 run 的 audit 不在 GitHub，job 仍红（不是静默用错 audit）。迁移前旧 run 会成功拿到 GitHub artifact，Silo 故障被掩盖。
- P1 两问：Silo 抖一下就会走这条（触发：是）。后果是错误信息/掩盖故障，不是丢数据、静默错结果或崩溃 → 非 P1。
- 建议：get 只对 Error Code `NoSuchKey`/`404` 返回 2；或 disposition 用 stderr 文案区分缺失与其它错误。契约测试要锁「非 NoSuchKey 不得进 `gh run download`」。

## 不变式核对

| # | 结果 |
|---|---|
| 1 双读合并 | 通过。GitHub 先写入、Silo 覆盖。Silo 失败 `::warning::` + incomplete。GitHub 失败沿用 fail-open（无 `::warning::`，记 incomplete）。 |
| 2 返回结构 | 通过。`_fetch_*` 签名、`HistoryLoad` 字段、`_terminal_row`、receipt 去重键（artifact 名）未改。消费点无 diff。面板渲染仍按 `(run_id, run_attempt)` 排序。 |
| 3 disposition | 部分，见 F2。无 `upload-artifact`；`gh run download` 字面只在 `silo_rc==2` 分支；MagicDNS `100.100.100.100` 在首个 get/put-dir 前；audit 名=`primary-audit-v2-<repo_id>-<当前 PR head_sha>-<run>-<attempt>`；receipt 名来自 `issue_receipt.py` 未改；`--tier d30`。 |
| 4 silo_store list | 通过。put/get/resolve 无语义 diff。空列表 0、列举失败非零。非四段键只在 `--dest` 时 fail-loud（CI dest 走这条）。 |
| 5 runs-on | 通过。`[self-hosted, linux, ci]` 与 quality 一致。GitHub-hosted 到不了 100.x 与 B1 hosted fail-loud 一致。workflow_dispatch/call 只 checkout gate 稀疏文件，无 PR 三元。caller 已 `secrets: inherit`。 |
| 6 `_silo_objects_under` | **F1**。双路径有真实场景；`connect()` ImportError 不漏；glue 无测试。 |
| 7 表驱动 6×2 | 通过。`both_same` 断言 Silo 覆盖（terminal `pass` 盖 `fail`；receipt `from-silo`）。红验见下。 |
| 8 凭据 | 通过。diff 无凭据值。S3 步前检查 `AWS_ACCESS_KEY_ID` 与 `uv`。 |
| 9 incomplete 过滤 | 通过。`silo_only` 丢掉 GitHub prefix 空匹配；`both_empty` 保留 `no terminal artifact matched`。过滤按 `run_id` 与 GitHub 文案粒度一致。 |
| 10 文档 | 通过。面板与 convergence 文档描述双读、Silo 优先下载、d30 receipt。 |

## OCR 对照（工具 / 本仓 / 两问）

| 工具 | 本仓 | 两问 |
|---|---|---|
| F3 high：incomplete 只按 run_id | 不成立 | GitHub 文案就是 `matched run {id}`，targeted 扫描也按 run_id；与过滤同粒度 |
| F4 high：ImportError 过宽 | **F1 P2** | 见上；connect() 本身不抛 ImportError |
| F5 med：四段键双路径不对称 | P3 backlog | 生产者只写四段键；CLI dest 仍 fail-loud |
| F13 med：`put-dir` token 配不上 | 不成立 | workflow 文本含 `" put-dir "`（`put-dir \` 前有空格） |
| F1/F2/F6–F12 low | ≤P3 或不成立 | F1 把 boto3 返回值说成非 dict，与 FakeS3/生产 dict 不符；其余风格/超时/文案 |

## 证据

```
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q \
  tests/test_gate_aggregator.py tests/test_silo_store.py tests/test_gate_v2_contract.py
# 425 passed in 14.44s
```

红验（HEAD 树，注入前已是提交态）：`_merge_history_loads` 两圈对调（Silo 先、GitHub 后），`rg` 见 `# RED-VERIFY`。`test_terminal_history_dual_read_table[both_same]` 退出 1，断言失败（非 ImportError）：`{(35082772768, 1): 'fail'} != {(35082772768, 1): 'pass'}`。同次 `test_disposition_receipts_dual_read_table[both_same]` 仍绿（它走另一合并函数）。只还原该处后 `git diff .github/actions/gate-aggregator/aggregate.py` 空。

`git diff --check f61ef54ef21b41e3addbd2ae3ef796b7e45cb524..9dd653588a68ea8dc21a34bc5486069ed854cd02` 退出 0。

## 未知

- 设计文档无落地补记；本轮按实现卡不变式审。
- 未跑 canary E2E（面板同时含迁移前 GitHub 行与新 Silo 行；人工 disposition 上传 d30 receipt）。
- ci 池是否常驻 `gh`/`jq`（disposition 从 `ubuntu-latest` 迁走后仍依赖；同池 quality 已证 python3/uv/sudo/MagicDNS）。
- aggregate 用的 `python3` 是否自带 boto3（本机有；B1 用 uv 暗示不依赖）。
