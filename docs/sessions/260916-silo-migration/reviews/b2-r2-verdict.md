# B2 R2 verdict — gate PR 180 跨 run 消费侧迁 Silo

## 结论

固定范围 `f61ef54ef21b41e3addbd2ae3ef796b7e45cb524..51d2880930442b9017d6ceb402fea0d0da0d9a16`（8 commits：实现 4 + R1 修复 3 + R1 verdict 1）：**pass**。无 P1。R1 F1/F2 已按登记修复且契约锁死。1 条 P3 接受不修。risk-tier: personal（AGENTS.md）。

## 对象与隔离

- Spec = 实现卡锁定决策/约束/行为验收 + head 上 R1 verdict findings/不变式表。未读实现/修复报告、对话、R1 推理过程。
- 先审增量 `9dd653588a68ea8dc21a34bc5486069ed854cd02..51d2880930442b9017d6ceb402fea0d0da0d9a16`，再全量 base..head。
- OCR：`ocr-review` `status=reviewed`（primary MiniMax-M3，`reason=primary_selected`），6 条 findings，复核腿全 unverified。工具 severity 只当输入，下面按 personal 两问重判。

## 增量四问（修复段）

1. 只修已登记 finding：是。`cmd_get`/`cmd_list` 错误码映射（F2）、`except ImportError` 收窄 + CLI/ImportError 契约测试（F1）、`sys.modules` boto3 stub（F1 环境解耦）、R1 verdict 文档。无范围外应用改动。
2. 无新增模块/配置项/GitHub artifact fallback。`has_boto3` 是局部旗标，不是新状态源。
3. 无新增双路径：GitHub∪Silo 与 boto3/CLI 均为卡面锁定的过渡路径；本轮只给后者补锁。
4. verdict 提交 `5855299` 只含 `b2-r1-verdict.md`。

## F1 / F2 复核

### F1 关闭

- `except ImportError` 现在只包 `import boto3` 探针（`aggregate.py:1656-1659`）。`connect()` 仍把 boto3 ImportError 转 `fail()`/`SystemExit`；有 boto3 时该 SystemExit 再转 `RuntimeError`，不落 CLI。`list_keys`/`get_object` 的 ImportError 不再被吞。
- `test_silo_objects_under_narrows_import_error` 不 mock `_silo_objects_under`：dummy boto3 让探针成功，`list_keys` 抛 ImportError，断言不调 `_silo_cli`。`sys.modules` stub 使有无环境 boto3 都走同一分支。
- `test_silo_objects_under_cli_path_contract`：`boto3 is None` 强制 CLI；锁 `["list","--prefix",prefix,"--dest",dest]`、dest 已存在、四段键读回、非法键 `ValueError`。改 argv/`--dest` 布局/键切分会红。
- 表驱动 6×2 仍 mock `_silo_objects_under`（合并层锁）。CLI 真身在上述契约；in-process glue 见 P3。

### F2 关闭

- `cmd_get`/`cmd_list` 只对 `err.response["Error"]["Code"]` ∈ {NoSuchKey, 404, NoSuchBucket} 返回 2；有 Code 但不在集合 → 1；`response` 非 dict / Error.Code 缺失 → 1。本机 boto3 1.34.46：`ClientError.response["Error"]["Code"]` 为 `NoSuchKey`；缺 Code 时 `.get` 得 `None` → 1。
- `test_get_client_error_mapping_contract` 锁 AccessDenied/InternalError=1。workflow：`silo_rc==2` 才 `gh run download`，`elif silo_rc != 0` 失败；契约测锁 `eq 2` 分支与「此前无 gh run download」。list 下载同映射；`list_keys` 列举失败仍是 1；aggregate 任意非零 → `RuntimeError` + `::warning::`，不进 GitHub 回退。
- 红验：get 集合塞进 AccessDenied 后 `test_get_client_error_mapping_contract[AccessDenied]` AssertionError `2 == 1`（非 ImportError）；还原后 `git diff scripts/silo_store.py` 空。

## 不变式核对（全量）

| # | 结果 |
|---|---|
| 1 双读合并 | 通过。GitHub 先写入、Silo 覆盖。Silo 失败 `::warning::` + incomplete。GitHub 失败沿用 fail-open。 |
| 2 返回结构 | 通过。`_fetch_*` 签名、`HistoryLoad` 字段、`_terminal_row`、receipt 去重键未改。面板仍按 `(run_id, run_attempt)` 排序。 |
| 3 disposition | 通过。无 `upload-artifact`；`gh run download` 只在 `silo_rc==2`；MagicDNS `100.100.100.100` 在首个 get/put-dir 前；audit/receipt 名原样；`--tier d30`。F2 映射见上。 |
| 4 silo_store list | 通过。put/get/resolve 无语义 diff（get 映射除外）。空列表 0、列举失败非零。非四段键在 `--dest` 时 fail-loud。 |
| 5 runs-on | 通过。`[self-hosted, linux, ci]`。workflow_dispatch/call 稀疏 checkout gate，无 PR 三元。 |
| 6 `_silo_objects_under` | 通过。F1 已关。CLI argv/四段键/`--dest` 有锁。 |
| 7 表驱动 6×2 | 通过。`both_same` 断言 Silo 覆盖（terminal `pass` 盖 `fail`；receipt `from-silo`）。 |
| 8 凭据 | 通过。diff 无凭据值。S3 步前检查 `AWS_ACCESS_KEY_ID` 与 `uv`。 |
| 9 incomplete 过滤 | 通过。`silo_only` 丢掉 GitHub prefix 空匹配；`both_empty` 保留 `no terminal artifact matched`。 |
| 10 文档 | 通过。面板与 convergence 描述双读、Silo 优先下载、d30 receipt。 |

## Findings

### P3 — `_silo_objects_under` in-process glue 仍无不 mock 该函数的测试（接受不修）

- 违反：不变式 6 的「CI 真走路径要有测试」残余。F1 已锁 CLI 与 ImportError 收窄；有 boto3 时 `connect`/`list_keys`/`get_object`/`_body_bytes` 拼装仍只被 6×2 整函数 mock 掉。
- 路径：runner `python3` 若自带 boto3（本机有 1.34.46），aggregate 走 in-process。store 层 FakeS3 已锁 list/get。
- P1 两问：glue 改坏会在每次 silo 扫描走到（触发：是）。后果是 `::warning::` 降级，不是丢账本/静默错结果/崩溃 → 非 P1。接受不修。

## OCR 对照（工具 / 本仓 / 两问）

| 工具 | 本仓 | 两问 |
|---|---|---|
| F1/F2/F5 high：`"404"` 不是 boto3 Code | 不成立 | R1 建议含 404；真实 boto3 用 `NoSuchKey`，多这一格不把 403/5xx 当 miss |
| F3 med：`_layout_suffix` 不拒 `..` | P3 backlog（R1 F5 同类） | 生产者 `build_key` 已拒 `..`；桶为自有 CI 产物。非丢数据/崩溃 |
| F6 med：非结构化 ClientError 测错路径 | 不成立 | `getattr(response)` 为 None 时仍走内层 `fail(...): {err}` 返回 1，锁的是旧字符串匹配不再当 miss |
| F4 low：`sorted(TIERS)` 重复 | 不成立 | 风格；无第二消费者的抽取不在本轮 |

## 证据

```
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q \
  tests/test_silo_store.py tests/test_gate_aggregator.py tests/test_gate_v2_contract.py
# 437 passed in 47.27s
```

红验（HEAD 树，注入前已是提交态）：`cmd_get` 集合加入 `AccessDenied`，`rg` 见 `# RED-VERIFY`。`test_get_client_error_mapping_contract[AccessDenied]` 退出 1，断言失败（非 ImportError）：`assert 2 == 1`；同参数化 NoSuchKey/404/NoSuchBucket/InternalError 仍绿。只还原该处后 `git diff scripts/silo_store.py` 空。

`git diff --check f61ef54ef21b41e3addbd2ae3ef796b7e45cb524..51d2880930442b9017d6ceb402fea0d0da0d9a16` 退出 0。

## 未知

- 未跑 canary E2E（面板同时含迁移前 GitHub 行与新 Silo 行；人工 disposition 上传 d30 receipt）。
- 未另开无 boto3 的 venv；新测试用 `sys.modules` stub 解耦，本机有 boto3 1.34.46。
- S3 兼容实现若用非 `NoSuchKey` 的缺失码（如 `NotFound`）会 fail-loud（exit 1）而非回退 GitHub。
- ci 池是否常驻 `gh`/`jq`（disposition 从 `ubuntu-latest` 迁走后仍依赖）。
