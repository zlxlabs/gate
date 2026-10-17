failure-visibility: clean

## 裁决
**PASS；本轮没有发现违反卡面不变式的问题。**审查范围固定为 `fed4ae1079f002b2cf5c73f454dfb026e2c633d5..92ee2182e844ab5a4b3abbfcad55acf5818f4b48`，risk-tier 为 personal。只审查本轮 diff 与直接调用路径。

## 不变式核验
1. **来源 attempt 与身份（规格 1、2）**：`.github/workflows/gate-v2.yml` 的 resolver 用仓库 ID、head SHA、run ID 和当前 attempt 前缀调用 `silo resolve`；`scripts/silo_store.py` 只选择不大于当前 attempt 的完整数字后缀。归档步骤从各自 resolver 输出取 artifact 名，因此 audit 和 terminal 都保留来源 attempt；terminal 名含 run ID。目标测试以来源 attempt 小于当前 attempt 的输入断言没有改存为当前 attempt。
2. **名称与单文件（规格 2）**：`scripts/silo_store.py:68-89` 只允许三种完整 per-run artifact 名与固定 suffix 配对；`cmd_put` 要求 archive tier 只有一个 `--file`，`put-dir` 禁止 archive。新增 audit/terminal 步骤各只传一个约定源文件。
3. **写后读回、错误可见（规格 3）**：两个新增 archive 步骤都在 PUT 后 GET 精确 key，再以 `cmp -s` 比较源与读回字节；命令失败或字节不同会输出 `::error::` 并以非零退出。`set -euo pipefail` 未吞错。
4. **步骤独立性（规格 4）**：实际顺序是生成并上传原始 ledger、上传 ledger archive，然后 audit archive，最后 terminal archive。audit 与 terminal 的下载和归档均显式 `always()`；audit 归档失败不会跳过后续 terminal 归档。ledger job 现有 `continue-on-error: true`，归档失败不改变 gate 的 required 判定。
5. **字节与日志（规格 1、3、5）**：新增测试从 canonical audit fixture 的文件字节读取并核对实际 HTTP PUT body；terminal 由真实 `aggregate.py` 子进程生成，再核对上传和读回字节。Silo 命令只打印对象 key/目标路径，读回比较使用静默 `cmp -s`，未发现原始审查内容进入公开日志的路径。

## 验证记录
- 在 `scratch-worktree.sh` 固定 `92ee2182e844ab5a4b3abbfcad55acf5818f4b48` 运行 6 个归档与 workflow 节点：**6 passed in 13.24s**。覆盖名称/suffix 白名单、单文件约束、真实 workflow PUT/GET 字节、403 读回拒绝、字节损坏、缺失源文件，以及 audit 归档失败后 terminal 仍可归档。
- 同一固定 SHA 运行 3 个 resolver 节点：**3 passed in 0.54s**。覆盖回退到前一 attempt、选择当前 attempt、拒绝未来 attempt。
- `git diff --check fed4ae1079f002b2cf5c73f454dfb026e2c633d5..92ee2182e844ab5a4b3abbfcad55acf5818f4b48`：通过。
- OCR：`reviewed_fallback`，profile `deepseek`，完整覆盖，findings 为空；主腿返回 `primary_marker_active(primary_failed_unclassified)`。备用腿结果只作为补充证据，本结论仍基于固定范围全量人工审查与定向运行结果。

## Finding 与边界
- Findings：无；无阻断项、无 P2 项。
- 无依据的扩范围建议：无。
- 本次 verdict 提交新增测试文件：无；未修改实现、测试、workflow 或卡面。
- 主干基线查询在派发时失败（`gh api request failed`），继承红未能判定。
