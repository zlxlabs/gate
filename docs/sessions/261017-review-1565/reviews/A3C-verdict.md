failure-visibility: p2-only

## Verdict

1 条 P2 非阻断意见；未发现违反数据保全不变式的 P1。可继续合并，P2 可顺手收敛。

## Finding

### P2 — `review_record_archive_key` 是单用途 helper

- **依据**：卡面方法要求新增机制有现存消费者；仓库规则要求新增 helper 有已发生的失败或第二个调用方。
- **证据**：固定 head `7a0a51d` 中 `scripts/silo_store.py:583` 定义该 helper，`scripts/silo_store.py:630` 是唯一调用；`git grep -n review_record_archive_key <head> --` 只返回这两行。没有已发生的失败或第二个消费者作为拆分依据。
- **触发与后果**：每次运行 `put-review-record` 都经过这个单调用边界；它不改变当前 key 结果，但多维护一个只服务此命令的内部入口。
- **建议**：把验证和 key 拼接内联到 `cmd_put_review_record`；若存在既有故障或第二个真实消费者，再保留独立 helper。
- **阻断**：否；仅维护性意见，不影响数据写入正确性。

## 不变式核对

- **1 — sidecar 与旧契约**：通过。旧 action 输入省略 `record-output` 时 ledger-only 路径仍成功；已有 producer 测试比较 stdout 与 ledger 文件，并确认旧 row 不变。live action 以真实 YAML `run` body 和 action 参数生成 sidecar，producer 字节原样出现在 fake HTTP Silo PUT body；`provenance.source_key` 指向原 d30 ledger。
- **2 — key 身份**：通过。repo 两段、安全字符、PR、40 位小写 SHA、source allowlist、run/attempt 正整数和 bool 排除均在 `connect()` 前校验。窄测试覆盖坏身份拒绝联网。
- **3 — 保全写入**：通过。精确目标存在时先拒写（同字节也拒绝）；空目标只执行 List、PUT、GET，按长度与 SHA 校验；失败不删本地源，无 DELETE、GC、latest 或重试路径。
- **4 — workflow 顺序与失败条件**：通过静态核对。旧 ledger、latest、raw ledger、primary audit、terminal 归档均排在新 writer 前；新 step 的条件是 `always() && build outcome == success`，所以旧归档失败后仍可尝试新 record，build 失败时跳过缺失 sidecar。实际运行了 action `run` body 与 writer CLI；未模拟 GitHub 托管 runner 的表达式调度器。
- **5 — 范围与权限**：本地 fake HTTP 验证通过；没有生产 Silo 写入、真实凭据或 IAM 修改。正式 IAM 权限仍待 canary 验证；本卡没有实现 reader 聚合。

## 验证

目标提交固定为 `15f984a81ed184e64fb36d99f171770bf4b05a80..7a0a51dbbd39f1ff1b093579431fb90b64b80920`。在目标 head 的临时 detached worktree 执行：

```text
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_silo_store.py::test_gate_workflow_writes_live_review_record_bytes_to_versioned_silo_key tests/test_silo_store.py::test_put_review_record_rejects_bad_payload_before_connect tests/test_silo_store.py::test_put_review_record_refuses_identical_existing_key tests/test_silo_store.py::test_put_review_record_http_failure_keeps_source_bytes tests/test_review_ledger.py::test_live_record_cli_writes_contract_fixture_and_keeps_legacy_row
17 passed in 0.69s
```

主干基线作业不可用，继承红未能判定。按卡面要求没有重跑 OCR。审查结论仅基于固定 diff、仓库规则和上述本地探针；Silo IAM 权限需由 canary 确认。
