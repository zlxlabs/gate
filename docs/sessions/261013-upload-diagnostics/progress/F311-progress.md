# F311 卡 · Silo 请求失败最小脱敏诊断

## 段 1 · pickup 与设计

**当前阶段**：implementing

**本段结论**：`f4213194` 的 `_request` 是 stdlib `http.client`（`silo_exec.sh` 为 `exec python3`，非 boto3）。观测点落在既有 except/finally；成功仍仅 status==200。

**关键决策与已否决方案**：
- 触发=非 200 或既有网络异常，不把成功判据改成 2xx。
- 已否决：新 retry/fallback、生产 env 超时开关、把 send-return 当对端收到、借 Server 头归因哪一层。
- 空 body 的 declared_content_length 记 null，不把 http.client 补 0 伪报成客户端显式头。

**下一步唯一动作**：先写 `tests/test_silo_store.py` 新断言并在未改生产码时拿到 AssertionError 红，再改 `_request`。

## 段 2 · TDD 红与实现

**当前阶段**：verifying

**本段结论**：未改生产码时新测 5 红 1 绿（成功路径本就无诊断）。红因是 AssertionError：缺 `SILO-REQUEST-FAILED` 或 stderr 含 `MSG_SENTINEL_XML_NOT_FOR_LOG`，不是 ImportError。实现后 `tests/test_silo_store.py` 34 passed。

**关键决策与已否决方案**：
- 观测点=既有 `finally`；非 200 仍 `return status, body`，成功仍仅 200。
- 失败文案收紧：`Silo request failed: {type}: {err}` → 只留类名；`S3Error` 字符串 `{code}: {message}` → `{status} {code}`。旧文案示例：`Silo put failed: S3Error: IncompleteBody: MSG_SENTINEL_XML_NOT_FOR_LOG missing bytes`；`Silo request failed: OSError: send-secret-boom`。
- 已否决：新 retry、生产 env 超时开关、把 2xx 当成功。

**下一步唯一动作**：commit 真修后定点反验（去掉 `phase` 字段应红），只还原该处。

## 段 3 · 红验与收尾

**当前阶段**：shipping

**本段结论**：真修 `a350b22`。反验 (a) 去掉 event `phase` → `_assert_shape` 红，`Extra items in the right set: 'phase'`；(b) 事件加 `leak=secret_key` → `_assert_redacted` 红，`wJalr_SENTINEL_SECRET_NOT_FOR_LOG`。两处均按行还原，`git diff HEAD -- scripts/silo_store.py` 空。全量 1540 passed，pin 检查 OK。diff 相对 f4213194 约 377 插入（硬预算 300），断言未砍。

**关键决策与已否决方案**：无新增。

**下一步唯一动作**：一次 push 开 draft PR（Refs #311 #312，不标 ready）。
