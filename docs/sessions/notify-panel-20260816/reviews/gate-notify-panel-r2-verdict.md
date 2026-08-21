<!-- delegate-outcome: succeeded -->

## 审查对象与最终判定

本轮审查对象冻结为：

- 全量：`2729d2ae4997432be7f226a075ab04572c226435..290bd5eb77af63eb8f2331ab1f99c1ec06bd1d18`
- 增量：`da5bd4c..290bd5e`（H0..H2）
- 远端核对：`origin/main=2729d2a`，`origin/card/gate-notify-panel=290bd5e`
- R1 verdict：`origin/card/gate-notify-review` 的 `gate-notify-panel-r1-verdict.md`

本轮新证据不是再次阅读 diff：是以前没有的子进程级真实 `aggregate.py` 运行、HTTP 边界 stub、publish-only 失败路径、actionlint schema 校验，以及两处“改坏实现后测试必须变红”的红验。OCR 前置扫描也已执行，但三腿均为 `status=skipped` / `status_missing`，没有把它当作清洁证据。

最终判定：**本轮无新增 P1；H0..H2 增量审通过。**

## Part A：H0..H2 增量审四问

### ① 两轮修复是否只修了登记 findings？

是。六个增量提交均能对应 R1 登记项或轮 2 的无效 concurrency 键：

- `c49ce03`：gate/OCR sticky 评论加入 workflow API 身份与 own-author 过滤，并补齐分页、重复自愈，针对 R1 P1-1、F-6、P1-5 的幂等边界。
- `fa62bad`、`3c60c59`：历史 artifact 的过期、坏记录、跨 PR 记录逐条隔离并把跳过原因写入 receipt/Summary，针对 R1 P1-2/P1-3、F-7。
- `4b7216b`：把 gate 评论发布拆为 terminal artifact 上传成功后的 `--publish-only` 步骤，针对 R1 P1-4，并配合 per-PR concurrency 针对 P1-5。
- `8f267c2`：恢复 sticky delivery 的失败保护与第二出口诊断，针对 R1 F-8。
- `290bd5e`：删除 GitHub 不支持的 `queue` concurrency 键，并新增全 workflow concurrency 键集合守卫。

没有发现与登记 findings 无关的用户语义、数据模型或部署路径变更。R1 已列 backlog 的 OCR delivery 语义、组合一致性、future-value 约束、信息卫生、guard-of-guard 未被本轮升级为 P1。

### ② 是否新增未经批准的抽象？

否。`HistoryLoad` 只承载历史行与逐条诊断；`--publish-only` 只把已登记的“权威 artifact 上传后再发布”落成最小边界；面板正文 cache 只用于已批准的历史兜底合并。它们分别直接消除 R1 P1-2/P1-3/P1-4 的真实触发路径，没有引入未来价值配置、重试、并发中间件或新的状态机。

### ③ 状态、事实源、fallback 是否无依据增加？

否。权威事实源仍是 `gate-terminal-v1-*` Actions artifact；`_terminal_row` 在 HTTP stub 实测中按 repository、repository id、PR 逐条校验。面板正文仅在 artifact 历史不完整时作为已批准的 cache fallback，且会同时设置 `history_incomplete`、`history_incomplete_reasons`、逐条 `history_skipped_records` 并在面板/Summary/receipt 显示“历史可能不完整”。`HistoryLoad` 不参与 gate verdict，只是发布诊断。

### ④ 是否留下双路径？

没有留下行为上的双发布路径：普通 `aggregate.py` compute 模式只计算并写 `gate-terminal.json`，不发评论；workflow 唯一的 gate 面板发布步骤是 terminal upload 成功后的 `--publish-only`，旧的 `_finish` 内直发路径已删除。`gate-v2.yml` 中没有 `--pr-comment` 残留。

OCR advisory 是独立的、按 reviewer 分桶的既有路径，不是 gate status panel 的新旧并行路径。代码仍保留一个没有生产调用方的旧 `_find_panel_comment` 兼容函数，而真实路径只调用 `_find_panel_comments`；这属于不阻塞的死代码清理项，不构成双路径或 P1。

## Part B：全量复验

### 1. 子进程 compute 模式与 HTTP 边界：五轮、own marker、混入历史

命令：`python3 - <<'PY'` 临时 harness；harness 启动 `http.server.ThreadingHTTPServer`，通过子进程 `python3 .github/actions/gate-aggregator/aggregate.py` 的 `PYTHONPATH/sitecustomize` 将真实 `urllib` 请求重写到本地 HTTP stub；每轮使用真实 argv/env，先 compute 再 publish-only。

输出摘录：

```text
compute_http_calls_before_publish=0
compute_return_codes_skip_fail_fail_fail_pass= [0, 1, 1, 1, 0]
publish_return_codes= [0, 0, 0, 0, 0]
http_boundary_post_count= 1
http_boundary_patch_count= 4
non_owner_marker_patch_count=0
final_panel_history_runs= [101, 102, 103, 104, 105]
final_history_skipped_records= [{"name": "gate-terminal-v1-123-mixed-pr", "reason": "ValueError: gate terminal artifact identity does not match this PR"}, {"name": "gate-terminal-v1-123-bad-record", "reason": "ValueError: gate terminal artifact has an unsupported schema"}]
final_history_incomplete= True
```

结论：五轮在 HTTP 边界只有一次 POST，其余四次是 PATCH；quote-reply 的非 own marker 未被修改；同仓异 PR artifact 与坏 artifact 各自跳过，当前 PR 的五行历史保留，坏记录诊断可见。

### 2. 子进程 `--publish-only` 与 403/500 fail-open

同一 harness 将 stub 的 PATCH 分别返回 403、500，并检查子进程退出码、Summary 与 durable receipt。

输出摘录：

```text
failure_statuses=403,500
failure_publish_exit_codes= [0, 0]
failure_receipts= [{"delivery": "not_created", "reason_code": "http_403", "error_category": "permission_or_rate_limit", "http_status": 403}, {"delivery": "not_created", "reason_code": "http_5xx", "error_category": "server_error", "http_status": 500}]
failure_diagnostics_persisted=true
```

compute 阶段请求数为 0，只有 publish-only 阶段触达评论/历史 HTTP API。403/500 不改变 publish-only 退出码，`gate_v2_status_panel_delivery` receipt 和 Step Summary diagnostic 均落盘。

### 3. workflow schema/actionlint

命令：

```text
/tmp/gate-notify-r2-actionlint.HIdZdQ/actionlint -shellcheck= .github/workflows/ci.yml .github/workflows/gate-shadow-v2.yml .github/workflows/gate-v2.yml .github/workflows/gate.yml
```

输出：无 stdout/stderr，退出码 `0`。本仓 4 个 workflow 的 syntax-check 类错误为 `0`；没有把 `job.workflow_sha` 等 expression 类存量告警误计入。

### 4. 红验抽查

红验均在已有 H2 真修复 commit 之上进行，临时改动未提交，随后按补丁精确还原。

1. concurrency 键集合守卫：临时在 `gate-v2.yml` 的 gate concurrency 加入 `queue: max`，运行 `python3 -m pytest -q tests/test_gate_v2_contract.py::test_all_workflow_concurrency_mappings_use_only_github_supported_keys`，结果为 `1 failed`，失败信息明确为 `unsupported concurrency keys: ['queue']`；还原后该测试 `1 passed`。
2. own-author 过滤：临时把 `_find_panel_comments` 的身份条件替换为恒真，运行 `python3 -m pytest -q tests/test_gate_aggregator.py::test_panel_marker_candidates_require_own_author_and_choose_earliest`，结果为 `1 failed`，断言从期望 `[2, 3]` 变成 `[1, 2, 3]`；还原后该测试 `1 passed`。

最终针对性复验：

```text
python3 -m pytest -q ...concurrency... ...own-author...
....                                                                     [100%]
4 passed in 0.18s
```

### 5. 步骤顺序与残留双路径

workflow 位置核对：`.github/workflows/gate-v2.yml:876-884` 的 `Upload gate terminal envelope` 在 `:885-918` 的 `Publish gate status panel` 之前；publish 条件为 `always() && steps.upload-gate-terminal.outcome == 'success'`，调用含 `--publish-only`。`aggregate.py` 的 `_finish` 已无评论调用；`rg -n -- '--pr-comment' .github/actions/gate-aggregator/aggregate.py` 无输出。

契约测试命令与输出：

```text
python3 -m pytest -q tests/test_gate_v2_contract.py::test_gate_status_panel_publish_happens_after_terminal_upload tests/test_gate_v2_contract.py::test_gate_job_enables_the_sticky_status_panel_without_a_per_run_switch tests/test_gate_v2_contract.py::test_all_workflow_concurrency_mappings_use_only_github_supported_keys tests/test_gate_aggregator.py::test_panel_marker_candidates_require_own_author_and_choose_earliest
....                                                                     [100%]
4 passed in 0.18s
```

### 6. 全量 diff 与 R1 轴表三格抽查

全量 H0..H2 diff 统计为 5 个已有文件：`aggregate.py`、`gate-v2.yml`、面板文档、两个测试文件；`1367 insertions(+), 644 deletions(-)`。已通读聚合器、workflow、文档和新增/调整测试，`git diff --check` 无输出。

三格抽查及非恒真断言：

- 评论所有权格：`test_panel_marker_candidates_require_own_author_and_choose_earliest` 明确放入非 owner marker，断言只选 `[2, 3]`；并完成上面的红验。
- 历史隔离格：`test_history_loader_skips_other_pr_and_bad_records_individually` 明确放入 valid、other PR、坏 schema、expired 四类记录，断言只保留 valid 行并逐条记录三类 skip。
- 发布顺序格：`test_gate_status_panel_publish_happens_after_terminal_upload` 对 workflow 步骤索引和 upload success 条件作断言；并与子进程 compute 的 HTTP 计数为 0、publish-only 才触达 stub 的结果交叉核对。

抽查命令与输出：

```text
python3 -m pytest -q tests/test_gate_aggregator.py::test_panel_marker_candidates_require_own_author_and_choose_earliest tests/test_gate_aggregator.py::test_history_loader_skips_other_pr_and_bad_records_individually tests/test_gate_v2_contract.py::test_gate_status_panel_publish_happens_after_terminal_upload
...                                                                      [100%]
3 passed in 0.08s
```

## 基线、finding 与收敛结论

### 基线

```text
python3 -m pytest tests/ -q
444 passed in 5.87s
```

红验还原后工作树无临时生产改动；所有临时 stub、sitecustomize、OCR background 文件和 actionlint binary 均已清理。

### 新增 finding 分级

- **新增 P1：无。** 按 internal/infra 的 P1 两问复核：本轮未留下一个在真实部署方式下可触发且后果不可接受的新增缺陷。R1 五个 P1 与轮 2 无效键均由静态、测试和降层实测共同闭合。
- **P3-1，非阻塞维护备注：** `.github/actions/gate-aggregator/aggregate.py` 顶部模块 docstring 仍沿用旧的“可做一次可选 PR-comment network call”描述；H1 后 publish-only 实际会执行 identity、comments、artifact history 和 POST/PATCH/DELETE 多个 HTTP 请求。它不改变运行时语义，但会误导维护者，建议后续更新文档。
- **P3-2，非阻塞维护备注：** `_find_panel_comment` 兼容 wrapper 在 H0..H2 后仍存在但无生产调用方；当前行为只走 `_find_panel_comments`。它不构成双发布路径，也没有安全/数据后果，建议后续删除死 helper 及其兼容说明。

R1 backlog 中的 P2/P3 未重复计入，也未观察到升级后果。故本轮不新增 P1、不阻塞合并，收敛计数可记为一轮无新增 P1；下一轮若需要收敛，仍应换证据源或审查视角。
