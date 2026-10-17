# A1 独立审查结论

审查对象：`1674eef3d5af6676937cbd81d1e547223fe3f6eb..33baebe76323b05427c948d973dc4ba6d1f94951`（冻结 SHA）。风险等级：personal。

failure-visibility: clean

## 结论

未发现违反本卡不变式的 finding。P1/P2/P3 findings 均为 0；交付不阻断。审查只覆盖本次冻结差异；compact record 缺失不在本卡范围内。

## 不变式核对

1. **每 run 独立归档，仅允许 raw ledger 写入。** `scripts/silo_store.py:56-76` 将 archive 名称限制为 `codex-review-ledger-v2-<repo_id>-<40位小写SHA>-<run_id>-<attempt>`，并只接受 `ledger.jsonl`；`cmd_put` 限制单文件，`cmd_put_dir` 拒绝 archive（535-577）。`.github/workflows/gate-v2.yml:2675-2687` 用 repo ID、PR head SHA、run ID、attempt 生成归档名。`tests/test_silo_store.py:80-110, 737-819` 覆盖 key 限制、写入限制和实际对象 key。未发现可写入其他 run key 的路径。
2. **保留 d30 与 latest 语义。** 原有两个 d30 写入步骤保持不变（`.github/workflows/gate-v2.yml:2653-2673`）；latest 仍以 PR 为名写 d30。`tests/test_gate_v2_contract.py:1991-2083` 锁定 latest key、tier、顺序和 job 语义；端到端测试同时断言旧 d30 与 archive 的请求 key。
3. **归档失败可见，ledger job 仍非 required。** archive step（`.github/workflows/gate-v2.yml:2675-2691`）对上传非零退出写 `::error::Permanent review ledger archive upload failed` 并退出 1，没有 step 级 `continue-on-error`；ledger job 仍 `continue-on-error: true`（2184-2188）。contract test（`tests/test_gate_v2_contract.py:1991-2083`）锁定 job 语义、archive 条件、错误注解及无 step 级吞错。另在冻结 head 临时 worktree 用退出码 23 的假 `SILO_EXEC` 执行 workflow shell，实际得到退出码 1 与 error annotation；原始结果见派发报告。
4. **真实 producer 字节穿过序列化边界。** `tests/test_silo_store.py:737-819::test_gate_workflow_dual_writes_the_real_ledger_bytes_to_d30_and_archive` 执行真实 `build_ledger.py`，再执行两个 workflow shell 片段和真实 `silo_exec.sh`/`silo_store.py`，由本地 HTTP 记录器断言实际 PUT 的对象路径与 producer 输出字节完全相等。该测试在本机 fake S3 上通过；它不证明隔离中的真实 CI 凭据已具备 Put/Get 权限。
5. **不设 TTL、不删历史、不覆盖其他 run。** 本次差异没有增加 TTL、删除或覆盖逻辑；archive 使用独立前缀，key 含 run ID 与 attempt（`.github/workflows/gate-v2.yml:2678`）。端到端测试核对归档 key。对象存储服务端实际 lifecycle 配置不属于本次代码差异，本审查未验证该外部状态。

## 熵增检查

`ARCHIVE_TIER` 常量复用于 tier 白名单、key 校验与写入限制；用途限于本次 archive tier，没有新增通用包装层。`build-review-ledger` step ID 是 archive 条件所需的 outcome 锚点。两者均直接服务本卡行为，没有发现无第二消费者的未来通用化。

## 验证

- 冻结差异：`git diff --check 1674eef3d5af6676937cbd81d1e547223fe3f6eb 33baebe76323b05427c948d973dc4ba6d1f94951`，退出码 0，无输出。
- CI 同款全量测试：`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`，临时 worktree 退出码 0，`1692 passed in 267.78s (0:04:27)`。
- workflow pin 检查：`python3 scripts/check_pinned_uses.py`，临时 worktree 退出码 0，`OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative`。
- 基线行为红验：在 base 临时 worktree 断言有效 archive key 可构造，旧实现按预期拒绝 `archive` tier；原命令与原始输出见派发报告。
- OCR 前置扫描：`reviewed_fallback`、`coverage=complete`、`findings=[]`；主腿未分类失败，备用腿成功，finding verifier 为 `skipped`（无 findings 可核验）。

## 范围与限制

审查期间没有修改实现、测试、workflow 或 policy。未访问真实 CI Put/Get 权限；本机 fake S3 结果不作为该权限的通过证据。主干基线作业名/失败步骤基线不可用，因此继承红无法判定；本次本机全量测试没有新红。
