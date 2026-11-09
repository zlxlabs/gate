# Gate entry install-result 遥测进度

## 2026-11-09 实现
- 当前阶段：实现完成，准备提交后验证。
- 本段结论：发现 entry 的 `scripts/gate-quality` 由 caller 仓提供，但工作流在 caller workspace 中执行它，runner 注入的 `UV_CACHE_DIR` 可由 entry step 继承。共享 `scripts/install_result_telemetry.py` 抽出 legacy 的前后 `du -sb` 采样、cache scope/hit 推导与七字段 JSON 写入；该工具由 `job.workflow_sha` 对应的可信 workflow 源检出到 runner temp，避免从 caller 仓执行 telemetry。entry 在质量命令前后调用同一实现，并继续由原 ledger input 发布链路传递。
- 关键决策与已否决方案：不添加 mode 字段，不复制遥测实现，不更改 gate-hub 消费方；entry 的 status/duration 记录质量入口执行结果/总时长，self cache 仍按 uv-per-slot 或 host-shared 口径推导。
- 下一步唯一动作：提交实现、测试与本进度文件，再启动 actionlint、范围检查及远程 CI。
