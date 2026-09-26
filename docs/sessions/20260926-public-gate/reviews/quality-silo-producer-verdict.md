# 真实质量产物与可信存储边界：独立审查判定

failure-visibility: clean

- Verdict: 通过；无 P1/P2，记录两条不阻塞的 P3 可维护性意见。
- 固定范围：base `c701e367a1680cde85ffdea1f92142d62e4fb885` .. H0 `33ef5dc1935d5b10035d7f49627826803b82aaba`，全量 6 files；未读实现 `report.md` 或其他 reviewer 意见。
- 补丁审查范围：修复增量 `97aba657e90c5fc8ef76740547e84fddb8472ea4` .. `33ef5dc1935d5b10035d7f49627826803b82aaba`，随后复审上述固定全量范围。

## 修复增量四问

1. 登记边界：增量只补真实 producer fixture/roundtrip 证据并改正容量说明，落在卡片既定 tests、fixtures、design/progress 范围；没有扩到 runner/cache 或新事件矩阵。
2. 新抽象：没有新增运行时 helper、接口或共享机制；新增 fixture 路径常量只供本测试读取两个 fixture。
3. 状态与 fallback：没有新增状态、重试或容量 fallback；删除旧的 `<1MB` 断言，文档改为 advisory 限制与失败可见。
4. 双路径：没有新增传输路径；继续只用 job outputs，保留 GitHub artifact 禁令，Silo 写入留在 ledger。

## 全量不变式与证据

- Quality job 不再设置 Silo/AWS 凭据，也不执行 Silo checkout、MagicDNS、上传或网络诊断；持久化只在 ledger。测试集中核对 quality job 的 secret/Silo 引用与 publisher 内容。此结论只覆盖 workflow 配置，不代表 runner 主机、缓存或挂载的物理隔离。
- `tests/test_gate_v2_contract.py::test_quality_publish_and_ledger_consume_roundtrip_fixture_bytes` 调用真实 `.github/actions/pr-size-preflight/preflight.py`，在临时 Git 仓生成二进制、`.pdf` 后缀和中文路径；实际 producer JSON 经 workflow Publish 的 `GITHUB_OUTPUT` 写入及 ledger Download 消费后，以字节和原 producer 文件比对。preflight fixture 的 R1/R2 excluded path 与 producer 输出对照；install fixture 与 workflow 中的 printf 格式产物逐字节一致。
- 身份从 workflow 的 `github.repository_id`、PR head SHA、run ID、attempt 形成；payload 自报 `repository` 留作数据。resolver 不再把旧 Silo listing 当 input 来源。新增回归分别锁定 artifact identity、忽略旧 listing、空 bundle 失败。
- consumer 只 `json.loads` job output，并按固定 mapping 写两个固定文件名；payload 不经 `source`、`eval` 或 shell 执行。单调用的 `github_fact_input_artifact()` 只包住受信身份校验/组装，不构成通用化机制或第二条状态路径。
- primary/OCR job 未在本次 diff 中改变；quality 检查不再依赖 Silo，ledger 的 D1 put/retry 仍 `continue-on-error`，失败保持 advisory。
- 容量文档已收窄：约 1KB fixture 只证明小样本；`excluded_files` 无数量上限，单环境变量可能先受 Linux `MAX_ARG_STRLEN` 限制，GitHub Actions job output 上限按 UTF-16 计。没有无界成功承诺，也没有容量 fallback。GitHub 文档确认每 job outputs 上限 1MB、按 UTF-16 近似计量；文档没有说明 failed-only rerun 是否恢复上次 job outputs。[workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[rerun workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)。
- private/fork/Dependabot/draft→ready 真实消费矩阵、实际 runner/cache 隔离、超大 excluded_files 容量和私有仓回归均未在本轮执行或证明；按卡片留作后续，不作为已部署结论。

## Finding 分级

### P1

无。没有确认的静默错存或崩溃路径。持久化真实故障未在生产环境注入；不以代码形态推定触发。若两次 Silo put 都失败，`silo_store` 会向 stderr 输出明确错误，仍保持 advisory，不足以按本仓 P1 红线定级。

### P2

无。

### P3

- `tests/test_gate_v2_contract.py:3268`：`_repo()` 返回的 `head` 在后续 git commit 后、被覆盖前未读取。仅死赋值，不影响测试结果；可接受不修。
- `.github/workflows/gate-v2.yml:1932,1935`：`INPUT_RESOLVE` 仍被赋空并 export，但 resolver 已不再读取它。仅遗留环境变量，不改变身份或数据来源；可接受不修。

### Unknown

- OCR 提到的 ledger-only failed rerun 是否仍能读取上次 quality job outputs，没有 GitHub 实际 run 证据，官方 rerun 文档也未说明。当前不判 finding、不增加旧 listing fallback；若该恢复路径属于调用契约，后续用真实 attempt 验证。
- 真实 producer/consumer event 矩阵、runner/cache 物理隔离与大 payload 上限未量。不得据本地 fixture 推断这些场景。

## OCR 与 P1 两问

- `ocr-review`：`reviewed_fallback`，模型 `deepseek-v4-flash`，coverage complete；主腿 `backup_quota_exhausted`，DeepSeek 备腿成功。完整 34,030-byte envelope：`/tmp/public-quality-producer-review-ocr-envelope.json`；stderr 原件：`/tmp/public-quality-producer-review-ocr.stderr`。OCR 四条均经人工核验：两条低级维护项列为 P3；“双重持久化失败完全无提示”不成立（底层 put 失败会输出错误）；跨 attempt job outputs 保持状态列为 unknown。
- P1 问一（真实路径是否触发）：未在生产 runner 或真实 Silo 故障路径执行；不得据此声称生产隔离或故障已实测。
- P1 问二（触发后果是否可接受）：双重写入失败会使 advisory D1 输入缺失，但每次失败有 stderr 错误、且本卡不把 advisory 变 required；作为可见的存储失败处理，不满足静默出错 P1。

## 闸、预算与验证

- 只审查固定 SHA；未改 workflow/source/tests，未运行实现测试、部署、ready、merge 或开 PR。
- 实现项目 diff（到 H0）：`393 additions / 137 deletions`，处于主脑明确的 400 最终上限内。原人类 200 与机器 500 冲突，原 `358/137` 并未守 200；本轮 `104/69`（净 +35）超过反馈 raw-50 估计。保留 38 行真实 producer fixture，没有删解释或压格式凑数。
- 新增本 verdict 后执行卡面 `git diff --check c701e367a1680cde85ffdea1f92142d62e4fb885..HEAD`；仅 verdict 文件进入本次提交。
- 后续节点仍是卡 B/F 的真实 runner、公开事件矩阵与消费环境验收；本 verdict 不代表 workflow 已发布或 v2 已提升。
