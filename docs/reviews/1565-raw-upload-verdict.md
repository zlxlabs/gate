# 原始审查数据 Silo 上传独立审查

## 结论

代码有一条 P2 非阻断 finding：新建隔离目录的失败原因被统一误报为“目录已存在”，底层错误也被丢弃。上传隔离、原始字节保留、已知 TOKEN 拦截和 Silo 归档校验在本地 mock 探针中符合卡面不变式。

交付当前阻断：本改动依赖 gate-hub R3 让 primary 直接在 `REVIEW_EVIDENCE_ROOT` 生成并保留原件，也依赖真实 runner 挂载该绝对路径；二者尚未 ready/部署。本审查没有部署、调用真实模型或访问 S3，须在两项依赖就绪后再验真实消费者。

failure-visibility: p2-only

## Finding

### P2（非阻断）：目录创建失败被误报成路径冲突

- **位置**：`.github/workflows/gate-v2.yml:1076-1078`
- **对应不变式**：3（目录未知或写入失败时显式失败）。
- **触发与后果**：`mkdir` 因磁盘空间、I/O 或权限等非冲突原因失败时，`2>/dev/null` 丢弃原始错误，脚本仍报告“Primary raw evidence directory already exists”。步骤会以非零退出，不会复用目录或上传；但值班者无法从日志区分真实冲突与创建失败，可能沿错误方向排查。
- **证据**：用临时 `mkdir` 替身在实际 YAML shell 中注入 `injected ENOSPC` 后，步骤退出码为 1，stderr 只出现“already exists; refusing to reuse”，没有底层错误，且未创建目录、未写入 `GITHUB_ENV`。
- **处置**：非阻断 P2；后续可保留 mkdir 的 stderr，并用不声称特定原因的错误文案。无需重试或备用路径。

## 不变式核对

| 不变式 | 结论 | 证据与边界 |
|---|---|---|
| 1. repo/head/run/attempt 独占持久根子目录；拒绝复用；primary 与上传目录一致；邻居不上传 | 本地通过 | 执行实际 prepare shell 后读取 `GITHUB_ENV`，两个变量都指向按四个标识生成的同一子目录；碰撞场景拒绝复用；相邻 attempt 的哨兵字节未上传。真实 runner 挂载未验证。 |
| 2. 原始 bytes 不改写、不删除；TOKEN 扫描只读；命中已知 TOKEN 则拒绝上传 | 本地通过 | mock 传输读回的源文件 bytes 与上传前逐字节相同；known TOKEN 场景无 PUT/GET，错误不回显 TOKEN。扫描能力仅覆盖 scrubber 已知 TOKEN 类别，不声称识别所有秘密；凭据隔离仍由 hub R2 负责。 |
| 3. primary setup/model 失败仍可归档已有文件；未知目录/写入失败显式失败；沿用不可覆盖 manifest 与 GET SHA 校验 | 本地通过，含上述 P2 | metadata-only 和模型失败模拟都成功归档；隔离 mock 的 PUT 与 GET 成对，源 bytes、manifest SHA 匹配。Silo 实际实现先检查 archive 前缀为空，再为每个对象 PUT/GET 并比对长度与 SHA。没有连接真实 S3。 |
| 4. 只放行固定的新 artifact 名字 | 通过 | 只新增 `primary-review-raw-evidence-v1-<repo>-<40位head>-<run>-<attempt>` 目录名模式；既有未列名目录仍由 archive `put-dir` 拒绝。 |
| 5. 消费者使用 runner 注入的绝对 root；依赖 hub R3 与真实挂载 | 未验证，交付阻断 | 本地用 fake primary 执行实际 YAML 命令，观察其 cwd 与两个 root 环境变量；没有运行 hub R3 的真实消费者，也没有 runner 挂载。 |

## 验证

- 在目标 head `0e7b005ecb1dfdbce8b87e3afeef9243037c3cfd` 的临时快照运行：`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_diagnostics_upload.py tests/test_silo_store.py` → **54 passed**。
- 测试从目标 YAML 读取 prepare/upload shell，运行隔离传输 mock；成功、metadata-only、TOKEN、PUT 失败、远端前缀已占用、本地目录碰撞六种模式均有断言。成功模式检查 producer 源文件 bytes、manifest 和 GET 回读 SHA。
- 额外本地探针以 fake primary 分别模拟 setup 失败和 model 失败，并运行 YAML 中的上传 shell：primary 收到的 `REVIEW_EVIDENCE_ROOT` 与 `PRIMARY_RAW_EVIDENCE_DIR` 相同；fake primary cwd 是模拟的 `GITHUB_WORKSPACE`；实际 `silo_exec.sh` 传入的 argv 为 `put-dir --tier archive --repo-id … --name primary-review-raw-evidence-v1-… --dir <该目录>`。两种失败后分别归档 `invocation.json`，以及 `invocation.json` 加已写的原始输出；PUT/GET 配对，邻居文件未上传。
- 以上是本地模拟，不等于 GitHub runner 的 `if: always()` 调度或实际 hub 消费环境。真实模型、S3、hub R3 与 runner root mount 均未验证。
- OCR 按任务说明沿用已有结果，未重跑。主干 CI 基线不可用（`gh api request failed`），继承红无法判定。
