# disposition 稳定键改绑进度

- 2026-09-13：接手 `card/gate-20260913-03`，基线 `e74743b`，工作树初始干净。
- 2026-09-13：新增唯一规范函数 `canonical_finding_key()`，稳定键严格取 `file + line + category + severity`；缺字段、`null`、空字符串使用不同标签编码。`CANONICAL_AUDIT_DIGEST_FINDING_FIELDS` 移除 `id` 与 `trigger_kind`。
- 2026-09-13：新回执在 `finding_key` 写入完整稳定键，artifact 名使用稳定键短哈希；无 `finding_key` 的在途 v2 回执继续走旧 `finding_id` 分支，保留 gate#150 expand-then-contract TODO。
- 2026-09-13：新增稳定键 rerun、line 变化、冲突、可操作拒收文案及 producer/consumer 跨进程契约测试；定向 `117 passed`，全量 `922 passed`，pin 检查通过。
- 2026-09-13：提交 `a7cf3ec fix(disposition): bind receipts to stable finding keys`。
