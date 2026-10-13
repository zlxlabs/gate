# F311 卡 · Silo 请求失败最小脱敏诊断

## 段 1 · pickup 与设计

**当前阶段**：implementing

**本段结论**：`f4213194` 的 `_request` 是 stdlib `http.client`（`silo_exec.sh` 为 `exec python3`，非 boto3）。观测点落在既有 except/finally；成功仍仅 status==200。

**关键决策与已否决方案**：
- 触发=非 200 或既有网络异常，不把成功判据改成 2xx。
- 已否决：新 retry/fallback、生产 env 超时开关、把 send-return 当对端收到、借 Server 头归因哪一层。
- 空 body 的 declared_content_length 记 null，不把 http.client 补 0 伪报成客户端显式头。

**下一步唯一动作**：先写 `tests/test_silo_store.py` 新断言并在未改生产码时拿到 AssertionError 红，再改 `_request`。
