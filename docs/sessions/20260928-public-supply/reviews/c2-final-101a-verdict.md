# C2 Final Review Verdict — 101a

冻结审查范围：`e7a57ece42d4b6d87d7eb27668d167d376c388e0..101a7ba3a3d5ca66ab2e602f2f644e368f2b92e7`。工作树干净。

修订增量 `6aad868b05c13bb5269da7fa09c1565d16f8d6ba..11a943cf306e521bf18958a8b2375914fc1903b8` 已先行登记：只改 `README.md`、C2 实施记录和 `tests/test_gate_caller_contract_guard.py`，共 29 行新增、13 行删除。没有应用源码、状态、fallback 或新抽象变更；增量没有引入第二条凭据路径。

failure-visibility: p2-only

## 本轮已穷举的不变式轴

1. **Required Gate 与 disposition caller secret 边界**：Required Gate 模板只映射 `FEISHU_CI_WEBHOOK`；disposition 模板仍映射两项 legacy Silo keys；没有 `secrets: inherit`。
2. **callee 与凭据来源**：gate-v2 的两个 Silo secrets 仍为 optional；只有 Gate repo ID `1295374164` 选择 `managed-profile`；其他 caller 的消费 jobs 仍映射 legacy AWS env。
3. **README/template 的范围表述**：Gate 专属 managed source 没有被推广成所有仓已有权限或 profile；没有 legacy keys 的其他 public caller 仍被描述为 Silo unavailable。
4. **源码、生产状态与事件验收**：README 区分源码合并、生产 profile/权限验证、真实事件矩阵；没有把 Draft 检查或源码合并描述为端到端完成。
5. **测试与变更预算**：新增测试解析实际 YAML 模板和 gate-v2 workflow；既有 non-Gate legacy mapping/optional-secret 断言仍在 `tests/test_gate_v2_contract.py:598-626`；未新增通用表达式求值器或 fallback。修订增量中测试与普通文档新增均未超过 40 行，应用源码改动为 0。
6. **可检索性与 SQL**：未发现新增导出符号、运行时日志字符串或新增 SQL 写操作。

旧 verdict 仅视为历史记录；未沿用其结论或实现记录中的作者论证。既有 OCR complete 结果没有用于缩小本轮审查范围。

## Finding

### P2 — README 把 quality job 的 profile 文件隔离写成已确认事实（文档）

**位置：** `README.md:39`

**不变式：** 中性 spec 要求生产 profile/权限状态与源码状态分开核验，不能把未验证的权限状态写成完成事实。

**问题与触发场景：** README 写明 quality job“不可见该档案”。当前 workflow 只显示 quality 使用 `ci` runner label（`.github/workflows/gate-v2.yml:313`），消费 Silo 的 jobs 使用 `codex` label（同文件 `:671`）；YAML 没有证明两组 runner/文件系统互斥。若 profile 安装在与 `ci` runner 共享的主机或 runner 账户可读的位置，quality 执行 caller 脚本时可能读取 `/opt/review-auth/silo.json`。现有 quality 测试只锁定不映射 Silo keys、不调用 Silo（`tests/test_gate_v2_contract.py:3296-3311`），没有验证文件系统可见性。风险按 personal 等级为 P2；本 finding 不断言生产环境已发生暴露。

**有界修复任务：**

- **目标：** 让 README 只陈述源码能证明的边界，并把 profile 文件隔离留待生产验证。
- **范围：** 仅修订 `README.md:35-40` 的 quality/profile 表述；保留“quality 不接收 Silo keys、不执行 Silo 操作”的已测试契约。生产隔离经真实 runner 权限验证后再作确定性声明。
- **验证：** 检查修订后的 README 不再把文件不可见写成已验证事实；确认现有 quality 静态契约仍覆盖 key/env/Silo-call 边界。生产 profile 可见性须由后续实际 runner 权限验收证明。

## 验证盲区

本轮按任务约束未运行测试或模型。测试报告中的通过记录未作为本轮证据。生产 profile 安装、runner 文件权限和真实 private/public/fork/Dependabot/draft→ready 矩阵未验证；这些状态仍需独立验收。

## 结论

需修复后复审
