# G1 第 0 步取证：canonical audit 体积 vs GitHub job outputs 限制，及 secret 屏蔽行为

- Task-Id：G1（dispatch dlg-20261019-015957-6bf03d）
- 性质：只读取证。Silo 侧零写操作（只读 S3 GET/LIST，宿主文件系统只读）；凭据仅在宿主进程环境内使用，未打印、未落盘、未传出宿主。
- 结论：**前提 1 成立（附一条硬性实现约束：output 值必须 base64 编码）**。可以继续实现。

## 1. 体积分布（Silo `d14` 层 primary-audit-v2 实测）

采集方法：宿主 `zlx-vm-work-i5-infra` 上用 gate 仓 `scripts/silo_store.py` 的 S3 客户端
（SigV4，凭据读自 `/mnt/data/silo/secrets/ci-artifacts-*-key.txt`，仅存在于远端进程环境）对
`http://127.0.0.1:9000` 执行 `list_objects_v2(Prefix="d14/")` + 逐对象 GET，统计对象字节长度。
只读操作，2026-10-19 执行。

样本 = `d14` 层全部现存 `primary-audit-v2-*` 对象，共 **2439** 个。窗口说明：d14 层对象即
canonical audit 的读取层；当前 MinIO 实例 2026-09-30 02:06Z 启动（R2 取证 §1.1），本样本覆盖
启动以来全部存活 audit（约 20 天连续写入）。30 天完整窗口受实例生命周期所限不可得，但见余量
分析——结论对窗口外推稳健。

| 分位 | 字节 |
|---|---|
| max | **27 384（26.7 KiB）** |
| P99 | 18 250 |
| P95 | 12 050 |
| P50 | 4 682 |
| min | 1 749 |

- JSON 可解析性：2439/2439 全部可解析（0 个损坏对象）。
- TOKEN 扫描（`scripts/scrub_outbound.py`，与 raw evidence 步骤同一扫描器）：2439/2439 **0 命中**。

### 门槛判定

| 门槛 | 值 | max 占用 |
|---|---|---|
| 任务卡停车线 | 256 KB | **10.7%** |
| GitHub 硬限制（见 §2） | 1 MB | 2.7% |

即使按 P99 之外的最坏外推（findings 数百条、每条 quote 上千字节，audit 到 300 KB），仍在 1 MB
之内；实现中另加双端长度守卫（primary 端发布前检查、gate/ledger 端解码后校验长度标记），
超限 fail-closed，不会静默截断。

## 2. GitHub 官方文档与 actions/runner 源码依据

### 2.1 「每 job 1 MB」的真实出处

官方文档正文没有给出 job outputs 的大小数字；硬限制在 **actions/runner 表达式求值器的内存预算**：

- `actions/runner` `src/Sdk/Expressions/EvaluationOptions.cs` L23-27：
  `Maximum memory (in bytes) allowed during expression evaluation. Default is 1 MB (1048576 bytes) if not specified.`
- `src/Sdk/Expressions/Sdk/EvaluationContext.cs` L24-27：默认写死 `options.MaxMemory = 1048576; // 1 mb`。
- `src/Sdk/Expressions/Sdk/EvaluationMemory.cs` L78-82：超出预算抛
  `InvalidOperationException`（`ExceededAllowedMemory`）→ 表达式求值失败 → step 失败
  （fail-closed 方向：不会静默截断后继续）。

`needs.<job>.outputs.<name>` 的读取就是一次表达式求值，因此每 job outputs 总量的实际约束即
该 1 MB 预算。本卡 audit max 26.7 KiB（base64 后 35.4 KiB）占预算 3.5%。

### 2.2 secret 屏蔽行为：官方确认存在，须用编码规避

- 官方文档（github/docs `content/actions/reference/security/secrets.md`）「Automatically
  redacted secrets」：GitHub 自动屏蔽的对象作用于 **workflow logs**。
- `actions/runner` `src/Runner.Worker/FileCommandManager.cs`：
  - `GITHUB_OUTPUT`（`SetOutputFileCommand`，L328-350）原样存储，**无 masking**；
  - 对比 `GITHUB_STEP_SUMMARY` 路径（L262-266）上传前逐行 `SecretMasker.MaskSecrets` 生成
    scrubbed 副本——output 路径没有对应处理。
- **决定性反例**：`actions/runner#1061`（closed）"Masked output can't be read by downstream
  jobs"，官方维护者 TingluoHuang 答复原文：
  > "We currently don't support having secrets in job outputs."

  即：**output 值若包含本 run 已知 secret 的字面值（workflow secrets 或 `::add-mask::` 值），
  下游 job 经 `needs.*.outputs` 读到的是 `***`——静默数据损毁，官方不修**（文档已同步）。
  同类：#1498、#2316。

### 2.3 对本卡的影响与实现约束

canonical audit 含 findings quote（引自 PR diff）。若某 PR 泄漏了 workflow secret（如
SILO_ACCESS_KEY）且 finding 引用了该字面值，明文 JSON 直写 output 会触发上述屏蔽 → gate 读到
`***` → 判定记录破损。

**硬性约束（实现采纳）**：primary 端发布 output 前**必须**先 base64 编码（`base64 -w0` 单行），
gate/ledger 端解码。base64 打破 SecretMasker 的子串匹配（secret 字面值不再以原文字节出现在
output 值中），数据面保真；日志面 masking 仍然生效（解码后的值打进日志仍会被 mask——保护不变，
数据通道不受影响）。编码后体积放大 4/3，已计入 §1 余量。

### 2.4 残余风险（记录，不阻塞）

- GITHUB_OUTPUT 单值无官方大小文档，机制上受 §2.1 表达式内存预算约束；超过即 step 失败
  （fail-closed），与「截断」不同，不会产生半截 JSON 被当合法记录消费。
- 「GitHub 端 output 往返实测」（含 findings quote 的真实样本在一个真实 run 中走 primary →
  gate/ledger 通道）在本卡合并前的 canary 规模验证中一并完成（执行器无权触发 fleet run，
  所需命令已写入 G1-progress.md 交主脑执行）。

## 3. 裁决

前提 1 成立。体积余量 ≥ 10 倍（对 256 KB 停车线）、≥ 28 倍（对 1 MB 硬限制）；secret 屏蔽风险
经 base64 编码约束后消除官方已确认的唯一损毁路径。继续实现。

## 4. 证据索引

| # | 命令/来源（只读） | 支撑 |
|---|---|---|
| 1 | 宿主只读探针：`silo_store.py` S3Client list+GET `d14/` 前缀逐对象计长 | §1 分布表 |
| 2 | 同一探针对每个对象跑 `scrub_outbound.scrub_outbound_text` | §1 TOKEN 0 命中 |
| 3 | `github/docs` `content/actions/reference/security/secrets.md` | §2.2 |
| 4 | `actions/runner` `EvaluationOptions.cs` / `EvaluationContext.cs` / `EvaluationMemory.cs` | §2.1 |
| 5 | `actions/runner` `FileCommandManager.cs`（GITHUB_OUTPUT vs STEP_SUMMARY 对比） | §2.2 |
| 6 | `actions/runner#1061`（维护者答复）、#1498、#2316 | §2.2 |
| 7 | 真实样本 fixture：`tests/fixtures/primary-audit-v2-live-sample.json`（pass、findings 空、扫描干净） | 跨边界测试输入 |
