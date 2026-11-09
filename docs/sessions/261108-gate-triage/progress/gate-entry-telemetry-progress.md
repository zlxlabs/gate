# Gate entry install-result 遥测进度

## 2026-11-09 实现
- 当前阶段：实现完成，准备提交后验证。
- 本段结论：发现 entry 的 `scripts/gate-quality` 由 caller 仓提供，但工作流在 caller workspace 中执行它，runner 注入的 `UV_CACHE_DIR` 可由 entry step 继承。共享 `scripts/install_result_telemetry.py` 抽出 legacy 的前后 `du -sb` 采样、cache scope/hit 推导与七字段 JSON 写入；该工具由 `job.workflow_sha` 对应的可信 workflow 源检出到 runner temp，避免从 caller 仓执行 telemetry。entry 在质量命令前后调用同一实现，并继续由原 ledger input 发布链路传递。
- 关键决策与已否决方案：不添加 mode 字段，不复制遥测实现，不更改 gate-hub 消费方；entry 的 status/duration 记录质量入口执行结果/总时长，self cache 仍按 uv-per-slot 或 host-shared 口径推导。
- 下一步唯一动作：提交实现、测试与本进度文件，再启动 actionlint、范围检查及远程 CI。

## 2026-11-09 actionlint 修正
- 当前阶段：首轮 actionlint 后修复。
- 本段结论：actionlint 报 `SC2034`，legacy install block 的 `ecosystem` 变量在遥测抽取后不再被读取。移除该死赋值，生态检测由共用 helper 按 lockfile 执行，安装命令与 status 行为不变。
- 关键决策与已否决方案：不以 export 或虚假引用消除警告。
- 下一步唯一动作：提交该修正与进度，再重跑 actionlint。

## 2026-11-09 PR #400 CI 红修复
- 当前阶段：修正 CI test 对本卡新增 helper/step 的契约期望，准备提交后全量验证。
- 本段结论：Run `42646780766` 的 test job 实际有 5 项失败：新 entry 遥测测试对子串的断言错误、trusted sparse checkout 清单未更新、caller-check 顺序未纳入 start step、sparse scripts 白名单未纳入 helper，以及运行期入口闭集漏列 helper。现按生产 YAML 和 helper 的实际受信任调用路径更新断言/白名单，不删除 sparse helper。
- 关键决策与已否决方案：保留 workflow 的 trusted source sparse checkout 与 runner temp copy；不改生产步骤来迁就旧契约。
- 下一步唯一动作：提交这些测试修正与进度文件，然后运行全量 `python -m pytest -q` 和 actionlint。
