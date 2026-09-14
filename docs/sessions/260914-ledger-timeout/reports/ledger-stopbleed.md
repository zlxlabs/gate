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

## 第二轮修复：terminal 上传失败仍染红 gate

验收第一轮发现首传 terminal artifact 没有 `continue-on-error`，且 panel 发布只看首传 outcome，导致重试成功也无法救回 gate job 或发布面板。本轮只修改 `.github/workflows/gate-v2.yml` gate job 与其契约测试：

- `.github/workflows/gate-v2.yml:1260-1280`：首传和重传均 fail-open；重传步骤 id 为 `retry-upload-gate-terminal`。
- `.github/workflows/gate-v2.yml:1281-1282`：`Publish gate status panel` 改为首传或重传任一成功即发布。
- `.github/workflows/gate-v2.yml:1328-1341`：复用现有诊断 warning 步骤；两次 terminal 上传均失败时额外留下 `::warning::`，并保留诊断上传失败 warning。
- `tests/test_gate_v2_contract.py:907-925,1575-1608`：恒等断言首传 fail-open、重传 id、两次 outcome 的发布条件，以及 terminal 双失败 warning 的条件/env/文案。
- 未修改 ledger job、`build_ledger.py` 或 `Aggregate required verdict`。

本轮验证：

- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q` → `994 passed in 86.38s (0:01:26)`。
- `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py tests/test_gate_v2_diagnostics_upload.py` → `136 passed in 29.16s`。
- `python3 scripts/check_pinned_uses.py` → 退出码 0，9 个 live workflow/action metadata 文件检查通过。

## 第三轮修复：历史降级可辨认，预算覆盖请求窗口

- Task-Id：卡面未填写；审查对象为 `gate-20260914-04`
- Dispatch：`dlg-20260914-091330-c5569e`
- Executor：Codex / implementer
- Root-cause group：止血改动把“失败”降级成“继续”，但没有把“这一轮降级过”变成 ledger 数据或时间上的可判定事实
- Introduced-by-commit：`87b3ce8`（`fix(gate-v2): stop ledger history download from poisoning gate`）
- Open findings：F-1（部分/空历史静默驱动 comparison）、F-2（历史预算可能越过 step 的 3 分钟）

### 完成条件对照

1. **F-1 / 历史完整性三态已进入条目数据。**
   `.github/actions/review-ledger/build_ledger.py` 新增顶层 `history_status`，取值为
   `complete`、`incomplete`、`none`。`fetch_prior_entries` 在完整读取且有条目时返回
   `complete`，完整读取但没有历史时返回 `none`；预算前置截断返回已下载部分并标记
   `incomplete`，读取异常由 `main` 的既有降级路径标记 `incomplete`。PR state comment
   里携带的有限游标在 artifact 没有历史时也会把状态提升为 `incomplete`，不会伪装成完整
   artifact 历史。

   `build_entry` 只在 `history_status=complete` 且存在无冲突上一条时生成原有
   `new_head` / `same_head_rerun` comparison，并附 `authoritative=true`；`none` 保留
   `first_review` 但附 `authoritative=false`；`incomplete` 生成
   `history_incomplete` 且附 `authoritative=false`，不写
   `persistent_finding_ids`、`resolved_finding_ids`、`new_finding_ids`、
   `missing_finding_ids` 或 `appeared_finding_ids`。因此只有完整历史能产生权威的 finding
   差异结论。

2. **F-2 / 历史阶段最坏耗时已被代码兜住。**
   历史请求单独使用 10 秒超时、2 次尝试、1 秒退避，单次请求最坏为
   `10×2+1=21` 秒；历史预算仍以 90 秒为默认值，并对环境变量上调做 90 秒硬上限。
   在 artifact 列表请求及每次 archive 请求前都检查“已用时间 + 21 秒”是否仍在预算内。
   默认最多一个列表请求加三个 artifact 下载，最坏总网络窗口为 `4×21=84` 秒，低于
   `timeout-minutes: 3` 的 80%（144 秒）。预算不足时在进入下一次不可中断请求前返回
   `(已下载条目, incomplete)`；`write_ledger` 仍在 `main` 中无条件位于其后。异常路径也
   由 `main` 继续构造并写出当前条目，再尝试后续评论更新。

3. **新增测试及约束力。**
   `tests/test_review_ledger.py` 新增/补强：

   - `test_history_status_has_three_states_and_only_complete_history_can_compare`：完整、
     不完整、无历史各一例，断言条目标记、comparison kind、权威位和降级时不出现 finding
     差异字段；在 Base 实现上因 `history_status` 不存在而失败。
   - `test_history_fetch_retry_exhaustion_stays_below_step_budget`：fake HTTP 按每次传入的
     timeout 推进可控时钟，列表请求重试到成功、archive 请求重试到底；当前实现耗时为
     `42` 秒且断言 `<144`，Base 实现耗时为 `186` 秒并失败。
   - `test_main_writes_current_row_when_history_is_incomplete`：无条目和已有部分条目两
     个参数例，断言当前 `run_id=10` 行仍写出且带 `history_incomplete`；Base 实现无法消费
     状态元组而失败。

   已有的 budget partial-return 测试也改为断言返回状态为 `incomplete`；HTTPError 主流程
   测试改为断言输出行携带 `history_status=incomplete` 和非权威 comparison。

4. **跨仓消费点核查。**
   `gate-hub/scripts/review/ledger_reader.py:40-56` 的
   `read_from_json_string` 只做 JSON 解析；`gate-hub/scripts/review-ledger-report.py:147-163`
   只校验显式存在的 receipt block、review attempts 和 refutation 结构，没有顶层闭集
   whitelist，因此新增 `history_status` 与 comparison 字段会随 dict 保留，不会因未知键
   报错或被反序列化丢弃。report 的 comparison rollup
   (`review-ledger-report.py:414-422`) 只计 `new_head` 和 `same_head_rerun`，不会把
   `history_incomplete` 的字段计入 persistent/resolved/new。

   `gate-hub/scripts/review-ledger-replay.py:252-268` 同样通过 `.get()` 读取 comparison
   和 review；未知 comparison kind 不产生 finding 差异计数。它仍会按当前 review 的
   `pass`/`fail` 状态处理当前轮，这是当前轮评审本身的收敛输入，不是把不完整历史当作
   comparison 结论。`build_ledger.py` 自己读取历史条目时也只按 `.get()` 取既有字段，新增
   字段不会破坏回读。

5. **既有测试与边界。**
   没有删除或放宽既有测试，也没有修改原有完整历史 comparison 的 finding 断言
   （`new_head`、`same_head_rerun` 仍原样验证）。必要的既有断言改动逐条如下：

   - `_run_ledger_main` 的历史 fake 从旧列表返回值改为生产函数的新 `(entries, status)` 契约；
     这是为保证主流程实际消费并传递三态，而非放宽行为。
   - artifact 读取测试由直接比较列表改为解包后同时断言 `complete`/`none`，budget 测试
     同时断言 `incomplete`，以锁住 producer 的状态事实。
   - HTTPError 测试从只断言 warning/一行，增强为断言条目的非权威降级字段，直接覆盖 F-1。
   - API fake 接受新增 timeout/attempts/backoff 参数；terminal entry 的精确字段集合
     增加 `history_status`，因为它是所有生产 ledger 行的新跨边界字段。

   未修改 `.github/workflows/gate-v2.yml`、legacy `.github/workflows/gate.yml`、verdict
   聚合或上传步骤；step 的 3 分钟超时与 ledger job 的 `continue-on-error: true` 保持不变。

6. **验证结果。**

   - Base 红验使用仓库实际可解析的 Base SHA
     `8b657fec36b247e7fe390673443dbd0704161270`（任务卡给定字符串末尾多一个字符，首次
     临时树创建未执行，随后已纠正）。三条新增测试在 Base 实现上均为非零：
     `red_verify_statuses=1,1,1`。
   - 定向：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_review_ledger.py tests/test_gate_v2_contract.py`
     → `360 passed in 26.89s`。
   - 全量：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`
     → `998 passed in 86.55s (0:01:26)`。
   - pin：`python3 scripts/check_pinned_uses.py` → 退出码 0，
     `OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative`。
   - `git diff --check` → 通过；本轮代码/测试 diff 为 211 行（84 insertions/19 deletions
     in `build_ledger.py`, 127 insertions/12 deletions in `test_review_ledger.py`），低于
     `Diff-Lines-Target: 520` 与 `Diff-Lines-Hard: 760`，未产生允许范围外文件改动。
