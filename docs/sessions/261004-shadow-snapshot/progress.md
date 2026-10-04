# shadow snapshot 配置传递（卡 C）

- 只改 `gate-shadow-v2.yml`：resolve 一次真实 `resolve_policy.py`，把完整 JSON 和旁边的 `REGISTRY_COMMIT` 文件 SHA 写入 job 输出，再交给 shadow 腿 env。
- 新调用：`--require-resolved-policy` 放在 PR 参数前；JSON 走 env，不走 argv。
- 夹具：`resolved-policy.fixture.json`（真实 resolve 顶层形状 + 带引号/换行的 model）、`old-review-shadow` / `new-review-shadow`。
- 未跑真实 B 新 CLI（B 树仍停在 base）；合并前需主脑做一次 producer→B 跨仓契约。
