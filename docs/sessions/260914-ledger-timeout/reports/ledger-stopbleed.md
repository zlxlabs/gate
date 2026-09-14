# Ledger 下载止血与 gate 诊断上传验收报告

- Task-Id：
- Dispatch：dlg-20260914-082948-baa747
- Base commit：8b657fec36b247e7fe390673443dbd0704161270
- Branch：card/gate-20260914-04
- Fixes-Issue：#167

## 完成条件对照

1. `.github/actions/review-ledger/build_ledger.py:778,796-798` 的
   `LEDGER_ARTIFACT_NAMES` 只含 `codex-review-ledger-v2`，查询每轮只发一个 v2
   URL；`.github/workflows/gate.yml` 未改。`tests/test_review_ledger.py:739-763`
   捕获真实 HTTP opener URL，锁定只查 v2；`831-847` 覆盖 v1-only 读不到、both
   只取 v2；`866-888` 用 v2 artifact 继续锁定 state comment 行为。
2. `fetch_prior_entries` 的默认 `artifact_limit=3` 位于
   `.github/actions/review-ledger/build_ledger.py:781-785`，真实 URL 测试同时锁定
   `per_page=3`。
3. `.github/actions/review-ledger/build_ledger.py:786-821` 增加
   `time_budget_seconds`，默认读取 `LEDGER_HISTORY_BUDGET_SECONDS`（默认 90 秒，
   变量说明在 `:788`），每份下载并解析后检查耗时；超预算保留已取条目并输出
   `::warning::`。`tests/test_review_ledger.py:850-863` 锁定触发预算时只返回已下载部分。
4. 下载函数仍在坏 zip 等错误处抛出；既有坏 zip 契约为
   `tests/test_review_ledger.py:728-736`。`main()` 在
   `.github/actions/review-ledger/build_ledger.py:960-963` 单独捕获历史读取异常，
   输出 warning 并以空历史继续；`tests/test_review_ledger.py:766-788` 锁定 HTTPError
   时 exit 0、产出当前 JSONL 且有 warning。
5. 三项新增/更新行为测试均非恒真：时间预算部分返回（`850-863`）、HTTPError
   main 降级（`766-788`）、真实 HTTP 请求 URL 不含 v1 查询（`739-763`）。
6. `Build v2 review effectiveness ledger` 在
   `.github/workflows/gate-v2.yml:1596-1598` 声明 `timeout-minutes: 3`；契约为
   `tests/test_gate_v2_contract.py:1013-1020`。
7. ledger job 在 `.github/workflows/gate-v2.yml:1333-1337` 声明
   `continue-on-error: true`；契约为 `tests/test_gate_v2_contract.py:968-970`。
8. `tests/test_gate_v2_contract.py:1053-1056` 对 job 级 continue-on-error 做恒等断言，
   期望集合为 `{"ocr", "ledger"}`。
9. 独立契约 `tests/test_gate_v2_contract.py:1013-1020` 断言构建步骤存在整数
   `timeout-minutes` 且不超过 3。
10. `.github/workflows/gate-v2.yml:1317-1331` 给诊断上传加步骤 id、
    `continue-on-error: true`，保留 `if-no-files-found: error` 以让缺文件进入可观测
    failure 分支，并在上传步骤失败（涵盖缺文件和上传失败）后执行显式
    `::warning::`。`tests/test_gate_v2_contract.py:1575-1592` 锁定两项行为。
11. `.github/workflows/gate-v2.yml:1260-1279` 增加一次 terminal envelope 重试，
    采用既有上传重试约定：失败条件、`continue-on-error`、相同 artifact/path/retention
    和 `overwrite: true`；契约为 `tests/test_gate_v2_contract.py:907-916`。
12. gate 诊断上传的 fail-open 与 warning、terminal 重试均由
    `tests/test_gate_v2_contract.py:907-916,1575-1592` 锁定；`Aggregate required verdict`
    步骤及其输入/计算未改。
13. 全量验证：

    `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`

    结果：`994 passed in 92.42s (0:01:32)`。

14. pin 验证：

    `python3 scripts/check_pinned_uses.py`

    结果：退出码 0，`OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative`。

## 额外定向验证

`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_review_ledger.py tests/test_gate_v2_contract.py tests/test_gate_v2_diagnostics_upload.py`

结果：`358 passed in 24.69s`。`git diff --check` 通过；代码/测试改动为 154 行新增、24 行删除，未触碰 `gate-aggregator/aggregate.py`、legacy `gate.yml` 或 quality/primary/classify/ocr job。

## PR 正文必写说明

第 7/8 条有意推翻既有“job 级 `continue-on-error` 集合恒等于 `{"ocr"}`”约定：ledger 只负责独立历史台账持久化，不参与 gate 放行（`ledger` 不在 gate 的 needs，`Aggregate required verdict` 也不读取 ledger），但其慢下载/上传失败会把整个 workflow run 染红，导致 PR 页面无法区分门禁失败与非放行台账故障；因此 ledger job 纳入 fail-open 集合，同时保留步骤级 3 分钟预算和 warning/产物观测信号。
