# r1 独立审查结论

审查范围固定为 `8675302d82963b46cfbfe40ab6ae967f33e92384..214fb0beba3419e07ac2b42dbf331541f253b9c2`，risk-tier 为 `personal`；未审查之后提交。结论：**不通过，存在 1 个 P1；另有 2 个非阻断 P2/P3 记录**。

## 发现清单

### F-1（P1）：原始 `abandoned` 没有被保留

- 文件：`.github/workflows/gate-v2.yml:1205-1206`、`:1294-1295`、`:1381-1382`；对照 `.github/actions/gate-aggregator/aggregate.py:422-440`、`:2034-2083` 与 `.github/actions/review-ledger/build_ledger.py:533-550`。
- 违反不变式 1、4。三处只把 `PRIMARY_RESULT_RAW` 放进 step 环境；实际 argv 只传 `$PRIMARY_RESULT`，resolver 只读取 `os.environ["PRIMARY_RESULT"]`。`PRIMARY_RESULT_RAW` 在运行时代码中没有消费者。
- 触发路径：真实 run `34740209146` 的 observed raw=`abandoned`、quality=`success`、review_expected=`true`；补丁路径把它变成 `cancelled` 后，aggregator 写入的终态字节只有 `primary_result=cancelled`，ledger 行只有 `review.status=not_run`、`verdict/result=null`。两者都不能区分“原始 abandoned”与原生 cancelled。
- 后果：显式要求保留的上游观测值在 producer→argv→terminal/ledger 边界丢失，事故记账与后续诊断失去关键事实。个人档 P1 的真实触发与不可接受后果问题均成立（数据丢失）；不构成放行，但仍阻断本轮。

### F-2（P2，接受不修，不阻塞）：Publish 面板增加了无效的第三份状态表达式，测试未锁定它

- 文件：`.github/workflows/gate-v2.yml:1289-1295`；`.github/actions/gate-aggregator/aggregate.py:2034-2048`；`tests/test_gate_v2_contract.py:439-447`。
- 违反不变式 4 及熵增审查要求。`Publish gate status panel` 与设计的“两入口”之外又复制 raw/normalized env，但 `--publish-only` 实际从已上传的 terminal 读取，忽略这些 CLI 输入；契约测试只遍历 Aggregate 与 resolver，不包含 Publish。
- 触发路径：未来仅修改 Publish 块的 abandoned 映射或 raw 绑定时，当前测试仍绿，面板继续以 terminal 投影运行，形成未被发现的双路径漂移。当前三处字面值已核对一致，故不是当前放行缺陷；应删除死配置或把三处复制关系机械锁死。

### F-3（P3，接受不修，不阻塞）：真实夹具错误记录 quality job ID

- 文件：`tests/fixtures/primary-abandoned-run-34740209146.json:30-35`。
- 违反不变式 4 的证据边界要求。夹具记录 `103678552493`，但 GitHub attempt-1 jobs API 的实际 `gate / quality` 是 `103678532493`；primary、ledger ID 和时间线均与 API 相符。
- 触发路径：未来按夹具的 quality job URL/ID 回查会落到错误对象或查无此对象，导致对质量输入生产边界的判断失真。现有新增测试只消费 `observed_env` 与 identity，未触发该错误，因此无当前运行时影响。

## 已确认的正确行为

- resolver 的规范化值为 `cancelled`，未知值仍在 `RESULT_DOMAIN` 外 fail-loud；缺 audit 只在规范化 cancelled 且 review_expected=true 时放宽，quality 成功的 input 与 gate terminal 仍必需，success/failure 仍要求 audit。
- 真实脚本级探针确认 resolver 输出字节为 `input_artifact_id=101`、空 `audit_artifact_id`、`terminal_artifact_id=201`；aggregator 返回 1 并写 `gate_result=unavailable`、`reason_code=primary_cancelled`；build_ledger 返回 0 并写 `review.status=not_run`、`verdict/result=null`。缺 input/terminal 的反向用例均失败。
- 三处 Publish/决策 env 的 raw 与 normalized 字面式已逐一核对；Publish 面板实际消费的是 terminal，故其当前显示跟随 normalized cancelled，不会放绿。
- 终态写入位于 `aggregate.py:2188-2193`，在本路径没有外部持久写入后才原子写 terminal；terminal 绑定 repository/head/run_id/run_attempt/pr，artifact 前缀和下载选择也保留 identity 与 attempt 上限。draft/fork/hosted/classify guard 未被本范围改动。

## 验证证据

- base 红验临时 detached worktree `/home/zlx/scratchpad/gate-abandoned-red-20260916-02`：两条指定 TDD 测试 `2 failed, 141 deselected`，失败分别是 base 缺规范化表达式与 resolver 仍强制 audit；非导入错误。
- HEAD 定向命令：`2 passed, 141 deselected`。
- 边界回归：`tests/test_gate_aggregator.py` 取消/非法 primary 选择 `7 passed, 246 deselected`；`tests/test_gate_v2_contract.py` 的 domain、缺 input/terminal 与 abandoned 选择 `26 passed, 117 deselected`。
- `git diff --check 8675302d82963b46cfbfe40ab6ae967f33e92384..214fb0beba3419e07ac2b42dbf331541f253b9c2` 通过。
- OCR 前置扫描：`status=reviewed`；其 raw 死状态、Publish 覆盖缺口候选经上述 producer/consumer 探针确认。其“夹具泄露”候选不成立：`zlxlabs/agent-config` 当前为 private。

## 未知与不可验证项

- GitHub run/job 元数据能确认真实结论、steps 数与时间线，但不能从 API 结构化字段独立重放 ledger 当时收到的 raw env；`abandoned` 取值以任务提供的 observed 证据与夹具为准。
- 未在 GitHub 上重跑或发布 workflow；本审查仅验证固定提交和本地真实脚本链路。未运行全量 suite，按任务要求只执行定向/边界测试。
